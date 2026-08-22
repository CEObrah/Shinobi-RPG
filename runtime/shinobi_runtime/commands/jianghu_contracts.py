"""Funded Jianghu contract command implementation.

The public semantic surface remains ``jianghu_contract_resolution``. Escort
contracts may protect cargo, aggregate civilian parties, exact persistent people,
or mixed convoys. Acceptance claims the commission; departure forms the actual
lawful field party from accepted principals plus an already-active standing
travel team. If the physical mission requires more martial coverage, the House
adds deterministic temporary mission staff from ready people at the route origin.
Those reinforcements belong to the contract commitment only and never become
members of the permanent travel team.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.martial_world.civic import civic_person
from shinobi_runtime.martial_world.commitments import reserve_resources
from shinobi_runtime.martial_world.contracts import transition as contract_transition
from shinobi_runtime.martial_world.escort import (
    active_retinue_party, ordinary_public_lot_quantity,
    plan_escort_objective, quote_escort_objective,
)
from shinobi_runtime.martial_world.live_state import roster_person
from shinobi_runtime.martial_world.regional_economy import unit_market_price_cash
from shinobi_runtime.martial_world.retinues import select_mission_escort_reinforcements
from shinobi_runtime.martial_world.scheduler import sync_route_activity
from shinobi_runtime.sim.events import CampaignTime

_CONTRACTS = "state/martial-world/contracts/index.json"
_ROUTE_OPS = "state/martial-world/route-operations.json"
_COMMITMENTS = "state/martial-world/commitments.json"
_DEPLOYMENTS = "state/martial-world/deployments.json"
_SCHEDULE = "state/martial-world/scheduler.json"
_GEOGRAPHY = "game/data/martial-world/geography.json"
_TRAVEL = "game/data/martial-world/travel.json"
_LOCAL_SITES = "game/data/martial-world/local-sites.json"


def _at_iso(value: CampaignTime) -> str:
    return str(value).removeprefix("SE-")


def _route(index: Mapping[str, Any], route_ref: str) -> Mapping[str, Any] | None:
    rows = index.get("routes", []) if isinstance(index, Mapping) else []
    if not isinstance(rows, list):
        return None
    return next((row for row in rows if isinstance(row, Mapping) and row.get("id") == route_ref), None)


def _route_endpoints(
    route: Mapping[str, Any], objective: Mapping[str, Any], geography: Mapping[str, Any]
) -> tuple[str, str]:
    source_place = str(objective.get("source_place_ref") or "")
    destination_place = str(objective.get("destination_place_ref") or "")
    ends = [str(route.get("from") or ""), str(route.get("to") or "")]
    if source_place and destination_place and source_place in ends and destination_place in ends and source_place != destination_place:
        return source_place, destination_place
    places = geography.get("places", {}) if isinstance(geography, Mapping) else {}
    source_region = str(objective.get("source_region") or "")
    destination_region = str(objective.get("destination_region") or "")
    if isinstance(places, Mapping) and source_region and destination_region:
        source_place = next((x for x in ends if isinstance(places.get(x), Mapping) and places[x].get("climate_profile") == source_region), "")
        destination_place = next((x for x in ends if x != source_place and isinstance(places.get(x), Mapping) and places[x].get("climate_profile") == destination_region), "")
    return source_place, destination_place


def _person_location_matches(person: Mapping[str, Any], *, source_place: str, sites: Mapping[str, Any]) -> bool:
    location = str(person.get("location_ref") or "")
    if location == source_place:
        return True
    row = sites.get(location) if isinstance(sites, Mapping) else None
    return isinstance(row, Mapping) and str(row.get("parent_place_ref") or "") == source_place


class JianghuContractCommandsMixin:
    def _resolve_contract_person(self, ref: str) -> tuple[str, dict[str, Any], int, dict[str, Any]]:
        try:
            return roster_person(self.repository, ref)
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            try:
                return civic_person(self.repository, ref)
            except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
                raise CommandRejectedError("jianghu_contract_person_unresolved") from exc

    @staticmethod
    def _person_owner(person: Mapping[str, Any], roster: Mapping[str, Any], ref: str) -> str:
        return str(person.get("faction_ref") or roster.get("faction_ref") or person.get("affiliation_ref") or ref)

    def _jianghu_contract_resolution(
        self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime
    ):
        self._require_jianghu(meta)
        action = str(command.payload.get("action") or "")
        if action not in {"accept", "start"}:
            raise CommandRejectedError("jianghu_contract_action_invalid")
        contract_ref = str(command.payload.get("contract_ref") or "")
        if not contract_ref:
            raise CommandRejectedError("jianghu_contract_invalid")
        index = copy.deepcopy(self.repository.read_json(_CONTRACTS))
        active = index.get("active")
        contract = active.get(contract_ref) if isinstance(active, dict) else None
        if not isinstance(contract, Mapping) or contract.get("contract_type") != "escort":
            raise CommandRejectedError("jianghu_contract_unresolved")
        _path, actor_roster, _ordinal, actor = self._person(command.actor_id)
        faction_ref = str(actor.get("faction_ref") or "")
        if not faction_ref:
            raise CommandRejectedError("jianghu_contract_not_authorized")
        now = datetime(current_time.year,current_time.month,current_time.day,current_time.hour,current_time.minute,current_time.second)
        try:
            if datetime.fromisoformat(str(contract.get("expires_at", ""))) <= now:
                raise CommandRejectedError("jianghu_contract_offer_expired")
        except ValueError as exc:
            raise CommandRejectedError("jianghu_contract_expiry_invalid") from exc

        if action == "accept":
            refs = command.payload.get("participant_refs")
            if (
                not isinstance(refs, (list, tuple)) or not refs
                or any(not isinstance(ref, str) or not ref for ref in refs)
                or len(set(refs)) != len(refs)
                or command.actor_id not in refs
            ):
                raise CommandRejectedError("jianghu_contract_participants_invalid")
            unavailable = self._unavailable_person_refs()
            for ref in refs:
                _p, roster, _i, person = self._resolve_contract_person(ref)
                owner = self._person_owner(person, roster, ref)
                if ref in unavailable or owner != faction_ref:
                    raise CommandRejectedError("jianghu_contract_participant_unavailable")
                health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
                if health.get("status") in {"dead", "incapacitated"} or int(health.get("consciousness", 100)) <= 0:
                    raise CommandRejectedError("jianghu_contract_participant_unavailable")
            if contract.get("status") != "offered" or contract.get("beneficiary_ref") not in {None, ""}:
                raise CommandRejectedError("jianghu_contract_not_offered")
            try:
                updated = contract_transition(
                    contract, at=_at_iso(current_time), to_status="accepted",
                    actor_ref=command.actor_id, participants=[str(x) for x in refs],
                )
            except ValueError as exc:
                raise CommandRejectedError("jianghu_contract_transition_invalid") from exc
            updated["beneficiary_ref"] = faction_ref
            active[contract_ref] = updated
            return self._simple_plan(
                command, meta, current_time, writes_records={_CONTRACTS:index},
                code="jianghu_contract_accepted",
                result={"command_type":command.command_type,"action":"accept","contract_ref":contract_ref,"status":"accepted"},
            )

        if contract.get("status") != "accepted" or contract.get("beneficiary_ref") != faction_ref:
            raise CommandRejectedError("jianghu_contract_not_authorized")
        accepted = [str(x) for x in contract.get("participants", []) if isinstance(x, str)]
        if command.actor_id not in accepted:
            raise CommandRejectedError("jianghu_contract_not_authorized")

        objective = copy.deepcopy(dict(contract.get("objective", {}))) if isinstance(contract.get("objective"), Mapping) else {}
        route_ref = str(objective.get("route_ref") or "")
        geography = self.repository.read_json(_GEOGRAPHY)
        route = _route(geography, route_ref)
        if not isinstance(route, Mapping):
            raise CommandRejectedError("jianghu_contract_route_unresolved")
        source_place, destination_place = _route_endpoints(route, objective, geography)
        if not source_place or not destination_place:
            raise CommandRejectedError("jianghu_contract_route_direction_unresolved")
        travel = self.repository.read_json(_TRAVEL)
        kind = str(objective.get("kind") or "escort_shipment")
        if kind not in {"escort_shipment", "escort_person", "escort_party", "escort_mixed_convoy"}:
            raise CommandRejectedError("jianghu_contract_objective_unsupported")
        item_ref = str(objective.get("item_ref") or "")
        quantity = max(0, int(objective.get("quantity", 0)))
        legacy = max(0, int(objective.get("escort_policy_version", 0))) < 3
        if legacy and item_ref and quantity > 0:
            quantity = ordinary_public_lot_quantity(item_ref, quantity)
        source_region = str(objective.get("source_region") or "")
        destination_region = str(objective.get("destination_region") or "")
        if not source_region or not destination_region:
            places = geography.get("places", {}) if isinstance(geography, Mapping) else {}
            if isinstance(places, Mapping):
                source_region = source_region or str((places.get(source_place) or {}).get("climate_profile") or "")
                destination_region = destination_region or str((places.get(destination_place) or {}).get("climate_profile") or "")
        protected_refs = [str(x) for x in objective.get("protected_person_refs", []) if isinstance(x, str)] if isinstance(objective.get("protected_person_refs"), list) else []
        protected_count = max(len(protected_refs), max(0, int(objective.get("protected_people_count", 0))))
        cargo_value = 0
        if item_ref and quantity > 0 and source_region:
            market = self.repository.read_json(f"state/martial-world/markets/{source_region}.json")
            stock = market.get("stock", {}) if isinstance(market, Mapping) else {}
            try:
                cargo_value = unit_market_price_cash(source_region, item_ref, stock if isinstance(stock, Mapping) else {}) * quantity
            except (KeyError, TypeError, ValueError):
                old_qty=max(1,int(objective.get("quantity",1))); cargo_value=max(0,int(objective.get("cargo_value_cash",0))) * quantity // old_qty
        try:
            normalized = plan_escort_objective(
                kind=kind, route=route, travel=travel,
                source_region=source_region, destination_region=destination_region,
                item_ref=item_ref, quantity=quantity, cargo_value_cash=cargo_value,
                protected_person_refs=protected_refs, protected_people_count=protected_count,
                civilian_party_kind=str(objective.get("civilian_party_kind") or "") or None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandRejectedError("jianghu_contract_objective_unsupported") from exc
        normalized["source_place_ref"] = source_place
        normalized["destination_place_ref"] = destination_place

        quote = quote_escort_objective(normalized)
        computed_reward = max(0, int(quote["total_reward_cash"]))
        escrow_before = max(0, int(contract.get("escrow_cash", 0)))
        funded_reward = min(escrow_before, computed_reward)
        refund = max(0, escrow_before - funded_reward)
        updated_contract = copy.deepcopy(dict(contract))
        updated_contract["objective"] = normalized
        updated_contract["reward_cash"] = funded_reward
        updated_contract["escrow_cash"] = funded_reward
        records: dict[str, Any] = {}
        issuer = str(contract.get("issuer_ref") or "")
        if refund > 0:
            if issuer.startswith("market:"):
                region = issuer.split(":", 1)[1]
                market_path = f"state/martial-world/markets/{region}.json"
                market = copy.deepcopy(self.repository.read_json(market_path))
                market["cash_pool"] = max(0, int(market.get("cash_pool", 0))) + refund
                records[market_path] = market
            elif issuer:
                from shinobi_runtime.martial_world.faction_state import read_faction
                fpath, issuer_faction = read_faction(self.repository, issuer)
                issuer_faction["treasury_cash"] = max(0, int(issuer_faction.get("treasury_cash", 0))) + refund
                records[fpath] = issuer_faction

        deployments = self.repository.read_json(_DEPLOYMENTS)
        core_escort_refs = active_retinue_party(
            deployments, leader_ref=command.actor_id, principals=accepted
        )
        minimum = max(1, int(normalized.get("minimum_escort_count", 1)))

        sites_data = self.repository.read_json(_LOCAL_SITES)
        sites = sites_data.get("sites", {}) if isinstance(sites_data, Mapping) else {}
        if not isinstance(sites, Mapping):
            raise CommandRejectedError("jianghu_local_sites_invalid")
        unavailable = self._unavailable_person_refs()

        temporary_escort_refs: list[str] = []
        needed = max(0, minimum - len(core_escort_refs))
        if needed:
            people = actor_roster.get("people", []) if isinstance(actor_roster, Mapping) else []
            if not isinstance(people, list):
                raise CommandRejectedError("jianghu_contract_escort_count_insufficient")
            origin_people: list[Mapping[str, Any]] = []
            for raw_person in people:
                if not isinstance(raw_person, Mapping):
                    continue
                ref = raw_person.get("person_id")
                if not isinstance(ref, str) or not ref:
                    continue
                try:
                    _person_path, _person_roster, _person_ordinal, person = self._resolve_contract_person(ref)
                except CommandRejectedError:
                    continue
                if _person_location_matches(person, source_place=source_place, sites=sites):
                    origin_people.append(person)
            temporary_escort_refs = select_mission_escort_reinforcements(
                actor,
                origin_people,
                needed_count=needed,
                year=current_time.year,
                unavailable_refs=sorted(unavailable),
                exclude_refs=core_escort_refs,
            )
            if len(temporary_escort_refs) < needed:
                raise CommandRejectedError("jianghu_contract_escort_count_insufficient")

        escort_refs = list(core_escort_refs)
        for ref in temporary_escort_refs:
            if ref not in escort_refs:
                escort_refs.append(ref)
        if len(escort_refs) < minimum:
            raise CommandRejectedError("jianghu_contract_escort_count_insufficient")

        all_people = list(escort_refs)
        for ref in protected_refs:
            if ref not in all_people:
                all_people.append(ref)
        owner_by_ref: dict[str, str] = {}
        faction_people_to_pause: list[str] = []
        for ref in all_people:
            if ref in unavailable:
                raise CommandRejectedError("jianghu_contract_participant_unavailable")
            _p, roster, _i, person = self._resolve_contract_person(ref)
            health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
            if health.get("status") in {"dead", "incapacitated"} or int(health.get("consciousness", 100)) <= 0:
                raise CommandRejectedError("jianghu_contract_participant_unavailable")
            if not _person_location_matches(person, source_place=source_place, sites=sites):
                raise CommandRejectedError("jianghu_contract_participant_not_at_route_origin")
            owner = self._person_owner(person, roster, ref)
            owner_by_ref[ref] = owner
            if owner == faction_ref:
                faction_people_to_pause.append(ref)
        if any(owner_by_ref.get(ref) != faction_ref for ref in escort_refs):
            raise CommandRejectedError("jianghu_contract_escort_wrong_faction")

        if quantity > 0:
            if not source_region or not destination_region or not item_ref:
                raise CommandRejectedError("jianghu_contract_cargo_invalid")
            market_path = f"state/martial-world/markets/{source_region}.json"
            market = copy.deepcopy(records.get(market_path, self.repository.read_json(market_path)))
            stock = market.get("stock")
            if not isinstance(stock, dict) or int(stock.get(item_ref, 0)) < quantity:
                raise CommandRejectedError("jianghu_contract_cargo_unavailable")
            stock[item_ref] = int(stock.get(item_ref, 0)) - quantity
            if stock[item_ref] <= 0:
                stock.pop(item_ref, None)
            records[market_path] = market

        commitments = copy.deepcopy(self.repository.read_json(_COMMITMENTS))
        try:
            commitments = reserve_resources(
                commitments,
                resources=[("person", ref, owner_by_ref[ref]) for ref in all_people],
                actor_ref=command.actor_id, owner_ref=faction_ref,
                activity_ref=contract_ref, activity_kind="contract_escort",
                started_at=_at_iso(current_time), location_ref=source_place,
            )
        except ValueError as exc:
            raise CommandRejectedError("jianghu_contract_participant_committed") from exc

        pause_fpath, paused_faction, pause_path, paused_roster = self._pause_institutional_training_now(
            faction_people_to_pause, current_time,
        )
        route_ops = copy.deepcopy(self.repository.read_json(_ROUTE_OPS))
        movements = route_ops.setdefault("movements", {})
        if not isinstance(movements, dict) or contract_ref in movements:
            raise CommandRejectedError("jianghu_contract_movement_conflict")
        movement = {
            "movement_ref": contract_ref,
            "movement_kind": "escort_contract",
            "contract_ref": contract_ref,
            "objective_kind": kind,
            "route_ref": route_ref,
            "origin_place_ref": source_place,
            "destination_place_ref": destination_place,
            "source_region": source_region,
            "destination_region": destination_region,
            "item_ref": item_ref,
            "quantity": quantity,
            "cargo_value_cash": max(0, int(normalized.get("cargo_value_cash", 0))),
            "beneficiary_ref": faction_ref,
            "escort_refs": escort_refs,
            "core_escort_refs": core_escort_refs,
            "temporary_mission_escort_refs": temporary_escort_refs,
            "protected_person_refs": protected_refs,
            "protected_people_count": protected_count,
            "participant_refs": all_people,
            "started_at": _at_iso(current_time),
            "elapsed_hours": 0,
            "required_hours": max(1, int(normalized.get("expected_travel_hours", 1))),
            "known_escort_count": len(escort_refs),
            "transport_mode": normalized.get("transport_mode"),
            "wagon_count": max(0, int(normalized.get("wagon_count", 0))),
            "pack_animal_count": max(0, int(normalized.get("pack_animal_count", 0))),
            "draft_animal_count": max(0, int(normalized.get("draft_animal_count", 0))),
            "civilian_crew_count": max(0, int(normalized.get("civilian_crew_count", 0))),
            "status": "active",
        }
        movements[contract_ref] = movement
        try:
            started = contract_transition(
                updated_contract, at=_at_iso(current_time), to_status="in_progress",
                actor_ref=command.actor_id, participants=escort_refs,
            )
        except ValueError as exc:
            raise CommandRejectedError("jianghu_contract_transition_invalid") from exc
        started_objective = copy.deepcopy(normalized)
        started_objective["cargo_committed"] = quantity > 0
        started["objective"] = started_objective
        active[contract_ref] = started
        schedule = copy.deepcopy(self.repository.read_json(_SCHEDULE))
        active_route_ids = [
            str(row.get("route_ref")) for row in movements.values()
            if isinstance(row, Mapping) and row.get("status", "active") == "active" and isinstance(row.get("route_ref"), str)
        ]
        try:
            schedule = sync_route_activity(schedule, active_route_ids=active_route_ids, now=now)
        except ValueError as exc:
            raise CommandRejectedError("jianghu_scheduler_invalid") from exc
        records.update({
            _CONTRACTS:index, _ROUTE_OPS:route_ops, _COMMITMENTS:commitments, _SCHEDULE:schedule,
            pause_fpath:paused_faction, pause_path:paused_roster,
        })
        return self._simple_plan(
            command, meta, current_time, writes_records=records,
            code="jianghu_contract_started",
            result={
                "command_type":command.command_type,"action":"start","contract_ref":contract_ref,
                "status":"in_progress","escort_refs":escort_refs,"core_escort_refs":core_escort_refs,
                "temporary_mission_escort_refs":temporary_escort_refs,
                "protected_person_refs":protected_refs,
                "protected_people_count":protected_count,"minimum_escort_count":minimum,
                "reward_cash":funded_reward,"escrow_refunded_cash":refund,
            },
        )


__all__ = ["JianghuContractCommandsMixin"]
