"""Delegate one offered player-led team mission to the team's NPC detachment.

The exact team owner remains unchanged. The command narrows only this mission's
participant set, conserves the player's relinquished escrowed reward, moves the
mission from player-wake handling into existing faction autonomy, and records a
bounded report-back route in faction operational memory.
"""
from __future__ import annotations

import copy
from functools import wraps
from typing import Any, Dict, Mapping, Optional

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _OwnerResolutionCache, _BuiltPlan, _exact_payload, _json_bytes, _stable_id
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.living_world_consequences import LivingWorldConsequencesMixin
from shinobi_runtime.commands.mission_owner import MissionOwner
from shinobi_runtime.commands.paths import INVENTORY_REGISTRY_PATH
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.reducers.missions import MissionTransitionError, transition_mission
from shinobi_runtime.sim.events import CampaignTime

_COMMAND = "mission_delegation_resolution"
_MAX_REPORT_ROUTES = 32
_INSTALLED = False


def _faction_owner_for_write(self: Any, faction_ref: str) -> tuple[str, Dict[str, Any]]:
    try:
        path, _digest, view = self._resolve_covered_owner_view(
            faction_ref,
            cache=_OwnerResolutionCache(),
        )
    except CommandRejectedError as exc:
        raise CommandRejectedError("mission_delegation_issuer_invalid") from exc
    try:
        record = copy.deepcopy(self.repository.read_json(path))
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("mission_delegation_issuer_invalid") from exc
    faction = record.get("faction") if isinstance(record, dict) else None
    if (
        not isinstance(view, Mapping)
        or record.get("schema") != "faction-owner"
        or not isinstance(faction, dict)
        or faction.get("id") != faction_ref
    ):
        raise CommandRejectedError("mission_delegation_issuer_invalid")
    return path, record


def _refund_player_reward(
    self: Any,
    *,
    owner: MissionOwner,
    player_ref: str,
) -> tuple[dict[str, Any], tuple[Mapping[str, Any], ...], int]:
    try:
        inventory = copy.deepcopy(self.repository.read_json(INVENTORY_REGISTRY_PATH))
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("mission_delegation_escrow_invalid") from exc
    holders = inventory.get("holders") if isinstance(inventory, dict) else None
    funding = holders.get(owner.funding_holder_ref) if isinstance(holders, dict) else None
    escrow = holders.get(owner.escrow_holder_ref) if isinstance(holders, dict) else None
    if (
        not isinstance(holders, dict)
        or not isinstance(funding, dict)
        or not isinstance(escrow, dict)
        or not isinstance(owner.escrow_holder_ref, str)
    ):
        raise CommandRejectedError("mission_delegation_escrow_invalid")

    kept: list[Mapping[str, Any]] = []
    refund = 0
    player_term_seen = False
    for term in owner.mission.settlement_terms:
        row = term.to_record()
        if row.get("account_ref") != player_ref:
            kept.append(row)
            continue
        player_term_seen = True
        if (
            row.get("direction") != "reward"
            or row.get("asset_ref") != "currency.ryo"
            or isinstance(row.get("quantity"), bool)
            or not isinstance(row.get("quantity"), int)
            or row.get("quantity") <= 0
        ):
            raise CommandRejectedError("mission_delegation_player_term_invalid")
        refund += int(row["quantity"])
    if not player_term_seen or refund <= 0:
        raise CommandRejectedError("mission_delegation_player_reward_missing")

    escrow_balance = escrow.get("currency.ryo", 0)
    funding_balance = funding.get("currency.ryo", 0)
    if (
        isinstance(escrow_balance, bool)
        or not isinstance(escrow_balance, int)
        or escrow_balance < refund
        or isinstance(funding_balance, bool)
        or not isinstance(funding_balance, int)
        or funding_balance < 0
    ):
        raise CommandRejectedError("mission_delegation_escrow_invalid")
    escrow["currency.ryo"] = escrow_balance - refund
    funding["currency.ryo"] = funding_balance + refund
    if escrow["currency.ryo"] == 0:
        holders.pop(owner.escrow_holder_ref, None)
    return inventory, tuple(kept), refund


def _plan_mission_delegation(
    self: Any,
    command: CommandEnvelope,
    meta: Mapping[str, Any],
    current_time: CampaignTime,
) -> _BuiltPlan:
    _exact_payload(
        command.payload,
        ("mission_id", "delegate_leader_ref"),
        command.command_type,
    )
    mission_id = _stable_id(
        command.payload.get("mission_id"),
        "mission_id_invalid",
        prefix="mission.",
    )
    delegate_ref = _stable_id(
        command.payload.get("delegate_leader_ref"),
        "mission_delegate_leader_invalid",
    )
    path, owner = self._read_mission(
        mission_id,
        actor_id=command.actor_id,
        current_time=current_time,
    )
    if owner.mission.state != "offered":
        raise CommandRejectedError("mission_delegation_requires_offered_mission")
    team_ref = owner.operation_ref
    if not isinstance(team_ref, str) or not team_ref.startswith("team."):
        raise CommandRejectedError("mission_delegation_team_invalid")
    _team_path, team = self._exact_team(team_ref)
    members = team.get("member_refs") if isinstance(team, Mapping) else None
    if (
        team.get("status") != "active"
        or team.get("leader_ref") != command.actor_id
        or team.get("deputy_ref") != delegate_ref
        or not isinstance(members, list)
        or command.actor_id not in members
        or delegate_ref not in members
    ):
        raise CommandRejectedError("mission_delegation_not_authorized")

    current_participants = set(owner.mission.participant_refs)
    delegated = tuple(
        ref
        for ref in members
        if isinstance(ref, str)
        and ref != command.actor_id
        and ref in current_participants
    )
    if (
        len(delegated) < 2
        or delegate_ref not in delegated
        or current_participants != set(delegated) | {command.actor_id}
    ):
        raise CommandRejectedError("mission_delegation_participants_invalid")

    for person_ref in delegated:
        profile = self._living_member_profile(person_ref, record_writes={})
        if profile is None or profile.available is not True:
            raise CommandRejectedError("mission_delegation_member_unavailable")

    inventory, kept_terms, refund = _refund_player_reward(
        self,
        owner=owner,
        player_ref=command.actor_id,
    )
    delegated_record = dict(owner.to_record())
    delegated_record["participant_refs"] = list(delegated)
    delegated_record["settlement_terms"] = [dict(row) for row in kept_terms]
    try:
        delegated_owner = MissionOwner.from_record(delegated_record)
        accepted = transition_mission(delegated_owner.mission, "accepted")
        active = transition_mission(accepted, "active")
        delegated_owner = delegated_owner.with_mission(active, effective_at=current_time)
    except (MissionTransitionError, TypeError, ValueError) as exc:
        raise CommandRejectedError("mission_delegation_transition_invalid") from exc

    faction_path, faction_record = _faction_owner_for_write(self, owner.issuer_ref)
    faction = faction_record["faction"]
    plan_state = faction.get("plan_state")
    if not isinstance(plan_state, dict):
        raise CommandRejectedError("mission_delegation_issuer_invalid")
    wake_refs = plan_state.get("wake_required_mission_refs")
    autonomous_refs = plan_state.get("autonomous_mission_refs")
    if not isinstance(wake_refs, list) or not isinstance(autonomous_refs, list):
        raise CommandRejectedError("mission_delegation_issuer_invalid")
    if mission_id not in wake_refs:
        raise CommandRejectedError("mission_delegation_wake_route_missing")
    plan_state["wake_required_mission_refs"] = [ref for ref in wake_refs if ref != mission_id]
    if mission_id not in autonomous_refs:
        autonomous_refs.append(mission_id)
        autonomous_refs.sort()

    record_writes: Dict[str, Dict[str, Any]] = {}
    memory = self._faction_memory(
        owner.issuer_ref,
        at=current_time,
        record_writes=record_writes,
    )
    active_team_refs = memory.get("active_mission_team_refs")
    if not isinstance(active_team_refs, dict):
        raise CommandRejectedError("faction_operational_memory_invalid")
    active_team_refs[mission_id] = team_ref
    routes = memory.setdefault("delegated_player_mission_reports", [])
    if (
        not isinstance(routes, list)
        or len(routes) >= _MAX_REPORT_ROUTES
        or any(not isinstance(row, Mapping) for row in routes)
        or any(row.get("mission_id") == mission_id for row in routes)
    ):
        raise CommandRejectedError("mission_delegation_report_route_invalid")
    routes.append(
        {
            "mission_id": mission_id,
            "recipient_ref": command.actor_id,
            "delegate_leader_ref": delegate_ref,
            "participant_refs": list(delegated),
            "ordered_at": str(current_time),
        }
    )
    memory_path = self._faction_memory_path(owner.issuer_ref)

    extra_writes = {
        INVENTORY_REGISTRY_PATH: _json_bytes(inventory),
        faction_path: _json_bytes(faction_record),
        memory_path: _json_bytes(memory),
    }
    consequence_refs = (
        f"mission_delegated:{mission_id}:{command.actor_id}->{delegate_ref}",
        f"mission_participants:{mission_id}:{','.join(sorted(delegated))}",
        f"mission_reward_refund:currency.ryo:{refund}:{owner.escrow_holder_ref}->{owner.funding_holder_ref}",
        f"mission_report_back:{mission_id}:{command.actor_id}",
    )
    return self._mission_built_plan(
        command=command,
        meta=meta,
        current_time=current_time,
        path=path,
        owner=delegated_owner,
        code="mission_delegation_resolution_ready",
        summary=(
            f"Mission {mission_id} is accepted for the delegated {team_ref} detachment under "
            f"{delegate_ref}; {command.actor_id} remains outside the mission participant set."
        ),
        result={
            "command_type": command.command_type,
            "mission_id": mission_id,
            "state": delegated_owner.mission.state,
            "team_ref": team_ref,
            "delegate_leader_ref": delegate_ref,
            "participant_refs": list(delegated),
            "report_back_to_ref": command.actor_id,
            "relinquished_reward_ryo": refund,
            "autonomy_route": "faction_operational_mission",
        },
        extra_writes=extra_writes,
        extra_material_consequence_refs=consequence_refs,
    )


def _delegated_report_after_result(original: Any):
    @wraps(original)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
        result = original(self, *args, **kwargs)
        if not isinstance(result, Mapping):
            return result
        mission_id = result.get("mission_id")
        faction_id = kwargs.get("faction_id")
        at = kwargs.get("at")
        command = kwargs.get("command")
        world_events = kwargs.get("world_events")
        record_writes = kwargs.get("record_writes")
        if (
            not isinstance(mission_id, str)
            or not isinstance(faction_id, str)
            or not isinstance(at, CampaignTime)
            or not isinstance(command, CommandEnvelope)
            or not isinstance(world_events, dict)
            or not isinstance(record_writes, dict)
        ):
            return result
        memory = self._faction_memory(
            faction_id,
            at=at,
            record_writes=record_writes,
        )
        routes = memory.get("delegated_player_mission_reports", [])
        if not isinstance(routes, list):
            raise CommandRejectedError("mission_delegation_report_route_invalid")
        route = next(
            (
                row
                for row in routes
                if isinstance(row, Mapping) and row.get("mission_id") == mission_id
            ),
            None,
        )
        if not isinstance(route, Mapping):
            return result
        recipient_ref = route.get("recipient_ref")
        delegate_ref = route.get("delegate_leader_ref")
        participants = route.get("participant_refs")
        if (
            not isinstance(recipient_ref, str)
            or not isinstance(delegate_ref, str)
            or not isinstance(participants, list)
            or any(not isinstance(ref, str) for ref in participants)
        ):
            raise CommandRejectedError("mission_delegation_report_route_invalid")
        routes[:] = [
            row
            for row in routes
            if not (isinstance(row, Mapping) and row.get("mission_id") == mission_id)
        ]
        report_event_id = self._append_internal_event(
            world_events,
            command=command,
            identity=f"{mission_id}:{recipient_ref}:{at}:delegated-report",
            kind="delegated_mission_report_received",
            at=at,
            host_refs=tuple(
                ref
                for ref in (mission_id, result.get("team_ref"), faction_id)
                if isinstance(ref, str)
            ),
            actor_refs=tuple(participants),
            affected_owner_refs=(self._faction_memory_path(faction_id),),
            material_consequence_refs=(
                mission_id,
                f"mission_outcome:{result.get('outcome')}",
                f"report_to:{recipient_ref}",
            ),
            classification="restricted",
            audience_refs=(recipient_ref,),
            source_refs=tuple(
                ref
                for ref in (result.get("report_event_id"), result.get("event_id"))
                if isinstance(ref, str)
            ),
            reducer_ref="shinobi_runtime.commands.player_mission_delegation",
        )
        enriched = dict(result)
        enriched["delegated_mission_report"] = {
            "mission_id": mission_id,
            "recipient_ref": recipient_ref,
            "delegate_leader_ref": delegate_ref,
            "participant_refs": list(participants),
            "outcome": result.get("outcome"),
            "reported_at": str(at),
            "report_event_id": report_event_id,
            "routine_consequences": result.get("routine_consequences"),
            "logistics": result.get("logistics"),
        }
        return enriched

    wrapped._player_mission_delegation_report = True  # type: ignore[attr-defined]
    return wrapped


def install_player_mission_delegation() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_player_handoffs as module

    COMMAND_SPECS.setdefault(
        _COMMAND,
        CommandSpec(
            ("mission_id", "delegate_leader_ref"),
            (),
            "Accept one offered player-led exact-team mission for the current non-player detachment under the team's registered deputy, conserve the player's relinquished reward, route the mission into existing faction autonomy, and report the result back to the player.",
            {
                "mission_id": "mission.<id>",
                "delegate_leader_ref": "current exact-team deputy person ref",
            },
        ),
    )
    planner = module.CampaignCommandPlanner
    setattr(planner, "_" + _COMMAND, _plan_mission_delegation)
    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)

    original = LivingWorldConsequencesMixin._after_autonomous_mission_result
    if not getattr(original, "_player_mission_delegation_report", False):
        LivingWorldConsequencesMixin._after_autonomous_mission_result = _delegated_report_after_result(original)

    _INSTALLED = True


__all__ = ["install_player_mission_delegation"]
