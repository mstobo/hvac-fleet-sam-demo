#!/usr/bin/env python3
"""
deadband_agent_sam.py
=====================
SAM-native Deadband / Delta Engine Agent — CORRECTED implementation.

Based on verified SAM source code (solace-agent-mesh-core-plugins pattern).

KEY CORRECTIONS from previous version:
  ❌ WRONG: from google.adk.agents import BaseAgent
  ❌ WRONG: class DeadbandAgent(BaseAgent)
  ❌ WRONG: async def _run_async_impl(self, ctx: InvocationContext)
  ❌ WRONG: yield Event(...) / EventActions(transfer_to_agent=...)
  ❌ WRONG: import solace_agent_mesh.common as sam

  ✅ CORRECT: SAM agents are Google ADK LlmAgent instances defined in YAML
  ✅ CORRECT: Custom logic lives in Python TOOL FUNCTIONS (not agent subclasses)
  ✅ CORRECT: Tools receive ToolContext (from google.adk.tools import ToolContext)
  ✅ CORRECT: Tools access host via tool_context._invocation_context.agent.host_component
  ✅ CORRECT: tool_type: python in YAML, with component_module + function_name

SAM Architecture (actual):
  - The YAML config defines an LlmAgent (via app_module: solace_agent_mesh.agent.sac.app)
  - Custom logic is implemented as plain Python async functions (tools)
  - Tools are wired to the agent via tool_type: python in YAML
  - The LLM orchestrates which tools to call — or for non-LLM logic,
    use tool_type: python with a deterministic function

For the Deadband Agent, we implement the filter as a Python tool function
that the LLM agent calls automatically when it receives a sensor event.
"""

import collections
import logging
import time
from typing import Any, Dict, Optional

from google.adk.tools import ToolContext

log = logging.getLogger(__name__)

# ── In-memory state (per-process, sufficient for single-instance POC) ───────
_last_value:      Dict[str, float]             = {}
_last_forward_ts: Dict[str, float]             = {}
_windows:         Dict[str, collections.deque] = {}

# ── Thresholds — overridden by tool_config in YAML ──────────────────────────
DEFAULT_DEADBAND_PCT   = 0.02    # 2% change required
DEFAULT_HEARTBEAT_SECS = 30.0
DEFAULT_WINDOW_SECS    = 30.0
DEFAULT_WARNING_TEMP   = 58.0
DEFAULT_CRITICAL_TEMP  = 65.0


# ════════════════════════════════════════════════════════════════════════════
# SAM Tool Function — this is the correct pattern for custom logic in SAM
# ════════════════════════════════════════════════════════════════════════════

async def apply_deadband_filter(
    sensor_id: str,
    temperature: float,
    timestamp: str,
    tool_context: Optional[ToolContext] = None,
    tool_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Apply deadband filtering and zone classification to a sensor reading.

    This tool is called by the SAM LlmAgent when it receives a sensor event.
    It suppresses readings within the deadband threshold and enriches
    forwarded events with window statistics and zone classification.

    Args:
        sensor_id:    The sensor identifier (e.g. "sensor-001")
        temperature:  The temperature reading in °C
        timestamp:    ISO8601 timestamp of the reading
        tool_context: Injected by SAM framework (google.adk.tools.ToolContext)
        tool_config:  Configuration from YAML tool_config block

    Returns:
        Dict with either:
          {"action": "suppress", "reason": "..."}        — drop this event
          {"action": "forward", "zone": "...", ...}       — pass to next agent
    """
    cfg = tool_config or {}
    deadband_pct   = float(cfg.get("deadband_pct",   DEFAULT_DEADBAND_PCT))
    heartbeat_secs = float(cfg.get("heartbeat_secs", DEFAULT_HEARTBEAT_SECS))
    window_secs    = float(cfg.get("window_secs",    DEFAULT_WINDOW_SECS))
    warning_temp   = float(cfg.get("warning_temp",   DEFAULT_WARNING_TEMP))
    critical_temp  = float(cfg.get("critical_temp",  DEFAULT_CRITICAL_TEMP))

    now = time.time()
    _add_to_window(sensor_id, temperature, now, window_secs)

    prev_val = _last_value.get(sensor_id)
    last_fwd = _last_forward_ts.get(sensor_id, 0)

    # ── Deadband check ───────────────────────────────────────────────────────
    if prev_val is not None:
        delta_pct     = abs(temperature - prev_val) / max(abs(prev_val), 0.001)
        heartbeat_due = (now - last_fwd) >= heartbeat_secs

        if delta_pct < deadband_pct and not heartbeat_due:
            log.debug(
                "[deadband] Suppressed %s | val=%.2f | Δ=%.2f%% < threshold %.2f%%",
                sensor_id, temperature, delta_pct * 100, deadband_pct * 100
            )
            return {
                "action": "suppress",
                "sensor_id": sensor_id,
                "reason": f"delta {delta_pct*100:.2f}% below deadband {deadband_pct*100}%"
            }

        forwarded_reason = "heartbeat" if delta_pct < deadband_pct else "delta"
        delta_pct_out    = delta_pct
    else:
        forwarded_reason = "first-reading"
        delta_pct_out    = 0.0

    # ── Forward — update state and enrich event ──────────────────────────────
    _last_value[sensor_id]      = temperature
    _last_forward_ts[sensor_id] = now

    zone = _classify_zone(temperature, warning_temp, critical_temp)
    win  = _get_window_stats(sensor_id)

    log.info(
        "[deadband] Forwarding %s | %.2f°C | zone=%s | reason=%s | Δ=%.2f%%",
        sensor_id, temperature, zone, forwarded_reason, delta_pct_out * 100
    )

    return {
        "action"          : "forward",
        "sensor_id"       : sensor_id,
        "temperature"     : temperature,
        "timestamp"       : timestamp,
        "zone"            : zone,
        "delta_pct"       : round(delta_pct_out, 4),
        "forwarded_reason": forwarded_reason,
        "window"          : win,
        "pipeline_ts"     : now
    }


# ════════════════════════════════════════════════════════════════════════════
# Pure helper functions (no SAM dependencies — fully unit-testable)
# ════════════════════════════════════════════════════════════════════════════

def _classify_zone(value: float, warning: float, critical: float) -> str:
    if value >= critical: return "CRITICAL"
    if value >= warning:  return "WARNING"
    return "NORMAL"


def _add_to_window(sensor_id: str, value: float, ts: float, window_secs: float):
    if sensor_id not in _windows:
        _windows[sensor_id] = collections.deque()
    _windows[sensor_id].append((ts, value))
    cutoff = ts - window_secs
    while _windows[sensor_id] and _windows[sensor_id][0][0] < cutoff:
        _windows[sensor_id].popleft()


def _get_window_stats(sensor_id: str) -> dict:
    if sensor_id not in _windows or not _windows[sensor_id]:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "count": 0}
    vals = [v for _, v in _windows[sensor_id]]
    return {
        "mean" : round(sum(vals) / len(vals), 3),
        "min"  : round(min(vals), 3),
        "max"  : round(max(vals), 3),
        "count": len(vals)
    }
