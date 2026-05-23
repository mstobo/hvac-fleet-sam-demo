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
import re
from datetime import datetime
from typing import List, Optional, Tuple
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen

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


def _normalize_chart_public_base(url: str) -> str:
    """
    Normalize CHART_PUBLIC_BASE_URL for HTTP requests.

    Common mistake: ``http://host/#charts`` (browser hash from the dashboard SPA).
    Fragments are never sent to Apache — use path ``http://host/charts`` instead.
    """
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""
    # http://host/#charts or http://host#charts → http://host/charts
    if "#charts" in u.lower():
        base = u.split("#", 1)[0].rstrip("/")
        u = f"{base}/charts"
    return u.rstrip("/")


def _infer_public_chart_base() -> str:
    """
    Browser-reachable chart base for Slack links.

    Order: CHART_PUBLIC_BASE_URL → DASHBOARD_PUBLIC_HOST/charts → EC2_PUBLIC_HOST/charts.
    """
    pub = _normalize_chart_public_base(os.getenv("CHART_PUBLIC_BASE_URL", ""))
    if pub and not _is_browser_unreachable_chart_base(pub):
        return pub
    derived = _derive_public_chart_base_from_dashboard_host()
    if derived and not _is_browser_unreachable_chart_base(derived):
        return derived
    host = (
        os.getenv("EC2_PUBLIC_HOST", "").strip()
        or os.getenv("PUBLIC_DEMO_HOST", "").strip()
        or os.getenv("DEMO_PUBLIC_HOST", "").strip()
    )
    if host:
        host = host.replace("https://", "").replace("http://", "").split("/")[0].strip()
        prefix = (os.getenv("CHART_PUBLIC_PATH_PREFIX", "/charts") or "/charts").strip()
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        base = f"http://{host}{prefix}".rstrip("/")
        if not _is_browser_unreachable_chart_base(base):
            return base
    return ""


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
    pub = _infer_public_chart_base()
    if pub:
        return internal, pub
    if not _is_browser_unreachable_chart_base(internal):
        return internal, internal
    return internal, internal


def chart_public_base_for_links() -> str:
    """Browser/Slack-safe chart-query base URL (no trailing slash)."""
    _, public = _chart_query_internal_public_bases()
    return public


_CHART_URL_INTERNAL_HOST_RE = re.compile(
    r"https?://(?:chart-query|127\.0\.0\.1|localhost)(?::\d+)?",
    re.IGNORECASE,
)

# LLM sometimes writes placeholders when plotly_html_url_pinned is missing from tool JSON.
_CHART_PLACEHOLDER_RE = re.compile(
    r"Chart:\s*\(([^)]+)\)\s*plot window\s*"
    r"(\d{4}-\d{2}-\d{2}T[\d:.]+Z)\s*→\s*(\d{4}-\d{2}-\d{2}T[\d:.]+Z)",
    re.IGNORECASE,
)

_ISO_WINDOW_RE = (
    r"(?P<start>\d{4}-\d{2}-\d{2}T[\d:.]+Z)\s*→\s*(?P<end>\d{4}-\d{2}-\d{2}T[\d:.]+Z)"
)

# Fleet analysis often emits this instead of pasting plotly_html_url_pinned from get_plotly_spec.
_CHART_SPEC_GENERATED_RE = re.compile(
    rf"(?P<prefix>^[-•]\s*)?"
    rf"(?P<machine>machine-\d{{3}})\s*:\s*"
    rf"(?P<label>inlet|motor|outlet|supply)\s*temp\s*chart"
    rf"(?:\s*\((?P<value_key>max_v|avg_v|last_v)\))?"
    rf"\s*-\s*chart spec generated for\s*{_ISO_WINDOW_RE}",
    re.IGNORECASE | re.MULTILINE,
)

_CHART_LABEL_TO_METRIC = {
    "inlet": "inlet_temp_c",
    "motor": "motor_temp_c",
    "outlet": "outlet_temp_c",
    "supply": "supply_temp_c",
}


def _value_key_for_public_chart_link(requested: Optional[str]) -> str:
    """Rollups often have sparse max_v; avg_v renders reliably in /plotly-html."""
    key = (requested or "avg_v").strip().lower()
    if key == "max_v":
        return "avg_v"
    if key in ("avg_v", "last_v", "min_v"):
        return key
    return "avg_v"


def _legacy_probe_for_point_id(point_id: str) -> Optional[str]:
    """Map canonical point id (machine-002:motor_temp_c) to demo probe id (m2-temp-motor)."""
    pid = (point_id or "").strip()
    if not pid or ":" not in pid:
        return pid or None
    asset, metric = pid.split(":", 1)
    prefix = {
        "machine-001": "m1",
        "machine-002": "m2",
        "machine-003": "m3",
    }.get(asset)
    if not prefix:
        return None
    if metric == "inlet_temp_c":
        return f"{prefix}-temp-inlet"
    if metric == "outlet_temp_c":
        return f"{prefix}-temp-outlet"
    if metric == "motor_temp_c":
        return f"{prefix}-temp-motor"
    if metric == "humidity_rh":
        return f"{prefix}-humidity"
    if metric == "motor_vibration_mm_s":
        return f"{prefix}-vibration"
    if metric == "supply_temp_c":
        return f"{prefix}-temp-motor"
    return None


def _chart_query_key_for_public_links() -> str:
    """
    When chart-query auth is enabled, browser/Slack plot links need ?key= (see chart_query_service).
    SAM containers load the same value from ENV_FILE as the chart-query service.
    """
    return os.getenv("CHART_QUERY_API_KEY", "").strip()


def _format_slack_chart_link(
    point_id: str,
    start: str,
    end: str,
    *,
    value_key: Optional[str] = None,
    prefix: str = "",
    label: Optional[str] = None,
) -> str:
    url = build_plotly_html_url(
        point_id,
        value_key=_value_key_for_public_chart_link(value_key),
        window_start=start,
        window_end=end,
    )
    if not url:
        return ""
    name = label or point_id
    lead = prefix or ""
    return f"{lead}{name}: <{url}> ({start} → {end})"


_POINT_HEADING_RE = re.compile(
    r"^###\s*(?P<point_id>machine-\d{3}:[a-z0-9_]+)\s*$",
    re.IGNORECASE,
)

_ANALYSIS_WINDOW_PATTERNS = (
    re.compile(rf"Analysis window:\s*{_ISO_WINDOW_RE}", re.IGNORECASE),
    re.compile(rf"(?:UTC window|120-minute UTC window)\s*{_ISO_WINDOW_RE}", re.IGNORECASE),
)

_NO_PINNED_URLS_NOTE_RE = re.compile(
    r"\(note:\s*chart service returned plot specs but no pinned public URLs[^)]*\)\.?\s*",
    re.IGNORECASE,
)


def _extract_analysis_window(text: str) -> Optional[Tuple[str, str]]:
    for pat in _ANALYSIS_WINDOW_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group("start"), m.group("end")
    m = re.search(_ISO_WINDOW_RE, text)
    if m:
        return m.group("start"), m.group("end")
    return None


def inject_fleet_analysis_chart_links(text: str) -> str:
    """
    After fleet analysis reports with ### machine-00x:metric headings, insert plotly-html
    links for the cited UTC window when the LLM did not paste plotly_html_url_pinned.
    """
    window = _extract_analysis_window(text)
    if not window:
        return text
    start, end = window
    public = chart_public_base_for_links()
    if not public or _is_browser_unreachable_chart_base(public):
        return text

    lines = text.splitlines()
    out: List[str] = []
    injected = 0
    for i, line in enumerate(lines):
        out.append(line)
        m = _POINT_HEADING_RE.match(line.strip())
        if not m:
            continue
        point_id = m.group("point_id")
        lookahead = "\n".join(lines[i + 1 : i + 4])
        if "plotly-html" in lookahead:
            continue
        link = _format_slack_chart_link(point_id, start, end, prefix="- Chart: ")
        if link:
            out.append(link)
            injected += 1

    if not injected:
        return text
    merged = "\n".join(out)
    merged = _NO_PINNED_URLS_NOTE_RE.sub("", merged)
    return re.sub(r"\n{3,}", "\n\n", merged)


def build_plotly_html_url(
    sensor_id: str,
    *,
    minutes: int = 120,
    source: str = "filtered",
    resolution: str = "1m",
    max_points: int = 120,
    value_key: str = "max_v",
    window_start: Optional[str] = None,
    window_end: Optional[str] = None,
) -> Optional[str]:
    """Browser-safe /plotly-html URL for Slack (uses CHART_PUBLIC_BASE_URL when set)."""
    public = chart_public_base_for_links()
    if not public or _is_browser_unreachable_chart_base(public):
        return None
    params = {
        "sensor_id": sensor_id,
        "source": source,
        "resolution": resolution,
        "max_points": int(max_points),
        "value_key": value_key,
        "minutes": int(minutes),
    }
    if window_start and window_end:
        params.pop("minutes", None)
        params["window_start"] = window_start
        params["window_end"] = window_end
    link_key = _chart_query_key_for_public_links()
    if link_key:
        params["key"] = link_key
    return f"{public.rstrip('/')}/plotly-html?{urlencode(params)}"


def rewrite_chart_urls_in_text(text: str) -> str:
    """
    Replace Docker-only chart-query hostnames in free text (e.g. LLM reports)
    with CHART_PUBLIC_BASE_URL or DASHBOARD_PUBLIC_HOST-derived base.

    Also turns chart placeholders into Slack angle-bracket plotly-html links when the
    LLM did not paste plotly_html_url_pinned from get_plotly_spec.
    """
    if not text:
        return text
    public = chart_public_base_for_links()
    if public and not _is_browser_unreachable_chart_base(public):
        text = _CHART_URL_INTERNAL_HOST_RE.sub(public.rstrip("/"), text)

        def _placeholder_link(match: re.Match) -> str:
            point_id = match.group(1).strip()
            start, end = match.group(2), match.group(3)
            linked = _format_slack_chart_link(point_id, start, end, value_key="max_v", prefix="Chart: ")
            return linked or match.group(0)

        def _chart_spec_generated_link(match: re.Match) -> str:
            machine = match.group("machine")
            label = (match.group("label") or "").lower()
            metric = _CHART_LABEL_TO_METRIC.get(label)
            if not metric:
                return match.group(0)
            point_id = f"{machine}:{metric}"
            start, end = match.group("start"), match.group("end")
            linked = _format_slack_chart_link(
                point_id,
                start,
                end,
                value_key=match.group("value_key"),
                prefix=match.group("prefix") or "- ",
                label=f"{machine} {label} temp",
            )
            return linked or match.group(0)

        text = _CHART_PLACEHOLDER_RE.sub(_placeholder_link, text)
        text = _CHART_SPEC_GENERATED_RE.sub(_chart_spec_generated_link, text)
    return inject_fleet_analysis_chart_links(text)


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
    Describe which metrics the demo models for this probe / point id.
    Full asset coverage requires querying sibling points on the same machine_id.
    """
    sid = (sensor_id or "").lower()
    machine_hint = None
    for machine in ("machine-001", "machine-002", "machine-003"):
        if machine in sid:
            machine_hint = machine
            break
    if not machine_hint:
        prefix_map = {"m1": "machine-001", "m2": "machine-002", "m3": "machine-003"}
        for prefix, machine in prefix_map.items():
            if sid.startswith(prefix):
                machine_hint = machine
                break

    if "humidity" in sid or sid.endswith("humidity_rh") or ":humidity_rh" in sid:
        return {
            "summary": "Humidity telemetry for this probe; temperature and vibration are separate points on the asset.",
            "signals_present": ["humidity_rh"],
            "signals_on_same_asset": ["inlet_temp_c", "outlet_temp_c", "motor_temp_c", "motor_vibration_mm_s"],
            "machine_id": machine_hint,
        }
    if "vibration" in sid or "motor_vibration" in sid:
        return {
            "summary": "Motor vibration telemetry for this probe; temperatures and humidity are separate points.",
            "signals_present": ["motor_vibration_mm_s"],
            "signals_on_same_asset": ["inlet_temp_c", "outlet_temp_c", "motor_temp_c", "humidity_rh"],
            "machine_id": machine_hint,
        }
    if "temp-outlet" in sid or "outlet_temp" in sid:
        return {
            "summary": "Outlet temperature for this probe; query m*-humidity and m*-vibration for correlated signals.",
            "signals_present": ["outlet_temp_c"],
            "signals_on_same_asset": ["inlet_temp_c", "motor_temp_c", "humidity_rh", "motor_vibration_mm_s"],
            "machine_id": machine_hint,
        }
    if "temp-inlet" in sid or "inlet_temp" in sid:
        return {
            "summary": "Inlet temperature for this probe; HVAC scenarios often correlate with humidity on the same asset.",
            "signals_present": ["inlet_temp_c"],
            "signals_on_same_asset": ["outlet_temp_c", "motor_temp_c", "humidity_rh", "motor_vibration_mm_s"],
            "machine_id": machine_hint,
        }
    if "temp-motor" in sid or "motor_temp" in sid:
        return {
            "summary": "Motor temperature; bearing scenarios also elevate motor_vibration_mm_s on the same asset.",
            "signals_present": ["motor_temp_c"],
            "signals_on_same_asset": ["inlet_temp_c", "outlet_temp_c", "humidity_rh", "motor_vibration_mm_s"],
            "machine_id": machine_hint,
        }
    return {
        "summary": (
            "Demo publishes 5 metrics per asset (3 temperatures, humidity, vibration). "
            "This query targets one probe; use get_system_statistics or query sibling point ids."
        ),
        "signals_present": ["varies_by_point_id"],
        "signals_on_same_asset": [
            "inlet_temp_c",
            "outlet_temp_c",
            "motor_temp_c",
            "humidity_rh",
            "motor_vibration_mm_s",
        ],
        "machine_id": machine_hint,
    }


def _value_stats(readings: list) -> dict:
    """Statistics from readings that may use value or temperature column."""
    vals = []
    for r in readings:
        v = r.get("value", r.get("temperature"))
        if v is not None:
            vals.append(float(v))
    if not vals:
        return {
            "reading_count": 0,
            "current_value": None,
            "avg_value": None,
            "min_value": None,
            "max_value": None,
        }
    return {
        "reading_count": len(vals),
        "current_value": vals[0],
        "avg_value": round(sum(vals) / len(vals), 2),
        "min_value": min(vals),
        "max_value": max(vals),
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
        
        all_alerts = sensor_db.get_recent_alerts(minutes=minutes, limit=100)
        sensor_alerts = [
            a
            for a in all_alerts
            if a.get("sensor_id") == sensor_id or a.get("point_id") == sensor_id
        ]

        if not readings and not sketches:
            return json.dumps({
                "status": "ok",
                "message": f"No data found for sensor '{sensor_id}' in the last {minutes} minutes",
                "sensor_id": sensor_id
            })

        stats = _value_stats(readings)
        if readings and readings[0].get("metric_id"):
            stats["metric_id"] = readings[0]["metric_id"]
            stats["unit"] = readings[0].get("unit")

        response = {
            "status": "ok",
            "sensor_id": sensor_id,
            "point_id": readings[0].get("point_id") if readings else sensor_id,
            "time_window_minutes": minutes,
            "source_order": ["sketches", "telemetry_readings", "alerts"],
            "telemetry_coverage": _incident_telemetry_coverage(sensor_id),
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
        sensor_alerts = [
            a
            for a in all_alerts
            if a.get("sensor_id") == sensor_id or a.get("point_id") == sensor_id
        ]

        stats = {
            "reading_count": len(readings),
            "sketch_count": len(sketches),
            "alert_count": len(sensor_alerts),
            **_value_stats(readings),
        }
        if readings and readings[0].get("metric_id"):
            stats["metric_id"] = readings[0]["metric_id"]
            stats["unit"] = readings[0].get("unit")

        response = {
            "status": "ok",
            "sensor_id": sensor_id,
            "point_id": readings[0].get("point_id") if readings else sensor_id,
            "time_window_minutes": minutes,
            "source_order": ["sketches", "telemetry_readings", "alerts"],
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
    - Which metrics are being ingested (temperature, humidity, vibration)
    
    Returns:
        JSON string with system statistics
    """
    try:
        stats = sensor_db.get_statistics()
        metrics = sensor_db.list_active_metrics(minutes=120)
        return json.dumps({
            "status": "ok",
            **stats,
            "active_metrics": metrics,
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
    metric_id: str = None,
    asset_id: str = None,
) -> str:
    """
    Fetch chart-ready deterministic series from chart_query_service.

    This is the preferred low-latency chart path for per-asset trend questions.
    It avoids large SQL payloads and returns a compact labels/values+stats bundle.

    Args:
        sensor_id: Probe or point id (e.g. m3-temp-motor, m1-humidity, machine-001:humidity_rh)
        metric_id: Optional metric filter (humidity_rh, motor_vibration_mm_s, inlet_temp_c, …)
        asset_id: Optional asset when using metric_id without probe id (e.g. machine-001)
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
        if metric_id:
            params["metric_id"] = metric_id
        if asset_id:
            params["asset_id"] = asset_id
        url = f"{internal}/series?{urlencode(params)}"

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
    metric_id: str = None,
    asset_id: str = None,
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
        if metric_id:
            params["metric_id"] = metric_id
        if asset_id:
            params["asset_id"] = asset_id
        url = f"{internal}/plotly-spec?{urlencode(params)}"

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
        pinned_start = stats.get("source_min_ts") or meta.get("window_start_utc")
        pinned_end = stats.get("source_max_ts") or meta.get("window_end_utc")
        rendered = int(stats.get("rendered_row_count") or 0)

        # Demo rollups often have empty max_v; retry avg_v so plotly_html_url_pinned is useful.
        if rendered == 0 and value_key == "max_v":
            avg_params = {**params, "value_key": "avg_v"}
            avg_url = f"{internal}/plotly-spec?{urlencode(avg_params)}"
            with urlopen(avg_url, timeout=8) as avg_resp:
                avg_data = json.loads(avg_resp.read().decode("utf-8"))
            if isinstance(avg_data, dict) and "plotly_spec" in avg_data:
                avg_stats = avg_data.get("stats") or {}
                if int(avg_stats.get("rendered_row_count") or 0) > 0:
                    data = avg_data
                    meta = data.get("meta", {}) or {}
                    stats = avg_stats
                    rendered = int(avg_stats.get("rendered_row_count") or 0)
                    value_key = "avg_v"
                    params = avg_params
                    pinned_start = stats.get("source_min_ts") or meta.get("window_start_utc")
                    pinned_end = stats.get("source_max_ts") or meta.get("window_end_utc")

        pinned_url = None
        if pinned_start and pinned_end:
            pinned_url = build_plotly_html_url(
                sensor_id,
                source=source,
                resolution=resolution,
                max_points=int(max_points),
                value_key=value_key,
                window_start=pinned_start,
                window_end=pinned_end,
            )
        if not pinned_url:
            pinned_url = build_plotly_html_url(
                sensor_id,
                minutes=int(minutes),
                source=source,
                resolution=resolution,
                max_points=int(max_points),
                value_key=value_key,
            )

        # Retry with legacy probe id when chart DB still has m*-temp-* series keys.
        if rendered == 0:
            legacy = _legacy_probe_for_point_id(sensor_id)
            if legacy and legacy != sensor_id:
                retry_url = f"{internal}/plotly-spec?{urlencode({**params, 'sensor_id': legacy})}"
                with urlopen(retry_url, timeout=8) as retry_resp:
                    retry_data = json.loads(retry_resp.read().decode("utf-8"))
                retry_stats = (retry_data.get("stats") or {}) if isinstance(retry_data, dict) else {}
                retry_meta = (retry_data.get("meta") or {}) if isinstance(retry_data, dict) else {}
                if int(retry_stats.get("rendered_row_count") or 0) > 0:
                    data = retry_data
                    meta = retry_meta
                    stats = retry_stats
                    rendered = int(retry_stats.get("rendered_row_count") or 0)
                    sensor_id = legacy
                    pinned_start = stats.get("source_min_ts") or meta.get("window_start_utc")
                    pinned_end = stats.get("source_max_ts") or meta.get("window_end_utc")
                    pinned_url = build_plotly_html_url(
                        sensor_id,
                        source=source,
                        resolution=resolution,
                        max_points=int(max_points),
                        value_key=value_key,
                        window_start=pinned_start,
                        window_end=pinned_end,
                    ) or build_plotly_html_url(
                        sensor_id,
                        minutes=int(minutes),
                        source=source,
                        resolution=resolution,
                        max_points=int(max_points),
                        value_key=value_key,
                    )

        out = {
            "status": "ok",
            "service": "chart_query_service",
            "sensor_id_queried": sensor_id,
            "meta": meta,
            "stats": stats,
            "plotly_spec": data.get("plotly_spec", {}),
        }
        if pinned_url:
            out["plotly_html_url_pinned"] = pinned_url
        if rendered == 0:
            out["chart_data_warning"] = (
                "No chart rows for this window; link may still open an empty chart. "
                "Confirm chart_writer is running and CHART_PUBLIC_BASE_URL is set for Slack."
            )
        if pinned_url and _is_browser_unreachable_chart_base(pinned_url):
            out["chart_link_warning"] = (
                "plotly_html_url_pinned uses an internal Docker hostname. "
                "Set CHART_PUBLIC_BASE_URL or DASHBOARD_PUBLIC_HOST in deploy/aws/.env for sam-control-plane."
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
            row = conn.execute(
                "SELECT sensor_id, timestamp FROM alerts WHERE id = ?",
                (alert_id,),
            ).fetchone()
            result = conn.execute(
                "UPDATE alerts SET acknowledged = TRUE WHERE id = ?",
                (alert_id,),
            )
            if result.rowcount > 0:
                if row and sensor_db.TELEMETRY_DUAL_WRITE:
                    conn.execute(
                        """UPDATE telemetry_alerts SET acknowledged = TRUE
                           WHERE point_id = ? AND timestamp = ?""",
                        (row["sensor_id"], row["timestamp"]),
                    )
                return json.dumps({
                    "status": "ok",
                    "message": f"Alert {alert_id} acknowledged"
                })
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
