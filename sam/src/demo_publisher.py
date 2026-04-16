#!/usr/bin/env python3
"""
demo_publisher.py
=================
Simulates a sensor network publishing temperature readings over MQTT5.
Produces three event types on a round-robin cycle to tell the full story:

  - NORMAL   : Stable readings within expected range (most messages)
  - NOISY    : Tiny random fluctuations — should be suppressed by deadband
  - ANOMALY  : Sudden spike well outside normal range — must reach the LLM

Usage:
    pip install paho-mqtt
    python demo_publisher.py

Configure broker credentials below or via environment variables.
"""

import json, time, math, random, os
import paho.mqtt.client as mqtt

# ── Broker config (Solace Cloud free tier) ─────────────────────────────────
BROKER_HOST = os.getenv("SOLACE_HOST",     "YOUR_BROKER.messaging.solace.cloud")
BROKER_PORT = int(os.getenv("SOLACE_PORT", "1883"))
USERNAME    = os.getenv("SOLACE_USER",     "YOUR_USERNAME")
PASSWORD    = os.getenv("SOLACE_PASS",     "YOUR_PASSWORD")
TOPIC_BASE  = "sensors/temperature"

# ── Sensor simulation config ────────────────────────────────────────────────
SENSORS = ["sensor-001", "sensor-002", "sensor-003"]
BASELINE_TEMP   = 45.0    # Normal operating temperature
NOISE_AMPLITUDE = 0.4     # Tiny fluctuation (below deadband threshold)
ANOMALY_SPIKE   = 18.0    # Large spike (well above deadband + warning threshold)
PUBLISH_INTERVAL = 2.0    # Seconds between messages

# Message cycle: 4 normal → 3 noisy → 1 anomaly (repeating)
# This tells the story: most data is filtered, anomalies always get through
CYCLE = ["NORMAL", "NORMAL", "NORMAL", "NORMAL",
         "NOISY",  "NOISY",  "NOISY",
         "ANOMALY"]

def build_payload(sensor_id: str, event_type: str, seq: int) -> dict:
    """Generate a sensor reading based on event type."""
    base = BASELINE_TEMP + math.sin(seq * 0.1) * 2  # Gentle drift

    if event_type == "NORMAL":
        temp = round(base + random.uniform(-1.0, 1.0), 2)
    elif event_type == "NOISY":
        temp = round(base + random.uniform(-NOISE_AMPLITUDE, NOISE_AMPLITUDE), 2)
    elif event_type == "ANOMALY":
        temp = round(base + ANOMALY_SPIKE + random.uniform(0, 3.0), 2)
    else:
        temp = round(base, 2)

    return {
        "sensorId"   : sensor_id,
        "temperature": temp,
        "timestamp"  : time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "eventType"  : event_type,   # Metadata for demo visibility only
        "sequence"   : seq
    }

def on_connect(client, userdata, flags, reason_code, properties=None):
    status = "✅ Connected" if reason_code == 0 else f"❌ Failed (rc={reason_code})"
    print(f"[Publisher] {status} to {BROKER_HOST}")

def main():
    client = mqtt.Client(
        client_id="demo-publisher",
        protocol=mqtt.MQTTv5
    )
    client.username_pw_set(USERNAME, PASSWORD)
    client.on_connect = on_connect
    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    client.loop_start()

    time.sleep(1)  # Wait for connection

    print(f"\n{'='*60}")
    print("  SOLACE AGENT MESH  |  Sensor Data Demo Publisher")
    print(f"{'='*60}")
    print(f"  Sensors  : {', '.join(SENSORS)}")
    print(f"  Topic    : {TOPIC_BASE}/<sensorId>")
    print(f"  Cycle    : {CYCLE}")
    print(f"  Interval : {PUBLISH_INTERVAL}s")
    print(f"{'='*60}\n")

    seq = 0
    cycle_pos = 0

    try:
        while True:
            for sensor_id in SENSORS:
                event_type = CYCLE[cycle_pos % len(CYCLE)]
                payload    = build_payload(sensor_id, event_type, seq)
                topic      = f"{TOPIC_BASE}/{sensor_id}"

                client.publish(
                    topic,
                    json.dumps(payload),
                    qos=0
                )

                icon = {"NORMAL": "🟢", "NOISY": "🟡", "ANOMALY": "🔴"}.get(event_type, "⚪")
                print(
                    f"[{time.strftime('%H:%M:%S')}] {icon} {event_type:<8} "
                    f"| {sensor_id} | {payload['temperature']:.2f}°C | seq={seq}"
                )

            seq      += 1
            cycle_pos = (cycle_pos + 1) % len(CYCLE)
            time.sleep(PUBLISH_INTERVAL)

    except KeyboardInterrupt:
        print("\n[Publisher] Stopped.")
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()
