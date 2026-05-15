"""
Mock workforce / CMMS directory for dispatch recommendations (demo only).

Data: sam/data/dispatch_technicians.json
Override path: DISPATCH_TECHNICIANS_JSON
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _data_path() -> Path:
    override = (os.getenv("DISPATCH_TECHNICIANS_JSON") or "").strip()
    if override:
        return Path(override).expanduser()
    here = Path(__file__).resolve().parent
    return here.parent / "data" / "dispatch_technicians.json"


@lru_cache(maxsize=1)
def load_workforce_document() -> Dict[str, Any]:
    path = _data_path()
    if not path.is_file():
        return {
            "schema_version": "0",
            "description": "missing",
            "technicians": [],
            "error": f"Workforce fixture not found: {path}",
        }
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_technicians() -> List[Dict[str, Any]]:
    doc = load_workforce_document()
    return list(doc.get("technicians") or [])


def _infer_site(sensor_id: str) -> str:
    sid = (sensor_id or "").lower()
    if "dc2" in sid:
        return "DC2"
    if "dc1" in sid:
        return "DC1"
    return (os.getenv("DC_BROKER_SITE") or "Hub").strip() or "Hub"


def _skill_hints(sensor_id: str, incident_zone: Optional[str]) -> List[str]:
    hints = ["HVAC"]
    s = (sensor_id or "").lower()
    if "motor" in s:
        hints.append("motor_drives")
    if "inlet" in s or "outlet" in s or "crac" in s:
        hints.append("airflow")
    z = (incident_zone or "").upper()
    if "CRITICAL" in z or z == "CRITICAL":
        hints.append("incident_lead")
    return list(dict.fromkeys(hints))


def _capacity_remaining(tech: Dict[str, Any]) -> int:
    mx = int(tech.get("max_concurrent_jobs") or 1)
    cur = int(tech.get("current_jobs") or 0)
    return max(0, mx - cur)


def score_technician(
    tech: Dict[str, Any],
    *,
    site: str,
    hints: List[str],
    urgency: str,
) -> Tuple[float, List[str]]:
    reasons: List[str] = []
    score = 0.0

    sites = [str(x) for x in (tech.get("sites") or [])]
    if site in sites or "*" in sites or "ALL" in [s.upper() for s in sites]:
        score += 12
        reasons.append(f"covers_site:{site}")
    else:
        reasons.append(f"site_mismatch(wants {site}, has {sites})")

    if tech.get("on_shift"):
        score += 10
        reasons.append("on_shift")
    else:
        score -= 25
        reasons.append("off_shift(mock)")

    cap = _capacity_remaining(tech)
    if cap <= 0:
        score -= 40
        reasons.append("at_capacity")
    else:
        score += min(8, cap * 4)
        reasons.append(f"capacity_remaining:{cap}")

    tskills = {str(s).lower() for s in (tech.get("skills") or [])}
    for h in hints:
        hl = h.lower()
        if hl in tskills:
            score += 6
            reasons.append(f"skill:{h}")

    if urgency == "critical":
        if "incident_lead" in tskills:
            score += 8
            reasons.append("incident_lead_for_critical")
        certs = " ".join(str(c) for c in (tech.get("certifications") or [])).lower()
        if "incident" in certs:
            score += 3
            reasons.append("incident_command_cert")

    return score, reasons


def recommend_technicians(
    sensor_id: str,
    incident_zone: Optional[str] = None,
    urgency: str = "high",
    top_n: int = 3,
) -> Dict[str, Any]:
    """
    Deterministic ranking for demo dispatch. Not a real CMMS optimizer.
    """
    doc = load_workforce_document()
    if doc.get("error"):
        return {"status": "error", "message": doc["error"]}

    site = _infer_site(sensor_id)
    hints = _skill_hints(sensor_id, incident_zone)
    urg = (urgency or "high").strip().lower()
    if urg in ("critical", "sev1", "p1"):
        urg_norm = "critical"
    else:
        urg_norm = "high"

    techs = list_technicians()
    ranked: List[Dict[str, Any]] = []
    for t in techs:
        s, reasons = score_technician(t, site=site, hints=hints, urgency=urg_norm)
        ranked.append(
            {
                "employee_id": t.get("employee_id"),
                "display_name": t.get("display_name"),
                "score": round(s, 2),
                "rank_reasons": reasons,
                "sites": t.get("sites"),
                "skills": t.get("skills"),
                "shift": t.get("shift"),
                "on_shift": t.get("on_shift"),
                "current_jobs": t.get("current_jobs"),
                "max_concurrent_jobs": t.get("max_concurrent_jobs"),
                "home_zone": t.get("home_zone"),
                "certifications": t.get("certifications"),
                "notes": t.get("notes"),
            }
        )

    ranked.sort(key=lambda x: (-x["score"], x.get("display_name") or ""))
    top = ranked[: max(1, min(int(top_n) if top_n else 3, 10))]

    return {
        "status": "ok",
        "data_source": "mock_cmms_fixture",
        "fixture_path": str(_data_path()),
        "sensor_id": sensor_id,
        "inferred_site": site,
        "skill_hints_used": hints,
        "incident_zone_input": incident_zone,
        "urgency": urg_norm,
        "recommendations": top,
        "directory_size": len(techs),
    }
