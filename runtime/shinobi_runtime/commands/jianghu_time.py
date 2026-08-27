"""Jianghu campaign-time command.

The campaign clock advances only through this reducer. It compares current
committed time with the compact Jianghu causal frontier, settles the earliest
due work chunk, and either reaches the requested target or returns a bounded
continuation boundary. No global per-person tick exists.

Before a frontier settles, the player and active standing-retinue members who
are physically away from their faction training site are staged as
institutionally paused. Safe lodging can then credit only the registered
self-practice window from the elapsed interval, without creating extra time or
remote faction instruction.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.martial_world.event_seeking import event_seeking_boundary_summary
from shinobi_runtime.martial_world.commitments import derived_commitment_state
from shinobi_runtime.martial_world.faction_state import read_faction, roster_path
from shinobi_runtime.martial_world.faction_registry import current_faction_refs
from shinobi_runtime.martial_world.live_state import person_route, roster_person, set_roster_person
from shinobi_runtime.martial_world.person_state import home_location_ref, hydrate_person_state
from shinobi_runtime.martial_world.rest_practice import (
    apply_rest_practice,
    evening_practice_hours_milli,
    practice_domain,
    practice_pressure_milli,
    safe_lodging_site,
)
from shinobi_runtime.martial_world.scheduler import due_events, settle_schedule, sync_faction_activity
from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_SCHEDULE = "state/martial-world/scheduler.json"
_DEPLOYMENTS = "state/martial-world/deployments.json"
_LOCAL_SITES = "game/data/martial-world/local-sites.json"


def _dt(value: CampaignTime) -> datetime:
    return datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)


def _campaign_time(value: datetime) -> CampaignTime:
    return CampaignTime(value.year, value.month, value.day, value.hour, value.minute, value.second)


class _RecordReadView:
    """Read-only repository overlay for a bounded set of current owner records."""

    def __init__(self, repository: Any, records: Mapping[str, Mapping[str, Any]]) -> None:
        self._repository = repository
        self._records = {str(path): copy.deepcopy(dict(row)) for path, row in records.items()}

    def read_json(self, path: str) -> Any:
        if path in self._records:
            return copy.deepcopy(self._records[path])
        return self._repository.read_json(path)


class JianghuTimeCommandsMixin:
    def _active_player_retinue_roles(self, player_id: str) -> dict[str, str]:
        try:
            state = self.repository.read_json(_DEPLOYMENTS)
        except FileNotFoundError:
            return {}
        rows = state.get("deployments", {}) if isinstance(state, Mapping) else {}
        if not isinstance(rows, Mapping):
            return {}
        out: dict[str, str] = {}
        for retinue_ref in sorted(str(x) for x in rows if isinstance(x, str)):
            row = rows.get(retinue_ref)
            if not isinstance(row, Mapping):
                continue
            if row.get("operation_kind") != "standing_retinue" or row.get("status") != "active" or row.get("leader_ref") != player_id:
                continue
            roles = row.get("member_roles", {}) if isinstance(row.get("member_roles"), Mapping) else {}
            members = row.get("member_refs", []) if isinstance(row.get("member_refs"), list) else []
            for ref in members:
                if isinstance(ref, str) and ref:
                    out.setdefault(ref, str(roles.get(ref) or ""))
        return out

    @staticmethod
    def _raw_faction_person(repository: Any, person_ref: str) -> tuple[str, dict[str, Any], int, dict[str, Any], dict[str, Any]]:
        faction_ref, ordinal = person_route(repository, person_ref)
        path = roster_path(faction_ref)
        roster = copy.deepcopy(repository.read_json(path))
        people = roster.get("people") if isinstance(roster, Mapping) else None
        if not isinstance(people, list) or ordinal < 0 or ordinal >= len(people):
            raise ValueError("jianghu person roster invalid")
        raw = people[ordinal]
        if not isinstance(raw, Mapping) or raw.get("person_id") != person_ref:
            raise ValueError("jianghu person route identity mismatch")
        _fpath, faction = read_faction(repository, faction_ref)
        person = hydrate_person_state(
            raw,
            faction_ref=faction_ref,
            home_location=home_location_ref(faction),
            include_storage_defaults=True,
        )
        return path, roster, ordinal, person, faction

    def _remote_training_pause_records(self, player_id: str) -> dict[str, Mapping[str, Any]]:
        """Stage physical-location pauses before a world frontier can train them."""
        records: dict[str, Mapping[str, Any]] = {}
        roles = self._active_player_retinue_roles(player_id)
        refs = [player_id, *roles]
        for ref in dict.fromkeys(str(x) for x in refs if isinstance(x, str) and x):
            view = _RecordReadView(self.repository, records)
            try:
                path, roster, ordinal, person, faction = self._raw_faction_person(view, ref)
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                continue
            training_site = str(faction.get("local_site_ref") or "")
            location = str(person.get("location_ref") or "")
            if not training_site or not location or location == training_site:
                continue
            state = copy.deepcopy(dict(person.get("training_state", {}))) if isinstance(person.get("training_state"), Mapping) else {}
            if state.get("institutional_paused") is True:
                continue
            state["institutional_paused"] = True
            person["training_state"] = state
            # This is a read-only causal overlay. Do not pass it through the
            # storage compactor, which intentionally strips derived occupancy.
            projected_roster = copy.deepcopy(dict(roster))
            rows = projected_roster.get("people", [])
            if not isinstance(rows, list) or not (0 <= ordinal < len(rows)):
                continue
            rows[ordinal] = person
            projected_roster["people"] = rows
            records[path] = projected_roster
        return records

    @staticmethod
    def _merge_frontier_records(frontier: Mapping[str, Any], records: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        out = copy.deepcopy(dict(frontier))
        writes = {
            str(path): copy.deepcopy(dict(row))
            for path, row in dict(out.get("writes", {})).items()
            if isinstance(path, str) and isinstance(row, Mapping)
        }
        for path, row in records.items():
            writes.setdefault(str(path), copy.deepcopy(dict(row)))
        out["writes"] = writes
        return out

    def _safe_lodging_practice(
        self,
        writes: dict[str, bytes],
        *,
        scene: Mapping[str, Any],
        start: datetime,
        end: datetime,
        player_id: str,
    ) -> dict[str, Any]:
        hours = evening_practice_hours_milli(start, end)
        if hours <= 0:
            return {}
        site_ref = str(scene.get("location_id") or "")
        try:
            sites_state = self.repository.read_json(_LOCAL_SITES)
        except FileNotFoundError:
            return {}
        sites = sites_state.get("sites", {}) if isinstance(sites_state, Mapping) else {}
        site = sites.get(site_ref) if isinstance(sites, Mapping) else None
        if not safe_lodging_site(site):
            return {}

        class _ByteView:
            def __init__(self, repository: Any, images: dict[str, bytes]) -> None:
                self.repository = repository
                self.images = images
            def read_json(self, path: str) -> Any:
                raw = self.images.get(path)
                if raw is not None:
                    return json.loads(raw.decode("utf-8"))
                return self.repository.read_json(path)

        view = _ByteView(self.repository, writes)
        roles = self._active_player_retinue_roles(player_id)
        try:
            commitments = derived_commitment_state(view.read_json)
        except FileNotFoundError:
            commitments = {}
        index = commitments.get("person_index", {}) if isinstance(commitments, Mapping) else {}
        busy = {str(x) for x in index} if isinstance(index, Mapping) else set()
        summaries: dict[str, Any] = {}
        candidates = [(player_id, None), *[(ref, role) for ref, role in roles.items()]]
        for ref, role in candidates:
            if not ref or ref in busy:
                continue
            try:
                path, roster, ordinal, person = roster_person(view, ref)
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                continue
            if str(person.get("location_ref") or "") != site_ref:
                continue
            domain = practice_domain(person, retinue_role=role)
            practiced, summary = apply_rest_practice(
                person,
                duration_hours_milli=hours,
                domain=domain,
                pressure_milli=practice_pressure_milli(journey=False),
            )
            if practiced != person:
                writes[path] = _json_bytes(set_roster_person(roster, ordinal, practiced))
            if domain is not None:
                summaries[ref] = summary
        if not summaries:
            return {}
        return {
            "site_ref": site_ref,
            "practice_hours_milli": hours,
            "people": summaries,
        }

    def _advance_time(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        if meta.get("game") != "jianghu":
            raise CommandRejectedError("jianghu_campaign_required")
        if set(command.payload) != {"target_time"}:
            raise CommandRejectedError("advance_time_payload_fields_invalid")
        try:
            target = CampaignTime.parse(command.payload.get("target_time"))
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("target_time_invalid") from exc
        current_dt, target_dt = _dt(current_time), _dt(target)
        if target_dt <= current_dt:
            raise CommandRejectedError("target_time_must_be_future")

        try:
            schedule = self.repository.read_json(_SCHEDULE)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("jianghu_scheduler_invalid") from exc
        if not isinstance(schedule, Mapping):
            raise CommandRejectedError("jianghu_scheduler_invalid")

        settled_raw = schedule.get("settled_through")
        if not isinstance(settled_raw, str):
            raise CommandRejectedError("jianghu_scheduler_invalid")
        try:
            settled_dt = datetime.fromisoformat(settled_raw)
        except ValueError as exc:
            raise CommandRejectedError("jianghu_scheduler_invalid") from exc
        if settled_dt > current_dt:
            raise CommandRejectedError("jianghu_scheduler_ahead_of_campaign")

        player_id = str(meta.get("player_id") or command.actor_id)
        pause_records = self._remote_training_pause_records(player_id)
        causal_view = _RecordReadView(self.repository, pause_records)

        working_schedule = sync_faction_activity(
            copy.deepcopy(dict(schedule)),
            faction_ids=current_faction_refs(causal_view.read_json),
            now=current_dt,
        )
        if settled_dt < current_dt:
            overdue = due_events(working_schedule, after=settled_dt, through=current_dt)
            if overdue:
                boundary = datetime.fromisoformat(str(overdue[0]["due_at"]))
                frontier = settle_martial_world_frontier(
                    read_json=causal_view.read_json,
                    schedule=working_schedule,
                    events=overdue,
                    at=boundary,
                )
                return self._jianghu_time_frontier_plan(
                    command, meta, current_time, target,
                    frontier=frontier, boundary=boundary,
                    requested_target=target_dt,
                    catchup=True,
                )
            working_schedule = settle_schedule(working_schedule, through=current_dt, processed_events=[])

        events = due_events(working_schedule, after=current_dt, through=target_dt)
        if events:
            boundary = datetime.fromisoformat(str(events[0]["due_at"]))
            frontier = settle_martial_world_frontier(
                read_json=causal_view.read_json,
                schedule=working_schedule,
                events=events,
                at=boundary,
            )
            return self._jianghu_time_frontier_plan(
                command, meta, current_time, target,
                frontier=frontier, boundary=boundary,
                requested_target=target_dt,
                catchup=False,
            )

        schedule_after = settle_schedule(working_schedule, through=target_dt, processed_events=[])
        scene = copy.deepcopy(self.repository.read_json(self.scene_path))
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=target)),
            self.scene_path: _json_bytes(scene),
            _SCHEDULE: _json_bytes(schedule_after),
        }
        rest_summary = self._safe_lodging_practice(
            writes,
            scene=scene,
            start=current_dt,
            end=target_dt,
            player_id=player_id,
        )
        writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("jianghu time write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=target)
            sch = overlay.read_json(_SCHEDULE)
            if sch.get("settled_through") != target_dt.isoformat():
                raise ValueError("jianghu scheduler frontier mismatch")

        result = {
            "command_type": "advance_time",
            "world_time": str(target),
            "requested_time": str(target),
            "interrupted": False,
            "continuation_required": False,
        }
        if rest_summary:
            result["autonomous_rest_practice"] = rest_summary
        return _BuiltPlan(
            code="time_advanced",
            affected_refs=expected,
            writes=writes,
            result=result,
            validator=validate,
        )

    def _jianghu_time_frontier_plan(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
        target: CampaignTime,
        *,
        frontier: Mapping[str, Any],
        boundary: datetime,
        requested_target: datetime,
        catchup: bool,
    ) -> _BuiltPlan:
        boundary_time = _campaign_time(boundary)
        writes = {
            str(path): _json_bytes(record)
            for path, record in dict(frontier.get("writes", {})).items()
            if isinstance(path, str) and isinstance(record, Mapping)
        }
        writes[self.meta_path] = _json_bytes(self._meta_after(meta, command, world_time=boundary_time))
        scene = copy.deepcopy(self.repository.read_json(self.scene_path))
        handoffs = [dict(x) for x in frontier.get("handoffs", []) if isinstance(x, Mapping)]
        boundary_summary = event_seeking_boundary_summary(handoffs)
        boundary_event = boundary_summary.get("event")
        if isinstance(boundary_event, Mapping):
            scene["activity_handoff"] = {
                "event_id": boundary_event.get("event_id"),
                "kind": boundary_event.get("kind"),
                "requires_player_decision": bool(boundary_summary.get("requires_player_decision")),
                "interrupts_continuation": True,
            }
        else:
            scene.pop("activity_handoff", None)
        writes[self.scene_path] = _json_bytes(scene)
        rest_summary = {}
        if not catchup:
            rest_summary = self._safe_lodging_practice(
                writes,
                scene=scene,
                start=_dt(current_time),
                end=boundary,
                player_id=str(meta.get("player_id") or command.actor_id),
            )
        writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes))
        continuation = boundary < requested_target and not boundary_summary.get("interrupted")

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("jianghu frontier write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=boundary_time)
            sch = overlay.read_json(_SCHEDULE)
            if sch.get("settled_through") != boundary.isoformat():
                raise ValueError("jianghu scheduler settlement mismatch")

        result = {
            "command_type": "advance_time",
            "world_time": str(boundary_time),
            "requested_time": str(target),
            "interrupted": bool(boundary_summary.get("interrupted")),
            "continuation_required": continuation,
            "continuation_target": str(target) if continuation else None,
            "internal_reviews_settled": len(frontier.get("reviews", [])),
            "catchup": catchup,
        }
        if rest_summary:
            result["autonomous_rest_practice"] = rest_summary
        if boundary_summary.get("interrupted") and isinstance(boundary_event, Mapping):
            result["player_boundary_kind"] = boundary_summary.get("class", "soft_player_facing")
            result["interrupt_event_id"] = boundary_event.get("event_id")
            result["player_handoffs"] = [row for row in handoffs if row.get("handoff", {}).get("interrupts_event_seeking")][:16]
        return _BuiltPlan(
            code="time_frontier_settled",
            affected_refs=expected,
            writes=writes,
            result=result,
            validator=validate,
        )
