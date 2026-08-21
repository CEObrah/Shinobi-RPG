"""Jianghu campaign-time command.

The campaign clock advances only through this reducer.  It compares current
committed time with the compact Jianghu causal frontier, settles the earliest
due work chunk, and either reaches the requested target or returns a bounded
continuation boundary.  No global per-person tick exists.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.martial_world.scheduler import due_events, settle_schedule
from shinobi_runtime.martial_world.event_seeking import event_seeking_boundary_summary
from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_SCHEDULE = "state/martial-world/scheduler.json"


def _dt(value: CampaignTime) -> datetime:
    return datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)


def _campaign_time(value: datetime) -> CampaignTime:
    return CampaignTime(value.year, value.month, value.day, value.hour, value.minute, value.second)


class JianghuTimeCommandsMixin:
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
        # The scheduler may be exactly at current time or behind current time only
        # after an explicit state reset. It may never be ahead of committed campaign time.
        if settled_dt > current_dt:
            raise CommandRejectedError("jianghu_scheduler_ahead_of_campaign")

        # If an explicit state reset leaves the scheduler behind the current campaign
        # clock, prove that no due work was skipped before moving its quiet
        # frontier up to current time.
        working_schedule = copy.deepcopy(dict(schedule))
        if settled_dt < current_dt:
            overdue = due_events(working_schedule, after=settled_dt, through=current_dt)
            if overdue:
                boundary = datetime.fromisoformat(str(overdue[0]["due_at"]))
                frontier = settle_martial_world_frontier(
                    read_json=self.repository.read_json,
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
                read_json=self.repository.read_json,
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
        writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("jianghu time write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=target)
            sch = overlay.read_json(_SCHEDULE)
            if sch.get("settled_through") != target_dt.isoformat():
                raise ValueError("jianghu scheduler frontier mismatch")

        return _BuiltPlan(
            code="time_advanced",
            affected_refs=expected,
            writes=writes,
            result={
                "command_type": "advance_time",
                "world_time": str(target),
                "requested_time": str(target),
                "interrupted": False,
                "continuation_required": False,
            },
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
        if boundary_summary.get("interrupted") and isinstance(boundary_event, Mapping):
            result["player_boundary_kind"] = boundary_summary.get("class", "soft_player_facing")
            result["interrupt_event_id"] = boundary_event.get("event_id")
            # Timed semantic commands may lawfully continue through soft
            # informational boundaries, but they must not lose the notice.
            # Keep this bounded and ephemeral in the command result rather
            # than persisting another runtime history owner.
            result["player_handoffs"] = [row for row in handoffs if row.get("handoff", {}).get("interrupts_event_seeking")][:16]
        return _BuiltPlan(
            code="time_frontier_settled",
            affected_refs=expected,
            writes=writes,
            result=result,
            validator=validate,
        )
