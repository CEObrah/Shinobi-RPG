"""Player-safe exact phase dates for active promotion examination cycles."""
from __future__ import annotations

import copy
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.operations import CampaignOperations, OperationError
from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.promotion_exam_pacing import promotion_exam_schedule_for_cycle

_INSTALLED = False


def install_player_promotion_exam_schedule_projection() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original_play_context = CampaignOperations.play_context
    if getattr(original_play_context, "_promotion_exam_schedule_projection", False):
        _INSTALLED = True
        return

    @wraps(original_play_context)
    def play_context(self: CampaignOperations) -> Mapping[str, Any]:
        response = copy.deepcopy(original_play_context(self))
        scene = response.get("scene") if isinstance(response, Mapping) else None
        handoffs = scene.get("promotion_exam_handoffs") if isinstance(scene, Mapping) else None
        if not isinstance(handoffs, list):
            return response
        enriched = []
        for row in handoffs:
            if not isinstance(row, Mapping):
                raise OperationError(503, "promotion_exam_context_invalid")
            updated = dict(row)
            cycle_id = updated.get("cycle_id")
            phase = updated.get("phase")
            if not isinstance(cycle_id, str) or not isinstance(phase, str):
                raise OperationError(503, "promotion_exam_context_invalid")
            try:
                schedule = dict(
                    promotion_exam_schedule_for_cycle(
                        self.repository,
                        cycle_id=cycle_id,
                    )
                )
            except (CommandRejectedError, TypeError, ValueError) as exc:
                raise OperationError(503, "promotion_exam_context_invalid") from exc
            phases = list(schedule)
            if phase not in phases:
                raise OperationError(503, "promotion_exam_context_invalid")
            index = phases.index(phase)
            next_phase = phases[index + 1] if index + 1 < len(phases) else None
            updated["phase_schedule"] = schedule
            updated["next_phase"] = next_phase
            updated["next_phase_at"] = schedule.get(next_phase) if next_phase is not None else None
            updated["finals_at"] = schedule.get("finals")
            updated["promotion_review_at"] = schedule.get("promotion_review")
            updated["closes_at"] = schedule.get("closed")
            enriched.append(updated)
        if isinstance(scene, dict):
            scene["promotion_exam_handoffs"] = enriched
        validate_bounded_json(response, label="play context", allow_float=True)
        return response

    play_context._promotion_exam_schedule_projection = True  # type: ignore[attr-defined]
    CampaignOperations.play_context = play_context
    _INSTALLED = True


__all__ = ["install_player_promotion_exam_schedule_projection"]
