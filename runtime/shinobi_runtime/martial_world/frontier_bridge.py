"""Production-facing settlement bridge for the Jianghu domain frontier.

This module does not own campaign time.  The production ``advance_time`` command
owns chronology.  This bridge receives the single Jianghu frontier that campaign
chronology actually reached, settles only work due at that frontier, and returns
closed-owner after-images plus player-safe review summaries.

Recurring work remains compact: the scheduler stores only the next boundary for
its class, then :func:`settle_schedule` advances that class after the committed
frontier.  No person-by-person global tick and no pre-expanded year of events.
"""
from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from .autonomous_factions import procure_project_materials
from .aggregate_transport import faction_available_capacity, make_transport_reservation
from .calendar_modifiers import trade_capital_milli
from .handoffs import classify_handoff
from .regional_economy import (
    execute_purchase,
    quote_sale,
    trade_shipment_opportunities,
    unit_market_price_cash,
)
from .scheduler import prune_contract_expiry_events, route_ids_needing_service, settle_schedule, sync_faction_activity, sync_route_activity, upsert_one_off_event
from .upkeep import monthly_upkeep_quote
from .compensation import monthly_stipend
from .person_state import compact_roster_state, hydrate_roster_state, reconcile_faction_population
from .physiology_frontier import new_physiology_wakes_from_touched_people, settle_due_person_physiology, settle_review_faction_physiology
from .faction_registry import REGISTRY_PATH as _FACTION_REGISTRY_PATH, current_faction_refs, unregister_faction
from .faction_existence import settle_extinctions_from_touched_rosters
from .faction_state import (
    compact_faction_state,
    faction_path,
    resolved_faction_type,
    hydrate_faction_state,
    inventory_path,
    roster_path,
    with_derived_population,
)
from .inventory_state import compact_inventory_state, hydrate_inventory_state
from .training import advance_faction_training_epoch, institutional_training_pause_refs, settle_and_reset_faction_training_cycle
from .infrastructure import (
    infirmary_capacity,
    enterprise_scale_value,
    workshop_capacity,
    enterprise_operating_efficiency_milli,
    start_building_expansion,
    building_expansion_requirements,
    building_upgrade_requirements,
    start_building_upgrade,
    start_enterprise_scale_expansion,
    enterprise_scale_basis,
    transport_yard_capacity,
    compact_project_state,
)
from .commitments import derived_commitment_state, release_resources, reserve_resources
from .exact_combat import capability_from_person
from .family_simulation import apply_recognized_succession
from .institutional_lifecycle import settle_institutional_offices
from .death_lifecycle import close_family_authorities, exact_person_index, is_living, prune_dead_from_durable_activities, release_custody_held_by_extinct_factions, settle_exact_death_estates
from .faction_relations import (
    apply_relation_event, refresh_coalition_decision_view,
    resolve_friendly_aid_transfer, settle_positive_obligation,
)
from .escort_living_world import principal_ransom_value_cash
from .escort import cargo_unit_mass_grams
from .captivity_lifecycle import close_kin_refs, rescue_force_size, should_launch_rescue
from .live_state import person_route as routed_person_route
from .civic import compact_civic_person, hydrate_civic_person
from .independent_people import compact_independent_person, hydrate_independent_person
from .manpower import is_faction_member
from .outlaws import outlaw_raid_target_is_local
from .strategic_autonomy import stable_permille
from .doctrines import resolve_faction_force_intent
from .travel import travel_plan
from .physical_travel import build_route_journey
from .route_activity import ROUTE_SERVICE_STATUSES, route_controlling_refs
from .travel_provisions import planned_journey_seconds, provisioning_journey_seconds, reserve_faction_rations
from .faction_politics import faction_camp
from .frontier_support import (
    arrival_site as _arrival_site,
    event_order as _event_order,
    market_path as _market_path,
    person_place as _person_place,
    place_to_region as _place_to_region,
    relations_by_faction as _relations_by_faction,
    route_lookup as _route_lookup,
)
from .route_frontier import settle_route_frontier
from .life_frontier import appoint_civic_successors, settle_annual_life_frontier
from .institutional_evolution_frontier import settle_autonomous_institutional_evolution
from .captivity_frontier import settle_captivity_frontier
from .autonomy_frontier import settle_faction_autonomy_frontier
from .tournament_frontier import settle_tournament_frontier
from .regional_frontier import settle_regional_frontier
from .faction_cycle_frontier import settle_faction_cycle_frontier
from .project_frontier import settle_project_frontier
from .equipment_frontier import settle_equipment_maintenance_frontier
from .clinical_physiology import prepare_patient_for_treatment, rebase_treated_patient_wakes

_SCHEDULER_PATH = "state/martial-world/scheduler.json"
_CONTRACT_INDEX_PATH = "state/martial-world/contracts/index.json"
_RELATIONS_PATH = "state/martial-world/faction-relations.json"
_GEOGRAPHY_PATH = "game/data/martial-world/geography.json"
_REGIONAL_ECONOMY_PATH = "game/data/martial-world/regional-economy.json"
_SOCIAL_PATH = "state/martial-world/social.json"
_META_PATH = "state/meta.json"
_ROUTE_OPERATIONS_PATH = "state/martial-world/route-operations.json"
_EQUIPMENT_LEDGER_PATH = "state/martial-world/equipment-ledger.json"
_LOCAL_SITES_PATH = "game/data/martial-world/local-sites.json"
_COMBATS_PATH = "state/martial-world/combats.json"
_TRAVEL_DATA_PATH = "game/data/martial-world/travel.json"
_TOURNAMENTS_PATH = "state/martial-world/tournaments.json"
_REPUTATION_PATH = "state/martial-world/reputation.json"
_FAMILY_PATH = "state/martial-world/family.json"
_GOVERNMENT_PATH = "state/martial-world/government.json"
_GOVERNMENT_TROOPS_PATH = "game/data/martial-world/government-troops.json"
_CIVILIANS_PATH = "state/martial-world/civilian-populations.json"
_CUSTODY_PATH = "state/martial-world/custody.json"
_INDEPENDENTS_PATH = "state/martial-world/independent-people.json"
_CIVIC_PEOPLE_PATH = "state/martial-world/civic-people.json"
_DEPLOYMENTS_PATH = "state/martial-world/deployments.json"
_PROJECTS_PATH = "state/martial-world/projects.json"

def settle_shared_frontier(
    *,
    read_json: Callable[[str], Mapping[str, Any]],
    schedule: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    at: datetime,
) -> dict[str, Any]:
    """Settle one exact due frontier and return authoritative after-images.

    The caller is responsible for committing these after-images atomically with
    the production campaign clock.  This function is deterministic and performs
    no wall-clock, RNG, filesystem listing, or model reasoning.
    """
    at_iso = at.isoformat()
    writes: dict[str, dict[str, Any]] = {}
    reviews: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []
    pending_one_off_events: list[dict[str, Any]] = []

    try:
        relations = read_json(_RELATIONS_PATH)
    except FileNotFoundError:
        relations = {"edges": []}
    if not isinstance(relations, Mapping):
        raise ValueError("jianghu faction relations invalid")
    relations_state = copy.deepcopy(dict(relations))
    relation_index = _relations_by_faction(relations_state)

    def directed_relation(source_faction: str, target_faction: str) -> Mapping[str, Any] | None:
        if not source_faction or not target_faction or source_faction == target_faction:
            return None
        for edge in relation_index.get(source_faction, []):
            if isinstance(edge, Mapping) and edge.get("to_faction") == target_faction:
                return edge
        return None

    def apply_directed_relation_event(source_faction: str, target_faction: str, event_kind: str) -> None:
        if not source_faction or not target_faction or source_faction == target_faction:
            return
        edges = relations_state.setdefault("edges", [])
        if not isinstance(edges, list):
            raise ValueError("jianghu faction relations edges invalid")
        prior = next((
            edge for edge in edges
            if isinstance(edge, Mapping) and edge.get("from_faction") == source_faction
            and edge.get("to_faction") == target_faction
        ), None)
        reverse = next((
            edge for edge in edges
            if isinstance(edge, Mapping) and edge.get("from_faction") == target_faction
            and edge.get("to_faction") == source_faction
        ), None)
        updated = apply_relation_event(prior, from_faction=source_faction, to_faction=target_faction, event_kind=event_kind)
        edges[:] = [
            edge for edge in edges
            if not (isinstance(edge, Mapping) and edge.get("from_faction") == source_faction and edge.get("to_faction") == target_faction)
        ]
        edges.append(updated)
        edges.sort(key=lambda edge: (str(edge.get("from_faction", "")), str(edge.get("to_faction", ""))))
        relation_index[source_faction] = [edge for edge in edges if isinstance(edge, Mapping) and edge.get("from_faction") == source_faction]
        writes[_RELATIONS_PATH] = relations_state
    def settle_directed_obligation(source_faction: str, target_faction: str, amount: int) -> int:
        if not source_faction or not target_faction or source_faction == target_faction:
            return 0
        prior = directed_relation(source_faction, target_faction)
        current = max(0, int(prior.get("obligation", 0))) if isinstance(prior, Mapping) else 0
        if current <= 0:
            return 0
        edges = relations_state.setdefault("edges", [])
        if not isinstance(edges, list):
            raise ValueError("jianghu faction relations edges invalid")
        updated = settle_positive_obligation(
            prior, from_faction=source_faction, to_faction=target_faction, amount=amount,
        )
        edges[:] = [
            edge for edge in edges
            if not (isinstance(edge, Mapping) and edge.get("from_faction") == source_faction and edge.get("to_faction") == target_faction)
        ]
        edges.append(updated)
        edges.sort(key=lambda edge: (str(edge.get("from_faction", "")), str(edge.get("to_faction", ""))))
        relation_index[source_faction] = [edge for edge in edges if isinstance(edge, Mapping) and edge.get("from_faction") == source_faction]
        writes[_RELATIONS_PATH] = relations_state
        return current - max(0, int(updated.get("obligation", 0)))

    try:
        contract_index = read_json(_CONTRACT_INDEX_PATH)
    except FileNotFoundError:
        contract_index = {"active": {}}
    active_contracts = contract_index.get("active", {}) if isinstance(contract_index, Mapping) else {}
    if not isinstance(active_contracts, Mapping):
        raise ValueError("jianghu contract index invalid")

    try:
        social_state = read_json(_SOCIAL_PATH)
    except FileNotFoundError:
        social_state = {"relationships": {}}
    if not isinstance(social_state, Mapping):
        social_state = {"relationships": {}}
    try:
        meta_state = read_json(_META_PATH)
    except FileNotFoundError:
        meta_state = {}
    player_ref = str(meta_state.get("player_id", "")) if isinstance(meta_state, Mapping) else ""
    world_seed = str(meta_state.get("world_seed", "jianghu-world")) if isinstance(meta_state, Mapping) else "jianghu-world"
    # Current faction existence is mutable campaign truth. The world seed is
    # creation-time authorship and must never hide founded/splintered factions
    # or keep destroyed factions alive in simulation loops.
    all_faction_ids = current_faction_refs(read_json)
    try:
        civilian_state = copy.deepcopy(dict(read_json(_CIVILIANS_PATH)))
    except FileNotFoundError:
        civilian_state = {"schema": "jianghu-civilian-populations-1.0", "places": {}}
    if not isinstance(civilian_state.get("places", {}), Mapping):
        raise ValueError("jianghu civilian population state invalid")

    try:
        independent_state = copy.deepcopy(dict(read_json(_INDEPENDENTS_PATH)))
    except FileNotFoundError:
        independent_state = {"schema": "jianghu-independent-people-state-1.0", "people": []}
    if not isinstance(independent_state.get("people"), list):
        raise ValueError("jianghu independent people state invalid")
    try:
        civic_state = copy.deepcopy(dict(read_json(_CIVIC_PEOPLE_PATH)))
    except FileNotFoundError:
        civic_state = {"schema": "jianghu-civic-people-state-1.0", "people": []}
    if not isinstance(civic_state.get("people"), list):
        raise ValueError("jianghu civic people state invalid")
    # Semantic event receipts are transient frontier telemetry, not save-game state.

    try:
        geography = read_json(_GEOGRAPHY_PATH)
    except FileNotFoundError:
        geography = {"routes": [], "places": {}}
    if not isinstance(geography, Mapping):
        raise ValueError("jianghu geography invalid")
    route_index = _route_lookup(geography)
    place_region = _place_to_region(geography)
    try:
        economy_rules = read_json("game/data/martial-world/economy.json")
    except FileNotFoundError:
        economy_rules = {"labor": {"general_labor_cash_per_hour": 30}}
    labor_rules = economy_rules.get("labor", {}) if isinstance(economy_rules, Mapping) else {}
    general_labor_cash_per_hour = max(1, int(labor_rules.get("general_labor_cash_per_hour", 30))) if isinstance(labor_rules, Mapping) else 30

    faction_cache: dict[str, tuple[str, dict[str, Any]]] = {}
    inventory_cache: dict[str, tuple[str, dict[str, Any]]] = {}
    market_cache: dict[str, tuple[str, dict[str, Any]]] = {}
    roster_cache: dict[str, tuple[str, dict[str, Any]]] = {}

    def load_faction(fid: str) -> tuple[str, dict[str, Any]]:
        if fid not in faction_cache:
            path = faction_path(fid)
            staged = writes.get(path)
            row = staged if isinstance(staged, Mapping) else read_json(path)
            if not isinstance(row, Mapping) or row.get("faction_id") != fid:
                raise ValueError("jianghu faction owner invalid")
            faction = hydrate_faction_state(row)
            rpath = roster_path(fid)
            roster_row = writes.get(rpath) if isinstance(writes.get(rpath), Mapping) else read_json(rpath)
            faction_cache[fid] = (path, with_derived_population(faction, roster_row))
        return faction_cache[fid]

    def current_faction_type(fid: str) -> str:
        try:
            _path, faction = load_faction(fid)
        except (FileNotFoundError, ValueError):
            return ""
        return resolved_faction_type(faction)

    def load_inventory(fid: str) -> tuple[str, dict[str, Any]]:
        if fid not in inventory_cache:
            path = inventory_path(fid)
            staged = writes.get(path)
            row = staged if isinstance(staged, Mapping) else read_json(path)
            if not isinstance(row, Mapping) or row.get("faction_ref") != fid:
                raise ValueError("jianghu inventory owner invalid")
            inventory_cache[fid] = (path, hydrate_inventory_state(row))
        return inventory_cache[fid]

    def load_roster(fid: str) -> tuple[str, dict[str, Any]]:
        if fid not in roster_cache:
            _fpath, faction = load_faction(fid)
            path = roster_path(fid)
            staged = writes.get(path)
            row = staged if isinstance(staged, Mapping) else read_json(path)
            if not isinstance(row, Mapping) or row.get("faction_ref") != fid:
                raise ValueError("jianghu roster invalid")
            roster_cache[fid] = (path, hydrate_roster_state(row, faction=faction))
        path, roster = roster_cache[fid]
        # Availability is owned by live projects/routes/deployments/custody, not
        # by a duplicated per-person boolean. Project it into the hydrated roster
        # for this frontier only so training/duty logic sees the same exact body
        # reservation authority. The final compactor strips this projection.
        # Training availability follows the same single physical-presence contract
        # as travel, combat, custody, projects and social attendance. A route/combat/
        # custody owner may leave the sparse roster location at its home default,
        # so commitments alone are not a sufficient pause set.
        busy = unavailable_person_refs()
        people = roster.get("people", []) if isinstance(roster, Mapping) else []
        if isinstance(people, list):
            _fpath, faction = load_faction(fid)
            paused = set(institutional_training_pause_refs(faction, [p for p in people if isinstance(p, Mapping)], unavailable_refs=sorted(busy)))
            projected: list[Any] = []
            for raw in people:
                if not isinstance(raw, Mapping):
                    projected.append(raw)
                    continue
                person = copy.deepcopy(dict(raw))
                state = copy.deepcopy(dict(person.get("training_state", {}))) if isinstance(person.get("training_state"), Mapping) else {}
                if str(person.get("person_id") or "") in paused:
                    state["institutional_paused"] = True
                else:
                    state.pop("institutional_paused", None)
                if state:
                    person["training_state"] = state
                else:
                    person.pop("training_state", None)
                projected.append(person)
            roster["people"] = projected
        roster_cache[fid] = (path, roster)
        return roster_cache[fid]

    def load_market(region: str) -> tuple[str, dict[str, Any]]:
        if region not in market_cache:
            path = _market_path(region)
            staged = writes.get(path)
            row = staged if isinstance(staged, Mapping) else read_json(path)
            if not isinstance(row, Mapping):
                raise ValueError("jianghu market invalid")
            market_cache[region] = (path, copy.deepcopy(dict(row)))
        return market_cache[region]

    commitments_state = derived_commitment_state(read_json)
    try:
        route_ops_state = copy.deepcopy(dict(read_json(_ROUTE_OPERATIONS_PATH)))
    except FileNotFoundError:
        route_ops_state = {"schema": "jianghu-route-operations-state-1.0", "movements": {}, "contacts": {}}
    try:
        projects_state = copy.deepcopy(dict(read_json(_PROJECTS_PATH)))
    except FileNotFoundError:
        projects_state = {"schema": "jianghu-project-registry-1.0", "projects": {}}
    try:
        deployments_state = copy.deepcopy(dict(read_json(_DEPLOYMENTS_PATH)))
    except FileNotFoundError:
        deployments_state = {"schema": "jianghu-deployment-state-1.0", "deployments": {}}
    if not isinstance(projects_state.get("projects"), dict) or not isinstance(deployments_state.get("deployments"), dict):
        raise ValueError("jianghu project/deployment state invalid")
    try:
        local_sites = read_json(_LOCAL_SITES_PATH)
    except FileNotFoundError:
        local_sites = {"sites": {}}
    site_rows = local_sites.get("sites", {}) if isinstance(local_sites, Mapping) else {}
    if not isinstance(site_rows, Mapping):
        raise ValueError("jianghu local sites invalid")
    try:
        travel_data = read_json(_TRAVEL_DATA_PATH)
    except FileNotFoundError:
        travel_data = {"mode_speed_km_per_day": {"convoy": 24}, "terrain_time_milli": {}, "road_time_milli": {}}
    try:
        tournament_state = copy.deepcopy(dict(read_json(_TOURNAMENTS_PATH)))
    except FileNotFoundError:
        tournament_state = {"schema": "jianghu-tournament-state-1.0", "tournaments": {}}
    if not isinstance(tournament_state.get("tournaments"), dict):
        raise ValueError("jianghu tournament state invalid")
    try:
        reputation_state = copy.deepcopy(dict(read_json(_REPUTATION_PATH)))
    except FileNotFoundError:
        reputation_state = {"schema": "jianghu-reputation-state-1.0", "audiences": {}, "rankings": {}}
    if not isinstance(reputation_state.get("audiences"), dict) or not isinstance(reputation_state.get("rankings"), dict):
        raise ValueError("jianghu reputation state invalid")
    try:
        family_state = copy.deepcopy(dict(read_json(_FAMILY_PATH)))
    except FileNotFoundError:
        family_state = {"schema": "jianghu-family-state-1.0", "marriages": {}, "parentage": {}, "households": {}, "succession_claims": {}}
    if not isinstance(family_state.get("marriages", {}), Mapping):
        raise ValueError("jianghu family state invalid")
    try:
        government_state = copy.deepcopy(dict(read_json(_GOVERNMENT_PATH)))
    except FileNotFoundError:
        government_state = {"schema": "jianghu-government-state-1.0", "attention": {}, "warrants": {}, "regional_capacity": {}}
    try:
        government_troops = read_json(_GOVERNMENT_TROOPS_PATH)
    except FileNotFoundError:
        government_troops = {"default_regional_capacity": {"militia": 120, "standard": 60, "elite": 12}, "monthly_reconstitution": {"militia": 40, "standard": 20, "elite": 4}, "contact_resolution": {"militia_power": 35, "standard_power": 65, "elite_power": 95, "detention_advantage_milli": 1800}}
    try:
        custody_state = copy.deepcopy(dict(read_json(_CUSTODY_PATH)))
    except FileNotFoundError:
        custody_state = {"schema": "jianghu-custody-state-1.0", "records": []}
    if not isinstance(custody_state.get("records"), list):
        raise ValueError("jianghu custody state invalid")
    try:
        equipment_ledger = copy.deepcopy(dict(read_json(_EQUIPMENT_LEDGER_PATH)))
    except FileNotFoundError:
        equipment_ledger = {"schema": "jianghu-equipment-ledger-1.0", "person_loadouts": {}}
    try:
        combats_state = copy.deepcopy(dict(read_json(_COMBATS_PATH)))
    except FileNotFoundError:
        combats_state = {"schema": "jianghu-combat-state-1.0", "combats": {}}
    combats = combats_state.setdefault("combats", {})
    if not isinstance(combats, dict):
        raise ValueError("jianghu combat state invalid")

    def commitment_person_refs(state: Mapping[str, Any] | None = None) -> set[str]:
        source = commitments_state if state is None else state
        index = source.get("person_index", {}) if isinstance(source, Mapping) else {}
        return {str(x) for x in index} if isinstance(index, Mapping) else set()

    def committed_person_refs() -> set[str]:
        return commitment_person_refs()

    def custody_person_refs() -> set[str]:
        rows = custody_state.get("records", []) if isinstance(custody_state, Mapping) else []
        if not isinstance(rows, list):
            return set()
        return {
            str(row.get("person_ref"))
            for row in rows
            if isinstance(row, Mapping)
            and isinstance(row.get("person_ref"), str)
            and row.get("status") not in {"released", "escaped", "rescued", "executed"}
        }

    def active_combat_person_refs() -> set[str]:
        refs: set[str] = set()
        for combat in combats.values():
            if not isinstance(combat, Mapping) or combat.get("status") != "active":
                continue
            sides = combat.get("sides", {})
            if not isinstance(sides, Mapping):
                continue
            for side in ("side_a", "side_b"):
                members = sides.get(side, [])
                if isinstance(members, list):
                    refs.update(str(x) for x in members if isinstance(x, str))
        return refs

    def active_route_person_refs() -> set[str]:
        refs: set[str] = set()
        movements = route_ops_state.get("movements", {}) if isinstance(route_ops_state, Mapping) else {}
        if not isinstance(movements, Mapping):
            return refs
        for movement in movements.values():
            if not isinstance(movement, Mapping) or str(movement.get("status") or "") not in ROUTE_SERVICE_STATUSES:
                continue
            for key in ("participant_refs", "protected_person_refs", "captive_refs", "rescued_refs"):
                values = movement.get(key)
                if isinstance(values, list):
                    refs.update(str(x) for x in values if isinstance(x, str))
        return refs

    def unavailable_person_refs(state: Mapping[str, Any] | None = None) -> set[str]:
        return commitment_person_refs(state) | custody_person_refs() | active_combat_person_refs() | active_route_person_refs()

    def active_strategic_operations(fid: str) -> int:
        rows = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
        if not isinstance(rows, Mapping):
            return 0
        return sum(
            1 for row in rows.values()
            if isinstance(row, Mapping) and row.get("faction_ref") == fid
            and row.get("operation_kind") in {"formal_challenge", "faction_raid", "faction_war_strike", "custody_rescue"}
            and row.get("status") not in {"completed", "cancelled"}
        )

    def start_strategic_operation(fid: str, intent: Mapping[str, Any]) -> dict[str, Any]:
        nonlocal commitments_state
        kind = str(intent.get("action") or "")
        target_fid = str(intent.get("target_faction_ref") or "")
        if kind not in {"formal_challenge", "faction_raid", "faction_war_strike"} or not target_fid or target_fid == fid:
            return {"result": "invalid_intent"}
        # The player's own faction never launches an autonomous protected
        # offensive. NPC factions may still attack it and the consequence will
        # surface through normal handoff/information paths.
        if fid == "house_tang":
            return {"result": "player_faction_offense_protected"}
        try:
            fpath, faction = load_faction(fid); tfpath, target = load_faction(target_fid)
            rpath, roster = load_roster(fid); ipath, inventory = load_inventory(fid)
        except (KeyError, FileNotFoundError, ValueError):
            return {"result": "target_unresolved"}
        source_place = str(faction.get("headquarters") or "")
        target_place = str(target.get("headquarters") or "")
        if (kind == "faction_raid" and resolved_faction_type(faction) == "outlaw_faction"
                and not outlaw_raid_target_is_local(faction, target_place=target_place, read_json=read_json)):
            return {"result": "outlaw_raid_target_outside_operating_routes"}
        # Autonomous expeditions need real preparation. Independent monthly
        # reviews must not make every challenge/raid/war column step onto the
        # road at the exact review timestamp. The same deterministic intent is
        # preserved, but mobilization, mustering and route preparation spread
        # unrelated operations across believable hours/days.
        prep_roll = stable_permille("strategic-mobilization", fid, target_fid, kind, at.year, at.month)
        if kind == "formal_challenge":
            prep_hours = 12 + prep_roll * 24 // 999
        elif kind == "faction_war_strike":
            prep_hours = 18 + prep_roll * 78 // 999
        else:
            prep_hours = 6 + prep_roll * 42 // 999
        departure_at = at + timedelta(hours=max(1, int(prep_hours)))
        if not source_place or not target_place or source_place == target_place:
            # Same-settlement rivalry does not need a travel operation; a
            # challenge still needs preparation before the target contact.
            plan = {"arrival_at": (departure_at + timedelta(days=1)).isoformat(), "travel_hours": 24.0, "toll_cash": 0, "edges": []}
        else:
            try:
                plan = travel_plan(world_seed=world_seed, start_at=departure_at, start=source_place, end=target_place, mode="foot")
            except (KeyError, ValueError):
                return {"result": "no_registered_route"}
        blocked = unavailable_person_refs()
        available = [p for p in usable_martial_people(roster, exclude_committed=blocked) if _person_place(p, local_sites=local_sites, home_place=source_place, home_site_ref=str(faction.get("local_site_ref") or "")) == source_place]
        # Children/retired members do not become autonomous expedition bodies.
        available = [
            p for p in available
            if at.year - int(p.get("birth_year", at.year)) >= 16 and not bool(p.get("retired_from_field", False))
        ]
        available.sort(key=lambda p: (-person_combat_index(p), str(p.get("person_id", ""))))
        risk = max(0, min(100, int((faction.get("autonomy_policy") or {}).get("risk_tolerance", 50)))) if isinstance(faction.get("autonomy_policy"), Mapping) else 50
        hostility = max(0, min(100, int(intent.get("hostility", 0))))
        if kind == "formal_challenge":
            desired = 1
        else:
            # The institution chooses a real detachment from its available body.
            # There is no engine-level 6/24-person ceiling: mission intent, risk,
            # hostility, coordination and the need to retain a home reserve set
            # the force size. Exact combat geometry/frontage later determines how
            # many of those real people can engage at the same instant.
            home_reserve = max(1, len(available) * max(15, 45 - risk // 3) // 100)
            deployable = max(0, len(available) - home_reserve)
            if kind == "faction_war_strike":
                fraction = max(25, min(80, 25 + risk // 2 + max(0, hostility - 60) // 2))
                desired = min(deployable, max(3, len(available) * fraction // 100))
            else:
                coordination_scale = max(3, int(len(available) ** 0.5) * max(80, 80 + risk) // 100)
                desired = min(deployable, coordination_scale)
        if desired <= 0:
            return {"result": "insufficient_available_fighters"}
        participants = [str(p["person_id"]) for p in available[:desired] if isinstance(p.get("person_id"), str)]
        if len(participants) < desired:
            return {"result": "insufficient_available_fighters"}
        # This is only an affordability screen. Actual food is reserved once,
        # from the current faction inventory, when the physical route leg really
        # departs under the shared travel authority.
        travel_days = max(1, (int(float(plan.get("travel_hours", 0)) * 1000) + 23999) // 24000)
        estimated_food_need = len(participants) * travel_days
        food_before = max(0, int(inventory.get("food_ration_days", 0)))
        if food_before < estimated_food_need:
            return {"result": "insufficient_travel_provisions"}
        # Formal public travel pays ordinary route tolls in both directions. A
        # covert/hostile raid does not receive a lawful toll abstraction here.
        toll = (max(0, int(plan.get("toll_cash", 0))) * 2) if kind == "formal_challenge" else 0
        cash_before = max(0, int(faction.get("treasury_cash", 0)))
        if cash_before < toll:
            return {"result": "insufficient_travel_cash"}
        toll_market = None; toll_market_path = ""; source_region = None
        if toll > 0:
            source_region = place_region.get(source_place)
            if not isinstance(source_region, str) or not source_region:
                return {"result": "travel_toll_destination_unresolved"}
            try:
                toll_market_path, toll_market = load_market(source_region)
            except (FileNotFoundError, ValueError):
                return {"result": "travel_toll_destination_unresolved"}
            if not isinstance(toll_market, dict) or toll_market.get("region_id") not in (None, source_region):
                return {"result": "travel_toll_destination_unresolved"}
        op_ref = f"operation:{kind}:{fid}:{target_fid}:{at.year:04d}{at.month:02d}"
        deployments = deployments_state.setdefault("deployments", {})
        if not isinstance(deployments, dict) or op_ref in deployments:
            return {"result": "operation_already_active"}
        try:
            commitments_state = reserve_resources(
                commitments_state,
                resources=[("person", ref, fid) for ref in participants],
                actor_ref=participants[0], owner_ref=fid, activity_ref=op_ref,
                activity_kind=kind, started_at=at_iso, location_ref=source_place,
            )
        except ValueError:
            return {"result": "participants_unavailable"}
        faction["treasury_cash"] = cash_before - toll
        if toll > 0 and isinstance(toll_market, dict) and isinstance(source_region, str):
            toll_market["cash_pool"] = max(0, int(toll_market.get("cash_pool", 0))) + toll
            writes[toll_market_path] = toll_market; market_cache[source_region] = (toll_market_path, toll_market)
        deployments[op_ref] = {
            "faction_ref": fid, "target_faction_ref": target_fid,
            "operation_kind": kind, "participant_refs": participants,
            "source_place_ref": source_place, "target_place_ref": target_place,
            "started_at": at_iso, "departure_at": departure_at.isoformat(), "arrival_at": str(plan.get("arrival_at")),
            "travel_hours": float(plan.get("travel_hours", 0)), "route_refs": list(plan.get("edges", [])),
            "status": "mobilizing",
            "arrival_event_kind": "faction_operation_arrival",
            "operation_intent": str(intent.get("operation_intent") or ("honor_challenge" if kind == "formal_challenge" else "punitive_expedition")),
            "targeting_intent": (
                "disable" if kind == "formal_challenge" or str(intent.get("operation_intent") or "") in {"robbery", "kidnapping", "cargo_seizure", "extortion", "cargo_diversion"}
                else resolve_faction_force_intent(faction.get("doctrine", {}) if isinstance(faction.get("doctrine"), Mapping) else {}, "battlefield" if kind == "faction_war_strike" else "lethal_attack")
            ),
        }
        pause_people_for_commitment(fid, participants)
        writes[_DEPLOYMENTS_PATH] = deployments_state;writes[fpath] = faction; writes[ipath] = inventory
        faction_cache[fid] = (fpath, faction); inventory_cache[fid] = (ipath, inventory)
        pending_one_off_events.append({
            "event_id": f"operation_departure:{op_ref}", "kind": "faction_operation_departure",
            "due_at": departure_at.isoformat(), "owner_ref": op_ref, "direction": "outbound",
            "arrival_event_kind": "faction_operation_arrival", "requires_player_decision": False,
        })
        if target_fid == "house_tang":
            notice = {
                "kind": "faction_war_movement" if kind == "faction_war_strike" else ("hostile_faction_movement" if kind == "faction_raid" else "faction_challenge_approaching"),
                "source_faction_ref": fid, "target_faction_ref": target_fid,
                "source_camp": faction_camp(fid) or "unclassified",
                "target_camp": faction_camp(target_fid) or "unclassified",
                "operation_ref": op_ref, "arrival_at": str(plan.get("arrival_at")),
                "delivered_to_player": True, "requires_player_decision": False,
            }
            handoff = classify_handoff(notice); handoffs.append({**notice, "handoff": handoff})
        return {"result": "mobilizing", "operation_ref": op_ref, "target_faction_ref": target_fid, "participant_count": len(participants)}

    def start_custody_rescue_operation(responder_fid: str, custody_record: Mapping[str, Any]) -> dict[str, Any]:
        """Dispatch exact people to recover one known captive from a real holding faction.

        The responder may act only after that faction is actually informed.  Its
        go/no-go estimate uses public institutional scale and doctrine, never the
        captor roster's hidden current character sheets.  Arrival combat still
        loads the exact people physically defending the target.
        """
        nonlocal commitments_state
        captive_ref = str(custody_record.get("person_ref") or "")
        holder_fid = str(custody_record.get("holder_faction_ref") or "")
        custody_id = str(custody_record.get("custody_id") or "")
        informed = {
            str(x) for x in custody_record.get("informed_faction_refs", [])
            if isinstance(x, str) and x
        }
        if responder_fid not in informed:
            return {"result": "rescue_not_informed"}
        if not responder_fid or not captive_ref or not holder_fid or responder_fid == holder_fid:
            return {"result": "rescue_target_invalid"}
        try:
            _rfpath, responder = load_faction(responder_fid)
            _rrpath, responder_roster = load_roster(responder_fid)
            ripath, responder_inventory = load_inventory(responder_fid)
            _hfpath, holder = load_faction(holder_fid)
        except (KeyError, FileNotFoundError, ValueError):
            return {"result": "rescue_owner_unresolved"}
        source_place = str(responder.get("headquarters") or "")
        holder_place = str(holder.get("headquarters") or "")
        custody_location = str(custody_record.get("location_ref") or "")
        custody_site = site_rows.get(custody_location) if isinstance(site_rows, Mapping) else None
        if isinstance(custody_site, Mapping):
            target_place = str(custody_site.get("parent_place_ref") or "")
            target_site = custody_location
            if target_place != holder_place:
                return {"result": "rescue_target_in_transit"}
        elif custody_location == holder_place:
            target_place = holder_place
            target_site = str(holder.get("local_site_ref") or holder_place)
        else:
            # Knowledge that somebody was abducted does not reveal an exact
            # moving coordinate. Keep the custody informed and reconsider it
            # when the captors reach a known holding site.
            return {"result": "rescue_target_in_transit"}
        if not source_place or not target_place or not target_site:
            return {"result": "rescue_location_unresolved"}
        departure_roll = stable_permille("custody-rescue-mobilization", custody_id, responder_fid, at_iso)
        departure_at = at + timedelta(hours=4 + departure_roll * 20 // 999)
        if source_place == target_place:
            plan = {"arrival_at": (departure_at + timedelta(hours=2)).isoformat(), "travel_hours": 2.0, "edges": []}
        else:
            try:
                plan = travel_plan(
                    world_seed=world_seed, start_at=departure_at, start=source_place, end=target_place, mode="foot",
                )
            except (KeyError, ValueError):
                return {"result": "rescue_route_unavailable"}

        blocked = unavailable_person_refs()
        available = [
            p for p in usable_martial_people(responder_roster, exclude_committed=blocked)
            if at.year - int(p.get("birth_year", at.year)) >= 16
            and not bool(p.get("retired_from_field", False))
            and _person_place(p, local_sites=local_sites, home_place=source_place, home_site_ref=str(responder.get("local_site_ref") or "")) == source_place
        ]
        kin_refs = set(close_kin_refs(family_state, captive_ref))
        autonomy = responder.get("autonomy_policy", {}) if isinstance(responder.get("autonomy_policy"), Mapping) else {}
        risk = max(0, min(100, int(autonomy.get("risk_tolerance", 50))))
        # A ransom is relevant only if a real later demand has been made. Capture
        # itself does not create a ransom or a payment obligation.
        ransom = max(0, int(custody_record.get("ransom_demand_cash", 0)))
        try:
            _ofid, _opath, _oowner, _oordinal, captive = load_person_ref(captive_ref)
            captive_value = principal_ransom_value_cash(captive)
        except (KeyError, FileNotFoundError, TypeError, ValueError):
            captive_value = ransom
        desired = rescue_force_size(
            available_count=len(available), captive_value_cash=max(captive_value, ransom),
            close_kin_count=len(kin_refs), risk_tolerance=risk,
        )
        if desired <= 0:
            return {"result": "rescue_force_unavailable"}
        available.sort(key=lambda p: (
            0 if str(p.get("person_id") or "") in kin_refs else 1,
            -person_combat_index(p), str(p.get("person_id") or ""),
        ))
        selected = available[:desired]
        participants = [str(p["person_id"]) for p in selected if isinstance(p.get("person_id"), str)]
        if not participants:
            return {"result": "rescue_force_unavailable"}

        # Institutions can estimate a known faction's scale/training but do not
        # receive its exact live roster or exact combat ratings. Deterministic
        # estimation error represents incomplete intelligence.
        # Use the current faction owner, not launch-seed metadata. Runtime-created
        # factions participate in rescue intelligence and can change training/size.
        _holder_path, holder_profile = load_faction(holder_fid)
        training = holder_profile.get("training", {}) if isinstance(holder_profile.get("training"), Mapping) else {}
        martial_keys = ("sword", "spear", "bow", "unarmed", "hidden_weapons", "qi", "qi_control")
        authored_training = [max(0, int(training.get(key, 0))) for key in martial_keys if key in training]
        avg_training = sum(authored_training) // len(authored_training) if authored_training else 35
        authored_population = max(1, int(holder_profile.get("exact_population", 30)))
        estimated_guard_count = max(2, 2 + authored_population // 20)
        estimated_guard_index = max(20, 20 + avg_training * 3 // 4)
        intel_roll = stable_permille("custody-rescue-defense-estimate", responder_fid, holder_fid, custody_id)
        intel_error_milli = 850 + intel_roll * 300 // 999
        defender_power = max(1, estimated_guard_count * estimated_guard_index * intel_error_milli // 1000)
        rescue_power = sum(person_combat_index(p) for p in selected)
        if not should_launch_rescue(
            rescue_power=rescue_power, estimated_defender_power=defender_power,
            captive_value_cash=max(captive_value, ransom), close_kin_count=len(kin_refs),
            ransom_cash=ransom, treasury_cash=max(0, int(responder.get("treasury_cash", 0))),
            risk_tolerance=risk,
        ):
            return {
                "result": "rescue_risk_rejected", "rescue_power": rescue_power,
                "estimated_defender_power": defender_power,
            }

        travel_days = max(1, (int(float(plan.get("travel_hours", 0)) * 1000) + 23999) // 24000)
        estimated_food_need = len(participants) * travel_days
        food_before = max(0, int(responder_inventory.get("food_ration_days", 0)))
        if food_before < estimated_food_need:
            return {"result": "rescue_provisions_unavailable", "food_need": estimated_food_need}

        deployments = deployments_state.setdefault("deployments", {})
        if not isinstance(deployments, dict):
            raise ValueError("jianghu deployments invalid")
        op_token = hashlib.sha256((custody_id + "|" + responder_fid).encode("utf-8")).hexdigest()[:20]
        op_ref = f"operation:custody_rescue:{op_token}"
        if op_ref in deployments:
            return {"result": "rescue_already_active", "operation_ref": op_ref}
        try:
            commitments_state = reserve_resources(
                commitments_state,
                resources=[("person", ref, responder_fid) for ref in participants],
                actor_ref=participants[0], owner_ref=responder_fid, activity_ref=op_ref,
                activity_kind="custody_rescue", started_at=at_iso, location_ref=source_place,
            )
        except ValueError:
            return {"result": "rescue_force_became_unavailable"}
        deployments[op_ref] = {
            "faction_ref": responder_fid, "target_faction_ref": holder_fid,
            "operation_kind": "custody_rescue", "participant_refs": participants,
            "source_place_ref": source_place, "target_place_ref": target_place,
            "target_site_ref": target_site, "custody_id": custody_id, "captive_ref": captive_ref,
            "started_at": at_iso, "departure_at": departure_at.isoformat(),
            "arrival_at": str(plan.get("arrival_at")), "travel_hours": float(plan.get("travel_hours", 0)),
            "route_refs": list(plan.get("edges", [])), "status": "mobilizing",
            "arrival_event_kind": "faction_operation_arrival",
            "targeting_intent": "disable",
        }
        pause_people_for_commitment(responder_fid, participants)
        writes[_DEPLOYMENTS_PATH] = deployments_state
        writes[ripath] = responder_inventory; inventory_cache[responder_fid] = (ripath, responder_inventory)
        pending_one_off_events.append({
            "event_id": f"operation_departure:{op_ref}", "kind": "faction_operation_departure",
            "due_at": departure_at.isoformat(), "owner_ref": op_ref, "direction": "outbound",
            "arrival_event_kind": "faction_operation_arrival", "requires_player_decision": False,
        })
        return {
            "result": "rescue_dispatched", "operation_ref": op_ref, "participant_refs": participants,
            "close_kin_refs": [ref for ref in participants if ref in kin_refs],
            "target_faction_ref": holder_fid, "captive_ref": captive_ref,
            "estimated_defender_power": defender_power,
        }

    def start_autonomous_investment(fid: str, intent: Mapping[str, Any]) -> dict[str, Any]:
        nonlocal commitments_state
        kind = str(intent.get("kind") or "")
        if kind not in {"expand_building", "upgrade_building", "expand_enterprise"}:
            return {"result": "no_investment_intent"}
        fpath, faction = load_faction(fid); ipath, inventory = load_inventory(fid); rpath, roster = load_roster(fid)
        # Project starts are transactional. Requirement/labor failures must not
        # mutate the cached live faction merely because a quote function already
        # deducted cash or material on a temporary after-image.
        starting_cash = max(0, int(faction.get("treasury_cash", 0)))
        faction = copy.deepcopy(dict(faction)); inventory = copy.deepcopy(dict(inventory))
        local_region = place_region.get(str(faction.get("headquarters") or ""))
        if not isinstance(local_region, str):
            return {"result": "project_market_unavailable"}
        try:
            mpath, cached_project_market = load_market(local_region)
        except FileNotFoundError:
            return {"result": "project_market_unavailable"}
        project_market = copy.deepcopy(dict(cached_project_market))
        material_cash_spent = 0
        registry = projects_state.setdefault("projects", {})
        if not isinstance(registry, dict):
            raise ValueError("jianghu projects invalid")
        if any(isinstance(row, Mapping) and row.get("faction_ref") == fid and not row.get("completed") for row in registry.values()):
            return {"result": "project_already_active"}
        available = [
            p for p in usable_martial_people(roster, exclude_committed=unavailable_person_refs())
            if not bool(p.get("retired_from_field", False))
            and at.year - int(p.get("birth_year", at.year)) >= 16
        ]
        if len(available) < 2:
            return {"result": "project_labor_unavailable"}
        skilled_sorted = sorted(
            available,
            key=lambda p: (-max(int((p.get("professional_skills") or {}).get("crafting", 0)), int((p.get("professional_skills") or {}).get("administration", 0))), str(p.get("person_id", ""))),
        )
        skill = max(
            (max(int((p.get("professional_skills") or {}).get("crafting", 0)), int((p.get("professional_skills") or {}).get("administration", 0))) for p in skilled_sorted),
            default=0,
        )
        cash_before_project_start = max(0, int(faction.get("treasury_cash", 0)))
        try:
            if kind == "expand_building":
                btype = str(intent.get("building_type") or "")
                added = max(1, int(intent.get("additional_footprint_m2", 0)))
                current_level = max(0, int((faction.get("buildings") or {}).get(btype, 0)))
                requirements = building_expansion_requirements(
                    btype, current_level=current_level, additional_footprint_m2=added,
                )
                procured = procure_project_materials(
                    faction, inventory, project_market, region_id=local_region,
                    required_materials=requirements.get("materials", {}),
                )
                faction = procured["faction"]; inventory = procured["inventory"]; project_market = procured["market"]
                material_cash_spent += int(procured.get("cash_spent", 0))
                cash_before_project_start = max(0, int(faction.get("treasury_cash", 0)))
                out = start_building_expansion(
                    treasury_cash=int(faction.get("treasury_cash", 0)), material_stock=inventory.get("raw_materials", {}),
                    buildings=faction.get("buildings", {}), infrastructure=faction.get("infrastructure", {}),
                    building_type=btype, additional_footprint_m2=added, crafting_or_administration=skill,
                )
                faction["treasury_cash"] = int(out["treasury_cash_after_start"]); inventory["raw_materials"] = out["material_stock_after_start"]
            elif kind == "upgrade_building":
                btype = str(intent.get("building_type") or ""); current = max(0, int((faction.get("buildings") or {}).get(btype, 0))); target = int(intent.get("target_level", 0))
                requirements = building_upgrade_requirements(btype, target)
                procured = procure_project_materials(
                    faction, inventory, project_market, region_id=local_region,
                    required_materials=requirements.get("materials", {}),
                )
                faction = procured["faction"]; inventory = procured["inventory"]; project_market = procured["market"]
                material_cash_spent += int(procured.get("cash_spent", 0))
                cash_before_project_start = max(0, int(faction.get("treasury_cash", 0)))
                out = start_building_upgrade(
                    treasury_cash=int(faction.get("treasury_cash", 0)), material_stock=inventory.get("raw_materials", {}),
                    building_type=btype, current_level=current, target_level=target, crafting_or_administration=skill,
                )
                faction["treasury_cash"] = int(out["treasury_cash_after_start"]); inventory["raw_materials"] = out["material_stock_after_start"]
            else:
                etype = str(intent.get("enterprise_type") or ""); added = max(1, int(intent.get("additional_scale", 1))); current_level = max(0, int((faction.get("enterprises") or {}).get(etype, 0)))
                if current_level <= 0:
                    return {"result": "enterprise_not_operating"}
                basis = enterprise_scale_basis(etype); current_row = (faction.get("enterprise_scale") or {}).get(etype, {}) if isinstance(faction.get("enterprise_scale"), Mapping) else {}; current_value = max(0, int(current_row.get(basis, 0))) if isinstance(current_row, Mapping) else 0
                target_value = current_value + added
                if etype == "agriculture_landholding":
                    holdings = faction.get("holdings", {}) if isinstance(faction.get("holdings"), Mapping) else {}
                    if target_value > max(0, int(holdings.get("rural_land_mu", 0))): return {"result": "physical_scale_limit"}
                elif etype == "crafting_workshop":
                    if target_value > max(0, int(workshop_capacity(faction.get("buildings", {}), faction.get("infrastructure", {})).get("craft_workstations", 0))): return {"result": "physical_scale_limit"}
                elif etype == "medicine_apothecary":
                    if target_value > max(0, int(infirmary_capacity(faction.get("buildings", {}), faction.get("infrastructure", {})).get("apothecary_workstations", 0))): return {"result": "physical_scale_limit"}
                elif etype == "escort_service":
                    transport = transport_yard_capacity(faction.get("buildings", {}), faction.get("infrastructure", {}))
                    people_cap = len(usable_martial_people(roster, exclude_committed=unavailable_person_refs())) // 4
                    transport_cap = max(0, int(transport.get("mount_or_pack_slots", 0))) // 4 + max(0, int(transport.get("wagon_slots", 0))) // 2
                    if target_value > max(1, min(people_cap, transport_cap)): return {"result": "physical_scale_limit"}
                out = start_enterprise_scale_expansion(
                    treasury_cash=int(faction.get("treasury_cash", 0)), enterprise_type=etype,
                    current_level=current_level, additional_scale=added,
                )
                faction["treasury_cash"] = int(out["treasury_cash_after_start"])
        except (KeyError, TypeError, ValueError):
            return {"result": "project_requirements_not_met"}

        min_days = max(1, int(out.get("minimum_calendar_days", 1)))
        if out.get("project_type") in {"building_upgrade", "building_expansion"}:
            skilled_need = max(1, (int(out.get("skilled_labor_hours_remaining", 0)) + 6 * min_days - 1) // (6 * min_days))
            general_need = max(1, (int(out.get("general_labor_hours_remaining", 0)) + 8 * min_days - 1) // (8 * min_days))
            skilled = skilled_sorted[:skilled_need]; skilled_ids = {str(p["person_id"]) for p in skilled}
            general = [p for p in sorted(available, key=lambda p: str(p.get("person_id", ""))) if str(p.get("person_id")) not in skilled_ids][:general_need]
            if not skilled or not general: return {"result": "project_labor_unavailable"}
            out["skilled_worker_refs"] = [str(p["person_id"]) for p in skilled]; out["general_worker_refs"] = [str(p["person_id"]) for p in general]
            days_needed = max(min_days, (int(out.get("skilled_labor_hours_remaining", 0)) + 6 * len(skilled) - 1) // max(1, 6 * len(skilled)), (int(out.get("general_labor_hours_remaining", 0)) + 8 * len(general) - 1) // max(1, 8 * len(general)))
        else:
            managers = sorted(available, key=lambda p: (-max(int((p.get("professional_skills") or {}).get("administration", 0)), int((p.get("professional_skills") or {}).get("commerce", 0))), str(p.get("person_id", ""))))
            management_need = max(1, (int(out.get("management_labor_hours_remaining", 0)) + 4 * min_days - 1) // (4 * min_days)); managers = managers[:management_need]
            mids = {str(p["person_id"]) for p in managers}; general_need = max(1, (int(out.get("general_setup_labor_hours_remaining", 0)) + 4 * min_days - 1) // (4 * min_days)); general = [p for p in sorted(available, key=lambda p: str(p.get("person_id", ""))) if str(p.get("person_id")) not in mids][:general_need]
            if not managers or not general: return {"result": "project_labor_unavailable"}
            out["management_worker_refs"] = [str(p["person_id"]) for p in managers]; out["general_worker_refs"] = [str(p["person_id"]) for p in general]
            days_needed = max(min_days, (int(out.get("management_labor_hours_remaining", 0)) + 4 * len(managers) - 1) // max(1, 4 * len(managers)), (int(out.get("general_setup_labor_hours_remaining", 0)) + 4 * len(general) - 1) // max(1, 4 * len(general)))
        worker_refs = list(dict.fromkeys(out.get("skilled_worker_refs", []) + out.get("management_worker_refs", []) + out.get("general_worker_refs", [])))
        # Investment is justified by operating reserves, so the quote must not
        # consume those same reserves.  Keep at least the faction's configured
        # reserve target, bounded to 4-8 months, after materials and setup cash.
        transport_assets = inventory.get("transport_capacity", {}) if isinstance(inventory.get("transport_capacity"), Mapping) else {}
        project_upkeep = monthly_upkeep_quote(
            faction, rider_capacity_slots=max(0, int(transport_assets.get("rider_slots", 0))),
            freight_capacity_kg=max(0, int(transport_assets.get("freight_capacity_kg", 0))),
        )
        roster_people = roster.get("people", []) if isinstance(roster, Mapping) else []
        project_stipends = sum(
            monthly_stipend(person) for person in roster_people
            if isinstance(person, Mapping) and not (isinstance(person.get("health"), Mapping) and person.get("health", {}).get("status") == "dead")
        ) if isinstance(roster_people, list) else 0
        policy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
        reserve_months = max(4, min(8, int(policy.get("reserve_cash_months", 6))))
        reserve_floor_cash = max(1, int(project_upkeep.get("total_cash", 0)) + project_stipends) * reserve_months
        if max(0, int(faction.get("treasury_cash", 0))) < reserve_floor_cash:
            return {"result": "project_would_breach_cash_reserve", "reserve_floor_cash": reserve_floor_cash}
        worker_refs = list(dict.fromkeys(worker_refs))
        project_ref = f"project:auto:{fid}:{at.year:04d}{at.month:02d}:{kind}"
        project_site = str(faction.get("local_site_ref") or faction.get("headquarters") or "")
        if not project_site:
            return {"result": "project_site_unresolved"}
        out["planned_skilled_worker_count"] = len(out.get("skilled_worker_refs", []))
        out["planned_management_worker_count"] = len(out.get("management_worker_refs", []))
        out["planned_general_worker_count"] = len(out.get("general_worker_refs", []))
        out.update({"project_ref": project_ref, "faction_ref": fid, "site_ref": project_site, "started_at": at_iso, "last_progress_at": at_iso})
        try:
            commitments_after = reserve_resources(
                commitments_state, resources=[("person", ref, fid) for ref in worker_refs], actor_ref=worker_refs[0], owner_ref=fid,
                activity_ref=project_ref, activity_kind="construction" if out.get("project_type") in {"building_upgrade", "building_expansion"} else "enterprise_setup",
                started_at=at_iso, location_ref=project_site,
            )
        except ValueError:
            return {"result": "project_labor_unavailable"}
        # Missing physical inputs were already bought through execute_purchase,
        # which moved their cash into this market.  Only project overhead still
        # needs an explicit conserved transfer to the surrounding economy.
        overhead_cash_spent = max(0, cash_before_project_start - max(0, int(faction.get("treasury_cash", 0))))
        if overhead_cash_spent > 0:
            project_market["cash_pool"] = max(0, int(project_market.get("cash_pool", 0))) + overhead_cash_spent
        commitments_state = commitments_after
        if material_cash_spent > 0 or overhead_cash_spent > 0:
            writes[mpath] = project_market; market_cache[local_region] = (mpath, project_market)
        registry[project_ref] = compact_project_state(out, project_ref=project_ref)
        pause_people_for_commitment(fid, worker_refs)
        writes[_PROJECTS_PATH] = projects_state; writes[fpath] = faction; writes[ipath] = inventory
        faction_cache[fid] = (fpath, faction); inventory_cache[fid] = (ipath, inventory)
        due = at + timedelta(days=max(1, int(days_needed)))
        pending_one_off_events.append({"event_id": f"autonomous_project_due:{project_ref}", "kind": "autonomous_project_due", "due_at": due.isoformat(), "owner_ref": project_ref, "requires_player_decision": False})
        return {
            "result": "project_started", "project_ref": project_ref,
            "project_type": out.get("project_type"), "minimum_calendar_days": int(days_needed),
            "material_purchase_cash": material_cash_spent,
            "project_overhead_cash": overhead_cash_spent,
            "total_cash_spent": max(0, starting_cash - max(0, int(faction.get("treasury_cash", 0)))),
        }

    def execute_friendly_aid(fid: str, target_fid: str) -> dict[str, Any]:
        if not target_fid or target_fid == fid:
            return {"result": "no_aid_target"}
        if fid == "house_tang" or target_fid == "house_tang":
            return {"result": "player_faction_diplomacy_protected"}
        try:
            fpath, faction = load_faction(fid); tfpath, target = load_faction(target_fid)
            _ipath, inventory = load_inventory(fid); _tipath, target_inventory = load_inventory(target_fid)
        except (KeyError, FileNotFoundError, ValueError):
            return {"result": "aid_target_unresolved"}
        transfer = resolve_friendly_aid_transfer(faction, inventory, target, target_inventory)
        if transfer.get("result") != "aid_transferred":
            return transfer
        faction = transfer.pop("source_after"); target = transfer.pop("target_after")
        faction_cache[fid] = (fpath, faction); faction_cache[target_fid] = (tfpath, target)
        writes[fpath] = faction; writes[tfpath] = target
        owed_before = max(0, int((directed_relation(fid, target_fid) or {}).get("obligation", 0)))
        obligation_repaid = settle_directed_obligation(fid, target_fid, 12) if owed_before > 0 else 0
        if owed_before > 0:
            apply_directed_relation_event(fid, target_fid, "silver_obligation_repaid")
            apply_directed_relation_event(target_fid, fid, "silver_obligation_repayment_received")
        else:
            apply_directed_relation_event(fid, target_fid, "silver_aid_given")
            apply_directed_relation_event(target_fid, fid, "silver_aid_received")
        return {**transfer, "target_faction_ref": target_fid, "obligation_repaid": obligation_repaid}

    def start_monthly_merchant_trade(fid: str) -> dict[str, Any]:
        """Start one conserved cross-region merchant caravan when a real spread exists.

        The trade enterprise supplies organizational capacity only.  The actual
        shipment spends faction silver into a finite source market, commits real
        members, consumes round-trip provisions and tolls, travels on a registered
        route, then sells against finite destination stock/cash before returning.
        """
        nonlocal commitments_state
        try:
            fpath, faction = load_faction(fid); ipath, inventory = load_inventory(fid); rpath, roster = load_roster(fid)
        except (KeyError, FileNotFoundError, ValueError):
            return {"result": "trade_owner_unresolved"}
        enterprises = faction.get("enterprises", {}) if isinstance(faction.get("enterprises"), Mapping) else {}
        level = max(0, int(enterprises.get("trade_merchant_business", 0)))
        if level <= 0:
            return {"result": "no_trade_enterprise"}
        scale = enterprise_scale_value(faction, "trade_merchant_business")
        efficiency = enterprise_operating_efficiency_milli("trade_merchant_business", level)
        if scale <= 0 or efficiency <= 0:
            return {"result": "no_trade_enterprise_capacity"}
        movements = route_ops_state.setdefault("movements", {})
        if not isinstance(movements, dict):
            raise ValueError("jianghu route movement state invalid")
        if any(
            isinstance(row, Mapping) and row.get("movement_kind") == "merchant_trade"
            and row.get("beneficiary_ref") == fid and row.get("status") not in {"completed", "cancelled"}
            for row in movements.values()
        ):
            return {"result": "merchant_caravan_already_active"}

        source_place = str(faction.get("headquarters") or "")
        source_region = place_region.get(source_place)
        if not source_place or not isinstance(source_region, str):
            return {"result": "trade_origin_unresolved"}
        transport = inventory.get("transport_capacity", {}) if isinstance(inventory.get("transport_capacity"), Mapping) else {}
        available_transport = faction_available_capacity(inventory, route_ops_state, faction_ref=fid)
        available_freight_kg = max(0, int(available_transport.get("freight_capacity_kg", 0)))
        if available_freight_kg <= 0:
            return {"result": "no_freight_transport_capacity"}
        try:
            smpath, source_market = load_market(source_region)
        except FileNotFoundError:
            return {"result": "source_market_unavailable"}

        policy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
        transport_quote = monthly_upkeep_quote(
            faction,
            rider_capacity_slots=max(0, int(transport.get("rider_slots", 0))),
            freight_capacity_kg=max(0, int(transport.get("freight_capacity_kg", 0))),
        )
        reserve_months = max(2, int(policy.get("reserve_cash_months", 6)))
        reserve_floor = max(1, int(transport_quote.get("total_cash", 1))) * reserve_months
        treasury = max(0, int(faction.get("treasury_cash", 0)))
        base_trade_milli=trade_capital_milli(at,river_route=False,review_window_days=30)
        monthly_cap = max(0, scale * efficiency // 1000) * base_trade_milli // 1000
        spendable = min(monthly_cap, max(0, treasury - reserve_floor))
        if spendable <= 0:
            return {"result": "trade_capital_reserved"}

        # Only direct registered edges are background merchant routes.  Longer
        # multi-edge expeditions can be represented later as chained route owners
        # without inventing teleportation or a second route simulator.
        candidates: list[dict[str, Any]] = []
        for route_id, route in sorted(route_index.items()):
            if not isinstance(route, Mapping) or str(route.get("status", "open")) != "open":
                continue
            ends = (str(route.get("from") or ""), str(route.get("to") or ""))
            if source_place not in ends:
                continue
            if "convoy" not in [str(x) for x in route.get("allowed_modes", []) if isinstance(x, str)]:
                continue
            destination_place = ends[1] if ends[0] == source_place else ends[0]
            destination_region = place_region.get(destination_place)
            if not isinstance(destination_region, str) or destination_region == source_region:
                continue
            try:
                dmpath, destination_market = load_market(destination_region)
            except FileNotFoundError:
                continue
            opportunities = trade_shipment_opportunities(
                market_states={source_region: source_market, destination_region: destination_market},
                route_rows=[route], place_to_region=place_region,
            )
            for opp in opportunities:
                if not isinstance(opp, Mapping) or opp.get("source_region") != source_region or opp.get("destination_region") != destination_region:
                    continue
                item_ref = str(opp.get("item_ref") or ""); available_qty = max(0, int(opp.get("quantity", 0)))
                if not item_ref or available_qty <= 0:
                    continue
                try:
                    buy_unit = unit_market_price_cash(source_region, item_ref, source_market.get("stock", {}))
                    sell_unit = int(quote_sale(destination_region, item_ref, 1, destination_market)["unit_price_cash"])
                except (KeyError, TypeError, ValueError):
                    continue
                if sell_unit <= buy_unit:
                    continue
                try:
                    candidate_plan = travel_plan(
                        world_seed=world_seed, start_at=at, start=source_place,
                        end=destination_place, mode="convoy",
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                required_seconds = planned_journey_seconds(candidate_plan)
                if required_seconds <= 0:
                    continue
                # Profitability conservatively reserves expected toll in both
                # directions, while the physical movement uses this exact
                # weather/terrain plan for the outbound leg.
                toll_cash = max(0, int(candidate_plan.get("toll_cash", 0))) * 2
                route_trade_milli=trade_capital_milli(at,river_route=(str(route.get("terrain") or "")=="river_plain"),review_window_days=30)
                route_spendable=spendable * route_trade_milli // max(1,base_trade_milli)
                capital_after_toll = max(0, route_spendable - toll_cash)
                if capital_after_toll < buy_unit:
                    continue
                destination_cash = max(0, int(destination_market.get("cash_pool", 0)))
                max_by_dest_cash = destination_cash // max(1, sell_unit)
                # One pack animal can support a bounded aggregate shipment. The
                # item unit is already the economy's registered physical unit;
                # this cap exists to stop a tiny faction moving a regional stock
                # pile merely because it has enough silver.
                try:
                    unit_mass_grams = cargo_unit_mass_grams(item_ref)
                except KeyError:
                    continue
                max_by_transport = max(0, available_freight_kg * 1000 // max(1, unit_mass_grams))
                quantity = min(available_qty, capital_after_toll // buy_unit, max_by_dest_cash, max_by_transport)
                if quantity <= 0:
                    continue
                purchase_cash = buy_unit * quantity
                expected_sale = sell_unit * quantity
                expected_profit = expected_sale - purchase_cash - toll_cash
                if expected_profit <= max(50, purchase_cash * 20 // 1000):
                    continue
                candidates.append({
                    "route_ref": str(route_id), "destination_place_ref": destination_place,
                    "destination_region": destination_region, "destination_market_path": dmpath,
                    "item_ref": item_ref, "quantity": quantity, "buy_unit": buy_unit, "sell_unit": sell_unit,
                    "purchase_cash": purchase_cash, "expected_sale_cash": expected_sale,
                    "expected_profit_cash": expected_profit, "toll_cash": toll_cash,
                    "freight_capacity_kg": (quantity * unit_mass_grams + 999) // 1000,
                    "required_seconds": required_seconds,
                    "travel_plan": candidate_plan,
                })
        if not candidates:
            return {"result": "no_profitable_cross_region_trade"}
        candidates.sort(key=lambda row: (-int(row["expected_profit_cash"]), int(row["required_seconds"]), str(row["route_ref"]), str(row["item_ref"])))
        chosen = candidates[0]

        blocked = unavailable_person_refs()
        people = [
            p for p in usable_martial_people(roster, exclude_committed=blocked)
            if _person_place(p, local_sites=local_sites, home_place=source_place, home_site_ref=str(faction.get("local_site_ref") or "")) == source_place and str(p.get("person_id") or "") != player_ref
            and at.year - int(p.get("birth_year", at.year)) >= 16 and not bool(p.get("retired_from_field", False))
        ]
        people.sort(key=lambda p: (
            -int((p.get("professional_skills") or {}).get("commerce", 0)) if isinstance(p.get("professional_skills"), Mapping) else 0,
            -person_combat_index(p), str(p.get("person_id", "")),
        ))
        desired_people = max(1, 1 + level // 2)
        participants = [str(p["person_id"]) for p in people[:desired_people] if isinstance(p.get("person_id"), str)]
        if not participants:
            return {"result": "no_available_trade_staff"}
        merchant_plan = chosen.get("travel_plan") if isinstance(chosen.get("travel_plan"), Mapping) else None
        if not isinstance(merchant_plan, Mapping):
            return {"result": "trade_route_plan_missing"}
        try:
            inventory, provision_reservation = reserve_faction_rations(
                inventory, faction_ref=fid, participant_count=len(participants),
                travel_seconds=provisioning_journey_seconds(merchant_plan),
            )
        except ValueError:
            return {"result": "insufficient_trade_provisions"}

        route_ref = str(chosen["route_ref"]); item_ref = str(chosen["item_ref"]); quantity = int(chosen["quantity"])
        movement_ref = f"merchant_trade:{fid}:{route_ref}:{at.year:04d}{at.month:02d}"
        try:
            purchased = execute_purchase(
                source_region, item_ref, quantity, source_market,
                buyer_cash=treasury,
            )
        except (KeyError, TypeError, ValueError):
            return {"result": "trade_purchase_failed"}
        toll_cash = int(chosen["toll_cash"])
        if int(purchased["buyer_cash_after"]) < toll_cash:
            return {"result": "trade_toll_unaffordable"}
        try:
            commitments_state = reserve_resources(
                commitments_state,
                resources=[("person", ref, fid) for ref in participants],
                actor_ref=participants[0], owner_ref=fid, activity_ref=movement_ref,
                activity_kind="merchant_trade", started_at=at_iso, location_ref=source_place,
            )
        except ValueError:
            return {"result": "trade_resources_unavailable"}
        source_market = copy.deepcopy(dict(purchased["market_state_after"]))
        faction["treasury_cash"] = int(purchased["buyer_cash_after"]) - toll_cash
        # Route tolls remain tracked currency. Split round-trip tolls between
        # the two surrounding market authorities rather than deleting silver.
        if toll_cash > 0:
            source_toll = toll_cash // 2; destination_toll = toll_cash - source_toll
            source_market["cash_pool"] = max(0, int(source_market.get("cash_pool", 0))) + source_toll
            dmpath, destination_market = load_market(str(chosen["destination_region"]))
            destination_market["cash_pool"] = max(0, int(destination_market.get("cash_pool", 0))) + destination_toll
            writes[dmpath] = destination_market; market_cache[str(chosen["destination_region"])] = (dmpath, destination_market)
        movements[movement_ref] = build_route_journey(
            movement_ref=movement_ref, movement_kind="merchant_trade", purpose_ref=movement_ref,
            plan=merchant_plan, participants=participants, leader_ref=participants[0],
            beneficiary_ref=fid, started_at=at, mode="convoy",
            extra={
                "item_ref": item_ref, "quantity": quantity, "trade_leg": "outbound",
                "provision_reservation": provision_reservation,
                "transport_reservation": make_transport_reservation(
                    provider_kind="faction_pool", provider_ref=fid,
                    freight_capacity_kg=max(1, int(chosen.get("freight_capacity_kg", 0))),
                ),
            },
        )
        pause_people_for_commitment(fid, participants)
        writes[fpath] = faction; faction_cache[fid] = (fpath, faction)
        writes[ipath] = inventory; inventory_cache[fid] = (ipath, inventory)
        writes[smpath] = source_market; market_cache[source_region] = (smpath, source_market); writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
        return {
            "result": "merchant_trade_started", "movement_ref": movement_ref, "route_ref": route_ref,
            "destination_region": str(chosen["destination_region"]), "item_ref": item_ref, "quantity": quantity,
            "purchase_cash": int(chosen["purchase_cash"]), "expected_profit_cash": int(chosen["expected_profit_cash"]),
        }

    pending_training_resume_refs: set[str] = set()

    def pause_people_for_commitment(fid: str, person_refs: Sequence[str]) -> None:
        refs = {str(x) for x in person_refs if isinstance(x, str)}
        if not refs:
            return
        fpath, faction = load_faction(fid)
        rpath, roster = load_roster(fid)
        # Materialize the whole faction before changing who is physically
        # available to teach/train. Otherwise a departing instructor could
        # retroactively disappear from the already-elapsed epoch.
        faction, roster, _summary = settle_and_reset_faction_training_cycle(
            faction, roster, at_iso=at_iso, paused_refs=sorted(unavailable_person_refs()),
        )
        people = roster.get("people", []) if isinstance(roster, Mapping) else []
        if not isinstance(people, list):
            return
        after_people: list[Any] = []
        for raw in people:
            if not isinstance(raw, Mapping) or raw.get("person_id") not in refs:
                after_people.append(raw)
                continue
            person = copy.deepcopy(dict(raw))
            ts = copy.deepcopy(dict(person.get("training_state", {}))) if isinstance(person.get("training_state"), Mapping) else {}
            ts["institutional_paused"] = True
            person["training_state"] = ts
            after_people.append(person)
        roster["people"] = after_people
        writes[fpath] = faction
        writes[rpath] = roster
        faction_cache[fid] = (fpath, faction)
        roster_cache[fid] = (rpath, roster)

    def person_combat_index(person: Mapping[str, Any]) -> int:
        profile = capability_from_person(person)
        return max(1, (int(profile.offense) + int(profile.defense) + int(profile.control) + int(profile.mobility)) // 4)

    # Exact identity routing is RAM-only.  Production repositories reuse the
    # shared metadata-keyed transient index in ``live_state``; lightweight tools
    # without a repository root fall back to one compact roster scan per frontier.
    # Neither path persists routing state.
    class _FrontierPersonView:
        def __init__(self) -> None:
            # Preserve the underlying repository chain instead of copying its
            # ``root`` onto this staged view.  ``live_state.person_route`` can
            # then reuse the base RAM route cache while still recognizing this
            # object as an overlay that must verify staged roster changes.
            self.repository = getattr(read_json, "__self__", None)
            self._read_json = read_json
        def read_json(self, path: str) -> Any:
            row = writes.get(path)
            if isinstance(row, Mapping):
                return row
            return self._read_json(path)

    frontier_person_view = _FrontierPersonView()
    fallback_person_owner_index: dict[str, tuple[str, str, int]] = {}
    fallback_person_index_built = False

    def rebuild_fallback_person_owner_index() -> None:
        nonlocal fallback_person_index_built
        fallback_person_owner_index.clear()
        for fid in all_faction_ids:
            rpath = roster_path(fid)
            try:
                roster = frontier_person_view.read_json(rpath)
            except (FileNotFoundError, ValueError):
                continue
            rows = roster.get("people", []) if isinstance(roster, Mapping) else []
            if not isinstance(rows, list):
                continue
            for ordinal, raw in enumerate(rows):
                ref = raw.get("person_id") if isinstance(raw, Mapping) else None
                if not isinstance(ref, str) or not ref:
                    continue
                if ref in fallback_person_owner_index:
                    raise ValueError(f"duplicate jianghu person identity: {ref}")
                fallback_person_owner_index[ref] = (fid, rpath, ordinal)
        fallback_person_index_built = True

    def _faction_person_route(person_ref: str) -> tuple[str, str, int] | None:
        nonlocal fallback_person_index_built
        try:
            fid, ordinal = routed_person_route(frontier_person_view, person_ref)
            return fid, roster_path(fid), ordinal
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            if not fallback_person_index_built:
                rebuild_fallback_person_owner_index()
            route = fallback_person_owner_index.get(person_ref)
            if route is None:
                return None
            fid, rpath, ordinal = route
            try:
                owner = frontier_person_view.read_json(rpath)
            except (FileNotFoundError, ValueError):
                owner = {}
            rows = owner.get("people", []) if isinstance(owner, Mapping) else []
            if not (isinstance(rows, list) and 0 <= ordinal < len(rows) and isinstance(rows[ordinal], Mapping) and rows[ordinal].get("person_id") == person_ref):
                # A staged roster may be compacted or reordered during this
                # frontier. Repair the stale ordinal by scanning only the known
                # owner roster before falling back to an expensive whole-world
                # rebuild. The person's faction route remains authoritative
                # unless the identity genuinely disappeared or changed owner.
                local_ordinal = next((
                    idx for idx, raw in enumerate(rows)
                    if isinstance(raw, Mapping) and raw.get("person_id") == person_ref
                ), None) if isinstance(rows, list) else None
                if local_ordinal is not None:
                    ordinal = int(local_ordinal)
                    fallback_person_owner_index[person_ref] = (fid, rpath, ordinal)
                else:
                    rebuild_fallback_person_owner_index()
                    route = fallback_person_owner_index.get(person_ref)
                    if route is None:
                        return None
                    fid, rpath, ordinal = route
            return fid, rpath, ordinal

    def load_person_ref(person_ref: str) -> tuple[str, str, dict[str, Any], int, dict[str, Any]]:
        route = _faction_person_route(person_ref)
        if route is not None:
            fid, _rpath, ordinal = route
            rpath, owner = load_roster(fid)
            rows = owner.get("people", []) if isinstance(owner, Mapping) else []
            if isinstance(rows, list) and 0 <= ordinal < len(rows):
                raw = rows[ordinal]
                if isinstance(raw, Mapping) and raw.get("person_id") == person_ref:
                    return fid, rpath, owner, ordinal, copy.deepcopy(dict(raw))
            # A staged compaction changed the ordinal after route lookup. Rebuild
            # the fallback current-view index and verify once against the owner.
            rebuild_fallback_person_owner_index()
            fallback = fallback_person_owner_index.get(person_ref)
            if fallback is not None:
                fid, _rpath, ordinal = fallback
                rpath, owner = load_roster(fid)
                rows = owner.get("people", []) if isinstance(owner, Mapping) else []
                if isinstance(rows, list) and 0 <= ordinal < len(rows):
                    raw = rows[ordinal]
                    if isinstance(raw, Mapping) and raw.get("person_id") == person_ref:
                        return fid, rpath, owner, ordinal, copy.deepcopy(dict(raw))
        for path, owner, hydrator in (
            (_INDEPENDENTS_PATH, independent_state, hydrate_independent_person),
            (_CIVIC_PEOPLE_PATH, civic_state, hydrate_civic_person),
        ):
            rows = owner.get("people", []) if isinstance(owner, Mapping) else []
            if not isinstance(rows, list):
                continue
            for ordinal, raw in enumerate(rows):
                if isinstance(raw, Mapping) and raw.get("person_id") == person_ref:
                    return "", path, owner, ordinal, hydrator(raw)
        raise KeyError(person_ref)

    def save_exact_person(person_ref: str, person: Mapping[str, Any]) -> None:
        """Persist one exact person's current body/location to its real owner."""
        fid, path, owner, ordinal, _current = load_person_ref(person_ref)
        if fid:
            rows = owner.get("people", []) if isinstance(owner, Mapping) else []
            if not isinstance(rows, list) or ordinal < 0 or ordinal >= len(rows):
                raise ValueError("jianghu roster person owner invalid")
            rows[ordinal] = copy.deepcopy(dict(person))
            writes[path] = owner
            roster_cache[fid] = (path, owner)
            return
        rows = owner.get("people", []) if isinstance(owner, Mapping) else []
        if not isinstance(rows, list) or ordinal < 0 or ordinal >= len(rows):
            raise ValueError("jianghu exact person owner invalid")
        if path == _INDEPENDENTS_PATH:
            rows[ordinal] = compact_independent_person(person)
            writes[path] = owner
        elif path == _CIVIC_PEOPLE_PATH:
            rows[ordinal] = compact_civic_person(person)
            writes[path] = owner
        else:
            raise ValueError("jianghu exact person owner unresolved")

    def move_exact_people_to_location(person_refs: Sequence[str], location_ref: str) -> None:
        if not location_ref:
            return
        for ref in [str(x) for x in person_refs if isinstance(x, str)]:
            try:
                _fid, _path, _owner, _ordinal, person = load_person_ref(ref)
            except (KeyError, ValueError, FileNotFoundError):
                continue
            person["location_ref"] = location_ref
            save_exact_person(ref, person)

    def move_exact_people(person_refs: Sequence[str], place_ref: str) -> None:
        if not place_ref:
            return
        destination = _arrival_site(local_sites, place_ref) or place_ref
        move_exact_people_to_location(person_refs, destination)

    def usable_martial_people(roster: Mapping[str, Any], *, exclude_committed: set[str] | None = None) -> list[dict[str, Any]]:
        blocked = exclude_committed or set()
        rows = roster.get("people", []) if isinstance(roster, Mapping) else []
        out: list[dict[str, Any]] = []
        if not isinstance(rows, list):
            return out
        for row in rows:
            if not isinstance(row, Mapping) or not isinstance(row.get("person_id"), str):
                continue
            ref = str(row["person_id"])
            if ref in blocked or not is_faction_member(row):
                continue
            health = row.get("health", {}) if isinstance(row.get("health"), Mapping) else {}
            if health.get("status") in {"dead", "incapacitated"} or int(health.get("consciousness", 100)) <= 0:
                continue
            out.append(copy.deepcopy(dict(row)))
        return out

    def resume_people_training(person_refs: Sequence[str]) -> None:
        """Resume training only for people with no remaining availability owner."""
        refs = {str(x) for x in person_refs if isinstance(x, str)}
        refs -= unavailable_person_refs()
        if not refs:
            return
        by_faction: dict[str, set[str]] = {}
        for ref in sorted(refs):
            try:
                fid, _rpath, _roster, _ordinal, _person = load_person_ref(ref)
            except (KeyError, ValueError, FileNotFoundError):
                continue
            # Independents have no faction owner and therefore no institutional
            # training program to resume.  This also makes old/disposable
            # checkpoints robust if a person became independent while an
            # earlier commitment was still active.
            if not fid:
                continue
            by_faction.setdefault(fid, set()).add(ref)
        for fid, local_refs in by_faction.items():
            fpath, faction = load_faction(fid)
            rpath, roster = load_roster(fid)
            # Resuming a person changes the environment for everyone. Settle
            # the old epoch while they are still paused, then unpause them only
            # for the new epoch that starts at this frontier.
            paused_through_release = institutional_training_pause_refs(
                faction, [p for p in roster.get("people", []) if isinstance(p, Mapping)],
                unavailable_refs=sorted(unavailable_person_refs() | local_refs),
            )
            faction, roster, _summary = settle_and_reset_faction_training_cycle(
                faction, roster, at_iso=at_iso, paused_refs=paused_through_release,
            )
            people = roster.get("people", []) if isinstance(roster, Mapping) else []
            if not isinstance(people, list):
                continue
            after_people: list[Any] = []
            for person in people:
                if not isinstance(person, Mapping) or person.get("person_id") not in local_refs:
                    after_people.append(person)
                    continue
                caught = copy.deepcopy(dict(person))
                ts = copy.deepcopy(dict(caught.get("training_state", {}))) if isinstance(caught.get("training_state"), Mapping) else {}
                ts.pop("institutional_paused", None)
                if ts:
                    caught["training_state"] = ts
                else:
                    caught.pop("training_state", None)
                after_people.append(caught)
            roster["people"] = after_people
            writes[fpath] = faction
            writes[rpath] = roster
            faction_cache[fid] = (fpath, faction)
            roster_cache[fid] = (rpath, roster)

    def settle_and_resume_people(person_refs: Sequence[str], *, activity_ref: str, commitments_state: Mapping[str, Any]) -> dict[str, Any]:
        """Release a finite commitment without awarding occupied training time."""
        refs = {str(x) for x in person_refs if isinstance(x, str)}
        released = release_resources(commitments_state, activity_ref=activity_ref)
        if not refs:
            return released
        # Evaluate the released after-image against every physical owner, not
        # only the finite-activity ledger.  A project/contract can finish while
        # the same person is still in exact combat, custody, or a live route;
        # releasing one commitment must never make that body institutionally
        # trainable until all remaining availability authorities clear.
        still_blocked = unavailable_person_refs(released)
        resumable = refs - still_blocked
        if resumable:
            by_faction: dict[str, set[str]] = {}
            for ref in sorted(resumable):
                try:
                    fid, _rpath, _roster, _ordinal, _person = load_person_ref(ref)
                except (KeyError, ValueError, FileNotFoundError):
                    continue
                if not fid:
                    continue
                by_faction.setdefault(fid, set()).add(ref)
            for fid, local_refs in by_faction.items():
                # Independents have no institutional training authority to
                # resume. This is a defensive invariant for old checkpoints
                # that may contain a route/contract commitment spanning a
                # historical faction departure. New departures exclude active
                # commitments above, but a released independent is still a
                # lawful current person and must never resolve as factions/.json.
                if not fid:
                    continue
                fpath, faction = load_faction(fid)
                rpath, roster = load_roster(fid)
                # Release changes the teaching/student set. Materialize the
                # previous epoch while these people are still paused, then
                # unpause them for the new epoch only.
                paused_through_release = institutional_training_pause_refs(
                    faction, [p for p in roster.get("people", []) if isinstance(p, Mapping)],
                    unavailable_refs=sorted(unavailable_person_refs(released) | local_refs),
                )
                faction, roster, _summary = settle_and_reset_faction_training_cycle(
                    faction, roster, at_iso=at_iso, paused_refs=paused_through_release,
                )
                people = roster.get("people", []) if isinstance(roster, Mapping) else []
                if not isinstance(people, list):
                    continue
                after_people: list[Any] = []
                for person in people:
                    if not isinstance(person, Mapping) or person.get("person_id") not in local_refs:
                        after_people.append(person)
                        continue
                    caught = copy.deepcopy(dict(person))
                    ts = copy.deepcopy(dict(caught.get("training_state", {}))) if isinstance(caught.get("training_state"), Mapping) else {}
                    ts.pop("institutional_paused", None)
                    if ts:
                        caught["training_state"] = ts
                    else:
                        caught.pop("training_state", None)
                    after_people.append(caught)
                roster["people"] = after_people
                writes[fpath] = faction
                writes[rpath] = roster
                faction_cache[fid] = (fpath, faction)
                roster_cache[fid] = (rpath, roster)
        return released

    frontier_closed_dead_refs: set[str] = set()

    def close_dead_current_authorities(dead_refs: Sequence[str]) -> None:
        """Close current authorities immediately for deaths at this frontier.

        Permanent kinship facts remain in family state, but current offices,
        courtships, custody and finite availability reservations cannot continue
        to treat a dead body as an active participant.
        """
        nonlocal family_state, commitments_state, custody_state, social_state
        nonlocal independent_state, civic_state, civilian_state
        nonlocal contract_index, active_contracts
        dead = {str(x) for x in dead_refs if isinstance(x, str)}
        if not dead:
            return
        frontier_closed_dead_refs.update(dead)

        # Death releases the deceased from every finite reservation without
        # erasing surviving people or non-person resources owned by that same
        # current activity. Empty person-only reservations disappear.
        rows = commitments_state.setdefault("commitments", {})
        person_index = commitments_state.setdefault("person_index", {})
        if not isinstance(rows, dict) or not isinstance(person_index, dict):
            raise ValueError("jianghu commitment state invalid")
        for cid, raw in list(rows.items()):
            if not isinstance(raw, Mapping):
                continue
            before_resources = raw.get("resources", []) if isinstance(raw.get("resources"), list) else []
            before_people = [str(x) for x in raw.get("person_refs", []) if isinstance(x, str)] if isinstance(raw.get("person_refs"), list) else []
            if not any(ref in dead for ref in before_people):
                continue
            row = copy.deepcopy(dict(raw))
            row["resources"] = [
                r for r in before_resources
                if not (isinstance(r, Mapping) and r.get("kind") == "person" and str(r.get("ref")) in dead)
            ]
            survivors = [ref for ref in before_people if ref not in dead]
            row["person_refs"] = survivors
            for ref in before_people:
                if ref in dead and person_index.get(ref) == cid:
                    person_index.pop(ref, None)
            had_person_resources = any(isinstance(r, Mapping) and r.get("kind") == "person" for r in before_resources)
            has_nonperson_resources = any(isinstance(r, Mapping) and r.get("kind") != "person" for r in row["resources"])
            if had_person_resources and not survivors and not has_nonperson_resources:
                rows.pop(cid, None)
            else:
                rows[cid] = row# Current courtship/social authorities end at death.
        courtships = social_state.get("courtships", {}) if isinstance(social_state, Mapping) else {}
        if isinstance(courtships, dict):
            for pair_ref in list(courtships):
                row = courtships.get(pair_ref); refs = row.get("person_refs", []) if isinstance(row, Mapping) else []
                if any(str(ref) in dead for ref in refs):
                    courtships.pop(pair_ref, None)
        relationships = social_state.get("relationships", {}) if isinstance(social_state, Mapping) else {}
        if isinstance(relationships, dict):
            for edge_ref in list(relationships):
                if any(part in dead for part in str(edge_ref).split("|", 1)):
                    relationships.pop(edge_ref, None)
        writes[_SOCIAL_PATH] = social_state

        # A dead detainee leaves custody.  A dead personal custodian does not
        # magically free a prisoner already held by an institution at its base:
        # another actually available local guard may assume custody.  If no such
        # guard exists, the physical restraint authority really has collapsed and
        # the living prisoner is released.
        prior_custody = [row for row in custody_state.get("records", []) if isinstance(row, Mapping)]
        next_custody: list[Mapping[str, Any]] = []
        released: set[str] = set()
        custody_changed = False
        for raw in prior_custody:
            prisoner_ref = str(raw.get("person_ref") or "")
            captor_ref = str(raw.get("captor_ref") or "")
            if prisoner_ref in dead:
                custody_changed = True
                continue
            if captor_ref not in dead:
                next_custody.append(raw)
                continue
            holder_fid = str(raw.get("holder_faction_ref") or "")
            replacement_ref = ""
            if holder_fid:
                try:
                    _hfpath, holder = load_faction(holder_fid)
                    _hrpath, holder_roster = load_roster(holder_fid)
                    home_place = str(holder.get("headquarters") or "")
                    home_site = str(holder.get("local_site_ref") or "")
                    custody_location = str(raw.get("location_ref") or "")

                    # While a prisoner is being carried home, custody can pass
                    # only to another surviving raider in that same physical
                    # party. A guard sitting at the hideout cannot assume control
                    # of a body that is still kilometers away on the road.
                    movement_rows = route_ops_state.get("movements", {}) if isinstance(route_ops_state, Mapping) else {}
                    travel_candidates: list[Mapping[str, Any]] = []
                    if isinstance(movement_rows, Mapping):
                        for movement in movement_rows.values():
                            if not isinstance(movement, Mapping):
                                continue
                            captive_refs = [str(x) for x in movement.get("captive_refs", []) if isinstance(x, str)]
                            if prisoner_ref not in captive_refs:
                                continue
                            for candidate_ref in route_controlling_refs(movement):
                                if candidate_ref in dead or candidate_ref == prisoner_ref:
                                    continue
                                try:
                                    candidate_owner, _cpath, _cowner, _cordinal, candidate = load_person_ref(candidate_ref)
                                except (KeyError, ValueError, FileNotFoundError):
                                    continue
                                health = candidate.get("health", {}) if isinstance(candidate.get("health"), Mapping) else {}
                                if candidate_owner == holder_fid and health.get("status") != "dead" and int(health.get("consciousness", 100)) > 0:
                                    travel_candidates.append(candidate)
                            break
                    travel_candidates.sort(key=lambda person: (-person_combat_index(person), str(person.get("person_id", ""))))
                    if travel_candidates:
                        replacement_ref = str(travel_candidates[0].get("person_id") or "")

                    # At a real holding site the institution can hand custody to
                    # another actually present available guard.
                    custody_site = site_rows.get(custody_location) if isinstance(site_rows, Mapping) else None
                    custody_place = str(custody_site.get("parent_place_ref") or "") if isinstance(custody_site, Mapping) else custody_location
                    if not replacement_ref and custody_place == home_place:
                        candidates = [
                            person for person in usable_martial_people(holder_roster, exclude_committed=unavailable_person_refs())
                            if str(person.get("person_id") or "") not in dead
                            and str(person.get("person_id") or "") != prisoner_ref
                            and _person_place(person, local_sites=local_sites, home_place=home_place, home_site_ref=home_site) == home_place
                        ]
                        candidates.sort(key=lambda person: (-person_combat_index(person), str(person.get("person_id", ""))))
                        if candidates:
                            replacement_ref = str(candidates[0].get("person_id") or "")
                except (KeyError, ValueError, FileNotFoundError):
                    replacement_ref = ""
            if replacement_ref:
                row = copy.deepcopy(dict(raw))
                row["captor_ref"] = replacement_ref
                next_custody.append(row)
                custody_changed = True
            else:
                released.add(prisoner_ref)
                custody_changed = True
        custody_state["records"] = next_custody
        if custody_changed:
            writes[_CUSTODY_PATH] = custody_state
            pending_training_resume_refs.update(released)

        # Death is global identity lifecycle, not faction-local cleanup. Resolve
        # every exact owner first so cross-faction spouses/children, civic
        # officials and independents follow the same family and estate rules.
        person_routes = exact_person_index(
            read_json=read_json, writes=writes, faction_refs=all_faction_ids,
        )
        living_people = {
            ref: route["person"] for ref, route in person_routes.items()
            if ref not in dead and isinstance(route.get("person"), Mapping) and is_living(route["person"])
        }
        family_state = close_family_authorities(
            family_state, dead_refs=sorted(dead), living_people=living_people,
        )
        writes[_FAMILY_PATH] = family_state

        by_faction: dict[str, set[str]] = {}
        dead_civic_rows: list[dict[str, Any]] = []
        for ref in sorted(dead):
            route = person_routes.get(ref)
            if not isinstance(route, Mapping):
                continue
            if route.get("owner_kind") == "faction":
                fid = str(route.get("owner_ref") or "")
                if fid:
                    by_faction.setdefault(fid, set()).add(ref)
            elif route.get("owner_kind") == "civic" and isinstance(route.get("person"), Mapping):
                dead_civic_rows.append(copy.deepcopy(dict(route["person"])))

            # Current offices end immediately regardless of storage owner. Keep
            # the pre-clean civic row above so succession still knows which
            # offices became vacant.
            try:
                _fid, _path, _owner, _ordinal, person = load_person_ref(ref)
            except (KeyError, ValueError, FileNotFoundError):
                continue
            if person.get("standing_offices"):
                person["standing_offices"] = []
                save_exact_person(ref, person)

        # Hereditary faction succession remains institution-specific, but it now
        # consumes the globally closed family state.
        for fid, local_dead in by_faction.items():
            fpath, faction = load_faction(fid)
            rpath, roster = load_roster(fid)
            people = roster.get("people", []) if isinstance(roster, Mapping) else []
            if not isinstance(people, list):
                continue
            succession = apply_recognized_succession(
                family_state, faction_ref=fid,
                roster_people=[p for p in people if isinstance(p, Mapping)], year=at.year,
            )
            roster["people"] = succession["people_after"]
            custody_unavailable = {
                str(row.get("person_ref"))
                for row in (custody_state.get("records", []) if isinstance(custody_state.get("records"), list) else [])
                if isinstance(row, Mapping) and str(row.get("person_ref") or "")
                and str(row.get("status") or "") not in {"released", "escaped", "rescued", "executed"}
            }
            office_result = settle_institutional_offices(
                faction, roster, year=at.year, social=social_state,
                player_ref=player_ref or None, unavailable_refs=sorted(custody_unavailable),
            )
            roster = office_result["roster"]
            faction, _rotation = advance_faction_training_epoch(
                faction, roster, at_iso=at_iso, refresh_environment=True,
            )
            writes[fpath] = faction
            writes[rpath] = compact_roster_state(roster, faction=faction)
            faction_cache[fid] = (fpath, faction)
            roster_cache[fid] = (rpath, hydrate_roster_state(writes[rpath], faction=faction))
            successor_ref = succession.get("successor_ref")
            if successor_ref is None:
                successor_ref = next((
                    row["person_ref"] for row in office_result["appointments"]
                    if row.get("office") == "leader"
                ), None)
            if successor_ref and (fid == "house_tang" or successor_ref == player_ref):
                notice = {"kind": "succession_notice", "faction_ref": fid, "successor_ref": successor_ref, "delivered_to_player": True}
                handoff = classify_handoff(notice); handoffs.append({**notice, "handoff": handoff})

        # Personal estates settle before any later extinction pass. The helper
        # resolves the destination before clearing the deceased purse.
        settle_exact_death_estates(
            read_json=read_json, writes=writes, faction_refs=all_faction_ids,
            family=family_state, dead_refs=sorted(dead),
            place_region=place_region, site_rows=site_rows,
        )
        prune_dead_from_durable_activities(
            read_json=read_json, writes=writes, dead_refs=sorted(dead), faction_refs=all_faction_ids,
        )
        staged_contracts = writes.get(_CONTRACT_INDEX_PATH)
        if isinstance(staged_contracts, Mapping):
            contract_index = copy.deepcopy(dict(staged_contracts))
            staged_active = contract_index.get("active", {})
            if not isinstance(staged_active, Mapping):
                raise ValueError("jianghu contract index invalid after death cleanup")
            active_contracts = staged_active
        # Route/tournament reducers hold references to these owner dictionaries.
        # Synchronize the objects in place so their later write-back cannot
        # resurrect a dead worker, traveler, retinue member or warrant subject.
        for path, target in (
            (_PROJECTS_PATH, projects_state),
            (_DEPLOYMENTS_PATH, deployments_state),
            (_ROUTE_OPERATIONS_PATH, route_ops_state),
        ):
            staged = writes.get(path)
            if isinstance(staged, Mapping):
                # ``staged`` can already be the same mutable object as the live
                # frontier owner. Snapshot first so synchronizing shared reducer
                # references can never clear the staged write by aliasing it.
                snapshot = copy.deepcopy(dict(staged))
                target.clear(); target.update(snapshot)
                writes[path] = target
        refreshed_commitments = derived_commitment_state(
            lambda path: copy.deepcopy(writes[path]) if path in writes else read_json(path)
        )
        commitments_state.clear(); commitments_state.update(refreshed_commitments)

        # The universal estate helper writes owners directly. Refresh local
        # frontier caches/state so later work in this same frontier cannot read
        # stale pre-transfer values and overwrite conserved cash.
        for fid in all_faction_ids:
            fpath = faction_path(fid); rpath = roster_path(fid)
            if isinstance(writes.get(fpath), Mapping):
                faction = hydrate_faction_state(writes[fpath])
                faction_cache[fid] = (fpath, faction)
                if isinstance(writes.get(rpath), Mapping):
                    roster_cache[fid] = (rpath, hydrate_roster_state(writes[rpath], faction=faction))
        if isinstance(writes.get(_INDEPENDENTS_PATH), Mapping):
            independent_state = copy.deepcopy(dict(writes[_INDEPENDENTS_PATH]))
        if isinstance(writes.get(_CIVIC_PEOPLE_PATH), Mapping):
            civic_state = copy.deepcopy(dict(writes[_CIVIC_PEOPLE_PATH]))
        for region in sorted(set(place_region.values())):
            mpath = _market_path(region)
            if isinstance(writes.get(mpath), Mapping):
                market_cache[region] = (mpath, copy.deepcopy(dict(writes[mpath])))

        # Civic office vacancy resolution is immediate just like hereditary
        # faction succession. It may materialize one exact official only by
        # consuming a body from the aggregate civilian population.
        if dead_civic_rows:
            civic_rows = [
                hydrate_civic_person(row) for row in civic_state.get("people", [])
                if isinstance(row, Mapping)
            ]
            civic_rows, civilian_after, appointments = appoint_civic_successors(
                civic_rows, dead_rows=dead_civic_rows, civilian_state=civilian_state,
                world_seed=world_seed, year=at.year,
            )
            civic_state["people"] = [compact_civic_person(row) for row in civic_rows]
            writes[_CIVIC_PEOPLE_PATH] = civic_state
            if civilian_after != civilian_state:
                civilian_state = civilian_after
                writes[_CIVILIANS_PATH] = civilian_state
            for appointment in appointments:
                if appointment.get("successor_ref") == player_ref:
                    notice = {"kind": "civic_succession_notice", **appointment, "delivered_to_player": True}
                    handoff = classify_handoff(notice); handoffs.append({**notice, "handoff": handoff})

        writes[_FAMILY_PATH] = family_state

    recurring_rows = schedule.get("recurring", {}) if isinstance(schedule.get("recurring"), Mapping) else {}
    faction_schedule = recurring_rows.get("faction_monthly", {}) if isinstance(recurring_rows, Mapping) else {}
    scheduled_faction_ids = [str(x) for x in faction_schedule.get("owner_refs", [])] if isinstance(faction_schedule, Mapping) and isinstance(faction_schedule.get("owner_refs"), Sequence) and not isinstance(faction_schedule.get("owner_refs"), (str, bytes)) else []
    global_names_cache: set[str] | None = None

    def family_bound_refs(fid: str) -> set[str]:
        refs: set[str] = set()
        marriages = family_state.get("marriages", {}) if isinstance(family_state, Mapping) else {}
        if isinstance(marriages, Mapping):
            for row in marriages.values():
                if not isinstance(row, Mapping) or row.get("status") != "married":
                    continue
                affiliated = set()
                if isinstance(row.get("faction_ref"), str) and row.get("faction_ref"):
                    affiliated.add(str(row["faction_ref"]))
                if isinstance(row.get("faction_refs"), list):
                    affiliated.update(str(x) for x in row["faction_refs"] if isinstance(x, str) and x)
                if fid not in affiliated:
                    continue
                for ref in row.get("spouse_refs", []):
                    if isinstance(ref, str):
                        refs.add(ref)
        return refs

    def all_existing_names() -> set[str]:
        nonlocal global_names_cache
        if global_names_cache is None:
            names: set[str] = set()
            for owner in all_faction_ids:
                try:
                    roster = read_json(roster_path(owner))
                except FileNotFoundError:
                    continue
                people = roster.get("people", []) if isinstance(roster, Mapping) else []
                if isinstance(people, list):
                    names.update(
                        str(person.get("name")) for person in people
                        if isinstance(person, Mapping) and isinstance(person.get("name"), str) and person.get("name")
                    )
            # Exact independent and civic identities must participate in the
            # same uniqueness check even though they are outside faction rosters.
            for owner in (independent_state,):
                rows = owner.get("people", []) if isinstance(owner, Mapping) else []
                if isinstance(rows, list):
                    names.update(str(p.get("name")) for p in rows if isinstance(p, Mapping) and isinstance(p.get("name"), str) and p.get("name"))
            rows = civic_state.get("people", []) if isinstance(civic_state, Mapping) else []
            if isinstance(rows, list):
                names.update(str(p.get("name")) for p in rows if isinstance(p, Mapping) and isinstance(p.get("name"), str) and p.get("name"))
            global_names_cache = names
        return global_names_cache

    outlaw_by_route: dict[str, list[Mapping[str, Any]]] = {}
    outlaw_routes_seeded = False

    def outlaws_for_route(route_id: str) -> list[Mapping[str, Any]]:
        nonlocal outlaw_routes_seeded
        if not outlaw_routes_seeded:
            for fid in all_faction_ids:
                try:
                    _p, faction = load_faction(fid)
                except (FileNotFoundError, ValueError):
                    continue
                if resolved_faction_type(faction) != "outlaw_faction":
                    continue
                routes = faction.get("operating_routes", [])
                if not isinstance(routes, Sequence) or isinstance(routes, (str, bytes)):
                    continue
                for rid in routes:
                    if isinstance(rid, str):
                        outlaw_by_route.setdefault(rid, []).append(faction)
            for rows in outlaw_by_route.values():
                rows.sort(key=lambda f: str(f.get("faction_id", "")))
            outlaw_routes_seeded = True
        return outlaw_by_route.get(route_id, [])

    local_factions_by_place: dict[str, list[Mapping[str, Any]]] = {}
    local_factions_seeded = False

    def local_factions_for_route(route: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        """Derive institutions with a plausible local observation/pursuit presence.

        Outlaw operating territory is handled separately.  Ordinary institutions
        only become road-interception candidates when one endpoint is their real
        headquarters/local site, avoiding omniscient world-wide retaliation.
        """
        nonlocal local_factions_seeded
        if not local_factions_seeded:
            for fid in all_faction_ids:
                try:
                    _path, faction = load_faction(fid)
                except (FileNotFoundError, ValueError):
                    continue
                places: set[str] = set()
                headquarters = str(faction.get("headquarters") or "")
                if headquarters:
                    places.add(headquarters)
                site_ref = str(faction.get("local_site_ref") or "")
                site = site_rows.get(site_ref) if site_ref and isinstance(site_rows, Mapping) else None
                if isinstance(site, Mapping) and site.get("parent_place_ref"):
                    places.add(str(site.get("parent_place_ref")))
                for place_ref in places:
                    local_factions_by_place.setdefault(place_ref, []).append(faction)
            for rows in local_factions_by_place.values():
                rows.sort(key=lambda f: str(f.get("faction_id", "")))
            local_factions_seeded = True
        refs: dict[str, Mapping[str, Any]] = {}
        for place_ref in (str(route.get("from") or ""), str(route.get("to") or "")):
            for faction in local_factions_by_place.get(place_ref, []):
                fid = str(faction.get("faction_id") or "")
                if fid:
                    refs[fid] = faction
        return [refs[fid] for fid in sorted(refs)]

    def route_interception_candidates(route_id: str, route: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        rows: dict[str, Mapping[str, Any]] = {}
        for faction in outlaws_for_route(route_id):
            fid = str(faction.get("faction_id") or "")
            if fid:
                rows[fid] = faction
        for faction in local_factions_for_route(route):
            fid = str(faction.get("faction_id") or "")
            if fid:
                rows[fid] = faction
        return [rows[fid] for fid in sorted(rows)]

    # Close expired funded offers before any review or new offer generation.
    # Expiry is a conservation event: escrow returns to the exact issuer and
    # the closed owner is removed instead of accumulating a status-history log.
    contract_after = copy.deepcopy(dict(contract_index))
    active_after = contract_after.setdefault("active", {})
    expired_contracts: list[str] = []
    refunded_cash = 0
    for cid in sorted(list(active_after)):
        row = active_after.get(cid)
        if not isinstance(row, Mapping) or row.get("status") not in {"offered", "accepted"}:
            continue
        try:
            expires = datetime.fromisoformat(str(row.get("expires_at", "")))
        except ValueError:
            continue
        if expires > at:
            continue
        escrow = max(0, int(row.get("escrow_cash", 0)))
        issuer = str(row.get("issuer_ref", ""))
        if issuer.startswith("market:"):
            region = issuer.split(":", 1)[1]
            mpath, market = load_market(region)
            market["cash_pool"] = max(0, int(market.get("cash_pool", 0))) + escrow
            writes[mpath] = market
            market_cache[region] = (mpath, market)
        elif issuer:
            try:
                fpath, issuer_faction = load_faction(issuer)
            except (FileNotFoundError, ValueError):
                # Never erase a funded obligation if the destination owner is
                # unresolved.  Keep it active so conservation remains explicit.
                continue
            issuer_faction["treasury_cash"] = max(0, int(issuer_faction.get("treasury_cash", 0))) + escrow
            writes[fpath] = issuer_faction
            faction_cache[issuer] = (fpath, issuer_faction)
        else:
            continue
        active_after.pop(cid, None)
        expired_contracts.append(cid)
        refunded_cash += escrow
    if expired_contracts:
        writes[_CONTRACT_INDEX_PATH] = contract_after
        contract_index = contract_after
        active_contracts = active_after
        reviews.append({
            "kind": "contract_expiry",
            "expired_count": len(expired_contracts),
            "refunded_cash": refunded_cash,
            "contract_refs": expired_contracts[:64],
            "truncated": len(expired_contracts) > 64,
        })

    sorted_events = sorted((dict(e) for e in events if isinstance(e, Mapping)), key=_event_order)

    # One sparse one-off event owns elapsed physiology for every exact person.
    # Settle those due bodies before any same-frontier institution, route, or
    # contract reducer reads them, then close authorities immediately if a body
    # crossed into death at this frontier.
    physiology_result = settle_due_person_physiology(
        sorted_events, at=at, load_person=load_person_ref, save_person=save_exact_person,
    )
    pending_one_off_events.extend(physiology_result.get("pending_events", []))
    if physiology_result.get("dead_refs"):
        close_dead_current_authorities(physiology_result["dead_refs"])
    review_physiology = settle_review_faction_physiology(schedule, faction_refs=[str(e.get("owner_ref")) for e in sorted_events if e.get("kind")=="faction_review"], at=at, load_roster=load_roster, save_person=save_exact_person, already_settled_refs=physiology_result.get("settled_refs", []))
    if review_physiology.get("dead_refs"): close_dead_current_authorities(review_physiology["dead_refs"])
    def _get_commitments_state() -> Mapping[str, Any]:
        return commitments_state

    def _set_commitments_state(value: Mapping[str, Any]) -> None:
        nonlocal commitments_state
        commitments_state = dict(value)

    def _refresh_durable_activity_views() -> None:
        """Reload staged finite owners after a mortality reducer changed them."""
        nonlocal commitments_state, route_ops_state, projects_state, deployments_state
        nonlocal contract_index, active_contracts, contract_after, active_after
        staged = writes.get(_ROUTE_OPERATIONS_PATH)
        if isinstance(staged, Mapping):
            route_ops_state = copy.deepcopy(dict(staged))
        staged = writes.get(_PROJECTS_PATH)
        if isinstance(staged, Mapping):
            projects_state = copy.deepcopy(dict(staged))
        staged = writes.get(_DEPLOYMENTS_PATH)
        if isinstance(staged, Mapping):
            deployments_state = copy.deepcopy(dict(staged))
        staged = writes.get(_CONTRACT_INDEX_PATH)
        if isinstance(staged, Mapping):
            contract_index = copy.deepcopy(dict(staged))
            active_contracts = contract_index.get("active", {})
            if not isinstance(active_contracts, Mapping):
                raise ValueError("jianghu contract index invalid after mortality refresh")
            contract_after = copy.deepcopy(dict(contract_index))
            active_after = contract_after.setdefault("active", {})
            if not isinstance(active_after, dict):
                raise ValueError("jianghu contract active owner invalid after mortality refresh")
        commitments_state = derived_commitment_state(
            lambda path: copy.deepcopy(writes[path]) if path in writes else read_json(path)
        )

    # Captivity, ransom and informed rescue responses are one domain frontier.
    settle_captivity_frontier(
        events=sorted_events, at=at, world_seed=world_seed, family_state=family_state,
        custody_state=custody_state, deployments_state=deployments_state,
        writes=writes, reviews=reviews, handoffs=handoffs, pending_one_off_events=pending_one_off_events,
        load_faction=load_faction, load_person_ref=load_person_ref,
        start_custody_rescue_operation=start_custody_rescue_operation,
        apply_directed_relation_event=apply_directed_relation_event, faction_cache=faction_cache,
        get_commitments_state=_get_commitments_state, set_commitments_state=_set_commitments_state,
    )

    # Regional market/government and monthly faction life are separate owners.
    settle_regional_frontier(
        events=sorted_events, at_iso=at_iso, player_ref=player_ref,
        government_state=government_state, government_troops=government_troops, custody_state=custody_state,
        writes=writes, reviews=reviews, handoffs=handoffs, market_cache=market_cache,
        load_market=load_market, load_person_ref=load_person_ref, unavailable_person_refs=unavailable_person_refs,
        pause_people_for_commitment=pause_people_for_commitment, person_combat_index=person_combat_index,
        site_rows=site_rows, place_region=place_region,
        pending_one_off_events=pending_one_off_events, resume_people_training=resume_people_training,
    )
    faction_cycle = settle_faction_cycle_frontier(
        events=sorted_events, at=at, player_ref=player_ref, family_state=family_state,
        social_state=social_state, custody_state=custody_state, independent_state=independent_state,
        writes=writes, reviews=reviews, handoffs=handoffs, pending_training_resume_refs=pending_training_resume_refs,
        pending_one_off_events=pending_one_off_events, place_region=place_region, site_rows=site_rows,
        faction_refs=all_faction_ids, read_json=read_json,
        faction_cache=faction_cache, inventory_cache=inventory_cache, market_cache=market_cache, roster_cache=roster_cache,
        load_faction=load_faction, load_inventory=load_inventory, load_market=load_market, load_roster=load_roster,
        family_bound_refs=family_bound_refs, unavailable_person_refs=unavailable_person_refs,
    )
    upkeep_pressure = faction_cycle["upkeep_pressure"]
    family_state = faction_cycle["family_state"]
    social_state = faction_cycle["social_state"]
    custody_state = faction_cycle["custody_state"]
    independent_state = faction_cycle["independent_state"]
    _refresh_durable_activity_views()

    coalition_refresh = any(event.get('kind') == 'faction_review' for event in sorted_events)
    prior_relations = relations_state
    relations_state, coalition_targets_by_faction = refresh_coalition_decision_view(
        relations_state, at_iso=at_iso, faction_refs=set(all_faction_ids), refresh=coalition_refresh,
    )
    if relations_state != prior_relations:
        writes[_RELATIONS_PATH] = relations_state
        relation_index = _relations_by_faction(relations_state)

    # Monthly faction autonomy/production is a coherent domain reducer.
    autonomy_result = settle_faction_autonomy_frontier(
        sorted_events=sorted_events, at=at, at_iso=at_iso, writes=writes, reviews=reviews,
        active_contracts=active_contracts, active_after=active_after, contract_after=contract_after,
        contract_index=contract_index, commitments_state=commitments_state, upkeep_pressure=upkeep_pressure,
        relation_index=relation_index, coalition_targets_by_faction=coalition_targets_by_faction,
        projects_state=projects_state, route_ops_state=route_ops_state,
        custody_state=custody_state, social_state=social_state, family_state=family_state, civilian_state=civilian_state,
        independent_state=independent_state, travel_data=travel_data, economy_rules=economy_rules,
        geography=geography, place_region=place_region, route_index=route_index, site_rows=site_rows,
        world_seed=world_seed, player_ref=player_ref, general_labor_cash_per_hour=general_labor_cash_per_hour,
        faction_cache=faction_cache, inventory_cache=inventory_cache, market_cache=market_cache, roster_cache=roster_cache,
        load_faction=load_faction, load_inventory=load_inventory, load_market=load_market, load_roster=load_roster,
        load_person_ref=load_person_ref, unavailable_person_refs=unavailable_person_refs, usable_martial_people=usable_martial_people,
        person_combat_index=person_combat_index, active_strategic_operations=active_strategic_operations,
        all_existing_names=all_existing_names, pause_people_for_commitment=pause_people_for_commitment,
        start_monthly_merchant_trade=start_monthly_merchant_trade,
        start_custody_rescue_operation=start_custody_rescue_operation,
        start_strategic_operation=start_strategic_operation, start_autonomous_investment=start_autonomous_investment,
        execute_friendly_aid=execute_friendly_aid,
        prepare_patient_for_treatment=lambda ref, person: prepare_patient_for_treatment(ref, person, schedule=schedule, pending_events=pending_one_off_events, at=at),
        get_commitments_state=_get_commitments_state, set_commitments_state=_set_commitments_state,
    )
    active_contracts = autonomy_result["active_contracts"]
    contract_index = autonomy_result["contract_index"]
    commitments_state = autonomy_result["commitments_state"]
    autonomy_social = autonomy_result.get("social_state", social_state)
    if isinstance(autonomy_social, Mapping) and autonomy_social != social_state:
        social_state = copy.deepcopy(dict(autonomy_social))
        writes[_SOCIAL_PATH] = social_state
    clinical_rebase = rebase_treated_patient_wakes(autonomy_result.get("clinical_physiology_rebases", {}), schedule=schedule, pending_events=pending_one_off_events, at=at, load_person=load_person_ref)
    schedule = clinical_rebase["schedule_after"]
    pending_one_off_events[:] = clinical_rebase["pending_events_after"]
    autonomy_dead = [
        str(ref) for ref in autonomy_result.get("newly_dead_refs", [])
        if isinstance(ref, str) and ref
    ]
    if autonomy_dead:
        # Monthly institutional recovery can close previously incapacitated
        # casualties after the member-cycle reducer has already run. Route those
        # deaths through the same universal authority used by combat/tournaments
        # so deployments, routes, custody, offices, estates and commitments are
        # pruned in the same causal frontier rather than surviving until a later
        # semantic check.
        close_dead_current_authorities(autonomy_dead)
        _refresh_durable_activity_views()

    def _settle_tournament_phase(phase_events: Sequence[Mapping[str, Any]]) -> None:
        nonlocal commitments_state, reputation_state, social_state, equipment_ledger
        nonlocal combats_state, combats, tournament_state, deployments_state
        if not phase_events:
            return
        result = settle_tournament_frontier(
            sorted_events=phase_events, at=at, at_iso=at_iso, world_seed=world_seed,
            player_ref=player_ref, all_faction_ids=all_faction_ids, tournament_state=tournament_state,
            deployments_state=deployments_state, civilian_state=civilian_state, reputation_state=reputation_state,
            social_state=social_state, equipment_ledger=equipment_ledger, combats_state=combats_state,
            commitments_state=commitments_state, writes=writes, reviews=reviews, handoffs=handoffs,
            pending_one_off_events=pending_one_off_events, faction_cache=faction_cache,
            inventory_cache=inventory_cache, market_cache=market_cache, roster_cache=roster_cache,
            local_sites=local_sites, site_rows=site_rows, place_region=place_region, relation_index=relation_index,
            load_faction=load_faction, load_inventory=load_inventory, load_market=load_market,
            load_roster=load_roster, load_person_ref=load_person_ref, current_faction_type=current_faction_type,
            person_place=lambda person, **kw: _person_place(person, local_sites=local_sites, **kw), person_combat_index=person_combat_index,
            unavailable_person_refs=unavailable_person_refs, usable_martial_people=usable_martial_people,
            pause_people_for_commitment=pause_people_for_commitment, settle_and_resume_people=settle_and_resume_people,
            apply_directed_relation_event=apply_directed_relation_event,
        )
        commitments_state = result["commitments_state"]
        reputation_state = result["reputation_state"]
        social_state = result["social_state"]
        equipment_ledger = result["equipment_ledger"]
        combats_state = result["combats_state"]
        combats = combats_state.setdefault("combats", {})
        tournament_state = result["tournament_state"]
        deployments_state = result["deployments_state"]
        tournament_dead = [str(ref) for ref in result.get("newly_dead_refs", []) if isinstance(ref, str) and ref]
        if tournament_dead:
            close_dead_current_authorities(tournament_dead)

    _settle_tournament_phase([
        event for event in sorted_events
        if event.get("kind") in {
            "tournament_delegation_departure", "tournament_trip_departure",
            "tournament_delegation_arrival", "tournament_travel_arrival", "tournament_return_arrival",
        }
    ])

    commitments_state = settle_project_frontier(
        events=sorted_events, at=at, projects_state=projects_state, commitments_state=commitments_state,
        writes=writes, reviews=reviews, pending_one_off_events=pending_one_off_events,
        faction_cache=faction_cache, roster_cache=roster_cache, load_faction=load_faction, load_roster=load_roster,
        settle_and_resume_people=settle_and_resume_people, pause_people_for_commitment=pause_people_for_commitment,
        unavailable_person_refs=unavailable_person_refs,
    )
    equipment_ledger = settle_equipment_maintenance_frontier(
        events=sorted_events, at=at, player_ref=player_ref, equipment_ledger=equipment_ledger,
        writes=writes, reviews=reviews, inventory_cache=inventory_cache, load_faction=load_faction,
        load_inventory=load_inventory, load_roster=load_roster, unavailable_person_refs=unavailable_person_refs,
        usable_martial_people=usable_martial_people,
    )

    # Monthly trade-demand publication is extracted to civilian_frontier.py.

    # Active route operations are physical world owners.  Daily route frontiers
    # advance only movements on that exact route, evaluate finite outlaw forces,
    # resolve autonomous NPC contacts through exact combat, and stop for a hard
    # player handoff when the player is present.  Resolved contacts do not become
    # an append-only history; current injuries/equipment/cargo/cash are authority.
    route_events = [e for e in sorted_events if e.get("kind") == "route_activity_cycle"]
    if route_events:
        route_result = settle_route_frontier(
            active_after=active_after,
            active_contracts=active_contracts,
            apply_directed_relation_event=apply_directed_relation_event,
            at=at,
            at_iso=at_iso,
            close_dead_current_authorities=close_dead_current_authorities,
            combats=combats,
            combats_state=combats_state,
            commitments_state=commitments_state,
            contract_after=contract_after,
            contract_index=contract_index,
            current_faction_type=current_faction_type,
            custody_state=custody_state,
            deployments_state=deployments_state,
            directed_relation=directed_relation,
            economy_rules=economy_rules,
            equipment_ledger=equipment_ledger,
            faction_cache=faction_cache,
            family_state=family_state,
            government_state=government_state,
            handoffs=handoffs,
            inventory_cache=inventory_cache,
            load_faction=load_faction,
            load_inventory=load_inventory,
            load_market=load_market,
            load_person_ref=load_person_ref,
            load_roster=load_roster,
            local_factions_by_place=local_factions_by_place,
            local_factions_for_route=local_factions_for_route,
            local_sites=local_sites,
            market_cache=market_cache,
            move_exact_people=move_exact_people,
            move_exact_people_to_location=move_exact_people_to_location,
            outlaws_for_route=outlaws_for_route,
            pause_people_for_commitment=pause_people_for_commitment,
            pending_one_off_events=pending_one_off_events,
            person_combat_index=person_combat_index,
            place_region=place_region,
            player_ref=player_ref,
            read_json=read_json,
            reputation_state=reputation_state,
            reviews=reviews,
            roster_cache=roster_cache,
            route_events=route_events,
            route_index=route_index,
            route_interception_candidates=route_interception_candidates,
            route_ops_state=route_ops_state,
            save_exact_person=save_exact_person,
            schedule=schedule,
            settle_and_resume_people=settle_and_resume_people,
            site_rows=site_rows,
            social_state=social_state,
            sorted_events=sorted_events,
            start_custody_rescue_operation=start_custody_rescue_operation,
            travel_data=travel_data,
            unavailable_person_refs=unavailable_person_refs,
            usable_martial_people=usable_martial_people,
            world_seed=world_seed,
            writes=writes,
        )
        commitments_state = route_result["commitments_state"]
        custody_state = route_result["custody_state"]
        equipment_ledger = route_result["equipment_ledger"]
        contract_index = route_result["contract_index"]
        active_contracts = route_result["active_contracts"]
        reputation_state = route_result["reputation_state"]
        social_state = route_result["social_state"]

    _settle_tournament_phase([
        event for event in sorted_events
        if event.get("kind") in {
            "tournament_advance_notice", "tournament_registration_open",
            "tournament_registration_close", "tournament_convergence_day",
            "regional_martial_tournament", "great_jianghu_tournament", "midyear_junior_tournament",
            "tournament_competition_continue",
        }
    ])

    # Annual persistent-person life course is a separate domain reducer.
    life_result = settle_annual_life_frontier(
        events=sorted_events, at=at, player_ref=player_ref,
        family_state=family_state, social_state=social_state, custody_state=custody_state,
        independent_state=independent_state, civic_state=civic_state, civilian_state=civilian_state,
        schedule=schedule, world_seed=world_seed, read_json=read_json, faction_refs=all_faction_ids,
        place_region=place_region, site_rows=site_rows,
        load_market=load_market, market_cache=market_cache, writes=writes, reviews=reviews, handoffs=handoffs,
        pending_training_resume_refs=pending_training_resume_refs,
        load_faction=load_faction, load_roster=load_roster,
        committed_person_refs=committed_person_refs, active_combat_person_refs=active_combat_person_refs,
        unavailable_person_refs=unavailable_person_refs, family_bound_refs=family_bound_refs,
        faction_cache=faction_cache, roster_cache=roster_cache,
    )
    family_state = life_result["family_state"]
    social_state = life_result["social_state"]
    custody_state = life_result["custody_state"]
    independent_state = life_result["independent_state"]
    civic_state = life_result["civic_state"]
    civilian_state = life_result["civilian_state"]
    _refresh_durable_activity_views()

    # The final annual faction chunk may complete at most one causal, conserved
    # institutional transition.  This is not a random spawner: every person,
    # silver unit, ration and estate must already exist before the transition.
    evolution = settle_autonomous_institutional_evolution(
        read_json=read_json, writes=writes, schedule=schedule, events=sorted_events,
        year=at.year, at_iso=at_iso, player_ref=player_ref, site_rows=site_rows,
        relations_state=relations_state, family_state=family_state,
        independent_state=independent_state, social_state=social_state,
    )
    relations_state = evolution["relations"]
    family_state = evolution["family"]
    independent_state = evolution["independents"]
    reviews.extend(copy.deepcopy(dict(row)) for row in evolution.get("reviews", []) if isinstance(row, Mapping))

    # Calendar rows are public institutions.  Starting/closing them is not by
    # itself acceptance, registration, travel, or a player decision.
    known_internal = {
        "regional_market_cycle", "faction_upkeep", "faction_member_cycle", "equipment_maintenance_review",
        "faction_review", "trade_demand_review", "route_activity_cycle",
        "custody_captor_review", "custody_response_due",
        "annual_faction_life_review", "family_birth_due", "contract_expiry_due", "autonomous_project_due",
        "tournament_advance_notice", "tournament_registration_open", "tournament_registration_close",
        "regional_martial_tournament", "great_jianghu_tournament", "midyear_junior_tournament", "tournament_competition_continue",
        "jianghu_ranking_publication", "annual_civilian_demography", "person_physiology_due",
    }
    for event in sorted_events:
        kind = str(event.get("kind", ""))
        if kind in known_internal:
            continue
        row = dict(event)
        row.setdefault("delivered_to_player", False)
        handoff = classify_handoff(row)
        reviews.append({"kind": "calendar_event", "event": row, "handoff": handoff})
        if handoff["class"] != "internal":
            handoffs.append({**row, "handoff": handoff})

    # Custody can end because a captor died at another faction's frontier.
    # Resume those detainees only after all same-time reducers have finished so
    # a local roster after-image cannot overwrite the release.
    if pending_training_resume_refs:
        resume_people_training(sorted(pending_training_resume_refs))

    # Death cleanup is a same-frontier postcondition, not merely an early
    # reducer side effect. Later reducers at the same timestamp may hold local
    # copies of deployments/routes that were loaded before a monthly recovery,
    # tournament, route contact, or other death was closed. Re-prune only the
    # finite durable activity owners here so no stale local write can resurrect
    # a dead exact identity. Estate/family/office settlement is intentionally
    # not repeated.
    if frontier_closed_dead_refs:
        prune_dead_from_durable_activities(
            read_json=read_json, writes=writes, dead_refs=sorted(frontier_closed_dead_refs),
            faction_refs=all_faction_ids,
        )
        _refresh_durable_activity_views()

    # Any touched roster is authoritative for current living population counts.
    # Reconcile here so combat, execution, birth, recruitment, and natural death
    # cannot leave faction upkeep/capacity using stale casualty totals.
    for path, record in list(writes.items()):
        if not isinstance(record, Mapping) or not path.startswith("state/martial-world/people/"):
            continue
        fid = record.get("faction_ref")
        if not isinstance(fid, str) or not fid:
            continue
        fpath, faction = load_faction(fid)
        faction = reconcile_faction_population(faction, record)
        writes[fpath] = faction
        faction_cache[fid] = (fpath, faction)

    # A faction ceases to be a living institution only when no living member
    # remains. Keep its former owner/inventory/roster as a dormant estate, but
    # remove it from current existence and current institutional diplomacy.
    extinction = settle_extinctions_from_touched_rosters(
        read_json=read_json, writes=writes, relations_state=relations_state,
        load_faction=load_faction, relations_path=_RELATIONS_PATH,
    )
    registry_state = extinction["registry"]
    relations_state = extinction["relations"]
    for fid, (fpath, faction) in extinction["faction_updates"].items():
        faction_cache[fid] = (fpath, faction)
    for fid in extinction["extinct_refs"]:
        reviews.append({"kind": "faction_extinction", "faction_ref": fid})
    if extinction["extinct_refs"]:
        custody_after, extinct_releases = release_custody_held_by_extinct_factions(
            custody_state, extinct_refs=extinction["extinct_refs"],
        )
        if extinct_releases:
            custody_state = custody_after
            writes[_CUSTODY_PATH] = custody_state
            for released in extinct_releases:
                commitments_state = settle_and_resume_people(
                    [released["person_ref"]], activity_ref=released["custody_id"],
                    commitments_state=commitments_state,
                )

    # Every hot owner leaves this bridge in canonical sparse form so recurring
    # settlement cannot re-inflate defaults/static policy on the next cycle.
    for path, record in list(writes.items()):
        if not isinstance(record, Mapping):
            continue
        if path.startswith("state/martial-world/factions/"):
            writes[path] = compact_faction_state(record)
        elif path.startswith("state/martial-world/inventories/"):
            writes[path] = compact_inventory_state(record)
        elif path.startswith("state/martial-world/people/"):
            fid = record.get("faction_ref")
            if isinstance(fid, str) and fid:
                _fp, faction = load_faction(fid)
                writes[path] = compact_roster_state(record, faction=faction)

    # Any reducer may have created a wound, toxin burden, or active medicine
    # state on a touched exact person. Materialize exactly one future wake only
    # after all same-frontier writes are final, avoiding duplicate body clocks.
    current_one_off = schedule.get("one_off", {}) if isinstance(schedule, Mapping) else {}
    existing_event_ids = list(current_one_off) if isinstance(current_one_off, Mapping) else []
    existing_event_ids.extend(str(row.get("event_id")) for row in pending_one_off_events if isinstance(row, Mapping) and row.get("event_id"))
    pending_one_off_events.extend(new_physiology_wakes_from_touched_people(
        writes, now=at, existing_event_ids=existing_event_ids, replace_event_ids=review_physiology.get("replaced_event_ids", []), replacement_carries=review_physiology.get("carry_by_person", {}),
    ))

    schedule_after = settle_schedule(schedule, through=at, processed_events=sorted_events)
    for event_id in review_physiology.get("replaced_event_ids", []): schedule_after.get("one_off", {}).pop(event_id, None)
    schedule_after = prune_contract_expiry_events(schedule_after, active_contracts)
    schedule_after = sync_faction_activity(
        schedule_after,
        faction_ids=registry_state.get("faction_refs", []) if isinstance(registry_state, Mapping) else [],
        now=at,
    )
    active_route_ids = route_ids_needing_service(route_ops_state.get("movements", {}))
    schedule_after = sync_route_activity(schedule_after, active_route_ids=active_route_ids, now=at)
    for one_off in pending_one_off_events:
        schedule_after = upsert_one_off_event(schedule_after, one_off)
    writes[_SCHEDULER_PATH] = schedule_after
    return {
        "writes": writes,
        "reviews": reviews,
        "handoffs": handoffs,
        "schedule_after": schedule_after,
    }

__all__ = ["settle_shared_frontier"]
