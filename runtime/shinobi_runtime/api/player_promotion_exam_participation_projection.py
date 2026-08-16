"""Expose cycle-wide Chunin Exam participation counts without leaking hidden IDs."""
from __future__ import annotations

import copy
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.commands.promotion_exam_integrity import team_safe_finals_state
from shinobi_runtime.commands.promotion_exam_scheduler import promotion_exam_profiles

_INSTALLED = False


def _cycle_registration_counts(pipeline: Mapping[str, Any], cycle_id: str) -> tuple[int, int]:
    history = pipeline.get("history")
    if not isinstance(history, list):
        raise OperationError(503, "promotion_exam_context_invalid")
    candidates: set[str] = set()
    teams: set[str] = set()
    for row in history:
        if not (
            isinstance(row, Mapping)
            and row.get("kind") == "promotion_exam_registration"
            and row.get("cycle_id") == cycle_id
        ):
            continue
        refs = row.get("candidate_refs")
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            raise OperationError(503, "promotion_exam_context_invalid")
        candidates.update(refs)
        team_ref = row.get("team_ref")
        if isinstance(team_ref, str) and team_ref:
            teams.add(team_ref)
    return len(teams), len(candidates)


def install_player_promotion_exam_participation_projection() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.api import player_promotion_exam_projection as module

    original = module._promotion_exam_handoffs
    if getattr(original, "_promotion_exam_participation_projection", False):
        _INSTALLED = True
        return

    @wraps(original)
    def wrapped(operations: Any, *, player_id: str) -> list[dict[str, Any]]:
        handoffs = copy.deepcopy(original(operations, player_id=player_id))
        if not handoffs:
            return handoffs
        try:
            pipeline = operations.repository.read_json("state/reg/shinobi-career-pipeline.json")
            profiles = promotion_exam_profiles(operations.repository)
        except (FileNotFoundError, TypeError, ValueError, CommandRejectedError) as exc:
            raise OperationError(503, "promotion_exam_context_invalid") from exc
        profile_by_id = {
            profile.get("id"): profile
            for profile in profiles
            if isinstance(profile.get("id"), str)
        }
        for handoff in handoffs:
            cycle_id = handoff.get("cycle_id")
            profile = profile_by_id.get(handoff.get("profile_ref"))
            if not isinstance(cycle_id, str) or not isinstance(profile, Mapping):
                continue
            team_count, candidate_count = _cycle_registration_counts(pipeline, cycle_id)
            handoff["cycle_registered_team_count"] = team_count
            handoff["cycle_registered_candidate_count"] = candidate_count
            handoff["cycle_registration_scope"] = "institutional_exact_team_count_only"
            if handoff.get("phase") == "finals":
                try:
                    state = team_safe_finals_state(pipeline, profile, cycle_id)
                except CommandRejectedError as exc:
                    raise OperationError(503, "promotion_exam_context_invalid") from exc
                handoff["finals_co_finalist_refs"] = list(state.get("co_finalist_refs", ()))
        return handoffs

    wrapped._promotion_exam_participation_projection = True  # type: ignore[attr-defined]
    module._promotion_exam_handoffs = wrapped
    _INSTALLED = True


__all__ = ["install_player_promotion_exam_participation_projection"]
