#!/usr/bin/env python3
"""
deadband_service.py
===================
Microservice 1/3: Deadband Filter

Subscribes to: dc/<DC_BROKER_SITE>/v1/raw/# (see pipeline_config.TOPIC_SENSOR_RAW;
               or dc/+/v1/raw/# when DC_PIPELINE_MULTISITE_RAW=true)
Publishes to:  dc/<DC_BROKER_SITE>/v1/pipeline/filtered (forwarded readings)
               dc/<DC_BROKER_SITE>/v1/pipeline/suppressed (filtered out readings)

Suppresses sensor readings that haven't changed significantly (< 2%)
unless a heartbeat interval (30s) has elapsed.

This is the first stage of the deterministic data plane - no LLM involved.
"""

import collections
import json
import time

import pipeline_config as config

log = config.get_logger("Deadband")

# ── Deadband State ───────────────────────────────────────────────────────────
_last_value = {}          # sensor_id -> last temperature value
_last_forward_ts = {}     # sensor_id -> timestamp of last forwarded reading
_windows = {}             # sensor_id -> deque of (timestamp, temperature)


def get_window_stats(sensor_id):
    """Calculate rolling window statistics."""
    if sensor_id not in _windows or not _windows[sensor_id]:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "count": 0}
    vals = [v for _, v in _windows[sensor_id]]
    return {
        "mean": round(sum(vals) / len(vals), 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
        "count": len(vals)
    }


def calculate_trend(sensor_id):
    """Calculate temperature trend from window data."""
    if sensor_id not in _windows or len(_windows[sensor_id]) < 3:
        return "STABLE"
    
    vals = [v for _, v in _windows[sensor_id]]
    recent = vals[-3:]
    
    if all(recent[i] < recent[i+1] for i in range(len(recent)-1)):
        return "RISING"
    elif all(recent[i] > recent[i+1] for i in range(len(recent)-1)):
        return "FALLING"
    return "STABLE"


def apply_deadband(sensor_id, temperature, timestamp):
    """
    Apply deadband filter to sensor reading.
    
    Returns: (action, result_dict)
        action: "suppress" or "forward"
        result_dict: payload to publish
    """
    now = time.time()
    
    # Update rolling window
    if sensor_id not in _windows:
        _windows[sensor_id] = collections.deque(maxlen=50)
    _windows[sensor_id].append((now, temperature))
    
    # Prune old entries outside window
    cutoff = now - config.WINDOW_SECS
    while _windows[sensor_id] and _windows[sensor_id][0][0] < cutoff:
        _windows[sensor_id].popleft()
    
    prev_val = _last_value.get(sensor_id)
    last_fwd = _last_forward_ts.get(sensor_id, 0)
    
    if prev_val is not None:
        delta_pct = abs(temperature - prev_val) / max(abs(prev_val), 1.0)
        heartbeat_due = (now - last_fwd) >= config.HEARTBEAT_SECS
        
        # Suppress if change is below threshold and no heartbeat due
        if delta_pct < config.DEADBAND_PCT and not heartbeat_due:
            return "suppress", {
                "action": "suppress",
                "sensorId": sensor_id,
                "temperature": temperature,
                "timestamp": timestamp,
                "reason": f"delta {delta_pct*100:.1f}% < {config.DEADBAND_PCT*100}%",
                "delta_pct": round(delta_pct, 4)
            }
        
        forwarded_reason = "heartbeat" if delta_pct < config.DEADBAND_PCT else "delta"
        delta_pct_out = delta_pct
    else:
        forwarded_reason = "first-reading"
        delta_pct_out = 0.0
    
    # Update state
    _last_value[sensor_id] = temperature
    _last_forward_ts[sensor_id] = now
    
    # Classify zone and get stats
    zone = config.classify_zone(temperature)
    window = get_window_stats(sensor_id)
    trend = calculate_trend(sensor_id)
    
    return "forward", {
        "action": "forward",
        "sensorId": sensor_id,
        "temperature": temperature,
        "timestamp": timestamp,
        "zone": zone,
        "delta_pct": round(delta_pct_out, 4),
        "forwarded_reason": forwarded_reason,
        "window": window,
        "trend": trend
    }


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        log.info("connected to %s", config.BROKER_HOST)
        client.subscribe(config.TOPIC_SENSOR_RAW)
        log.info("subscribed to %s", config.TOPIC_SENSOR_RAW)
    else:
        log.error("connection failed rc=%s", reason_code)


def on_message(client, userdata, msg):
    """Process incoming sensor message through deadband filter."""
    try:
        payload = json.loads(msg.payload.decode())
        topic_meta, temp_from_topic = config.parse_raw_topic_with_temperature(msg.topic)
        sensor_id = payload.get("sensorId") or topic_meta.get("asset")
        temperature = (
            temp_from_topic
            if temp_from_topic is not None
            else payload.get("temperature", payload.get("value"))
        )
        timestamp = payload.get("timestamp", payload.get("ts", config.now_utc_iso()))
        
        if not sensor_id or temperature is None:
            return
        
        # Apply deadband filter
        action, result = apply_deadband(sensor_id, temperature, timestamp)
        result.update(
            {
                "schema": config.SCHEMA_FILTERED,
                "schemaRevision": config.SCHEMA_REVISION,
                "site": payload.get("site", topic_meta.get("site", config.DEFAULT_SITE)),
                "room": payload.get("room", topic_meta.get("room", config.DEFAULT_ROOM)),
                "row": payload.get("row", topic_meta.get("row")),
                "rack": payload.get("rack", topic_meta.get("rack")),
                "asset": payload.get("asset", topic_meta.get("asset")),
                "metric": payload.get("metric", topic_meta.get("metric", "supply_temp_c")),
            }
        )
        config.copy_raw_metadata_to_result(payload, result)
        
        if action == "suppress":
            log.debug("SUPPRESS %s | %s", sensor_id, result["reason"])
            config.publish_checked(client, config.TOPIC_SUPPRESSED, json.dumps(result), source="Deadband")
        else:
            log.debug("FORWARD %s | %.1f°C | zone=%s", sensor_id, temperature, result["zone"])
            config.publish_checked(client, config.TOPIC_FILTERED, json.dumps(result), source="Deadband")

    except Exception:
        log.exception("error processing message on %s", msg.topic)


def main():
    config.print_service_banner(
        "Deadband Filter",
        config.TOPIC_SENSOR_RAW,
        config.TOPIC_FILTERED
    )
    
    client = config.create_mqtt_client("deadband")
    client.on_connect = on_connect
    client.on_message = on_message
    
    try:
        client.connect(config.BROKER_HOST, config.BROKER_PORT, keepalive=60)
        client.loop_forever()
    except KeyboardInterrupt:
        log.info("stopped by user")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
