#!/usr/bin/env python3
"""
fleet_analysis_response.py
========================
Parse fleet-analysis MQTT payloads and extract LLM token usage from SAM
event-mesh gateway ``task_response`` objects.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

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

    a2a = task_response.get("a2a_task_response")
    task = _unwrap_a2a_task(a2a)
    if not task:
        return None

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
            return {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "cached_tokens": cached,
                "total_tokens": prompt + completion,
                "source": "a2a_history_metadata",
            }

    return None


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
        report = str(obj.get("text") or "")
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
