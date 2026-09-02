"""Funded Jianghu contract command implementation.

The public semantic surface remains ``jianghu_contract_resolution``. Escort
contracts may protect cargo, exact persistent people, or mixed convoys. Aggregate
civilian parties become exact civic people at departure because route danger can
physically affect each traveler. Acceptance claims the commission; departure forms the actual
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
from shinobi_runtime.martial_world.civic import civic_person, hydrate_civic_person
from shinobi_runtime.martial_world.aggregate_transport import civilian_available_capacity, freight_crew_required, make_transport_reservation
from shinobi_runtime.martial_world.commitments import derived_commitment_state, reserve_resources
from shinobi_runtime.martial_world.contracts import transition as contract_transition
from shinobi_runtime.martial_world.contract_escort_rosters import approved_contract_escort_roster
from shinobi_runtime.martial_world.escort import (
    active_retinue_party, ordinary_public_lot_quantity,
    compact_started_escort_objective, hydrate_contract_escort_objective, materialize_civilian_identities,
    plan_escort_journey_objective,
)
from shinobi_runtime.martial_world.faction_state import inventory_path
from shinobi_runtime.martial_world.live_state import roster_person
from shinobi_runtime.martial_world.institutional_operations import ensure_contract_dossier, stage_institutional_phase
from shinobi_runtime.martial_world.regional_economy import unit_market_price_cash
from shinobi_runtime.martial_world.scheduler import prune_contract_expiry_events, route_ids_needing_service, sync_route_activity
from shinobi_runtime.martial_world.travel import travel_plan
from shinobi_runtime.martial_world.physical_travel import build_route_journey
from shinobi_runtime.martial_world.travel_provisions import planned_journey_seconds, provisioning_journey_seconds, reserve_faction_rations
from shinobi_runtime.sim.events import CampaignTime

_CONTRACTS = "state/martial-world/contracts/index.json"
_ROUTE_OPS = "state/martial-world/route-operations.json"
_DEPLOYMENTS = "state/martial-world/deployments.json"
_SCHEDULE = "state/martial-world/scheduler.json"
_GEOGRAPHY = "game/data/martial-world/geography.json"
_TRAVEL = "game/data/martial-world/travel.json"
_LOCAL_SITES = "game/data/martial-world/local-sites.json"
_CIVILIANS = "state/martial-world/civilian-populations.json"
_CIVIC_PEOPLE = "state/martial-world/civic-people.json"


def _at_iso(value: CampaignTime) -> str:
    return str(value).removeprefix("SE-")


def _route(index: Mapping[str, Any], route_ref: str) -> Mapping[str, Any] | None:
    rows = index.get("routes", []) if isinstance(index, Mapping) else []
    if not isinstance(rows, list):
        return None
    return next((row for row in rows if isinstance(row, Mapping) and row.get("id") == route_ref), None)



class JianghuContractCommandsMixin:
    def _contract_person_exact_location(self, ref: str, person: Mapping[str, Any]) -> str:
        return str(self._effective_person_location(ref, person) or "")

    @staticmethod
    def _contract_location_is_at_source(location: str, *, source_place: str, sites: Mapping[str, Any]) -> bool:
        if location == source_place:
            return True
        row = sites.get(location) if isinstance(sites, Mapping) else None
        return isinstance(row, Mapping) and str(row.get("parent_place_ref") or "") == source_place

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
            deployments = self.repository.read_json(_DEPLOYMENTS)
            player_controlled_principals = set(active_retinue_party(
                deployments if isinstance(deployments, Mapping) else {},
                leader_ref=command.actor_id, principals=[command.actor_id],
            ))
            if any(ref not in player_controlled_principals for ref in refs):
                raise CommandRejectedError("jianghu_contract_participant_not_under_player_authority")
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
            records = {_CONTRACTS: index}
            ensure_contract_dossier(
                read_json=self.repository.read_json, writes=records, contract_ref=contract_ref,
                faction_ref=faction_ref, actor_ref=command.actor_id, at_iso=_at_iso(current_time), phase="accepted",
                participant_refs=[str(x) for x in refs], objective="Fulfill the funded escort commission", issuer_ref=str(contract.get("issuer_ref") or ""),
            )
            return self._simple_plan(
                command, meta, current_time, writes_records=records,
                code="jianghu_contract_accepted",
                result={"command_type":command.command_type,"action":"accept","contract_ref":contract_ref,"status":"accepted"},
            )

        if contract.get("status") != "accepted" or contract.get("beneficiary_ref") != faction_ref:
            raise CommandRejectedError("jianghu_contract_not_authorized")
        accepted = [str(x) for x in contract.get("participants", []) if isinstance(x, str)]
        if command.actor_id not in accepted:
            raise CommandRejectedError("jianghu_contract_not_authorized")

        raw_objective = copy.deepcopy(dict(contract.get("objective", {}))) if isinstance(contract.get("objective"), Mapping) else {}
        geography = self.repository.read_json(_GEOGRAPHY)
        travel = self.repository.read_json(_TRAVEL)
        try:
            objective = hydrate_contract_escort_objective(raw_objective, geography=geography, travel=travel)
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandRejectedError("jianghu_contract_objective_unsupported") from exc
        source_place = str(objective.get("source_place_ref") or "")
        destination_place = str(objective.get("destination_place_ref") or "")
        if not source_place or not destination_place or source_place == destination_place:
            raise CommandRejectedError("jianghu_contract_route_direction_unresolved")
        kind = str(objective.get("kind") or "escort_shipment")
        if kind not in {"escort_shipment", "escort_person", "escort_party", "escort_mixed_convoy"}:
            raise CommandRejectedError("jianghu_contract_objective_unsupported")
        item_ref = str(objective.get("item_ref") or "")
        quantity = max(0, int(objective.get("quantity", 0)))
        if item_ref and quantity > 0:
            quantity = ordinary_public_lot_quantity(item_ref, quantity)
        places = geography.get("places", {}) if isinstance(geography, Mapping) else {}
        source_region = str((places.get(source_place) or {}).get("climate_profile") or "") if isinstance(places, Mapping) else ""
        destination_region = str((places.get(destination_place) or {}).get("climate_profile") or "") if isinstance(places, Mapping) else ""
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
            normalized = plan_escort_journey_objective(
                kind=kind, geography=geography, travel=travel,
                source_place_ref=source_place, destination_place_ref=destination_place,
                item_ref=item_ref, quantity=quantity, cargo_value_cash=cargo_value,
                protected_person_refs=protected_refs, protected_people_count=protected_count,
                civilian_party_kind=str(objective.get("civilian_party_kind") or "") or None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandRejectedError("jianghu_contract_objective_unsupported") from exc
        route_refs = [str(x) for x in normalized.get("route_refs", []) if isinstance(x, str)]
        places_crossed = [str(x) for x in normalized.get("places_crossed", []) if isinstance(x, str)]
        if not route_refs or len(places_crossed) != len(route_refs) + 1:
            raise CommandRejectedError("jianghu_contract_route_unresolved")
        route_ref = route_refs[0]
        first_route = _route(geography, route_ref)
        if not isinstance(first_route, Mapping):
            raise CommandRejectedError("jianghu_contract_route_unresolved")

        # Reward and escrow are contractual terms fixed when the funded offer is
        # created. Departure may derive a different current staffing/logistics
        # requirement, but it must not silently rewrite the bargain afterward.
        funded_reward = max(0, int(contract.get("reward_cash", 0)))
        escrow_before = max(0, int(contract.get("escrow_cash", 0)))
        if escrow_before < funded_reward:
            raise CommandRejectedError("jianghu_contract_escrow_invalid")
        updated_contract = copy.deepcopy(dict(contract))
        updated_contract["objective"] = compact_started_escort_objective(normalized)
        records: dict[str, Any] = {}

        # A counted civilian is enough while nobody can address that body. Escort
        # departure changes that: every protected traveler can now be wounded,
        # killed, captured, moved or rescued independently. Promote only the
        # missing aggregate principals to exact civic people and conserve the
        # source population at the same write frontier.
        newly_materialized_refs: list[str] = []
        materialized_people: dict[str, Mapping[str, Any]] = {}
        aggregate_missing = max(0, protected_count - len(protected_refs))
        if aggregate_missing:
            try:
                materialized = materialize_civilian_identities(
                    self.repository.read_json(_CIVILIANS), self.repository.read_json(_CIVIC_PEOPLE),
                    world_seed=str(meta.get("world_seed") or "jianghu"), source_place_ref=source_place,
                    count=aggregate_missing, current_year=current_time.year,
                    civilian_party_kind=str(objective.get("civilian_party_kind") or "") or None,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise CommandRejectedError("jianghu_contract_civilian_party_unavailable") from exc
            records[_CIVILIANS] = materialized["civilian_state"]
            records[_CIVIC_PEOPLE] = materialized["civic_state"]
            newly_materialized_refs = [str(x) for x in materialized.get("person_refs", []) if isinstance(x, str)]
            civic_rows = materialized["civic_state"].get("people", []) if isinstance(materialized.get("civic_state"), Mapping) else []
            if isinstance(civic_rows, list):
                wanted = set(newly_materialized_refs)
                materialized_people = {
                    str(row.get("person_id")): hydrate_civic_person(row) for row in civic_rows
                    if isinstance(row, Mapping) and row.get("person_id") in wanted
                }
            protected_refs.extend(ref for ref in newly_materialized_refs if ref not in protected_refs)
            normalized["protected_person_refs"] = list(protected_refs)

        issuer = str(contract.get("issuer_ref") or "")

        deployments = self.repository.read_json(_DEPLOYMENTS)
        standing_party_refs = active_retinue_party(
            deployments, leader_ref=command.actor_id, principals=accepted
        )
        minimum = max(1, int(normalized.get("minimum_escort_count", 1)))

        sites_data = self.repository.read_json(_LOCAL_SITES)
        sites = sites_data.get("sites", {}) if isinstance(sites_data, Mapping) else {}
        if not isinstance(sites, Mapping):
            raise CommandRejectedError("jianghu_local_sites_invalid")

        # Route travel begins at settlement scale, but the exact escort bodies
        # must first exist as one physical party.  Do not treat every courtyard,
        # inn, manor or gate under the same city as interchangeable co-location.
        # The accepting player is always one of the accepted principals, so their
        # current exact presence is the natural muster anchor.
        muster_location = self._contract_person_exact_location(command.actor_id, actor)
        if not muster_location or not self._contract_location_is_at_source(
            muster_location, source_place=source_place, sites=sites
        ):
            raise CommandRejectedError("jianghu_contract_participant_not_at_route_origin")
        unavailable = self._unavailable_person_refs()

        try:
            approved_roster = approved_contract_escort_roster(
                self.repository.read_json,
                contract_ref=contract_ref,
                accepted_refs=accepted,
                standing_party_refs=standing_party_refs,
                minimum_escort_count=minimum,
            )
        except ValueError as exc:
            if str(exc) == "approved_roster_below_minimum":
                raise CommandRejectedError("jianghu_contract_escort_count_insufficient") from exc
            raise CommandRejectedError("jianghu_contract_approved_roster_invalid") from exc

        if approved_roster is None:
            # Claiming a public offer records Wei's protected choice, but it does
            # not grant authority to mobilize House personnel/resources.  Public
            # escort departure therefore consumes an exact institutional plan.
            # A lawful officeholder can self-authorize that plan through the same
            # mission layer; everyone else needs the recorded council authority.
            raise CommandRejectedError("jianghu_contract_requires_institutional_authorization")

        escort_refs = list(approved_roster["escort_refs"])
        core_escort_refs = list(approved_roster["core_escort_refs"])
        temporary_escort_refs = list(approved_roster["temporary_mission_escort_refs"])
        escort_commander_ref = str(approved_roster["commander_ref"])

        all_people = list(escort_refs)
        for ref in protected_refs:
            if ref not in all_people:
                all_people.append(ref)
        owner_by_ref: dict[str, str] = {}
        faction_people_to_pause: list[str] = []
        for ref in all_people:
            if ref in unavailable:
                raise CommandRejectedError("jianghu_contract_participant_unavailable")
            if ref in materialized_people:
                person = materialized_people[ref]
                roster: Mapping[str, Any] = {"schema": "jianghu-civic-people-state-1.0"}
            else:
                _p, roster, _i, person = self._resolve_contract_person(ref)
            health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
            if health.get("status") in {"dead", "incapacitated"} or int(health.get("consciousness", 100)) <= 0:
                raise CommandRejectedError("jianghu_contract_participant_unavailable")
            exact_location = self._contract_person_exact_location(ref, person)
            if ref in escort_refs:
                if exact_location != muster_location:
                    raise CommandRejectedError("jianghu_contract_participant_not_colocated")
            else:
                # Public principals/cargo crews may be waiting at the settlement
                # route origin itself.  A named principal at a different exact
                # child site is not silently teleported into the convoy.
                if exact_location not in {muster_location, source_place}:
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

        commitments = derived_commitment_state(self.repository.read_json)
        try:
            commitments = reserve_resources(
                commitments,
                resources=[("person", ref, owner_by_ref[ref]) for ref in all_people],
                actor_ref=command.actor_id, owner_ref=faction_ref,
                activity_ref=contract_ref, activity_kind="contract_escort",
                started_at=_at_iso(current_time), location_ref=muster_location,
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
        civilian_places = self.repository.read_json(_CIVILIANS).get("places", {})
        source_civ = civilian_places.get(source_place, {}) if isinstance(civilian_places, Mapping) else {}
        population = max(0, int(source_civ.get("current_population", 0))) if isinstance(source_civ, Mapping) else 0
        freight_required = max(0, int(normalized.get("freight_capacity_kg", 0)))
        crew_required = max(0, int(normalized.get("civilian_crew_count", freight_crew_required(freight_required))))
        available_logistics = civilian_available_capacity(place_ref=source_place, place_population=population, route_operations=route_ops)
        if freight_required > int(available_logistics.get("freight_capacity_kg", 0)) or crew_required > int(available_logistics.get("crew_capacity", 0)):
            raise CommandRejectedError("jianghu_contract_transport_capacity_unavailable")
        try:
            route_plan = travel_plan(
                world_seed=str(meta.get("world_seed") or "jianghu"), start_at=now,
                start=source_place, end=destination_place, mode="convoy",
            )
            ipath = inventory_path(faction_ref)
            faction_inventory = copy.deepcopy(self.repository.read_json(ipath))
            faction_inventory, provision_reservation = reserve_faction_rations(
                faction_inventory, faction_ref=faction_ref, participant_count=len(all_people),
                travel_seconds=provisioning_journey_seconds(route_plan),
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
            raise CommandRejectedError("jianghu_contract_travel_provisions_unavailable") from exc
        movement = build_route_journey(
            movement_ref=contract_ref, movement_kind="escort_contract", purpose_ref=contract_ref,
            plan=route_plan, participants=all_people, leader_ref=escort_commander_ref,
            beneficiary_ref=faction_ref, started_at=now, mode="convoy",
            extra={
                "contract_ref": contract_ref, "item_ref": item_ref, "quantity": quantity,
                "escort_refs": escort_refs, "core_escort_refs": core_escort_refs,
                "temporary_mission_escort_refs": temporary_escort_refs,
                "protected_person_refs": protected_refs,
                "provision_reservation": provision_reservation,
                "transport_reservation": make_transport_reservation(
                    provider_kind="civilian_logistics", provider_ref=source_place,
                    freight_capacity_kg=freight_required, crew_capacity=crew_required,
                ),
            },
        )
        movements[contract_ref] = movement
        records[ipath] = faction_inventory
        try:
            started = contract_transition(
                updated_contract, at=_at_iso(current_time), to_status="in_progress",
                actor_ref=command.actor_id, participants=escort_refs,
            )
        except ValueError as exc:
            raise CommandRejectedError("jianghu_contract_transition_invalid") from exc
        started["objective"] = compact_started_escort_objective(normalized)
        active[contract_ref] = started
        schedule = copy.deepcopy(self.repository.read_json(_SCHEDULE))
        active_route_ids = route_ids_needing_service(movements)
        try:
            schedule = sync_route_activity(schedule, active_route_ids=active_route_ids, now=now)
            schedule = prune_contract_expiry_events(schedule, active)
        except ValueError as exc:
            raise CommandRejectedError("jianghu_scheduler_invalid") from exc
        ensure_contract_dossier(
            read_json=self.repository.read_json, writes=records, contract_ref=contract_ref,
            faction_ref=faction_ref, actor_ref=command.actor_id, at_iso=_at_iso(current_time), phase="in_field",
            participant_refs=escort_refs, commander_ref=escort_commander_ref,
            objective="Fulfill the funded escort commission", issuer_ref=str(contract.get("issuer_ref") or ""),
        )
        records.update({
            _CONTRACTS:index, _ROUTE_OPS:route_ops, _SCHEDULE:schedule,
            pause_fpath:paused_faction, pause_path:paused_roster,
        })
        return self._simple_plan(
            command, meta, current_time, writes_records=records,
            code="jianghu_contract_started",
            result={
                "command_type":command.command_type,"action":"start","contract_ref":contract_ref,
                "status":"in_progress","escort_refs":escort_refs,"core_escort_refs":core_escort_refs,
                "temporary_mission_escort_refs":temporary_escort_refs,"commander_ref":escort_commander_ref,
                "protected_person_refs":protected_refs,
                "protected_people_count":protected_count,"minimum_escort_count":minimum,
                "reward_cash":funded_reward,
            },
        )


__all__ = ["JianghuContractCommandsMixin"]
