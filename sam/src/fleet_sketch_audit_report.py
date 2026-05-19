#!/usr/bin/env python3
"""
fleet_sketch_audit_report.py
============================
Builds a structured post-incident audit from recent SQLite sketches when
FLEET_CRITICAL triggers automated fleet analysis.

Consumers (e.g. bridge to S3) subscribe to the MQTT topic published by
fleet_alert_analyzer (default: sensors/fleet/audit-report).

To publish a test message without waiting for FLEET_CRITICAL:

    cd sam && python src/fleet_alert_analyzer.py --audit-only

For analysis-request plus audit together without the default debounce wait:

    python src/fleet_alert_analyzer.py --now

Intentionally deterministic only (no SAM / LLM): aggregation and heuristics
over stored sketches. Optional LLM enrichment belongs in a separate path
if needed later.
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import sensor_db
except ImportError:
    sensor_db = None


def _parse_ts(ts: str) -> Optional[datetime]:
    if not ts or not isinstance(ts, str):
        return None
    s = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _hours_between(a: datetime, b: datetime) -> float:
    return abs((b - a).total_seconds()) / 3600.0


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _aggregate_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_sensor: Dict[str, Dict[str, Any]] = {}
    zone_totals = {"NORMAL": 0, "WARNING": 0, "CRITICAL": 0, "OTHER": 0}
    first_non_normal: Optional[Tuple[datetime, str, str]] = None
    last_row_ts: Optional[datetime] = None

    for row in rows:
        sid = row.get("sensor_id") or "unknown"
        zone = (row.get("zone") or "NORMAL").upper()
        ts_raw = row.get("timestamp") or ""
        ts = _parse_ts(ts_raw)
        if ts and (last_row_ts is None or ts > last_row_ts):
            last_row_ts = ts

        if sid not in by_sensor:
            by_sensor[sid] = {
                "count": 0,
                "by_zone": {"NORMAL": 0, "WARNING": 0, "CRITICAL": 0, "OTHER": 0},
                "first_ts": ts_raw,
                "last_ts": ts_raw,
            }
        agg = by_sensor[sid]
        agg["count"] += 1
        agg["last_ts"] = ts_raw
        zkey = zone if zone in zone_totals else "OTHER"
        agg["by_zone"][zkey] = agg["by_zone"].get(zkey, 0) + 1
        zone_totals[zkey] = zone_totals.get(zkey, 0) + 1

        if zone not in ("NORMAL",) and ts:
            if first_non_normal is None or ts < first_non_normal[0]:
                first_non_normal = (ts, sid, zone)

    return {
        "by_sensor": by_sensor,
        "zone_totals": zone_totals,
        "first_non_normal": (
            {
                "timestamp": first_non_normal[0].isoformat().replace("+00:00", "Z"),
                "sensor_id": first_non_normal[1],
                "zone": first_non_normal[2],
            }
            if first_non_normal
            else None
        ),
        "last_sketch_timestamp": (
            last_row_ts.isoformat().replace("+00:00", "Z") if last_row_ts else None
        ),
    }


def _pick_samples(rows: List[Dict[str, Any]], max_rows: int = 18) -> List[Dict[str, Any]]:
    """Most recent WARNING/CRITICAL sketches (trimmed for payload size)."""
    interesting = [r for r in rows if (r.get("zone") or "").upper() in ("WARNING", "CRITICAL")]
    interesting.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
    out: List[Dict[str, Any]] = []
    for r in interesting[:max_rows]:
        out.append(
            {
                "sensor_id": r.get("sensor_id"),
                "timestamp": r.get("timestamp"),
                "zone": r.get("zone"),
                "temperature": r.get("temperature"),
                "trend": r.get("trend"),
                "sketch_excerpt": _truncate(r.get("sketch") or "", 320),
            }
        )
    return out


def _avoidability_assessment(
    rows: List[Dict[str, Any]],
    agg: Dict[str, Any],
    trigger_ts_iso: str,
    days: int,
) -> Dict[str, Any]:
    trigger = _parse_ts(trigger_ts_iso) or datetime.now(timezone.utc)
    total = len(rows)
    nn = sum(1 for r in rows if (r.get("zone") or "").upper() in ("WARNING", "CRITICAL"))
    fn = agg.get("first_non_normal")
    signals: List[str] = []

    if total == 0:
        return {
            "verdict": "insufficient_data",
            "summary": (
                f"No sketch rows in SQLite in the last {days} days for the scoped sensors. "
                "Run sketch_service against this DB, or widen sensor scope."
            ),
            "signals": signals,
        }

    hours_early: Optional[float] = None
    if fn and fn.get("timestamp"):
        t0 = _parse_ts(fn["timestamp"])
        if t0:
            hours_early = _hours_between(t0, trigger)
            signals.append(
                f"Earliest non-NORMAL sketch in window: {fn['timestamp']} "
                f"({fn.get('sensor_id')}, {fn.get('zone')})"
            )
            if hours_early >= 24:
                signals.append(
                    f"Non-NORMAL activity began ~{hours_early:.1f}h before fleet-analysis trigger."
                )

    rising_hits = sum(
        1
        for r in rows
        if re.search(r"\bRISING\b", str(r.get("trend") or ""), re.I)
        or re.search(r"\brising\b", str(r.get("sketch") or ""), re.I)
    )
    if rising_hits >= 5:
        signals.append(f"Found {rising_hits} sketch rows mentioning rising trend in the window.")

    if nn >= 80:
        verdict = "progressive_stress_likely"
        summary = (
            f"High non-NORMAL sketch volume ({nn}/{total}) over {days} days suggests "
            "gradual thermal stress; earlier capacity or maintenance actions might have reduced risk."
        )
    elif hours_early is not None and hours_early >= 36 and nn >= 10:
        verdict = "possibly_avoidable_with_earlier_ops"
        summary = (
            "Multiple non-NORMAL sketches appeared more than a day before this fleet incident; "
            "monitoring or runbooks could likely have intervened earlier."
        )
    elif nn < 8:
        verdict = "acute_or_sparse_history"
        summary = (
            "Few non-NORMAL sketches in the 3-day window relative to a FLEET_CRITICAL trigger; "
            "this may be a sudden correlated excursion or telemetry/sketch retention may be incomplete."
        )
    else:
        verdict = "mixed_or_inconclusive"
        summary = (
            "Sketch history shows some non-NORMAL activity but not a clear long runway; "
            "review per-sensor samples and maintenance logs for a fuller picture."
        )

    return {"verdict": verdict, "summary": summary, "signals": signals}


def build_fleet_sketch_audit_report(
    analysis_event: Dict[str, Any],
    collected_sensor_events: List[Dict[str, Any]],
    *,
    correlation_id: str,
    days: int = 3,
) -> Dict[str, Any]:
    """
    Build JSON-serializable audit document. Does not publish MQTT.
    """
    sensor_ids: List[str] = list(analysis_event.get("sensors") or [])
    for ev in collected_sensor_events or []:
        sid = ev.get("sensor_id")
        if sid and sid not in sensor_ids:
            sensor_ids.append(sid)

    rows: List[Dict[str, Any]] = []
    db_error: Optional[str] = None
    if sensor_db is None:
        db_error = "sensor_db not available"
    else:
        try:
            rows = sensor_db.get_sketches_since_days(
                days=days,
                sensor_ids=sensor_ids if sensor_ids else None,
                limit=100_000,
            )
        except Exception as exc:
            rows = []
            db_error = str(exc)

    agg = _aggregate_rows(rows)
    _now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    trigger_ts = analysis_event.get("timestamp") or _now_iso
    avoid = _avoidability_assessment(rows, agg, trigger_ts, days)

    report = {
        "event_type": "FLEET_SKETCH_AUDIT_REPORT",
        "schema_version": "1.0.0",
        "correlation_id": correlation_id,
        "parent_event_type": analysis_event.get("event_type"),
        "audit_window_days": days,
        "generated_at_utc": _now_iso,
        "fleet_context": {
            "fleet_status": analysis_event.get("fleet_status"),
            "critical_count": analysis_event.get("critical_count"),
            "active_sensors": analysis_event.get("active_sensors"),
            "sensors_in_scope": sensor_ids,
            "analysis_notes": analysis_event.get("notes"),
            "analysis_trigger_timestamp": trigger_ts,
        },
        "sketch_query": {
            "row_count": len(rows),
            "sensor_filter_applied": bool(sensor_ids),
            "db_error": db_error,
        },
        "aggregates": {
            "zone_totals": agg["zone_totals"],
            "per_sensor": agg["by_sensor"],
            "first_non_normal_in_window": agg["first_non_normal"],
            "last_sketch_timestamp_in_batch": agg["last_sketch_timestamp"],
        },
        "avoidability": avoid,
        "notable_sketches_sample": _pick_samples(rows),
    }

    return report
