"""Event-seeking downtime over the same causal time-settlement path.

The command never creates story events. It advances one persisted causal
boundary at a time, continues across internal-only work, stops softly when a
new player-facing event is projected, and stops hard when the underlying time
settlement reaches a protected player decision.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _exact_payload
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.sim.events import CampaignTime

_INSTALLED = False
_COMMAND = "advance_until_event"


def _player_facing_event(result: Mapping[str, Any]) -> bool:
    explicit = result.get("player_facing_events")
    if isinstance(explicit, list) and any(isinstance(row, Mapping) for row in explicit):
        return True

    updates = result.get("world_front_updates")
    if isinstance(updates, list) and updates:
        return True

    actions = result.get("autonomous_actions")
    if isinstance(actions, list):
        for action in actions:
            if not isinstance(action, Mapping):
                continue
            if action.get("kind") == "player_mission_offer" and action.get("skipped") is None:
                return True
            if isinstance(action.get("promotion_exam_cycle"), Mapping):
                return True
            deliveries = action.get("player_report_deliveries")
            if isinstance(deliveries, list) and any(isinstance(row, Mapping) for row in deliveries):
                return True

    reviews = result.get("team_reviews")
    if isinstance(reviews, list) and any(
        isinstance(row, Mapping) and row.get("kind") == "player_led_team_checkin"
        for row in reviews
    ):
        return True
    return False


def _staged_player_facing_event(plan: _BuiltPlan, scene_path: str) -> bool:
    """Use the production scene projection as an extensible soft-event signal.

    The time handoff freshness layer removes inherited transient fields before
    this function runs. A remaining report, active question, or observable
    pressure therefore represents newly settled player-facing material rather
    than a stale scene artifact.
    """

    raw = plan.writes.get(scene_path)
    if raw is None:
        return False
    try:
        scene = json.loads(raw.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return False
    if not isinstance(scene, Mapping):
        return False
    pressures = scene.get("observable_pressures")
    if isinstance(pressures, list) and any(isinstance(value, str) and value for value in pressures):
        return True
    narrative = scene.get("narrative")
    if not isinstance(narrative, Mapping):
        return False
    for field in ("available_reports", "active_questions"):
        values = narrative.get(field)
        if isinstance(values, list) and any(isinstance(value, str) and value for value in values):
            return True
    return False


def _stop_kind(result: Mapping[str, Any], *, staged_scene_event: bool = False) -> str:
    if result.get("interrupted") is True:
        return "hard_decision"
    if staged_scene_event or _player_facing_event(result):
        return "player_facing_event"
    return "internal_boundary"


def _meaningful(result: Mapping[str, Any]) -> bool:
    """Compatibility helper: any soft or hard stop is meaningful to the caller."""

    return _stop_kind(result) != "internal_boundary"


def _advance_until_event(
    self: Any,
    command: CommandEnvelope,
    meta: Mapping[str, Any],
    current_time: CampaignTime,
) -> _BuiltPlan:
    _exact_payload(command.payload, ("target_time",), command.command_type)
    raw_target = command.payload.get("target_time")
    try:
        requested = CampaignTime.parse(raw_target)
    except (TypeError, ValueError) as exc:
        raise CommandRejectedError("target_time_invalid") from exc
    if requested <= current_time:
        raise CommandRejectedError(
            "command_no_op" if requested == current_time else "target_time_before_current"
        )

    scene = self._scene_base(current_time)
    scheduler = self._load_scheduler(current_time=current_time, scene=scene)
    due_times = [
        event.due_at
        for event in scheduler.queue.snapshot()
        if event.due_at >= current_time
    ]
    if due_times:
        boundary = min(due_times)
        if boundary <= current_time:
            boundary = current_time.add_seconds(1)
        target = boundary if boundary < requested else requested
    else:
        target = requested

    inner = CommandEnvelope(
        campaign_id=command.campaign_id,
        request_id=command.request_id + ".boundary",
        actor_id=command.actor_id,
        command_type="advance_time",
        expected_revision=command.expected_revision,
        submitted_at=command.submitted_at,
        payload={"target_time": str(target)},
        mode=command.mode,
    )
    base = self._advance_time(inner, meta, current_time)
    result = dict(base.result)
    reached_raw = result.get("world_time")
    try:
        reached = CampaignTime.parse(reached_raw)
    except (TypeError, ValueError) as exc:
        raise CommandRejectedError("advance_until_event_result_invalid") from exc

    scene_path = getattr(self, "scene_path", "state/scene.json")
    staged_scene_event = _staged_player_facing_event(base, scene_path)
    stop_kind = _stop_kind(result, staged_scene_event=staged_scene_event)
    result["advance_until_event"] = {
        "requested_target": str(requested),
        "boundary_target": str(target),
        "stop_kind": stop_kind,
        "meaningful_event": stop_kind != "internal_boundary",
        "player_facing_event": stop_kind == "player_facing_event",
        "hard_decision": stop_kind == "hard_decision",
    }
    if stop_kind == "internal_boundary" and reached < requested:
        result["continuation_required"] = True
        result["continuation_target"] = str(requested)
    else:
        result.pop("continuation_required", None)
        result.pop("continuation_target", None)

    return _BuiltPlan(
        _COMMAND,
        base.affected_refs,
        base.writes,
        result,
        base.validator,
    )


def _register_planner(planner: type) -> None:
    """Register the command on one concrete planner without relying on another installer."""

    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)
    setattr(planner, "_" + _COMMAND, _advance_until_event)


def install_downtime_until_event() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    if _COMMAND not in COMMAND_SPECS:
        COMMAND_SPECS[_COMMAND] = CommandSpec(
            required_fields=("target_time",),
            summary=(
                "Advance through established downtime across internal causal work and "
                "stop at the first player-facing event or protected player decision."
            ),
            payload_hints={"target_time": "SE-YYYY-MM-DDTHH:MM:SS"},
            availability="scene_must_allow_time_passage",
        )

    _register_planner(RepositoryCommandPlanner)

    # Production dispatch is a later campaign subclass, not the generic planner.
    # Register explicitly on every concrete planner layer that can be imported at
    # bootstrap so this command never depends on an unrelated installer refreshing
    # COMMAND_TYPES as a side effect.
    try:
        from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner

        _register_planner(CampaignCommandPlanner)
    except ImportError:
        pass
    try:
        from shinobi_runtime.commands.campaign_mission_assignment import (
            CampaignCommandPlanner as FinalCampaignCommandPlanner,
        )

        _register_planner(FinalCampaignCommandPlanner)
    except ImportError:
        pass
    _INSTALLED = True


__all__ = [
    "install_downtime_until_event",
    "_meaningful",
    "_player_facing_event",
    "_register_planner",
    "_staged_player_facing_event",
    "_stop_kind",
]
