#!/usr/bin/env python3
"""
chart_writer_service.py
=======================
Chart writer microservice (SQLite).

Subscribes to:
- dc/<DC_BROKER_SITE>/v1/pipeline/suppressed
- dc/<DC_BROKER_SITE>/v1/pipeline/filtered

Writes:
- chart_points (optional raw-ish continuity points)
- 10s and 1m rollups for dashboard queries
"""

import json
import time

import chart_db
import pipeline_config as config


def _extract_value(payload: dict):
    return config.observation_value(payload)


def _write(payload: dict, source: str):
    value = _extract_value(payload)
    identity = chart_db.resolve_chart_identity(payload=payload)
    point_id = identity.get("point_id")
    if not point_id or value is None:
        return False

    ts = payload.get("timestamp") or payload.get("ts") or config.now_utc_iso()
    chart_db.write_point_and_rollups(
        ts=ts,
        sensor_id=point_id,
        value=float(value),
        source=source,
        zone=payload.get("zone"),
        point_id=point_id,
        asset_id=identity.get("asset_id"),
        metric_id=identity.get("metric_id"),
        unit=identity.get("unit"),
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
