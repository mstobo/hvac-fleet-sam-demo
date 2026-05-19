#!/usr/bin/env python3
"""
demo_publisher.py
=================
Simulates a data center row with 3 cooling assets, each having 3 temperature sensors.

Asset/Sensor Hierarchy:
  machine-001: m1-temp-inlet, m1-temp-outlet, m1-temp-motor
  machine-002: m2-temp-inlet, m2-temp-outlet, m2-temp-motor
  machine-003: m3-temp-inlet, m3-temp-outlet, m3-temp-motor

Sensor Behaviors:
  - Inlet temps: Coolest, affected by ambient/HVAC
  - Outlet temps: Warmer than inlet, shows heat generation
  - Motor temps: Hottest, most likely to spike

Asset Profiles:
  - Asset 1: Stable operation (baseline)
  - Asset 2: Occasional anomalies (demonstrates spike detection)
  - Asset 3: Running slightly hot (demonstrates sustained warnings)

This structure enables realistic fleet monitoring scenarios:
  - All inlet temps spike → Environmental issue (HVAC failure)
  - All sensors on one asset spike → Asset-level issue
  - Single sensor spikes → Sensor or component issue

Usage:
    pip install paho-mqtt
    python demo_publisher.py

Configure broker credentials via environment variables:
    SOLACE_HOST     - Broker hostname
    SOLACE_PORT     - Broker port (default: 8883 for TLS)
    SOLACE_USER     - Username
    SOLACE_PASS     - Password
    SOLACE_TLS      - Set to "true" for TLS (default: true)
    TOPIC_BASE      - Base topic (default: dc/<DC_BROKER_SITE>/v1/raw/… from pipeline_config)
"""

import json
import math
import os
import random
import ssl
import time
from dataclasses import dataclass
from typing import Dict, List

import paho.mqtt.client as mqtt

import pipeline_config as _broker

# ── Broker config (same resolution as deadband / chart writer / sketch) ───────
BROKER_HOST = _broker.BROKER_HOST
BROKER_PORT = _broker.BROKER_PORT
USERNAME = _broker.USERNAME
PASSWORD = _broker.PASSWORD
USE_TLS = _broker.USE_TLS
TOPIC_BASE = os.getenv(
    "TOPIC_BASE",
    f"{_broker.pipeline_topic_prefix()}/raw/dc1/hall-a/row-a3/rack-12",
)
SCHEMA_RAW = os.getenv("RAW_SCHEMA_NAME", "dc.raw.v1")
SCHEMA_REVISION = os.getenv("RAW_SCHEMA_REVISION", "1.0.0")

# ── Timing ───────────────────────────────────────────────────────────────────
PUBLISH_INTERVAL = 2.0     # Seconds between message batches
RECONNECT_DELAY = 5        # Seconds to wait before reconnecting


@dataclass
class SensorConfig:
    """Configuration for a single sensor."""
    sensor_id: str
    machine_id: str
    sensor_type: str  # inlet, outlet, motor
    baseline_temp: float
    noise_amplitude: float


@dataclass
class MachineConfig:
    """Configuration for a cooling asset with multiple sensors."""
    machine_id: str
    name: str
    profile: str  # stable, spiky, hot
    sensors: List[SensorConfig]


# ── Machine and Sensor Definitions ───────────────────────────────────────────
MACHINES = [
    MachineConfig(
        machine_id="machine-001",
        name="CNC Mill #1",
        profile="stable",
        sensors=[
            SensorConfig("m1-temp-inlet", "machine-001", "inlet", 38.0, 0.3),
            SensorConfig("m1-temp-outlet", "machine-001", "outlet", 48.0, 0.4),
            SensorConfig("m1-temp-motor", "machine-001", "motor", 52.0, 0.5),
        ]
    ),
    MachineConfig(
        machine_id="machine-002",
        name="CNC Mill #2",
        profile="spiky",  # This machine has occasional anomalies
        sensors=[
            SensorConfig("m2-temp-inlet", "machine-002", "inlet", 39.0, 0.3),
            SensorConfig("m2-temp-outlet", "machine-002", "outlet", 49.0, 0.4),
            SensorConfig("m2-temp-motor", "machine-002", "motor", 54.0, 0.6),
        ]
    ),
    MachineConfig(
        machine_id="machine-003",
        name="CNC Mill #3",
        profile="hot",  # This machine runs slightly hot
        sensors=[
            SensorConfig("m3-temp-inlet", "machine-003", "inlet", 42.0, 0.4),
            SensorConfig("m3-temp-outlet", "machine-003", "outlet", 53.0, 0.5),
            SensorConfig("m3-temp-motor", "machine-003", "motor", 58.0, 0.7),  # Near warning threshold
        ]
    ),
]

# Build flat sensor list for easy iteration
ALL_SENSORS = [sensor for machine in MACHINES for sensor in machine.sensors]

# ── Anomaly Scenarios ────────────────────────────────────────────────────────
# These scenarios create realistic fleet events

class AnomalyScenario:
    """Defines an anomaly scenario that affects specific sensors."""
    
    def __init__(self, name: str, affected_sensors: List[str], spike_amount: float, 
                 probability: float, duration_cycles: int, description: str):
        self.name = name
        self.affected_sensors = affected_sensors
        self.spike_amount = spike_amount
        self.probability = probability  # Chance per cycle to start
        self.duration_cycles = duration_cycles
        self.description = description
        self.active_cycles_remaining = 0
    
    def should_start(self) -> bool:
        """Check if this scenario should start (probabilistic)."""
        if self.active_cycles_remaining > 0:
            return False
        return random.random() < self.probability
    
    def start(self):
        """Start the scenario."""
        self.active_cycles_remaining = self.duration_cycles
        print(f"\n[Scenario] STARTING: {self.name}")
        print(f"           {self.description}")
        print(f"           Affecting: {', '.join(self.affected_sensors)}")
        print(f"           Duration: {self.duration_cycles} cycles\n")
    
    def tick(self) -> bool:
        """Tick the scenario, return True if still active."""
        if self.active_cycles_remaining > 0:
            self.active_cycles_remaining -= 1
            if self.active_cycles_remaining == 0:
                print(f"\n[Scenario] ENDED: {self.name}\n")
            return True
        return False
    
    def get_spike_for_sensor(self, sensor_id: str) -> float:
        """Get spike amount for a sensor if affected by this scenario."""
        if self.active_cycles_remaining > 0 and sensor_id in self.affected_sensors:
            return self.spike_amount
        return 0.0


# Define anomaly scenarios
SCENARIOS = [
    # Machine-level failure (all sensors on machine-002 spike)
    AnomalyScenario(
        name="Machine-002 Overload",
        affected_sensors=["m2-temp-inlet", "m2-temp-outlet", "m2-temp-motor"],
        spike_amount=20.0,
        probability=0.02,  # 2% chance per cycle
        duration_cycles=3,
        description="Machine-002 experiencing overload - all temps rising"
    ),
    
    # Environmental event (all inlet sensors spike - HVAC failure)
    AnomalyScenario(
        name="HVAC Failure",
        affected_sensors=["m1-temp-inlet", "m2-temp-inlet", "m3-temp-inlet"],
        spike_amount=15.0,
        probability=0.015,  # 1.5% chance per cycle
        duration_cycles=5,
        description="HVAC failure detected - all inlet temperatures rising"
    ),
    
    # Single motor issue
    AnomalyScenario(
        name="Motor-003 Bearing Wear",
        affected_sensors=["m3-temp-motor"],
        spike_amount=18.0,
        probability=0.03,  # 3% chance per cycle
        duration_cycles=2,
        description="Motor-003 showing signs of bearing wear"
    ),
    
    # Fleet-wide event (simulates power surge affecting all machines)
    AnomalyScenario(
        name="Power Surge",
        affected_sensors=[s.sensor_id for s in ALL_SENSORS],  # All sensors
        spike_amount=12.0,
        probability=0.008,  # 0.8% chance per cycle (rare but dramatic)
        duration_cycles=2,
        description="Power surge detected - all equipment affected"
    ),
]


def _telemetry_availability(sensor: SensorConfig) -> dict:
    """
    Realistic data-fidelity hints: the demo only models one temperature stream per
    published message; other facility signals are intentionally absent.
    """
    if sensor.sensor_type == "outlet":
        return {
            "scope": "outlet_temperature_only",
            "signals_present": ["outlet_temperature_c"],
            "signals_not_in_this_stream": [
                "inlet_airflow_cfm",
                "humidity_rh",
                "differential_pressure_pa",
            ],
            "note": (
                "Only outlet temperature telemetry is present in this incident bundle; "
                "inlet airflow, humidity, and pressure signals are not included."
            ),
        }
    if sensor.sensor_type == "inlet":
        return {
            "scope": "inlet_temperature_only",
            "signals_present": ["inlet_temperature_c"],
            "signals_not_in_this_stream": [
                "outlet_temperature_c",
                "airflow_cfm",
                "humidity_rh",
                "differential_pressure_pa",
            ],
            "note": (
                "Only inlet temperature is modeled on this stream; airflow, humidity, "
                "and pressure are not included in the simulated bundle."
            ),
        }
    return {
        "scope": "motor_winding_temperature_only",
        "signals_present": ["motor_temperature_c"],
        "signals_not_in_this_stream": [
            "inlet_airflow_cfm",
            "humidity_rh",
            "differential_pressure_pa",
            "bearing_vibration",
        ],
        "note": (
            "Motor winding temperature only; inlet/outlet airflow, humidity, and "
            "pressure are not included in this simulated stream."
        ),
    }


def get_active_spikes() -> Dict[str, float]:
    """Get current spike amounts for all sensors from active scenarios."""
    spikes = {}
    for scenario in SCENARIOS:
        if scenario.should_start():
            scenario.start()
        scenario.tick()
        for sensor_id in scenario.affected_sensors:
            spike = scenario.get_spike_for_sensor(sensor_id)
            if spike > 0:
                spikes[sensor_id] = spikes.get(sensor_id, 0) + spike
    return spikes


def build_payload(sensor: SensorConfig, seq: int, spike: float = 0.0) -> dict:
    """Generate a sensor reading."""
    # Base temperature with gentle drift
    base = sensor.baseline_temp + math.sin(seq * 0.05) * 1.5
    
    # Add noise
    noise = random.uniform(-sensor.noise_amplitude, sensor.noise_amplitude)
    
    # Add any active spike
    temp = round(base + noise + spike, 2)
    
    # Determine event type for logging
    if spike > 15:
        event_type = "ANOMALY"
    elif spike > 0:
        event_type = "ELEVATED"
    elif abs(noise) < sensor.noise_amplitude * 0.3:
        event_type = "NOISY"  # Will likely be suppressed
    else:
        event_type = "NORMAL"
    
    return {
        "schema": SCHEMA_RAW,
        "schemaRevision": SCHEMA_REVISION,
        "sensorId": sensor.sensor_id,
        "machineId": sensor.machine_id,
        "sensorType": sensor.sensor_type,
        "site": "dc1",
        "room": "hall-a",
        "row": "row-a3",
        "rack": "rack-12",
        "asset": sensor.machine_id,
        "metric": "supply_temp_c",
        "sourceProtocol": "MQTT",
        "value": temp,
        "unit": "C",
        "quality": "GOOD",
        "temperature": temp,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "eventType": event_type,
        "sequence": seq,
        "telemetry_availability": _telemetry_availability(sensor),
    }


def on_connect(client, userdata, flags, reason_code, properties=None):
    """Handle connection result."""
    if reason_code == 0:
        print(f"[Publisher] Connected to {BROKER_HOST}")
        userdata["connected"] = True
    else:
        print(f"[Publisher] Connection failed (rc={reason_code})")
        userdata["connected"] = False


def on_disconnect(client, userdata, flags, reason_code, properties=None):
    """Handle disconnection with reconnect logic."""
    userdata["connected"] = False
    if reason_code != 0:
        print(f"[Publisher] Unexpected disconnect (rc={reason_code}). Reconnecting in {RECONNECT_DELAY}s...")
        time.sleep(RECONNECT_DELAY)
        try:
            client.reconnect()
        except Exception as e:
            print(f"[Publisher] Reconnect failed: {e}")


def create_client() -> mqtt.Client:
    """Create and configure the MQTT client."""
    _broker.validate_broker_config()
    userdata = {"connected": False}

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"demo-publisher-{int(time.time())}",
        protocol=mqtt.MQTTv5,
        userdata=userdata
    )
    client.username_pw_set(USERNAME, PASSWORD)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    if USE_TLS:
        print(f"[Publisher] TLS enabled")
        client.tls_set(
            ca_certs=None,
            certfile=None,
            keyfile=None,
            cert_reqs=ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLS,
            ciphers=None
        )

    return client


def print_machine_summary():
    """Print summary of cooling assets and sensors."""
    print(f"\n{'='*70}")
    print("  DATA CENTER HVAC SIMULATOR  |  3 Assets x 3 Sensors = 9 Total")
    print(f"{'='*70}")
    for machine in MACHINES:
        print(f"\n  - {machine.machine_id} ({machine.name}) - Profile: {machine.profile.upper()}")
        for sensor in machine.sensors:
            print(f"      └─ {sensor.sensor_id}: {sensor.sensor_type} @ {sensor.baseline_temp}°C baseline")
    print(f"\n{'='*70}")
    print("  ANOMALY SCENARIOS (probabilistic):")
    for scenario in SCENARIOS:
        print(f"    • {scenario.name} ({scenario.probability*100:.1f}% per cycle)")
    print(f"{'='*70}\n")


def main():
    client = create_client()
    userdata = client._userdata

    print_machine_summary()
    
    print(f"  Broker   : {BROKER_HOST}:{BROKER_PORT} {'(TLS)' if USE_TLS else ''}")
    print(f"  Topic    : {TOPIC_BASE}/<sensorId>")
    print(f"  Interval : {PUBLISH_INTERVAL}s per batch")
    print(f"{'='*70}\n")

    # Connect to broker
    try:
        client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
        client.loop_start()
    except Exception as e:
        print(f"[Publisher] Failed to connect: {e}")
        return

    # Wait for connection
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

            # Get active spikes from scenarios
            spikes = get_active_spikes()
            
            # Publish readings for all sensors
            for sensor in ALL_SENSORS:
                spike = spikes.get(sensor.sensor_id, 0.0)
                payload = build_payload(sensor, seq, spike)
                topic = f"{TOPIC_BASE}/{sensor.machine_id}/supply_temp_c"

                result = client.publish(topic, json.dumps(payload), qos=0)

                # Icon based on event type
                status = "✓" if result.rc == 0 else "✗"
                
                # Compact logging
                print(
                    f"[{time.strftime('%H:%M:%S')}] {sensor.machine_id} | "
                    f"{sensor.sensor_type:6} | {payload['temperature']:5.1f}°C | "
                    f"{payload['eventType']:8} {status}"
                )

            seq += 1
            print()  # Blank line between batches
            time.sleep(PUBLISH_INTERVAL)

    except KeyboardInterrupt:
        print("\n[Publisher] Stopped by user.")
    finally:
        client.loop_stop()
        client.disconnect()
        print("[Publisher] Disconnected.")


if __name__ == "__main__":
    main()
