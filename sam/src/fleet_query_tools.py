#!/usr/bin/env python3
"""
fleet_query_tools.py
====================
SAM Agent tools for querying the sensor pipeline SQLite database.

These tools are used by SAM agents (with LLM) to answer user questions
about sensor status, alerts, and fleet health.

The actual data processing happens in the deterministic pipeline
(deadband_service.py → sketch_service.py → anomaly_service.py).
These tools only READ from the database - they don't process raw sensor data.
"""

import json
import os
import re
from datetime import datetime
from typing import Optional, Tuple
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

# Import the database module
import sensor_db
import dispatch_workforce


_BROWSER_UNREACHABLE_HOSTS = frozenset(
    {"chart-query", "localhost", "127.0.0.1", "0.0.0.0", "::1"}
)


def _is_browser_unreachable_chart_base(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in _BROWSER_UNREACHABLE_HOSTS


def _derive_public_chart_base_from_dashboard_host() -> str:
    """
    When CHART_PUBLIC_BASE_URL is unset, build a browser/Slack-safe base from
    DASHBOARD_PUBLIC_HOST (same host used for demo_dashboard.config.json).
    """
    host = os.getenv("DASHBOARD_PUBLIC_HOST", "").strip()
    if not host:
        return ""
    prefix = os.getenv("CHART_PUBLIC_PATH_PREFIX", "/charts").strip()
    if prefix:
        prefix = "/" + prefix.strip("/")
        return f"http://{host}{prefix}".rstrip("/")
    port = os.getenv("CHART_QUERY_PUBLISH_PORT", os.getenv("CHART_QUERY_PORT", "8010"))
    return f"http://{host}:{port}".rstrip("/")


def _chart_query_internal_public_bases() -> Tuple[str, str]:
    """
    Internal base: GET /series and /plotly-spec from this host (SAM tools).
    Public base: plotly_html_url_pinned for humans (e.g. Slack); must be reachable
    outside Docker (not chart-query).
    """
    internal = os.getenv("CHART_QUERY_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
    pub = os.getenv("CHART_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if pub:
        return internal, pub
    if not _is_browser_unreachable_chart_base(internal):
        return internal, internal
    derived = _derive_public_chart_base_from_dashboard_host()
    if derived:
        return internal, derived
    return internal, internal


def chart_public_base_for_links() -> str:
    """Browser/Slack-safe chart-query base URL (no trailing slash)."""
    _, public = _chart_query_internal_public_bases()
    return public


def _chart_query_api_key() -> str:
    """Optional auth token for chart-query. Empty string means auth is disabled."""
    return os.getenv("CHART_QUERY_API_KEY", "").strip()


def _open_chart_query(url: str, timeout: int = 8):
    """urlopen wrapper that sends X-API-Key when CHART_QUERY_API_KEY is set."""
    key = _chart_query_api_key()
    if key:
        return urlopen(Request(url, headers={"X-API-Key": key}), timeout=timeout)
    return urlopen(url, timeout=timeout)


_CHART_URL_INTERNAL_HOST_RE = re.compile(
    r"https?://(?:chart-query|127\.0\.0\.1|localhost)(?::\d+)?",
    re.IGNORECASE,
)


def rewrite_chart_urls_in_text(text: str) -> str:
    """
    Replace Docker-only chart-query hostnames in free text (e.g. LLM reports)
    with CHART_PUBLIC_BASE_URL or DASHBOARD_PUBLIC_HOST-derived base.
    """
    public = chart_public_base_for_links()
    if not public or _is_browser_unreachable_chart_base(public):
        return text
    return _CHART_URL_INTERNAL_HOST_RE.sub(public.rstrip("/"), text)


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


def _incident_telemetry_coverage(sensor_id: str) -> dict:
    """
    What this demo models per probe id (temperature-only streams), matching
    demo_publisher telemetry_availability on raw/filtered MQTT.
    """
    sid = (sensor_id or "").lower()
    if "temp-outlet" in sid or sid.endswith("outlet"):
        return {
            "summary": (
                "Only outlet temperature telemetry is present in this incident bundle; "
                "inlet airflow, humidity, and pressure signals are not included."
            ),
            "signals_present": ["outlet_temperature"],
            "signals_not_in_bundle": [
                "inlet_airflow",
                "humidity",
                "differential_pressure",
            ],
            "correlation_hint": (
                "For cross-probe analysis, query inlet/motor sensor ids on the same machine separately."
            ),
        }
    if "temp-inlet" in sid or sid.endswith("inlet"):
        return {
            "summary": (
                "Only inlet temperature appears in this simulated stream; airflow, humidity, "
                "and pressure are not included."
            ),
            "signals_present": ["inlet_temperature"],
            "signals_not_in_bundle": [
                "airflow",
                "humidity",
                "differential_pressure",
            ],
        }
    if "temp-motor" in sid or sid.endswith("motor"):
        return {
            "summary": (
                "Motor winding temperature only in this bundle; airflow, humidity, pressure, "
                "and vibration are not modeled on this stream."
            ),
            "signals_present": ["motor_temperature"],
            "signals_not_in_bundle": [
                "inlet_airflow",
                "humidity",
                "differential_pressure",
                "bearing_vibration",
            ],
        }
    return {
        "summary": (
            "Demo telemetry is temperature-centric per probe; humidity, pressure, and "
            "airflow are not included in this bundle unless integrated separately."
        ),
        "signals_present": ["temperature"],
        "signals_not_in_bundle": ["inlet_airflow", "humidity", "differential_pressure"],
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
            "telemetry_coverage": _incident_telemetry_coverage(sensor_id),
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
        internal, _ = _chart_query_internal_public_bases()
        params = {
            "sensor_id": sensor_id,
            "minutes": int(minutes),
            "source": source,
            "resolution": resolution,
            "max_points": int(max_points),
        }
        url = f"{internal}/series?{urlencode(params)}"

        with _open_chart_query(url, timeout=8) as resp:
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
        internal, public = _chart_query_internal_public_bases()
        params = {
            "sensor_id": sensor_id,
            "minutes": int(minutes),
            "source": source,
            "resolution": resolution,
            "max_points": int(max_points),
            "value_key": value_key,
        }
        url = f"{internal}/plotly-spec?{urlencode(params)}"

        with _open_chart_query(url, timeout=8) as resp:
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
            # Slack-clickable URLs hit /plotly-html as plain GETs in a browser — no headers, so the
            # API key (when configured) has to travel as a query param.
            api_key = _chart_query_api_key()
            if api_key:
                pinned_params["key"] = api_key
            pinned_url = f"{public}/plotly-html?{urlencode(pinned_params)}"

        out = {
            "status": "ok",
            "service": "chart_query_service",
            "meta": meta,
            "stats": stats,
            "plotly_spec": data.get("plotly_spec", {}),
        }
        if pinned_url:
            out["plotly_html_url_pinned"] = pinned_url
        if pinned_url and _is_browser_unreachable_chart_base(pinned_url):
            out["chart_link_warning"] = (
                "plotly_html_url_pinned uses an internal Docker hostname. "
                "Set CHART_PUBLIC_BASE_URL or DASHBOARD_PUBLIC_HOST in the SAM environment."
            )

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


def get_dispatch_workforce_directory() -> str:
    """
    Return the mock CMMS / workforce directory (read-only demo fixture).

    Use for questions about who is available, skills, sites, or shift coverage.
    This is NOT connected to a real HR or CMMS system.
    """
    try:
        doc = dispatch_workforce.load_workforce_document()
        if doc.get("error"):
            return json.dumps({"status": "error", "message": doc["error"]}, indent=2)
        techs = dispatch_workforce.list_technicians()
        return json.dumps(
            {
                "status": "ok",
                "data_source": "mock_cmms_fixture",
                "fixture_path": str(dispatch_workforce._data_path()),
                "schema_version": doc.get("schema_version"),
                "description": doc.get("description"),
                "count": len(techs),
                "technicians": techs,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, indent=2)


def recommend_dispatch_technicians(
    sensor_id: str,
    incident_zone: str = None,
    urgency: str = "high",
    top_n: int = 3,
) -> str:
    """
    Rank mock technicians for a cooling-asset incident (deterministic demo scoring).

    Call AFTER get_incident_context for detailed investigations so sensor_id and
    zone align with the incident under analysis. Read-only: does not create tickets.

    Args:
        sensor_id: Same cooling asset id used with get_incident_context (e.g. m3-temp-motor).
        incident_zone: Optional zone string (e.g. CRITICAL) to tune skill hints.
        urgency: high (default) or critical for stronger weighting on incident leads.
        top_n: Number of ranked recommendations to return (default 3, max 10).
    """
    try:
        out = dispatch_workforce.recommend_technicians(
            sensor_id=sensor_id or "",
            incident_zone=incident_zone,
            urgency=urgency or "high",
            top_n=int(top_n) if top_n is not None else 3,
        )
        return json.dumps(out, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, indent=2)
