"""Event-seeking downtime and procedure time over one causal settlement path.

These commands never create story events. They advance through persisted causal
boundaries, continue across internal-only work, stop softly when a new
player-facing event is projected, and stop hard when the underlying time
settlement reaches a protected player decision. Procedure callers select a
closed authored activity kind against the fresh scene; they never supply an
arbitrary duration.
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
_PROCEDURE_COMMAND = "procedure_time_resolution"
_PROCEDURE_RULES = "game/data/mechanics/procedure-time.json"
_CUE_REF_KEYS = (
    "event_id",
    "delivery_id",
    "report_ref",
    "mission_id",
    "mission_ref",
    "team_ref",
    "cycle_id",
    "pressure_ref",
    "commitment_ref",
    "checkin_ref",
    "source_event_ref",
)


def _bounded_event_cue(kind: str, row: Mapping[str, Any]) -> dict[str, Any]:
    refs: list[str] = []
    for key in _CUE_REF_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value and value not in refs:
            refs.append(value)
        if len(refs) >= 6:
            break
    cue: dict[str, Any] = {"kind": kind, "source_refs": refs}
    actor_ref = row.get("contact_actor_ref")
    if isinstance(actor_ref, str) and actor_ref:
        cue["contact_actor_ref"] = actor_ref
    return cue


def _player_facing_event_cue(result: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return one bounded player-safe cue for the first soft event in result order."""

    explicit = result.get("player_facing_events")
    if isinstance(explicit, list):
        for row in explicit:
            if isinstance(row, Mapping):
                kind = row.get("kind")
                return _bounded_event_cue(
                    kind if isinstance(kind, str) and kind else "player_facing_event",
                    row,
                )

    updates = result.get("world_front_updates")
    if isinstance(updates, list):
        for row in updates:
            if isinstance(row, Mapping):
                return _bounded_event_cue("world_front_update", row)
            if isinstance(row, str) and row:
                return {"kind": "world_front_update", "source_refs": [row]}

    actions = result.get("autonomous_actions")
    if isinstance(actions, list):
        for action in actions:
            if not isinstance(action, Mapping):
                continue
            if action.get("kind") == "player_mission_offer" and action.get("skipped") is None:
                return _bounded_event_cue("player_mission_offer", action)
            cycle = action.get("promotion_exam_cycle")
            if isinstance(cycle, Mapping):
                return _bounded_event_cue("promotion_exam_cycle", cycle)
            deliveries = action.get("player_report_deliveries")
            if isinstance(deliveries, list):
                for delivery in deliveries:
                    if isinstance(delivery, Mapping):
                        return _bounded_event_cue("player_report_delivery", delivery)

    reviews = result.get("team_reviews")
    if isinstance(reviews, list):
        for row in reviews:
            if isinstance(row, Mapping) and row.get("kind") == "player_led_team_checkin":
                return _bounded_event_cue("player_led_team_checkin", row)
    return None


def _player_facing_event(result: Mapping[str, Any]) -> bool:
    return _player_facing_event_cue(result) is not None


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
    # Promotion-exam phases are subordinate campaign-time boundaries derived
    # from a persisted cycle opening, not Academy's unrelated monthly review.
    # Include their next boundary in event seeking so downtime cannot silently
    # jump across a public examination phase change.
    from shinobi_runtime.commands.promotion_exam_pacing import next_promotion_exam_boundary

    exam_boundary = next_promotion_exam_boundary(self.repository, current_time)
    if exam_boundary is not None:
        due_times.append(exam_boundary)
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
    event_cue = _player_facing_event_cue(result)
    stop_kind = _stop_kind(result, staged_scene_event=staged_scene_event)
    boundary_result: dict[str, Any] = {
        "requested_target": str(requested),
        "boundary_target": str(target),
        "stop_kind": stop_kind,
        "meaningful_event": stop_kind != "internal_boundary",
        "player_facing_event": stop_kind == "player_facing_event",
        "hard_decision": stop_kind == "hard_decision",
    }
    if stop_kind == "player_facing_event":
        boundary_result["event_cue"] = event_cue or {
            "kind": "scene_projection_event",
            "source_refs": [],
        }
    result["advance_until_event"] = boundary_result
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


def _procedure_rules(repository: Any) -> Mapping[str, Any]:
    try:
        record = repository.read_json(_PROCEDURE_RULES)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("procedure_time_rules_invalid") from exc
    procedures = record.get("procedures") if isinstance(record, Mapping) else None
    if (
        record.get("schema") != "procedure-time-rules"
        or record.get("version") != 1
        or not isinstance(procedures, Mapping)
    ):
        raise CommandRejectedError("procedure_time_rules_invalid")
    return procedures


def _procedure_time_resolution(
    self: Any,
    command: CommandEnvelope,
    meta: Mapping[str, Any],
    current_time: CampaignTime,
) -> _BuiltPlan:
    _exact_payload(command.payload, ("scene_id", "procedure_kind"), command.command_type)
    scene_id = command.payload.get("scene_id")
    procedure_kind = command.payload.get("procedure_kind")
    if (
        not isinstance(scene_id, str)
        or not scene_id
        or not isinstance(procedure_kind, str)
        or not procedure_kind
    ):
        raise CommandRejectedError("procedure_time_input_invalid")

    scene = self._scene_base(current_time)
    if scene.get("scene_id") != scene_id:
        raise CommandRejectedError("procedure_scene_stale")
    if (
        scene.get("active_combat") is True
        or scene.get("time_passage_allowed") is not True
        or scene.get("decision_required") is not None
    ):
        raise CommandRejectedError("procedure_time_not_available")

    procedures = _procedure_rules(self.repository)
    rule = procedures.get(procedure_kind)
    duration = rule.get("duration_seconds") if isinstance(rule, Mapping) else None
    if (
        not isinstance(rule, Mapping)
        or isinstance(duration, bool)
        or not isinstance(duration, int)
        or duration <= 0
        or duration > 6 * 60 * 60
    ):
        raise CommandRejectedError("procedure_kind_invalid")

    target = current_time.add_seconds(duration)
    inner = CommandEnvelope(
        campaign_id=command.campaign_id,
        request_id=command.request_id + ".procedure",
        actor_id=command.actor_id,
        command_type=_COMMAND,
        expected_revision=command.expected_revision,
        submitted_at=command.submitted_at,
        payload={"target_time": str(target)},
        mode=command.mode,
    )
    base = _advance_until_event(self, inner, meta, current_time)
    result = dict(base.result)
    reached_raw = result.get("world_time")
    try:
        reached = CampaignTime.parse(reached_raw)
    except (TypeError, ValueError) as exc:
        raise CommandRejectedError("procedure_time_result_invalid") from exc
    boundary = result.get("advance_until_event")
    stop_kind = boundary.get("stop_kind") if isinstance(boundary, Mapping) else None
    completed = reached >= target
    result["procedure_time"] = {
        "procedure_kind": procedure_kind,
        "scene_id": scene_id,
        "start_time": str(current_time),
        "authored_duration_seconds": duration,
        "target_time": str(target),
        "reached_time": str(reached),
        "completed": completed,
        "stop_kind": stop_kind,
    }
    if not completed and stop_kind == "internal_boundary":
        # Preserve the exact authored horizon through the ordinary event-seeking
        # continuation path. A later chunk must use this target, not add another
        # full procedure duration.
        result["continuation_required"] = True
        result["continuation_target"] = str(target)
    elif stop_kind in ("player_facing_event", "hard_decision"):
        # A real event interrupted the procedure. The player/GM must stage that
        # boundary before deciding whether the remainder resumes.
        result.pop("continuation_required", None)
        result.pop("continuation_target", None)

    return _BuiltPlan(
        _PROCEDURE_COMMAND,
        base.affected_refs,
        base.writes,
        result,
        base.validator,
    )


def _register_planner(planner: type) -> None:
    """Register the commands on one concrete planner without side-effect coupling."""

    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)
    setattr(planner, "_" + _COMMAND, _advance_until_event)
    setattr(planner, "_" + _PROCEDURE_COMMAND, _procedure_time_resolution)


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
    if _PROCEDURE_COMMAND not in COMMAND_SPECS:
        COMMAND_SPECS[_PROCEDURE_COMMAND] = CommandSpec(
            required_fields=("scene_id", "procedure_kind"),
            summary=(
                "Settle authored elapsed time for one already-established substantive "
                "procedure stage through the causal scheduler, stopping at earlier "
                "player-facing events or protected decisions. The caller never supplies duration."
            ),
            payload_hints={
                "scene_id": "fresh scene.scene_id",
                "procedure_kind": (
                    "brief_exchange | substantive_conversation | briefing | team_checkin | "
                    "training_review | medical_consultation | negotiation_session | "
                    "examination_evaluation | council_session"
                ),
            },
            availability="scene_must_allow_time_passage_and_have_no_protected_decision",
        )

    _register_planner(RepositoryCommandPlanner)

    # Production dispatch is a later campaign subclass, not the generic planner.
    # Register explicitly on every concrete planner layer that can be imported at
    # bootstrap so these commands never depend on an unrelated installer refreshing
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
    try:
        from shinobi_runtime.commands.campaign_player_handoffs import (
            CampaignCommandPlanner as PlayerHandoffCampaignCommandPlanner,
        )

        _register_planner(PlayerHandoffCampaignCommandPlanner)
    except ImportError:
        pass
    _INSTALLED = True


__all__ = [
    "install_downtime_until_event",
    "_meaningful",
    "_player_facing_event",
    "_player_facing_event_cue",
    "_procedure_rules",
    "_procedure_time_resolution",
    "_register_planner",
    "_staged_player_facing_event",
    "_stop_kind",
]
