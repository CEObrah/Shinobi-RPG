"""Production Jianghu planner composition.

The generic repository planner owns command mechanics. Production additionally
closes a narrow causal gap between exact route combat and the physical route
owner: a resolved player road combat is reconciled in the same transaction, and
legacy stale contacts are staged before the next time-bearing action.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.martial_world.route_contact_reconciliation import (
    reconcile_resolved_player_route_contact_records,
)
from shinobi_runtime.sim.events import CampaignTime

from .planner import RepositoryCommandPlanner


class _RecordReadView:
    def __init__(self, repository: Any, records: Mapping[str, Mapping[str, Any]]) -> None:
        self._repository = repository
        self._records = {
            str(path): copy.deepcopy(dict(row))
            for path, row in records.items()
            if isinstance(path, str) and isinstance(row, Mapping)
        }

    def read_json(self, path: str) -> Any:
        row = self._records.get(str(path))
        if row is not None:
            return copy.deepcopy(row)
        return self._repository.read_json(path)


class _PlanReadView:
    def __init__(self, repository: Any, writes: Mapping[str, bytes]) -> None:
        self._repository = repository
        self._writes = dict(writes)

    def read_json(self, path: str) -> Any:
        raw = self._writes.get(str(path))
        if raw is not None:
            return json.loads(raw.decode("utf-8"))
        return self._repository.read_json(path)


def _datetime(value: CampaignTime) -> datetime:
    return datetime(
        value.year, value.month, value.day,
        value.hour, value.minute, value.second,
    )


class CampaignCommandPlanner(RepositoryCommandPlanner):
    """Production planner with atomic route-contact reconciliation."""

    def _resolved_route_contact_records(
        self,
        *,
        read_json,
        at: CampaignTime,
        player_ref: str,
        combat_ref: str | None = None,
    ) -> dict[str, Mapping[str, Any]]:
        try:
            return reconcile_resolved_player_route_contact_records(
                read_json=read_json,
                at=_datetime(at),
                player_ref=player_ref,
                combat_ref=combat_ref,
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
            raise CommandRejectedError("jianghu_route_contact_reconciliation_invalid") from exc

    def _jianghu_combat_resolution(self, command, meta, current_time):
        plan = super()._jianghu_combat_resolution(command, meta, current_time)
        if str(plan.result.get("combat_status") or "") != "resolved":
            return plan
        try:
            world_time = CampaignTime.parse(plan.result.get("world_time"))
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("jianghu_route_contact_reconciliation_invalid") from exc
        combat_ref = str(plan.result.get("combat_ref") or "")
        view = _PlanReadView(self.repository, plan.writes)
        records = self._resolved_route_contact_records(
            read_json=view.read_json,
            at=world_time,
            player_ref=command.actor_id,
            combat_ref=combat_ref or None,
        )
        if not records:
            return plan
        return self._combine_time_plan(
            command,
            plan,
            extra_records=records,
            code=plan.code,
            result={**dict(plan.result), "route_contact_reconciled": True},
        )

    def _time_plan_exact_staged(
        self,
        command,
        meta,
        current_time,
        *,
        seconds,
        staged_records,
        allow_hard_interrupt=False,
        stop_on_soft_interrupt=False,
        handoff_matcher=None,
        include_unmatched_handoffs=True,
        persist_staged_records=False,
        max_frontiers=None,
    ):
        staged = {
            str(path): copy.deepcopy(dict(record))
            for path, record in staged_records.items()
            if isinstance(path, str) and isinstance(record, Mapping)
        }
        view = _RecordReadView(self.repository, staged)
        player_ref = str(meta.get("player_id") or command.actor_id)
        repair = self._resolved_route_contact_records(
            read_json=view.read_json,
            at=current_time,
            player_ref=player_ref,
        )
        if repair:
            staged.update(repair)
        plan = super()._time_plan_exact_staged(
            command,
            meta,
            current_time,
            seconds=seconds,
            staged_records=staged,
            allow_hard_interrupt=allow_hard_interrupt,
            stop_on_soft_interrupt=stop_on_soft_interrupt,
            handoff_matcher=handoff_matcher,
            include_unmatched_handoffs=include_unmatched_handoffs,
            persist_staged_records=persist_staged_records,
            max_frontiers=max_frontiers,
        )
        if not repair:
            return plan
        untouched = {
            path: record
            for path, record in repair.items()
            if path not in plan.writes
        }
        if not untouched:
            return plan
        return self._combine_time_plan(
            command,
            plan,
            extra_records=untouched,
            code=plan.code,
            result={**dict(plan.result), "route_contact_reconciled": True},
        )


__all__ = ["CampaignCommandPlanner"]
