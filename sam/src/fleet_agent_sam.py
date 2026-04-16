#!/usr/bin/env python3
"""
fleet_agent_sam.py
==================
SAM-native Fleet Anomaly Agent — merged best-of-both implementation.

Detects cross-sensor patterns that individual per-sensor agents miss:
  1. Correlated Drift         — Majority of sensors trending same direction
                                (linear trend: first-third vs last-third mean)
  2. Simultaneous Transitions — Multiple sensors escalating zone together
                                (dedicated transition deque, O(1) lookup)
  3. Geographic/Logical       — Named sensor group showing disproportionate
     Cluster Anomaly            elevation (configurable via YAML)
  4. Sensor Silence           — Recently-active sensors that have gone quiet
                                (guards against false positives from
                                 decommissioned/not-yet-configured sensors)

Also exposes get_fleet_status as a standalone query tool for dashboards
and on-demand health checks — callable by the LLM at any time.

Runs PARALLEL to the per-sensor AnomalyAgent. Fleet alerts are additive
context, not replacements for per-sensor detection.

SAM wiring (fleet_agent_sam.yaml):
  tool_type: python
  component_module: fleet_agent_sam
  function_name: analyze_fleet          ← main ingestion + analysis
  function_name: get_fleet_status       ← standalone query
"""

import collections
import logging
import statistics
import time
from typing import Any, Dict, List, Optional

from google.adk.tools import ToolContext

log = logging.getLogger(__name__)

# ── In-memory fleet state ────────────────────────────────────────────────────
# In production: back with Redis for multi-instance deployments.

# Per-sensor reading history: sensor_id → deque of (ts, temperature, zone)
_sensor_history:  Dict[str, collections.deque] = {}

# Last-seen wall-clock time per sensor
_last_seen:       Dict[str, float]             = {}

# Global zone transition log (bounded, fleet-wide)
_zone_transitions: collections.deque = collections.deque(maxlen=200)

# Sensor group membership loaded from tool_config on first call
# e.g. {"engine_bay": ["sensor-001", "sensor-002"], "cooling": ["sensor-003"]}
_sensor_groups:   Dict[str, List[str]]         = {}

# ── Defaults (all overrideable via tool_config in YAML) ─────────────────────
DEFAULT_WINDOW_SECS            = 300    # 5-min correlation window
DEFAULT_HISTORY_SIZE           = 50     # Max readings stored per sensor
DEFAULT_CORRELATION_THRESHOLD  = 0.60   # 60% sensors trending same = alert
DEFAULT_SILENCE_THRESHOLD_SECS = 60     # Silent >60s = concerning
DEFAULT_CLUSTER_THRESHOLD      = 3      # 3+ sensors in same zone = cluster
DEFAULT_TRANSITION_WINDOW_SECS = 30     # Zone transitions within 30s = simultaneous
DEFAULT_DRIFT_DELTA_CELSIUS    = 0.5    # Min °C change to count as a trend
DEFAULT_MIN_SENSORS_FOR_FLEET  = 2      # Need at least N sensors for fleet analysis


# ════════════════════════════════════════════════════════════════════════════
# Primary Tool — called for every incoming sketch
# ════════════════════════════════════════════════════════════════════════════

async def analyze_fleet(
    sensor_id:    str,
    temperature:  float,
    zone:         str,
    sketch:       str,
    timestamp:    str,
    window:       Optional[Dict[str, Any]] = None,
    tool_context: Optional[ToolContext]    = None,
    tool_config:  Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Ingest a sensor reading and evaluate fleet-wide anomaly patterns.

    Called by the SAM FleetAnomalyAgent for every sketch that arrives.
    Maintains rolling state across all sensors and fires when a cross-sensor
    pattern is detected.

    Args:
        sensor_id:    Sensor identifier (e.g. "sensor-001")
        temperature:  Current temperature reading in °C
        zone:         NORMAL | WARNING | CRITICAL
        sketch:       Natural language summary from the Sketch Agent
        timestamp:    ISO8601 timestamp
        window:       Rolling window stats from the Deadband Agent
                      (mean, min, max, count)
        tool_context: Injected by SAM framework
        tool_config:  Configuration block from fleet_agent_sam.yaml

    Returns:
        fleet_status: "NORMAL"    — no fleet pattern detected
        fleet_status: "WARNING"   — correlated warning-level pattern
        fleet_status: "CRITICAL"  — critical fleet-level pattern
        Includes patterns list, fleet_alert narrative, and active sensor count.
    """
    cfg = tool_config or {}
    window_secs        = float(cfg.get("window_secs",            DEFAULT_WINDOW_SECS))
    corr_threshold     = float(cfg.get("correlation_threshold",  DEFAULT_CORRELATION_THRESHOLD))
    silence_threshold  = float(cfg.get("silence_threshold_secs", DEFAULT_SILENCE_THRESHOLD_SECS))
    cluster_threshold  = int(cfg.get("cluster_threshold",        DEFAULT_CLUSTER_THRESHOLD))
    transition_window  = float(cfg.get("transition_window_secs", DEFAULT_TRANSITION_WINDOW_SECS))
    drift_delta        = float(cfg.get("drift_delta_celsius",     DEFAULT_DRIFT_DELTA_CELSIUS))
    min_sensors        = int(cfg.get("min_sensors_for_fleet",    DEFAULT_MIN_SENSORS_FOR_FLEET))

    # Load sensor groups from config on first call
    global _sensor_groups
    if cfg.get("sensor_groups") and not _sensor_groups:
        _sensor_groups = cfg["sensor_groups"]

    now = time.time()

    # ── 1. Update fleet state ────────────────────────────────────────────────
    prev_zone = _get_current_zone(sensor_id)
    _update_sensor_state(sensor_id, temperature, zone, now, window_secs)

    # Record zone transitions into the global deque
    if prev_zone and prev_zone != zone:
        _zone_transitions.append({
            "sensor_id": sensor_id,
            "from_zone": prev_zone,
            "to_zone":   zone,
            "timestamp": now
        })

    # ── 2. Guard: need minimum sensors for meaningful fleet analysis ─────────
    if len(_last_seen) < min_sensors:
        log.debug(
            "[fleet] %d active sensor(s) — need at least %d for fleet analysis",
            len(_last_seen), min_sensors
        )
        return _build_response("NORMAL", sensor_id, [], len(_last_seen))

    # ── 3. Run all pattern detectors ─────────────────────────────────────────
    patterns = []

    # 3a. Correlated drift (linear trend: first-third vs last-third mean)
    drift = _analyze_correlated_drift(now, window_secs, corr_threshold, drift_delta)
    if drift:
        patterns.append(drift)

    # 3b. Simultaneous zone transitions (uses global deque — O(1) window scan)
    transitions = _analyze_zone_transitions(now, transition_window, cluster_threshold)
    if transitions:
        patterns.append(transitions)

    # 3c. Geographic/logical cluster anomaly (requires sensor_groups in config)
    if _sensor_groups:
        cluster = _analyze_cluster_anomaly(cluster_threshold)
        if cluster:
            patterns.append(cluster)

    # 3d. Sensor silence (guards against false positives from
    #     decommissioned/not-yet-configured sensors using recency check)
    silence = _analyze_sensor_silence(now, silence_threshold, cluster_threshold, window_secs)
    if silence:
        patterns.append(silence)

    # ── 4. Determine fleet status from worst detected pattern ────────────────
    if not patterns:
        log.debug("[fleet] Nominal | sensors=%d | trigger=%s", len(_last_seen), sensor_id)
        return _build_response("NORMAL", sensor_id, [], len(_last_seen))

    fleet_status = "CRITICAL" if any(p["severity"] == "CRITICAL" for p in patterns) \
                   else "WARNING"

    alert_narrative = _build_fleet_alert(patterns)

    log.warning(
        "[fleet] %s | %d pattern(s) detected | trigger=%s | alert=%s",
        fleet_status, len(patterns), sensor_id, alert_narrative
    )

    return _build_response(fleet_status, sensor_id, patterns, len(_last_seen),
                           alert_narrative)


# ════════════════════════════════════════════════════════════════════════════
# Secondary Tool — standalone fleet health query
# ════════════════════════════════════════════════════════════════════════════

async def get_fleet_status(
    tool_context: Optional[ToolContext] = None,
    tool_config:  Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Query current fleet health without processing a new sensor reading.

    Returns active sensor count, zone distribution, silent sensors,
    and recent transition count. Useful for:
      - Periodic health checks
      - Dashboard queries
      - Operator-initiated "what's the fleet status?" requests

    Args:
        tool_context: Injected by SAM
        tool_config:  Configuration from YAML

    Returns:
        Dict with zone distribution, silent sensor list, and summary metrics.
    """
    cfg = tool_config or {}
    silence_threshold = float(cfg.get("silence_threshold_secs", DEFAULT_SILENCE_THRESHOLD_SECS))
    now = time.time()

    # Current zone distribution from most recent reading per sensor
    zones: Dict[str, List[str]] = {"NORMAL": [], "WARNING": [], "CRITICAL": []}
    for sid, history in _sensor_history.items():
        if history:
            current_zone = history[-1][2]
            zones.get(current_zone, zones["NORMAL"]).append(sid)

    # Silent sensors — those not seen within threshold
    silent = [
        {"sensor_id": sid, "silent_for_secs": round(now - ts, 1)}
        for sid, ts in _last_seen.items()
        if (now - ts) > silence_threshold
    ]

    total = len(_last_seen)
    elevated = len(zones["WARNING"]) + len(zones["CRITICAL"])
    health = "NORMAL" if elevated == 0 else \
             "CRITICAL" if zones["CRITICAL"] else "WARNING"

    return {
        "fleet_health":        health,
        "total_sensors":       total,
        "zone_distribution":   {k: len(v) for k, v in zones.items()},
        "sensors_by_zone":     zones,
        "silent_sensors":      silent,
        "silent_count":        len(silent),
        "recent_transitions":  len(_zone_transitions),
        "query_ts":            now,
        "summary": (
            f"Fleet {health}. {total} sensors active"
            f"{f', {len(silent)} silent' if silent else ''}. "
            f"Zone distribution: "
            f"{len(zones['NORMAL'])} NORMAL / "
            f"{len(zones['WARNING'])} WARNING / "
            f"{len(zones['CRITICAL'])} CRITICAL."
        )
    }


# ════════════════════════════════════════════════════════════════════════════
# State Management
# ════════════════════════════════════════════════════════════════════════════

def _update_sensor_state(sensor_id: str, temperature: float, zone: str,
                          ts: float, window_secs: float):
    """Append a new reading and trim entries outside the rolling window."""
    if sensor_id not in _sensor_history:
        _sensor_history[sensor_id] = collections.deque(maxlen=DEFAULT_HISTORY_SIZE)

    _sensor_history[sensor_id].append((ts, temperature, zone))
    _last_seen[sensor_id] = ts

    # Trim old entries outside the window
    cutoff = ts - window_secs
    for sid in list(_sensor_history.keys()):
        while _sensor_history[sid] and _sensor_history[sid][0][0] < cutoff:
            _sensor_history[sid].popleft()


def _get_current_zone(sensor_id: str) -> Optional[str]:
    """Return the most recent zone for a sensor, or None if unseen."""
    history = _sensor_history.get(sensor_id)
    if history:
        return history[-1][2]  # (ts, temp, zone)
    return None


# ════════════════════════════════════════════════════════════════════════════
# Pattern Detectors
# ════════════════════════════════════════════════════════════════════════════

def _analyze_correlated_drift(now: float, window_secs: float,
                               threshold: float, drift_delta: float) -> Optional[Dict]:
    """
    Detect when a majority of sensors are trending in the same direction.

    Uses linear trend estimation: compare the mean of the first third of
    each sensor's history to the mean of the last third. A delta greater
    than drift_delta_celsius in the same direction across >threshold% of
    sensors signals a systemic drift (e.g. HVAC failure, environmental shift).

    This is more sensitive to gradual drift than snapshot comparisons and
    correctly handles sensors that oscillate within their normal range.
    """
    trends = {"up": [], "down": [], "stable": []}

    for sid, history in _sensor_history.items():
        readings = list(history)  # [(ts, temp, zone), ...]
        if len(readings) < 3:
            continue

        n = len(readings)
        # Split into first and last third by index
        first_third = [r[1] for r in readings[:max(1, n // 3)]]
        last_third  = [r[1] for r in readings[-max(1, n // 3):]]

        avg_first = statistics.mean(first_third)
        avg_last  = statistics.mean(last_third)
        delta     = avg_last - avg_first

        if delta > drift_delta:
            trends["up"].append(sid)
        elif delta < -drift_delta:
            trends["down"].append(sid)
        else:
            trends["stable"].append(sid)

    total = sum(len(v) for v in trends.values())
    if total < 2:
        return None

    up_ratio   = len(trends["up"])   / total
    down_ratio = len(trends["down"]) / total

    if up_ratio >= threshold:
        return {
            "pattern":          "correlated_drift",
            "direction":        "increasing",
            "severity":         "WARNING",
            "affected_sensors": trends["up"],
            "correlation":      round(up_ratio, 2),
            "description": (
                f"{len(trends['up'])}/{total} sensors trending upward "
                f"({up_ratio*100:.0f}% correlation). "
                f"Possible systemic cause — environmental change or shared upstream condition."
            )
        }
    if down_ratio >= threshold:
        return {
            "pattern":          "correlated_drift",
            "direction":        "decreasing",
            "severity":         "WARNING",
            "affected_sensors": trends["down"],
            "correlation":      round(down_ratio, 2),
            "description": (
                f"{len(trends['down'])}/{total} sensors trending downward "
                f"({down_ratio*100:.0f}% correlation). "
                f"Possible systemic cause — coolant, power, or environmental change."
            )
        }
    return None


def _analyze_zone_transitions(now: float, transition_window: float,
                               cluster_threshold: int) -> Optional[Dict]:
    """
    Detect simultaneous zone escalations using the global transitions deque.

    Scans O(recent) entries rather than walking every sensor's history.
    Only counts true escalations (NORMAL→WARNING, WARNING→CRITICAL, etc.)
    to avoid counting recoveries as simultaneous alerts.
    """
    cutoff = now - transition_window
    recent_escalations = [
        t for t in _zone_transitions
        if t["timestamp"] >= cutoff and _is_escalation(t["from_zone"], t["to_zone"])
    ]

    if len(recent_escalations) < cluster_threshold:
        return None

    affected = list({t["sensor_id"] for t in recent_escalations})
    has_critical = any(t["to_zone"] == "CRITICAL" for t in recent_escalations)

    return {
        "pattern":          "simultaneous_escalation",
        "severity":         "CRITICAL" if has_critical else "WARNING",
        "affected_sensors": affected,
        "transition_count": len(recent_escalations),
        "within_seconds":   transition_window,
        "description": (
            f"{len(recent_escalations)} sensors escalated zone within "
            f"{transition_window:.0f}s. "
            f"Suggests a shared trigger event — check for environmental or process change."
        )
    }


def _analyze_cluster_anomaly(cluster_threshold: int) -> Optional[Dict]:
    """
    Detect when a named sensor group has disproportionate elevation.

    Uses sensor_groups from tool_config to identify logical clusters
    (e.g. engine_bay, cooling_loop). Fires when >50% of a group is
    in WARNING or CRITICAL — a more actionable signal than raw sensor counts
    because it points to a specific physical or logical subsystem.
    """
    best_cluster = None
    best_score   = 0.0

    for group_name, group_sensors in _sensor_groups.items():
        # Only consider sensors we've actually seen
        active_in_group = [s for s in group_sensors if s in _sensor_history
                           and _sensor_history[s]]
        if len(active_in_group) < 2:
            continue

        elevated = [
            sid for sid in active_in_group
            if _sensor_history[sid][-1][2] in ("WARNING", "CRITICAL")
        ]

        if not elevated:
            continue

        score = len(elevated) / len(active_in_group)
        if score > 0.5 and score > best_score:
            best_score   = score
            best_cluster = {
                "pattern":          "geographic_cluster",
                "cluster_name":     group_name,
                "severity":         "CRITICAL" if score == 1.0 else "WARNING",
                "affected_sensors": elevated,
                "cluster_size":     len(active_in_group),
                "correlation":      round(score, 2),
                "description": (
                    f"{len(elevated)}/{len(active_in_group)} sensors in "
                    f"group '{group_name}' are elevated "
                    f"({score*100:.0f}% of cluster). "
                    f"Localised fault likely within this sensor group."
                )
            }

    return best_cluster


def _analyze_sensor_silence(now: float, silence_threshold: float,
                             cluster_threshold: int,
                             window_secs: float) -> Optional[Dict]:
    """
    Detect multiple recently-active sensors that have gone silent.

    Critically, only considers sensors that reported within the last
    2× window period — this avoids false positives from sensors that
    have been legitimately decommissioned, not yet deployed, or are
    on a known longer reporting interval.
    """
    recently_active_cutoff = now - (window_secs * 2)

    silent = []
    for sid, last_ts in _last_seen.items():
        # Was recently active but is now silent
        if last_ts >= recently_active_cutoff and (now - last_ts) > silence_threshold:
            silent.append({
                "sensor_id":     sid,
                "silent_for_secs": round(now - last_ts, 1),
                "last_zone":     _get_current_zone(sid) or "UNKNOWN"
            })

    if len(silent) < cluster_threshold:
        return None

    return {
        "pattern":          "sensor_silence",
        "severity":         "CRITICAL" if len(silent) >= cluster_threshold * 2 else "WARNING",
        "affected_sensors": [s["sensor_id"] for s in silent],
        "silent_sensors":   silent,
        "silent_count":     len(silent),
        "description": (
            f"{len(silent)} recently-active sensor(s) have gone silent "
            f"(>{silence_threshold:.0f}s without a reading). "
            f"Possible network partition, sensor failure, or upstream process stop."
        )
    }


# ════════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════════

def _is_escalation(from_zone: str, to_zone: str) -> bool:
    """Return True if this transition represents a worsening zone."""
    order = {"NORMAL": 0, "WARNING": 1, "CRITICAL": 2}
    return order.get(to_zone, 0) > order.get(from_zone, 0)


def _build_fleet_alert(patterns: List[Dict]) -> str:
    """Produce a concise plain-English fleet alert for the LLM."""
    parts = [f"[{p['severity']}] {p['pattern']}: {p['description']}" for p in patterns]
    return " | ".join(parts)


def _build_response(status: str, sensor_id: str, patterns: List[Dict],
                    active_count: int,
                    alert_narrative: str = "") -> Dict[str, Any]:
    """Assemble the standard tool response dict."""
    resp: Dict[str, Any] = {
        "fleet_status":     status,
        "triggering_sensor": sensor_id,
        "active_sensors":   active_count,
        "patterns_detected": len(patterns),
        "patterns":         patterns,
        "analysis_ts":      time.time(),
    }
    if alert_narrative:
        resp["fleet_alert"] = alert_narrative
    return resp
