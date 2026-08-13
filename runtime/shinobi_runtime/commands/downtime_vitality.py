"""Player-facing downtime vitality and persisted boundary normalization."""
from __future__ import annotations

import json
from functools import wraps
from typing import Any, Dict, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes

_INSTALLED = False


def normalize_time_handoff_plan(plan: _BuiltPlan, scene_path: str) -> _BuiltPlan:
    """Clear stale decision state after non-interrupting time settlement."""

    if plan.result.get("interrupted") is True:
        return plan
    raw = plan.writes.get(scene_path)
    if raw is None:
        return plan
    try:
        scene = json.loads(raw.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise CommandRejectedError("campaign_scene_invalid") from exc
    if not isinstance(scene, dict):
        raise CommandRejectedError("campaign_scene_invalid")

    changed = False
    if scene.get("decision_required") is not None:
        scene["decision_required"] = None
        changed = True
    if scene.get("time_passage_allowed") is not True:
        scene["time_passage_allowed"] = True
        changed = True
    if not changed:
        return plan

    writes: Dict[str, bytes] = dict(plan.writes)
    writes[scene_path] = _json_bytes(scene)
    result = dict(plan.result)
    result["player_boundary_kind"] = "none"
    return _BuiltPlan(
        code=plan.code,
        affected_refs=plan.affected_refs,
        writes=writes,
        result=result,
        validator=plan.validator,
    )


def install_downtime_vitality() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_runtime_planner as module

    original = module._refresh_time_advanced_plan
    if not getattr(original, "_player_boundary_semantics", False):
        @wraps(original)
        def wrapped(
            plan: _BuiltPlan,
            scene_path: str,
            *,
            previous_scene: Mapping[str, Any],
        ) -> _BuiltPlan:
            refreshed = original(plan, scene_path, previous_scene=previous_scene)
            return normalize_time_handoff_plan(refreshed, scene_path)

        wrapped._player_boundary_semantics = True
        module._refresh_time_advanced_plan = wrapped
    _INSTALLED = True


__all__ = ["install_downtime_vitality", "normalize_time_handoff_plan"]
