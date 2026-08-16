"""Scheduled local attendance for non-player exact Chunin Exam finalists."""
from __future__ import annotations

import copy
import json
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache, _json_bytes
from shinobi_runtime.commands.domains.time import TimeCommandsMixin
from shinobi_runtime.commands.paths import ROUTES_PATH
from shinobi_runtime.commands import promotion_exam_finals as finals
from shinobi_runtime.commands import promotion_exam_scheduler as scheduler
from shinobi_runtime.commands import promotion_exam_integrity as integrity
from shinobi_runtime.sim.events import CampaignTime

_INSTALLED = False
_CAREER = "state/reg/shinobi-career-pipeline.json"
_MAX_ATTENDEES = 64


def _place_anchor(repository: Any, location_ref: str) -> str:
    try:
        world = repository.read_json(ROUTES_PATH)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("promotion_exam_attendance_route_invalid") from exc
    payload = world.get("payload") if isinstance(world, Mapping) else None
    places = payload.get("places") if isinstance(payload, Mapping) else None
    if not isinstance(places, list) or len(places) > 4096:
        raise CommandRejectedError("promotion_exam_attendance_route_invalid")
    matches = [row for row in places if isinstance(row, Mapping) and row.get("id") == location_ref]
    if len(matches) != 1:
        raise CommandRejectedError("promotion_exam_attendance_route_invalid")
    anchor = matches[0].get("route_anchor_ref")
    return anchor if isinstance(anchor, str) and anchor else location_ref


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
    if not isinstance(value, dict) or value.get("schema") != "shinobi-career-pipeline" or not isinstance(value.get("history"), list):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    return value


def stage_npc_finalists(
    self: Any,
    *,
    pipeline: Mapping[str, Any],
    profile: Mapping[str, Any],
    cycle_id: str,
    at: CampaignTime,
    player_id: str,
    record_writes: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    config = profile.get("finals_format")
    venue_ref = config.get("venue_ref") if isinstance(config, Mapping) else None
    if not isinstance(venue_ref, str) or not venue_ref:
        raise CommandRejectedError("promotion_exam_finals_rules_invalid")
    venue_anchor = _place_anchor(self.repository, venue_ref)
    _team_by_candidate, instructor_by_candidate = integrity._registration_team_map(pipeline, cycle_id)
    candidate_refs = finals.promotion_exam_finals_candidate_refs(pipeline, cycle_id)
    cache = _OwnerResolutionCache()
    staged: list[dict[str, Any]] = []
    for candidate_ref in candidate_refs:
        if instructor_by_candidate.get(candidate_ref) == player_id:
            continue
        try:
            path, _digest, view = self._resolve_covered_owner_view(candidate_ref, cache=cache)
        except CommandRejectedError as exc:
            raise CommandRejectedError("promotion_exam_candidate_unresolved") from exc
        subject = record_writes.get(path)
        if subject is None:
            subject = copy.deepcopy(dict(view)) if isinstance(view, Mapping) else None
        if not isinstance(subject, dict) or subject.get("life_status") not in ("active", "alive"):
            continue
        condition = subject.get("condition")
        if isinstance(condition, Mapping) and condition.get("readiness") not in (None, "ready"):
            continue
        current_location = subject.get("current_location_id")
        if not isinstance(current_location, str) or not current_location:
            raise CommandRejectedError("promotion_exam_candidate_location_invalid")
        if current_location == venue_ref:
            continue
        if _place_anchor(self.repository, current_location) != venue_anchor:
            raise CommandRejectedError("promotion_exam_finalist_not_locally_reachable")
        subject["current_location_id"] = venue_ref
        life = subject.get("life_course_state")
        if not isinstance(life, dict):
            raise CommandRejectedError("promotion_exam_candidate_location_invalid")
        history = life.get("location_history")
        changes = life.get("location_changes", 0)
        if not isinstance(history, list) or isinstance(changes, bool) or not isinstance(changes, int) or changes < 0:
            raise CommandRejectedError("promotion_exam_candidate_location_invalid")
        history.append(
            {
                "at": str(at),
                "location_id": venue_ref,
                "reason": "scheduled local attendance for Chunin Examination finals",
            }
        )
        if len(history) > 128:
            del history[:-128]
        life["location_changes"] = changes + 1
        record_writes[path] = subject
        staged.append(
            {
                "candidate_ref": candidate_ref,
                "from_location_ref": current_location,
                "to_location_ref": venue_ref,
                "path": path,
            }
        )
        if len(staged) > _MAX_ATTENDEES:
            raise CommandRejectedError("promotion_exam_attendance_limit")
    return staged


class _BaseOverlay:
    def __init__(self, overlay: Any, base_writes: Mapping[str, bytes]) -> None:
        self._overlay = overlay
        self._base = dict(base_writes)
        self.changed_paths = tuple(sorted(base_writes))

    def read_json(self, path: str) -> Any:
        raw = self._base.get(path)
        if raw is not None:
            return json.loads(raw.decode("utf-8"))
        return self._overlay.read_json(path)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._overlay, name)


def install_promotion_exam_attendance() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original = TimeCommandsMixin._advance_time
    if getattr(original, "_promotion_exam_attendance", False):
        _INSTALLED = True
        return

    @wraps(original)
    def wrapped(self: Any, command: Any, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        base = original(self, command, meta, current_time)
        reached_raw = base.result.get("world_time") if isinstance(base.result, Mapping) else None
        if not isinstance(reached_raw, str):
            return base
        try:
            reached = CampaignTime.parse(reached_raw)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("promotion_exam_cycle_state_invalid") from exc
        pipeline = _pipeline_after(self.repository, base.writes)
        profiles = scheduler.promotion_exam_profiles(self.repository)
        by_id = {row.get("id"): row for row in profiles if isinstance(row.get("id"), str)}
        record_writes: dict[str, dict[str, Any]] = {}
        attendance: list[dict[str, Any]] = []
        for cycle in scheduler.active_promotion_exam_cycles(pipeline, profiles):
            cycle_id = cycle.get("cycle_id")
            profile = by_id.get(cycle.get("profile_ref"))
            if cycle.get("phase") != "finals" or not isinstance(cycle_id, str) or not isinstance(profile, Mapping):
                continue
            attendance.extend(
                stage_npc_finalists(
                    self,
                    pipeline=pipeline,
                    profile=profile,
                    cycle_id=cycle_id,
                    at=reached,
                    player_id=command.actor_id,
                    record_writes=record_writes,
                )
            )
        if not attendance:
            return base
        world_events = self._world_events_after(base)
        event_id = self._append_internal_event(
            world_events,
            command=command,
            identity=f"promotion-exam-attendance:{reached}",
            kind="promotion_exam_finalists_attended",
            at=reached,
            host_refs=tuple(sorted({row["to_location_ref"] for row in attendance})),
            actor_refs=tuple(row["candidate_ref"] for row in attendance),
            affected_owner_refs=tuple(sorted(record_writes)),
            material_consequence_refs=tuple(
                f"promotion_exam_attendance:{row['candidate_ref']}:{row['to_location_ref']}" for row in attendance
            ),
            classification="public",
            audience_refs=(command.actor_id,),
            source_refs=tuple(sorted({row["to_location_ref"] for row in attendance})),
            reducer_ref="shinobi_runtime.commands.promotion_exam_attendance",
        )
        writes = dict(base.writes)
        writes.update({path: _json_bytes(record) for path, record in record_writes.items()})
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))
        base_writes = dict(base.writes)
        original_validator = base.validator
        expected_records = copy.deepcopy(record_writes)

        def validate(overlay: Any, manifest: Any) -> None:
            if original_validator is not None:
                original_validator(_BaseOverlay(overlay, base_writes), manifest)
            if overlay.changed_paths != expected_paths:
                raise ValueError("promotion exam attendance write set changed after planning")
            for path, expected in expected_records.items():
                if overlay.read_json(path) != expected:
                    raise ValueError("promotion exam attendance after-image differs from plan")

        result = dict(base.result)
        result["promotion_exam_npc_finals_attendance"] = attendance
        result["promotion_exam_npc_finals_attendance_event_id"] = event_id
        return _BuiltPlan(
            code=base.code,
            affected_refs=expected_paths,
            writes=writes,
            result=result,
            validator=validate,
        )

    wrapped._promotion_exam_attendance = True  # type: ignore[attr-defined]
    TimeCommandsMixin._advance_time = wrapped
    _INSTALLED = True


__all__ = ["install_promotion_exam_attendance", "stage_npc_finalists"]
