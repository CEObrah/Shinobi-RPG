"""Bounded downtime command that advances one causal boundary at a time.

The command deliberately reuses ``advance_time`` rather than compacting training,
recovery or autonomy in a second simulation path. A client may repeat it while
``continuation_required`` is true; it stops repeating as soon as the settled
boundary produces a player-facing event.
"""
from __future__ import annotations

from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.sim.events import CampaignTime

_INSTALLED = False
_COMMAND = "advance_until_event"


def _meaningful(result: Mapping[str, Any]) -> bool:
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
    reviews = result.get("team_reviews")
    if isinstance(reviews, list) and any(
        isinstance(row, Mapping) and row.get("kind") == "player_led_team_checkin"
        for row in reviews
    ):
        return True
    return False


def _advance_until_event(self: Any, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
    raw_target = command.payload.get("target_time")
    try:
        requested = CampaignTime.parse(raw_target)
    except (TypeError, ValueError) as exc:
        raise CommandRejectedError("target_time_invalid") from exc
    if requested <= current_time:
        raise CommandRejectedError("command_no_op" if requested == current_time else "target_time_before_current")
    scene = self._scene_base(current_time)
    scheduler = self._load_scheduler(current_time=current_time, scene=scene)
    due_times = [event.due_at for event in scheduler.queue.snapshot() if event.due_at >= current_time]
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
    meaningful = _meaningful(result)
    result["advance_until_event"] = {
        "requested_target": str(requested),
        "boundary_target": str(target),
        "meaningful_event": meaningful,
    }
    if not meaningful and reached < requested:
        result["continuation_required"] = True
        result["continuation_target"] = str(requested)
    return _BuiltPlan(_COMMAND, base.affected_refs, base.writes, result, base.validator)


def install_downtime_until_event() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    if _COMMAND not in COMMAND_SPECS:
        COMMAND_SPECS[_COMMAND] = CommandSpec(
            required_fields=("target_time",),
            summary="Advance through established downtime one causal boundary at a time and stop when a player-facing event is produced.",
            payload_hints={"target_time": "SE-YYYY-MM-DDTHH:MM:SS"},
            availability="scene_must_allow_time_passage",
        )
    RepositoryCommandPlanner.COMMAND_TYPES = frozenset(COMMAND_SPECS)
    setattr(RepositoryCommandPlanner, "_" + _COMMAND, _advance_until_event)
    try:
        from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
        CampaignCommandPlanner.COMMAND_TYPES = frozenset(COMMAND_SPECS)
    except ImportError:
        pass
    _INSTALLED = True


__all__ = ["install_downtime_until_event", "_meaningful"]
