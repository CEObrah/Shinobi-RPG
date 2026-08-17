"""Compact player-facing orientation for unfinished scenes and procedures.

This projection does not create a second activity authority. It derives one
small turn-continuity hint from already-authoritative scene, examination,
check-in, report, and time-continuation state so a fresh ChatGPT session can
distinguish a real player decision from an obvious procedural continuation.
"""
from __future__ import annotations

import copy
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.operations import CampaignOperations

_INSTALLED = False
_MAX_SOURCE_REFS = 6


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _texts(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _resume_fields(scene: Mapping[str, Any]) -> dict[str, Any]:
    narrative = scene.get("narrative")
    result: dict[str, Any] = {}
    if isinstance(narrative, Mapping):
        last_major = _text(narrative.get("last_major_choice"))
        last_scene = _text(narrative.get("last_scene_summary"))
        scene_type = _text(narrative.get("current_scene_type"))
        if last_major is not None:
            result["last_major_choice_summary"] = last_major
        if last_scene is not None:
            result["last_scene_summary"] = last_scene
        if scene_type is not None:
            result["scene_type"] = scene_type
    summary = _text(scene.get("scene_summary"))
    if summary is not None:
        result["current_scene_summary"] = summary
    return result


def _exam_actionable(row: Mapping[str, Any]) -> tuple[str, str] | None:
    if row.get("registration_open") is True and _texts(row.get("unregistered_candidate_refs")):
        return "registration", "Eligible player-team candidates remain unregistered."
    if row.get("evaluation_open") is True and _texts(row.get("unevaluated_candidate_refs")):
        return "evaluation", "Registered player-team candidates remain unevaluated in the current stage."
    if row.get("finals_open") is True:
        bouts = row.get("finals_open_bouts")
        if isinstance(bouts, list) and bouts:
            return "finals", "The player-team examination has an unresolved public finals bout."
    return None


def _exam_activity(handoffs: object) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (actionable, waiting) exam handoffs in stable projection order."""

    if not isinstance(handoffs, list):
        return None, None
    waiting: dict[str, Any] | None = None
    for raw in handoffs:
        if not isinstance(raw, Mapping):
            continue
        cycle_id = _text(raw.get("cycle_id"))
        team_ref = _text(raw.get("team_ref"))
        phase = _text(raw.get("phase"))
        if cycle_id is None or team_ref is None or phase is None:
            continue
        source_refs = [cycle_id, team_ref][:_MAX_SOURCE_REFS]
        actionable = _exam_actionable(raw)
        if actionable is not None:
            stage, reason = actionable
            return {
                "kind": "promotion_exam",
                "status": "requires_player_decision",
                "requires_player_decision": True,
                "continue_without_player": False,
                "current_stage": stage,
                "phase": phase,
                "cycle_id": cycle_id,
                "team_ref": team_ref,
                "source_refs": source_refs,
                "reason": reason,
            }, waiting

        next_phase = _text(raw.get("next_phase"))
        next_phase_at = _text(raw.get("next_phase_at"))
        if waiting is None and next_phase_at is not None:
            waiting = {
                "kind": "promotion_exam",
                "status": "standing_wait",
                "requires_player_decision": False,
                "continue_without_player": True,
                "phase": phase,
                "cycle_id": cycle_id,
                "team_ref": team_ref,
                "source_refs": source_refs,
                "next_stage": next_phase,
                "next_boundary_at": next_phase_at,
                "reason": (
                    "The current player-team examination stage has no unresolved player action; "
                    "the next authoritative phase boundary is scheduled."
                ),
            }
    return None, waiting


def _time_continuation(scene: Mapping[str, Any]) -> dict[str, Any] | None:
    continuation = scene.get("time_continuation")
    if not isinstance(continuation, Mapping):
        return None
    target = None
    for key in ("continuation_target", "requested_target", "target_time", "target"):
        target = _text(continuation.get(key))
        if target is not None:
            break
    result: dict[str, Any] = {
        "kind": "time_continuation",
        "status": "procedural_continuation",
        "requires_player_decision": False,
        "continue_without_player": True,
        "reason": "A previously declared time horizon still has bounded causal work remaining.",
    }
    if target is not None:
        result["target_time"] = target
    return result


def derive_activity_handoff(scene: Mapping[str, Any]) -> dict[str, Any]:
    """Derive one primary turn-completion signal from player-visible scene state."""

    resume = _resume_fields(scene)
    decision = _text(scene.get("decision_required"))
    if decision is not None:
        return {
            **resume,
            "kind": "protected_decision",
            "status": "requires_player_decision",
            "requires_player_decision": True,
            "continue_without_player": False,
            "reason": decision,
        }

    exam_actionable, exam_waiting = _exam_activity(scene.get("promotion_exam_handoffs"))
    if exam_actionable is not None:
        return {**resume, **exam_actionable}

    checkins = scene.get("team_checkin_handoffs")
    if isinstance(checkins, list):
        visible = [row for row in checkins if isinstance(row, Mapping)]
        if visible:
            first = visible[0]
            source_refs = [
                value
                for value in (
                    _text(first.get("checkin_ref")),
                    _text(first.get("team_ref")),
                    _text(first.get("contact_actor_ref")),
                )
                if value is not None
            ][:_MAX_SOURCE_REFS]
            result: dict[str, Any] = {
                **resume,
                "kind": "team_checkin",
                "status": "requires_player_decision",
                "requires_player_decision": True,
                "continue_without_player": False,
                "pending_count": len(visible),
                "source_refs": source_refs,
                "reason": "A player-led team has a durable unhandled check-in ready for Wei.",
            }
            topics = _texts(first.get("topic_cues"))[:3]
            if topics:
                result["topic_cues"] = topics
            return result

    narrative = scene.get("narrative")
    if isinstance(narrative, Mapping):
        reports = _texts(narrative.get("available_reports"))
        if reports:
            return {
                **resume,
                "kind": "report_handoff",
                "status": "requires_player_decision",
                "requires_player_decision": True,
                "continue_without_player": False,
                "pending_count": len(reports),
                "reason": "A newly available player-visible report is waiting for review or handling.",
            }

    continuation = _time_continuation(scene)
    if continuation is not None:
        return {**resume, **continuation}

    if exam_waiting is not None:
        return {**resume, **exam_waiting}

    return {
        **resume,
        "kind": "scene",
        "status": "scene_open",
        "requires_player_decision": False,
        "continue_without_player": False,
        "reason": (
            "No protected decision or authoritative automatic continuation is currently projected; "
            "use the live scene and the player's declared intent to decide whether the scene naturally continues."
        ),
    }


def install_player_activity_handoff_projection() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original_play_context = CampaignOperations.play_context
    if getattr(original_play_context, "_player_activity_handoff_projection", False):
        _INSTALLED = True
        return

    @wraps(original_play_context)
    def play_context(self: CampaignOperations) -> Mapping[str, Any]:
        response = copy.deepcopy(original_play_context(self))
        scene = response.get("scene") if isinstance(response, Mapping) else None
        if isinstance(scene, dict):
            scene["activity_handoff"] = derive_activity_handoff(scene)
        validate_bounded_json(response, label="play context", allow_float=True)
        return response

    play_context._player_activity_handoff_projection = True  # type: ignore[attr-defined]
    CampaignOperations.play_context = play_context
    _INSTALLED = True


__all__ = ["derive_activity_handoff", "install_player_activity_handoff_projection"]
