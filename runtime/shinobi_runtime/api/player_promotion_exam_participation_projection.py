"""Expose cycle-wide Chunin Exam participation and public stage results."""
from __future__ import annotations

import copy
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.commands.promotion_exam_evaluation import (
    promotion_exam_evaluation_rows,
    promotion_exam_stage_candidate_refs,
)
from shinobi_runtime.commands.promotion_exam_integrity import team_safe_finals_state
from shinobi_runtime.commands.promotion_exam_scheduler import promotion_exam_profiles

_INSTALLED = False
_MAX_PUBLIC_RESULTS = 96


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


def _public_identity(operations: Any, candidate_ref: str) -> tuple[str | None, str | None]:
    try:
        _path, person = operations._owner_record(candidate_ref)
    except OperationError:
        return None, None
    if not isinstance(person, Mapping):
        return None, None
    name = person.get("name")
    village = person.get("village_or_affiliation")
    return (
        name if isinstance(name, str) and name else None,
        village if isinstance(village, str) and village else None,
    )


def _latest_cycle_phase(pipeline: Mapping[str, Any], cycle_id: str) -> str | None:
    history = pipeline.get("history")
    if not isinstance(history, list):
        raise OperationError(503, "promotion_exam_context_invalid")
    phase = None
    for row in history:
        if (
            isinstance(row, Mapping)
            and row.get("kind") == "promotion_exam_cycle_phase"
            and row.get("cycle_id") == cycle_id
            and isinstance(row.get("phase"), str)
        ):
            phase = str(row["phase"])
    return phase


def _stage_is_settled(
    pipeline: Mapping[str, Any],
    profile: Mapping[str, Any],
    cycle_id: str,
    phase: str,
    rows: list[Mapping[str, Any]],
) -> bool:
    phases = profile.get("phases")
    latest = _latest_cycle_phase(pipeline, cycle_id)
    if isinstance(phases, list) and phase in phases and latest in phases:
        if phases.index(latest) > phases.index(phase):
            return True
    try:
        expected = set(promotion_exam_stage_candidate_refs(pipeline, profile, cycle_id, phase))
    except CommandRejectedError as exc:
        raise OperationError(503, "promotion_exam_context_invalid") from exc
    evaluated = {
        str(row["candidate_ref"])
        for row in rows
        if isinstance(row.get("candidate_ref"), str)
    }
    return bool(expected) and expected.issubset(evaluated)


def _public_stage_results(
    operations: Any,
    pipeline: Mapping[str, Any],
    profile: Mapping[str, Any],
    cycle_id: str,
) -> Mapping[str, Any]:
    visibility = profile.get("result_visibility")
    if not isinstance(visibility, Mapping):
        return {
            "stages": {},
            "stage_summaries": {},
            "result_count": 0,
            "results_truncated": False,
            "projection_limit": _MAX_PUBLIC_RESULTS,
        }
    stages: dict[str, list[dict[str, Any]]] = {}
    summaries: dict[str, dict[str, int]] = {}
    total = 0
    emitted = 0
    truncated = False
    for phase in ("qualification", "field_evaluation"):
        if visibility.get(phase) != "public_after_settlement":
            continue
        try:
            rows = list(promotion_exam_evaluation_rows(pipeline, cycle_id, phase=phase))
        except CommandRejectedError as exc:
            raise OperationError(503, "promotion_exam_context_invalid") from exc
        if not _stage_is_settled(pipeline, profile, cycle_id, phase, rows):
            continue
        ordered = sorted(
            (row for row in rows if isinstance(row.get("candidate_ref"), str)),
            key=lambda row: str(row["candidate_ref"]),
        )
        total += len(ordered)
        pass_count = sum(1 for row in ordered if row.get("outcome") == "pass")
        fail_count = sum(1 for row in ordered if row.get("outcome") == "fail")
        summaries[phase] = {
            "candidate_count": len(ordered),
            "pass_count": pass_count,
            "fail_count": fail_count,
        }
        projected: list[dict[str, Any]] = []
        for row in ordered:
            if emitted >= _MAX_PUBLIC_RESULTS:
                truncated = True
                break
            candidate_ref = str(row["candidate_ref"])
            name, village = _public_identity(operations, candidate_ref)
            projected.append(
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
            emitted += 1
        stages[phase] = projected
    if total > emitted:
        truncated = True
    return {
        "stages": stages,
        "stage_summaries": summaries,
        "result_count": total,
        "results_truncated": truncated,
        "projection_limit": _MAX_PUBLIC_RESULTS,
    }


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
            handoff["cycle_registration_scope"] = "hosted_institutional_team_and_home_village_delegation_count"
            hosted = profile.get("hosted_exam")
            if isinstance(hosted, Mapping):
                handoff["exam_host_village"] = hosted.get("host_village")
                villages = hosted.get("participating_villages")
                handoff["exam_participating_villages"] = list(villages) if isinstance(villages, list) else []
            public = _public_stage_results(operations, pipeline, profile, cycle_id)
            handoff["public_stage_results"] = public["stages"]
            handoff["public_stage_result_summaries"] = public["stage_summaries"]
            handoff["public_stage_result_count"] = public["result_count"]
            handoff["public_stage_results_truncated"] = public["results_truncated"]
            handoff["public_stage_results_projection_limit"] = public["projection_limit"]
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
