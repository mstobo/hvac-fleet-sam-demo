#!/usr/bin/env python3
"""
demo_publisher.py
=================
Simulates a data center row with 3 cooling assets and multiple metrics per asset:
temperature (inlet / outlet / motor), humidity, and motor vibration.

Publish modes (DEMO_PUBLISH_MODE):
  - topics  — one MQTT message per telemetry point (modern MQTT devices)
  - bundle  — one dc.raw.bundle.v1 JSON snapshot per asset (legacy gateway style)

Usage:
    pip install paho-mqtt
    python demo_publisher.py

Environment:
    SOLACE_* / TOPIC_BASE — see pipeline_config
    DEMO_PUBLISH_MODE     — topics (default) or bundle
"""

import json
import math
import os
import random
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pipeline_config as _broker

BROKER_HOST = _broker.BROKER_HOST
BROKER_PORT = _broker.BROKER_PORT
USERNAME = _broker.USERNAME
PASSWORD = _broker.PASSWORD
USE_TLS = _broker.USE_TLS
TOPIC_BASE = os.getenv(
    "TOPIC_BASE",
    f"{_broker.pipeline_topic_prefix()}/raw/dc1/hall-a/row-a3/rack-12",
)
SCHEMA_RAW = os.getenv("RAW_SCHEMA_NAME", _broker.SCHEMA_RAW)
SCHEMA_RAW_BUNDLE = os.getenv("RAW_BUNDLE_SCHEMA_NAME", _broker.SCHEMA_RAW_BUNDLE)
SCHEMA_REVISION = os.getenv("RAW_SCHEMA_REVISION", _broker.SCHEMA_REVISION)
DEMO_PUBLISH_MODE = (os.getenv("DEMO_PUBLISH_MODE", "topics") or "topics").strip().lower()

PUBLISH_INTERVAL = float(os.getenv("DEMO_PUBLISH_INTERVAL", "2.0"))
RECONNECT_DELAY = 5

LOCATION = {
    "site": "dc1",
    "room": "hall-a",
    "row": "row-a3",
    "rack": "rack-12",
}


@dataclass
class TelemetryPointConfig:
    """One telemetry point on an asset (maps to asset + metric in MQTT)."""

    point_id: str
    machine_id: str
    metric_id: str
    role: str
    baseline: float
    noise_amplitude: float


@dataclass
class MachineConfig:
    machine_id: str
    name: str
    profile: str
    points: List[TelemetryPointConfig] = field(default_factory=list)


def _temp_point(
    legacy_id: str,
    machine_id: str,
    role: str,
    metric_id: str,
    baseline: float,
    noise: float,
) -> TelemetryPointConfig:
    return TelemetryPointConfig(
        point_id=legacy_id,
        machine_id=machine_id,
        metric_id=metric_id,
        role=role,
        baseline=baseline,
        noise_amplitude=noise,
    )


def _machine_points(
    machine_id: str,
    prefix: str,
    inlet: float,
    outlet: float,
    motor: float,
    humidity: float = 55.0,
    vibration: float = 0.6,
) -> List[TelemetryPointConfig]:
    return [
        _temp_point(f"{prefix}-temp-inlet", machine_id, "inlet", "inlet_temp_c", inlet, 0.3),
        _temp_point(f"{prefix}-temp-outlet", machine_id, "outlet", "outlet_temp_c", outlet, 0.4),
        _temp_point(f"{prefix}-temp-motor", machine_id, "motor", "motor_temp_c", motor, 0.5),
        TelemetryPointConfig(
            point_id=f"{prefix}-humidity",
            machine_id=machine_id,
            metric_id="humidity_rh",
            role="humidity",
            baseline=humidity,
            noise_amplitude=1.5,
        ),
        TelemetryPointConfig(
            point_id=f"{prefix}-vibration",
            machine_id=machine_id,
            metric_id="motor_vibration_mm_s",
            role="vibration",
            baseline=vibration,
            noise_amplitude=0.08,
        ),
    ]


MACHINES = [
    MachineConfig(
        machine_id="machine-001",
        name="CNC Mill #1",
        profile="stable",
        points=_machine_points("machine-001", "m1", 38.0, 48.0, 52.0, humidity=52.0, vibration=0.5),
    ),
    MachineConfig(
        machine_id="machine-002",
        name="CNC Mill #2",
        profile="spiky",
        points=_machine_points("machine-002", "m2", 39.0, 49.0, 54.0, humidity=54.0, vibration=0.55),
    ),
    MachineConfig(
        machine_id="machine-003",
        name="CNC Mill #3",
        profile="hot",
        points=_machine_points("machine-003", "m3", 42.0, 53.0, 58.0, humidity=58.0, vibration=0.75),
    ),
]

ALL_POINTS = [p for m in MACHINES for p in m.points]
# Legacy alias for scenarios that reference sensor ids
ALL_SENSORS = ALL_POINTS


class AnomalyScenario:
    def __init__(
        self,
        name: str,
        affected_points: List[str],
        spike_amount: float,
        probability: float,
        duration_cycles: int,
        description: str,
    ):
        self.name = name
        self.affected_points = affected_points
        self.affected_sensors = affected_points
        self.spike_amount = spike_amount
        self.probability = probability
        self.duration_cycles = duration_cycles
        self.description = description
        self.active_cycles_remaining = 0

    def should_start(self) -> bool:
        if self.active_cycles_remaining > 0:
            return False
        return random.random() < self.probability

    def start(self):
        self.active_cycles_remaining = self.duration_cycles
        print(f"\n[Scenario] STARTING: {self.name}")
        print(f"           {self.description}")
        print(f"           Affecting: {', '.join(self.affected_points)}")
        print(f"           Duration: {self.duration_cycles} cycles\n")

    def tick(self) -> bool:
        if self.active_cycles_remaining > 0:
            self.active_cycles_remaining -= 1
            if self.active_cycles_remaining == 0:
                print(f"\n[Scenario] ENDED: {self.name}\n")
            return True
        return False

    def get_spike_for_point(self, point_id: str) -> float:
        if self.active_cycles_remaining > 0 and point_id in self.affected_points:
            return self.spike_amount
        return 0.0

    def get_spike_for_sensor(self, sensor_id: str) -> float:
        return self.get_spike_for_point(sensor_id)


SCENARIOS = [
    AnomalyScenario(
        name="Machine-002 Overload",
        affected_points=[
            "m2-temp-inlet",
            "m2-temp-outlet",
            "m2-temp-motor",
            "m2-humidity",
            "m2-vibration",
        ],
        spike_amount=20.0,
        probability=0.02,
        duration_cycles=3,
        description="Machine-002 overload — temperature, humidity, and vibration rising",
    ),
    AnomalyScenario(
        name="HVAC Failure",
        affected_points=[
            "m1-temp-inlet",
            "m2-temp-inlet",
            "m3-temp-inlet",
            "m1-humidity",
            "m2-humidity",
            "m3-humidity",
        ],
        spike_amount=15.0,
        probability=0.015,
        duration_cycles=5,
        description="HVAC failure — inlet temps and humidity rising across the row",
    ),
    AnomalyScenario(
        name="Motor-003 Bearing Wear",
        affected_points=["m3-temp-motor", "m3-vibration"],
        spike_amount=18.0,
        probability=0.03,
        duration_cycles=2,
        description="Motor-003 bearing wear — motor temp and vibration elevated",
    ),
    AnomalyScenario(
        name="Power Surge",
        affected_points=[p.point_id for p in ALL_POINTS],
        spike_amount=12.0,
        probability=0.008,
        duration_cycles=2,
        description="Power surge — all points on all assets affected",
    ),
]


def get_active_spikes() -> Dict[str, float]:
    spikes: Dict[str, float] = {}
    for scenario in SCENARIOS:
        if scenario.should_start():
            scenario.start()
        scenario.tick()
        for point in ALL_POINTS:
            spike = scenario.get_spike_for_point(point.point_id)
            if spike > 0:
                spikes[point.point_id] = spikes.get(point.point_id, 0) + spike
    return spikes


def _scaled_spike(point: TelemetryPointConfig, spike: float) -> float:
    """Scale scenario spike for non-temperature metrics."""
    if point.metric_id == "humidity_rh":
        return spike * 0.35
    if point.metric_id == "motor_vibration_mm_s":
        return spike * 0.04
    return spike


def _sample_value(point: TelemetryPointConfig, seq: int, spike: float) -> float:
    base = point.baseline + math.sin(seq * 0.05 + hash(point.point_id) % 7) * (
        1.5 if point.metric_id.endswith("_temp_c") or point.metric_id == "supply_temp_c" else 0.8
    )
    noise = random.uniform(-point.noise_amplitude, point.noise_amplitude)
    return round(base + noise + _scaled_spike(point, spike), 2)


def _event_type(value: float, baseline: float, spike: float, noise_amp: float) -> str:
    if spike > 15:
        return "ANOMALY"
    if spike > 0:
        return "ELEVATED"
    if abs(value - baseline) < noise_amp * 0.3:
        return "NOISY"
    return "NORMAL"


def _unit_for(metric_id: str) -> str:
    return str(_broker._metric_rule(metric_id).get("unit", ""))


def build_payload(point: TelemetryPointConfig, seq: int, spike: float = 0.0) -> dict:
    value = _sample_value(point, seq, spike)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    unit = _unit_for(point.metric_id)
    return {
        "schema": SCHEMA_RAW,
        "schemaRevision": SCHEMA_REVISION,
        "pointId": _broker.make_point_id(point.machine_id, point.metric_id),
        "sensorId": point.point_id,
        "machineId": point.machine_id,
        "sensorType": point.role,
        "site": LOCATION["site"],
        "room": LOCATION["room"],
        "row": LOCATION["row"],
        "rack": LOCATION["rack"],
        "asset": point.machine_id,
        "metric": point.metric_id,
        "sourceProtocol": "MQTT",
        "value": value,
        "unit": unit,
        "quality": "GOOD",
        "temperature": value,
        "timestamp": ts,
        "ts": ts,
        "eventType": _event_type(value, point.baseline, spike, point.noise_amplitude),
        "sequence": seq,
    }


def build_bundle_payload(machine: MachineConfig, seq: int, spikes: Dict[str, float]) -> dict:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    readings = []
    for point in machine.points:
        spike = spikes.get(point.point_id, 0.0)
        value = _sample_value(point, seq, spike)
        readings.append(
            {
                "metric": point.metric_id,
                "value": value,
                "unit": _unit_for(point.metric_id),
                "quality": "GOOD",
            }
        )
    return {
        "schema": SCHEMA_RAW_BUNDLE,
        "schemaRevision": SCHEMA_REVISION,
        "ts": ts,
        "timestamp": ts,
        "site": LOCATION["site"],
        "room": LOCATION["room"],
        "row": LOCATION["row"],
        "rack": LOCATION["rack"],
        "asset": machine.machine_id,
        "sourceProtocol": "Modbus",
        "readings": readings,
        "sequence": seq,
    }


def topic_for_point(point: TelemetryPointConfig) -> str:
    return f"{TOPIC_BASE}/{point.machine_id}/{point.metric_id}"


def bundle_topic_for_machine(machine_id: str) -> str:
    return f"{TOPIC_BASE}/{machine_id}/{_broker.BUNDLE_TOPIC_METRIC}"


def publish_cycle(client: Any, seq: int, spikes: Dict[str, float]) -> None:
    if DEMO_PUBLISH_MODE == "bundle":
        for machine in MACHINES:
            payload = build_bundle_payload(machine, seq, spikes)
            topic = bundle_topic_for_machine(machine.machine_id)
            result = client.publish(topic, json.dumps(payload), qos=0)
            status = "✓" if result.rc == 0 else "✗"
            metrics = ", ".join(f"{r['metric']}={r['value']}" for r in payload["readings"])
            print(
                f"[{time.strftime('%H:%M:%S')}] {machine.machine_id} | BUNDLE | "
                f"{len(payload['readings'])} pts | {status}"
            )
            print(f"             {metrics}")
        return

    for point in ALL_POINTS:
        spike = spikes.get(point.point_id, 0.0)
        payload = build_payload(point, seq, spike)
        topic = topic_for_point(point)
        result = client.publish(topic, json.dumps(payload), qos=0)
        status = "✓" if result.rc == 0 else "✗"
        unit = payload.get("unit") or ""
        print(
            f"[{time.strftime('%H:%M:%S')}] {point.machine_id} | "
            f"{point.metric_id:22} | {payload['value']:6.2f} {unit:4} | "
            f"{payload['eventType']:8} {status}"
        )


def on_connect(client, userdata, flags, reason_code, properties=None):  # noqa: ARG001
    if reason_code == 0:
        print(f"[Publisher] Connected to {BROKER_HOST}")
        userdata["connected"] = True
    else:
        print(f"[Publisher] Connection failed (rc={reason_code})")
        userdata["connected"] = False


def on_disconnect(client, userdata, flags, reason_code, properties=None):
    userdata["connected"] = False
    if reason_code != 0:
        print(f"[Publisher] Unexpected disconnect (rc={reason_code}). Reconnecting in {RECONNECT_DELAY}s...")
        time.sleep(RECONNECT_DELAY)
        try:
            client.reconnect()
        except Exception as e:
            print(f"[Publisher] Reconnect failed: {e}")


def create_client() -> Any:
    import paho.mqtt.client as mqtt

    userdata = {"connected": False}
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"demo-publisher-{int(time.time())}",
        protocol=mqtt.MQTTv5,
        userdata=userdata,
    )
    client.username_pw_set(USERNAME, PASSWORD)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    if USE_TLS:
        print("[Publisher] TLS enabled")
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)
    return client


def print_machine_summary():
    print(f"\n{'='*70}")
    print("  DATA CENTER HVAC SIMULATOR  |  3 Assets x 5 Metrics = 15 Points")
    print(f"  Publish mode: {DEMO_PUBLISH_MODE.upper()}")
    print(f"{'='*70}")
    for machine in MACHINES:
        print(f"\n  - {machine.machine_id} ({machine.name}) — {machine.profile.upper()}")
        for point in machine.points:
            unit = _unit_for(point.metric_id)
            print(
                f"      └─ {point.point_id}: {point.metric_id} "
                f"@ {point.baseline} {unit} ({point.role})"
            )
    print(f"\n{'='*70}")
    print("  ANOMALY SCENARIOS (probabilistic):")
    for scenario in SCENARIOS:
        print(f"    • {scenario.name} ({scenario.probability * 100:.1f}% per cycle)")
    print(f"{'='*70}\n")


def main():
    client = create_client()
    userdata = client._userdata

    print_machine_summary()
    print(f"  Broker   : {BROKER_HOST}:{BROKER_PORT} {'(TLS)' if USE_TLS else ''}")
    if DEMO_PUBLISH_MODE == "bundle":
        print(f"  Topic    : {TOPIC_BASE}/<asset>/{_broker.BUNDLE_TOPIC_METRIC}")
    else:
        print(f"  Topic    : {TOPIC_BASE}/<asset>/<metric>")
    print(f"  Interval : {PUBLISH_INTERVAL}s per batch")
    print(f"{'='*70}\n")

    try:
        client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
        client.loop_start()
    except Exception as e:
        print(f"[Publisher] Failed to connect: {e}")
        return

    for _ in range(10):
        if userdata.get("connected"):
            break
        time.sleep(0.5)

    if not userdata.get("connected"):
        print("[Publisher] Connection timeout. Check credentials and network.")
        client.loop_stop()
        return

    seq = 0
    try:
        while True:
            if not userdata.get("connected"):
                print("[Publisher] Waiting for reconnection...")
                time.sleep(RECONNECT_DELAY)
                continue
            publish_cycle(client, seq, get_active_spikes())
            seq += 1
            print()
            time.sleep(PUBLISH_INTERVAL)
    except KeyboardInterrupt:
        print("\n[Publisher] Stopped by user.")
    finally:
        client.loop_stop()
        client.disconnect()
        print("[Publisher] Disconnected.")


if __name__ == "__main__":
    main()
