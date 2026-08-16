"""Cardinality-safe retention for the shared shinobi career pipeline.

The career pipeline is authoritative current administration state, not a complete
lifetime archive. Historical material consequences are separately persisted as
semantic world events. Legacy producers used fixed 512-row truncation directly
inside reducers; a sufficiently large active promotion exam could therefore
truncate its own registration/evaluation/bout evidence and become false.

This extension disables those inline finite trims and applies one owner-level
retention policy after planning: every row belonging to every still-active exam
cycle is preserved regardless of cardinality, while only non-active history is
bounded. When a cycle is closed its durable material history remains available
through semantic event archives, so the hot career owner can compact old rows
without making current exam truth depend on a list-size ceiling.
"""
from __future__ import annotations

import copy
import json
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes

_CAREER = "state/reg/shinobi-career-pipeline.json"
_NON_ACTIVE_HISTORY_LIMIT = 512
_INSTALLED = False


def active_exam_cycle_ids(history: list[Any]) -> set[str]:
    latest_phase: dict[str, str] = {}
    for row in history:
        if not isinstance(row, Mapping):
            raise CommandRejectedError("shinobi_career_pipeline_invalid")
        if row.get("kind") != "promotion_exam_cycle_phase":
            continue
        cycle_id = row.get("cycle_id")
        phase = row.get("phase")
        if not isinstance(cycle_id, str) or not cycle_id or not isinstance(phase, str) or not phase:
            raise CommandRejectedError("shinobi_career_pipeline_invalid")
        latest_phase[cycle_id] = phase
    return {
        cycle_id
        for cycle_id, phase in latest_phase.items()
        if phase != "closed"
    }


def compact_career_history(
    history: list[Any],
    *,
    non_active_limit: int = _NON_ACTIVE_HISTORY_LIMIT,
) -> int:
    if (
        isinstance(non_active_limit, bool)
        or not isinstance(non_active_limit, int)
        or non_active_limit < 0
    ):
        raise ValueError("non-active history limit must be a non-negative integer")
    active_cycles = active_exam_cycle_ids(history)
    protected_indices: set[int] = set()
    non_active_indices: list[int] = []
    for index, row in enumerate(history):
        cycle_id = row.get("cycle_id") if isinstance(row, Mapping) else None
        if isinstance(cycle_id, str) and cycle_id in active_cycles:
            protected_indices.add(index)
        else:
            non_active_indices.append(index)
    retained_non_active = set(
        non_active_indices[-non_active_limit:]
        if non_active_limit
        else ()
    )
    keep = protected_indices | retained_non_active
    if len(keep) == len(history):
        return 0
    original_count = len(history)
    history[:] = [row for index, row in enumerate(history) if index in keep]
    return original_count - len(history)


def compact_career_pipeline(
    pipeline: Mapping[str, Any],
    *,
    non_active_limit: int = _NON_ACTIVE_HISTORY_LIMIT,
) -> tuple[dict[str, Any], int]:
    if (
        not isinstance(pipeline, Mapping)
        or pipeline.get("schema") != "shinobi-career-pipeline"
        or pipeline.get("version") != 1
    ):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    result = copy.deepcopy(dict(pipeline))
    history = result.get("history")
    if not isinstance(history, list):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    removed = compact_career_history(history, non_active_limit=non_active_limit)
    return result, removed


class _OriginalCareerOverlay:
    """Present a reducer's planned career image to its original validator."""

    def __init__(self, overlay: Any, *, changed_paths: tuple[str, ...], career: Mapping[str, Any]) -> None:
        self._overlay = overlay
        self.changed_paths = changed_paths
        self._career = copy.deepcopy(dict(career))

    def read_json(self, path: str) -> Any:
        if path == _CAREER:
            return copy.deepcopy(self._career)
        return self._overlay.read_json(path)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._overlay, name)


def _disable_legacy_inline_caps() -> None:
    # These constants are only legacy hot-owner truncation controls. Setting
    # them to infinity prevents deletion before the final owner-level retention
    # pass below. The retained current truth has no finite active-cycle ceiling.
    infinity = float("inf")
    from shinobi_runtime.commands import promotion_exam_scheduler as scheduler
    from shinobi_runtime.commands import promotion_exam_evaluation as evaluation
    from shinobi_runtime.commands import promotion_exam_finals as finals
    from shinobi_runtime.commands import promotion_exam_pacing as pacing
    from shinobi_runtime.commands import shinobi_career_progression as career

    scheduler._CURSOR = infinity
    evaluation._CURSOR = infinity
    finals._CURSOR = infinity
    pacing._MAX_HISTORY = infinity
    career._MAX_HISTORY = infinity


def install_career_history_retention() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _disable_legacy_inline_caps()

    from shinobi_runtime.commands import planner as planner_module

    planner = planner_module.RepositoryCommandPlanner
    original = planner._with_routing_projections
    if getattr(original, "_career_history_retention", False):
        _INSTALLED = True
        return

    @wraps(original)
    def wrapped(self: Any, built: _BuiltPlan) -> _BuiltPlan:
        planned = original(self, built)
        raw = planned.writes.get(_CAREER)
        if raw is None:
            return planned
        try:
            original_career = json.loads(raw.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CommandRejectedError("shinobi_career_pipeline_invalid") from exc
        compacted, removed = compact_career_pipeline(original_career)
        if removed == 0:
            return planned

        writes = dict(planned.writes)
        writes[_CAREER] = _json_bytes(compacted)
        expected_paths = tuple(sorted(writes))
        original_validator = planned.validator
        original_paths = tuple(planned.affected_refs)
        expected_career = copy.deepcopy(compacted)

        def validate(overlay: Any, manifest: Any) -> None:
            if original_validator is not None:
                original_validator(
                    _OriginalCareerOverlay(
                        overlay,
                        changed_paths=original_paths,
                        career=original_career,
                    ),
                    manifest,
                )
            if overlay.changed_paths != expected_paths:
                raise ValueError("career history retention write set changed after planning")
            if overlay.read_json(_CAREER) != expected_career:
                raise ValueError("career history retention after-image differs from plan")

        result = dict(planned.result)
        result["career_history_retention"] = {
            "removed_non_active_rows": removed,
            "retained_active_exam_cycle_ids": sorted(
                active_exam_cycle_ids(expected_career["history"])
            ),
            "non_active_history_limit": _NON_ACTIVE_HISTORY_LIMIT,
        }
        return _BuiltPlan(
            code=planned.code,
            affected_refs=expected_paths,
            writes=writes,
            result=result,
            validator=validate,
        )

    wrapped._career_history_retention = True  # type: ignore[attr-defined]
    planner._with_routing_projections = wrapped
    _INSTALLED = True


__all__ = [
    "active_exam_cycle_ids",
    "compact_career_history",
    "compact_career_pipeline",
    "install_career_history_retention",
]
