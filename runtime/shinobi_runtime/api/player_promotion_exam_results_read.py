"""Paged player-safe reads for settled public promotion-exam results.

Institution-wide result tables are deliberately excluded from the mandatory
per-turn play context. Exact public rows remain available through advertised
``exam-results:`` refs handled by the existing ``inspect_game_object`` tool.
The career pipeline remains the sole mutable examination authority.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.operations import CampaignOperations, OperationError
from shinobi_runtime.api.player_promotion_exam_participation_projection import (
    _public_identity,
    _stage_is_settled,
)
from shinobi_runtime.commands.promotion_exam_evaluation import promotion_exam_evaluation_rows
from shinobi_runtime.commands.promotion_exam_scheduler import promotion_exam_profiles
from shinobi_runtime.tx.errors import DirtyRepositoryError, LockUnavailableError


_INSTALLED = False
_PREFIX = "exam-results:"
_PAGE_SIZE = 16
_ALLOWED_PHASES = frozenset(("qualification", "field_evaluation"))


def _parse_ref(object_ref: str) -> tuple[str, str, int]:
    if not object_ref.startswith(_PREFIX):
        raise ValueError("not a promotion exam results ref")
    payload = object_ref[len(_PREFIX):]
    try:
        cycle_id, phase, offset_text = payload.rsplit(":", 2)
    except ValueError as exc:
        raise OperationError(404, "object_not_player_visible") from exc
    if (
        not cycle_id
        or phase not in _ALLOWED_PHASES
        or not offset_text.isdigit()
    ):
        raise OperationError(404, "object_not_player_visible")
    offset = int(offset_text)
    if offset < 0 or offset > 100000:
        raise OperationError(404, "object_not_player_visible")
    return cycle_id, phase, offset


def _cycle_profile_ref(pipeline: Mapping[str, Any], cycle_id: str) -> str:
    history = pipeline.get("history")
    if not isinstance(history, list):
        raise OperationError(503, "promotion_exam_context_invalid")
    refs = {
        row.get("profile_ref")
        for row in history
        if isinstance(row, Mapping)
        and row.get("cycle_id") == cycle_id
        and isinstance(row.get("profile_ref"), str)
    }
    if len(refs) != 1:
        raise OperationError(404, "object_not_player_visible")
    return next(iter(refs))


def _public_results_page(
    operations: CampaignOperations,
    *,
    object_ref: str,
) -> Mapping[str, Any]:
    cycle_id, phase, offset = _parse_ref(object_ref)
    try:
        pipeline = operations.repository.read_json("state/reg/shinobi-career-pipeline.json")
        profiles = promotion_exam_profiles(operations.repository)
        profile_ref = _cycle_profile_ref(pipeline, cycle_id)
    except OperationError:
        raise
    except (FileNotFoundError, TypeError, ValueError, CommandRejectedError) as exc:
        raise OperationError(503, "promotion_exam_context_invalid") from exc

    profile = next(
        (
            row for row in profiles
            if isinstance(row, Mapping) and row.get("id") == profile_ref
        ),
        None,
    )
    if not isinstance(profile, Mapping):
        raise OperationError(404, "object_not_player_visible")
    visibility = profile.get("result_visibility")
    if not isinstance(visibility, Mapping) or visibility.get(phase) != "public_after_settlement":
        raise OperationError(404, "object_not_player_visible")

    try:
        rows = list(promotion_exam_evaluation_rows(pipeline, cycle_id, phase=phase))
    except CommandRejectedError as exc:
        raise OperationError(503, "promotion_exam_context_invalid") from exc
    if not _stage_is_settled(pipeline, profile, cycle_id, phase, rows):
        # A guessed route never reveals partial scores before institutional
        # settlement makes that phase public.
        raise OperationError(404, "object_not_player_visible")

    ordered = sorted(
        (row for row in rows if isinstance(row.get("candidate_ref"), str)),
        key=lambda row: str(row["candidate_ref"]),
    )
    page_rows: list[dict[str, Any]] = []
    for row in ordered[offset:offset + _PAGE_SIZE]:
        candidate_ref = str(row["candidate_ref"])
        name, village = _public_identity(operations, candidate_ref)
        page_rows.append(
            {
                "candidate_ref": candidate_ref,
                "candidate_name": name,
                "village": village,
                "team_ref": row.get("team_ref"),
                "score": row.get("score"),
                "threshold": row.get("threshold"),
                "outcome": row.get("outcome"),
            }
        )
    next_offset = offset + len(page_rows)
    next_ref = (
        f"{_PREFIX}{cycle_id}:{phase}:{next_offset}"
        if next_offset < len(ordered)
        else None
    )
    result = {
        "object_ref": object_ref,
        "view": "promotion_exam_results_page",
        "object": {
            "cycle_id": cycle_id,
            "profile_ref": profile_ref,
            "phase": phase,
            "result_visibility": "public_after_settlement",
            "offset": offset,
            "page_size": _PAGE_SIZE,
            "result_count": len(ordered),
            "rows": page_rows,
            "next_ref": next_ref,
        },
    }
    try:
        validate_bounded_json(result["object"], label="game object projection", allow_float=True)
    except ValueError as exc:
        raise OperationError(503, "promotion_exam_results_out_of_bounds") from exc
    return result


def install_player_promotion_exam_results_read() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original = CampaignOperations.inspect_game_object
    if getattr(original, "_player_promotion_exam_results_read", False):
        _INSTALLED = True
        return

    @wraps(original)
    def inspect_game_object(self: CampaignOperations, object_ref: str) -> Mapping[str, Any]:
        if not object_ref.startswith(_PREFIX):
            return original(self, object_ref)
        try:
            with self._locked():
                self.coordinator.git.assert_pristine()
                before = self._read_fingerprint()
                result = _public_results_page(self, object_ref=object_ref)
                self._require_read_only(before, "promotion_exam_results_read_mutated_campaign")
                return result
        except OperationError:
            raise
        except LockUnavailableError as exc:
            raise OperationError(503, "campaign_writer_busy") from exc
        except DirtyRepositoryError as exc:
            raise OperationError(503, "campaign_repository_dirty") from exc

    inspect_game_object._player_promotion_exam_results_read = True  # type: ignore[attr-defined]
    CampaignOperations.inspect_game_object = inspect_game_object
    _INSTALLED = True


__all__ = ["install_player_promotion_exam_results_read"]
