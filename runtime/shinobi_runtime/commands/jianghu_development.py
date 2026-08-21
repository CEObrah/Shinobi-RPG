"""Command-surface integration for field and combat development.

The underlying Jianghu reducers remain authoritative for travel/combat outcomes.
This mixin post-processes their planned roster after-images so development uses
the same atomic transaction and cannot exist only in narration.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Mapping

from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.martial_world.field_development import apply_combat_events, apply_field_activity
from shinobi_runtime.martial_world.live_state import roster_person, set_roster_person
from shinobi_runtime.sim.events import CampaignTime


class _PlanReadView:
    def __init__(self, repository: Any, writes: dict[str, bytes]) -> None:
        self._repository = repository
        self._writes = writes

    def read_json(self, path: str) -> Any:
        raw = self._writes.get(path)
        if raw is not None:
            return json.loads(raw.decode("utf-8"))
        return self._repository.read_json(path)


class JianghuDevelopmentCommandsMixin:
    def _replace_person_in_plan(
        self, writes: dict[str, bytes], person_ref: str, person_after: Mapping[str, Any]
    ) -> bool:
        view = _PlanReadView(self.repository, writes)
        try:
            path, roster, ordinal, _current = roster_person(view, person_ref)
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            return False
        writes[path] = _json_bytes(set_roster_person(roster, ordinal, person_after))
        return True

    def _plan_person(self, writes: dict[str, bytes], person_ref: str) -> dict[str, Any] | None:
        view = _PlanReadView(self.repository, writes)
        try:
            _path, _roster, _ordinal, person = roster_person(view, person_ref)
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            return None
        return copy.deepcopy(dict(person))

    @staticmethod
    def _with_plan(
        built: _BuiltPlan, *, writes: Mapping[str, bytes], result: Mapping[str, Any]
    ) -> _BuiltPlan:
        return _BuiltPlan(
            code=built.code,
            affected_refs=tuple(sorted(writes)),
            writes=dict(writes),
            result=dict(result),
            validator=built.validator,
        )

    def _jianghu_strategic_travel_resolution(
        self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime
    ) -> _BuiltPlan:
        built = super()._jianghu_strategic_travel_resolution(command, meta, current_time)
        writes = dict(built.writes)
        person = self._plan_person(writes, command.actor_id)
        if person is None:
            return built
        segments = built.result.get("segments", []) if isinstance(built.result, Mapping) else []
        hours_milli = 0
        if isinstance(segments, list):
            for segment in segments:
                if not isinstance(segment, Mapping):
                    continue
                try:
                    hours_milli += max(0, int(round(float(segment.get("hours", 0)) * 1000)))
                except (TypeError, ValueError):
                    continue
        developed, summary = apply_field_activity(
            person,
            duration_hours_milli=hours_milli,
            activity_kind="road_travel",
            leader=False,
            pressure_milli=650,
        )
        if not self._replace_person_in_plan(writes, command.actor_id, developed):
            return built
        result = copy.deepcopy(dict(built.result))
        result["field_development"] = {
            "duration_hours_milli": hours_milli,
            "domains": [
                {
                    "domain": str(row.get("domain") or ""),
                    "gain_milli": max(0, int(row.get("gain_milli", 0))),
                    "points": max(0, int(row.get("points", 0))),
                    "evidence_added_milli": max(0, int(row.get("evidence_added_milli", 0))),
                }
                for row in summary.get("domains", [])
                if isinstance(row, Mapping)
            ],
        }
        return self._with_plan(built, writes=writes, result=result)

    def _jianghu_combat_resolution(
        self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime
    ) -> _BuiltPlan:
        action = str(command.payload.get("action") or "")
        built = super()._jianghu_combat_resolution(command, meta, current_time)
        if action != "exchange":
            return built
        events = [row for row in built.result.get("events", []) if isinstance(row, Mapping)] if isinstance(built.result, Mapping) else []
        if not events:
            return built
        refs: set[str] = set()
        for event in events:
            for key in ("actor_ref", "intended_ref", "actual_ref"):
                value = event.get(key)
                if isinstance(value, str) and value:
                    refs.add(value)
        people_before: dict[str, Mapping[str, Any]] = {}
        for ref in sorted(refs):
            try:
                _path, _roster, _ordinal, person = self._person(ref)
            except Exception:
                continue
            people_before[ref] = copy.deepcopy(dict(person))
        writes = dict(built.writes)
        people_after: dict[str, Mapping[str, Any]] = {}
        for ref in sorted(refs):
            person = self._plan_person(writes, ref)
            if person is not None:
                people_after[ref] = person
        developed, summary = apply_combat_events(
            people_after,
            people_before=people_before,
            events=events,
        )
        for ref, person in developed.items():
            self._replace_person_in_plan(writes, ref, person)
        result = copy.deepcopy(dict(built.result))
        result["development_actions_counted"] = max(0, int(summary.get("actions_counted", 0)))
        return self._with_plan(built, writes=writes, result=result)


__all__ = ["JianghuDevelopmentCommandsMixin"]
