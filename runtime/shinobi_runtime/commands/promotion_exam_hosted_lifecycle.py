"""Lifecycle settlement for hosted inter-village Chunin Exam delegations."""
from __future__ import annotations

import copy
import json
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache, _campaign_datetime, _json_bytes
from shinobi_runtime.commands.domains.time import TimeCommandsMixin
from shinobi_runtime.commands import promotion_exam_attendance as attendance
from shinobi_runtime.commands import promotion_exam_integrity as integrity
from shinobi_runtime.commands import promotion_exam_pacing as pacing
from shinobi_runtime.commands import promotion_exam_scheduler as scheduler
from shinobi_runtime.commands.promotion_exam_hosted_policy import (
    append_location,
    candidate_home_location,
    delegation_by_team,
    hosted_config,
    minimum_route_days,
)
from shinobi_runtime.sim.events import CampaignTime

_CAREER = "state/reg/shinobi-career-pipeline.json"
_INSTALLED = False


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


def _pipeline_after(repository: Any, writes: Mapping[str, bytes]) -> dict[str, Any]:
    raw = writes.get(_CAREER)
    try:
        value = json.loads(raw.decode("utf-8")) if isinstance(raw, (bytes, bytearray)) else copy.deepcopy(repository.read_json(_CAREER))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CommandRejectedError("shinobi_career_pipeline_invalid") from exc
    if not isinstance(value, dict) or value.get("schema") != "shinobi-career-pipeline" or not isinstance(value.get("history"), list):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    return value


def _foreign_registered(
    pipeline: Mapping[str, Any], profile: Mapping[str, Any], cycle_id: str
) -> dict[str, Mapping[str, Any]]:
    history = pipeline.get("history")
    if not isinstance(history, list):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    delegations = delegation_by_team(profile)
    result: dict[str, Mapping[str, Any]] = {}
    for row in history:
        if not (
            isinstance(row, Mapping)
            and row.get("kind") == "promotion_exam_registration"
            and row.get("cycle_id") == cycle_id
        ):
            continue
        delegation = delegations.get(str(row.get("team_ref")))
        refs = row.get("candidate_refs")
        if delegation is None:
            continue
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref for ref in refs):
            raise CommandRejectedError("shinobi_career_pipeline_invalid")
        for ref in refs:
            result[ref] = delegation
    return result


def _install_hosted_arrivals() -> None:
    original = TimeCommandsMixin._advance_time
    if getattr(original, "_promotion_exam_hosted_arrivals", False):
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
        cache = _OwnerResolutionCache()
        staged: dict[str, dict[str, Any]] = {}
        arrivals: list[dict[str, Any]] = []
        for cycle in scheduler.active_promotion_exam_cycles(pipeline, profiles):
            if cycle.get("phase") not in ("qualification", "field_evaluation", "finals"):
                continue
            cycle_id = cycle.get("cycle_id")
            profile = by_id.get(cycle.get("profile_ref"))
            if not isinstance(cycle_id, str) or not isinstance(profile, Mapping):
                continue
            config = hosted_config(profile)
            if config is None:
                continue
            host_place = str(config["host_arrival_place_ref"])
            host_anchor = attendance._place_anchor(self.repository, host_place)
            schedule = pacing.promotion_exam_schedule_for_cycle(self.repository, cycle_id=cycle_id)
            registration_raw = schedule.get("registration")
            if not isinstance(registration_raw, str):
                raise CommandRejectedError("promotion_exam_cycle_state_invalid")
            try:
                registration_at = CampaignTime.parse(registration_raw)
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("promotion_exam_cycle_state_invalid") from exc
            elapsed_days = (_campaign_datetime(reached) - _campaign_datetime(registration_at)).total_seconds() / 86400.0
            for candidate_ref, delegation in _foreign_registered(pipeline, profile, cycle_id).items():
                try:
                    path, _digest, view = self._resolve_covered_owner_view(candidate_ref, cache=cache)
                except CommandRejectedError as exc:
                    raise CommandRejectedError("promotion_exam_candidate_unresolved") from exc
                subject = staged.get(path)
                if subject is None:
                    raw = base.writes.get(path)
                    subject = json.loads(raw.decode("utf-8")) if raw is not None else copy.deepcopy(dict(view)) if isinstance(view, Mapping) else None
                if not isinstance(subject, dict):
                    continue
                current_location = subject.get("current_location_id")
                if not isinstance(current_location, str) or not current_location:
                    raise CommandRejectedError("promotion_exam_candidate_location_invalid")
                if attendance._place_anchor(self.repository, current_location) == host_anchor:
                    continue
                route_days = minimum_route_days(self.repository, current_location, host_place)
                if route_days is None:
                    raise CommandRejectedError("promotion_exam_delegation_host_route_unavailable")
                if route_days > elapsed_days:
                    raise CommandRejectedError("promotion_exam_delegation_cannot_arrive_by_stage")
                append_location(
                    subject,
                    at=reached,
                    location_ref=host_place,
                    reason=f"scheduled hosted Chunin Examination delegation arrival from {delegation['service_village']}",
                )
                staged[path] = subject
                arrivals.append(
                    {
                        "candidate_ref": candidate_ref,
                        "from_location_ref": current_location,
                        "to_location_ref": host_place,
                        "minimum_route_days": route_days,
                    }
                )
        if not arrivals:
            return base
        world_events = self._world_events_after(base)
        event_id = self._append_internal_event(
            world_events,
            command=command,
            identity=f"promotion-exam-hosted-arrival:{reached}",
            kind="promotion_exam_hosted_delegations_arrived",
            at=reached,
            host_refs=tuple(sorted({row["to_location_ref"] for row in arrivals})),
            actor_refs=tuple(row["candidate_ref"] for row in arrivals),
            affected_owner_refs=tuple(sorted(staged)),
            material_consequence_refs=tuple(
                f"hosted_exam_arrival:{row['candidate_ref']}:{row['to_location_ref']}" for row in arrivals
            ),
            classification="public",
            audience_refs=(command.actor_id,),
            source_refs=tuple(sorted({row["to_location_ref"] for row in arrivals})),
            reducer_ref="shinobi_runtime.commands.promotion_exam_hosted_lifecycle",
        )
        writes = dict(base.writes)
        writes.update({path: _json_bytes(record) for path, record in staged.items()})
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))
        base_writes = dict(base.writes)
        original_validator = base.validator
        expected_records = copy.deepcopy(staged)

        def validate(overlay: Any, manifest: Any) -> None:
            if original_validator is not None:
                original_validator(_BaseOverlay(overlay, base_writes), manifest)
            if overlay.changed_paths != expected_paths:
                raise ValueError("hosted promotion exam arrival write set changed after planning")
            for path, expected in expected_records.items():
                if overlay.read_json(path) != expected:
                    raise ValueError("hosted promotion exam arrival after-image differs from plan")

        result = dict(base.result)
        result["promotion_exam_hosted_delegation_arrivals"] = arrivals
        result["promotion_exam_hosted_delegation_arrival_event_id"] = event_id
        return _BuiltPlan(code=base.code, affected_refs=expected_paths, writes=writes, result=result, validator=validate)

    wrapped._promotion_exam_hosted_arrivals = True  # type: ignore[attr-defined]
    TimeCommandsMixin._advance_time = wrapped


def _install_repair_travel() -> None:
    from shinobi_runtime.commands import campaign_environment as campaign_module

    planner = campaign_module.CampaignCommandPlanner
    name = "_campaign_promotion_exam_participation_repair"
    original = getattr(planner, name, None)
    if original is None or getattr(original, "_promotion_exam_hosted_repair_travel", False):
        return

    @wraps(original)
    def wrapped(self: Any, command: Any, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        base = original(self, command, meta, current_time)
        result = base.result if isinstance(base.result, Mapping) else {}
        cycle_id = result.get("cycle_id")
        added = result.get("registered_candidate_refs")
        finalists = result.get("finals_candidate_refs")
        evidence = result.get("reconciled_evidence_times")
        if not isinstance(cycle_id, str) or not isinstance(added, list) or not isinstance(finalists, list) or not isinstance(evidence, Mapping):
            return base
        pipeline = _pipeline_after(self.repository, base.writes)
        profiles = scheduler.promotion_exam_profiles(self.repository)
        cycle = next((row for row in scheduler.active_promotion_exam_cycles(pipeline, profiles) if row.get("cycle_id") == cycle_id), None)
        if not isinstance(cycle, Mapping):
            return base
        profile = scheduler._profile_for_cycle(profiles, cycle)
        config = hosted_config(profile)
        if config is None:
            return base
        schedule = pacing.promotion_exam_schedule_for_cycle(self.repository, cycle_id=cycle_id)
        qualification_raw = evidence.get("qualification")
        finals_raw = schedule.get("finals")
        if not isinstance(qualification_raw, str) or not isinstance(finals_raw, str):
            raise CommandRejectedError("promotion_exam_cycle_state_invalid")
        try:
            qualification_at = CampaignTime.parse(qualification_raw)
            finals_at = CampaignTime.parse(finals_raw)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("promotion_exam_cycle_state_invalid") from exc
        team_by_candidate, _instructors = integrity._registration_team_map(pipeline, cycle_id)
        delegation_map = delegation_by_team(profile)
        finalist_set = {ref for ref in finalists if isinstance(ref, str)}
        host_place = str(config["host_arrival_place_ref"])
        cache = _OwnerResolutionCache()
        staged: dict[str, dict[str, Any]] = {}
        movements: list[dict[str, Any]] = []
        for candidate_ref in (ref for ref in added if isinstance(ref, str)):
            delegation = delegation_map.get(team_by_candidate.get(candidate_ref, ""))
            if delegation is None:
                continue
            try:
                path, _digest, view = self._resolve_covered_owner_view(candidate_ref, cache=cache)
            except CommandRejectedError as exc:
                raise CommandRejectedError("promotion_exam_candidate_unresolved") from exc
            if not isinstance(view, Mapping):
                continue
            subject = copy.deepcopy(dict(view))
            home = candidate_home_location(subject)
            if not isinstance(home, str):
                raise CommandRejectedError("promotion_exam_candidate_location_invalid")
            route_days = minimum_route_days(self.repository, home, host_place)
            if route_days is None:
                raise CommandRejectedError("promotion_exam_delegation_host_route_unavailable")
            append_location(
                subject,
                at=qualification_at,
                location_ref=host_place,
                reason=f"reconciled hosted Chunin Examination delegation arrival from {delegation['service_village']}",
            )
            remains = candidate_ref in finalist_set
            if not remains:
                append_location(
                    subject,
                    at=finals_at,
                    location_ref=home,
                    reason="reconciled return home after elimination from hosted Chunin Examination",
                )
            staged[path] = subject
            movements.append(
                {
                    "candidate_ref": candidate_ref,
                    "home_location_ref": home,
                    "host_location_ref": host_place,
                    "minimum_route_days": route_days,
                    "remains_at_host_for_finals": remains,
                }
            )
        if not staged:
            return base
        world_events = self._world_events_after(base)
        event_id = self._append_internal_event(
            world_events,
            command=command,
            identity=f"{cycle_id}:hosted-delegation-travel-repair",
            kind="promotion_exam_hosted_delegation_travel_reconciled",
            at=current_time,
            host_refs=(str(profile["institution_ref"]), cycle_id),
            actor_refs=tuple(row["candidate_ref"] for row in movements),
            affected_owner_refs=tuple(sorted(staged)),
            material_consequence_refs=tuple(
                f"hosted_exam_travel_reconciled:{row['candidate_ref']}:{row['host_location_ref']}" for row in movements
            ),
            classification="restricted",
            audience_refs=(command.actor_id,),
            source_refs=(cycle_id,),
            reducer_ref="shinobi_runtime.commands.promotion_exam_hosted_lifecycle",
        )
        writes = dict(base.writes)
        writes.update({path: _json_bytes(record) for path, record in staged.items()})
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))
        base_writes = dict(base.writes)
        original_validator = base.validator
        expected_records = copy.deepcopy(staged)

        def validate(overlay: Any, manifest: Any) -> None:
            if original_validator is not None:
                original_validator(_BaseOverlay(overlay, base_writes), manifest)
            if overlay.changed_paths != expected_paths:
                raise ValueError("hosted promotion exam repair write set changed after planning")
            for path, expected in expected_records.items():
                if overlay.read_json(path) != expected:
                    raise ValueError("hosted promotion exam repair after-image differs from plan")

        enriched = dict(base.result)
        enriched["hosted_delegation_travel_reconciliation"] = movements
        enriched["hosted_delegation_travel_event_id"] = event_id
        return _BuiltPlan(code=base.code, affected_refs=expected_paths, writes=writes, result=enriched, validator=validate)

    wrapped._promotion_exam_hosted_repair_travel = True  # type: ignore[attr-defined]
    setattr(planner, name, wrapped)


def install_promotion_exam_hosted_lifecycle() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_hosted_arrivals()
    _install_repair_travel()
    _INSTALLED = True


__all__ = ["install_promotion_exam_hosted_lifecycle"]
