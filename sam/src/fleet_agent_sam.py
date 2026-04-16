#!/usr/bin/env python3
"""
fleet_agent_sam.py
==================
SAM-native Fleet Anomaly Agent — detects correlated patterns across sensors.

This agent monitors sketch outputs from all sensors and identifies fleet-wide
anomalies that individual sensors would miss:
  - Correlated drift (majority trending same direction)
  - Simultaneous zone transitions
  - Geographic/logical clustering of anomalies
  - Sensor silence patterns (multiple sensors going offline)

Architecture:
  Maintains a time-windowed view of all sensor sketches and applies
  statistical correlation analysis to detect fleet-level patterns.
"""

import collections
import logging
import statistics
import time
from typing import Any, Dict, List, Optional

from google.adk.tools import ToolContext

log = logging.getLogger(__name__)

# ── In-memory fleet state ────────────────────────────────────────────────────
_sensor_history: Dict[str, collections.deque] = {}  # sensor_id -> [(ts, temp, zone)]
_last_seen: Dict[str, float] = {}  # sensor_id -> last timestamp
_zone_transitions: collections.deque = collections.deque(maxlen=100)  # recent transitions

# ── Default thresholds ───────────────────────────────────────────────────────
DEFAULT_WINDOW_SECS = 300          # 5 minute correlation window
DEFAULT_HISTORY_SIZE = 50          # Max readings per sensor
DEFAULT_CORRELATION_THRESHOLD = 0.6  # 60% of sensors trending same way
DEFAULT_SILENCE_THRESHOLD_SECS = 60  # Sensor silent for 60s = concerning
DEFAULT_CLUSTER_THRESHOLD = 3      # 3+ sensors in same zone = cluster


async def analyze_fleet(
    sensor_id: str,
    temperature: float,
    zone: str,
    sketch: str,
    timestamp: str,
    window: Optional[Dict[str, Any]] = None,
    tool_context: Optional[ToolContext] = None,
    tool_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Analyze incoming sensor data for fleet-wide anomaly patterns.

    This tool is called for every sketch that passes through the pipeline.
    It maintains fleet state and checks for correlated anomalies.

    Args:
        sensor_id:    Sensor identifier
        temperature:  Current temperature reading (°C)
        zone:         NORMAL | WARNING | CRITICAL
        sketch:       Natural language sketch from SketchAgent
        timestamp:    ISO8601 timestamp
        window:       Rolling window stats from upstream
        tool_context: Injected by SAM framework
        tool_config:  Configuration from YAML

    Returns:
        Dict with fleet analysis results and any detected patterns
    """
    cfg = tool_config or {}
    window_secs = float(cfg.get("window_secs", DEFAULT_WINDOW_SECS))
    correlation_threshold = float(cfg.get("correlation_threshold", DEFAULT_CORRELATION_THRESHOLD))
    silence_threshold = float(cfg.get("silence_threshold_secs", DEFAULT_SILENCE_THRESHOLD_SECS))
    cluster_threshold = int(cfg.get("cluster_threshold", DEFAULT_CLUSTER_THRESHOLD))

    now = time.time()
    
    # Update fleet state
    _update_sensor_state(sensor_id, temperature, zone, now, window_secs)
    
    # Track zone transitions
    prev_zone = _get_previous_zone(sensor_id)
    if prev_zone and prev_zone != zone:
        _zone_transitions.append({
            "sensor_id": sensor_id,
            "from_zone": prev_zone,
            "to_zone": zone,
            "timestamp": now
        })

    # Run fleet analysis
    patterns_detected = []
    
    # 1. Check for correlated drift
    drift_analysis = _analyze_correlated_drift(now, window_secs, correlation_threshold)
    if drift_analysis["detected"]:
        patterns_detected.append(drift_analysis)
    
    # 2. Check for simultaneous zone transitions
    transition_analysis = _analyze_zone_transitions(now, window_secs=30)
    if transition_analysis["detected"]:
        patterns_detected.append(transition_analysis)
    
    # 3. Check for sensor silence (multiple sensors offline)
    silence_analysis = _analyze_sensor_silence(now, silence_threshold, cluster_threshold)
    if silence_analysis["detected"]:
        patterns_detected.append(silence_analysis)
    
    # 4. Check for zone clustering (many sensors in WARNING/CRITICAL)
    cluster_analysis = _analyze_zone_clustering(cluster_threshold)
    if cluster_analysis["detected"]:
        patterns_detected.append(cluster_analysis)

    # Build response
    fleet_status = "NORMAL"
    if any(p["severity"] == "CRITICAL" for p in patterns_detected):
        fleet_status = "CRITICAL"
    elif any(p["severity"] == "WARNING" for p in patterns_detected):
        fleet_status = "WARNING"

    result = {
        "sensor_id": sensor_id,
        "sensor_zone": zone,
        "fleet_status": fleet_status,
        "active_sensors": len(_last_seen),
        "patterns_detected": len(patterns_detected),
        "patterns": patterns_detected,
        "analysis_ts": now,
    }

    if patterns_detected:
        log.warning(
            "[fleet] Detected %d pattern(s) | fleet_status=%s | trigger=%s",
            len(patterns_detected), fleet_status, sensor_id
        )
        result["fleet_alert"] = _generate_fleet_alert(patterns_detected)
    else:
        log.debug("[fleet] No fleet anomalies | sensors=%d | trigger=%s", 
                  len(_last_seen), sensor_id)

    return result


# ════════════════════════════════════════════════════════════════════════════
# Fleet State Management
# ════════════════════════════════════════════════════════════════════════════

def _update_sensor_state(sensor_id: str, temperature: float, zone: str, 
                         ts: float, window_secs: float):
    """Update the fleet-wide sensor state."""
    if sensor_id not in _sensor_history:
        _sensor_history[sensor_id] = collections.deque(maxlen=DEFAULT_HISTORY_SIZE)
    
    _sensor_history[sensor_id].append((ts, temperature, zone))
    _last_seen[sensor_id] = ts
    
    # Prune old entries
    cutoff = ts - window_secs
    for sid in list(_sensor_history.keys()):
        while _sensor_history[sid] and _sensor_history[sid][0][0] < cutoff:
            _sensor_history[sid].popleft()


def _get_previous_zone(sensor_id: str) -> Optional[str]:
    """Get the previous zone for a sensor (before current reading)."""
    history = _sensor_history.get(sensor_id)
    if history and len(history) >= 2:
        return history[-2][2]  # (ts, temp, zone)
    return None


# ════════════════════════════════════════════════════════════════════════════
# Pattern Detection Functions
# ════════════════════════════════════════════════════════════════════════════

def _analyze_correlated_drift(now: float, window_secs: float, 
                               threshold: float) -> Dict[str, Any]:
    """
    Detect when majority of sensors are trending in the same direction.
    
    This catches scenarios like HVAC failure where all sensors drift upward
    but each individual reading is within its own normal range.
    """
    trends = {"up": [], "down": [], "stable": []}
    
    for sensor_id, history in _sensor_history.items():
        if len(history) < 3:
            continue
        
        # Get readings from last window
        readings = [(ts, temp) for ts, temp, _ in history]
        if len(readings) < 3:
            continue
        
        # Simple linear trend: compare first third to last third
        n = len(readings)
        first_third = [r[1] for r in readings[:n//3]] or [readings[0][1]]
        last_third = [r[1] for r in readings[-n//3:]] or [readings[-1][1]]
        
        avg_first = statistics.mean(first_third)
        avg_last = statistics.mean(last_third)
        delta = avg_last - avg_first
        
        if delta > 0.5:  # >0.5°C increase
            trends["up"].append(sensor_id)
        elif delta < -0.5:  # >0.5°C decrease
            trends["down"].append(sensor_id)
        else:
            trends["stable"].append(sensor_id)
    
    total_trending = len(trends["up"]) + len(trends["down"]) + len(trends["stable"])
    if total_trending == 0:
        return {"detected": False}
    
    # Check if majority trending same direction
    up_ratio = len(trends["up"]) / total_trending
    down_ratio = len(trends["down"]) / total_trending
    
    if up_ratio >= threshold:
        return {
            "detected": True,
            "pattern": "correlated_drift",
            "direction": "increasing",
            "severity": "WARNING",
            "affected_sensors": trends["up"],
            "correlation": round(up_ratio, 2),
            "description": f"{len(trends['up'])}/{total_trending} sensors trending upward ({up_ratio*100:.0f}%)"
        }
    elif down_ratio >= threshold:
        return {
            "detected": True,
            "pattern": "correlated_drift",
            "direction": "decreasing",
            "severity": "WARNING",
            "affected_sensors": trends["down"],
            "correlation": round(down_ratio, 2),
            "description": f"{len(trends['down'])}/{total_trending} sensors trending downward ({down_ratio*100:.0f}%)"
        }
    
    return {"detected": False}


def _analyze_zone_transitions(now: float, window_secs: float = 30) -> Dict[str, Any]:
    """
    Detect simultaneous zone transitions (multiple sensors changing zone together).
    """
    cutoff = now - window_secs
    recent = [t for t in _zone_transitions if t["timestamp"] >= cutoff]
    
    if len(recent) < 3:
        return {"detected": False}
    
    # Group by transition type
    escalations = [t for t in recent if _is_escalation(t["from_zone"], t["to_zone"])]
    
    if len(escalations) >= 3:
        affected = list(set(t["sensor_id"] for t in escalations))
        severity = "CRITICAL" if any(t["to_zone"] == "CRITICAL" for t in escalations) else "WARNING"
        
        return {
            "detected": True,
            "pattern": "simultaneous_escalation",
            "severity": severity,
            "affected_sensors": affected,
            "transition_count": len(escalations),
            "window_secs": window_secs,
            "description": f"{len(escalations)} sensors escalated zone within {window_secs}s"
        }
    
    return {"detected": False}


def _analyze_sensor_silence(now: float, threshold_secs: float, 
                            cluster_threshold: int) -> Dict[str, Any]:
    """
    Detect multiple sensors going silent (potential network/infrastructure issue).
    """
    silent_sensors = []
    
    for sensor_id, last_ts in _last_seen.items():
        if (now - last_ts) > threshold_secs:
            silent_sensors.append({
                "sensor_id": sensor_id,
                "silent_for_secs": round(now - last_ts, 1)
            })
    
    if len(silent_sensors) >= cluster_threshold:
        return {
            "detected": True,
            "pattern": "sensor_silence",
            "severity": "CRITICAL",
            "affected_sensors": [s["sensor_id"] for s in silent_sensors],
            "silent_count": len(silent_sensors),
            "description": f"{len(silent_sensors)} sensors silent for >{threshold_secs}s — possible infrastructure issue"
        }
    
    return {"detected": False}


def _analyze_zone_clustering(cluster_threshold: int) -> Dict[str, Any]:
    """
    Detect when many sensors are in WARNING or CRITICAL zone simultaneously.
    """
    zones = {"NORMAL": [], "WARNING": [], "CRITICAL": []}
    
    for sensor_id, history in _sensor_history.items():
        if history:
            current_zone = history[-1][2]  # Most recent zone
            zones[current_zone].append(sensor_id)
    
    total = sum(len(v) for v in zones.values())
    if total == 0:
        return {"detected": False}
    
    critical_count = len(zones["CRITICAL"])
    warning_count = len(zones["WARNING"])
    elevated_count = critical_count + warning_count
    
    # Alert if significant portion of fleet is elevated
    if critical_count >= cluster_threshold:
        return {
            "detected": True,
            "pattern": "zone_clustering",
            "severity": "CRITICAL",
            "affected_sensors": zones["CRITICAL"],
            "critical_count": critical_count,
            "warning_count": warning_count,
            "total_sensors": total,
            "description": f"{critical_count} sensors in CRITICAL zone — fleet-wide issue likely"
        }
    elif elevated_count >= cluster_threshold * 2:
        return {
            "detected": True,
            "pattern": "zone_clustering",
            "severity": "WARNING",
            "affected_sensors": zones["WARNING"] + zones["CRITICAL"],
            "critical_count": critical_count,
            "warning_count": warning_count,
            "total_sensors": total,
            "description": f"{elevated_count}/{total} sensors elevated ({warning_count} WARNING, {critical_count} CRITICAL)"
        }
    
    return {"detected": False}


# ════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ════════════════════════════════════════════════════════════════════════════

def _is_escalation(from_zone: str, to_zone: str) -> bool:
    """Check if a zone transition is an escalation (getting worse)."""
    order = {"NORMAL": 0, "WARNING": 1, "CRITICAL": 2}
    return order.get(to_zone, 0) > order.get(from_zone, 0)


def _generate_fleet_alert(patterns: List[Dict[str, Any]]) -> str:
    """Generate a natural language fleet alert summary."""
    if not patterns:
        return ""
    
    alerts = []
    for p in patterns:
        alerts.append(f"[{p['severity']}] {p['pattern']}: {p['description']}")
    
    return " | ".join(alerts)


# ════════════════════════════════════════════════════════════════════════════
# Fleet State Query (for debugging/monitoring)
# ════════════════════════════════════════════════════════════════════════════

async def get_fleet_status(
    tool_context: Optional[ToolContext] = None,
    tool_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Query current fleet status without processing a new sensor reading.
    Useful for dashboards and monitoring.
    """
    now = time.time()
    
    zones = {"NORMAL": 0, "WARNING": 0, "CRITICAL": 0}
    for history in _sensor_history.values():
        if history:
            zone = history[-1][2]
            zones[zone] = zones.get(zone, 0) + 1
    
    silent = sum(1 for ts in _last_seen.values() if (now - ts) > 60)
    
    return {
        "active_sensors": len(_last_seen),
        "silent_sensors": silent,
        "zone_distribution": zones,
        "recent_transitions": len(_zone_transitions),
        "query_ts": now
    }
