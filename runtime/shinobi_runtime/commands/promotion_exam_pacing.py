"""Canonical intra-cycle pacing for promotion examinations.

The Academy's ordinary institution review opens a configured exam cycle. Once
opened, the persisted registration timestamp plus authored phase offsets define
later boundaries. Those boundaries are settled only when canonical campaign
time crosses them, so exam pacing no longer inherits the Academy's unrelated
monthly bureaucracy cadence.
"""
from __future__ import annotations

import copy
import json
from functools import wraps
from typing import Any, Mapping, Optional

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.domains.time import TimeCommandsMixin
from shinobi_runtime.commands import promotion_exam_scheduler as scheduler_module
from shinobi_runtime.commands.promotion_exam_scheduler import (
    active_promotion_exam_cycles,
    next_cycle_phase as _legacy_next_cycle_phase,
    promotion_exam_profiles,
)
from shinobi_runtime.sim.events import CampaignTime

_CAREER = "state/reg/shinobi-career-pipeline.json"
_MAX_HISTORY = 512
_INSTALLED = False


class _BaseOverlay:
    def __init__(self, overlay: Any, base_writes: Mapping[str, bytes]) -> None:
        self._overlay = overlay
        self._base = dict(base_writes)
        self.changed_paths = tuple(sorted(base_writes))

    def read_json(self, path: str) -> Any:
        raw = self._base.get(path)
        if raw is None:
            return self._overlay.read_json(path)
        try:
            return json.loads(raw.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("base promotion-exam after-image is not JSON") from exc

    def __getattr__(self, name: str) -> Any:
        return getattr(self._overlay, name)


def _pipeline_after(repository: Any, writes: Mapping[str, bytes]) -> dict[str, Any]:
    raw = writes.get(_CAREER)
    try:
        value = (
            json.loads(raw.decode("utf-8"))
            if isinstance(raw, (bytes, bytearray))
            else copy.deepcopy(repository.read_json(_CAREER))
        )
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CommandRejectedError("shinobi_career_pipeline_invalid") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema") != "shinobi-career-pipeline"
        or value.get("version") != 1
        or not isinstance(value.get("history"), list)
    ):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    return value


def _phase_offsets(profile: Mapping[str, Any]) -> tuple[tuple[str, int], ...]:
    phases = profile.get("phases")
    offsets = profile.get("phase_offsets_days")
    if not isinstance(phases, list) or not phases or not isinstance(offsets, Mapping):
        raise CommandRejectedError("promotion_exam_rules_invalid")
    rows: list[tuple[str, int]] = []
    prior = -1
    for index, phase in enumerate(phases):
        value = offsets.get(phase) if isinstance(phase, str) else None
        if (
            not isinstance(phase, str)
            or not phase
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise CommandRejectedError("promotion_exam_rules_invalid")
        if index == 0 and value != 0:
            raise CommandRejectedError("promotion_exam_rules_invalid")
        if index > 0 and value <= prior:
            raise CommandRejectedError("promotion_exam_rules_invalid")
        rows.append((phase, value))
        prior = value
    if set(offsets) != set(phases):
        raise CommandRejectedError("promotion_exam_rules_invalid")
    return tuple(rows)


def _academy_cycle_opening_only(
    profile: Mapping[str, Any],
    pipeline: Mapping[str, Any],
    at: CampaignTime,
) -> Optional[tuple[str, str]]:
    """Let Academy review open a cycle, never advance an already-open cycle."""

    if active_promotion_exam_cycles(pipeline, (profile,)):
        return None
    return _legacy_next_cycle_phase(profile, pipeline, at)


def _cycle_anchor(
    pipeline: Mapping[str, Any],
    *,
    cycle_id: str,
    first_phase: str,
) -> CampaignTime:
    history = pipeline.get("history")
    if not isinstance(history, list):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    anchors = []
    for row in history:
        if (
            isinstance(row, Mapping)
            and row.get("kind") == "promotion_exam_cycle_phase"
            and row.get("cycle_id") == cycle_id
            and row.get("phase") == first_phase
            and isinstance(row.get("at"), str)
        ):
            try:
                anchors.append(CampaignTime.parse(row["at"]))
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("promotion_exam_cycle_state_invalid") from exc
    if len(anchors) != 1:
        raise CommandRejectedError("promotion_exam_cycle_state_invalid")
    return anchors[0]


def _next_phase_due(
    profile: Mapping[str, Any],
    pipeline: Mapping[str, Any],
    cycle: Mapping[str, Any],
) -> Optional[tuple[str, CampaignTime]]:
    cycle_id = cycle.get("cycle_id")
    current_phase = cycle.get("phase")
    if not isinstance(cycle_id, str) or not isinstance(current_phase, str):
        raise CommandRejectedError("promotion_exam_cycle_state_invalid")
    schedule = _phase_offsets(profile)
    phase_names = [phase for phase, _offset in schedule]
    if current_phase not in phase_names:
        raise CommandRejectedError("promotion_exam_cycle_state_invalid")
    index = phase_names.index(current_phase)
    if index + 1 >= len(schedule):
        return None
    anchor = _cycle_anchor(
        pipeline,
        cycle_id=cycle_id,
        first_phase=phase_names[0],
    )
    next_phase, offset_days = schedule[index + 1]
    return next_phase, anchor.add_seconds(offset_days * 24 * 60 * 60)


def promotion_exam_schedule_for_cycle(
    repository: Any,
    *,
    cycle_id: str,
) -> Mapping[str, str]:
    pipeline = _pipeline_after(repository, {})
    profiles = promotion_exam_profiles(repository)
    active = active_promotion_exam_cycles(pipeline, profiles)
    cycle = next((row for row in active if row.get("cycle_id") == cycle_id), None)
    if not isinstance(cycle, Mapping):
        raise CommandRejectedError("promotion_exam_cycle_not_active")
    profile_ref = cycle.get("profile_ref")
    profile = next((row for row in profiles if row.get("id") == profile_ref), None)
    if not isinstance(profile, Mapping):
        raise CommandRejectedError("promotion_exam_cycle_state_invalid")
    schedule = _phase_offsets(profile)
    anchor = _cycle_anchor(pipeline, cycle_id=cycle_id, first_phase=schedule[0][0])
    return {
        phase: str(anchor.add_seconds(offset_days * 24 * 60 * 60))
        for phase, offset_days in schedule
    }


def next_promotion_exam_boundary(
    repository: Any,
    current_time: CampaignTime,
) -> Optional[CampaignTime]:
    pipeline = _pipeline_after(repository, {})
    profiles = promotion_exam_profiles(repository)
    active = active_promotion_exam_cycles(pipeline, profiles)
    by_id = {
        row.get("id"): row
        for row in profiles
        if isinstance(row.get("id"), str)
    }
    due_points: list[CampaignTime] = []
    for cycle in active:
        profile = by_id.get(cycle.get("profile_ref"))
        if not isinstance(profile, Mapping):
            raise CommandRejectedError("promotion_exam_cycle_state_invalid")
        next_due = _next_phase_due(profile, pipeline, cycle)
        if next_due is None:
            continue
        _phase, due = next_due
        due_points.append(
            due if due > current_time else current_time.add_seconds(1)
        )
    return min(due_points) if due_points else None


def _install_academy_opening_only() -> None:
    current = scheduler_module.next_cycle_phase
    if getattr(current, "_promotion_exam_opening_only", False):
        return
    _academy_cycle_opening_only._promotion_exam_opening_only = True  # type: ignore[attr-defined]
    scheduler_module.next_cycle_phase = _academy_cycle_opening_only


def _install_time_progression() -> None:
    original = TimeCommandsMixin._advance_time
    if getattr(original, "_promotion_exam_phase_pacing", False):
        return

    @wraps(original)
    def wrapped(
        self: Any,
        command: Any,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        base = original(self, command, meta, current_time)
        reached_raw = (
            base.result.get("world_time")
            if isinstance(base.result, Mapping)
            else None
        )
        if not isinstance(reached_raw, str):
            return base
        try:
            reached = CampaignTime.parse(reached_raw)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("promotion_exam_cycle_state_invalid") from exc

        pipeline = _pipeline_after(self.repository, base.writes)
        profiles = promotion_exam_profiles(self.repository)
        profile_by_id = {
            row.get("id"): row
            for row in profiles
            if isinstance(row.get("id"), str)
        }
        raw_actions = base.result.get("autonomous_actions")
        actions = list(raw_actions) if isinstance(raw_actions, list) else []
        world_events: Optional[dict[str, Any]] = None
        changed = False

        while True:
            active = active_promotion_exam_cycles(pipeline, profiles)
            due_candidates: list[
                tuple[CampaignTime, Mapping[str, Any], Mapping[str, Any], str]
            ] = []
            for cycle in active:
                profile = profile_by_id.get(cycle.get("profile_ref"))
                if not isinstance(profile, Mapping):
                    raise CommandRejectedError("promotion_exam_cycle_state_invalid")
                next_due = _next_phase_due(profile, pipeline, cycle)
                if next_due is None:
                    continue
                next_phase, due = next_due
                if due <= reached:
                    due_candidates.append((due, cycle, profile, next_phase))
            if not due_candidates:
                break
            due, cycle, profile, next_phase = min(
                due_candidates,
                key=lambda row: (
                    row[0],
                    str(row[1].get("cycle_id")),
                    row[3],
                ),
            )
            cycle_id = cycle.get("cycle_id")
            profile_ref = profile.get("id")
            institution_ref = profile.get("institution_ref")
            authority_ref = profile.get("authority_ref")
            if not all(
                isinstance(value, str) and value
                for value in (
                    cycle_id,
                    profile_ref,
                    institution_ref,
                    authority_ref,
                )
            ):
                raise CommandRejectedError("promotion_exam_cycle_state_invalid")

            history = pipeline["history"]
            history.append(
                {
                    "kind": "promotion_exam_cycle_phase",
                    "at": str(due),
                    "cycle_id": cycle_id,
                    "profile_ref": profile_ref,
                    "phase": next_phase,
                    "canon_status": "campaign_institutional_not_future_canon",
                    "authority_ref": authority_ref,
                }
            )
            if len(history) > _MAX_HISTORY:
                del history[:-_MAX_HISTORY]

            if world_events is None:
                world_events = self._world_events_after(base)
            event_id = self._append_internal_event(
                world_events,
                command=command,
                identity=f"{cycle_id}:{next_phase}:{due}",
                kind="promotion_exam_cycle_phase_changed",
                at=due,
                host_refs=(institution_ref,),
                affected_owner_refs=(_CAREER,),
                material_consequence_refs=(
                    cycle_id,
                    f"phase:{next_phase}",
                ),
                classification="public",
                audience_refs=(command.actor_id,),
                source_refs=(institution_ref, authority_ref),
                reducer_ref="shinobi_runtime.commands.promotion_exam_pacing",
            )
            actions.append(
                {
                    "kind": "promotion_exam_phase_progression",
                    "promotion_exam_cycle": {
                        "cycle_id": cycle_id,
                        "profile_ref": profile_ref,
                        "phase": next_phase,
                        "at": str(due),
                        "institution_ref": institution_ref,
                        "event_id": event_id,
                        "public_institutional_event": True,
                    },
                }
            )
            changed = True

        if not changed:
            return base

        writes = dict(base.writes)
        writes[_CAREER] = _json_bytes(pipeline)
        if world_events is not None:
            writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))
        base_writes = dict(base.writes)
        original_validator = base.validator
        expected_pipeline = copy.deepcopy(pipeline)

        def validate(overlay: Any, manifest: Any) -> None:
            if original_validator is not None:
                original_validator(_BaseOverlay(overlay, base_writes), manifest)
            if overlay.changed_paths != expected_paths:
                raise ValueError(
                    "promotion exam pacing changed write set after planning"
                )
            if overlay.read_json(_CAREER) != expected_pipeline:
                raise ValueError(
                    "promotion exam pacing after-image differs from plan"
                )

        result = dict(base.result)
        result["autonomous_actions"] = actions
        result["promotion_exam_phase_progression"] = [
            dict(action["promotion_exam_cycle"])
            for action in actions
            if isinstance(action, Mapping)
            and action.get("kind") == "promotion_exam_phase_progression"
            and isinstance(action.get("promotion_exam_cycle"), Mapping)
        ]
        return _BuiltPlan(
            code=base.code,
            affected_refs=expected_paths,
            writes=writes,
            result=result,
            validator=validate,
        )

    wrapped._promotion_exam_phase_pacing = True  # type: ignore[attr-defined]
    TimeCommandsMixin._advance_time = wrapped


def install_promotion_exam_pacing() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_academy_opening_only()
    _install_time_progression()
    _INSTALLED = True


__all__ = [
    "install_promotion_exam_pacing",
    "next_promotion_exam_boundary",
    "promotion_exam_schedule_for_cycle",
]
