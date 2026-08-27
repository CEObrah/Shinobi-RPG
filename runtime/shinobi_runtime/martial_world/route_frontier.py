"""Physical route frontier reducer for all traveling Jianghu parties.

Purpose owners such as contracts and deployments describe why a party travels.
This reducer alone owns physical road progress, settlement stops, public
exposure, interception, route combat, seizure and route completion. It stages
after-images into the caller's atomic frontier write set and never commits by
itself.
"""
from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from .commitments import release_resources, reserve_resources
from .combat_simulation import simulate_exact_combat
from .contracts import settle_payment, transition as contract_transition
from .institutional_operations import close_institutional_operation, close_linked_contract_operation, stage_institutional_phase, stage_linked_contract_phase
from .crime_custody import create_custody_record, mark_custody_informed
from .escort_living_world import (
    apply_lodging_rest, best_route_observer, escort_can_resume_field_travel, escort_rest_hours, interception_decision,
    interception_force_size, observed_escort_strength, principal_ransom_value_cash, route_interception_opportunity_permille,
)
from .exact_combat import initialize_combat
from .environment import combat_environment, movement_edge_progress_milli, route_terrain_at
from .frontier_support import (
    arrival_site as _arrival_site, chunk_contains_final_owner as _chunk_contains_final_owner,
    credit_cargo_to_inventory as _credit_cargo_to_inventory, lodging_site as _lodging_site,
    reputation_after_points as _reputation_after_points, social_event as _social_event,
)
from .government import attention_from_evidence, compact_attention_row
from .government_finance import fund_bounty_escrow
from .handoffs import classify_handoff
from .infrastructure import enterprise_scale_value
from .manpower import combat_ready_count
from .operational_equipment import detach_operation_issue_holders, reclaim_operation_equipment
from .physical_travel import (
    advance_movement_progress, begin_next_segment, build_route_journey, exact_segment_due_event, movement_complete,
    movement_required_seconds, refresh_current_segment,
)
from .relationships import apply_sparse_group_relationship_event
from .repatriation import build_repatriation_operation
from .rankings import (
    apply_faction_awareness_evidence, apply_faction_reputation_evidence, apply_personal_fame_evidence,
)
from .regional_economy import current_cargo_market_value_cash, execute_sale
from .route_activity import compact_route_movement_roles, route_controlling_refs, route_exposure, route_potential_controller_refs, route_traffic_milli
from .social_presence import person_attends_site, person_settlement
from .social_causality import prune_beliefs_for_subject_refs
from .strategic_autonomy import stable_permille
from .training import settle_and_reset_faction_training_cycle
from .travel import travel_plan
from .travel_provisions import (
    add_faction_upkeep_credit, apply_route_provision_progress, planned_journey_seconds, provisioning_journey_seconds,
    refund_unused_to_faction, refund_unused_to_person, reserve_faction_rations,
)
from .weather import weather_snapshot
from .warfare import strategic_operation_targeting_intent
from .captivity_lifecycle import family_household_faction

_COMBATS_PATH = "state/martial-world/combats.json"
_CONTRACT_INDEX_PATH = "state/martial-world/contracts/index.json"
_CUSTODY_PATH = "state/martial-world/custody.json"
_DEPLOYMENTS_PATH = "state/martial-world/deployments.json"
_EQUIPMENT_LEDGER_PATH = "state/martial-world/equipment-ledger.json"
_GOVERNMENT_PATH = "state/martial-world/government.json"
_REPUTATION_PATH = "state/martial-world/reputation.json"
_ROUTE_OPERATIONS_PATH = "state/martial-world/route-operations.json"
_SCENE_PATH = "state/scene.json"
_SOCIAL_PATH = "state/martial-world/social.json"


def _credit_failed_escort_cargo_to_origin_market(
    *, origin_region: str, item_ref: str, quantity: int,
    load_market: Callable[[str], tuple[str, dict[str, Any]]],
    writes: dict[str, Any], market_cache: dict[str, tuple[str, dict[str, Any]]],
) -> None:
    """Return unseized failed-escort cargo to one exact market owner.

    Contract failure cannot delete physical cargo merely because geography or a
    market owner is malformed. Resolve and validate the destination before the
    caller finishes the staged transaction, and fail closed when it is absent.
    """
    if not origin_region:
        raise ValueError("failed escort cargo return region unresolved")
    try:
        mpath, origin_market = load_market(origin_region)
    except FileNotFoundError as exc:
        raise ValueError("failed escort cargo return market unresolved") from exc
    if not isinstance(origin_market, dict) or origin_market.get("region_id") not in (None, origin_region):
        raise ValueError("failed escort cargo return market invalid")
    stock = origin_market.setdefault("stock", {})
    if not isinstance(stock, dict):
        raise ValueError("failed escort cargo return market stock invalid")
    stock[item_ref] = max(0, int(stock.get(item_ref, 0))) + max(0, int(quantity))
    writes[mpath] = origin_market
    market_cache[origin_region] = (mpath, origin_market)


def bounded_raid_retreat_ref(parent_movement_ref: str, attacker_fid: str, at_iso: str) -> str:
    """Return a deterministic bounded identity for a failed attacker's return.

    A retreat can itself be intercepted. Embedding the parent movement ID in
    each next retreat recursively grows identifiers and any evidence references
    that point at them. Hash the full causal input instead; the movement row
    retains the exact faction, route, people and timing facts.
    """
    at = datetime.fromisoformat(str(at_iso))
    digest = hashlib.sha256(
        f"{parent_movement_ref}|{attacker_fid}|{at.date().isoformat()}".encode("utf-8")
    ).hexdigest()[:20]
    return f"raid_retreat:{at.date().isoformat()}:{digest}"

def _public_offense_for_route_intent(intent: str) -> str:
    """Map the actual observed contact objective to registered public crime."""
    value = str(intent or "")
    if value == "kidnap_principal":
        return "kidnapping"
    if value in {"revenge", "hostile_interception"}:
        return "assault"
    return "robbery"


def settle_route_frontier(
    *,
    active_after: Any,
    active_contracts: Any,
    apply_directed_relation_event: Any,
    at: Any,
    at_iso: Any,
    close_dead_current_authorities: Any,
    combats: Any,
    combats_state: Any,
    commitments_state: Any,
    contract_after: Any,
    contract_index: Any,
    current_faction_type: Any,
    custody_state: Any,
    deployments_state: Any,
    directed_relation: Any,
    economy_rules: Any,
    equipment_ledger: Any,
    faction_cache: Any,
    family_state: Any,
    government_state: Any,
    handoffs: Any,
    inventory_cache: Any,
    load_faction: Any,
    load_inventory: Any,
    load_market: Any,
    load_person_ref: Any,
    load_roster: Any,
    local_factions_by_place: Any,
    local_factions_for_route: Any,
    local_sites: Any,
    market_cache: Any,
    move_exact_people: Any,
    move_exact_people_to_location: Any,
    outlaws_for_route: Any,
    pause_people_for_commitment: Any,
    pending_one_off_events: Any,
    person_combat_index: Any,
    place_region: Any,
    player_ref: Any,
    read_json: Any,
    reputation_state: Any,
    reviews: Any,
    roster_cache: Any,
    route_events: Any,
    route_index: Any,
    route_interception_candidates: Any,
    route_ops_state: Any,
    save_exact_person: Any,
    schedule: Any,
    settle_and_resume_people: Any,
    site_rows: Any,
    social_state: Any,
    sorted_events: Any,
    start_custody_rescue_operation: Any,
    travel_data: Any,
    unavailable_person_refs: Any,
    usable_martial_people: Any,
    world_seed: Any,
    writes: Any,
) -> dict[str, Any]:
    if route_events:
        movements = route_ops_state.setdefault("movements", {})
        initial_movement_refs = {str(ref) for ref in movements if isinstance(ref, str)} if isinstance(movements, Mapping) else set()
        contacts = route_ops_state.setdefault("contacts", {})
        if not isinstance(movements, dict) or not isinstance(contacts, dict):
            raise ValueError("jianghu route operations invalid")
        # Route owners are resumably chunked at the same timestamp. Keep the
        # per-day route attack budget as a tiny current-day accumulator so
        # changing transaction chunk size cannot grant extra attacks.
        attack_tracker = route_ops_state.get("daily_route_attack_budget", route_ops_state.get("daily_outlaw_attack_budget", {}))
        route_ops_state.pop("daily_outlaw_attack_budget", None)
        if not isinstance(attack_tracker, Mapping) or attack_tracker.get("date") != at.date().isoformat():
            attack_tracker = {"date": at.date().isoformat(), "counts": {}}
        else:
            attack_tracker = copy.deepcopy(dict(attack_tracker))
        route_attack_counts = attack_tracker.setdefault("counts", {})
        if not isinstance(route_attack_counts, dict):
            raise ValueError("jianghu route attack budget invalid")

        def _detach_operation_issue_refs(
            op_ref: str, holder_refs: Sequence[str], *, status: str = "operation_issue_separated",
        ) -> int:
            """Materialize source title when issued gear leaves its physical operation."""
            nonlocal equipment_ledger
            refs = sorted({str(ref) for ref in holder_refs if isinstance(ref, str) and ref})
            if not op_ref or not refs:
                return 0
            deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
            op = deployments.get(op_ref) if isinstance(deployments, dict) else None
            if not isinstance(op, Mapping) or not isinstance(op.get("issued_equipment"), Mapping):
                return 0
            faction_ref = str(op.get("faction_ref") or "")
            if not faction_ref:
                raise ValueError(f"operation issue source faction unresolved: {op_ref}")
            detached = detach_operation_issue_holders(
                operation=op, source_faction_ref=faction_ref, holder_refs=refs,
                equipment_ledger=equipment_ledger, status=status,
            )
            deployments[op_ref] = copy.deepcopy(dict(detached["operation_after"]))
            equipment_ledger = copy.deepcopy(dict(detached["equipment_ledger_after"]))
            writes[_DEPLOYMENTS_PATH] = deployments_state
            writes[_EQUIPMENT_LEDGER_PATH] = equipment_ledger
            return max(0, int(detached.get("detached_holder_count", 0)))

        def _update_roster_people(fid: str, people_after: Mapping[str, Mapping[str, Any]]) -> None:
            rpath, roster = load_roster(fid)
            rows = roster.get("people", []) if isinstance(roster, Mapping) else []
            if not isinstance(rows, list):
                return
            after_rows: list[Any] = []
            changed = False
            for raw in rows:
                ref = raw.get("person_id") if isinstance(raw, Mapping) else None
                if isinstance(ref, str) and ref in people_after:
                    after_rows.append(copy.deepcopy(dict(people_after[ref])))
                    changed = True
                else:
                    after_rows.append(raw)
            if changed:
                roster["people"] = after_rows
                writes[rpath] = roster
                roster_cache[fid] = (rpath, roster)

        def _move_faction_people(fid: str, person_refs: Sequence[str], place_ref: str) -> None:
            refs = {str(x) for x in person_refs if isinstance(x, str)}
            if not refs or not place_ref:
                return
            arrival = _arrival_site(local_sites, place_ref) or place_ref
            rpath, roster = load_roster(fid)
            rows = roster.get("people", []) if isinstance(roster, Mapping) else []
            if not isinstance(rows, list):
                return
            changed = False; after_rows: list[Any] = []
            for raw in rows:
                if isinstance(raw, Mapping) and str(raw.get("person_id")) in refs:
                    person = copy.deepcopy(dict(raw)); person["location_ref"] = arrival; after_rows.append(person); changed = True
                else:
                    after_rows.append(raw)
            if changed:
                roster["people"] = after_rows
                writes[rpath] = roster; roster_cache[fid] = (rpath, roster)

        def _movement_region(place_ref: str) -> str:
            region = place_region.get(str(place_ref or ""))
            return str(region) if isinstance(region, str) else ""

        def _refund_movement_provisions(movement: Mapping[str, Any]) -> int:
            """Return only unused reserved ration-days to their exact source.

            Consumed ration-days are current economic use and are never refunded.
            Any positive unused reserve is conserved value, so an unresolved
            source is a hard ownership error rather than a best-effort no-op.
            """
            reservation = movement.get("provision_reservation")
            if not isinstance(reservation, Mapping):
                return 0
            reserved = max(0, int(reservation.get("ration_days_reserved", 0)))
            consumed = max(0, min(reserved, int(reservation.get("ration_days_consumed", 0))))
            unused = max(0, reserved - consumed)
            if unused <= 0:
                return 0
            source_kind = str(reservation.get("source_kind") or "")
            source_ref = str(reservation.get("source_ref") or "")
            if not source_ref:
                raise ValueError("route provision refund source unresolved")
            if source_kind == "faction":
                try:
                    ipath, inventory = load_inventory(source_ref)
                except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
                    raise ValueError("route provision faction refund source unresolved") from exc
                inventory_after, refunded = refund_unused_to_faction(inventory, movement)
                if refunded:
                    writes[ipath] = inventory_after
                    inventory_cache[source_ref] = (ipath, inventory_after)
                return refunded
            if source_kind == "person":
                try:
                    _fid, _ppath, _owner, _ordinal, person = load_person_ref(source_ref)
                except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
                    raise ValueError("route provision person refund source unresolved") from exc
                person_after, refunded = refund_unused_to_person(person, movement)
                if refunded:
                    save_exact_person(source_ref, person_after)
                return refunded
            raise ValueError("route provision refund source kind invalid")

        def _extinguished_route_place(movement: Mapping[str, Any]) -> str:
            """Project an abandoned road party to the nearest real endpoint."""
            origin = str(movement.get("segment_origin_place_ref") or movement.get("origin_place_ref") or "")
            destination = str(movement.get("segment_destination_place_ref") or movement.get("destination_place_ref") or "")
            required = max(1, movement_required_seconds(movement))
            elapsed = max(0, int(movement.get("elapsed_seconds", 0)))
            if destination and elapsed * 2 >= required:
                return destination
            return origin or destination

        def _salvage_extinguished_raid_return(movement_ref: str, movement: Mapping[str, Any]) -> dict[str, Any]:
            """Conserve an abandoned hostile return without inventing a salvage actor."""
            nonlocal commitments_state, custody_state
            place_ref = _extinguished_route_place(movement)
            region = _movement_region(place_ref)
            if not place_ref or not region:
                raise ValueError("extinguished raid return salvage region unresolved")
            release_location = _arrival_site(local_sites, place_ref) or place_ref

            item_ref = str(movement.get("item_ref") or "")
            quantity = max(0, int(movement.get("quantity", 0)))
            cash_quantity = max(0, int(movement.get("cash_quantity", 0)))
            reservation = movement.get("provision_reservation")
            unused_rations = 0
            if isinstance(reservation, Mapping):
                reserved = max(0, int(reservation.get("ration_days_reserved", 0)))
                consumed = max(0, min(reserved, int(reservation.get("ration_days_consumed", 0))))
                unused_rations = max(0, reserved - consumed)
            if quantity > 0 and not item_ref:
                raise ValueError("extinguished raid return cargo item unresolved")
            if quantity > 0 or unused_rations > 0 or cash_quantity > 0:
                mpath, market = load_market(region)
                stock = market.setdefault("stock", {})
                if not isinstance(stock, dict):
                    raise ValueError("extinguished raid return salvage market stock invalid")
                if quantity > 0:
                    stock[item_ref] = max(0, int(stock.get(item_ref, 0))) + quantity
                if unused_rations > 0:
                    stock["food_ration_day"] = max(0, int(stock.get("food_ration_day", 0))) + unused_rations
                if cash_quantity > 0:
                    market["cash_pool"] = max(0, int(market.get("cash_pool", 0))) + cash_quantity
                writes[mpath] = market
                market_cache[region] = (mpath, market)

            captive_refs = {str(x) for x in movement.get("captive_refs", []) if isinstance(x, str) and x}
            carried_refs = set(captive_refs)
            carried_refs.update(str(x) for x in movement.get("protected_person_refs", []) if isinstance(x, str) and x)
            carried_refs.update(str(x) for x in movement.get("rescued_refs", []) if isinstance(x, str) and x)
            released_refs: list[str] = []
            custody_changed = False
            records = custody_state.get("records", []) if isinstance(custody_state, Mapping) else []
            if not isinstance(records, list):
                raise ValueError("jianghu custody records invalid")
            kept_records: list[dict[str, Any]] = []
            for row in records:
                if not isinstance(row, dict):
                    continue
                ref = str(row.get("person_ref") or "")
                if ref in captive_refs:
                    if row.get("status") not in {"released", "escaped", "rescued", "executed"}:
                        released_refs.append(ref)
                        custody_changed = True
                    # Custody state owns current restraint only. A released/ended
                    # record is historical evidence and does not remain hot state.
                    continue
                kept_records.append(row)
            if custody_changed or len(kept_records) != len(records):
                custody_state["records"] = kept_records
                writes[_CUSTODY_PATH] = custody_state
            if carried_refs:
                move_exact_people_to_location(sorted(carried_refs), release_location)

            op_ref = str(movement.get("purpose_ref") or movement.get("operation_ref") or "")
            operation_closed = False
            if op_ref:
                deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
                op = deployments.get(op_ref) if isinstance(deployments, dict) else None
                if isinstance(op, Mapping):
                    issue_refs = list(op.get("issued_equipment", {}).keys()) if isinstance(op.get("issued_equipment"), Mapping) else []
                    _detach_operation_issue_refs(op_ref, issue_refs, status="operation_issue_stranded")
                    deployments.pop(op_ref, None)
                    writes[_DEPLOYMENTS_PATH] = deployments_state
                    commitments_state = release_resources(commitments_state, activity_ref=op_ref)
                    operation_closed = True
            commitments_state = release_resources(commitments_state, activity_ref=movement_ref)
            movements.pop(movement_ref, None)
            writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
            return {
                "closed": True, "success": False, "party_extinguished": True,
                "operation_closed": operation_closed,
                "released_captive_refs": sorted(released_refs),
                "salvaged_item_ref": item_ref, "salvaged_quantity": quantity,
                "salvaged_cash": cash_quantity,
                "salvaged_ration_days": unused_rations, "salvage_region": region,
            }

        def _fresh_faction_return_journey(
            movement_ref: str, movement: Mapping[str, Any], *, beneficiary: str,
            participants: Sequence[str], origin_place: str, destination_place: str,
            movement_kind: str, mode: str, extra: Mapping[str, Any] | None = None,
            journey_start_at: datetime | None = None,
        ) -> dict[str, Any] | None:
            if not beneficiary or not participants or not origin_place or not destination_place:
                return None
            journey_at = journey_start_at or at
            try:
                plan = travel_plan(
                    world_seed=world_seed, start_at=journey_at, start=origin_place,
                    end=destination_place, mode=mode,
                )
                ipath, inventory = load_inventory(beneficiary)
                inventory, provision = reserve_faction_rations(
                    inventory, faction_ref=beneficiary, participant_count=len(participants),
                    travel_seconds=provisioning_journey_seconds(plan),
                )
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                return None
            writes[ipath] = inventory
            inventory_cache[beneficiary] = (ipath, inventory)
            payload = {
                "provision_reservation": provision,
                **({"transport_reservation": copy.deepcopy(movement.get("transport_reservation"))}
                   if isinstance(movement.get("transport_reservation"), Mapping) else {}),
            }
            if isinstance(extra, Mapping):
                payload.update(copy.deepcopy(dict(extra)))
            return build_route_journey(
                movement_ref=movement_ref, movement_kind=movement_kind, purpose_ref=str(movement.get("purpose_ref") or movement_ref),
                plan=plan, participants=participants, leader_ref=participants[0], beneficiary_ref=beneficiary,
                started_at=journey_at, mode=mode, destination_site_ref="", extra=payload,
            )

        def _fresh_faction_return_from_current_position(
            movement_ref: str, movement: Mapping[str, Any], *, beneficiary: str,
            participants: Sequence[str], destination_place: str, movement_kind: str,
            mode: str, extra: Mapping[str, Any] | None = None, inherit_transport: bool = True,
        ) -> dict[str, Any] | None:
            """Build a fresh funded return beginning at exact current edge progress.

            The party may leave the current edge through either endpoint.  The
            deterministic shorter total travel time wins, so a faction based
            ahead of the intercepted convoy does not irrationally retreat to the
            convoy's previous settlement first.  The selected tail is freshly
            planned at the actual return time.
            """
            if not beneficiary or not participants or not destination_place:
                return None
            route_ref = str(movement.get("route_ref") or "")
            edge = route_index.get(route_ref) if isinstance(route_index, Mapping) else None
            segment_origin = str(movement.get("segment_origin_place_ref") or movement.get("origin_place_ref") or "")
            segment_destination = str(movement.get("segment_destination_place_ref") or movement.get("destination_place_ref") or "")
            if not route_ref or not isinstance(edge, Mapping) or not segment_origin or not segment_destination:
                return _fresh_faction_return_journey(
                    movement_ref, movement, beneficiary=beneficiary, participants=participants,
                    origin_place=segment_origin or str(movement.get("origin_place_ref") or ""),
                    destination_place=destination_place, movement_kind=movement_kind, mode=mode, extra=extra,
                    journey_start_at=at,
                )
            progress = movement_edge_progress_milli(movement, edge)
            start_progress = max(0, min(1000, int(movement.get("edge_start_milli", 0))))
            end_progress = max(0, min(1000, int(movement.get("edge_end_milli", 1000))))
            segment_span = max(1, abs(end_progress - start_progress))
            segment_seconds = movement_required_seconds(movement)

            candidates: list[tuple[int, str, int, str, Mapping[str, Any] | None, int]] = []
            for endpoint, endpoint_progress, opposite in (
                (segment_origin, start_progress, segment_destination),
                (segment_destination, end_progress, segment_origin),
            ):
                partial_span = abs(progress - endpoint_progress)
                partial_seconds = 0 if partial_span <= 0 else max(60, (segment_seconds * partial_span + segment_span - 1) // segment_span)
                cursor_at = at + timedelta(seconds=partial_seconds)
                tail: Mapping[str, Any] | None = None
                tail_seconds = 0
                if endpoint != destination_place:
                    try:
                        tail = travel_plan(
                            world_seed=world_seed, start_at=cursor_at, start=endpoint,
                            end=destination_place, mode=mode,
                        )
                        tail_seconds = planned_journey_seconds(tail)
                    except (FileNotFoundError, KeyError, TypeError, ValueError):
                        continue
                    if tail_seconds <= 0:
                        continue
                candidates.append((partial_seconds + tail_seconds, endpoint, endpoint_progress, opposite, tail, partial_seconds))
            if not candidates:
                return None
            _total, endpoint, endpoint_progress, opposite, tail, partial_seconds = min(
                candidates, key=lambda row: (row[0], row[1])
            )
            plan_edges: list[str] = []
            plan_nodes: list[str] = []
            plan_segments: list[dict[str, Any]] = []
            if partial_seconds > 0:
                weather = weather_snapshot(world_seed=world_seed, at=at, place_id=endpoint)
                plan_edges.append(route_ref)
                plan_nodes.extend([opposite, endpoint])
                plan_segments.append({
                    "hours": partial_seconds / 3600.0, "weather": weather,
                    "edge_start_milli": progress, "edge_end_milli": endpoint_progress,
                })
            if isinstance(tail, Mapping):
                tail_edges = [str(x) for x in tail.get("edges", []) if isinstance(x, str)]
                tail_nodes = [str(x) for x in tail.get("nodes", []) if isinstance(x, str)]
                tail_segments = [copy.deepcopy(dict(row)) for row in tail.get("segments", []) if isinstance(row, Mapping)]
                if not tail_edges or len(tail_nodes) != len(tail_edges) + 1 or len(tail_segments) != len(tail_edges):
                    return None
                if plan_edges:
                    plan_edges.extend(tail_edges)
                    plan_nodes.extend(tail_nodes[1:])
                    plan_segments.extend(tail_segments)
                else:
                    plan_edges, plan_nodes, plan_segments = tail_edges, tail_nodes, tail_segments
            if not plan_edges or len(plan_nodes) != len(plan_edges) + 1:
                return None
            plan = {"edges": plan_edges, "nodes": plan_nodes, "segments": plan_segments}
            try:
                ipath, inventory = load_inventory(beneficiary)
                inventory, provision = reserve_faction_rations(
                    inventory, faction_ref=beneficiary, participant_count=len(participants),
                    travel_seconds=provisioning_journey_seconds(plan),
                )
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                return None
            writes[ipath] = inventory
            inventory_cache[beneficiary] = (ipath, inventory)
            payload: dict[str, Any] = {"provision_reservation": provision}
            if inherit_transport and isinstance(movement.get("transport_reservation"), Mapping):
                payload["transport_reservation"] = copy.deepcopy(movement.get("transport_reservation"))
            if isinstance(extra, Mapping):
                payload.update(copy.deepcopy(dict(extra)))
            return build_route_journey(
                movement_ref=movement_ref, movement_kind=movement_kind, purpose_ref=str(movement.get("purpose_ref") or movement_ref),
                plan=plan, participants=participants, leader_ref=participants[0], beneficiary_ref=beneficiary,
                started_at=at, mode=mode, destination_site_ref="", extra=payload,
            )

        _RETURN_EXTRA_KEYS = (
            "trade_leg", "trade_outcome", "item_ref", "quantity", "contract_ref",
            "protected_person_refs", "captive_refs", "rescued_refs", "escort_refs",
        )

        def _return_extra_from_movement(movement: Mapping[str, Any]) -> dict[str, Any]:
            return {
                key: copy.deepcopy(movement[key])
                for key in _RETURN_EXTRA_KEYS if key in movement
            }

        def _park_waiting_return(
            movement_ref: str, source: Mapping[str, Any], *, beneficiary: str,
            participants: Sequence[str], destination_place: str, movement_kind: str,
            mode: str, from_current_position: bool, origin_place: str = "",
            extra: Mapping[str, Any] | None = None,
        ) -> dict[str, Any]:
            """Keep an unfunded/unroutable return as a real finite owner.

            People do not become institutionally available merely because the
            return leg could not be provisioned at this frontier.  The parked
            movement retains only current physical position plus the unresolved
            return objective; route_daily retries it deterministically.
            """
            participant_refs = list(dict.fromkeys(str(x) for x in participants if isinstance(x, str) and x))
            row: dict[str, Any] = {
                "movement_kind": movement_kind,
                "route_ref": str(source.get("route_ref") or ""),
                "origin_place_ref": str(origin_place or source.get("origin_place_ref") or ""),
                "destination_place_ref": str(destination_place),
                "beneficiary_ref": beneficiary,
                "participant_refs": participant_refs,
                "leader_ref": participant_refs[0] if participant_refs else "",
                "purpose_ref": str(source.get("purpose_ref") or movement_ref),
                "started_at": at_iso,
                "status": "awaiting_return_logistics",
                "return_mode": mode,
                "return_from_current_position": bool(from_current_position),
                "return_destination_place_ref": str(destination_place),
            }
            if isinstance(source.get("transport_reservation"), Mapping):
                row["transport_reservation"] = copy.deepcopy(source["transport_reservation"])
            if from_current_position:
                for key in (
                    "segment_origin_place_ref", "segment_destination_place_ref",
                    "edge_start_milli", "edge_end_milli", "elapsed_seconds",
                    "required_seconds", "last_progress_at", "route_index",
                ):
                    if key in source:
                        row[key] = copy.deepcopy(source[key])
            if isinstance(extra, Mapping):
                row.update(copy.deepcopy(dict(extra)))
            return compact_route_movement_roles(row)

        def _retry_waiting_return(movement_ref: str, movement: Mapping[str, Any]) -> dict[str, Any] | None:
            beneficiary = str(movement.get("beneficiary_ref") or "")
            participants = _nondead_person_refs([
                str(x) for x in movement.get("participant_refs", []) if isinstance(x, str)
            ])
            potential_controllers = route_potential_controller_refs(movement)
            ready_controllers = _field_ready_person_refs(potential_controllers)
            if not beneficiary or not participants or not ready_controllers:
                return None
            destination = str(movement.get("return_destination_place_ref") or movement.get("destination_place_ref") or "")
            mode = str(movement.get("return_mode") or "foot")
            movement_kind = str(movement.get("movement_kind") or "return")
            extra = _return_extra_from_movement(movement)
            current_potential = route_potential_controller_refs({**dict(movement), "participant_refs": participants})
            if set(ready_controllers) == set(current_potential):
                extra.pop("escort_refs", None)
            else:
                extra["escort_refs"] = ready_controllers
            if bool(movement.get("return_from_current_position")):
                returning = _fresh_faction_return_from_current_position(
                    movement_ref, movement, beneficiary=beneficiary, participants=participants,
                    destination_place=destination, movement_kind=movement_kind, mode=mode,
                    extra=extra, inherit_transport=True,
                )
            else:
                returning = _fresh_faction_return_journey(
                    movement_ref, movement, beneficiary=beneficiary, participants=participants,
                    origin_place=str(movement.get("origin_place_ref") or ""),
                    destination_place=destination, movement_kind=movement_kind, mode=mode,
                    extra=extra, journey_start_at=at,
                )
            if not isinstance(returning, Mapping):
                return None
            out = copy.deepcopy(dict(returning))
            out["status"] = "returning"
            return compact_route_movement_roles(out)

        def _movement_cargo_value_cash(movement: Mapping[str, Any]) -> int:
            # Market value must observe the current staged frontier, not an
            # undefined/legacy view closure.  Markets already written earlier
            # in this frontier therefore participate in the same deterministic
            # valuation, while untouched owners fall back to committed state.
            def _current(rel: str):
                staged = writes.get(rel) if isinstance(writes, Mapping) else None
                return staged if isinstance(staged, Mapping) else read_json(rel)
            return current_cargo_market_value_cash(movement, read_json=_current)

        def _living_person_refs(person_refs: Sequence[str]) -> list[str]:
            out: list[str] = []
            for ref in [str(x) for x in person_refs if isinstance(x, str)]:
                try:
                    _fid, _path, _owner, _ordinal, person = load_person_ref(ref)
                except (FileNotFoundError, KeyError, TypeError, ValueError):
                    continue
                health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
                if health.get("status") != "dead" and int(health.get("consciousness", 100)) > 0:
                    out.append(ref)
            return out

        def _nondead_person_refs(person_refs: Sequence[str]) -> list[str]:
            out: list[str] = []
            for ref in [str(x) for x in person_refs if isinstance(x, str)]:
                try:
                    _fid, _path, _owner, _ordinal, person = load_person_ref(ref)
                except (FileNotFoundError, KeyError, TypeError, ValueError):
                    continue
                health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
                if health.get("status") != "dead":
                    out.append(ref)
            return out

        def _field_ready_person_refs(person_refs: Sequence[str]) -> list[str]:
            out: list[str] = []
            for ref in [str(x) for x in person_refs if isinstance(x, str)]:
                try:
                    _fid, _path, _owner, _ordinal, person = load_person_ref(ref)
                except (FileNotFoundError, KeyError, TypeError, ValueError):
                    continue
                health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
                if int(health.get("consciousness", 100)) <= 0 or health.get("status") == "dead":
                    continue
                if escort_can_resume_field_travel([person]):
                    out.append(ref)
            return out

        def _movement_environment(movement_ref: str, movement: Mapping[str, Any]) -> dict[str, Any]:
            rid=str(movement.get("route_ref") or "")
            edge=route_index.get(rid) if isinstance(route_index,Mapping) else None
            if not isinstance(edge,Mapping):
                return combat_environment(terrain="plain",zone_ref=rid or "route",seed_ref=f"{movement_ref}|{at_iso}",weather=movement.get("route_weather") if isinstance(movement.get("route_weather"),Mapping) else {})
            progress=movement_edge_progress_milli(movement,edge)
            terrain=route_terrain_at(edge,progress)
            try:
                weather=weather_snapshot(world_seed=world_seed,at=at,place_id=str(movement.get("segment_origin_place_ref") or movement.get("origin_place_ref") or edge.get("from") or ""))
            except (KeyError,ValueError):
                weather=movement.get("route_weather") if isinstance(movement.get("route_weather"),Mapping) else {}
            env=combat_environment(terrain=terrain,zone_ref=rid,seed_ref=f"{movement_ref}|{rid}|{progress//50}",weather=weather)
            env["route_progress_milli"]=progress
            return env

        def _movement_mount_assignments(movement: Mapping[str, Any], participant_refs: Sequence[str]) -> dict[str, dict[str, Any]]:
            if str(movement.get("mode") or "") != "horse":
                return {}
            reservation=movement.get("transport_reservation") if isinstance(movement.get("transport_reservation"),Mapping) else {}
            slots=max(0,int(reservation.get("rider_slots",0)))
            owner=str(reservation.get("provider_ref") or movement.get("beneficiary_ref") or "")
            if slots<=0 or not owner:
                return {}
            return {str(ref):{"owner_faction_ref":owner,"condition_milli":1000,"inventory_debited":False} for ref in list(participant_refs)[:slots] if isinstance(ref,str)}

        def _apply_shared_danger_social(participant_refs: Sequence[str]) -> None:
            nonlocal social_state
            living: list[str] = []
            for ref in participant_refs:
                if not isinstance(ref, str) or not ref or ref in living:
                    continue
                try:
                    _fid, _path, _owner, _ordinal, person = load_person_ref(ref)
                except (FileNotFoundError, KeyError, TypeError, ValueError):
                    continue
                health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
                if health.get("status") != "dead":
                    living.append(ref)
            party = living[:12]
            if len(party) > 1:
                social_state = apply_sparse_group_relationship_event(
                    social_state, participant_refs=party, event_kind="shared_danger",
                    severity_milli=550, protected_player_ref=player_ref or "pc_wei_tang",
                )["state_after"]
                writes[_SOCIAL_PATH] = social_state

        def _start_settlement_exposure_pursuits(
            movement_ref: str, movement: Mapping[str, Any], *, stop_place: str, site_ref: str,
        ) -> list[str]:
            """Turn a real public stop into possible real pursuit, never a random event row."""
            nonlocal commitments_state
            site = site_rows.get(site_ref) if isinstance(site_rows, Mapping) else None
            if not isinstance(site, Mapping) or str(site.get("site_type") or "") not in {"inn", "tea_house", "wine_shop", "market", "caravan_yard", "stable"}:
                return []
            participants=[str(x) for x in movement.get("participant_refs",[]) if isinstance(x,str)]
            escort_refs=[str(x) for x in movement.get("escort_refs",participants) if isinstance(x,str)]
            escorts=[]
            for ref in escort_refs:
                try: _fid,_path,_owner,_ordinal,person=load_person_ref(ref)
                except (FileNotFoundError,KeyError,TypeError,ValueError): continue
                health=person.get("health",{}) if isinstance(person.get("health"),Mapping) else {}
                if health.get("status") != "dead": escorts.append(person)
            if not escorts: return []
            beneficiary=str(movement.get("beneficiary_ref") or "")
            target_factions=[beneficiary] if beneficiary else []
            actual_ransom=0
            for ref in [str(x) for x in movement.get("protected_person_refs",[]) if isinstance(x,str)]:
                try: pfid,_path,_owner,_ordinal,person=load_person_ref(ref)
                except (FileNotFoundError,KeyError,TypeError,ValueError): continue
                actual_ransom=max(actual_ransom,principal_ransom_value_cash(person))
                if pfid and pfid not in target_factions: target_factions.append(pfid)
            cargo_value=_movement_cargo_value_cash(movement)
            next_route=route_index.get(str(movement.get("route_ref") or ""))
            if isinstance(next_route,Mapping): local_factions_for_route(next_route)
            existing={
                (str(row.get("beneficiary_ref") or ""),str(row.get("target_movement_ref") or ""))
                for row in movements.values() if isinstance(row,Mapping) and row.get("movement_kind")=="route_pursuit"
            }
            started=[]; blocked=unavailable_person_refs()
            for candidate in list(local_factions_by_place.get(stop_place,[])):
                attacker_fid=str(candidate.get("faction_id") or "")
                if not attacker_fid or attacker_fid in target_factions or (attacker_fid,movement_ref) in existing: continue
                attacker_type=current_faction_type(attacker_fid)
                relation_options=[directed_relation(attacker_fid,target) for target in target_factions if target and target != attacker_fid]
                relation_options=[row for row in relation_options if isinstance(row,Mapping)]
                relation=max(relation_options,key=lambda row:(max(0,int(row.get("hostility",0))),-int(row.get("trust",0))),default=None)
                hostility=max(0,int(relation.get("hostility",0))) if isinstance(relation,Mapping) else 0
                if attacker_type != "outlaw_faction" and hostility < 55: continue
                try: afpath,attacker=load_faction(attacker_fid); arpath,roster=load_roster(attacker_fid)
                except (FileNotFoundError,KeyError,ValueError): continue
                rows=roster.get("people",[]) if isinstance(roster,Mapping) else []
                if not isinstance(rows,list): continue
                hq=str(attacker.get("headquarters") or "")
                attendees=[
                    row for row in rows if isinstance(row,Mapping) and isinstance(row.get("person_id"),str)
                    and str(row.get("person_id")) not in blocked
                    and person_attends_site(row,site_ref=site_ref,site=site,faction_headquarters=hq,sites=site_rows,at=at,unavailable_refs=blocked)
                ]
                observer=best_route_observer(attendees)
                if not isinstance(observer,Mapping): continue
                observed=observed_escort_strength(observer=observer,escorts=escorts,world_seed=world_seed,observation_ref=f"settlement-stop|{movement_ref}|{stop_place}|{attacker_fid}|{at_iso}")
                confidence=max(0,min(1000,int(observed.get("confidence_milli",0))))
                known_cargo=0
                if cargo_value>0 and stable_permille("settlement-cargo-recognition",movement_ref,stop_place,attacker_fid,at_iso) < max(180,confidence):
                    # Visible wagons/pack train reveal scale, never exact books.
                    error=700+stable_permille("settlement-cargo-estimate",movement_ref,attacker_fid,at_iso)*600//999
                    known_cargo=max(1,cargo_value*error//1000)
                known_ransom=0
                if actual_ransom>0 and stable_permille("settlement-principal-recognition",movement_ref,stop_place,attacker_fid,at_iso) < max(100,confidence):
                    known_ransom=actual_ransom
                local_available=[]
                for row in usable_martial_people(roster,exclude_committed=blocked):
                    if person_settlement(row,faction_headquarters=hq,sites=site_rows) == stop_place:
                        local_available.append(row)
                if not local_available: continue
                local_available.sort(key=lambda person:(-person_combat_index(person),str(person.get("person_id") or "")))
                desired=max(2,int(observed.get("visible_escort_count",len(escort_refs)))*2+1)
                if max(known_cargo,known_ransom)>=50_000: desired+=2
                enterprises=attacker.get("enterprises",{}) if isinstance(attacker.get("enterprises"),Mapping) else {}
                criminal_level=max(0,int(enterprises.get("criminal_enterprise",0)))
                criminal_scale=enterprise_scale_value(attacker,"criminal_enterprise") if criminal_level>0 else 0
                autonomy=attacker.get("autonomy_policy",{}) if isinstance(attacker.get("autonomy_policy"),Mapping) else {}
                force_size=interception_force_size(
                    available_count=len(local_available), observed_escort_count=max(1,int(observed.get("visible_escort_count",len(escort_refs)))),
                    hostility=hostility, criminal_scale=criminal_scale, risk_tolerance=max(0,int(autonomy.get("risk_tolerance",50))),
                    known_value_cash=max(known_cargo,known_ransom), attacker_faction_type=attacker_type,
                )
                attackers=local_available[:force_size]
                if not attackers: continue
                own_index=max(1,sum(person_combat_index(row) for row in attackers)//len(attackers))
                policy=attacker.get("outlaw_policy",{}) if isinstance(attacker.get("outlaw_policy"),Mapping) else {}
                decision=interception_decision(
                    attacker_faction_type=attacker_type,relation=relation,own_available_martial=len(attackers),own_combat_index=own_index,
                    observed_escort_count=max(1,int(observed.get("visible_escort_count",len(escort_refs)))),observed_escort_combat_index=max(1,int(observed.get("estimated_combat_index",1))),
                    cargo_value_cash=known_cargo,ransom_value_cash=known_ransom,risk_tolerance=max(0,int(autonomy.get("risk_tolerance",50))),
                    government_risk_milli=250,minimum_attack_advantage_milli=max(650,int(policy.get("minimum_attack_advantage_milli",1100))),
                    civilian_restraint=max(0,int((attacker.get("doctrine",{}) or {}).get("civilian_restraint",0))) if isinstance(attacker.get("doctrine",{}),Mapping) else 0,
                )
                if not decision.get("attack"): continue
                attacker,roster,_boundary=settle_and_reset_faction_training_cycle(attacker,roster,at_iso=at_iso)
                writes[afpath]=attacker; writes[arpath]=roster; faction_cache[attacker_fid]=(afpath,attacker); roster_cache[attacker_fid]=(arpath,roster)
                refs=[str(row.get("person_id")) for row in attackers if isinstance(row.get("person_id"),str)]
                pursuit_ref=f"pursuit:{movement_ref}:{attacker_fid}:{at.strftime('%Y%m%d%H%M%S')}"
                try:
                    commitments_state=reserve_resources(
                        commitments_state,resources=[("person",ref,attacker_fid) for ref in refs],actor_ref=refs[0],owner_ref=attacker_fid,
                        activity_ref=pursuit_ref,activity_kind="route_pursuit",started_at=at_iso,location_ref=stop_place,
                    )
                except (ValueError,IndexError): continue
                pause_people_for_commitment(attacker_fid,refs)
                ready_hours=2+stable_permille("settlement-pursuit-mobilization",pursuit_ref,at_iso)*7//999
                movements[pursuit_ref]={
                    "movement_kind":"route_pursuit","target_movement_ref":movement_ref,"route_ref":str(movement.get("route_ref") or ""),
                    "origin_place_ref":stop_place,"destination_place_ref":str(movement.get("segment_destination_place_ref") or movement.get("destination_place_ref") or ""),
                    "beneficiary_ref":attacker_fid,"participant_refs":refs,"escort_refs":refs,"started_at":at_iso,
                    "ready_at":(at+timedelta(hours=ready_hours)).isoformat(),"status":"pursuing","contact_intent":str(decision.get("intent") or "hostile_interception"),
                }
                existing.add((attacker_fid,movement_ref)); started.append(pursuit_ref)
            if started: writes[_ROUTE_OPERATIONS_PATH]=route_ops_state
            return started

        def _begin_intermediate_route_stop(movement_ref: str, movement: dict[str, Any]) -> bool:
            """Advance one multi-edge physical journey into a real settlement stop.

            Contracts and deployments own purpose; this route owner owns the
            bodies while they are physically travelling.  Every supported
            journey therefore reaches the intermediate settlement, chooses a
            real inn or field camp, rests, can be observed there, then resumes
            the already-selected next road.
            """
            movement_kind = str(movement.get("movement_kind") or "")
            if movement_kind not in {
                "escort_contract", "player_strategic_travel", "faction_operation_travel",
                "merchant_trade", "escort_return", "raid_return", "escort_emergency_return",
            }:
                return False

            stop_place = str(movement.get("segment_destination_place_ref") or "")
            if not stop_place:
                return False

            # Current physical journeys carry their complete deterministic path.
            # An invalid path is a corrupt owner and must fail closed rather than
            # being mistaken for a completed journey.
            next_leg = begin_next_segment(movement, at=at)
            if next_leg is None:
                return False

            participants=[str(x) for x in movement.get("participant_refs",[]) if isinstance(x,str)]
            if not participants:
                return False
            resting_refs=[str(x) for x in movement.get("escort_refs",participants) if isinstance(x,str)]
            resting_people=[]
            for ref in resting_refs:
                try:
                    _fid,_path,_owner,_ordinal,person=load_person_ref(ref)
                except (FileNotFoundError,KeyError,TypeError,ValueError):
                    continue
                health=person.get("health",{}) if isinstance(person.get("health"),Mapping) else {}
                if health.get("status") != "dead": resting_people.append(person)
            rest_hours=max(8,escort_rest_hours(resting_people))
            lodging_base=max(1,int((economy_rules.get("consumables",{}) or {}).get("lodging_common_person_night",{}).get("base_value_cash",40))) if isinstance(economy_rules,Mapping) else 40
            nights=max(1,(rest_hours+23)//24); lodging_cost=lodging_base*max(1,len(participants))*nights

            # Covert/hostile expeditions do not announce themselves at inns.
            # Ordinary travel, formal delegations and escorts may buy lodging.
            operation_kind=str(movement.get("operation_kind") or "")
            force_field_camp = (
                (movement_kind == "faction_operation_travel" and operation_kind in {"faction_raid", "faction_war_strike", "custody_rescue"})
                or movement_kind == "raid_return"
            )
            lodging_ref = None if force_field_camp else _lodging_site(local_sites, stop_place)
            rest_mode = "inn" if lodging_ref else "field_camp"
            rest_location = str(lodging_ref or stop_place)
            if not lodging_ref and not force_field_camp:
                rest_hours = max(8, rest_hours * 2)
                lodging_cost = 0

            if rest_mode == "inn":
                payer_kind=str(movement.get("lodging_payer_kind") or ("person" if movement_kind=="player_strategic_travel" else "faction"))
                payer_ref=str(movement.get("lodging_payer_ref") or (movement.get("leader_ref") if payer_kind=="person" else movement.get("beneficiary_ref")) or "")
                paid=False
                stop_region=_movement_region(stop_place)
                lodging_market=None; lodging_market_path=""
                if stop_region:
                    try:
                        lodging_market_path,lodging_market=load_market(stop_region)
                    except (FileNotFoundError,ValueError):
                        lodging_market=None; lodging_market_path=""
                    if isinstance(lodging_market,Mapping) and lodging_market.get("region_id") not in (None,stop_region):
                        lodging_market=None; lodging_market_path=""
                # An inn purchase is only possible when its aggregate regional
                # recipient is resolvable before payment. Otherwise the party
                # keeps its silver and uses the deterministic field-camp fallback.
                if isinstance(lodging_market,dict) and payer_kind == "person" and payer_ref:
                    try:
                        _pfid,_ppath,_powner,_pordinal,payer=load_person_ref(payer_ref)
                        cash=max(0,int(payer.get("personal_cash",0)))
                    except (FileNotFoundError,KeyError,TypeError,ValueError):
                        payer={}; cash=0
                    if lodging_cost <= cash:
                        payer["personal_cash"]=cash-lodging_cost
                        save_exact_person(payer_ref,payer); paid=True
                elif isinstance(lodging_market,dict) and payer_kind == "faction" and payer_ref:
                    try:
                        fpath,faction=load_faction(payer_ref); cash=max(0,int(faction.get("treasury_cash",0)))
                    except (FileNotFoundError,KeyError,TypeError,ValueError):
                        faction={}; cash=0; fpath=""
                    if lodging_cost <= cash:
                        faction["treasury_cash"]=cash-lodging_cost; writes[fpath]=faction; faction_cache[payer_ref]=(fpath,faction); paid=True
                if paid and isinstance(lodging_market,dict) and stop_region:
                    lodging_market["cash_pool"]=max(0,int(lodging_market.get("cash_pool",0)))+lodging_cost
                    writes[lodging_market_path]=lodging_market; market_cache[stop_region]=(lodging_market_path,lodging_market)
                else:
                    rest_mode="field_camp"; rest_location=stop_place; rest_hours=max(8,rest_hours*2); lodging_cost=0

            move_exact_people_to_location(participants,rest_location)
            next_leg.update({
                "status":"lodging_rest" if rest_mode=="inn" else "field_rest",
                "resume_status":"active","rest_mode":rest_mode,"rest_place_ref":rest_location,
                "rest_hours_remaining":rest_hours,"rest_started_at":at_iso,
                "rest_last_progress_at":at_iso,"rest_until":(at+timedelta(hours=rest_hours)).isoformat(),
            })
            movements[movement_ref]=next_leg; writes[_ROUTE_OPERATIONS_PATH]=route_ops_state
            pending_one_off_events.append({
                "event_id":f"route_rest_due:{movement_ref}:{int(next_leg.get('route_index',0))}",
                "kind":"route_activity_cycle","due_at":str(next_leg["rest_until"]),
                "owner_ref":str(next_leg.get("route_ref") or ""),"movement_ref":movement_ref,
                "exact_rest_due":True,"requires_player_decision":False,
            })
            if rest_mode == "inn":
                _start_settlement_exposure_pursuits(movement_ref,next_leg,stop_place=stop_place,site_ref=rest_location)
            if player_ref and player_ref in participants:
                try: scene=copy.deepcopy(dict(read_json(_SCENE_PATH)))
                except FileNotFoundError: scene={}
                scene["location_id"]=rest_location; scene["present_person_ids"]=participants; scene["visible_person_ids"]=participants
                writes[_SCENE_PATH]=scene
                notice={
                    "kind":"travel_city_stop","movement_ref":movement_ref,"place_ref":stop_place,
                    "site_ref":rest_location,"rest_mode":rest_mode,"rest_hours":rest_hours,
                    "movement_kind":movement_kind,"delivered_to_player":True,"requires_player_decision":False,
                }
                handoff=classify_handoff(notice); handoffs.append({**notice,"handoff":handoff})
            return True

        def _start_seizure_return(
            movement_ref: str, movement: Mapping[str, Any], *, attacker_fid: str,
            attacker_refs: Sequence[str], intent: str, people_after: Mapping[str, Any] | None = None,
            allow_empty_return: bool = False,
        ) -> dict[str, Any]:
            """Move seized property/captives home after an exact combat victory.

            Combat decides control.  Capture creates continuing custody, not a
            ransom, payment, or abstract mission result.  Cargo remains physical
            cargo until the raiders reach home; captives travel with the actual
            surviving raider party and remain rescuable throughout.
            """
            nonlocal commitments_state, custody_state
            if not attacker_fid:
                return {"started": False}
            custody_before_seizure = copy.deepcopy(custody_state)
            living_attackers: list[str] = []
            for ref in [str(x) for x in attacker_refs if isinstance(x, str)]:
                person = people_after.get(ref) if isinstance(people_after, Mapping) else None
                if not isinstance(person, Mapping):
                    try:
                        _fid, _path, _owner, _ordinal, person = load_person_ref(ref)
                    except (FileNotFoundError, KeyError, TypeError, ValueError):
                        continue
                health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
                if health.get("status") != "dead" and int(health.get("consciousness", 100)) > 0:
                    living_attackers.append(ref)
            if not living_attackers:
                return {"started": False}

            captured_refs: list[str] = []
            rescued_refs: list[str] = []
            existing_captive_refs = [
                str(x) for x in movement.get("captive_refs", []) if isinstance(x, str) and x
            ] if movement.get("movement_kind") == "raid_return" else []
            active_records = {
                str(row.get("person_ref")): row for row in custody_state.get("records", [])
                if isinstance(row, dict) and isinstance(row.get("person_ref"), str)
                and row.get("status") not in {"released", "escaped", "rescued", "executed"}
            }

            # A defeated raider party cannot retain abstract custody. Control of
            # an already-captured person follows the actual combat result. Their
            # own family/faction frees them; another victorious faction becomes
            # the new physical holder. No second hostage object is created.
            for captive_ref in existing_captive_refs:
                record = active_records.get(captive_ref)
                if not isinstance(record, dict):
                    continue
                try:
                    captive_owner_fid, _path, _owner, _ordinal, captive = load_person_ref(captive_ref)
                except (FileNotFoundError, KeyError, TypeError, ValueError):
                    continue
                health = captive.get("health", {}) if isinstance(captive.get("health"), Mapping) else {}
                if health.get("status") == "dead":
                    continue
                family_fid = family_household_faction(family_state, captive_ref) or ""
                if attacker_fid in {captive_owner_fid, family_fid}:
                    custody_state["records"] = [row for row in custody_state.get("records", []) if row is not record]
                    rescued_refs.append(captive_ref)
                else:
                    record["captor_ref"] = living_attackers[0]
                    record["holder_faction_ref"] = attacker_fid
                    record["location_ref"] = str(movement.get("route_ref") or "")
                    captured_refs.append(captive_ref)

            if intent == "kidnap_principal":
                for protected_ref in [str(x) for x in movement.get("protected_person_refs", []) if isinstance(x, str)]:
                    if protected_ref in active_records or protected_ref in captured_refs or protected_ref in rescued_refs:
                        continue
                    person = people_after.get(protected_ref) if isinstance(people_after, Mapping) else None
                    if not isinstance(person, Mapping):
                        try:
                            _owner_fid, _path, _owner, _ordinal, person = load_person_ref(protected_ref)
                        except (FileNotFoundError, KeyError, TypeError, ValueError):
                            continue
                    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
                    if health.get("status") == "dead":
                        continue
                    record = create_custody_record(
                        person_ref=protected_ref, captor_ref=living_attackers[0], at=at_iso,
                        location_ref=str(movement.get("route_ref") or ""),
                        basis=f"route_kidnapping:{movement_ref}", holder_faction_ref=attacker_fid,
                    )
                    custody_state["records"].append(record)
                    captured_refs.append(protected_ref)
            if captured_refs or rescued_refs or existing_captive_refs:
                writes[_CUSTODY_PATH] = custody_state

            item_ref = str(movement.get("item_ref") or "")
            quantity = max(0, int(movement.get("quantity", 0)))
            carried_cash = max(0, int(movement.get("cash_quantity", 0)))
            cash_seizures: list[tuple[str, int, dict[str, Any]]] = []
            if intent in {"robbery", "rob_cargo", "extortion", "cargo_seizure", "hostile_interception"}:
                for ref in [str(x) for x in movement.get("participant_refs", []) if isinstance(x, str) and x]:
                    person = people_after.get(ref) if isinstance(people_after, Mapping) else None
                    if isinstance(person, Mapping):
                        person = copy.deepcopy(dict(person))
                    else:
                        try:
                            _owner_fid, _path, _owner, _ordinal, person = load_person_ref(ref)
                            person = copy.deepcopy(dict(person))
                        except (FileNotFoundError, KeyError, TypeError, ValueError):
                            continue
                    cash = max(0, int(person.get("personal_cash", 0)))
                    if cash > 0:
                        cash_seizures.append((ref, cash, person))
            seized_personal_cash = sum(row[1] for row in cash_seizures)
            cash_quantity = carried_cash + seized_personal_cash
            carried_refs = list(dict.fromkeys(captured_refs + rescued_refs))
            if quantity <= 0 and cash_quantity <= 0 and not carried_refs and not allow_empty_return:
                return {"started": False, "captured_refs": captured_refs, "rescued_refs": rescued_refs}

            _afpath, attacker_faction = load_faction(attacker_fid)
            headquarters = str(attacker_faction.get("headquarters") or "")
            route_ref = str(movement.get("route_ref") or "")
            return_ref = f"seizure_return:{movement_ref}:{attacker_fid}:{at.date().isoformat()}"
            if return_ref in movements:
                return {
                    "started": True, "movement_ref": return_ref, "captured_refs": captured_refs,
                    "rescued_refs": rescued_refs, "cargo_quantity": quantity,
                }
            try:
                commitments_state = reserve_resources(
                    commitments_state,
                    resources=[("person", ref, attacker_fid) for ref in living_attackers],
                    actor_ref=living_attackers[0], owner_ref=attacker_fid,
                    activity_ref=return_ref, activity_kind="seizure_return",
                    started_at=at_iso, location_ref=route_ref,
                )
            except ValueError:
                custody_state.clear(); custody_state.update(custody_before_seizure)
                return {
                    "started": False, "captured_refs": [], "rescued_refs": [],
                    "reason": "attackers_unavailable_for_return",
                }
            participants = list(living_attackers)
            for ref in carried_refs:
                if ref not in participants:
                    participants.append(ref)
            return_extra = {
                "escort_refs": living_attackers, "protected_person_refs": carried_refs,
                "raider_refs": living_attackers, "captive_refs": captured_refs,
                "rescued_refs": rescued_refs, "item_ref": item_ref, "quantity": quantity,
                "cash_quantity": cash_quantity,
            }
            returning = _fresh_faction_return_from_current_position(
                return_ref, movement, beneficiary=attacker_fid, participants=participants,
                destination_place=headquarters, movement_kind="raid_return", mode="convoy",
                extra=return_extra, inherit_transport=True,
            )
            if not isinstance(returning, Mapping):
                returning = _park_waiting_return(
                    return_ref, movement, beneficiary=attacker_fid, participants=participants,
                    destination_place=headquarters, movement_kind="raid_return", mode="convoy",
                    from_current_position=True, extra=return_extra,
                )
            # Cash changes hands only after the physical return owner exists.
            # If reservation/route construction failed above, victims retain it.
            for ref, _cash, person in cash_seizures:
                person["personal_cash"] = 0
                save_exact_person(ref, person)
            pause_people_for_commitment(attacker_fid, living_attackers)
            movements[return_ref] = returning
            if captured_refs:
                changed_location = False
                for record in custody_state.get("records", []):
                    if isinstance(record, dict) and str(record.get("person_ref") or "") in set(captured_refs) and record.get("status") not in {"released", "escaped", "rescued", "executed"}:
                        record["location_ref"] = return_ref; changed_location = True
                if changed_location:
                    writes[_CUSTODY_PATH] = custody_state
            writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
            return {
                "started": True, "movement_ref": return_ref, "captured_refs": captured_refs,
                "rescued_refs": rescued_refs, "cargo_quantity": quantity,
                "return_seconds": planned_journey_seconds(returning),
            }

        def _start_attacker_retreat_return(
            movement_ref: str, movement: Mapping[str, Any], *, attacker_fid: str,
            attacker_refs: Sequence[str], people_after: Mapping[str, Any] | None = None,
        ) -> dict[str, Any]:
            """Keep surviving failed attackers physically committed until home.

            A lost or inconclusive interception does not transfer cargo or custody.
            It only creates the minimum real return movement for the exact living
            attackers, so they cannot teleport home or be double-booked.
            """
            nonlocal commitments_state
            if not attacker_fid:
                return {"started": False}
            living: list[str] = []
            for ref in [str(x) for x in attacker_refs if isinstance(x, str)]:
                person = people_after.get(ref) if isinstance(people_after, Mapping) else None
                if not isinstance(person, Mapping):
                    try:
                        _fid, _path, _owner, _ordinal, person = load_person_ref(ref)
                    except (FileNotFoundError, KeyError, TypeError, ValueError):
                        continue
                health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
                if health.get("status") != "dead" and int(health.get("consciousness", 100)) > 0:
                    living.append(ref)
            if not living:
                return {"started": False}
            _afpath, attacker_faction = load_faction(attacker_fid)
            headquarters = str(attacker_faction.get("headquarters") or "")
            route_ref = str(movement.get("route_ref") or "")
            return_ref = bounded_raid_retreat_ref(movement_ref, attacker_fid, at_iso)
            if return_ref in movements:
                existing = movements.get(return_ref, {})
                return {
                    "started": True, "movement_ref": return_ref,
                    "return_seconds": planned_journey_seconds(existing) if isinstance(existing, Mapping) else 0,
                }
            try:
                commitments_state = reserve_resources(
                    commitments_state, resources=[("person", ref, attacker_fid) for ref in living],
                    actor_ref=living[0], owner_ref=attacker_fid, activity_ref=return_ref,
                    activity_kind="raid_return", started_at=at_iso, location_ref=route_ref,
                )
            except ValueError:
                return {"started": False, "reason": "attackers_unavailable_for_return"}
            returning = _fresh_faction_return_from_current_position(
                return_ref, movement, beneficiary=attacker_fid, participants=living,
                destination_place=headquarters, movement_kind="raid_return", mode="foot",
                extra={
                    "escort_refs": list(living), "protected_person_refs": [],
                    "raider_refs": list(living), "captive_refs": [], "rescued_refs": [],
                    "item_ref": "", "quantity": 0,
                },
                inherit_transport=False,
            )
            if not isinstance(returning, Mapping):
                returning = _park_waiting_return(
                    return_ref, movement, beneficiary=attacker_fid, participants=living,
                    destination_place=headquarters, movement_kind="raid_return", mode="foot",
                    from_current_position=True, extra={
                        "protected_person_refs": [], "captive_refs": [], "rescued_refs": [],
                        "item_ref": "", "quantity": 0,
                    },
                )
            pause_people_for_commitment(attacker_fid, living)
            movements[return_ref] = returning
            writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
            return {
                "started": True, "movement_ref": return_ref,
                "return_seconds": planned_journey_seconds(returning),
            }

        def _relink_lost_raid_operation_return(
            movement_ref: str, movement: Mapping[str, Any], *, raider_refs: Sequence[str],
        ) -> dict[str, Any]:
            """Keep defeated strategic raiders physical until home after re-seizure.

            The stolen objective has changed hands, but surviving members and any
            operation-issued gear still belong to the original strategic purpose
            until they return. Re-link that purpose to a cargo-free physical
            retreat instead of leaving a stale deployment or freeing people on the
            road.
            """
            nonlocal commitments_state
            op_ref = str(movement.get("purpose_ref") or movement.get("operation_ref") or "")
            deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
            op = deployments.get(op_ref) if op_ref and isinstance(deployments, dict) else None
            if not isinstance(op, Mapping):
                return {"started": False, "reason": "operation_missing"}
            survivors = _nondead_person_refs([str(x) for x in raider_refs if isinstance(x, str)])
            if not survivors:
                issue_refs = list(op.get("issued_equipment", {}).keys()) if isinstance(op.get("issued_equipment"), Mapping) else []
                _detach_operation_issue_refs(op_ref, issue_refs, status="operation_issue_stranded")
                deployments.pop(op_ref, None)
                writes[_DEPLOYMENTS_PATH] = deployments_state
                commitments_state = release_resources(commitments_state, activity_ref=op_ref)
                return {"started": False, "operation_closed": True, "reason": "no_surviving_raiders"}
            ready_controllers = _field_ready_person_refs(survivors)
            participants = list(ready_controllers) + [ref for ref in survivors if ref not in set(ready_controllers)]
            current = copy.deepcopy(dict(op))
            fid = str(current.get("faction_ref") or movement.get("beneficiary_ref") or "")
            if not fid:
                return {"started": False, "reason": "operation_faction_missing"}
            try:
                _fpath, faction = load_faction(fid)
                headquarters = str(faction.get("headquarters") or "")
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                headquarters = ""
            if not headquarters:
                return {"started": False, "reason": "operation_home_missing"}
            return_ref = bounded_raid_retreat_ref(movement_ref, fid, at_iso)
            extra = {
                "operation_ref": op_ref,
                "operation_kind": str(current.get("operation_kind") or "faction_raid"),
                "journey_phase": "return",
                "arrival_event_kind": "faction_operation_return",
                "targeting_intent": strategic_operation_targeting_intent(current),
            }
            if ready_controllers:
                if set(ready_controllers) != set(participants):
                    extra["escort_refs"] = list(ready_controllers)
                returning = _fresh_faction_return_from_current_position(
                    return_ref, movement, beneficiary=fid, participants=participants,
                    destination_place=headquarters, movement_kind="faction_operation_travel",
                    mode="foot", extra=extra, inherit_transport=False,
                )
            else:
                returning = None
            if not isinstance(returning, Mapping):
                parked_extra = copy.deepcopy(extra)
                if not ready_controllers:
                    parked_extra.pop("escort_refs", None)
                returning = _park_waiting_return(
                    return_ref, movement, beneficiary=fid, participants=participants,
                    destination_place=headquarters, movement_kind="faction_operation_travel",
                    mode="foot", from_current_position=True, extra=parked_extra,
                )
            for key in (
                "seized_cash", "seized_item_ref", "seized_quantity", "seized_cargo_bucket",
                "captive_refs", "return_escort_refs", "pending_travel_direction", "arrival_at",
            ):
                current.pop(key, None)
            current["participant_refs"] = participants
            current["status"] = "traveling_return"
            current["physical_movement_ref"] = return_ref
            deployments[op_ref] = current
            movements[return_ref] = copy.deepcopy(dict(returning))
            writes[_DEPLOYMENTS_PATH] = deployments_state
            writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
            return {"started": True, "movement_ref": return_ref, "operation_ref": op_ref}

        def _close_merchant_trade(
            movement_ref: str, movement: Mapping[str, Any], *, success: bool,
            attacker_fid: str | None = None, defer_seized_cargo: bool = False,
        ) -> dict[str, Any]:
            nonlocal commitments_state
            beneficiary = str(movement.get("beneficiary_ref") or "")
            participants = [str(x) for x in movement.get("participant_refs", []) if isinstance(x, str)]
            if not movement_ref or not beneficiary:
                return {"closed": False, "reason": "merchant_movement_invalid"}
            leg = str(movement.get("trade_leg") or "outbound")
            item_ref = str(movement.get("item_ref") or "")
            quantity = max(0, int(movement.get("quantity", 0)))

            if leg == "outbound" and not success:
                # Combat does not teleport merchant cargo into an attacker's
                # inventory.  When the generic interception layer successfully
                # staged a seizure return, that movement is the sole physical
                # owner of the cargo until it reaches the attacker's base.  If
                # no seizure owner exists, surviving merchants retain the cargo
                # while retreating.
                cargo_was_seized = bool(defer_seized_cargo and attacker_fid and quantity > 0)
                survivors = _living_person_refs(participants)
                if not survivors:
                    # With nobody left to control the caravan, any unseized
                    # cargo/provisions become local aggregate salvage rather than
                    # teleporting back to the beneficiary.  Seized cargo already
                    # belongs to the attacker's separate return movement.
                    place_ref = _extinguished_route_place(movement)
                    region = _movement_region(place_ref)
                    if not place_ref or not region:
                        raise ValueError("extinguished merchant salvage region unresolved")
                    salvage_quantity = 0 if cargo_was_seized else quantity
                    reservation = movement.get("provision_reservation")
                    unused_rations = 0
                    if isinstance(reservation, Mapping):
                        reserved = max(0, int(reservation.get("ration_days_reserved", 0)))
                        consumed = max(0, min(reserved, int(reservation.get("ration_days_consumed", 0))))
                        unused_rations = max(0, reserved - consumed)
                    if salvage_quantity > 0 or unused_rations > 0:
                        mpath, market = load_market(region)
                        stock = market.setdefault("stock", {})
                        if not isinstance(stock, dict):
                            raise ValueError("extinguished merchant salvage market stock invalid")
                        if salvage_quantity > 0:
                            if not item_ref:
                                raise ValueError("extinguished merchant cargo item unresolved")
                            stock[item_ref] = max(0, int(stock.get(item_ref, 0))) + salvage_quantity
                        if unused_rations > 0:
                            stock["food_ration_day"] = max(0, int(stock.get("food_ration_day", 0))) + unused_rations
                        writes[mpath] = market
                        market_cache[region] = (mpath, market)
                    commitments_state = release_resources(commitments_state, activity_ref=movement_ref)
                    movements.pop(movement_ref, None); writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                    return {
                        "closed": True, "success": False,
                        "cargo_lost": quantity if cargo_was_seized else 0,
                        "cargo_salvaged_locally": salvage_quantity,
                        "salvaged_ration_days": unused_rations,
                        "salvage_region": region,
                    }
                _refund_movement_provisions(movement)
                return_extra = {
                    "trade_leg": "return",
                    "trade_outcome": "cargo_lost" if cargo_was_seized else "unsold",
                    "item_ref": item_ref,
                    "quantity": 0 if cargo_was_seized else quantity,
                }
                returning = _fresh_faction_return_from_current_position(
                    movement_ref, movement, beneficiary=beneficiary, participants=survivors,
                    destination_place=str(movement.get("origin_place_ref") or ""),
                    movement_kind="merchant_trade", mode="convoy", extra=return_extra,
                )
                if not isinstance(returning, Mapping):
                    returning = _park_waiting_return(
                        movement_ref, movement, beneficiary=beneficiary, participants=survivors,
                        destination_place=str(movement.get("origin_place_ref") or ""),
                        movement_kind="merchant_trade", mode="convoy", from_current_position=True,
                        extra=return_extra,
                    )
                    movements[movement_ref] = returning
                    writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                    return {
                        "closed": False, "success": False, "cargo_lost": quantity,
                        "returning_after_loss": False, "awaiting_return_logistics": True,
                    }
                movements[movement_ref] = returning
                writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                return {"closed": False, "success": False, "returning_after_loss": True, "cargo_lost": quantity}

            if leg == "outbound":
                destination_place = str(movement.get("destination_place_ref") or "")
                destination_region = _movement_region(destination_place)
                sale_cash = 0; sale_succeeded = False
                if item_ref and quantity > 0 and destination_region:
                    try:
                        dmpath, destination_market = load_market(destination_region)
                        sold = execute_sale(
                            destination_region, item_ref, quantity, destination_market,
                            seller_stock=quantity, seller_cash=0,
                        )
                        destination_market = copy.deepcopy(dict(sold["market_state_after"]))
                        sale_cash = max(0, int(sold["seller_cash_after"]))
                        fpath, faction = load_faction(beneficiary)
                        faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + sale_cash
                        writes[fpath] = faction; faction_cache[beneficiary] = (fpath, faction)
                        writes[dmpath] = destination_market; market_cache[destination_region] = (dmpath, destination_market)
                        sale_succeeded = True
                    except (KeyError, TypeError, ValueError, FileNotFoundError):
                        sale_succeeded = False
                _move_faction_people(beneficiary, participants, destination_place)
                home_place = str(movement.get("origin_place_ref") or "")
                return_extra = {
                    "trade_leg": "return", "trade_outcome": "sold" if sale_succeeded else "unsold", "item_ref": item_ref,
                    "quantity": 0 if sale_succeeded else quantity,
                }
                _refund_movement_provisions(movement)
                returning = _fresh_faction_return_journey(
                    movement_ref, movement, beneficiary=beneficiary, participants=participants,
                    origin_place=destination_place, destination_place=home_place,
                    movement_kind="merchant_trade", mode="convoy", extra=return_extra,
                )
                if not isinstance(returning, Mapping):
                    # Return logistics are an unresolved physical obligation, not
                    # permission to reuse staff remotely.  Keep the exact people
                    # reserved at the destination until a later route wake can
                    # provision a real return leg.
                    returning = _park_waiting_return(
                        movement_ref, movement, beneficiary=beneficiary, participants=participants,
                        origin_place=destination_place, destination_place=home_place,
                        movement_kind="merchant_trade", mode="convoy", from_current_position=False,
                        extra=return_extra,
                    )
                    movements[movement_ref] = returning
                    writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                    return {
                        "closed": False, "success": sale_succeeded, "sale_cash": sale_cash,
                        "returning": False, "awaiting_return_logistics": True,
                    }
                movements[movement_ref] = returning
                writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                return {"closed": False, "success": sale_succeeded, "returning": True, "sale_cash": sale_cash}

            # Return leg. Any unsold cargo remains a real faction asset rather
            # than vanishing because the destination market ran out of cash.
            home_place = str(movement.get("destination_place_ref") or "")
            _move_faction_people(beneficiary, participants, home_place)
            if quantity > 0 and item_ref:
                ipath, inventory = load_inventory(beneficiary)
                _credit_cargo_to_inventory(inventory, item_ref=item_ref, quantity=quantity)
                writes[ipath] = inventory; inventory_cache[beneficiary] = (ipath, inventory)
            commitments_state = settle_and_resume_people(
                participants, activity_ref=movement_ref, commitments_state=commitments_state,
            )
            trade_outcome = str(movement.get("trade_outcome") or ("unsold" if quantity > 0 else "sold"))
            _refund_movement_provisions(movement)
            movements.pop(movement_ref, None)
            writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
            return {
                "closed": True, "success": trade_outcome == "sold",
                "trade_outcome": trade_outcome, "unsold_quantity": quantity,
            }

        def _close_escort(
            movement_ref: str, movement: Mapping[str, Any], *, success: bool,
            attacker_fid: str | None = None, defer_seized_cargo: bool = False,
            captured_refs: Sequence[str] = (),
        ) -> dict[str, Any]:
            nonlocal commitments_state, contract_index, active_contracts, reputation_state, social_state, equipment_ledger
            if movement.get("movement_kind") == "merchant_trade":
                return _close_merchant_trade(
                    movement_ref, movement, success=success, attacker_fid=attacker_fid,
                    defer_seized_cargo=defer_seized_cargo,
                )
            if movement.get("movement_kind") == "raid_return":
                participants = [str(x) for x in movement.get("participant_refs", []) if isinstance(x, str)]
                raider_refs = route_controlling_refs(movement)
                home_place = str(movement.get("destination_place_ref") or "")
                if not success:
                    if defer_seized_cargo:
                        # A second force has won physical control of the objective.
                        # Close the old cargo/custody owner, then re-link surviving
                        # original raiders to a cargo-free return under their
                        # strategic operation so neither people nor issued gear
                        # become magically available on the road.
                        commitments_state = settle_and_resume_people(
                            raider_refs, activity_ref=movement_ref, commitments_state=commitments_state,
                        )
                        _refund_movement_provisions(movement)
                        movements.pop(movement_ref, None)
                        relink = _relink_lost_raid_operation_return(
                            movement_ref, movement, raider_refs=raider_refs,
                        )
                        writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                        return {
                            "closed": True, "success": False, "seized_again": True,
                            "captured_refs": [str(x) for x in captured_refs if isinstance(x, str)],
                            "retreat_movement_ref": str(relink.get("movement_ref") or ""),
                            "operation_closed": bool(relink.get("operation_closed")),
                        }
                    movements[movement_ref] = copy.deepcopy(dict(movement))
                    writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                    return {"closed": False, "success": False, "return_interrupted": True}
                item_ref = str(movement.get("item_ref") or "")
                quantity = max(0, int(movement.get("quantity", 0)))
                cash_quantity = max(0, int(movement.get("cash_quantity", 0)))
                beneficiary = str(movement.get("beneficiary_ref") or "")
                if beneficiary and item_ref and quantity > 0:
                    ipath, inventory = load_inventory(beneficiary)
                    _credit_cargo_to_inventory(inventory, item_ref=item_ref, quantity=quantity)
                    writes[ipath] = inventory; inventory_cache[beneficiary] = (ipath, inventory)
                if beneficiary and cash_quantity > 0:
                    bfpath, beneficiary_faction = load_faction(beneficiary)
                    beneficiary_faction["treasury_cash"] = max(0, int(beneficiary_faction.get("treasury_cash", 0))) + cash_quantity
                    writes[bfpath] = beneficiary_faction; faction_cache[beneficiary] = (bfpath, beneficiary_faction)
                arrival_location = _arrival_site(local_sites, home_place) or home_place
                if beneficiary:
                    try:
                        _bfpath, beneficiary_faction = load_faction(beneficiary)
                    except (KeyError, FileNotFoundError, ValueError):
                        beneficiary_faction = {}
                    arrival_location = str(beneficiary_faction.get("local_site_ref") or arrival_location)
                move_exact_people_to_location(participants, arrival_location)
                captive_refs = {str(x) for x in movement.get("captive_refs", []) if isinstance(x, str)}
                custody_responses: list[dict[str, Any]] = []
                if captive_refs:
                    changed = False
                    arrived_records: list[dict[str, Any]] = []
                    for row in custody_state.get("records", []):
                        if not (
                            isinstance(row, dict)
                            and str(row.get("person_ref")) in captive_refs
                            and row.get("status") not in {"released", "escaped", "rescued", "executed"}
                        ):
                            continue
                        # Arrival changes only physical custody location. A
                        # ransom demand is a separate future choice/message and
                        # is never manufactured by reaching the hideout.
                        row["location_ref"] = arrival_location
                        row["holder_faction_ref"] = beneficiary or str(row.get("holder_faction_ref") or "")
                        arrived_records.append(row)
                        changed = True
                    if changed:
                        writes[_CUSTODY_PATH] = custody_state
                    # Reaching a real holding site creates a later captor review,
                    # not an immediate ransom. That interval is a genuine rescue
                    # window, and the one-off event stores no duplicate hostage state.
                    for row in arrived_records:
                        custody_id = str(row.get("custody_id") or "")
                        if custody_id and int(row.get("ransom_demand_cash", 0)) <= 0:
                            review_roll = stable_permille("custody-captor-review-delay", custody_id, beneficiary, at_iso)
                            review_due = at + timedelta(hours=12 + review_roll * 24 // 999)
                            pending_one_off_events.append({
                                "event_id": f"custody_captor_review:{custody_id}",
                                "kind": "custody_captor_review", "due_at": review_due.isoformat(),
                                "owner_ref": custody_id, "person_ref": str(row.get("person_ref") or ""),
                                "requires_player_decision": False,
                            })
                    # If survivors already carried news home, reaching the known
                    # hideout is the next causal boundary at which those informed
                    # institutions can turn concern into a real rescue deployment.
                    for row in arrived_records:
                        for responder_fid in [
                            str(x) for x in row.get("informed_faction_refs", [])
                            if isinstance(x, str) and x
                        ]:
                            if responder_fid == str(row.get("holder_faction_ref") or ""):
                                continue
                            response = start_custody_rescue_operation(responder_fid, row)
                            custody_responses.append({
                                "person_ref": str(row.get("person_ref") or ""),
                                "responder_faction_ref": responder_fid,
                                "result": str(response.get("result") or "response_deferred"),
                                **({"operation_ref": str(response.get("operation_ref"))} if response.get("operation_ref") else {}),
                            })
                op_ref = str(movement.get("purpose_ref") or movement.get("operation_ref") or "")
                if op_ref:
                    deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
                    op = deployments.get(op_ref) if isinstance(deployments, Mapping) else None
                    if isinstance(op, Mapping):
                        current_op = copy.deepcopy(dict(op))
                        equipment_recovered: dict[str, int] = {}
                        equipment_lost_or_consumed: dict[str, int] = {}
                        if beneficiary and isinstance(current_op.get("issued_equipment"), Mapping):
                            ipath, inventory = load_inventory(beneficiary)
                            settled_issue = reclaim_operation_equipment(
                                operation=current_op, inventory=inventory, equipment_ledger=equipment_ledger,
                            )
                            current_op = copy.deepcopy(dict(settled_issue["operation_after"]))
                            equipment_ledger = copy.deepcopy(dict(settled_issue["equipment_ledger_after"]))
                            writes[ipath] = copy.deepcopy(dict(settled_issue["inventory_after"]))
                            inventory_cache[beneficiary] = (ipath, writes[ipath])
                            writes[_EQUIPMENT_LEDGER_PATH] = equipment_ledger
                            equipment_recovered = copy.deepcopy(dict(settled_issue.get("recovered", {})))
                            equipment_lost_or_consumed = copy.deepcopy(dict(settled_issue.get("lost_or_consumed", {})))
                        dossier_ref = str(current_op.get("institutional_operation_ref") or "")
                        if dossier_ref:
                            physical = copy.deepcopy(dict(current_op))
                            physical["seized_cash"] = cash_quantity; physical["seized_item_ref"] = item_ref; physical["seized_quantity"] = quantity; physical["captive_refs"] = sorted(captive_refs)
                            mission_success = str(physical.get("battle_winner_side") or "") == "side_a"
                            close_institutional_operation(read_json=read_json, writes=writes, operation_ref=dossier_ref, at_iso=at_iso, success=mission_success, closure_reason="returned_and_reported" if mission_success else "returned_after_failure", physical_operation=physical, returned_refs=raider_refs, casualties=physical.get("casualty_refs", []), equipment_recovered=equipment_recovered, equipment_lost_or_consumed=equipment_lost_or_consumed)
                        deployments.pop(op_ref, None)
                        writes[_DEPLOYMENTS_PATH] = deployments_state
                commitments_state = settle_and_resume_people(
                    raider_refs, activity_ref=movement_ref, commitments_state=commitments_state,
                )
                _refund_movement_provisions(movement)
                movements.pop(movement_ref, None)
                writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                return {
                    "closed": True, "success": True, "returned_home": True,
                    "participant_count": len(participants), "cargo_secured": quantity,
                    "cash_secured": cash_quantity,
                    "captive_count": len(captive_refs), "custody_responses": custody_responses,
                }
            if movement.get("movement_kind") == "escort_emergency_return":
                reporters = [str(x) for x in movement.get("participant_refs", []) if isinstance(x, str)]
                report_home = str(movement.get("destination_place_ref") or "")
                if not success:
                    # A report party can itself be intercepted. Surviving
                    # messengers remain physically committed and try to continue;
                    # no information is delivered merely because the event ran.
                    if reporters:
                        interrupted = copy.deepcopy(dict(movement))
                        interrupted["status"] = "active"
                        movements[movement_ref] = interrupted
                        writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                        return {"closed": False, "success": False, "report_interrupted": True}
                    commitments_state = settle_and_resume_people(
                        reporters, activity_ref=movement_ref, commitments_state=commitments_state,
                    )
                    _refund_movement_provisions(movement)
                    movements.pop(movement_ref, None)
                    writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                    return {"closed": True, "success": False, "report_lost": True}

                move_exact_people(reporters, report_home)
                recipient_fids = [
                    str(x) for x in movement.get("report_recipient_faction_refs", [])
                    if isinstance(x, str) and x
                ]
                report_holder = str(movement.get("report_holder_faction_ref") or "")
                report_captives = [
                    str(x) for x in movement.get("report_captive_refs", [])
                    if isinstance(x, str) and x
                ]
                response_rows: list[dict[str, Any]] = []
                custody_changed = False
                for captive_ref in report_captives:
                    record = next((
                        row for row in custody_state.get("records", [])
                        if isinstance(row, dict)
                        and str(row.get("person_ref") or "") == captive_ref
                        and row.get("status") not in {"released", "escaped", "rescued", "executed"}
                        and (not report_holder or str(row.get("holder_faction_ref") or "") == report_holder)
                    ), None)
                    if not isinstance(record, dict):
                        continue
                    for responder_fid in recipient_fids:
                        if not responder_fid or responder_fid == str(record.get("holder_faction_ref") or ""):
                            continue
                        known_before = {
                            str(x) for x in record.get("informed_faction_refs", [])
                            if isinstance(x, str) and x
                        }
                        if responder_fid not in known_before:
                            informed = mark_custody_informed(record, faction_ref=responder_fid)
                            record.clear(); record.update(informed)
                            custody_changed = True
                            apply_directed_relation_event(responder_fid, str(record.get("holder_faction_ref") or ""), "member_abducted")
                        outcome = start_custody_rescue_operation(responder_fid, record)
                        response_rows.append({
                            "person_ref": captive_ref,
                            "responder_faction_ref": responder_fid,
                            "result": str(outcome.get("result") or "response_deferred"),
                            **({"operation_ref": str(outcome.get("operation_ref"))} if outcome.get("operation_ref") else {}),
                        })
                if custody_changed:
                    writes[_CUSTODY_PATH] = custody_state
                commitments_state = settle_and_resume_people(
                    reporters, activity_ref=movement_ref, commitments_state=commitments_state,
                )
                contract_ref = str(movement.get("contract_ref") or "")
                if contract_ref:
                    close_linked_contract_operation(read_json=read_json, writes=writes, contract_ref=contract_ref, at_iso=at_iso, success=False, closure_reason="escort_failed_report_delivered", returned_refs=reporters, extra_report={"contract_ref": contract_ref, "reported_captive_refs": [str(x) for x in movement.get("report_captive_refs", []) if isinstance(x, str)]})
                _refund_movement_provisions(movement)
                movements.pop(movement_ref, None)
                writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                return {
                    "closed": True, "success": True, "report_delivered": True,
                    "reporter_count": len(reporters), "responses": response_rows,
                }
            if movement.get("movement_kind") == "faction_operation_travel":
                participants = [str(x) for x in movement.get("participant_refs", []) if isinstance(x, str)]
                op_ref = str(movement.get("purpose_ref") or movement.get("operation_ref") or "")
                deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
                op = deployments.get(op_ref) if isinstance(deployments, Mapping) else None
                phase = str(movement.get("journey_phase") or "outbound")
                if not isinstance(op, Mapping):
                    commitments_state = settle_and_resume_people(
                        participants, activity_ref=op_ref or movement_ref, commitments_state=commitments_state,
                    )
                    _refund_movement_provisions(movement)
                    movements.pop(movement_ref, None)
                    writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                    return {"closed": True, "success": False, "operation_missing": True}
                if not success:
                    if phase == "return":
                        # A defeated return party remains physically on the road;
                        # exact casualties are already applied and survivors must
                        # still complete or otherwise resolve their journey.
                        interrupted = copy.deepcopy(dict(movement))
                        interrupted["status"] = "active"
                        interrupted["last_progress_at"] = at_iso
                        movements[movement_ref] = interrupted
                        writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                        return {"closed": False, "success": False, "return_interrupted": True}
                    commitments_state = settle_and_resume_people(
                        participants, activity_ref=op_ref, commitments_state=commitments_state,
                    )
                    issue_refs = list(op.get("issued_equipment", {}).keys()) if isinstance(op.get("issued_equipment"), Mapping) else []
                    _detach_operation_issue_refs(op_ref, issue_refs, status="operation_issue_stranded")
                    dossier_ref = str(op.get("institutional_operation_ref") or "")
                    if dossier_ref:
                        close_institutional_operation(read_json=read_json, writes=writes, operation_ref=dossier_ref, at_iso=at_iso, success=False, closure_reason="operation_stopped_on_route", physical_operation=op, returned_refs=[])
                    deployments.pop(op_ref, None)
                    _refund_movement_provisions(movement)
                    movements.pop(movement_ref, None)
                    writes[_DEPLOYMENTS_PATH] = deployments_state
                    writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                    return {"closed": True, "success": False, "operation_stopped_on_route": True}

                current = copy.deepcopy(dict(op))
                destination_place = str(movement.get("destination_place_ref") or "")
                destination_site = str(movement.get("destination_site_ref") or "")
                arrival = destination_site or _arrival_site(local_sites, destination_place) or destination_place
                if arrival:
                    move_exact_people_to_location(participants, arrival)
                _refund_movement_provisions(movement)
                movements.pop(movement_ref, None)
                current.pop("physical_movement_ref", None)
                if phase == "return":
                    equipment_recovered: dict[str, int] = {}
                    equipment_lost_or_consumed: dict[str, int] = {}
                    fid = str(current.get("faction_ref") or "")
                    if fid and isinstance(current.get("issued_equipment"), Mapping):
                        ipath, inventory = load_inventory(fid)
                        settled_issue = reclaim_operation_equipment(
                            operation=current, inventory=inventory, equipment_ledger=equipment_ledger,
                        )
                        current = copy.deepcopy(dict(settled_issue["operation_after"]))
                        equipment_ledger = copy.deepcopy(dict(settled_issue["equipment_ledger_after"]))
                        inventory = copy.deepcopy(dict(settled_issue["inventory_after"]))
                        writes[ipath] = inventory
                        inventory_cache[fid] = (ipath, inventory)
                        writes[_EQUIPMENT_LEDGER_PATH] = equipment_ledger
                        equipment_recovered = copy.deepcopy(dict(settled_issue.get("recovered", {})))
                        equipment_lost_or_consumed = copy.deepcopy(dict(settled_issue.get("lost_or_consumed", {})))
                    commitments_state = settle_and_resume_people(
                        participants, activity_ref=op_ref, commitments_state=commitments_state,
                    )
                    followup = current.get("repatriate_after_return") if isinstance(current.get("repatriate_after_return"), Mapping) else None
                    followup_ref = ""
                    if isinstance(followup, Mapping):
                        person_ref = str(followup.get("person_ref") or "")
                        owner_fid = str(followup.get("owner_faction_ref") or "")
                        try:
                            _owner_path, owner_faction = load_faction(owner_fid) if owner_fid else ("", {})
                            owner_home = str(owner_faction.get("headquarters") or "") if isinstance(owner_faction, Mapping) else ""
                            followup_ref, followup_op, followup_event = build_repatriation_operation(
                                person_ref=person_ref, owner_faction_ref=owner_fid, origin_place_ref=destination_place,
                                home_place_ref=owner_home, at=at, cause_ref=str(followup.get("cause_ref") or op_ref),
                                counterparty_faction_ref=fid,
                            )
                        except (FileNotFoundError, KeyError, TypeError, ValueError):
                            followup_ref = ""
                        if followup_ref and followup_ref not in deployments:
                            deployments[followup_ref] = followup_op
                            pending_one_off_events.append(followup_event)
                            try:
                                _pfid, _ppath, _powner, _ordinal, _person = load_person_ref(person_ref)
                                commitments_state = reserve_resources(
                                    commitments_state, resources=[("person", person_ref, owner_fid or _pfid)],
                                    actor_ref=person_ref, owner_ref=owner_fid or _pfid, activity_ref=followup_ref,
                                    activity_kind="captive_repatriation", started_at=at_iso, location_ref=destination_place,
                                )
                            except (FileNotFoundError, KeyError, TypeError, ValueError):
                                deployments.pop(followup_ref, None); followup_ref = ""
                    dossier_ref = str(current.get("institutional_operation_ref") or "")
                    if dossier_ref:
                        operation_kind = str(current.get("operation_kind") or "")
                        mission_success = (
                            bool(current.get("intelligence_report")) if operation_kind == "faction_reconnaissance" else
                            bool(current.get("rescue_success")) if operation_kind == "custody_rescue" else
                            str(current.get("battle_winner_side") or "") == "side_a" if operation_kind in {"faction_raid", "faction_war_strike"} else
                            True
                        )
                        close_institutional_operation(
                            read_json=read_json, writes=writes, operation_ref=dossier_ref, at_iso=at_iso,
                            success=mission_success, closure_reason="returned_and_reported" if mission_success else "returned_after_failure",
                            physical_operation=current, returned_refs=participants, casualties=current.get("casualty_refs", []),
                            equipment_recovered=equipment_recovered, equipment_lost_or_consumed=equipment_lost_or_consumed,
                        )
                    deployments.pop(op_ref, None)
                    writes[_DEPLOYMENTS_PATH] = deployments_state
                    writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                    return {
                        "closed": True, "success": True, "returned_home": True,
                        "operation_ref": op_ref, "participant_count": len(participants),
                        "repatriation_operation_ref": followup_ref,
                        "equipment_recovered": equipment_recovered,
                        "equipment_lost_or_consumed": equipment_lost_or_consumed,
                    }

                current["status"] = "arrived_pending"
                current["participant_refs"] = participants
                deployments[op_ref] = current
                arrival_kind = str(movement.get("arrival_event_kind") or "")
                if not arrival_kind:
                    op_kind = str(current.get("operation_kind") or "")
                    arrival_kind = (
                        "tournament_travel_arrival" if op_kind == "tournament_travel"
                        else "tournament_delegation_arrival" if op_kind == "tournament_delegation"
                        else "faction_operation_arrival"
                    )
                pending_one_off_events.append({
                    "event_id": f"operation_arrival:{op_ref}", "kind": arrival_kind,
                    "due_at": (at + timedelta(seconds=1)).isoformat(), "owner_ref": op_ref,
                    "requires_player_decision": False,
                })
                writes[_DEPLOYMENTS_PATH] = deployments_state
                writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                return {
                    "closed": True, "success": True, "arrived_pending": True,
                    "operation_ref": op_ref, "arrival_event_kind": arrival_kind,
                    "participant_count": len(participants),
                }

            if movement.get("movement_kind") == "player_strategic_travel":
                participants = [str(x) for x in movement.get("participant_refs", []) if isinstance(x, str)]
                if success:
                    destination_site = str(movement.get("destination_site_ref") or "")
                    destination_place = str(movement.get("destination_place_ref") or "")
                    arrival = destination_site or _arrival_site(local_sites, destination_place) or destination_place
                    move_exact_people_to_location(participants, arrival)
                    commitments_state = settle_and_resume_people(
                        participants, activity_ref=movement_ref, commitments_state=commitments_state,
                    )
                    movement_contact = str(movement.get("contact_ref") or "")
                    if movement_contact:
                        contacts.pop(movement_contact, None)
                    _refund_movement_provisions(movement)
                    movements.pop(movement_ref, None)
                    if player_ref and player_ref in participants:
                        try:
                            scene = copy.deepcopy(dict(read_json(_SCENE_PATH)))
                        except FileNotFoundError:
                            scene = {}
                        scene.pop("active_combat_ref", None)
                        scene["location_id"] = arrival
                        scene["present_person_ids"] = participants
                        scene["visible_person_ids"] = participants
                        writes[_SCENE_PATH] = scene
                    writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                    return {"closed": True, "success": True, "arrived": True, "participant_count": len(participants)}

                # A defeated ordinary traveling party stops on the road. Any
                # robbery proceeds have already been transferred onto the real
                # seizure-return movement above. Never credit an outlaw treasury
                # directly at combat resolution.
                commitments_state = settle_and_resume_people(
                    participants, activity_ref=movement_ref, commitments_state=commitments_state,
                )
                movement_contact = str(movement.get("contact_ref") or "")
                if movement_contact:
                    contacts.pop(movement_contact, None)
                _refund_movement_provisions(movement)
                movements.pop(movement_ref, None)
                writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                return {
                    "closed": True, "success": False, "journey_stopped": True,
                    "participant_count": len(participants),
                }

            if movement.get("movement_kind") == "escort_return":
                participants = [str(x) for x in movement.get("participant_refs", []) if isinstance(x, str)]
                home_place = str(movement.get("destination_place_ref") or "")
                if success:
                    move_exact_people(participants, home_place)
                    commitments_state = settle_and_resume_people(
                        participants, activity_ref=movement_ref, commitments_state=commitments_state,
                    )
                    contract_ref = str(movement.get("contract_ref") or "")
                    if contract_ref:
                        close_linked_contract_operation(read_json=read_json, writes=writes, contract_ref=contract_ref, at_iso=at_iso, success=True, closure_reason="escort_returned_and_reported", returned_refs=participants, extra_report={"contract_ref": contract_ref})
                    _refund_movement_provisions(movement)
                    movements.pop(movement_ref, None)
                    writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                    return {"closed": True, "success": True, "returned_home": True, "participant_count": len(participants)}
                # A failed return trip remains a physical route problem.  The
                # exact combat path removes dead people before this branch;
                # surviving escorts stay committed until another causal wake.
                movements[movement_ref] = copy.deepcopy(dict(movement))
                writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                return {"closed": False, "success": False, "return_interrupted": True}
            cid = str(movement.get("contract_ref") or movement_ref or "")
            contract = active_after.get(cid)
            if not cid or not isinstance(contract, Mapping):
                return {"closed": False, "reason": "contract_missing"}
            beneficiary = str(movement.get("beneficiary_ref") or contract.get("beneficiary_ref") or "")
            participants = [str(x) for x in movement.get("participant_refs", []) if isinstance(x, str)]
            item_ref = str(movement.get("item_ref") or "")
            quantity = max(0, int(movement.get("quantity", 0)))
            contact_ref = str(movement.get("contact_ref") or "")
            contact_attackers = [str(x) for x in movement.get("contact_attacker_refs", []) if isinstance(x, str)]
            if contact_ref and contact_attackers:
                commitments_state = settle_and_resume_people(
                    contact_attackers, activity_ref=contact_ref, commitments_state=commitments_state,
                )
            if success:
                destination_place = str(movement.get("destination_place_ref") or "")
                destination_region = _movement_region(destination_place)
                # Person-only escorts do not create a phantom empty-string cargo
                # stack. Only real carried goods are delivered to market stock.
                if item_ref and quantity > 0 and destination_region:
                    mpath, market = load_market(destination_region)
                    stock = market.setdefault("stock", {})
                    if not isinstance(stock, dict):
                        raise ValueError("jianghu destination market stock invalid")
                    stock[item_ref] = max(0, int(stock.get(item_ref, 0))) + quantity
                    writes[mpath] = market
                    market_cache[destination_region] = (mpath, market)
                resolved = contract_transition(contract, at=at_iso, to_status="objective_resolved", actor_ref=participants[0] if participants else beneficiary)
                payment = settle_payment(resolved, success=True)
                settled = contract_transition(resolved, at=at_iso, to_status="settled", actor_ref=participants[0] if participants else beneficiary)
                settled["escrow_cash"] = int(payment["escrow_after"])
                if beneficiary:
                    fpath, faction = load_faction(beneficiary)
                    faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + int(payment["paid_cash"])
                    writes[fpath] = faction
                    faction_cache[beneficiary] = (fpath, faction)
                # Delivery ends the client obligation, but not the escort
                # personnel's physical journey.  Everyone reaches the destination;
                # protected principals disembark there while surviving escorts stay
                # committed to this route owner through rest and the return leg.
                # Everyone physically reaches the destination settlement.
                # Surviving escorts who need rest then occupy one real public
                # inn (or, if they cannot afford lodging, remain in a field
                # camp at the settlement) so observation/social exposure uses
                # an actual place rather than an abstract `resting` flag.
                move_exact_people(participants, destination_place)
                paid = int(payment["paid_cash"])
                # Contract revenue belongs to the beneficiary treasury, while the
                # institutional dossier preserves that settlement for the later AAR.
                stage_linked_contract_phase(
                    read_json=read_json, writes=writes, contract_ref=cid, phase="returning",
                    at_iso=at_iso, details={"contract_revenue_cash": paid},
                )
                public_audience = f"public:{destination_place}" if destination_place else ""
                for ref in participants:
                    reputation_state = _reputation_after_points(reputation_state, ref, contract_points=1)
                    if public_audience:
                        reputation_state = apply_personal_fame_evidence(
                            reputation_state, audience_ref=public_audience, person_ref=ref,
                            evidence_kind="fulfilled_contract", delivered=True,
                        )
                if beneficiary and public_audience:
                    reputation_state = apply_faction_awareness_evidence(
                        reputation_state, audience_ref=public_audience, faction_ref=beneficiary,
                        evidence_kind="public_contract", delivered=True,
                    )
                    reputation_state = apply_faction_reputation_evidence(
                        reputation_state, audience_ref=public_audience, faction_ref=beneficiary,
                        axis_deltas={"reliability": 3, "trustworthiness": 2}, delivered=True,
                    )
                writes[_REPUTATION_PATH] = reputation_state
                issuer = str(contract.get("issuer_ref") or "")
                if issuer and not issuer.startswith("market:") and beneficiary and issuer != beneficiary:
                    apply_directed_relation_event(issuer, beneficiary, "honored_contract")
                    apply_directed_relation_event(beneficiary, issuer, "honored_contract")

                # A completed escort is real prolonged shared travel. Shared
                # danger is applied at the exact combat boundary itself, so no
                # encounter-history list is retained on the movement owner.
                social_party = participants[:12]
                if len(social_party) > 1:
                    social_state = apply_sparse_group_relationship_event(
                        social_state, participant_refs=social_party, event_kind="shared_travel",
                        severity_milli=350, protected_player_ref=player_ref or "pc_wei_tang",
                    )["state_after"]
                    writes[_SOCIAL_PATH] = social_state
                escort_refs = [str(x) for x in movement.get("escort_refs", []) if isinstance(x, str)]
                escort_people: list[Mapping[str, Any]] = []
                for ref in escort_refs:
                    try:
                        _efid, _epath, _eowner, _eordinal, escort_person = load_person_ref(ref)
                    except (FileNotFoundError, KeyError, TypeError, ValueError):
                        continue
                    health = escort_person.get("health", {}) if isinstance(escort_person.get("health"), Mapping) else {}
                    if health.get("status") != "dead":
                        escort_people.append(escort_person)
                rest_hours = escort_rest_hours(escort_people)
                if escort_refs:
                    lodging_base = max(1, int((economy_rules.get("consumables", {}) or {}).get("lodging_common_person_night", {}).get("base_value_cash", 40))) if isinstance(economy_rules, Mapping) else 40
                    nights = max(1, (max(1, rest_hours) + 23) // 24) if rest_hours > 0 else 0
                    lodging_cost = lodging_base * len(escort_refs) * nights
                    lodging_ref = _lodging_site(local_sites, destination_place)
                    rest_mode = "inn" if lodging_ref else "field_camp"
                    rest_location = str(lodging_ref or destination_place)
                    if not lodging_ref:
                        rest_hours = max(8, rest_hours * 2)
                        lodging_cost = 0
                    if beneficiary and rest_mode == "inn":
                        fpath, faction = load_faction(beneficiary)
                        cash = max(0, int(faction.get("treasury_cash", 0)))
                        destination_market = None; dmpath = ""
                        if destination_region:
                            try:
                                dmpath, destination_market = load_market(destination_region)
                            except (FileNotFoundError, ValueError):
                                destination_market = None; dmpath = ""
                            if isinstance(destination_market, Mapping) and destination_market.get("region_id") not in (None, destination_region):
                                destination_market = None; dmpath = ""
                        if lodging_cost > cash or (lodging_cost > 0 and not isinstance(destination_market, dict)):
                            rest_mode = "field_camp"
                            rest_location = destination_place
                            rest_hours = max(8, rest_hours * 2)
                            lodging_cost = 0
                        else:
                            faction["treasury_cash"] = cash - lodging_cost
                            writes[fpath] = faction; faction_cache[beneficiary] = (fpath, faction)
                            # Lodging is a real local service purchase. Money
                            # leaves the escort organization and enters the
                            # destination regional economy rather than vanishing.
                            if lodging_cost > 0 and isinstance(destination_market, dict):
                                destination_market["cash_pool"] = max(0, int(destination_market.get("cash_pool", 0))) + lodging_cost
                                writes[dmpath] = destination_market; market_cache[destination_region] = (dmpath, destination_market)
                    elif not beneficiary:
                        rest_mode = "field_camp"
                        rest_location = destination_place
                        rest_hours = max(8, rest_hours * 2)
                        lodging_cost = 0
                    move_exact_people_to_location(escort_refs, rest_location)
                    return_start = at + timedelta(hours=max(0, rest_hours))
                    _refund_movement_provisions(movement)
                    returning = _fresh_faction_return_journey(
                        movement_ref, movement, beneficiary=beneficiary, participants=escort_refs,
                        origin_place=destination_place, destination_place=str(movement.get("origin_place_ref") or ""),
                        movement_kind="escort_return", mode="convoy",
                        journey_start_at=return_start,
                        extra={"escort_refs": escort_refs, "contract_ref": cid},
                    )
                    if isinstance(returning, Mapping):
                        returning = copy.deepcopy(dict(returning))
                        resume_status = "returning"
                    else:
                        returning = _park_waiting_return(
                            movement_ref, movement, beneficiary=beneficiary, participants=escort_refs,
                            origin_place=destination_place, destination_place=str(movement.get("origin_place_ref") or ""),
                            movement_kind="escort_return", mode="convoy", from_current_position=False,
                            extra={"escort_refs": escort_refs, "contract_ref": cid},
                        )
                        resume_status = "awaiting_return_logistics"
                    returning["status"] = "lodging_rest" if rest_mode == "inn" else "field_rest"
                    returning["resume_status"] = resume_status
                    returning["rest_mode"] = rest_mode
                    returning["rest_place_ref"] = rest_location
                    returning["rest_hours_remaining"] = max(0, rest_hours)
                    returning["rest_started_at"] = at_iso
                    returning["rest_last_progress_at"] = at_iso
                    returning["rest_until"] = return_start.isoformat()
                    movements[movement_ref] = compact_route_movement_roles(returning)
                    outcome = {
                        "closed": True, "success": True, "paid_cash": paid,
                        "delivered_quantity": quantity, "escort_returning": resume_status == "returning",
                        "awaiting_return_logistics": resume_status == "awaiting_return_logistics",
                        "rest_mode": rest_mode, "rest_hours": rest_hours,
                        "lodging_cost_cash": lodging_cost,
                    }
                else:
                    commitments_state = settle_and_resume_people(participants, activity_ref=cid, commitments_state=commitments_state)
                    _refund_movement_provisions(movement)
                    movements.pop(movement_ref, None)
                    outcome = {"closed": True, "success": True, "paid_cash": paid, "delivered_quantity": quantity}
            else:
                failed = contract_transition(contract, at=at_iso, to_status="failed", actor_ref=attacker_fid or beneficiary or None)
                payment = settle_payment(failed, success=False)
                issuer = str(contract.get("issuer_ref") or "")
                if issuer.startswith("market:"):
                    source_region = issuer.split(":", 1)[1]
                    mpath, source_market = load_market(source_region)
                    source_market["cash_pool"] = max(0, int(source_market.get("cash_pool", 0))) + int(payment["refunded_cash"])
                    writes[mpath] = source_market
                    market_cache[source_region] = (mpath, source_market)
                elif issuer:
                    fpath, issuer_faction = load_faction(issuer)
                    issuer_faction["treasury_cash"] = max(0, int(issuer_faction.get("treasury_cash", 0))) + int(payment["refunded_cash"])
                    writes[fpath] = issuer_faction
                    faction_cache[issuer] = (fpath, issuer_faction)
                if beneficiary:
                    fpath, faction = load_faction(beneficiary)
                    writes[fpath] = faction
                    faction_cache[beneficiary] = (fpath, faction)
                if attacker_fid and item_ref and quantity > 0 and not defer_seized_cargo:
                    ipath, outlaw_inventory = load_inventory(attacker_fid)
                    _credit_cargo_to_inventory(outlaw_inventory, item_ref=item_ref, quantity=quantity)
                    writes[ipath] = outlaw_inventory
                    inventory_cache[attacker_fid] = (ipath, outlaw_inventory)
                elif not attacker_fid and item_ref and quantity > 0:
                    origin_region = _movement_region(str(movement.get("origin_place_ref") or ""))
                    _credit_failed_escort_cargo_to_origin_market(
                        origin_region=origin_region, item_ref=item_ref, quantity=quantity,
                        load_market=load_market, writes=writes, market_cache=market_cache,
                    )
                captured = [str(x) for x in captured_refs if isinstance(x, str) and x]
                reporters = [
                    ref for ref in [str(x) for x in movement.get("escort_refs", []) if isinstance(x, str)]
                    if ref not in set(captured)
                ]
                # The failed client contract closes now, but a kidnapping can
                # leave a second real obligation: surviving escorts must carry
                # the news home. No abstract timer informs the House for them.
                if captured and reporters:
                    report_home = str(movement.get("origin_place_ref") or "")
                    recipients: list[str] = []
                    if beneficiary:
                        recipients.append(beneficiary)
                    if issuer and not issuer.startswith("market:"):
                        try:
                            _ifpath, issuer_faction = load_faction(issuer)
                        except (KeyError, FileNotFoundError, ValueError):
                            issuer_faction = {}
                        if str(issuer_faction.get("headquarters") or "") == report_home:
                            recipients.append(issuer)
                    for captive_ref in captured:
                        candidate_fids: list[str] = []
                        try:
                            owner_fid, _ppath, _powner, _ordinal, _person = load_person_ref(captive_ref)
                        except (KeyError, ValueError, FileNotFoundError):
                            owner_fid = ""
                        if owner_fid:
                            candidate_fids.append(owner_fid)
                        family_fid = family_household_faction(family_state, captive_ref) or ""
                        if family_fid:
                            candidate_fids.append(family_fid)
                        for candidate_fid in candidate_fids:
                            if not candidate_fid or candidate_fid in recipients:
                                continue
                            try:
                                _cfpath, candidate_faction = load_faction(candidate_fid)
                            except (KeyError, FileNotFoundError, ValueError):
                                continue
                            if str(candidate_faction.get("headquarters") or "") == report_home:
                                recipients.append(candidate_fid)
                    commitments_state = settle_and_resume_people(
                        participants, activity_ref=cid, commitments_state=commitments_state,
                    )
                    _refund_movement_provisions(movement)
                    reporter_resources: list[tuple[str, str, str]] = []
                    for reporter_ref in reporters:
                        try:
                            reporter_owner, _rpath, _rowner, _ordinal, _person = load_person_ref(reporter_ref)
                        except (KeyError, ValueError, FileNotFoundError):
                            reporter_owner = beneficiary
                        reporter_resources.append(("person", reporter_ref, reporter_owner or beneficiary))
                    try:
                        commitments_state = reserve_resources(
                            commitments_state, resources=reporter_resources,
                            actor_ref=reporters[0], owner_ref=beneficiary or reporters[0],
                            activity_ref=movement_ref, activity_kind="escort_emergency_return",
                            started_at=at_iso, location_ref=str(movement.get("route_ref") or ""),
                        )
                    except ValueError:
                        # If the surviving party cannot remain a coherent finite
                        # owner, do not fabricate institutional knowledge.
                        outcome = {
                            "closed": True, "success": False,
                            "refunded_cash": int(payment["refunded_cash"]),
                            "cargo_lost": quantity, "kidnapping_report_started": False,
                        }
                    else:
                        emergency_extra = {
                            "escort_refs": reporters,
                            "report_captive_refs": captured,
                            "report_holder_faction_ref": str(attacker_fid or ""),
                            "report_recipient_faction_refs": list(dict.fromkeys(recipients)),
                        }
                        emergency = _fresh_faction_return_from_current_position(
                            movement_ref, movement, beneficiary=beneficiary, participants=reporters,
                            destination_place=report_home, movement_kind="escort_emergency_return",
                            mode="convoy", extra=emergency_extra,
                        )
                        if not isinstance(emergency, Mapping):
                            commitments_state = settle_and_resume_people(
                                reporters, activity_ref=movement_ref, commitments_state=commitments_state,
                            )
                            outcome = {
                                "closed": True, "success": False,
                                "refunded_cash": int(payment["refunded_cash"]),
                                "cargo_lost": quantity, "kidnapping_report_started": False,
                                "report_logistics_failure": "insufficient_provisions_or_route",
                            }
                        else:
                            if beneficiary:
                                pause_people_for_commitment(beneficiary, reporters)
                            movements[movement_ref] = emergency
                            outcome = {
                                "closed": True, "success": False,
                                "refunded_cash": int(payment["refunded_cash"]),
                                "cargo_lost": quantity, "kidnapping_report_started": True,
                                "reporter_count": len(reporters),
                            }
                else:
                    commitments_state = settle_and_resume_people(
                        participants, activity_ref=cid, commitments_state=commitments_state,
                    )
                    _refund_movement_provisions(movement)
                    outcome = {
                        "closed": True, "success": False,
                        "refunded_cash": int(payment["refunded_cash"]), "cargo_lost": quantity,
                    }
            active_after.pop(cid, None)
            contract_index = contract_after
            active_contracts = active_after
            if not (
                movement_ref in movements
                and movements[movement_ref].get("movement_kind") in {"escort_return", "escort_emergency_return"}
            ):
                movements.pop(movement_ref, None)
            for contact_ref, contact in list(contacts.items()):
                if isinstance(contact, Mapping) and contact.get("movement_ref") == cid:
                    contacts.pop(contact_ref, None)
            writes[_CONTRACT_INDEX_PATH] = contract_after; writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
            return outcome

        for event in route_events:
            rid = event.get("owner_ref")
            route = route_index.get(str(rid)) if isinstance(rid, str) else None
            if route is None:
                continue
            outlaw_rows = list(outlaws_for_route(str(rid)))
            fighters = 0
            blocked_now = unavailable_person_refs()
            for outlaw_row in outlaw_rows:
                outlaw_fid = str(outlaw_row.get("faction_id") or "") if isinstance(outlaw_row, Mapping) else ""
                if not outlaw_fid:
                    continue
                _orp, outlaw_roster = load_roster(outlaw_fid)
                outlaw_people = outlaw_roster.get("people", []) if isinstance(outlaw_roster, Mapping) else []
                if isinstance(outlaw_people, list):
                    fighters += combat_ready_count(
                        [p for p in outlaw_people if isinstance(p, Mapping)],
                        year=at.year, unavailable_refs=blocked_now, minimum_age=14, minimum_combat_skill=20,
                    )
            source_place = str(route.get("from") or "")
            source_region = place_region.get(source_place)
            road_quality = str(route.get("road_quality") or "")
            traffic_milli = route_traffic_milli(road_quality)
            # A route cycle represents the whole preceding/coming day, not the
            # scheduler wake clock. Derive one stable encounter window inside
            # that day so a 21:15 scheduler anchor does not make every caravan
            # encounter happen at night forever.
            encounter_hour = min(23, stable_permille(f"{world_seed}|route-window|{rid}|{at.date().isoformat()}") * 24 // 1000)
            exposure_at = at.replace(hour=encounter_hour, minute=0, second=0, microsecond=0)
            try:
                weather = weather_snapshot(world_seed=world_seed, at=exposure_at, place_id=source_place)
                visibility_milli = max(0, min(1000, int(weather.get("visibility_milli",1000))))
            except (KeyError, ValueError):
                visibility_milli = 1000
            night = encounter_hour < 6 or encounter_hour >= 19
            capacities = government_state.get("regional_capacity", {}) if isinstance(government_state, Mapping) else {}
            gcap = capacities.get(source_region, {}) if isinstance(capacities, Mapping) and isinstance(source_region,str) else {}
            patrol_presence = (int(gcap.get("militia",0))//40 + int(gcap.get("standard",0))//20 + int(gcap.get("elite",0))//4) if isinstance(gcap,Mapping) else 0
            exposure = route_exposure(
                traffic_milli=traffic_milli, patrol_presence=patrol_presence, outlaw_fighters=fighters,
                weather_visibility_milli=visibility_milli, night=night,
            )
            route_review: dict[str, Any] = {
                "kind": "route_activity_cycle", "event_id": event.get("event_id"),
                "route_id": rid, "outlaw_fighters": fighters, "traffic_milli": traffic_milli,
                "patrol_presence": patrol_presence, "weather_visibility_milli": visibility_milli, "night": night, **exposure,
                "movements_advanced": 0, "hostile_contacts": 0, "completed_movements": 0, "closed_outcomes": {},
            }
            route_movements = [
                (mid, movement) for mid, movement in sorted(movements.items())
                if isinstance(movement, Mapping) and movement.get("route_ref") == rid
            ]
            for movement_ref, raw_movement in route_movements:
                if movement_ref not in movements:
                    continue
                movement = copy.deepcopy(dict(raw_movement))
                status = str(movement.get("status", "active"))
                participants = [str(x) for x in movement.get("participant_refs", []) if isinstance(x, str)]
                beneficiary = str(movement.get("beneficiary_ref") or "")

                if status == "awaiting_return_logistics":
                    # An explicit escort list can be empty while a living but
                    # temporarily incapacitated non-carried traveler still has the
                    # potential to control the party after recovery. Only a party
                    # with no remaining potential controller is truly extinguished.
                    # `_retry_waiting_return` performs the stricter field-readiness
                    # check and simply leaves the finite owner parked when nobody
                    # can yet travel.
                    potential_controllers = _nondead_person_refs(route_potential_controller_refs(movement))
                    if not potential_controllers:
                        movement["status"] = "party_extinguished"
                        movements[movement_ref] = compact_route_movement_roles(movement)
                        writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                        continue
                    returning = _retry_waiting_return(movement_ref, movement)
                    if isinstance(returning, Mapping):
                        movements[movement_ref] = returning
                        writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                    # Otherwise the exact party remains physically reserved at
                    # its current location and the next daily route service can
                    # retry after recovery/logistics change.
                    continue

                if status == "party_extinguished":
                    movement_kind = str(movement.get("movement_kind") or "")
                    if movement_kind == "merchant_trade":
                        item_ref = str(movement.get("item_ref") or "")
                        quantity = max(0, int(movement.get("quantity", 0)))
                        place_ref = _extinguished_route_place(movement)
                        region = _movement_region(place_ref)
                        if quantity > 0 and not item_ref:
                            raise ValueError("extinguished merchant cargo item unresolved")
                        if not place_ref or not region:
                            raise ValueError("extinguished merchant salvage region unresolved")
                        reservation = movement.get("provision_reservation")
                        unused_rations = 0
                        if isinstance(reservation, Mapping):
                            reserved = max(0, int(reservation.get("ration_days_reserved", 0)))
                            consumed = max(0, min(reserved, int(reservation.get("ration_days_consumed", 0))))
                            unused_rations = max(0, reserved - consumed)
                        if quantity > 0 or unused_rations > 0:
                            mpath, market = load_market(region)
                            stock = market.setdefault("stock", {})
                            if not isinstance(stock, dict):
                                raise ValueError("extinguished merchant salvage market stock invalid")
                            if quantity > 0:
                                stock[item_ref] = max(0, int(stock.get(item_ref, 0))) + quantity
                            if unused_rations > 0:
                                stock["food_ration_day"] = max(0, int(stock.get("food_ration_day", 0))) + unused_rations
                            writes[mpath] = market
                            market_cache[region] = (mpath, market)
                        commitments_state = release_resources(commitments_state, activity_ref=movement_ref)
                        movements.pop(movement_ref, None)
                        writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                        route_review["completed_movements"] += 1
                        route_review["closed_outcomes"][movement_ref] = {
                            "closed": True, "success": False, "party_extinguished": True,
                            "cargo_salvaged_locally": quantity,
                            "salvaged_ration_days": unused_rations, "salvage_region": region,
                        }
                        continue
                    if movement_kind == "faction_operation_travel":
                        outcome = _close_escort(movement_ref, movement, success=False)
                        if not outcome.get("closed"):
                            op_ref = str(movement.get("purpose_ref") or movement.get("operation_ref") or "")
                            deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
                            op = deployments.get(op_ref) if isinstance(deployments, Mapping) else None
                            stranded_rescued_refs: list[str] = []
                            stranded_place = ""
                            if isinstance(op, Mapping) and str(op.get("operation_kind") or "") == "custody_rescue":
                                stranded_rescued_refs = [
                                    str(ref) for ref in movement.get("rescued_refs", [])
                                    if isinstance(ref, str) and ref
                                ]
                                if not stranded_rescued_refs:
                                    captive_ref = str(op.get("captive_ref") or "")
                                    if captive_ref and captive_ref in {
                                        str(ref) for ref in movement.get("participant_refs", []) if isinstance(ref, str)
                                    }:
                                        stranded_rescued_refs = [captive_ref]
                                if stranded_rescued_refs:
                                    stranded_place = _extinguished_route_place(movement)
                                    stranded_location = _arrival_site(local_sites, stranded_place) or stranded_place
                                    if not stranded_location:
                                        raise ValueError("extinguished rescue return location unresolved")
                                    move_exact_people_to_location(stranded_rescued_refs, stranded_location)

                            if isinstance(op, Mapping):
                                fid = str(op.get("faction_ref") or "")
                                reserve_cash = sum(max(0, int(op.get(key, 0))) for key in (
                                    "entry_fee_reserved_cash", "host_spend_reserved_cash", "delegate_ticket_reserved_cash",
                                ))
                                if reserve_cash and fid:
                                    fpath, faction = load_faction(fid)
                                    faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + reserve_cash
                                    writes[fpath] = faction
                                    faction_cache[fid] = (fpath, faction)
                                issue_refs = list(op.get("issued_equipment", {}).keys()) if isinstance(op.get("issued_equipment"), Mapping) else []
                                _detach_operation_issue_refs(op_ref, issue_refs, status="operation_issue_stranded")
                                deployments.pop(op_ref, None)
                                writes[_DEPLOYMENTS_PATH] = deployments_state
                            _refund_movement_provisions(movement)
                            # Purpose and physical movement can both appear in the
                            # derived same-frontier occupancy view. Close both old
                            # authorities before transferring a carried survivor to
                            # a new finite repatriation owner.
                            if op_ref:
                                commitments_state = release_resources(commitments_state, activity_ref=op_ref)
                            commitments_state = release_resources(commitments_state, activity_ref=movement_ref)
                            movements.pop(movement_ref, None)
                            writes[_ROUTE_OPERATIONS_PATH] = route_ops_state

                            repatriation_refs: list[str] = []
                            if isinstance(op, Mapping) and stranded_rescued_refs:
                                followup = op.get("repatriate_after_return") if isinstance(op.get("repatriate_after_return"), Mapping) else {}
                                for rescued_ref in stranded_rescued_refs:
                                    if player_ref and rescued_ref == player_ref:
                                        notice = {
                                            "kind": "player_rescue_party_lost",
                                            "person_ref": rescued_ref,
                                            "location_ref": _arrival_site(local_sites, stranded_place) or stranded_place,
                                            "rescuer_faction_ref": str(op.get("faction_ref") or ""),
                                            "requires_player_decision": True,
                                            "delivered_to_player": True,
                                        }
                                        handoffs.append({**notice, "handoff": classify_handoff(notice)})
                                        continue
                                    owner_fid = ""
                                    if str(followup.get("person_ref") or "") == rescued_ref:
                                        owner_fid = str(followup.get("owner_faction_ref") or "")
                                    if not owner_fid:
                                        try:
                                            owner_fid, _ppath, _powner, _ordinal, _person = load_person_ref(rescued_ref)
                                        except (FileNotFoundError, KeyError, TypeError, ValueError):
                                            owner_fid = ""
                                    if not owner_fid:
                                        continue
                                    try:
                                        _owner_path, owner_faction = load_faction(owner_fid)
                                    except (FileNotFoundError, KeyError, TypeError, ValueError):
                                        continue
                                    owner_home = str(owner_faction.get("headquarters") or "") if isinstance(owner_faction, Mapping) else ""
                                    if not owner_home or not stranded_place:
                                        continue
                                    if owner_home == stranded_place:
                                        home_site = str(owner_faction.get("local_site_ref") or "") if isinstance(owner_faction, Mapping) else ""
                                        if home_site:
                                            move_exact_people_to_location([rescued_ref], home_site)
                                        continue
                                    try:
                                        rep_ref, rep_op, rep_event = build_repatriation_operation(
                                            person_ref=rescued_ref, owner_faction_ref=owner_fid,
                                            origin_place_ref=stranded_place, home_place_ref=owner_home,
                                            at=at, cause_ref=str(op.get("custody_id") or op_ref),
                                            counterparty_faction_ref=str(op.get("faction_ref") or ""),
                                        )
                                        commitments_state = reserve_resources(
                                            commitments_state,
                                            resources=[("person", rescued_ref, owner_fid)],
                                            actor_ref=rescued_ref, owner_ref=owner_fid,
                                            activity_ref=rep_ref, activity_kind="captive_repatriation",
                                            started_at=at_iso, location_ref=stranded_place,
                                        )
                                    except (FileNotFoundError, KeyError, TypeError, ValueError):
                                        continue
                                    if rep_ref not in deployments:
                                        deployments[rep_ref] = rep_op
                                        pending_one_off_events.append(rep_event)
                                        writes[_DEPLOYMENTS_PATH] = deployments_state
                                        repatriation_refs.append(rep_ref)

                            outcome = {
                                "closed": True, "success": False, "party_extinguished": True,
                                "stranded_rescued_refs": stranded_rescued_refs,
                                "repatriation_operation_refs": repatriation_refs,
                            }
                        route_review["completed_movements"] += 1
                        route_review["closed_outcomes"][movement_ref] = outcome
                        continue
                    if movement_kind == "raid_return":
                        outcome = _salvage_extinguished_raid_return(movement_ref, movement)
                        route_review["completed_movements"] += 1
                        route_review["closed_outcomes"][movement_ref] = outcome
                        continue
                    if movement_kind in {"escort_contract", "escort_return", "escort_emergency_return"} or movement.get("contract_ref"):
                        # When every actual escort/controller is gone, surviving
                        # protected clients are still real people on the road. The
                        # failed contract may close, but the people cannot vanish or
                        # retain a stale pre-trip location. Project them to the
                        # nearest real endpoint before removing the finite movement.
                        stranded_carried_refs = list(dict.fromkeys(
                            str(ref)
                            for key in ("protected_person_refs", "rescued_refs")
                            for ref in (movement.get(key, []) if isinstance(movement.get(key), list) else [])
                            if isinstance(ref, str) and ref
                            and ref in {str(x) for x in movement.get("participant_refs", []) if isinstance(x, str)}
                        ))
                        stranded_place = ""
                        if stranded_carried_refs:
                            stranded_place = _extinguished_route_place(movement)
                            stranded_location = _arrival_site(local_sites, stranded_place) or stranded_place
                            if not stranded_location:
                                raise ValueError("extinguished escort survivor location unresolved")
                            move_exact_people_to_location(stranded_carried_refs, stranded_location)
                            if player_ref and player_ref in stranded_carried_refs:
                                notice = {
                                    "kind": "player_escort_party_lost",
                                    "person_ref": player_ref, "location_ref": stranded_location,
                                    "movement_ref": movement_ref, "requires_player_decision": True,
                                    "delivered_to_player": True,
                                }
                                handoffs.append({**notice, "handoff": classify_handoff(notice)})
                        outcome = _close_escort(movement_ref, movement, success=False)
                        if not outcome.get("closed"):
                            if outcome.get("reason") == "contract_missing":
                                raise ValueError("extinguished contract movement owner missing")
                            _refund_movement_provisions(movement)
                            commitments_state = release_resources(
                                commitments_state, activity_ref=str(movement.get("contract_ref") or movement_ref),
                            )
                            movements.pop(movement_ref, None)
                            writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                            outcome = {"closed": True, "success": False, "party_extinguished": True}
                        if stranded_carried_refs:
                            outcome = copy.deepcopy(dict(outcome))
                            outcome["stranded_carried_refs"] = stranded_carried_refs
                            outcome["stranded_place_ref"] = stranded_place
                        route_review["completed_movements"] += 1
                        route_review["closed_outcomes"][movement_ref] = outcome
                        continue
                    _refund_movement_provisions(movement)
                    commitments_state = release_resources(commitments_state, activity_ref=movement_ref)
                    movements.pop(movement_ref, None)
                    writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                    route_review["completed_movements"] += 1
                    route_review["closed_outcomes"][movement_ref] = {
                        "closed": True, "success": False, "party_extinguished": True,
                    }
                    continue

                if movement.get("movement_kind") == "route_pursuit":
                    # A pursuit is already a real physical party and therefore
                    # reserves its exact people by ownership. It does not roll a
                    # second autonomous travel encounter against itself. It
                    # shadows the target's current leg until the target's own
                    # route frontier creates the exact contact.
                    target_ref = str(movement.get("target_movement_ref") or "")
                    target = movements.get(target_ref)
                    if not isinstance(target, Mapping):
                        move_exact_people(participants, str(movement.get("destination_place_ref") or movement.get("origin_place_ref") or ""))
                        movements.pop(movement_ref, None); writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                        continue
                    target_route = str(target.get("route_ref") or "")
                    if target_route and target_route != str(movement.get("route_ref") or ""):
                        movement["route_ref"] = target_route
                        movement["origin_place_ref"] = str(target.get("segment_origin_place_ref") or target.get("origin_place_ref") or movement.get("origin_place_ref") or "")
                        movement["destination_place_ref"] = str(target.get("segment_destination_place_ref") or target.get("destination_place_ref") or movement.get("destination_place_ref") or "")
                        movements[movement_ref] = movement; writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                    continue

                if status in {"lodging_rest", "field_rest"}:
                    remaining = max(0, int(movement.get("rest_hours_remaining", 0)))
                    rest_until_raw = movement.get("rest_until")
                    rest_last_raw = movement.get("rest_last_progress_at")
                    if not isinstance(rest_until_raw, str) or not rest_until_raw or not isinstance(rest_last_raw, str) or not rest_last_raw:
                        raise ValueError("route rest clock missing")
                    try:
                        rest_until = datetime.fromisoformat(rest_until_raw)
                        rest_last = datetime.fromisoformat(rest_last_raw)
                    except ValueError as exc:
                        raise ValueError("route rest clock invalid") from exc
                    if at >= rest_until:
                        rest_step = remaining
                    else:
                        elapsed_rest_hours = max(0, int((at - rest_last).total_seconds() // 3600))
                        rest_step = min(remaining, elapsed_rest_hours)
                    if rest_step > 0:
                        effective_rest = rest_step if status == "lodging_rest" else max(1, rest_step // 2)
                        for ref in participants:
                            try:
                                _rfid, _rpath, _rowner, _rordinal, person = load_person_ref(ref)
                            except (FileNotFoundError, KeyError, TypeError, ValueError):
                                continue
                            save_exact_person(ref, apply_lodging_rest(person, elapsed_hours=effective_rest))
                        remaining = max(0, remaining - rest_step)
                        movement["rest_last_progress_at"] = at_iso
                    movement["rest_hours_remaining"] = remaining
                    if remaining <= 0:
                        for key in (
                            "rest_hours_remaining", "rest_place_ref", "rest_mode", "rest_started_at",
                            "rest_last_progress_at", "rest_until",
                        ):
                            movement.pop(key, None)
                        movement["status"] = str(movement.pop("resume_status", "returning") or "returning")
                        if movement["status"] in {"active", "returning"} and movement.get("route_ref"):
                            movement = refresh_current_segment(movement, at=at, world_seed=world_seed)
                            pending_one_off_events.append(exact_segment_due_event(movement_ref, movement, at=at))
                        else:
                            movement["last_progress_at"] = at_iso
                        if player_ref and player_ref in participants and movement["status"] in {"active", "returning"}:
                            try: scene=copy.deepcopy(dict(read_json(_SCENE_PATH)))
                            except FileNotFoundError: scene={}
                            scene["location_id"]=str(movement.get("route_ref") or scene.get("location_id") or "")
                            scene["present_person_ids"]=participants; scene["visible_person_ids"]=participants
                            writes[_SCENE_PATH]=scene
                    movements[movement_ref] = movement
                    writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                    if rest_step > 0:
                        route_review["movements_advanced"] += 1
                    continue

                # A player contact waits on the authoritative exact-combat owner.
                if status == "contact_pending":
                    combat_ref = str(movement.get("combat_ref") or "")
                    combat = combats.get(combat_ref)
                    if isinstance(combat, Mapping) and combat.get("status") == "resolved":
                        winner = combat.get("winner_side")
                        protected_failed = False
                        for protected_ref in [str(x) for x in movement.get("protected_person_refs", []) if isinstance(x, str)]:
                            try:
                                _fid, _path, _owner, _ordinal, protected_person = load_person_ref(protected_ref)
                            except (KeyError, ValueError, FileNotFoundError):
                                protected_failed = True
                                break
                            health = protected_person.get("health", {}) if isinstance(protected_person.get("health"), Mapping) else {}
                            if health.get("status") == "dead":
                                protected_failed = True
                                break
                        attacker_fid = str(
                            movement.get("contact_attacker_faction_ref")
                            or movement.get("contact_outlaw_faction_ref")
                            or ""
                        ) or None
                        escort_success = winner == "side_a" and not protected_failed
                        attacker_control = winner == "side_b"
                        seizure = {"started": False}
                        contact_attacker_refs = [
                            str(x) for x in movement.get("contact_attacker_refs", []) if isinstance(x, str)
                        ]
                        if attacker_control and attacker_fid:
                            seizure = _start_seizure_return(
                                movement_ref, movement, attacker_fid=attacker_fid,
                                attacker_refs=contact_attacker_refs,
                                intent=str(movement.get("contact_intent") or "hostile_interception"),
                                allow_empty_return=True,
                            )
                        elif attacker_fid and contact_attacker_refs:
                            # Player-resolved defenders do not make the attacking
                            # party teleport home either. Move their reservation
                            # from the finished combat into a real retreat owner.
                            commitments_state = release_resources(
                                commitments_state, activity_ref=str(movement.get("contact_ref") or "")
                            )
                            _start_attacker_retreat_return(
                                movement_ref, movement, attacker_fid=attacker_fid,
                                attacker_refs=contact_attacker_refs,
                            )
                        _apply_shared_danger_social(participants)
                        # Ordinary player travel is a journey, not an escort
                        # objective. Defending the road successfully resumes the
                        # same physical leg; it never teleports the party to the
                        # ultimate destination. A lost fight closes/stops the
                        # journey through the generic physical-travel branch.
                        if movement.get("movement_kind") == "player_strategic_travel" and escort_success:
                            contact_ref = str(movement.get("contact_ref") or "")
                            if contact_ref:
                                commitments_state = release_resources(commitments_state, activity_ref=contact_ref)
                                contacts.pop(contact_ref, None)
                            for key in (
                                "contact_ref", "combat_ref", "contact_attacker_faction_ref",
                                "contact_outlaw_faction_ref", "contact_attacker_refs", "contact_intent",
                            ):
                                movement.pop(key, None)
                            movement["status"] = "active"
                            movement["last_progress_at"] = at_iso
                            movements[movement_ref] = movement
                            pending_one_off_events.append(exact_segment_due_event(movement_ref, movement, at=at))
                            writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                            outcome = {"closed": False, "success": True, "journey_resumed": True}
                        else:
                            outcome = _close_escort(
                                movement_ref, movement, success=escort_success,
                                attacker_fid=attacker_fid if attacker_control else None,
                                defer_seized_cargo=bool(seizure.get("started")),
                                captured_refs=seizure.get("captured_refs", []),
                            )
                        combats.pop(combat_ref, None)
                        writes[_COMBATS_PATH] = combats_state
                        route_review["completed_movements"] += int(bool(outcome.get("closed")))
                        if outcome.get("closed"):
                            route_review["closed_outcomes"][movement_ref] = bool(outcome.get("success"))
                    continue

                if status not in {"active", "returning"}:
                    continue
                # Physical movement advances by the exact elapsed interval before
                # this route boundary is evaluated.
                movement, progressed_seconds = advance_movement_progress(movement, at=at)
                movement, newly_consumed_rations = apply_route_provision_progress(
                    movement, progressed_seconds=progressed_seconds,
                )
                if newly_consumed_rations > 0:
                    provision = movement.get("provision_reservation")
                    if isinstance(provision, Mapping) and provision.get("source_kind") == "faction":
                        provision_fid = str(provision.get("source_ref") or "")
                        if provision_fid:
                            try:
                                provision_ipath, provision_inventory = load_inventory(provision_fid)
                            except (FileNotFoundError, KeyError, TypeError, ValueError):
                                provision_inventory = None; provision_ipath = ""
                            if isinstance(provision_inventory, Mapping):
                                provision_inventory = add_faction_upkeep_credit(
                                    provision_inventory, newly_consumed_rations,
                                )
                                writes[provision_ipath] = provision_inventory
                                inventory_cache[provision_fid] = (provision_ipath, provision_inventory)
                movements[movement_ref] = movement
                if progressed_seconds > 0:
                    writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                people: dict[str, Mapping[str, Any]] = {}
                for ref in participants:
                    try:
                        _pfid, _ppath, _powner, _pordinal, person = load_person_ref(ref)
                    except (KeyError, ValueError, FileNotFoundError) as exc:
                        raise ValueError(f"route movement participant unresolved: {ref}") from exc
                    people[ref] = person
                if movement.get("movement_kind") == "raid_return":
                    # Captives travel physically with the party but do not become
                    # combatants for their captors.
                    escort_refs = route_controlling_refs(movement)
                    if not movement.get("protected_person_refs"):
                        movement["protected_person_refs"] = [
                            str(x) for x in movement.get("captive_refs", []) if isinstance(x, str)
                        ]
                else:
                    escort_refs = [str(x) for x in movement.get("escort_refs", participants) if isinstance(x, str)]
                missing_escorts = [ref for ref in escort_refs if ref not in people]
                if missing_escorts:
                    raise ValueError(f"route escort participant unresolved: {missing_escorts[0]}")
                escorts = [people[ref] for ref in escort_refs]
                target_faction_refs: list[str] = []
                if beneficiary:
                    target_faction_refs.append(beneficiary)
                actual_ransom_value = 0
                protected_refs_ordered = [
                    str(x) for x in movement.get("protected_person_refs", []) if isinstance(x, str)
                ]
                for protected_ref in protected_refs_ordered:
                    try:
                        protected_owner, _ppath, _powner, _pordinal, protected_person = load_person_ref(protected_ref)
                    except (KeyError, ValueError, FileNotFoundError):
                        continue
                    actual_ransom_value = max(actual_ransom_value, principal_ransom_value_cash(protected_person))
                    # On an ordinary escort, the protected person's own faction
                    # is part of the convoy side and cannot attack itself. On a
                    # raid return, that owner is a plausible rescuer and should
                    # remain eligible if it has local presence and information.
                    if movement.get("movement_kind") != "raid_return":
                        if protected_owner and protected_owner not in target_faction_refs:
                            target_faction_refs.append(protected_owner)
                cargo_value = _movement_cargo_value_cash(movement)
                chosen: tuple[str, list[dict[str, Any]], dict[str, Any]] | None = None
                threat_milli = max(0, int(exposure.get("threat_milli", 0)))
                witness_milli = max(0, int(exposure.get("witness_milli", 0)))

                chosen_pursuit_ref = ""
                # A consequential observation/disclosure may already have
                # created a real pursuing party. The plan remains hidden, but
                # once its mobilization time has elapsed and it shares this
                # physical road with the target, those exact people get first
                # opportunity to make contact. No second motive roll is needed.
                for pursuit_ref, pursuit in sorted(movements.items(), key=lambda item: str(item[0])):
                    if not isinstance(pursuit, Mapping) or pursuit.get("movement_kind") != "route_pursuit":
                        continue
                    if str(pursuit.get("target_movement_ref") or "") != movement_ref or str(pursuit.get("route_ref") or "") != str(rid):
                        continue
                    try: ready_at = datetime.fromisoformat(str(pursuit.get("ready_at") or at_iso))
                    except ValueError: ready_at = at
                    if ready_at > at:
                        continue
                    attacker_fid = str(pursuit.get("beneficiary_ref") or "")
                    attacker_refs = [str(x) for x in pursuit.get("participant_refs", []) if isinstance(x, str)]
                    attackers=[]
                    for ref in attacker_refs:
                        try: _afid,_apath,_aowner,_aordinal,person=load_person_ref(ref)
                        except (FileNotFoundError,KeyError,TypeError,ValueError): continue
                        health=person.get("health",{}) if isinstance(person.get("health"),Mapping) else {}
                        if health.get("status") != "dead" and int(health.get("consciousness",100)) > 0: attackers.append(person)
                    if not attacker_fid or not attackers:
                        continue
                    chosen=(attacker_fid,attackers,{"attack":True,"intent":str(pursuit.get("contact_intent") or "hostile_interception"),"pursuit":True})
                    chosen_pursuit_ref=str(pursuit_ref)
                    break

                # Route-local criminal organizations and genuinely hostile local
                # institutions share one causal interception path. Nobody gets
                # global knowledge: ordinary factions need local presence and a
                # serious existing grievance; every faction sizes up the visible
                # escorts through one actual observer before deciding to act.
                for candidate in route_interception_candidates(str(rid), route):
                    if chosen is not None:
                        break
                    attacker_fid = str(candidate.get("faction_id") or "")
                    if not attacker_fid or attacker_fid == beneficiary or attacker_fid in target_faction_refs:
                        continue
                    attacker_type = current_faction_type(attacker_fid)
                    relation_options = [
                        directed_relation(attacker_fid, target_fid)
                        for target_fid in target_faction_refs
                        if target_fid and target_fid != attacker_fid
                    ]
                    relation_options = [edge for edge in relation_options if isinstance(edge, Mapping)]
                    relation = max(
                        relation_options,
                        key=lambda edge: (max(0, int(edge.get("hostility", 0))), -int(edge.get("trust", 0))),
                        default=None,
                    )
                    hostility = max(0, int(relation.get("hostility", 0))) if isinstance(relation, Mapping) else 0
                    if attacker_type != "outlaw_faction" and hostility < 55:
                        continue

                    enterprises = candidate.get("enterprises", {}) if isinstance(candidate.get("enterprises"), Mapping) else {}
                    criminal_level = max(0, int(enterprises.get("criminal_enterprise", 0)))
                    criminal_scale = enterprise_scale_value(candidate, "criminal_enterprise") if criminal_level > 0 else 0
                    daily_attack_capacity = (
                        max(1, min(6, (criminal_scale + 2) // 3))
                        if attacker_type == "outlaw_faction" and criminal_scale > 0 else 1
                    )
                    if route_attack_counts.get(attacker_fid, 0) >= daily_attack_capacity:
                        continue

                    # Screening must be cheaper than materializing an entire
                    # faction's pending training. The exact opportunity is monotone
                    # in observer confidence and never exceeds its confidence=1000
                    # value. Therefore a deterministic contact roll that fails this
                    # upper bound cannot possibly pass after training. This is an
                    # exact short-circuit, not an approximation.
                    contact_roll = stable_permille(
                        f"{world_seed}|route-contact|{movement_ref}|{at.date().isoformat()}|{attacker_fid}"
                    )
                    maximum_opportunity = route_interception_opportunity_permille(
                        attacker_faction_type=attacker_type,
                        route_threat_milli=threat_milli,
                        witness_milli=witness_milli,
                        hostility=hostility,
                        observer_confidence_milli=1000,
                    )
                    if maximum_opportunity <= 0 or contact_roll >= maximum_opportunity:
                        continue

                    afpath, attacker_current = load_faction(attacker_fid)
                    arpath, attacker_roster = load_roster(attacker_fid)
                    # A candidate that can still lawfully make contact now reaches
                    # the real causal boundary. Materialize its training before
                    # exact observation and combat so capability remains current.
                    attacker_current, attacker_roster, _training_boundary = settle_and_reset_faction_training_cycle(
                        attacker_current, attacker_roster, at_iso=at_iso,
                    )
                    writes[afpath] = attacker_current; faction_cache[attacker_fid] = (afpath, attacker_current)
                    writes[arpath] = attacker_roster; roster_cache[attacker_fid] = (arpath, attacker_roster)
                    candidate = attacker_current
                    available = usable_martial_people(attacker_roster, exclude_committed=unavailable_person_refs())
                    if not available:
                        continue
                    observer = best_route_observer(available)
                    if not isinstance(observer, Mapping):
                        continue
                    observed = observed_escort_strength(
                        observer=observer, escorts=escorts, world_seed=world_seed,
                        observation_ref=f"{movement_ref}|{at.date().isoformat()}|{attacker_fid}",
                    )
                    opportunity = route_interception_opportunity_permille(
                        attacker_faction_type=attacker_type,
                        route_threat_milli=threat_milli,
                        witness_milli=witness_milli,
                        hostility=hostility,
                        observer_confidence_milli=int(observed.get("confidence_milli", 0)),
                    )
                    if opportunity <= 0 or contact_roll >= opportunity:
                        continue

                    # Recognition of a valuable principal is itself imperfect.
                    # Unknown travelers do not magically expose their social value.
                    known_ransom_value = 0
                    if actual_ransom_value > 0:
                        recognition = min(950, max(100, int(observed.get("confidence_milli", 0))))
                        if stable_permille(
                            f"{world_seed}|principal-recognition|{movement_ref}|{at.date().isoformat()}|{attacker_fid}"
                        ) < recognition:
                            known_ransom_value = actual_ransom_value

                    autonomy = candidate.get("autonomy_policy", {}) if isinstance(candidate.get("autonomy_policy"), Mapping) else {}
                    policy = candidate.get("outlaw_policy", {}) if isinstance(candidate.get("outlaw_policy"), Mapping) else {}
                    desired_force = max(2, int(observed.get("visible_escort_count", len(escort_refs))) * 2 + 1)
                    if known_ransom_value >= 50_000:
                        desired_force += 2
                    available.sort(key=lambda p: (-person_combat_index(p), str(p.get("person_id", ""))))
                    attack_count = interception_force_size(
                        available_count=len(available), observed_escort_count=max(1,int(observed.get("visible_escort_count",len(escort_refs)))),
                        hostility=hostility, criminal_scale=criminal_scale, risk_tolerance=max(0,int(autonomy.get("risk_tolerance",50))),
                        known_value_cash=max(cargo_value,known_ransom_value), attacker_faction_type=attacker_type,
                    )
                    attackers = available[:attack_count]
                    if not attackers:
                        continue
                    own_index = max(1, sum(person_combat_index(p) for p in attackers) // len(attackers))
                    government_risk = min(1000, max(0, int(patrol_presence)) * 45 + witness_milli // 2)
                    decision = interception_decision(
                        attacker_faction_type=attacker_type,
                        relation=relation,
                        own_available_martial=len(attackers), own_combat_index=own_index,
                        observed_escort_count=max(1, int(observed.get("visible_escort_count", len(escort_refs)))),
                        observed_escort_combat_index=max(1, int(observed.get("estimated_combat_index", 1))),
                        cargo_value_cash=cargo_value,
                        ransom_value_cash=known_ransom_value,
                        risk_tolerance=max(0, int(autonomy.get("risk_tolerance", 50))),
                        government_risk_milli=government_risk,
                        minimum_attack_advantage_milli=max(650, int(policy.get("minimum_attack_advantage_milli", 1100))),
                        civilian_restraint=max(0,int((candidate.get("doctrine",{}) or {}).get("civilian_restraint",0))) if isinstance(candidate.get("doctrine",{}),Mapping) else 0,
                    )
                    if decision.get("attack"):
                        decision = dict(decision)
                        decision["observer_ref"] = str(observer.get("person_id") or "")
                        decision["observed_escort_count"] = int(observed.get("visible_escort_count", 0))
                        decision["observed_escort_combat_index"] = int(observed.get("estimated_combat_index", 0))
                        decision["known_ransom_value_cash"] = known_ransom_value
                        chosen = (attacker_fid, attackers, decision)
                        break

                if chosen is not None:
                    attacker_fid, attackers, decision = chosen
                    if chosen_pursuit_ref:
                        movements.pop(chosen_pursuit_ref, None)
                        commitments_state = release_resources(commitments_state, activity_ref=chosen_pursuit_ref)
                        writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                    route_attack_counts[attacker_fid] = route_attack_counts.get(attacker_fid, 0) + 1
                    attacker_refs = [str(p["person_id"]) for p in attackers]
                    people.update({str(p["person_id"]): p for p in attackers})
                    contact_ref = f"contact:{movement_ref}:{at.date().isoformat()}:{attacker_fid}"
                    if beneficiary:
                        apply_directed_relation_event(beneficiary, attacker_fid, "armed_raid")
                        apply_directed_relation_event(attacker_fid, beneficiary, "armed_raid")
                    route_review["hostile_contacts"] += 1
                    if isinstance(source_region, str) and int(exposure.get("witness_milli",0)) >= 350:
                        public_offense = _public_offense_for_route_intent(str(decision.get("intent") or ""))
                        attention_rows = government_state.setdefault("attention", {})
                        warrants = government_state.setdefault("warrants", {})
                        if not isinstance(attention_rows, dict) or not isinstance(warrants, dict):
                            raise ValueError("jianghu government state invalid")
                        confidence = max(35, min(100, int(exposure.get("witness_milli",0))//10))
                        for attacker_ref in attacker_refs:
                            prior = attention_rows.get(attacker_ref, {}) if isinstance(attention_rows.get(attacker_ref), Mapping) else {}
                            prior_offenses = max(0, int(prior.get("prior_offenses",0)))
                            added = attention_from_evidence([{
                                "kind":public_offense, "publicly_delivered":True, "confidence":confidence,
                            }], prior_offenses=prior_offenses)
                            total = min(300, max(0,int(prior.get("attention",0))) + added)
                            attention_rows[attacker_ref] = compact_attention_row(
                                attention=total, bounty_cash=max(0,int(prior.get("bounty_cash",0))),
                                prior_offenses=prior_offenses + 1,
                            )
                            if total >= 40:
                                warrant_ref=f"warrant:{attacker_ref}"
                                existing=warrants.get(warrant_ref,{}) if isinstance(warrants.get(warrant_ref),Mapping) else {}
                                existing_escrow=max(0,int(existing.get("bounty_escrow_cash",0))) if isinstance(existing,Mapping) else 0
                                desired=max(500,total*25)
                                funded_bounty=existing_escrow
                                if desired>existing_escrow:
                                    mpath,bounty_market=load_market(source_region)
                                    funding=fund_bounty_escrow(bounty_market,existing_warrant=existing,desired_cash=desired)
                                    funded_bounty=int(funding["escrow_cash"])
                                    if int(funding["escrow_added_cash"]):
                                        bounty_market=funding["market_after"]
                                        market_cache[source_region]=(mpath,bounty_market)
                                        writes[mpath]=bounty_market
                                warrants[warrant_ref]={
                                    "subject_ref":attacker_ref,"offense":public_offense,"bounty_cash":funded_bounty,
                                    "bounty_escrow_cash":funded_bounty,
                                    "status":str(existing.get("status") or "active") if str(existing.get("status") or "active") in {"active","pursuing"} else "active",
                                    "evidence_ref":contact_ref,"issued_at":str(existing.get("issued_at") or at_iso),
                                    "jurisdiction_ref":source_region,
                                }
                                attention_rows[attacker_ref]["bounty_cash"]=funded_bounty
                        writes[_GOVERNMENT_PATH]=government_state
                    doctrines: dict[str, Mapping[str, Any]] = {}
                    if beneficiary:
                        _fp, bf = load_faction(beneficiary); doctrines[beneficiary] = bf.get("doctrine", {}) if isinstance(bf.get("doctrine"), Mapping) else {}
                    _ofp, ofaction = load_faction(attacker_fid); doctrines[attacker_fid] = ofaction.get("doctrine", {}) if isinstance(ofaction.get("doctrine"), Mapping) else {}
                    if player_ref and player_ref in participants:
                        # A player-facing combat can remain unresolved across
                        # turns, so the outlaw attackers need the same finite
                        # availability reservation as the convoy. Otherwise the
                        # scheduler could use the same bodies in another route
                        # action while this combat owner is still live.
                        commitments_state = reserve_resources(
                            commitments_state,
                            resources=[("person", ref, attacker_fid) for ref in attacker_refs],
                            actor_ref=attacker_refs[0], owner_ref=attacker_fid,
                            activity_ref=contact_ref, activity_kind="route_attack",
                            started_at=at_iso, location_ref=str(rid),
                        )
                        pause_people_for_commitment(attacker_fid, attacker_refs); combat_ref = f"combat:{contact_ref}"
                        combat_side_a=(escort_refs if movement.get("movement_kind") == "raid_return" else participants)
                        combat_env=_movement_environment(movement_ref,movement)
                        combat_mounts=_movement_mount_assignments(movement,combat_side_a)
                        combat = initialize_combat(
                            combat_ref=combat_ref, side_a_refs=combat_side_a, side_b_refs=attacker_refs,
                            people=people, zone_ref=str(rid), started_at=at_iso,
                            objective={
                                "kind": "retain_seized_people_and_cargo" if movement.get("movement_kind") == "raid_return" else "protect_cargo",
                                "movement_ref": movement_ref,
                            },
                            awareness_mode="mutual", initial_range_band=2, equipment_ledger=equipment_ledger,
                            environment=combat_env, mount_assignments=combat_mounts,
                        )
                        combats[combat_ref] = combat
                        contacts[contact_ref] = {
                            "movement_ref": movement_ref, "route_ref": rid,
                            "attacker_faction_ref": attacker_fid, "escort_refs": escort_refs,
                            "attacker_refs": attacker_refs, "combat_ref": combat_ref, "status": "active",
                        }
                        movement["status"] = "contact_pending"; movement["contact_ref"] = contact_ref
                        movement["combat_ref"] = combat_ref; movement["contact_attacker_faction_ref"] = attacker_fid
                        movement["contact_attacker_refs"] = attacker_refs
                        movement["contact_intent"] = str(decision.get("intent") or "hostile_interception")
                        movements[movement_ref] = movement
                        writes[_COMBATS_PATH] = combats_state
                        writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                        try:
                            scene = copy.deepcopy(dict(read_json(_SCENE_PATH)))
                            scene["active_combat_ref"] = combat_ref
                            scene["location_id"] = str(rid)
                            scene["present_person_ids"] = participants + attacker_refs
                            scene["visible_person_ids"] = participants + attacker_refs
                            writes[_SCENE_PATH] = scene
                        except FileNotFoundError:
                            pass
                        row = {
                            "kind": "hostile_contact", "event_id": contact_ref, "route_ref": rid,
                            "combat_ref": combat_ref, "movement_ref": movement_ref,
                            "attacker_faction_ref": attacker_fid, "requires_player_decision": True,
                            "delivered_to_player": True,
                        }
                        handoff = classify_handoff(row); handoffs.append({**row, "handoff": handoff})
                        route_review["player_contact"] = contact_ref
                        continue
                    combat_side_a=(escort_refs if movement.get("movement_kind") == "raid_return" else participants)
                    combat_env=_movement_environment(movement_ref,movement)
                    combat_mounts=_movement_mount_assignments(movement,combat_side_a)
                    result = simulate_exact_combat(
                        combat_ref=f"combat:{contact_ref}", side_a_refs=combat_side_a, side_b_refs=attacker_refs,
                        people=people, equipment_ledger=equipment_ledger, doctrines=doctrines,
                        zone_ref=str(rid), started_at=at_iso,
                        objective={
                            "kind": "retain_seized_people_and_cargo" if movement.get("movement_kind") == "raid_return" else "protect_cargo",
                            "movement_ref": movement_ref,
                        },
                        # Background route predation is an interception window,
                        # not an off-screen deathmatch. Four exact exchanges
                        # establish physical contact, injuries and reaction
                        # saturation. If no decisive result exists after that
                        # bounded window, the interception failed and the
                        # attackers disengage. Player-present contacts remain
                        # normal full exact-combat owners above.
                        targeting_intent="disable", max_exchanges=4, environment=combat_env, mount_assignments=combat_mounts, initial_range_band=2,
                        social_state=social_state,
                    )
                    social_state = copy.deepcopy(dict(result.get("social_state_after") or social_state))
                    writes[_SOCIAL_PATH] = social_state
                    equipment_ledger = copy.deepcopy(dict(result["equipment_ledger_after"]))
                    people_after = result["people_after"]
                    for ref, person in people_after.items():
                        if isinstance(ref, str) and isinstance(person, Mapping):
                            save_exact_person(ref, person)
                    writes[_EQUIPMENT_LEDGER_PATH] = equipment_ledger
                    newly_dead = {
                        str(ref) for ref, person in people_after.items()
                        if isinstance(ref, str) and isinstance(person, Mapping)
                        and isinstance(person.get("health"), Mapping) and person.get("health", {}).get("status") == "dead"
                    }
                    protected_refs = {str(x) for x in movement.get("protected_person_refs", []) if isinstance(x, str)}
                    protected_dead = protected_refs & newly_dead
                    if newly_dead:
                        surviving_participants = [ref for ref in participants if ref not in newly_dead]
                        surviving_escorts = [ref for ref in escort_refs if ref not in newly_dead]
                        movement["participant_refs"] = surviving_participants
                        movement["escort_refs"] = surviving_escorts
                        close_dead_current_authorities(sorted(newly_dead))
                        participants = surviving_participants
                        escort_refs = surviving_escorts
                    attacker_control = bool(
                        (result.get("resolved") and result.get("winner_side") == "side_b")
                        or not escort_refs
                    )
                    if protected_dead or attacker_control:
                        seizure = {"started": False}
                        if attacker_control:
                            seizure = _start_seizure_return(
                                movement_ref, movement, attacker_fid=attacker_fid,
                                attacker_refs=attacker_refs,
                                intent=str(decision.get("intent") or "hostile_interception"),
                                people_after=people_after, allow_empty_return=True,
                            )
                        else:
                            # A failed ambush is still a physical expedition. The
                            # surviving attackers must retreat home through the same
                            # route instead of becoming instantly free at their base.
                            _start_attacker_retreat_return(
                                movement_ref, movement, attacker_fid=attacker_fid,
                                attacker_refs=attacker_refs, people_after=people_after,
                            )
                        outcome = _close_escort(
                            movement_ref, movement, success=False,
                            attacker_fid=attacker_fid if attacker_control else None,
                            defer_seized_cargo=bool(seizure.get("started")),
                            captured_refs=seizure.get("captured_refs", []),
                        )
                        route_review["completed_movements"] += int(bool(outcome.get("closed")))
                        if outcome.get("closed"):
                            route_review["closed_outcomes"][movement_ref] = bool(outcome.get("success"))
                        continue
                    # The convoy held. Surviving attackers still need real travel
                    # time back to their headquarters before those people can be
                    # selected for another raid, escort attack, or faction task.
                    _start_attacker_retreat_return(
                        movement_ref, movement, attacker_fid=attacker_fid,
                        attacker_refs=attacker_refs, people_after=people_after,
                    )
                    _apply_shared_danger_social(participants)

                # The movement clock was advanced at the start of this owner
                # boundary, so completion follows the exact current clock.
                if progressed_seconds > 0:
                    route_review["movements_advanced"] += 1
                if movement_complete(movement):
                    # Multi-edge physical journeys never jump from the first road
                    # directly to their ultimate destination. Player travel and
                    # purpose-owned faction expeditions advance one exact route
                    # segment at a time through this shared lifecycle.
                    if _begin_intermediate_route_stop(movement_ref, movement):
                        route_review["settlement_stops"] = int(route_review.get("settlement_stops", 0)) + 1
                        continue
                    outcome = _close_escort(movement_ref, movement, success=True)
                    route_review["completed_movements"] += int(bool(outcome.get("closed")))
                    if outcome.get("closed"):
                        route_review["closed_outcomes"][movement_ref] = bool(outcome.get("success"))
                else:
                    movements[movement_ref] = movement
                    writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
            reviews.append(route_review)
        if route_events:
            # A deployment roster describes purpose; the linked route owner is
            # present-tense physical custody. If a live issued holder has been
            # captured, hospitalized, abandoned, or otherwise separated from the
            # movement, detach that holder before the frontier is persisted so a
            # later return cannot reclaim their gear remotely.
            deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
            if isinstance(deployments, dict):
                for op_ref, raw_op in list(deployments.items()):
                    if not isinstance(raw_op, Mapping) or not isinstance(raw_op.get("issued_equipment"), Mapping):
                        continue
                    movement_ref = str(raw_op.get("physical_movement_ref") or "")
                    if not movement_ref:
                        continue
                    movement = movements.get(movement_ref)
                    if not isinstance(movement, Mapping):
                        continue
                    physical_refs = {
                        str(ref) for ref in movement.get("participant_refs", [])
                        if isinstance(ref, str) and ref
                    } if isinstance(movement.get("participant_refs"), list) else set()
                    issue_refs = {
                        str(ref) for ref in raw_op.get("issued_equipment", {})
                        if isinstance(ref, str) and ref
                    }
                    separated_refs = sorted(issue_refs - physical_refs)
                    if separated_refs:
                        _detach_operation_issue_refs(op_ref, separated_refs)

            route_ops_state["movements"] = {
                str(ref): compact_route_movement_roles(row) if isinstance(row, Mapping) else row
                for ref, row in movements.items()
            }
            movements = route_ops_state["movements"]
            if _chunk_contains_final_owner(schedule, sorted_events, class_id="route_daily"):
                route_ops_state.pop("daily_route_attack_budget", None)
            else:
                route_ops_state["daily_route_attack_budget"] = attack_tracker
            writes[_ROUTE_OPERATIONS_PATH] = route_ops_state

        # Beliefs about route-specific claims are current intelligence, not a
        # permanent rumor archive.  Once a movement closes, no exact observer
        # can act on that movement-specific belief, so compact it immediately.
        remaining_movement_refs = {str(ref) for ref in movements if isinstance(ref, str)} if isinstance(movements, Mapping) else set()
        closed_subject_refs = sorted(initial_movement_refs - remaining_movement_refs)
        if closed_subject_refs:
            compacted_social = prune_beliefs_for_subject_refs(social_state, closed_subject_refs)
            if compacted_social != social_state:
                social_state = compacted_social
                writes[_SOCIAL_PATH] = social_state

    return {
        "commitments_state": commitments_state,
        "custody_state": custody_state,
        "equipment_ledger": equipment_ledger,
        "contract_index": contract_index,
        "active_contracts": active_contracts,
        "reputation_state": reputation_state,
        "social_state": social_state,
    }


__all__ = ["settle_route_frontier"]
