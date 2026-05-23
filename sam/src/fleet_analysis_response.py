#!/usr/bin/env python3
"""
fleet_analysis_response.py
========================
Parse fleet-analysis MQTT payloads and extract LLM token usage from SAM
event-mesh gateway ``task_response`` objects.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

_SKETCH_RETURNED_LINE_RE = re.compile(
    r"^Sketch evidence: (\d+) sketch\(es\) returned for [`']?([^`'(\s]+)",
    re.MULTILINE,
)
_INSUFFICIENT_SKETCH_LINE_RE = re.compile(
    r"^Insufficient sketch context:.*$",
    re.MULTILINE,
)
_SKETCH_BY_MACHINE_LINE_RE = re.compile(
    r"^Sketch evidence \(by (?:asset|machine)\):.*$",
    re.MULTILINE,
)
_PER_POINT_PLOTLY_URL_LINE_RE = re.compile(
    r"^.*plotly-html\?sensor_id=.*$",
    re.MULTILINE | re.IGNORECASE,
)
_CHART_LINE_PER_POINT_RE = re.compile(
    r"^- Chart:.*plotly-html\?sensor_id=.*$",
    re.MULTILINE | re.IGNORECASE,
)
_MACHINE_PLOTLY_MARKER = "machine-plotly-html"

SCHEMA_VERSION = "1.0.0"
EVENT_TYPE = "FLEET_ANALYSIS_RESPONSE"


def _coerce_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _unwrap_a2a_task(task: Any) -> Dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    if "result" in task and isinstance(task.get("result"), dict):
        return task["result"]
    return task


def _sum_by_model(details: Dict[str, Any]) -> Tuple[int, int, int]:
    prompt = completion = cached = 0
    by_model = details.get("by_model")
    if not isinstance(by_model, dict):
        return prompt, completion, cached
    for entry in by_model.values():
        if not isinstance(entry, dict):
            continue
        prompt += _coerce_int(entry.get("input_tokens"))
        completion += _coerce_int(entry.get("output_tokens"))
        cached += _coerce_int(entry.get("cached_input_tokens") or entry.get("cached_tokens"))
    return prompt, completion, cached


def _sum_usage_list(details: Dict[str, Any]) -> Tuple[int, int, int]:
    prompt = completion = cached = 0
    usages = details.get("usages") or details.get("usage")
    if not isinstance(usages, list):
        return prompt, completion, cached
    for entry in usages:
        if not isinstance(entry, dict):
            continue
        prompt += _coerce_int(
            entry.get("input_tokens")
            or entry.get("prompt_tokens")
            or entry.get("prompt_token_count")
        )
        completion += _coerce_int(
            entry.get("output_tokens")
            or entry.get("completion_tokens")
            or entry.get("completion_token_count")
        )
        cached += _coerce_int(
            entry.get("cached_input_tokens")
            or entry.get("cached_tokens")
        )
    return prompt, completion, cached


def extract_llm_usage_from_metadata(metadata: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(metadata, dict):
        return None

    prompt = _coerce_int(metadata.get("total_input_tokens"))
    completion = _coerce_int(metadata.get("total_output_tokens"))
    cached = _coerce_int(metadata.get("total_cached_input_tokens"))

    details = metadata.get("token_usage_details")
    if isinstance(details, dict):
        if prompt == 0 and completion == 0:
            p2, c2, ca2 = _sum_by_model(details)
            prompt, completion, cached = p2, c2, max(cached, ca2)
        if prompt == 0 and completion == 0:
            p3, c3, ca3 = _sum_usage_list(details)
            prompt, completion, cached = p3, c3, max(cached, ca3)

    # OpenAI-style usage block occasionally nested under metadata
    usage = metadata.get("usage")
    if isinstance(usage, dict):
        if prompt == 0:
            prompt = _coerce_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
        if completion == 0:
            completion = _coerce_int(
                usage.get("completion_tokens") or usage.get("output_tokens")
            )
        if cached == 0:
            cached = _coerce_int(
                (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                if isinstance(usage.get("prompt_tokens_details"), dict)
                else usage.get("cached_tokens")
            )

    if prompt == 0 and completion == 0 and cached == 0:
        return None

    total = prompt + completion
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": cached,
        "total_tokens": total,
    }


def _deep_find_llm_usage(node: Any, *, _depth: int = 0) -> Optional[Dict[str, Any]]:
    """Walk nested gateway payloads for SAM/LiteLLM token blocks (shape varies by version)."""
    if _depth > 14:
        return None
    if isinstance(node, dict):
        usage = extract_llm_usage_from_metadata(node)
        if usage:
            return usage
        for value in node.values():
            found = _deep_find_llm_usage(value, _depth=_depth + 1)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _deep_find_llm_usage(item, _depth=_depth + 1)
            if found:
                return found
    return None


def extract_llm_usage_from_task_response(task_response: Any) -> Optional[Dict[str, Any]]:
    """Extract cumulative LLM usage from a sam-event-mesh-gateway task_response object."""
    if not isinstance(task_response, dict):
        return None

    if isinstance(task_response.get("llm_usage"), dict):
        usage = dict(task_response["llm_usage"])
        if usage.get("total_tokens") is None:
            usage["total_tokens"] = _coerce_int(usage.get("prompt_tokens")) + _coerce_int(
                usage.get("completion_tokens")
            )
        return usage

    usage = extract_llm_usage_from_metadata(task_response.get("metadata"))
    if usage:
        usage["source"] = "task_response_metadata"
        return usage

    a2a = task_response.get("a2a_task_response")
    task = _unwrap_a2a_task(a2a) if a2a is not None else None
    if not task and isinstance(a2a, dict):
        task = a2a

    if not task:
        return _deep_find_llm_usage(task_response)

    usage = extract_llm_usage_from_metadata(task.get("metadata"))
    if usage:
        usage["source"] = "a2a_task_metadata"
        task_id = task.get("id")
        if task_id:
            usage["task_id"] = task_id
        return usage

    # Fallback: walk status message metadata / history parts (ADK/LiteLLM)
    status = task.get("status")
    if isinstance(status, dict):
        message = status.get("message")
        if isinstance(message, dict):
            usage = extract_llm_usage_from_metadata(message.get("metadata"))
            if usage:
                usage["source"] = "a2a_status_message_metadata"
                task_id = task.get("id")
                if task_id:
                    usage["task_id"] = task_id
                return usage

    history = task.get("history")
    if isinstance(history, list):
        prompt = completion = cached = 0
        for message in history:
            if not isinstance(message, dict):
                continue
            part_usage = extract_llm_usage_from_metadata(message.get("metadata"))
            if not part_usage:
                continue
            prompt += part_usage["prompt_tokens"]
            completion += part_usage["completion_tokens"]
            cached += part_usage["cached_tokens"]
        if prompt or completion or cached:
            usage = {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "cached_tokens": cached,
                "total_tokens": prompt + completion,
                "source": "a2a_history_metadata",
            }
            task_id = task.get("id")
            if task_id:
                usage["task_id"] = task_id
            return usage

    usage = _deep_find_llm_usage(task)
    if usage:
        usage["source"] = "a2a_deep_metadata"
        task_id = task.get("id")
        if task_id:
            usage["task_id"] = task_id
        return usage

    return _deep_find_llm_usage(task_response)


def _text_from_a2a_message(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    chunks: List[str] = []
    for part in message.get("parts") or []:
        if not isinstance(part, dict):
            continue
        for key in ("text", "content", "data"):
            val = part.get(key)
            if isinstance(val, str) and val.strip():
                chunks.append(val.strip())
                break
    return "\n\n".join(chunks)


def extract_report_text_from_task_response(obj: Dict[str, Any]) -> str:
    """
    Prefer full report text from task_response.text; fall back to A2A status/history
    when the gateway leaves text empty or the model splits output across messages.
    """
    primary = str(obj.get("text") or "").strip()
    if primary and "1) Summary" in primary:
        return primary

    candidates: List[str] = []
    if primary:
        candidates.append(primary)

    a2a = obj.get("a2a_task_response")
    task = _unwrap_a2a_task(a2a) if isinstance(a2a, dict) else {}
    if task:
        status = task.get("status")
        if isinstance(status, dict):
            msg = status.get("message")
            status_text = _text_from_a2a_message(msg)
            if status_text:
                candidates.append(status_text)

        for message in task.get("history") or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").lower()
            if role and role not in ("agent", "assistant", "model"):
                continue
            hist_text = _text_from_a2a_message(message)
            if hist_text:
                candidates.append(hist_text)

    for candidate in sorted(candidates, key=len, reverse=True):
        if "1) Summary" in candidate:
            return candidate

    return "\n\n".join(candidates) if candidates else primary


def validate_fleet_report_structure(body: str) -> List[str]:
    """Return human-readable issues when SECTION A shape is missing (apples-to-apples checks)."""
    text = (body or "").strip()
    issues: List[str] = []
    if not text:
        return ["empty report body"]

    if "1) Summary" not in text:
        issues.append("missing section '1) Summary' (got unnumbered 'Summary'?)")
    if re.search(r"(?m)^Summary\s*$", text) and "1) Summary" not in text:
        issues.append("unnumbered section headings (must be '1) Summary', '2) Timeline', …)")
    if text.startswith("### machine-"):
        issues.append("report begins with ### per-point headings (should start with 1) Summary)")
    lowered = text.lower()
    if "machine-plotly-html" not in lowered:
        issues.append("no machine-plotly-html chart links (expected exactly 3)")
    if "plotly-html?sensor_id=" in lowered or "plotly-html?sensor_id%3d" in lowered:
        if "machine-plotly-html" in lowered:
            issues.append("per-point plotly-html?sensor_id= links present (forbidden when machine charts exist)")
        else:
            issues.append("only per-point plotly-html links (need 3× machine-plotly-html)")
    if "Chart Evidence" not in text and ("1) Summary" in text or text.startswith("Summary")):
        issues.append("missing 'Chart Evidence' subsection")
    for section in ("2) Timeline", "3) Severity", "8) Dispatch"):
        if section not in text:
            issues.append(f"missing '{section}'")
    if text.count("plotly-html") > 6:
        issues.append(f"too many chart URLs ({text.count('plotly-html')}); expected 3 machine charts only")
    return issues


def extract_a2a_task_state(task_response: Any) -> Optional[str]:
    """Return A2A task status state (e.g. completed, failed) when present."""
    if not isinstance(task_response, dict):
        return None
    a2a = task_response.get("a2a_task_response")
    task = _unwrap_a2a_task(a2a) if a2a is not None else task_response
    if not task:
        return None
    status = task.get("status")
    if isinstance(status, dict):
        state = status.get("state")
        if state is not None:
            return str(state).lower()
    return None


def is_failed_task_response(task_response: Any, report_text: str = "") -> bool:
    state = extract_a2a_task_state(task_response)
    if state in ("failed", "error", "cancelled", "canceled"):
        return True
    lowered = (report_text or "").lower()
    return any(
        phrase in lowered
        for phrase in (
            "rejected the authentication credentials",
            "rejected the request",
            "invalid api key",
            "authentication",
            "unauthorized",
            "401",
            "403",
            "rate limit",
        )
    )


def normalize_fleet_chart_links(body: str) -> str:
    """
    Fleet SECTION A cleanup: drop per-point plotly-html?sensor_id= URLs and Chart:
    bullets under ### when combined machine-plotly-html links are also present.
    """
    text = body or ""
    lowered = text.lower()
    if "plotly-html" not in lowered:
        return text

    has_machine = _MACHINE_PLOTLY_MARKER in lowered
    has_per_point = "sensor_id=" in lowered and "plotly-html" in lowered

    lines = []
    for line in text.splitlines():
        line_lower = line.lower()
        if has_machine and has_per_point:
            if _PER_POINT_PLOTLY_URL_LINE_RE.match(line):
                continue
            if _CHART_LINE_PER_POINT_RE.match(line):
                continue
            if line.strip().lower().startswith("- chart:") and "plotly-html" in line_lower:
                if "sensor_id=" in line_lower and _MACHINE_PLOTLY_MARKER not in line_lower:
                    continue
            if "insufficient per-point incident context" in line_lower:
                continue
        lines.append(line)

    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out)


def compress_fleet_sketch_section(body: str) -> str:
    """
    Collapse repeated per-point sketch debug lines (fleet SECTION A) into one summary.
    Safe no-op when fewer than four matching lines (single-asset reports unchanged).
    """
    matches = list(_SKETCH_RETURNED_LINE_RE.finditer(body or ""))
    if len(matches) < 4:
        return body

    per_machine: Dict[str, int] = {}
    for match in matches:
        count = int(match.group(1))
        scope = match.group(2).strip()
        machine = scope.split(":")[0] if ":" in scope else scope
        per_machine[machine] = max(per_machine.get(machine, 0), count)

    out = _SKETCH_RETURNED_LINE_RE.sub("", body)
    out = _INSUFFICIENT_SKETCH_LINE_RE.sub("", out)
    out = _SKETCH_BY_MACHINE_LINE_RE.sub("", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()

    parts = [f"{machine}={count}" for machine, count in sorted(per_machine.items())]
    summary = f"Sketch evidence (by machine): {', '.join(parts)}"
    if any(count >= 25 for count in per_machine.values()):
        summary += " (tool limit 25 per machine; more sketches may exist in the window)"
    summary += "."
    if any(count > 0 for count in per_machine.values()):
        summary += "\nInsufficient sketch context: No — sketch narratives were included in this analysis."

    if "8) Dispatch" in out:
        return out.replace("8) Dispatch", f"{summary}\n\n8) Dispatch", 1)
    return f"{out}\n\n{summary}"


def format_slack_analysis_body(
    raw: str,
    *,
    trace_id: str,
    rewrite_urls=None,
) -> str:
    """
    Build Slack message body for analysis-response (never dumps raw task_response JSON).
    """
    report_text, llm_usage, meta = parse_analysis_response_payload(raw)
    task_state = meta.get("task_state")
    failed = meta.get("task_failed", False)

    if rewrite_urls and report_text:
        body = rewrite_urls(report_text)
    else:
        body = report_text or ""

    if body:
        body = normalize_fleet_chart_links(body)
        body = compress_fleet_sketch_section(body)
        structure_issues = validate_fleet_report_structure(body)
        if structure_issues and not failed:
            body = (
                "⚠️ *Fleet report format incomplete* — "
                + "; ".join(structure_issues)
                + ". Expected sections 1–8, Chart Evidence with 3 machine-plotly-html URLs. "
                "Partial model output follows.\n\n"
                + body
            )

    if not body.strip() and meta.get("payload_format") == "json":
        body = (
            "Fleet analysis returned an empty response. "
            "Check /tmp/sam-fleet-analysis-gateway.log and SAM control-plane logs."
        )

    footer = format_llm_usage_footer(llm_usage)
    task_id = (llm_usage or {}).get("task_id")
    if not task_id and meta.get("payload_format") == "json":
        try:
            obj = json.loads(raw)
            a2a = obj.get("a2a_task_response") if isinstance(obj, dict) else None
            task = _unwrap_a2a_task(a2a) if a2a else {}
            task_id = task.get("id")
        except json.JSONDecodeError:
            pass

    if failed:
        header = "*Automated Fleet Analysis failed*"
        hint = (
            "\n\n_Check `LLM_SERVICE_API_KEY` / `LLM_SERVICE_ENDPOINT` in `sam/.env`, "
            "run `python test_llm.py`, then restart `./start_demo_stack.sh --fresh`._"
        )
        if "team_model_access_denied" in body.lower() or "team not allowed" in body.lower():
            hint = (
                "\n\n_Your LiteLLM key is restricted to a model group (e.g. `production-models`). "
                "Set `LLM_SERVICE_GENERAL_MODEL_NAME` and `LLM_SERVICE_PLANNING_MODEL_NAME` to that "
                "group in `sam/.env`, run `python test_llm.py`, then `./start_demo_stack.sh --fresh`._"
            )
        elif any(
            k in body.lower()
            for k in ("authentication", "api key", "rejected the request", "rejected the")
        ):
            body = body + hint
    else:
        header = "*Automated Fleet Analysis*"

    lines = [header, f"`Trace ID: {trace_id}`"]
    if task_id:
        lines.append(f"`task_id: {task_id}`" + (f" · state={task_state}" if task_state else ""))
    lines.append(body)
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def parse_analysis_response_payload(raw: str) -> Tuple[str, Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Parse MQTT payload from sensors/fleet/analysis-response.

    Returns (report_text, llm_usage_or_none, envelope_metadata).
    Supports legacy plain-text bodies and JSON task_response / envelope objects.
    """
    text = (raw or "").strip()
    meta: Dict[str, Any] = {"payload_format": "text"}

    if not text:
        return "", None, meta

    if not text.startswith("{"):
        return text, None, meta

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return text, None, meta

    if not isinstance(obj, dict):
        return text, None, meta

    meta["payload_format"] = "json"

    if obj.get("event_type") == EVENT_TYPE or obj.get("schema_version"):
        report = str(obj.get("text") or "")
        usage = obj.get("llm_usage") if isinstance(obj.get("llm_usage"), dict) else None
        if usage is None:
            usage = extract_llm_usage_from_task_response(obj.get("task_response"))
        meta["schema_version"] = obj.get("schema_version") or SCHEMA_VERSION
        return report, usage, meta

    if "text" in obj or "a2a_task_response" in obj:
        report = extract_report_text_from_task_response(obj)
        usage = extract_llm_usage_from_task_response(obj)
        meta["schema_version"] = SCHEMA_VERSION
        meta["task_response"] = True
        meta["task_state"] = extract_a2a_task_state(obj)
        meta["task_failed"] = is_failed_task_response(obj, report)
        return report, usage, meta

    # Unknown JSON — do not return the raw blob as report text
    meta["parse_warning"] = "unrecognized_json_shape"
    return "", None, meta


def format_llm_usage_footer(usage: Optional[Dict[str, Any]]) -> str:
    if not usage:
        return ""
    prompt = _coerce_int(usage.get("prompt_tokens"))
    completion = _coerce_int(usage.get("completion_tokens"))
    cached = _coerce_int(usage.get("cached_tokens"))
    total = _coerce_int(usage.get("total_tokens")) or (prompt + completion)
    lines = [
        "",
        "---",
        f"*LLM usage (this run):* {total:,} tokens "
        f"({prompt:,} in / {completion:,} out"
        + (f" / {cached:,} cached" if cached else "")
        + ")",
    ]
    task_id = usage.get("task_id")
    if task_id:
        lines.append(f"`task_id: {task_id}`")
    return "\n".join(lines)
