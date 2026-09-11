"""Closed deterministic procedure-time registry."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[3]
_MW = _ROOT / "game/data/martial-world"


@lru_cache(maxsize=1)
def _data() -> Mapping[str, Any]:
    row = json.loads((_MW / "procedures.json").read_text(encoding="utf-8"))
    if not isinstance(row, Mapping):
        raise ValueError("procedure registry invalid")
    return row


def procedure_duration_minutes(kind: str) -> int:
    row = _data().get("procedures", {}).get(kind)
    if not isinstance(row, Mapping):
        raise KeyError(kind)
    minutes = int(row.get("duration_minutes", 0))
    if minutes <= 0:
        raise ValueError("procedure duration invalid")
    return minutes


def medical_procedure_plan(category: str, health: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return the one canonical foreground clinical timing plan."""
    cfg = _data().get("medical_selection", {})
    if not isinstance(cfg, Mapping):
        raise ValueError("medical procedure selection invalid")
    cat = str(category)
    diagnostic = {str(x) for x in cfg.get("diagnostic_categories", []) if isinstance(x, str)}
    major_categories = {str(x) for x in cfg.get("major_categories", []) if isinstance(x, str)}
    exam_minutes = procedure_duration_minutes("medical_examination") if cat in diagnostic else 0
    if cat == "antidote":
        treatment_kind = "poison_treatment"
    elif cat in {"wound", "bone", "internal"}:
        major = cat in major_categories
        health_row = health if isinstance(health, Mapping) else {}
        injuries = health_row.get("injuries", []) if isinstance(health_row.get("injuries"), list) else []
        severity_min = max(1, int(cfg.get("major_wound_severity_min", 60)))
        bleed_min = max(1, int(cfg.get("major_bleeding_ml_per_min_min", 6)))
        structural = tuple(str(x) for x in cfg.get("major_structure_fields", []) if isinstance(x, str))
        for wound in injuries:
            if not isinstance(wound, Mapping):
                continue
            if int(wound.get("severity", 0)) >= severity_min or int(wound.get("bleeding_ml_per_min", 0)) >= bleed_min:
                major = True
                break
            if any(int(wound.get(field, 0)) > 0 for field in structural):
                major = True
                break
        treatment_kind = "wound_treatment_major" if major else "wound_treatment_minor"
    else:
        treatment_kind = "medicine_administration"
    treatment_minutes = procedure_duration_minutes(treatment_kind)
    return {
        "category": cat,
        "examination_minutes": exam_minutes,
        "treatment_kind": treatment_kind,
        "treatment_minutes": treatment_minutes,
        "total_minutes": exam_minutes + treatment_minutes,
    }


__all__ = ["medical_procedure_plan", "procedure_duration_minutes"]
