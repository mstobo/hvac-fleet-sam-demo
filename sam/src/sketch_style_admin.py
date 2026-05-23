#!/usr/bin/env python3
"""
Runtime sketch style toggle (NL vs jargon) for demo dashboards.

Writes SKETCH_STYLE_OVERRIDE_PATH on the shared dbdata volume so sketch,
sam-control-plane, and chart-query see the same effective style without
recreating containers.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

VALID_STYLES = frozenset({"nl", "jargon"})


def override_path() -> Path:
    return Path(
        os.getenv("SKETCH_STYLE_OVERRIDE_PATH", "/app/dbdata/sketch_style.override")
    ).expanduser()


def normalize_style(value: str) -> str:
    style = (value or "").strip().lower()
    if style in ("jargon", "expert", "expert_lexicon", "sot"):
        return "jargon"
    if style in ("nl", "natural", "natural_language", "default"):
        return "nl"
    raise ValueError(f"Invalid sketch style {value!r}; use nl or jargon")


def read_override_file() -> Optional[str]:
    path = override_path()
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip().lower()
    if text in VALID_STYLES:
        return text
    return None


def env_sketch_style() -> str:
    style = (os.getenv("SKETCH_STYLE") or "nl").strip().lower()
    if style in ("jargon", "expert", "expert_lexicon", "sot"):
        return "jargon"
    return "nl"


def get_effective_sketch_style() -> str:
    """Override file wins over SKETCH_STYLE env."""
    override = read_override_file()
    if override:
        return override
    return env_sketch_style()


def set_sketch_style_override(style: str) -> str:
    normalized = normalize_style(style)
    path = override_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized + "\n", encoding="utf-8")
    return normalized


def clear_sketch_style_override() -> bool:
    path = override_path()
    if path.is_file():
        path.unlink()
        return True
    return False


def sketch_style_status() -> Dict[str, Any]:
    override = read_override_file()
    env_style = env_sketch_style()
    effective = get_effective_sketch_style()
    return {
        "style": effective,
        "effective": effective,
        "env_style": env_style,
        "override": override,
        "override_path": str(override_path()),
        "persisted": override is not None,
    }


def admin_token_required() -> str:
    return (
        os.getenv("SKETCH_ADMIN_TOKEN", "").strip()
        or os.getenv("CHART_QUERY_API_KEY", "").strip()
    )


def admin_request_authorized(headers: Any, query: Dict[str, list]) -> bool:
    """When a token is configured, require X-API-Key or ?key= match."""
    required = admin_token_required()
    if not required:
        return True
    provided = ""
    if headers:
        provided = (headers.get("X-API-Key") or headers.get("x-api-key") or "").strip()
    if not provided and query:
        provided = (query.get("key") or [""])[0].strip()
    return provided == required
