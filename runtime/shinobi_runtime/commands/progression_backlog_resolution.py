"""Deterministic reconciliation for progression debt created by runtime defects.

This is a narrow semantic maintenance command, not an arbitrary state patch. It
settles only already-due causal progression through the current campaign clock
and replays Team Fujin's saved standing training policy from the authoritative
development-bank cursor. Campaign world time does not advance.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, Mapping, Optional

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _campaign_datetime, _json_bytes
from shinobi_runtime.commands.domains.time import TimeCommandsMixin
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.mission_owner import MissionOwner
from shinobi_runtime.commands.paths import DEVELOPMENT_BANK_PATH, PERSON_CONTINUITY_PATH
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry
from shinobi_runtime.sim.scheduler_store import SchedulerStore

_COMMAND = "progression_backlog_resolution"
_CAREER_PATH = "state/reg/shinobi-career-pipeline.json"
_FUJIN_REF = "team.konoha.fujin"
_FUJIN_HOST = "host.team.team.konoha.fujin"
_FUJIN_MEMBERS = ("pc_wei_tang", "char.kai", "char.riku_hyuga", "char.mei_arakawa")
_INSTALLED = False


class _WritesReader:
    def __init__(self, repository: Any, writes: Mapping[str, bytes]) -> None:
        self.repository = repository
        self.writes = writes

    def read_optional_bytes(self, path: str) -> Optional[bytes]:
        if path in self.writes:
            return self.writes[path]
        return self.repository.read_optional_bytes(path)

    def read_json(self, path: str) -> Any:
        raw = self.read_optional_bytes(path)
        if raw is None:
            raise FileNotFoundError(path)
        return json.loads(raw.decode("utf-8"))


def _decode_write(writes: Mapping[str, bytes], path: str) -> Optional[Dict[str, Any]]:
    raw = writes.get(path)
    if raw is None:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CommandRejectedError("progression_backlog_after_image_invalid") from exc
    if not isinstance(value, dict):
        raise CommandRejectedError("progression_backlog_after_image_invalid")
    return value


def _historical_scheduler(
    planner: Any,
    scheduler: CausalSchedulerRegistry,
    *,
    at: CampaignTime,
) -> CausalSchedulerRegistry:
    """Hide missions that had not started at the historical training boundary."""
    clone = CausalSchedulerRegistry.from_record(scheduler.to_record())
    remove_hosts: set[str] = set()
    for host_id, wrapper in list(clone.hosts.items()):
        if getattr(wrapper, "authority_kind", None) != "mission":
            continue
        owner_path = getattr(wrapper, "owner_ref", None)
        if not isinstance(owner_path, str) or not owner_path:
            continue
        try:
            owner = MissionOwner.from_record(planner.repository.read_json(owner_path))
        except (FileNotFoundError, TypeError, ValueError):
            continue
        starts_at = owner.starts_at
        if starts_at is not None and starts_at > at:
            remove_hosts.add(host_id)
    if remove_hosts:
        for host_id in remove_hosts:
            clone.hosts.pop(host_id, None)
        clone.queue.replace(
            event for event in clone.queue.snapshot()
            if event.target_host not in remove_hosts and event.source_host not in remove_hosts
        )
    return clone


def _fujin_training_boundary(
    scheduler: CausalSchedulerRegistry,
    *,
    current_time: CampaignTime,
) -> tuple[CampaignTime, int]:
    matches = [
        event for event in scheduler.queue.snapshot()
        if event.target_host == _FUJIN_HOST and event.kind == "team.periodic_review"
    ]
    if len(matches) != 1:
        raise CommandRejectedError("progression_backlog_fujin_schedule_invalid")
    event = matches[0]
    recurrence = event.payload.get("recurrence") if isinstance(event.payload, Mapping) else None
    interval = recurrence.get("interval_seconds") if isinstance(recurrence, Mapping) else None
    if (
        recurrence.get("kind") != "fixed_interval"
        or isinstance(interval, bool)
        or not isinstance(interval, int)
        or interval <= 0
    ):
        raise CommandRejectedError("progression_backlog_fujin_schedule_invalid")
    boundary = event.due_at.add_seconds(-interval)
    if boundary > current_time:
        raise CommandRejectedError("progression_backlog_fujin_schedule_invalid")
    return boundary, interval


def _repair_fujin_training(
    planner: Any,
    *,
    command: CommandEnvelope,
    current_time: CampaignTime,
    scheduler: CausalSchedulerRegistry,
    world_events: Dict[str, Any],
    record_writes: Dict[str, Dict[str, Any]],
) -> Mapping[str, Any]:
    try:
        banks = copy.deepcopy(planner.repository.read_json(DEVELOPMENT_BANK_PATH))
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("development_bank_invalid") from exc
    entries = banks.get("entries") if isinstance(banks, dict) else None
    if not isinstance(entries, dict):
        raise CommandRejectedError("development_bank_invalid")

    cursors: Dict[str, CampaignTime] = {}
    for member_ref in _FUJIN_MEMBERS:
        entry = entries.get(member_ref)
        if not isinstance(entry, Mapping) or entry.get("owner_type") != "character":
            raise CommandRejectedError("progression_backlog_fujin_cursor_invalid")
        try:
            cursors[member_ref] = CampaignTime.parse(entry.get("resolved_through"))
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("progression_backlog_fujin_cursor_invalid") from exc

    boundary, interval_seconds = _fujin_training_boundary(scheduler, current_time=current_time)
    max_cursor = max(cursors.values())
    elapsed_seconds = int(
        (_campaign_datetime(boundary) - _campaign_datetime(max_cursor)).total_seconds()
    )
    reviews = elapsed_seconds // interval_seconds
    if reviews <= 0:
        return {
            "status": "already_settled",
            "through": str(max_cursor),
            "training_boundary": str(boundary),
        }
    interval_start = boundary.add_seconds(-reviews * interval_seconds)
    if any(cursor > interval_start for cursor in cursors.values()):
        raise CommandRejectedError("progression_backlog_fujin_overlap_detected")

    team_path, team_view = planner._exact_team(_FUJIN_REF)
    if not isinstance(team_view, Mapping):
        raise CommandRejectedError("progression_backlog_fujin_team_invalid")
    team = copy.deepcopy(dict(team_view))
    if tuple(team.get("member_refs", ())) != _FUJIN_MEMBERS:
        raise CommandRejectedError("progression_backlog_fujin_roster_changed")

    original_locations: Dict[str, str] = {}
    member_paths: Dict[str, str] = {}
    for member_ref in _FUJIN_MEMBERS:
        try:
            path, _digest, view = planner._resolve_covered_owner_view(
                member_ref, cache=planner._owner_resolution_cache_for_backlog()
            )
        except AttributeError:
            from shinobi_runtime.commands.core import _OwnerResolutionCache
            path, _digest, view = planner._resolve_covered_owner_view(
                member_ref, cache=_OwnerResolutionCache()
            )
        if not isinstance(view, Mapping):
            raise CommandRejectedError("progression_backlog_fujin_member_invalid")
        location = view.get("current_location_id")
        if not isinstance(location, str) or not location:
            raise CommandRejectedError("progression_backlog_fujin_member_invalid")
        original_locations[member_ref] = location
        member_paths[member_ref] = path

    historical_scheduler = _historical_scheduler(planner, scheduler, at=boundary)
    result = planner._apply_autonomous_team_training(
        team=team,
        owner_ref=team_path,
        at=boundary,
        compacted=reviews,
        command=command,
        scheduler=historical_scheduler,
        policy_book=planner._autonomy_policy_book(),
        world_events=world_events,
        record_writes=record_writes,
    )
    if not isinstance(result, Mapping) or result.get("skipped"):
        raise CommandRejectedError("progression_backlog_fujin_training_unavailable")
    outcomes = result.get("outcomes")
    if not isinstance(outcomes, Mapping) or set(outcomes) != set(_FUJIN_MEMBERS):
        raise CommandRejectedError("progression_backlog_fujin_training_incomplete")

    # Standing assembly proves historical co-location, but a maintenance repair
    # must not teleport the present-day party. Preserve the current locations;
    # the appended historical location entry remains provenance for the session.
    for member_ref, path in member_paths.items():
        record = record_writes.get(path)
        if not isinstance(record, dict):
            raise CommandRejectedError("progression_backlog_fujin_training_incomplete")
        record["current_location_id"] = original_locations[member_ref]

    staged_banks = record_writes.get(DEVELOPMENT_BANK_PATH)
    staged_entries = staged_banks.get("entries") if isinstance(staged_banks, Mapping) else None
    if not isinstance(staged_entries, Mapping):
        raise CommandRejectedError("progression_backlog_fujin_training_incomplete")
    for member_ref in _FUJIN_MEMBERS:
        entry = staged_entries.get(member_ref)
        if not isinstance(entry, Mapping) or entry.get("resolved_through") != str(boundary):
            raise CommandRejectedError("progression_backlog_fujin_training_incomplete")

    return {
        "status": "reconciled",
        "from_cursors": {key: str(value) for key, value in sorted(cursors.items())},
        "through": str(boundary),
        "compacted_reviews": reviews,
        "training": dict(result),
    }


def _progression_backlog_resolution(
    self: Any,
    command: CommandEnvelope,
    meta: Mapping[str, Any],
    current_time: CampaignTime,
) -> _BuiltPlan:
    if command.payload:
        raise CommandRejectedError("progression_backlog_resolution_payload_fields_invalid")

    # Reuse the production causal reducer to settle only already-due work. The
    # one-second surrogate exists solely because the public advance_time command
    # rejects an equal target; all campaign clocks are restored below.
    inner = CommandEnvelope(
        campaign_id=command.campaign_id,
        request_id=command.request_id + ".causal",
        actor_id=command.actor_id,
        command_type="advance_time",
        expected_revision=command.expected_revision,
        submitted_at=command.submitted_at,
        payload={"target_time": str(current_time.add_seconds(1))},
        mode=command.mode,
    )
    base = TimeCommandsMixin._advance_time(self, inner, meta, current_time)
    continuity_reviews = base.result.get("person_continuity_reviews")
    career_reviews = base.result.get("shinobi_career_reviews")
    has_causal_debt = bool(continuity_reviews) or bool(career_reviews)

    after_base = _WritesReader(self.repository, base.writes)
    try:
        repaired_scheduler = SchedulerStore(after_base, self.scheduler_path).load(full=True)
    except (FileNotFoundError, TypeError, ValueError) as exc:
        raise CommandRejectedError("progression_backlog_scheduler_invalid") from exc
    repaired_scheduler.world_time = current_time

    world_events = self._world_events_after(base)
    autonomous_writes: Dict[str, Dict[str, Any]] = {}
    fujin = _repair_fujin_training(
        self,
        command=command,
        current_time=current_time,
        scheduler=repaired_scheduler,
        world_events=world_events,
        record_writes=autonomous_writes,
    )
    has_fujin_debt = fujin.get("status") == "reconciled"
    if not has_causal_debt and not has_fujin_debt:
        raise CommandRejectedError("progression_backlog_already_settled")

    self._append_semantic_event(
        world_events,
        command=command,
        kind="progression_backlog_reconciled",
        at=current_time,
        host_refs=(_FUJIN_REF, "host.person_continuity"),
        actor_refs=(command.actor_id,),
        affected_owner_refs=tuple(
            sorted(
                set(autonomous_writes)
                | {PERSON_CONTINUITY_PATH, _CAREER_PATH, self.scheduler_path}
            )
        ),
        material_consequence_refs=(
            f"continuity_reviews:{len(continuity_reviews) if isinstance(continuity_reviews, list) else 0}",
            f"career_reviews:{len(career_reviews) if isinstance(career_reviews, list) else 0}",
            f"fujin_status:{fujin.get('status')}",
        ),
        classification="restricted",
        audience_refs=(command.actor_id,),
        reducer_ref="shinobi_runtime.commands.progression_backlog_resolution",
    )

    writes: Dict[str, bytes] = dict(base.writes)
    # Development maintenance must not alter the lived scene or campaign clock.
    writes.pop(self.scene_path, None)
    writes[self.meta_path] = _json_bytes(self._meta_after(meta, command, world_time=current_time))
    scheduler_root = _decode_write(writes, self.scheduler_path)
    if scheduler_root is None:
        raise CommandRejectedError("progression_backlog_scheduler_invalid")
    scheduler_root["world_time"] = str(current_time)
    writes[self.scheduler_path] = _json_bytes(scheduler_root)
    for path, record in autonomous_writes.items():
        writes[path] = _json_bytes(record)
    writes.update(self._world_event_writes(world_events))
    writes = self._prune_noop_writes(writes)
    expected_paths = tuple(sorted(writes))

    expected_json: Dict[str, Any] = {}
    for path, raw in writes.items():
        try:
            expected_json[path] = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CommandRejectedError("progression_backlog_after_image_invalid") from exc

    def validate(overlay: Any, manifest: Any) -> None:
        if overlay.changed_paths != expected_paths:
            raise ValueError("progression backlog write set changed after planning")
        staged_meta = overlay.read_json(self.meta_path)
        if (
            staged_meta.get("campaign_id") != command.campaign_id
            or staged_meta.get("revision") != command.expected_revision + 1
            or staged_meta.get("time") != str(current_time)
            or manifest.base_revision != command.expected_revision
            or manifest.target_revision != command.expected_revision + 1
        ):
            raise ValueError("progression backlog changed campaign clock or revision law")
        staged_scheduler = self._scheduler_from_reader(overlay)
        if staged_scheduler.world_time != current_time:
            raise ValueError("progression backlog changed scheduler world time")
        # Do not reject unrelated player-facing or mission boundaries that are
        # legitimately due at the current instant. This repair owns only stale
        # progression debt. The continuity host itself must now be safely in the
        # future, while other domains retain their independent causal authority.
        continuity_host = staged_scheduler.hosts.get("host.person_continuity")
        if continuity_host is None:
            raise ValueError("progression backlog lost person continuity host")
        continuity_due = continuity_host.state.next_due
        if continuity_due is not None and continuity_due <= current_time:
            raise ValueError("progression backlog left person continuity overdue")
        for path, expected in expected_json.items():
            if overlay.read_json(path) != expected:
                raise ValueError("progression backlog after-image differs from plan")
        if PERSON_CONTINUITY_PATH in expected_json:
            through = CampaignTime.parse(expected_json[PERSON_CONTINUITY_PATH].get("resolved_through"))
            if through > current_time:
                raise ValueError("person continuity advanced beyond campaign time")
        if _CAREER_PATH in expected_json:
            through = CampaignTime.parse(expected_json[_CAREER_PATH].get("last_review_at"))
            if through > current_time:
                raise ValueError("career pipeline advanced beyond campaign time")

    return _BuiltPlan(
        code="progression_backlog_resolution_ready",
        affected_refs=expected_paths,
        writes=writes,
        result={
            "command_type": command.command_type,
            "world_time_unchanged": str(current_time),
            "causal_reconciliation": {
                "person_continuity_reviews": continuity_reviews or [],
                "shinobi_career_reviews": career_reviews or [],
            },
            "team_fujin": fujin,
        },
        validator=validate,
    )


def install_progression_backlog_resolution() -> None:
    """Register the bounded maintenance command with the production planner."""
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands.planner import RepositoryCommandPlanner

    if _COMMAND not in COMMAND_SPECS:
        COMMAND_SPECS[_COMMAND] = CommandSpec(
            (),
            summary=(
                "Reconcile deterministic progression debt caused by a prior runtime defect "
                "without advancing campaign time."
            ),
            availability="maintenance_only_when_progression_debt_exists",
        )
    RepositoryCommandPlanner.COMMAND_TYPES = frozenset(COMMAND_SPECS)
    setattr(RepositoryCommandPlanner, "_" + _COMMAND, _progression_backlog_resolution)
    _INSTALLED = True


__all__ = ["install_progression_backlog_resolution"]