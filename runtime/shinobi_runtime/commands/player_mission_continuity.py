"""Preserve player-team mission continuity across offer generation and settlement.

Exact mission owners remain authoritative. This module maintains a bounded team
mission-memory projection from committed player-controlled mission settlements
and uses only that bounded evidence to prevent materially repeated mission
offers from being generated again for the same player-led team.
"""
from __future__ import annotations

import copy
import json
from functools import wraps
from typing import Any, Dict, Mapping, Optional, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _json_bytes
from shinobi_runtime.commands.domains.missions import MissionCommandsMixin
from shinobi_runtime.commands.living_world_consequences import LivingWorldConsequencesMixin
from shinobi_runtime.commands.living_world_mission import LivingWorldMissionMixin
from shinobi_runtime.commands.mission_owner import MissionOwner, mission_owner_path

_RECENT_MISSION_LIMIT = 16
_INSTALLED = False


def mission_assignment_signature(
    objective_kind: str,
    briefing: Mapping[str, Any],
) -> tuple[object, ...]:
    """Return the material assignment identity used for bounded dedupe.

    ``protect`` and ``escort`` are equivalent only for a concrete person moving
    along a concrete route. Other objective families remain distinct so normal
    mission variety is not collapsed merely because two missions share a place.
    """

    if not isinstance(objective_kind, str) or not objective_kind:
        raise ValueError("objective_kind must be non-empty text")
    if not isinstance(briefing, Mapping):
        raise TypeError("briefing must be a mapping")
    subject_kind = briefing.get("subject_kind")
    destination = briefing.get("destination_place_ref")
    route_id = briefing.get("route_id")
    family = objective_kind
    if (
        objective_kind in ("protect", "escort")
        and subject_kind == "person"
        and isinstance(destination, str)
        and destination
        and isinstance(route_id, str)
        and route_id
    ):
        family = "protected_person_transit"
    return (
        family,
        subject_kind,
        briefing.get("subject_ref"),
        briefing.get("origin_place_ref"),
        destination,
        route_id,
        briefing.get("threat_source_ref"),
    )


def _history_path(planner: Any, team_ref: str) -> str:
    resolver = getattr(planner, "_team_history_path", None)
    if callable(resolver):
        return resolver(team_ref)
    return f"state/team/history/{team_ref}.json"


def _validated_recent_refs(history: Mapping[str, Any]) -> list[str]:
    raw = history.get("recent_mission_refs", [])
    if raw is None:
        raw = []
    if (
        not isinstance(raw, list)
        or len(raw) > _RECENT_MISSION_LIMIT
        or any(
            not isinstance(ref, str)
            or not ref.startswith("mission.")
            or not ref
            for ref in raw
        )
        or len(raw) != len(set(raw))
    ):
        raise CommandRejectedError("team_operational_history_invalid")
    refs = list(raw)
    last = history.get("last_mission_ref")
    if isinstance(last, str) and last.startswith("mission.") and last not in refs:
        refs.append(last)
    return refs[-_RECENT_MISSION_LIMIT:]


def _read_team_history(planner: Any, team_ref: str) -> Optional[Dict[str, Any]]:
    path = _history_path(planner, team_ref)
    raw = planner.repository.read_optional_bytes(path)
    if raw is None:
        return None
    try:
        history = copy.deepcopy(planner.repository.read_json(path))
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("team_operational_history_invalid") from exc
    if (
        not isinstance(history, dict)
        or history.get("schema") != "team-operational-history"
        or history.get("team_id") != team_ref
    ):
        raise CommandRejectedError("team_operational_history_invalid")
    _validated_recent_refs(history)
    return history


def _find_completed_duplicate(
    planner: Any,
    *,
    team_ref: str,
    candidate_signature: tuple[object, ...],
    record_writes: Mapping[str, Mapping[str, Any]],
) -> Optional[str]:
    history = _read_team_history(planner, team_ref)
    if history is None:
        return None
    for mission_ref in reversed(_validated_recent_refs(history)):
        path = mission_owner_path(mission_ref)
        try:
            raw = record_writes.get(path)
            if raw is None:
                raw = planner.repository.read_json(path)
            owner = MissionOwner.from_record(raw)
        except (FileNotFoundError, TypeError, ValueError):
            continue
        if (
            owner.operation_ref != team_ref
            or owner.mission.state != "succeeded"
            or owner.briefing is None
        ):
            continue
        prior = mission_assignment_signature(
            owner.briefing.objective_kind,
            owner.briefing.to_record(),
        )
        if prior == candidate_signature:
            return mission_ref
    return None


def _duplicate_player_offer(
    planner: Any,
    *,
    decision: Any,
    at: Any,
    command: Any,
    scheduler: Any,
    record_writes: Dict[str, Dict[str, Any]],
    faction_record: Dict[str, Any],
) -> Optional[Mapping[str, Any]]:
    if command.mode != "gameplay":
        return None
    payload = decision.payload
    faction_id = payload.get("faction_id")
    if not isinstance(faction_id, str):
        return None
    try:
        _profile, assignment = planner._autonomy_policy_book().faction_context(faction_id)
    except (TypeError, ValueError, CommandRejectedError):
        return None
    config = assignment.get("player_offer") if isinstance(assignment, Mapping) else None
    if not isinstance(config, Mapping) or config.get("enabled") is not True:
        return None

    faction = faction_record.get("faction") if isinstance(faction_record, Mapping) else None
    plan_state = faction.get("plan_state") if isinstance(faction, Mapping) else None
    if not isinstance(plan_state, dict):
        raise CommandRejectedError("faction_owner_invalid")
    wake_refs = plan_state.get("wake_required_mission_refs", [])
    if not isinstance(wake_refs, list):
        raise CommandRejectedError("faction_owner_invalid")
    if planner._pending_player_offer(
        wake_refs,
        player_ref=command.actor_id,
        record_writes=record_writes,
    ) is not None:
        return None

    selected = planner._player_offer_team(
        config,
        player_ref=command.actor_id,
        scheduler=scheduler,
        record_writes=record_writes,
    )
    if selected is None:
        return None
    team_ref, _team = selected
    objective_kind = planner._mission_objective_kind(payload, faction_id, at)
    template = planner._player_offer_briefing_config(
        faction_id=faction_id,
        objective_kind=objective_kind,
    )
    candidate = mission_assignment_signature(objective_kind, template)
    duplicate = _find_completed_duplicate(
        planner,
        team_ref=team_ref,
        candidate_signature=candidate,
        record_writes=record_writes,
    )
    if duplicate is None:
        return None
    return {
        "kind": "player_mission_offer",
        "skipped": "recent_assignment_duplicate",
        "team_ref": team_ref,
        "objective_kind": objective_kind,
        "duplicate_of": duplicate,
    }


def _history_after_player_settlement(
    planner: Any,
    *,
    owner: MissionOwner,
    current_time: Any,
    extra_writes: Mapping[str, bytes],
) -> tuple[str, bytes] | None:
    team_ref = owner.operation_ref
    if not isinstance(team_ref, str) or not team_ref.startswith("team."):
        return None
    path = _history_path(planner, team_ref)
    existing = extra_writes.get(path)
    if existing is not None:
        try:
            history = json.loads(existing.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CommandRejectedError("team_operational_history_invalid") from exc
    else:
        history = _read_team_history(planner, team_ref)
    if history is None:
        history = {
            "schema": "team-operational-history",
            "team_id": team_ref,
            "as_of": str(current_time),
            "missions_total": 0,
            "missions_succeeded": 0,
            "missions_failed": 0,
            "training_sessions": 0,
            "casualty_events": 0,
            "replacement_events": 0,
            "former_member_refs": [],
            "notable_event_refs": [],
            "last_mission_ref": None,
            "last_result_at": None,
            "recent_mission_refs": [],
        }
    if not isinstance(history, dict):
        raise CommandRejectedError("team_operational_history_invalid")
    recent = _validated_recent_refs(history)
    mission_ref = owner.mission_id
    if mission_ref not in recent:
        for field in ("missions_total", "missions_succeeded", "missions_failed"):
            value = history.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CommandRejectedError("team_operational_history_invalid")
        history["missions_total"] += 1
        if owner.mission.state == "succeeded":
            history["missions_succeeded"] += 1
        else:
            history["missions_failed"] += 1
        recent.append(mission_ref)
    history["recent_mission_refs"] = recent[-_RECENT_MISSION_LIMIT:]
    history["last_mission_ref"] = mission_ref
    history["last_result_at"] = str(current_time)
    history["as_of"] = str(current_time)
    return path, _json_bytes(history)


def install_player_mission_continuity() -> None:
    """Install bounded duplicate prevention and player-team mission history."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_offer = LivingWorldMissionMixin._maybe_offer_player_mission
    if not getattr(original_offer, "_player_mission_continuity", False):
        @wraps(original_offer)
        def offer_wrapped(self: Any, **kwargs: Any) -> Optional[Mapping[str, Any]]:
            duplicate = _duplicate_player_offer(self, **kwargs)
            if duplicate is not None:
                return duplicate
            return original_offer(self, **kwargs)

        offer_wrapped._player_mission_continuity = True
        LivingWorldMissionMixin._maybe_offer_player_mission = offer_wrapped

    original_plan = MissionCommandsMixin._mission_built_plan
    if not getattr(original_plan, "_player_mission_continuity", False):
        @wraps(original_plan)
        def plan_wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
            command = kwargs.get("command")
            owner = kwargs.get("owner")
            current_time = kwargs.get("current_time")
            if (
                command is not None
                and owner is not None
                and current_time is not None
                and command.command_type == "mission_derive_and_settle"
                and command.actor_id in owner.mission.participant_refs
                and owner.mission.state in ("succeeded", "failed")
            ):
                merged = dict(kwargs.get("extra_writes") or {})
                update = _history_after_player_settlement(
                    self,
                    owner=owner,
                    current_time=current_time,
                    extra_writes=merged,
                )
                if update is not None:
                    path, encoded = update
                    if path in merged and merged[path] != encoded:
                        raise CommandRejectedError("mission_extra_write_conflict")
                    merged[path] = encoded
                    kwargs["extra_writes"] = merged
                    material = tuple(kwargs.get("extra_material_consequence_refs") or ())
                    kwargs["extra_material_consequence_refs"] = material + (
                        f"team_mission_history:{owner.operation_ref}:{owner.mission_id}:{owner.mission.state}",
                    )
            return original_plan(self, *args, **kwargs)

        plan_wrapped._player_mission_continuity = True
        MissionCommandsMixin._mission_built_plan = plan_wrapped

    original_autonomous = LivingWorldConsequencesMixin._after_autonomous_mission_result
    if not getattr(original_autonomous, "_player_mission_continuity", False):
        @wraps(original_autonomous)
        def autonomous_wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
            result = original_autonomous(self, *args, **kwargs)
            mission_ref = result.get("mission_id") if isinstance(result, Mapping) else None
            team_ref = result.get("team_ref") if isinstance(result, Mapping) else None
            at = kwargs.get("at")
            record_writes = kwargs.get("record_writes")
            if (
                isinstance(mission_ref, str)
                and isinstance(team_ref, str)
                and at is not None
                and isinstance(record_writes, dict)
            ):
                history = self._team_history(team_ref, at=at, record_writes=record_writes)
                recent = _validated_recent_refs(history)
                if mission_ref not in recent:
                    recent.append(mission_ref)
                history["recent_mission_refs"] = recent[-_RECENT_MISSION_LIMIT:]
            return result

        autonomous_wrapped._player_mission_continuity = True
        LivingWorldConsequencesMixin._after_autonomous_mission_result = autonomous_wrapped

    _INSTALLED = True


__all__ = ["install_player_mission_continuity", "mission_assignment_signature"]
