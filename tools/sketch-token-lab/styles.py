#!/usr/bin/env python3
"""
NL vs Expert-Lexicon-style sketch text for HVAC fleet demo telemetry.

NL mirrors sam/src/sketch_service.py. Jargon is inspired by Sketch-of-Thought
Expert Lexicons (arXiv:2503.05179) — domain shorthand, not full sentences.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List


@dataclass
class TelemetryEvent:
    point_id: str
    asset_id: str
    metric_id: str
    temperature: float
    zone: str
    delta_pct: float
    forwarded_reason: str = "delta"
    trend: str = "STABLE"
    win_mean: float | None = None
    win_min: float | None = None
    win_max: float | None = None

    def __post_init__(self) -> None:
        t = self.temperature
        self.win_mean = self.win_mean if self.win_mean is not None else t
        self.win_min = self.win_min if self.win_min is not None else t - 2
        self.win_max = self.win_max if self.win_max is not None else t + 2


_ZONE_SHORT = {"NORMAL": "N", "WARNING": "W", "CRITICAL": "C"}
_METRIC_SHORT = {
    "inlet_temp_c": "in",
    "outlet_temp_c": "out",
    "motor_temp_c": "mot",
    "humidity_rh": "rh",
    "motor_vibration_mm_s": "vib",
}
_ASSET_SHORT = {
    "machine-001": "m1",
    "machine-002": "m2",
    "machine-003": "m3",
}


def sketch_nl(event: TelemetryEvent) -> str:
    """Same shape as sketch_service.generate_sketch (non-heartbeat)."""
    suffix = " °C"
    point_id = event.point_id
    temperature = event.temperature
    zone = event.zone
    delta_pct_pct = event.delta_pct * 100
    win_mean = event.win_mean or temperature
    win_min = event.win_min or temperature
    win_max = event.win_max or temperature

    if event.forwarded_reason == "heartbeat":
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


def sketch_jargon(event: TelemetryEvent) -> str:
    """SoT Expert-Lexicon-style one-liner for facilities/HVAC."""
    asset = _ASSET_SHORT.get(event.asset_id, event.asset_id.replace("machine-", "m"))
    met = _METRIC_SHORT.get(event.metric_id, event.metric_id[:3])
    z = _ZONE_SHORT.get(event.zone, event.zone[:1])
    t = event.temperature
    d = event.delta_pct * 100
    mu = event.win_mean or t
    lo = event.win_min or t
    hi = event.win_max or t
    move = "↑" if t > mu else "↓"

    if event.forwarded_reason == "heartbeat":
        return f"[HB] {asset}:{met} T≈{t:.1f} μ30={mu:.1f}[{lo:.0f}-{hi:.0f}] Z:{z}"

    flag = ""
    if event.zone == "CRITICAL":
        flag = " !CRIT"
    elif event.zone == "WARNING":
        flag = " !W"

    return (
        f"{asset}:{met} Δ{move}{abs(d):.1f}% T{t:.1f} Z:{z} "
        f"μ30={mu:.1f}[{lo:.0f}-{hi:.0f}]{flag}"
    )


STYLE_RENDERERS: Dict[str, Callable[[TelemetryEvent], str]] = {
    "nl": sketch_nl,
    "jargon": sketch_jargon,
}


def demo_event_stream(count: int = 25) -> List[TelemetryEvent]:
    """Synthetic mix similar to demo publisher spikes."""
    assets = ("machine-001", "machine-002", "machine-003")
    metrics = ("inlet_temp_c", "outlet_temp_c", "motor_temp_c")
    zones = ("NORMAL", "WARNING", "CRITICAL")
    out: List[TelemetryEvent] = []
    for i in range(count):
        asset = assets[i % len(assets)]
        metric = metrics[i % len(metrics)]
        zone = zones[min(2, i // 8)]
        temp = 38.0 + (i % 7) * 4.0 + (2.0 if zone == "WARNING" else 8.0 if zone == "CRITICAL" else 0)
        if metric == "motor_temp_c" and zone != "NORMAL":
            temp += 6.0
        out.append(
            TelemetryEvent(
                point_id=f"{asset}:{metric}",
                asset_id=asset,
                metric_id=metric,
                temperature=temp,
                zone=zone,
                delta_pct=0.04 + (i % 5) * 0.02,
                win_mean=temp - 3,
                win_min=temp - 6,
                win_max=temp + 4,
            )
        )
    return out


# Fleet-analysis style system instructions (abridged) for prompt-overhead comparison.
FLEET_SYSTEM_PROMPT_NL = """You are an automated fleet analysis system for HVAC cooling assets.
Write clear operational English for facilities teams. Reference sketch narratives and charts.
Use sections 1-8. Explain severity in plain language (probable / suggests / not confirmed)."""

FLEET_SYSTEM_PROMPT_JARGON = """You are an automated fleet analysis system (HVAC/DC cooling).
Use Expert-Lexicon sketches: asset:metric, Z:N|W|C, Δ%, T, μ30[lo-hi]. Minimize prose.
Sections 1-8. Findings in technician shorthand; one-line chart evidence per machine."""


def bundle_sketches(events: List[TelemetryEvent], style: str) -> str:
    render = STYLE_RENDERERS[style]
    lines = [render(e) for e in events]
    return "\n".join(lines)
