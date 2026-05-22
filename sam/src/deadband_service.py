#!/usr/bin/env python3
"""
deadband_service.py
===================
Microservice 1/3: Deadband Filter

Subscribes to: dc/<DC_BROKER_SITE>/v1/raw/# (see pipeline_config.TOPIC_SENSOR_RAW;
               or dc/+/v1/raw/# when DC_PIPELINE_MULTISITE_RAW=true)
Publishes to:  dc/<DC_BROKER_SITE>/v1/pipeline/filtered (forwarded readings)
               dc/<DC_BROKER_SITE>/v1/pipeline/suppressed (filtered out readings)

Suppresses telemetry points that haven't changed significantly (per-metric deadband %)
unless a heartbeat interval has elapsed. Legacy gateway bundles (dc.raw.bundle.v1) are
expanded into one message per metric before filtering.

This is the first stage of the deterministic data plane - no LLM involved.
"""

import collections
import json
import time
from typing import Any, Dict, Optional, Tuple

import pipeline_config as config

# ── Deadband State (keyed by point_id = asset + metric) ──────────────────────
_last_value: Dict[str, float] = {}
_last_forward_ts: Dict[str, float] = {}
_windows: Dict[str, collections.deque] = {}


def reset_deadband_state() -> None:
    """Clear in-memory windows (for tests)."""
    _last_value.clear()
    _last_forward_ts.clear()
    _windows.clear()


def get_window_stats(point_id: str) -> Dict[str, Any]:
    """Calculate rolling window statistics for a telemetry point."""
    if point_id not in _windows or not _windows[point_id]:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "count": 0}
    vals = [v for _, v in _windows[point_id]]
    return {
        "mean": round(sum(vals) / len(vals), 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
        "count": len(vals),
    }


def calculate_trend(point_id: str) -> str:
    """Calculate value trend from window data."""
    if point_id not in _windows or len(_windows[point_id]) < 3:
        return "STABLE"

    vals = [v for _, v in _windows[point_id]]
    recent = vals[-3:]

    if all(recent[i] < recent[i + 1] for i in range(len(recent) - 1)):
        return "RISING"
    if all(recent[i] > recent[i + 1] for i in range(len(recent) - 1)):
        return "FALLING"
    return "STABLE"


def _metric_unit(metric_id: str) -> str:
    return str(config._metric_rule(metric_id).get("unit", ""))


def apply_deadband(
    point_id: str,
    metric_id: str,
    value: float,
    timestamp: str,
) -> Tuple[str, Dict[str, Any]]:
    """
    Apply deadband filter to one telemetry point.

    Returns: (action, result_dict) where action is "suppress" or "forward".
    """
    now = time.time()
    deadband_pct = config.deadband_pct_for(metric_id)
    heartbeat_secs = config.heartbeat_secs_for(metric_id)
    window_secs = config.window_secs_for(metric_id)

    if point_id not in _windows:
        _windows[point_id] = collections.deque(maxlen=50)
    _windows[point_id].append((now, value))

    cutoff = now - window_secs
    while _windows[point_id] and _windows[point_id][0][0] < cutoff:
        _windows[point_id].popleft()

    prev_val = _last_value.get(point_id)
    last_fwd = _last_forward_ts.get(point_id, 0)

    base = {
        "pointId": point_id,
        "sensorId": point_id,
        "metric": metric_id,
        "value": value,
        "temperature": value,
        "timestamp": timestamp,
    }

    if prev_val is not None:
        delta_pct = abs(value - prev_val) / max(abs(prev_val), 1.0)
        heartbeat_due = (now - last_fwd) >= heartbeat_secs

        if delta_pct < deadband_pct and not heartbeat_due:
            return "suppress", {
                **base,
                "action": "suppress",
                "reason": (
                    f"delta {delta_pct * 100:.1f}% < {deadband_pct * 100:.1f}% "
                    f"({metric_id})"
                ),
                "delta_pct": round(delta_pct, 4),
            }

        forwarded_reason = "heartbeat" if delta_pct < deadband_pct else "delta"
        delta_pct_out = delta_pct
    else:
        forwarded_reason = "first-reading"
        delta_pct_out = 0.0

    _last_value[point_id] = value
    _last_forward_ts[point_id] = now

    zone = config.classify_zone(value, metric_id)
    return "forward", {
        **base,
        "action": "forward",
        "zone": zone,
        "delta_pct": round(delta_pct_out, 4),
        "forwarded_reason": forwarded_reason,
        "window": get_window_stats(point_id),
        "trend": calculate_trend(point_id),
    }


def _enrich_result(
    result: Dict[str, Any],
    payload: Dict[str, Any],
    topic_meta: Dict[str, Any],
    asset_id: str,
    metric_id: str,
) -> None:
    result.update(
        {
            "schema": config.SCHEMA_FILTERED,
            "schemaRevision": config.SCHEMA_REVISION,
            "site": payload.get("site", topic_meta.get("site", config.DEFAULT_SITE)),
            "room": payload.get("room", topic_meta.get("room", config.DEFAULT_ROOM)),
            "row": payload.get("row", topic_meta.get("row")),
            "rack": payload.get("rack", topic_meta.get("rack")),
            "asset": payload.get("asset", topic_meta.get("asset", asset_id)),
            "metric": metric_id,
            "unit": payload.get("unit") or _metric_unit(metric_id),
        }
    )
    config.copy_raw_metadata_to_result(payload, result)


def process_observation(
    client: Any,
    payload: Dict[str, Any],
    topic_meta: Optional[Dict[str, Any]] = None,
    topic_value: Optional[float] = None,
) -> bool:
    """
    Run deadband for one scalar observation. Returns True if processed, False if skipped.
    """
    topic_meta = topic_meta or {}
    point_id, asset_id, metric_id = config.resolve_point_id(payload, topic_meta)
    if not point_id or metric_id == config.BUNDLE_TOPIC_METRIC:
        return False

    value = config.observation_value(payload, topic_value)
    if value is None:
        return False

    timestamp = payload.get("timestamp") or payload.get("ts") or config.now_utc_iso()

    action, result = apply_deadband(point_id, metric_id, value, timestamp)
    _enrich_result(result, payload, topic_meta, asset_id, metric_id)

    unit = result.get("unit") or ""
    unit_suffix = f" {unit}" if unit else ""

    if action == "suppress":
        print(f"[Deadband] SUPPRESS {point_id} | {result['reason']}")
        client.publish(config.TOPIC_SUPPRESSED, json.dumps(result))
    else:
        zone = result["zone"]
        print(
            f"[Deadband] FORWARD {point_id} | {value:.2f}{unit_suffix} | zone={zone}"
        )
        client.publish(config.TOPIC_FILTERED, json.dumps(result))
    return True


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print(f"[Deadband] Connected to {config.BROKER_HOST}")
        client.subscribe(config.TOPIC_SENSOR_RAW)
        print(f"[Deadband] Subscribed to {config.TOPIC_SENSOR_RAW}")
    else:
        print(f"[Deadband] Connection failed (rc={reason_code})")


def on_message(client, userdata, msg):
    """Process incoming raw telemetry (single-metric or gateway bundle)."""
    try:
        payload = json.loads(msg.payload.decode())
        topic_meta, temp_from_topic = config.parse_raw_topic_with_temperature(msg.topic)

        if config.is_bundle_payload(payload):
            expanded = config.expand_bundle_payload(payload)
            if not expanded:
                return
            print(
                f"[Deadband] BUNDLE {payload.get('asset')} -> {len(expanded)} point(s)"
            )
            for point_payload in expanded:
                process_observation(client, point_payload, topic_meta)
            return

        process_observation(client, payload, topic_meta, temp_from_topic)

    except Exception as e:
        print(f"[Deadband] Error: {e}")


def main():
    config.print_service_banner(
        "Deadband Filter",
        config.TOPIC_SENSOR_RAW,
        config.TOPIC_FILTERED,
    )

    client = config.create_mqtt_client("deadband")
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(config.BROKER_HOST, config.BROKER_PORT, keepalive=60)
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[Deadband] Stopped by user.")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
