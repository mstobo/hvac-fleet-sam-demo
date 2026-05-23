#!/usr/bin/env python3
"""
sketch_styles.py
================
Deterministic sketch text: natural language (default) or SoT-inspired Expert Lexicon.

Set SKETCH_STYLE=jargon to emit compact technician shorthand for LLM/agent context.
See tools/sketch-token-lab/ for offline token comparisons (arXiv:2503.05179).
"""

from __future__ import annotations

import os
from typing import Any, Dict

_ZONE_SHORT = {"NORMAL": "N", "WARNING": "W", "CRITICAL": "C"}
_METRIC_SHORT = {
    "inlet_temp_c": "in",
    "outlet_temp_c": "out",
    "motor_temp_c": "mot",
    "humidity_rh": "rh",
    "motor_vibration_mm_s": "vib",
    "supply_temp_c": "sup",
}
_ASSET_SHORT = {
    "machine-001": "m1",
    "machine-002": "m2",
    "machine-003": "m3",
}

SKETCH_JARGON_LEGEND = (
    "Sketch lexicon: m1|m2|m3=machine; in|out|mot=inlet|outlet|motor temp; "
    "Z:N|W|C=zone; Δ↑/Δ↓=spike/drop vs 30s mean; T=°C; μ30[lo-hi]=30s mean and range; "
    "!W|!CRIT=WARNING|CRITICAL; [HB]=heartbeat."
)


def get_sketch_style() -> str:
    """Return 'nl' (default) or 'jargon'."""
    style = (os.getenv("SKETCH_STYLE") or "nl").strip().lower()
    if style in ("jargon", "expert", "expert_lexicon", "sot"):
        return "jargon"
    return "nl"


def render_sketch_nl(
    *,
    point_id: str,
    temperature: float,
    zone: str,
    delta_pct: float,
    forwarded_reason: str,
    win_mean: float,
    win_min: float,
    win_max: float,
    unit_label: str = "",
) -> str:
    suffix = f" {unit_label}".rstrip() if unit_label else ""
    delta_pct_pct = delta_pct * 100

    if forwarded_reason == "heartbeat":
        return (
            f"[HEARTBEAT] {point_id} stable at ~{win_mean:.1f}{suffix} "
            f"(range {win_min:.1f}–{win_max:.1f}{suffix}) over last 30s. "
            f"No significant change. Zone: {zone}."
        )

    move = "spike" if temperature > win_mean else "drop"
    sketch = (
        f"{point_id} recorded a {delta_pct_pct:.1f}% {move} to "
        f"{temperature:.1f}{suffix}. 30s window: mean {win_mean:.1f}{suffix}, "
        f"range [{win_min:.1f}–{win_max:.1f}{suffix}]. Zone: {zone}."
    )
    if zone == "CRITICAL":
        sketch += " Anomaly detected - immediate review required."
    elif zone == "WARNING":
        sketch += " Elevated condition - monitoring advised."
    return sketch


def render_sketch_jargon(
    *,
    point_id: str,
    asset_id: str,
    metric_id: str,
    temperature: float,
    zone: str,
    delta_pct: float,
    forwarded_reason: str,
    win_mean: float,
    win_min: float,
    win_max: float,
) -> str:
    asset = _ASSET_SHORT.get(asset_id or "", (asset_id or "unk").replace("machine-", "m"))
    met = _METRIC_SHORT.get(metric_id or "", (metric_id or "x")[:3])
    z = _ZONE_SHORT.get(zone, (zone or "?")[:1])
    t = temperature
    d = delta_pct * 100
    move = "↑" if t > win_mean else "↓"

    if forwarded_reason == "heartbeat":
        return f"[HB] {asset}:{met} T≈{t:.1f} μ30={win_mean:.1f}[{win_min:.0f}-{win_max:.0f}] Z:{z}"

    flag = ""
    if zone == "CRITICAL":
        flag = " !CRIT"
    elif zone == "WARNING":
        flag = " !W"

    return (
        f"{asset}:{met} Δ{move}{abs(d):.1f}% T{t:.1f} Z:{z} "
        f"μ30={win_mean:.1f}[{win_min:.0f}-{win_max:.0f}]{flag}"
    )


def render_sketch_text(
    *,
    point_id: str,
    asset_id: str,
    metric_id: str,
    temperature: float,
    zone: str,
    delta_pct: float,
    forwarded_reason: str,
    win_mean: float,
    win_min: float,
    win_max: float,
    unit_label: str = "",
    style: str | None = None,
) -> str:
    """Render sketch body for the configured or requested style."""
    use = style or get_sketch_style()
    if use == "jargon":
        return render_sketch_jargon(
            point_id=point_id,
            asset_id=asset_id,
            metric_id=metric_id,
            temperature=temperature,
            zone=zone,
            delta_pct=delta_pct,
            forwarded_reason=forwarded_reason,
            win_mean=win_mean,
            win_min=win_min,
            win_max=win_max,
        )
    return render_sketch_nl(
        point_id=point_id,
        temperature=temperature,
        zone=zone,
        delta_pct=delta_pct,
        forwarded_reason=forwarded_reason,
        win_mean=win_mean,
        win_min=win_min,
        win_max=win_max,
        unit_label=unit_label,
    )


def sketch_context_for_agents() -> Dict[str, Any]:
    """Optional metadata for tool JSON when agents read sketches."""
    style = get_sketch_style()
    out: Dict[str, Any] = {"sketch_style": style}
    if style == "jargon":
        out["sketch_legend"] = SKETCH_JARGON_LEGEND
    return out
