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

Configure broker credentials via environment variables:
    SOLACE_HOST     - Broker hostname
    SOLACE_PORT     - Broker port (default: 1883, or 8883 for TLS)
    SOLACE_USER     - Username
    SOLACE_PASS     - Password
    SOLACE_TLS      - Set to "true" for TLS (default: false)
    TOPIC_BASE      - Base topic (default: sensors/temperature)
"""

import json
import math
import os
import random
import ssl
import time

import paho.mqtt.client as mqtt

# ── Broker config ────────────────────────────────────────────────────────────
BROKER_HOST = os.getenv("SOLACE_HOST", "YOUR_BROKER.messaging.solace.cloud")
BROKER_PORT = int(os.getenv("SOLACE_PORT", "1883"))
USERNAME = os.getenv("SOLACE_USER", "YOUR_USERNAME")
PASSWORD = os.getenv("SOLACE_PASS", "YOUR_PASSWORD")
USE_TLS = os.getenv("SOLACE_TLS", "false").lower() in ("true", "1", "yes")
TOPIC_BASE = os.getenv("TOPIC_BASE", "sensors/temperature")

# ── Sensor simulation config ─────────────────────────────────────────────────
SENSORS = ["sensor-001", "sensor-002", "sensor-003"]
BASELINE_TEMP = 45.0       # Normal operating temperature
NOISE_AMPLITUDE = 0.4      # Tiny fluctuation (below deadband threshold)
ANOMALY_SPIKE = 18.0       # Large spike (well above deadband + warning threshold)
PUBLISH_INTERVAL = 2.0     # Seconds between messages
RECONNECT_DELAY = 5        # Seconds to wait before reconnecting

# Message cycle: 4 normal → 3 noisy → 1 anomaly (repeating)
# This tells the story: most data is filtered, anomalies always get through
CYCLE = [
    "NORMAL", "NORMAL", "NORMAL", "NORMAL",
    "NOISY", "NOISY", "NOISY",
    "ANOMALY"
]


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
        "sensorId": sensor_id,
        "temperature": temp,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "eventType": event_type,  # Metadata for demo visibility only
        "sequence": seq
    }


def on_connect(client, userdata, flags, reason_code, properties=None):
    """Handle connection result."""
    if reason_code == 0:
        print(f"[Publisher] ✅ Connected to {BROKER_HOST}")
        userdata["connected"] = True
    else:
        print(f"[Publisher] ❌ Connection failed (rc={reason_code})")
        userdata["connected"] = False


def on_disconnect(client, userdata, flags, reason_code, properties=None):
    """Handle disconnection with reconnect logic."""
    userdata["connected"] = False
    if reason_code != 0:
        print(f"[Publisher] ⚠️ Unexpected disconnect (rc={reason_code}). Reconnecting in {RECONNECT_DELAY}s...")
        time.sleep(RECONNECT_DELAY)
        try:
            client.reconnect()
        except Exception as e:
            print(f"[Publisher] ❌ Reconnect failed: {e}")


def create_client() -> mqtt.Client:
    """Create and configure the MQTT client."""
    # Shared state for connection tracking
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

    # Configure TLS if enabled
    if USE_TLS:
        print(f"[Publisher] 🔒 TLS enabled")
        client.tls_set(
            ca_certs=None,  # Use system CA bundle
            certfile=None,
            keyfile=None,
            cert_reqs=ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLS,
            ciphers=None
        )
        # For self-signed certs in dev, uncomment:
        # client.tls_insecure_set(True)

    return client


def main():
    client = create_client()
    userdata = client._userdata

    print(f"\n{'='*60}")
    print("  SOLACE AGENT MESH  |  Sensor Data Demo Publisher")
    print(f"{'='*60}")
    print(f"  Host     : {BROKER_HOST}:{BROKER_PORT} {'(TLS)' if USE_TLS else ''}")
    print(f"  Sensors  : {', '.join(SENSORS)}")
    print(f"  Topic    : {TOPIC_BASE}/<sensorId>")
    print(f"  Cycle    : {CYCLE}")
    print(f"  Interval : {PUBLISH_INTERVAL}s")
    print(f"{'='*60}\n")

    # Connect to broker
    try:
        client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
        client.loop_start()
    except Exception as e:
        print(f"[Publisher] ❌ Failed to connect: {e}")
        return

    # Wait for connection
    for _ in range(10):
        if userdata.get("connected"):
            break
        time.sleep(0.5)

    if not userdata.get("connected"):
        print("[Publisher] ❌ Connection timeout. Check credentials and network.")
        client.loop_stop()
        return

    seq = 0
    cycle_pos = 0

    try:
        while True:
            # Check connection before publishing
            if not userdata.get("connected"):
                print("[Publisher] ⏳ Waiting for reconnection...")
                time.sleep(RECONNECT_DELAY)
                continue

            for sensor_id in SENSORS:
                event_type = CYCLE[cycle_pos % len(CYCLE)]
                payload = build_payload(sensor_id, event_type, seq)
                topic = f"{TOPIC_BASE}/{sensor_id}"

                result = client.publish(
                    topic,
                    json.dumps(payload),
                    qos=0
                )

                icon = {"NORMAL": "🟢", "NOISY": "🟡", "ANOMALY": "🔴"}.get(event_type, "⚪")
                status = "✓" if result.rc == 0 else "✗"
                print(
                    f"[{time.strftime('%H:%M:%S')}] {icon} {event_type:<8} "
                    f"| {sensor_id} | {payload['temperature']:.2f}°C | seq={seq} {status}"
                )

            seq += 1
            cycle_pos = (cycle_pos + 1) % len(CYCLE)
            time.sleep(PUBLISH_INTERVAL)

    except KeyboardInterrupt:
        print("\n[Publisher] Stopped by user.")
    finally:
        client.loop_stop()
        client.disconnect()
        print("[Publisher] Disconnected.")


if __name__ == "__main__":
    main()
