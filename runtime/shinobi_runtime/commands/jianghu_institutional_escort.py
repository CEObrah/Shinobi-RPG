"""Escort-specific institutional command integration.

Institutional escort plans own an exact approved House roster, while the shared
route engine owns physical travel. Dispatching an approved escort therefore
musters that exact roster to the linked contract origin; it does not start the
contract, teleport anyone, or replace named temporary escorts.
"""
from __future__ import annotations

import copy
import hashlib
from datetime import datetime
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.jianghu_institutional import _operation_state
from shinobi_runtime.martial_world.commitments import derived_commitment_state, reserve_resources
from shinobi_runtime.martial_world.escort import hydrate_contract_escort_objective
from shinobi_runtime.martial_world.faction_state import inventory_path, read_faction
from shinobi_runtime.martial_world.frontier_support import person_place
from shinobi_runtime.martial_world.institutional_operations import OPERATIONS_PATH
from shinobi_runtime.martial_world.physical_travel import build_route_journey, stage_route_journey
from shinobi_runtime.martial_world.travel import travel_plan
from shinobi_runtime.martial_world.travel_provisions import provisioning_journey_seconds, reserve_faction_rations

_CONTRACTS = "state/martial-world/contracts/index.json"
_ROUTE_OPERATIONS = "state/martial-world/route-operations.json"
_SCHEDULE = "state/martial-world/scheduler.json"
_LOCAL_SITES = "game/data/martial-world/local-sites.json"
_GEOGRAPHY = "game/data/martial-world/geography.json"
_TRAVEL = "game/data/martial-world/travel.json"


def _iso(time: Any) -> str:
    return str(time).removeprefix("SE-")


def _route_muster_ref(operation_ref: str, contract_ref: str) -> str:
    digest = hashlib.sha256(f"{operation_ref}|{contract_ref}".encode("utf-8")).hexdigest()[:20]
    return f"escort_muster:{digest}"


class JianghuInstitutionalEscortCommandsMixin:
    """Specialize approved escort dispatch without duplicating other missions."""

    def _jianghu_institutional_operation_resolution(
        self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: Any,
    ):
        action = str(command.payload.get("action") or "")
        op_ref = str(command.payload.get("operation_ref") or "")
        state = _operation_state(self.repository)
        active = state.get("active", {}) if isinstance(state, Mapping) else {}
        row = active.get(op_ref) if isinstance(active, Mapping) else None

        if (
            action == "cancel"
            and isinstance(row, Mapping)
            and str(row.get("mission_kind") or "") == "escort"
            and str(row.get("phase") or "") == "mustering"
        ):
            movement_ref = str(row.get("muster_movement_ref") or "")
            try:
                route_state = self.repository.read_json(_ROUTE_OPERATIONS)
            except FileNotFoundError:
                route_state = {}
            movements = route_state.get("movements", {}) if isinstance(route_state, Mapping) else {}
            if movement_ref and isinstance(movements, Mapping) and movement_ref in movements:
                raise CommandRejectedError("jianghu_institutional_in_field_cannot_paper_cancel")

        if not (
            action == "dispatch"
            and isinstance(row, Mapping)
            and str(row.get("mission_kind") or "") == "escort"
        ):
            return super()._jianghu_institutional_operation_resolution(command, meta, current_time)

        self._require_jianghu(meta)
        if str(row.get("phase") or "") != "approved":
            raise CommandRejectedError("jianghu_institutional_operation_not_approved")

        now_iso = _iso(current_time)
        _actor_path, _actor_roster, _actor_ordinal, actor = self._institutional_person(command.actor_id)
        actor_faction = str(actor.get("faction_ref") or "")
        if not actor_faction or str(row.get("faction_ref") or "") != actor_faction:
            raise CommandRejectedError("jianghu_institutional_wrong_faction")

        member_refs = [str(ref) for ref in row.get("participant_refs", []) if isinstance(ref, str) and ref]
        commander_ref = str(row.get("commander_ref") or "")
        if (
            not member_refs
            or len(set(member_refs)) != len(member_refs)
            or commander_ref not in member_refs
            or command.actor_id not in member_refs
            or str(row.get("operation_kind") or "") != "escort_contract"
        ):
            raise CommandRejectedError("jianghu_institutional_plan_invalid")

        linked_contract = str(row.get("linked_contract_ref") or "")
        if not linked_contract:
            raise CommandRejectedError("jianghu_institutional_escort_requires_contract")
        index = self.repository.read_json(_CONTRACTS)
        contract_rows = index.get("active", {}) if isinstance(index, Mapping) else {}
        contract = contract_rows.get(linked_contract) if isinstance(contract_rows, Mapping) else None
        if (
            not isinstance(contract, Mapping)
            or contract.get("contract_type") != "escort"
            or contract.get("status") != "accepted"
            or str(contract.get("beneficiary_ref") or "") != actor_faction
        ):
            raise CommandRejectedError("jianghu_institutional_escort_contract_not_accepted")

        try:
            objective = hydrate_contract_escort_objective(
                contract.get("objective", {}) if isinstance(contract.get("objective"), Mapping) else {},
                geography=self.repository.read_json(_GEOGRAPHY),
                travel=self.repository.read_json(_TRAVEL),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandRejectedError("jianghu_institutional_escort_contract_objective_invalid") from exc
        contract_origin = str(objective.get("source_place_ref") or "")
        if not contract_origin:
            raise CommandRejectedError("jianghu_institutional_route_unresolved")
        accepted_refs = [str(ref) for ref in contract.get("participants", []) if isinstance(ref, str) and ref]
        if (
            not accepted_refs
            or command.actor_id not in accepted_refs
            or not set(accepted_refs).issubset(set(member_refs))
        ):
            raise CommandRejectedError("jianghu_institutional_escort_roster_missing_principal")
        if len(member_refs) < max(1, int(objective.get("minimum_escort_count", 1))):
            raise CommandRejectedError("jianghu_institutional_escort_roster_below_contract_minimum")

        faction_path, faction = read_faction(self.repository, actor_faction)
        sites_doc = self.repository.read_json(_LOCAL_SITES)
        member_places: set[str] = set()
        for ref in member_refs:
            self._require_person_available_for_activity(ref, "jianghu_institutional_member_unavailable")
            _path, _roster, _ordinal, person = self._institutional_person(ref)
            if str(person.get("faction_ref") or "") != actor_faction:
                raise CommandRejectedError("jianghu_institutional_member_wrong_faction")
            health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
            if health.get("status") in {"dead", "incapacitated"} or int(health.get("consciousness", 100)) <= 0:
                raise CommandRejectedError("jianghu_institutional_member_unavailable")
            member_places.add(person_place(
                person,
                local_sites=sites_doc,
                home_place=str(faction.get("headquarters") or ""),
                home_site_ref=str(faction.get("local_site_ref") or ""),
            ))
        if len(member_places) != 1:
            raise CommandRejectedError("jianghu_institutional_members_not_colocated")
        muster_origin = next(iter(member_places))
        if not muster_origin:
            raise CommandRejectedError("jianghu_institutional_route_unresolved")

        row = copy.deepcopy(dict(row))
        row["phase"] = "mustering"
        row["muster_source_place_ref"] = muster_origin
        row["muster_destination_place_ref"] = contract_origin
        row["updated_at"] = now_iso

        if muster_origin == contract_origin:
            row["mustered_at"] = now_iso
            active[op_ref] = row
            return self._simple_plan(
                command, meta, current_time,
                writes_records={OPERATIONS_PATH: state},
                code="jianghu_institutional_escort_muster_ready",
                result={
                    "command_type": command.command_type,
                    "operation_ref": op_ref,
                    "contract_ref": linked_contract,
                    "commander_ref": commander_ref,
                    "member_count": len(member_refs),
                    "muster_place_ref": contract_origin,
                },
            )

        at = datetime.fromisoformat(now_iso)
        try:
            route_plan = travel_plan(
                world_seed=str(meta.get("world_seed") or "jianghu-world"),
                start_at=at,
                start=muster_origin,
                end=contract_origin,
                mode="foot",
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandRejectedError("jianghu_institutional_route_unresolved") from exc

        commitments = derived_commitment_state(self.repository.read_json)
        movement_ref = _route_muster_ref(op_ref, linked_contract)
        try:
            reserve_resources(
                commitments,
                resources=[("person", ref, actor_faction) for ref in member_refs],
                actor_ref=commander_ref,
                owner_ref=actor_faction,
                activity_ref=movement_ref,
                activity_kind="escort_muster_travel",
                started_at=now_iso,
                location_ref=muster_origin,
            )
        except ValueError as exc:
            raise CommandRejectedError("jianghu_institutional_members_unavailable") from exc

        ipath = inventory_path(actor_faction)
        try:
            inventory = self.repository.read_json(ipath)
            inventory_after, provision_reservation = reserve_faction_rations(
                inventory,
                faction_ref=actor_faction,
                participant_count=len(member_refs),
                travel_seconds=provisioning_journey_seconds(route_plan),
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
            raise CommandRejectedError("jianghu_institutional_travel_provisions_insufficient") from exc

        toll = max(0, int(route_plan.get("toll_cash", 0)))
        if max(0, int(faction.get("treasury_cash", 0))) < toll:
            raise CommandRejectedError("jianghu_institutional_travel_toll_cash_insufficient")

        geography = self.repository.read_json(_GEOGRAPHY)
        places = geography.get("places", {}) if isinstance(geography, Mapping) else {}
        staged_markets: dict[str, Mapping[str, Any]] = {}
        nodes = list(route_plan.get("nodes", []))
        credited_toll = 0
        for i, segment in enumerate(route_plan.get("segments", [])):
            amount = max(0, int(segment.get("toll_cash", 0))) if isinstance(segment, Mapping) else 0
            if amount <= 0:
                continue
            if i >= len(nodes):
                raise CommandRejectedError("jianghu_institutional_travel_toll_destination_unresolved")
            place = places.get(str(nodes[i])) if isinstance(places, Mapping) else None
            region = str(place.get("climate_profile") or "") if isinstance(place, Mapping) else ""
            if not region:
                raise CommandRejectedError("jianghu_institutional_travel_toll_destination_unresolved")
            market_path = f"state/martial-world/markets/{region}.json"
            try:
                market = copy.deepcopy(staged_markets.get(market_path) or self.repository.read_json(market_path))
            except FileNotFoundError as exc:
                raise CommandRejectedError("jianghu_institutional_travel_toll_destination_unresolved") from exc
            if not isinstance(market, Mapping) or market.get("region_id") not in (None, region):
                raise CommandRejectedError("jianghu_institutional_travel_toll_destination_unresolved")
            market = copy.deepcopy(dict(market))
            market["cash_pool"] = max(0, int(market.get("cash_pool", 0))) + amount
            staged_markets[market_path] = market
            credited_toll += amount
        if credited_toll != toll:
            raise CommandRejectedError("jianghu_institutional_travel_toll_conservation_failure")

        try:
            movement = build_route_journey(
                movement_ref=movement_ref,
                movement_kind="player_strategic_travel",
                purpose_ref=movement_ref,
                plan=route_plan,
                participants=member_refs,
                leader_ref=commander_ref,
                beneficiary_ref=actor_faction,
                started_at=at,
                mode="foot",
                extra={
                    "institutional_operation_ref": op_ref,
                    "linked_contract_ref": linked_contract,
                    "escort_muster": True,
                    "lodging_payer_kind": "faction",
                    "lodging_payer_ref": actor_faction,
                    "provision_reservation": provision_reservation,
                },
            )
            route_after, schedule_after = stage_route_journey(
                route_state=self.repository.read_json(_ROUTE_OPERATIONS),
                schedule=self.repository.read_json(_SCHEDULE),
                movement_ref=movement_ref,
                movement=movement,
                now=at,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandRejectedError("jianghu_route_operation_invalid") from exc

        pause_fpath, paused_faction, pause_rpath, paused_roster = self._pause_institutional_training_now(
            member_refs, current_time,
        )
        if pause_fpath != faction_path:
            raise CommandRejectedError("jianghu_institutional_faction_state_invalid")
        paused_faction = copy.deepcopy(dict(paused_faction))
        paused_faction["treasury_cash"] = max(0, int(paused_faction.get("treasury_cash", 0))) - toll

        row["muster_movement_ref"] = movement_ref
        row["muster_dispatched_at"] = now_iso
        row["estimated_muster_hours"] = max(1, int(float(route_plan.get("travel_hours", 0.0)) + 0.999999))
        active[op_ref] = row
        writes: dict[str, Any] = {
            OPERATIONS_PATH: state,
            _ROUTE_OPERATIONS: route_after,
            _SCHEDULE: schedule_after,
            ipath: inventory_after,
            pause_fpath: paused_faction,
            pause_rpath: paused_roster,
            **staged_markets,
        }
        return self._simple_plan(
            command, meta, current_time,
            writes_records=writes,
            code="jianghu_institutional_escort_muster_dispatched",
            result={
                "command_type": command.command_type,
                "operation_ref": op_ref,
                "contract_ref": linked_contract,
                "movement_ref": movement_ref,
                "commander_ref": commander_ref,
                "member_count": len(member_refs),
                "muster_origin_place_ref": muster_origin,
                "muster_destination_place_ref": contract_origin,
                "travel_hours": row["estimated_muster_hours"],
                "toll_cash": toll,
            },
        )


__all__ = ["JianghuInstitutionalEscortCommandsMixin"]
