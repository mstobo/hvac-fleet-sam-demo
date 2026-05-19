#!/usr/bin/env python3
"""
sketch_service.py
=================
Microservice 2/3: Sketch Generator

Subscribes to: sensors/pipeline/filtered
Publishes to:  sensors/pipeline/sketched
Writes to:     SQLite (sketches table)

Generates natural language summaries ("sketches") for each forwarded reading.
These sketches are what the LLM reads when answering queries - not raw numbers.

This is the second stage of the deterministic data plane - no LLM involved.
The sketch generation is pure Python string formatting, not AI.
"""

import json
import time

import pipeline_config as config
import sensor_db

log = config.get_logger("Sketch")

SKETCH_DB_BATCH_SIZE = 100
SKETCH_DB_FLUSH_INTERVAL_SEC = 0.5
SKETCH_LOG_EVERY_N = 50

_sketch_buffer = []
_last_flush_time = time.monotonic()
_processed_count = 0


def _flush_sketch_buffer(force=False):
    """Flush buffered sketch rows to SQLite in batches."""
    global _last_flush_time
    if not _sketch_buffer:
        return

    now = time.monotonic()
    if not force and len(_sketch_buffer) < SKETCH_DB_BATCH_SIZE and (now - _last_flush_time) < SKETCH_DB_FLUSH_INTERVAL_SEC:
        return

    sensor_db.insert_sketch_batch(_sketch_buffer)
    _sketch_buffer.clear()
    _last_flush_time = now


def generate_sketch(data):
    """
    Generate a natural language sketch from sensor data.
    
    Args:
        data: dict with sensorId, temperature, zone, delta_pct, 
              forwarded_reason, window, trend
    
    Returns:
        dict with sketch text and metadata
    """
    sensor_id = data["sensorId"]
    temperature = data["temperature"]
    zone = data["zone"]
    delta_pct = data["delta_pct"]
    forwarded_reason = data["forwarded_reason"]
    site = data.get("site", config.DEFAULT_SITE)
    room = data.get("room", config.DEFAULT_ROOM)
    window = data.get("window", {})
    trend = data.get("trend", "STABLE")
    incident_id = data.get("incidentId") or f"INC-{site.upper()}-{int(time.time())}-{sensor_id}"
    
    win_mean = window.get("mean", temperature)
    win_min = window.get("min", temperature)
    win_max = window.get("max", temperature)
    delta_pct_pct = delta_pct * 100
    
    timestamp = config.now_utc_iso()
    
    # Generate natural language summary
    if forwarded_reason == "heartbeat":
        sketch = (
            f"[HEARTBEAT] {sensor_id} stable at ~{win_mean:.1f}°C "
            f"(range {win_min:.1f}–{win_max:.1f}°C) over last 30s. "
            f"No significant change. Zone: {zone}."
        )
    else:
        move = "spike" if temperature > win_mean else "drop"
        sketch = (
            f"{sensor_id} recorded a {delta_pct_pct:.1f}% {move} to "
            f"{temperature:.1f}°C. 30s window: mean {win_mean:.1f}°C, "
            f"range [{win_min:.1f}–{win_max:.1f}°C]. Zone: {zone}."
        )
        if zone == "CRITICAL":
            sketch += " Anomaly detected - immediate review required."
        elif zone == "WARNING":
            sketch += " Elevated condition - monitoring advised."
    
    # Buffer database write; flush in batches to avoid per-message SQLite commits.
    _sketch_buffer.append(
        {
            "sensor_id": sensor_id,
            "temperature": temperature,
            "zone": zone,
            "sketch": sketch,
            "timestamp": timestamp,
            "trend": trend,
            "window_avg": win_mean,
            "window_min": win_min,
            "window_max": win_max,
        }
    )
    _flush_sketch_buffer()
    
    return {
        "schema": config.SCHEMA_SKETCH,
        "schemaRevision": config.SCHEMA_REVISION,
        "sensorId": sensor_id,
        "temperature": temperature,
        "zone": zone,
        "sketch": sketch,
        "timestamp": timestamp,
        "site": site,
        "room": room,
        "incidentId": incident_id,
        "window": window,
        "trend": trend,
        "delta_pct": delta_pct,
        "forwarded_reason": forwarded_reason
    }


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        log.info("connected to %s", config.BROKER_HOST)
        client.subscribe(config.TOPIC_FILTERED)
        log.info("subscribed to %s", config.TOPIC_FILTERED)
    else:
        log.error("connection failed rc=%s", reason_code)


def on_message(client, userdata, msg):
    """Process filtered reading and generate sketch."""
    global _processed_count
    try:
        data = json.loads(msg.payload.decode())
        
        # Only process forwarded readings
        if data.get("action") != "forward":
            return
        
        # Generate sketch
        result = generate_sketch(data)
        payload = json.dumps(result)
        config.publish_checked(client, config.TOPIC_SKETCHED, payload, source="Sketch")
        config.publish_checked(
            client,
            config.build_sketch_topic(
                result["site"],
                result["room"],
                result["incidentId"],
            ),
            payload,
            source="Sketch",
        )

        # Per-message detail at DEBUG; periodic counter at INFO so operators get a heartbeat
        # without the firehose. (LOG_LEVEL=DEBUG reveals each message.)
        _processed_count += 1
        log.debug("sketched %s zone=%s", result["sensorId"], result["zone"])
        if _processed_count % SKETCH_LOG_EVERY_N == 0:
            log.info(
                "processed=%d buffered=%d last=%s:%s",
                _processed_count, len(_sketch_buffer), result["sensorId"], result["zone"],
            )

    except Exception:
        log.exception("error processing message on %s", msg.topic)


def main():
    log.info("initializing SQLite database at %s", sensor_db.get_db_path())
    sensor_db.init_database()

    config.print_service_banner(
        "Sketch Generator",
        config.TOPIC_FILTERED,
        config.TOPIC_SKETCHED
    )

    client = config.create_mqtt_client("sketch")
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(config.BROKER_HOST, config.BROKER_PORT, keepalive=60)
        client.loop_forever()
    except KeyboardInterrupt:
        log.info("stopped by user")
    finally:
        _flush_sketch_buffer(force=True)
        client.disconnect()


if __name__ == "__main__":
    main()
