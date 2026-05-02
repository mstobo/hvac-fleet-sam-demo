#!/usr/bin/env python3
"""
chart_writer_service.py
=======================
Chart writer microservice (SQLite).

Subscribes to:
- dc/v1/pipeline/suppressed
- dc/v1/pipeline/filtered

Writes:
- chart_points (optional raw-ish continuity points)
- 10s and 1m rollups for dashboard queries
"""

import json
import time
from datetime import datetime

import chart_db
import pipeline_config as config


def _coerce_ts(payload: dict) -> str:
    return payload.get("timestamp", payload.get("ts", datetime.utcnow().isoformat() + "Z"))


def _extract_sensor_id(payload: dict) -> str:
    return payload.get("sensorId") or payload.get("asset")


def _extract_value(payload: dict):
    return payload.get("temperature", payload.get("value"))


def _write(payload: dict, source: str):
    sensor_id = _extract_sensor_id(payload)
    value = _extract_value(payload)
    if not sensor_id or value is None:
        return False

    ts = _coerce_ts(payload)
    zone = payload.get("zone")
    chart_db.write_point_and_rollups(
        ts=ts,
        sensor_id=sensor_id,
        value=float(value),
        source=source,
        zone=zone,
    )
    return True


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print(f"[ChartWriter] Connected to {config.BROKER_HOST}")
        client.subscribe(config.TOPIC_SUPPRESSED)
        client.subscribe(config.TOPIC_FILTERED)
        print(f"[ChartWriter] Subscribed to {config.TOPIC_SUPPRESSED}")
        print(f"[ChartWriter] Subscribed to {config.TOPIC_FILTERED}")
    else:
        print(f"[ChartWriter] Connection failed (rc={reason_code})")


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        topic = msg.topic
        source = "suppressed" if topic == config.TOPIC_SUPPRESSED else "filtered"
        ok = _write(payload, source=source)
        if ok:
            userdata["processed"] += 1
            # Throttle logs for throughput.
            if userdata["processed"] % 100 == 0:
                print(f"[ChartWriter] processed={userdata['processed']}")
    except Exception as e:
        print(f"[ChartWriter] Error: {e}")


def main():
    chart_db.init_database()
    print(f"[ChartWriter] DB initialized at {chart_db.get_db_path()}")

    config.print_service_banner(
        "Chart Writer",
        f"{config.TOPIC_SUPPRESSED}, {config.TOPIC_FILTERED}",
        "SQLite rollups (10s, 1m)",
    )

    userdata = {"processed": 0, "start_ts": time.time()}
    client = config.create_mqtt_client("chart-writer", userdata=userdata)
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(config.BROKER_HOST, config.BROKER_PORT, keepalive=60)
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[ChartWriter] Stopped by user.")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
