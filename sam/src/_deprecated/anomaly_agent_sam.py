"""
anomaly_agent_sam.py
─────────────────────────────────────────────────────────────────────────────
Per-Sensor Anomaly Agent — SAM Tool Implementation

Analyzes individual sensor events that have been classified as WARNING or CRITICAL.
Uses LLM reasoning to assess the severity, potential causes, and recommended actions.

This agent complements the Fleet Anomaly Agent:
  - Anomaly Agent: Per-sensor, deep analysis of individual alerts
  - Fleet Anomaly Agent: Cross-sensor, correlation and pattern detection

Deploy: Place in sam/src/ directory
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from collections import deque

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# In-memory state for tracking sensor history and alerts
# ─────────────────────────────────────────────────────────────────────────────

# Recent readings per sensor for trend analysis
_sensor_history: dict[str, deque] = {}
MAX_HISTORY = 20

# Recent alerts for deduplication
_recent_alerts: dict[str, dict] = {}
ALERT_COOLDOWN_SECS = 60

# Alert counters
_alert_stats = {
    "total_analyzed": 0,
    "alerts_raised": 0,
    "normal_skipped": 0,
    "deduplicated": 0,
}


def _get_sensor_history(sensor_id: str) -> deque:
    """Get or create history deque for a sensor."""
    if sensor_id not in _sensor_history:
        _sensor_history[sensor_id] = deque(maxlen=MAX_HISTORY)
    return _sensor_history[sensor_id]


def _is_duplicate_alert(sensor_id: str, zone: str) -> bool:
    """Check if we've recently raised an alert for this sensor/zone."""
    key = f"{sensor_id}:{zone}"
    if key not in _recent_alerts:
        return False
    
    last_alert = _recent_alerts[key]
    elapsed = (datetime.now(timezone.utc) - last_alert["timestamp"]).total_seconds()
    return elapsed < ALERT_COOLDOWN_SECS


def _record_alert(sensor_id: str, zone: str):
    """Record that we raised an alert."""
    key = f"{sensor_id}:{zone}"
    _recent_alerts[key] = {
        "timestamp": datetime.now(timezone.utc),
        "zone": zone,
    }


def _calculate_trend(history: list[dict]) -> dict:
    """Calculate trend from recent readings."""
    if len(history) < 3:
        return {"direction": "insufficient_data", "slope": 0.0}
    
    temps = [h["temperature"] for h in history]
    
    # Simple linear regression slope
    n = len(temps)
    x_mean = (n - 1) / 2
    y_mean = sum(temps) / n
    
    numerator = sum((i - x_mean) * (temps[i] - y_mean) for i in range(n))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    
    slope = numerator / denominator if denominator != 0 else 0.0
    
    if slope > 0.1:
        direction = "rising"
    elif slope < -0.1:
        direction = "falling"
    else:
        direction = "stable"
    
    return {
        "direction": direction,
        "slope": round(slope, 4),
        "readings": n,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Primary Tool: Analyze Sensor Event
# ─────────────────────────────────────────────────────────────────────────────

def analyze_sensor_event(
    sensor_id: str,
    temperature: float,
    zone: str,
    sketch: str,
    timestamp: str,
    window: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Analyze a sensor event for anomalies and generate assessment.
    
    This tool is called by the Anomaly Agent for events that have passed
    through the Deadband filter and Sketch generator.
    
    Args:
        sensor_id: Sensor identifier (e.g., "sensor-001")
        temperature: Current temperature reading in Celsius
        zone: Zone classification from sketch agent (NORMAL/WARNING/CRITICAL)
        sketch: Natural language sketch from the Sketch Agent
        timestamp: ISO8601 timestamp of the reading
        window: Optional rolling window stats (mean, min, max, count)
    
    Returns:
        dict with:
          - analyzed: bool - whether analysis was performed
          - alert_raised: bool - whether an alert was generated
          - severity: str - NONE/LOW/MEDIUM/HIGH/CRITICAL
          - assessment: str - human-readable assessment
          - trend: dict - trend analysis
          - recommendations: list - recommended actions
          - skip_reason: str - why analysis was skipped (if applicable)
    """
    _alert_stats["total_analyzed"] += 1
    
    # Parse timestamp
    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        ts = datetime.now(timezone.utc)
    
    # Record in history
    history = _get_sensor_history(sensor_id)
    history.append({
        "temperature": temperature,
        "zone": zone,
        "timestamp": ts,
    })
    
    # Skip NORMAL zone events - they don't need LLM analysis
    if zone == "NORMAL":
        _alert_stats["normal_skipped"] += 1
        return {
            "analyzed": False,
            "alert_raised": False,
            "severity": "NONE",
            "skip_reason": "NORMAL zone - no analysis needed",
            "assessment": f"Sensor {sensor_id} operating normally at {temperature:.1f}°C",
            "trend": _calculate_trend(list(history)),
            "recommendations": [],
        }
    
    # Check for duplicate alerts
    if _is_duplicate_alert(sensor_id, zone):
        _alert_stats["deduplicated"] += 1
        return {
            "analyzed": True,
            "alert_raised": False,
            "severity": "DEDUPLICATED",
            "skip_reason": f"Alert already raised within {ALERT_COOLDOWN_SECS}s",
            "assessment": f"Ongoing {zone} condition for {sensor_id}",
            "trend": _calculate_trend(list(history)),
            "recommendations": ["Monitor existing alert"],
        }
    
    # Calculate trend
    trend = _calculate_trend(list(history))
    
    # Determine severity based on zone and trend
    if zone == "CRITICAL":
        if trend["direction"] == "rising":
            severity = "CRITICAL"
            urgency = "IMMEDIATE"
        else:
            severity = "HIGH"
            urgency = "URGENT"
    else:  # WARNING
        if trend["direction"] == "rising":
            severity = "MEDIUM"
            urgency = "MONITOR"
        else:
            severity = "LOW"
            urgency = "ADVISORY"
    
    # Generate assessment
    window_info = ""
    if window:
        window_info = f" Window stats: mean={window.get('mean', 'N/A'):.1f}°C, range=[{window.get('min', 'N/A'):.1f}, {window.get('max', 'N/A'):.1f}]."
    
    assessment = (
        f"[{severity}] Sensor {sensor_id} in {zone} zone at {temperature:.1f}°C. "
        f"Trend: {trend['direction']} (slope: {trend['slope']:.3f}°C/reading).{window_info} "
        f"Sketch: {sketch}"
    )
    
    # Generate recommendations based on severity
    recommendations = []
    if severity == "CRITICAL":
        recommendations = [
            "IMMEDIATE: Check for sensor malfunction or environmental hazard",
            "Verify reading with adjacent sensors",
            "Consider automated shutdown if threshold persists",
            "Notify on-call personnel",
        ]
    elif severity == "HIGH":
        recommendations = [
            "URGENT: Investigate root cause within 15 minutes",
            "Check cooling/heating systems",
            "Review recent maintenance logs",
        ]
    elif severity == "MEDIUM":
        recommendations = [
            "MONITOR: Track trend over next 5 minutes",
            "Prepare contingency response",
            "Check for environmental factors",
        ]
    else:
        recommendations = [
            "ADVISORY: Log for trend analysis",
            "No immediate action required",
        ]
    
    # Record the alert
    _record_alert(sensor_id, zone)
    _alert_stats["alerts_raised"] += 1
    
    log.info(
        "Anomaly detected: sensor=%s zone=%s severity=%s trend=%s",
        sensor_id, zone, severity, trend["direction"]
    )
    
    return {
        "analyzed": True,
        "alert_raised": True,
        "severity": severity,
        "urgency": urgency,
        "zone": zone,
        "sensor_id": sensor_id,
        "temperature": temperature,
        "assessment": assessment,
        "trend": trend,
        "recommendations": recommendations,
        "timestamp": ts.isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Secondary Tool: Get Anomaly Stats
# ─────────────────────────────────────────────────────────────────────────────

def get_anomaly_stats() -> dict[str, Any]:
    """
    Get current anomaly detection statistics.
    
    Returns:
        dict with:
          - total_analyzed: int - total events analyzed
          - alerts_raised: int - alerts generated
          - normal_skipped: int - NORMAL events skipped
          - deduplicated: int - duplicate alerts suppressed
          - active_sensors: int - sensors with recent history
          - recent_alerts: list - recent alert summaries
    """
    # Get recent alerts summary
    recent = []
    now = datetime.now(timezone.utc)
    for key, alert in _recent_alerts.items():
        elapsed = (now - alert["timestamp"]).total_seconds()
        if elapsed < 300:  # Last 5 minutes
            sensor_id, zone = key.split(":", 1)
            recent.append({
                "sensor_id": sensor_id,
                "zone": zone,
                "seconds_ago": int(elapsed),
            })
    
    recent.sort(key=lambda x: x["seconds_ago"])
    
    return {
        **_alert_stats,
        "active_sensors": len(_sensor_history),
        "recent_alerts": recent[:10],  # Last 10 alerts
        "summary": (
            f"Analyzed {_alert_stats['total_analyzed']} events, "
            f"raised {_alert_stats['alerts_raised']} alerts, "
            f"skipped {_alert_stats['normal_skipped']} NORMAL events"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tertiary Tool: Get Sensor History
# ─────────────────────────────────────────────────────────────────────────────

def get_sensor_history(sensor_id: str) -> dict[str, Any]:
    """
    Get recent history for a specific sensor.
    
    Args:
        sensor_id: Sensor identifier
    
    Returns:
        dict with:
          - sensor_id: str
          - readings: list of recent readings
          - trend: trend analysis
          - last_zone: most recent zone classification
    """
    history = _get_sensor_history(sensor_id)
    readings = list(history)
    
    if not readings:
        return {
            "sensor_id": sensor_id,
            "readings": [],
            "trend": {"direction": "no_data", "slope": 0.0},
            "last_zone": "UNKNOWN",
            "summary": f"No recent data for sensor {sensor_id}",
        }
    
    trend = _calculate_trend(readings)
    last = readings[-1]
    
    return {
        "sensor_id": sensor_id,
        "readings": [
            {
                "temperature": r["temperature"],
                "zone": r["zone"],
                "timestamp": r["timestamp"].isoformat(),
            }
            for r in readings[-5:]  # Last 5 readings
        ],
        "trend": trend,
        "last_zone": last["zone"],
        "last_temperature": last["temperature"],
        "summary": (
            f"Sensor {sensor_id}: {len(readings)} readings, "
            f"last={last['temperature']:.1f}°C ({last['zone']}), "
            f"trend={trend['direction']}"
        ),
    }
