#!/usr/bin/env python3
"""
sketch_agent_sam.py
===================
SAM-native Sketch Summarizer Agent — CORRECTED implementation.

Custom logic is a plain Python async tool function, NOT a BaseAgent subclass.

The Sketch Agent runs as a SAM LlmAgent defined in YAML, with this file
providing the tool function that generates the natural language sketch.
"""

import json
import logging
import time
from typing import Any, Dict, Optional

from google.adk.tools import ToolContext

log = logging.getLogger(__name__)

# ── Default baselines (hardcoded for POC — production: fetch from time-series DB) ──
DEFAULT_BASELINES = {
    "default": {"weekly_mean": 46.0, "weekly_std": 4.5}
}

DEFAULT_WINDOW_SECS = 30.0


async def generate_sketch(
    sensor_id: str,
    temperature: float,
    zone: str,
    delta_pct: float,
    forwarded_reason: str,
    timestamp: str,
    window: Optional[Dict[str, Any]] = None,
    window_secs: Optional[float] = None,
    tool_context: Optional[ToolContext] = None,
    tool_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Convert a filtered sensor event into a natural language sketch.

    Receives the enriched output of the Deadband Agent and produces a
    concise, human-readable description for downstream LLM agents.

    Args:
        sensor_id:        Sensor identifier
        temperature:      Current temperature reading (°C)
        zone:             NORMAL | WARNING | CRITICAL
        delta_pct:        Fractional change from previous forwarded value
        forwarded_reason: Why this event was forwarded (delta/heartbeat/first-reading)
        timestamp:        ISO8601 timestamp
        window:           Rolling window stats dict (mean/min/max/count)
        window_secs:      Window duration in seconds (from upstream deadband agent)
        tool_context:     Injected by SAM (google.adk.tools.ToolContext)
        tool_config:      YAML tool_config block

    Returns:
        Dict with sketch string and routing metadata
    """
    cfg = tool_config or {}
    mode = cfg.get("mode", "TEMPLATE")
    baselines = cfg.get("baselines", DEFAULT_BASELINES)
    win = window or {}

    # Use provided window_secs or fall back to config/default
    actual_window_secs = window_secs or float(cfg.get("window_secs", DEFAULT_WINDOW_SECS))

    win_mean = win.get("mean", temperature)
    win_min = win.get("min", temperature)
    win_max = win.get("max", temperature)
    win_count = win.get("count", 1)

    baseline = baselines.get(sensor_id, baselines.get("default", DEFAULT_BASELINES["default"]))
    wk_mean = float(baseline.get("weekly_mean", 46.0))
    wk_std = float(baseline.get("weekly_std", 4.5))
    
    # Guard against division by zero
    sigma = abs(temperature - wk_mean) / max(wk_std, 0.1)
    direction = "above" if temperature > wk_mean else "below"
    ts_fmt = timestamp[11:16] if len(timestamp) >= 16 else timestamp
    move = "spike" if temperature > win_mean else "drop"
    delta_pct_pct = delta_pct * 100

    # Format window duration for human readability
    window_desc = f"{int(actual_window_secs)}s"

    # ── TEMPLATE mode (deterministic, zero LLM cost) ────────────────────────
    if mode == "TEMPLATE" or not _llm_available(tool_context):
        if forwarded_reason == "heartbeat":
            sketch = (
                f"[HEARTBEAT] {sensor_id} stable at ~{win_mean:.1f}°C "
                f"(range {win_min:.1f}–{win_max:.1f}°C) over last {window_desc}. "
                f"No significant change. Zone: {zone}."
            )
        else:
            sketch = (
                f"{sensor_id} recorded a {delta_pct_pct:.1f}% {move} to "
                f"{temperature:.1f}°C"
            )
            if ts_fmt:
                sketch += f" at {ts_fmt}"
            sketch += (
                f". {window_desc} window ({win_count} samples): mean {win_mean:.1f}°C, "
                f"range [{win_min:.1f}–{win_max:.1f}°C]. "
                f"{sigma:.1f}σ {direction} weekly average ({wk_mean:.1f}°C). "
                f"Zone: {zone}."
            )
            if zone == "CRITICAL":
                sketch += " ⚠️ ANOMALY — immediate review required."
            elif zone == "WARNING":
                sketch += " ⚡ Elevated — monitoring advised."

        log.info("[sketch] Generated TEMPLATE sketch for %s | zone=%s", sensor_id, zone)

    # ── LLM mode (richer narrative — Phase 4+) ──────────────────────────────
    else:
        # In LLM mode, we return the raw data and let the SAM orchestrator's
        # LLM generate the sketch via its system prompt.
        event_data = {
            "sensor_id": sensor_id,
            "temperature": temperature,
            "zone": zone,
            "delta_pct": delta_pct,
            "window": win,
            "sigma": round(sigma, 2),
            "direction": direction,
            "timestamp": timestamp
        }
        sketch = (
            f"Please summarize this sensor event in 1-2 sentences for an anomaly agent: "
            f"{json.dumps(event_data)}"
        )
        log.info("[sketch] Delegating to LLM for sketch generation | sensor=%s", sensor_id)

    return {
        "sensor_id":    sensor_id,
        "zone":         zone,
        "sketch":       sketch,
        "raw_value":    temperature,
        "timestamp":    timestamp,
        "window":       win,
        "window_secs":  actual_window_secs,
        "pipeline_ts":  time.time(),
        "sketch_mode":  mode
    }


def _llm_available(tool_context: Optional[ToolContext]) -> bool:
    """
    Check if an LLM client is accessible via tool_context.
    
    Uses defensive access to avoid exceptions from API changes.
    """
    if not tool_context:
        return False
    try:
        # Access the invocation context carefully
        invocation_ctx = getattr(tool_context, '_invocation_context', None)
        if invocation_ctx is None:
            return False
        agent = getattr(invocation_ctx, 'agent', None)
        return agent is not None
    except (AttributeError, TypeError):
        return False
