#!/usr/bin/env python3
"""
fleet_query_tools.py
====================
SAM Agent tools for querying the sensor pipeline SQLite database.

These tools are used by SAM agents (with LLM) to answer user questions
about sensor status, alerts, and fleet health.

The actual data processing happens in the deterministic pipeline (mock_pipeline.py).
These tools only READ from the database - they don't process raw sensor data.
"""

import json
import os
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode
from urllib.request import urlopen

# Import the database module
import sensor_db


def _debug_sketch_evidence_enabled() -> bool:
    """Feature flag for sketch-evidence debug output."""
    return os.getenv("FLEET_QUERY_DEBUG_SKETCH_EVIDENCE", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _build_sketch_debug_block(sketch_count: int) -> Optional[dict]:
    """Build debug metadata block when sketch-evidence debugging is enabled."""
    if not _debug_sketch_evidence_enabled():
        return None
    return {
        "sketch_evidence_enabled": True,
        "sketch_evidence_count": int(sketch_count),
        "sketch_evidence_note": f"Sketch evidence: {int(sketch_count)} sketches reviewed.",
    }


def get_recent_alerts(minutes: int = 60, severity: str = None, limit: int = 20) -> str:
    """
    Query recent alerts from the sensor pipeline.
    
    Use this tool when the user asks about:
    - Recent alerts or warnings
    - What anomalies have occurred
    - Critical events in the system
    
    Args:
        minutes: How far back to look (default 60 minutes)
        severity: Filter by severity (LOW, MEDIUM, HIGH, CRITICAL) or None for all
        limit: Maximum number of alerts to return
    
    Returns:
        JSON string with alert details
    """
    try:
        alerts = sensor_db.get_recent_alerts(minutes=minutes, severity=severity, limit=limit)
        
        if not alerts:
            return json.dumps({
                "status": "ok",
                "message": f"No alerts found in the last {minutes} minutes" + 
                          (f" with severity {severity}" if severity else ""),
                "alerts": [],
                "count": 0
            })
        
        return json.dumps({
            "status": "ok",
            "time_window_minutes": minutes,
            "count": len(alerts),
            "alerts": alerts
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def get_alert_summary(minutes: int = 60) -> str:
    """
    Get a summary of all alerts in the time window.
    
    Use this tool when the user asks for:
    - An overview of alert activity
    - Alert statistics
    - Which sensors have the most issues
    
    Args:
        minutes: How far back to look (default 60 minutes)
    
    Returns:
        JSON string with alert summary statistics
    """
    try:
        summary = sensor_db.get_alert_summary(minutes=minutes)
        return json.dumps({
            "status": "ok",
            **summary
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def get_fleet_status() -> str:
    """
    Get the current fleet health status.
    
    Use this tool when the user asks about:
    - Overall fleet health
    - How many sensors are active
    - Current system status
    
    Returns:
        JSON string with current fleet status
    """
    try:
        status = sensor_db.get_current_fleet_status()
        return json.dumps({
            "status": "ok",
            **status
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def get_fleet_history(minutes: int = 60) -> str:
    """
    Get fleet status history over time.
    
    Use this tool when the user asks about:
    - How fleet status has changed
    - When issues started
    - Fleet health trends
    
    Args:
        minutes: How far back to look (default 60 minutes)
    
    Returns:
        JSON string with fleet status history
    """
    try:
        history = sensor_db.get_fleet_status_history(minutes=minutes)
        return json.dumps({
            "status": "ok",
            "time_window_minutes": minutes,
            "snapshots": history
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def get_sensor_details(sensor_id: str, minutes: int = 30) -> str:
    """
    Get detailed information about a specific sensor.

    NOTE: This tool enforces sketch-first retrieval for incident context.
    
    Use this tool when the user asks about:
    - A specific sensor by name/ID
    - Temperature history for a sensor
    - What happened to sensor-001, etc.
    
    Args:
        sensor_id: The sensor identifier (e.g., "sensor-001")
        minutes: How far back to look (default 30 minutes)
    
    Returns:
        JSON string with sensor details and history
    """
    try:
        # Sketch-first retrieval to preserve incident narrative context.
        sketches = sensor_db.get_recent_sketches(minutes=minutes, sensor_id=sensor_id, limit=10)
        readings = sensor_db.get_sensor_history(sensor_id=sensor_id, minutes=minutes)
        
        # Get alerts for this sensor
        all_alerts = sensor_db.get_recent_alerts(minutes=minutes, limit=100)
        sensor_alerts = [a for a in all_alerts if a.get("sensor_id") == sensor_id]
        
        if not readings and not sketches:
            return json.dumps({
                "status": "ok",
                "message": f"No data found for sensor '{sensor_id}' in the last {minutes} minutes",
                "sensor_id": sensor_id
            })
        
        # Calculate stats from readings
        temps = [r["temperature"] for r in readings] if readings else []
        stats = {
            "reading_count": len(temps),
            "current_temp": temps[0] if temps else None,
            "avg_temp": round(sum(temps) / len(temps), 2) if temps else None,
            "min_temp": min(temps) if temps else None,
            "max_temp": max(temps) if temps else None,
        }
        
        response = {
            "status": "ok",
            "sensor_id": sensor_id,
            "time_window_minutes": minutes,
            "source_order": ["sketches", "sensor_history", "alerts"],
            "statistics": stats,
            "recent_readings": readings[:10],  # Last 10 readings
            "recent_sketches": sketches[:5],    # Last 5 sketches
            "alerts": sensor_alerts
        }

        debug_block = _build_sketch_debug_block(len(sketches[:5]))
        if debug_block:
            response["debug"] = debug_block

        return json.dumps(response, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def get_sketches(minutes: int = 30, zone: str = None, limit: int = 20) -> str:
    """
    Get recent sketch summaries from the pipeline.
    
    Use this tool when the user asks about:
    - Recent sensor activity summaries
    - What's been happening with the sensors
    - Natural language descriptions of sensor behavior
    
    Args:
        minutes: How far back to look (default 30 minutes)
        zone: Filter by zone (NORMAL, WARNING, CRITICAL) or None for all
        limit: Maximum number of sketches to return
    
    Returns:
        JSON string with sketch summaries
    """
    try:
        sketches = sensor_db.get_recent_sketches(minutes=minutes, zone=zone, limit=limit)
        
        if not sketches:
            return json.dumps({
                "status": "ok",
                "message": f"No sketches found in the last {minutes} minutes" +
                          (f" in {zone} zone" if zone else ""),
                "sketches": [],
                "count": 0
            })
        
        return json.dumps({
            "status": "ok",
            "time_window_minutes": minutes,
            "count": len(sketches),
            "sketches": sketches
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def get_incident_context(sensor_id: str, minutes: int = 90) -> str:
    """
    Deterministic incident context loader with sketch-first ordering.

    This tool is intended for detailed incident investigations. It always
    reads sketches first (narrative/timeline context), then enriches with
    numeric details and recent alerts for the same sensor.

    Args:
        sensor_id: Sensor/cooling-asset identifier (for example: m3-temp-motor)
        minutes: Time window to analyze

    Returns:
        JSON string containing ordered incident context.
    """
    try:
        # 1) Sketches first (required)
        sketches = sensor_db.get_recent_sketches(
            minutes=minutes,
            sensor_id=sensor_id,
            limit=25,
        )

        # 2) Numeric drill-down
        readings = sensor_db.get_sensor_history(sensor_id=sensor_id, minutes=minutes, limit=50)

        # 3) Alerts for same sensor in window
        all_alerts = sensor_db.get_recent_alerts(minutes=minutes, limit=200)
        sensor_alerts = [a for a in all_alerts if a.get("sensor_id") == sensor_id]

        temps = [r["temperature"] for r in readings] if readings else []
        stats = {
            "reading_count": len(readings),
            "sketch_count": len(sketches),
            "alert_count": len(sensor_alerts),
            "current_temp": temps[0] if temps else None,
            "avg_temp": round(sum(temps) / len(temps), 2) if temps else None,
            "min_temp": min(temps) if temps else None,
            "max_temp": max(temps) if temps else None,
        }

        response = {
            "status": "ok",
            "sensor_id": sensor_id,
            "time_window_minutes": minutes,
            "source_order": ["sketches", "sensor_details", "alerts"],
            "statistics": stats,
            "sketches": sketches,
            "recent_readings": readings,
            "alerts": sensor_alerts,
        }

        debug_block = _build_sketch_debug_block(len(sketches))
        if debug_block:
            response["debug"] = debug_block

        return json.dumps(response, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def get_system_statistics() -> str:
    """
    Get overall system statistics.
    
    Use this tool when the user asks about:
    - How much data has been processed
    - System metrics
    - Database statistics
    
    Returns:
        JSON string with system statistics
    """
    try:
        stats = sensor_db.get_statistics()
        return json.dumps({
            "status": "ok",
            **stats
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def get_chart_series(
    sensor_id: str,
    minutes: int = 60,
    source: str = "filtered",
    resolution: str = "1m",
    max_points: int = 120,
    compact: bool = True,
) -> str:
    """
    Fetch chart-ready deterministic series from chart_query_service.

    This is the preferred low-latency chart path for per-asset trend questions.
    It avoids large SQL payloads and returns a compact labels/values+stats bundle.

    Args:
        sensor_id: Cooling asset/sensor id (e.g. m3-temp-motor)
        minutes: Relative lookback window in minutes
        source: filtered|suppressed|all
        resolution: 1m|10s|points
        max_points: Max points returned by microservice
        compact: If true, omit verbose row payload in tool output

    Returns:
        JSON string
    """
    try:
        base = os.getenv("CHART_QUERY_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
        params = {
            "sensor_id": sensor_id,
            "minutes": int(minutes),
            "source": source,
            "resolution": resolution,
            "max_points": int(max_points),
        }
        url = f"{base}/series?{urlencode(params)}"

        with urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if not isinstance(data, dict) or "meta" not in data:
            return json.dumps(
                {
                    "status": "error",
                    "message": "Unexpected response from chart_query_service",
                    "service_url": url,
                },
                indent=2,
            )

        out = {
            "status": "ok",
            "service": "chart_query_service",
            "meta": data.get("meta", {}),
            "stats": data.get("stats", {}),
            "labels_hhmm_utc": data.get("labels_hhmm_utc", []),
            "values": data.get("values", []),
        }
        if not compact:
            out["rows"] = data.get("rows", [])

        return json.dumps(out, indent=2)
    except Exception as e:
        return json.dumps(
            {
                "status": "error",
                "message": str(e),
                "hint": "Ensure chart_query_service is running on CHART_QUERY_BASE_URL.",
            },
            indent=2,
        )


def get_plotly_spec(
    sensor_id: str,
    minutes: int = 60,
    source: str = "filtered",
    resolution: str = "1m",
    max_points: int = 120,
    value_key: str = "avg_v",
) -> str:
    """
    Fetch deterministic Plotly figure spec JSON from chart_query_service.

    Use this when the caller needs a chart-renderer-friendly spec (no Mermaid).
    """
    try:
        base = os.getenv("CHART_QUERY_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
        params = {
            "sensor_id": sensor_id,
            "minutes": int(minutes),
            "source": source,
            "resolution": resolution,
            "max_points": int(max_points),
            "value_key": value_key,
        }
        url = f"{base}/plotly-spec?{urlencode(params)}"

        with urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if not isinstance(data, dict) or "plotly_spec" not in data:
            return json.dumps(
                {
                    "status": "error",
                    "message": "Unexpected response from chart_query_service /plotly-spec",
                    "service_url": url,
                },
                indent=2,
            )

        meta = data.get("meta", {}) or {}
        stats = data.get("stats", {}) or {}
        pinned_start = stats.get("source_min_ts")
        pinned_end = stats.get("source_max_ts")
        pinned_url = None
        if pinned_start and pinned_end:
            pinned_params = {
                "sensor_id": sensor_id,
                "source": source,
                "resolution": resolution,
                "max_points": int(max_points),
                "value_key": value_key,
                "window_start": pinned_start,
                "window_end": pinned_end,
            }
            pinned_url = f"{base}/plotly-html?{urlencode(pinned_params)}"

        out = {
            "status": "ok",
            "service": "chart_query_service",
            "meta": meta,
            "stats": stats,
            "plotly_spec": data.get("plotly_spec", {}),
        }
        if pinned_url:
            out["plotly_html_url_pinned"] = pinned_url

        return json.dumps(out, indent=2)
    except Exception as e:
        return json.dumps(
            {
                "status": "error",
                "message": str(e),
                "hint": "Ensure chart_query_service is running on CHART_QUERY_BASE_URL.",
            },
            indent=2,
        )


def acknowledge_alert(alert_id: int) -> str:
    """
    Acknowledge an alert (mark it as reviewed).
    
    Use this tool when the user wants to:
    - Acknowledge an alert
    - Mark an alert as reviewed
    - Clear an alert notification
    
    Args:
        alert_id: The ID of the alert to acknowledge
    
    Returns:
        JSON string confirming the action
    """
    try:
        with sensor_db.get_connection() as conn:
            result = conn.execute(
                "UPDATE alerts SET acknowledged = TRUE WHERE id = ?",
                (alert_id,)
            )
            if result.rowcount > 0:
                return json.dumps({
                    "status": "ok",
                    "message": f"Alert {alert_id} acknowledged"
                })
            else:
                return json.dumps({
                    "status": "error",
                    "message": f"Alert {alert_id} not found"
                })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})
