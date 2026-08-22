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

from .factions import autonomy_review
from .autonomous_factions import (
    monthly_recruitment_tranche, procure_project_materials,
    secure_food_purchase, sell_surplus_to_market,
)
from .handoffs import classify_handoff
from .regional_economy import (
    execute_purchase, execute_sale, quote_sale, settle_cycles,
    trade_shipment_opportunities, unit_market_price_cash,
)
from .route_activity import route_exposure
from .outlaws import attack_decision, route_threat_score
from .contracts import escort_quote, funded_contract_offer, settle_payment, transition as contract_transition
from .scheduler import settle_schedule, sync_route_activity, upsert_one_off_event
from .upkeep import monthly_upkeep_quote
from .compensation import monthly_stipend, settle_monthly_compensation
from .person_state import compact_person_state, compact_roster_state, hydrate_roster_state, reconcile_faction_population
from .faction_state import (
    compact_faction_state, faction_path, faction_profile, faction_type, hydrate_faction_state, inventory_path, roster_path,
)
from .inventory_state import compact_inventory_state, hydrate_inventory_state
from .training import (
    advance_faction_training_epoch, apply_institutional_training,
    school_tuition_snapshot, settle_and_reset_faction_training_cycle,
)
from .institutional_lifecycle import advance_institution, institutional_status
from .infrastructure import (
    training_domain_capacity, administrative_workload_units, staffed_administrative_capability,
    administration_factor_milli, infirmary_capacity, enterprise_scale_value, residential_capacity, workshop_capacity,
    enterprise_operating_efficiency_milli,
    estate_land_summary, start_building_expansion, advance_building_expansion,
    building_expansion_requirements, building_upgrade_requirements,
    start_building_upgrade, advance_building_upgrade, start_enterprise_scale_expansion,
    advance_enterprise_scale_expansion, enterprise_scale_basis, transport_yard_capacity,
)
from .commitments import release_resources, reserve_resources
from .combat_simulation import simulate_exact_combat
from .exact_combat import capability_from_person, initialize_combat
from .tournaments import (
    open_tournament, register as tournament_register, close_registration,
    advance_individual_competition, event_profile as tournament_event_profile,
    convergence_pairs as tournament_convergence_pairs,
    convergence_day_theme as tournament_convergence_day_theme,
    faction_performance_standings, estimated_host_days as tournament_estimated_host_days,
    add_attendance_prize_cash as tournament_add_attendance_prize_cash,
    placement_payouts as tournament_placement_payouts,
    merge_delegation_presence as tournament_merge_delegation_presence,
    themed_convergence_pairs as tournament_themed_convergence_pairs,
)
from .rankings import (
    publish_rankings, public_score, add_public_points,
    apply_faction_awareness_evidence, apply_personal_fame_evidence,
    apply_faction_reputation_evidence,
)
from .relationships import apply_relationship_event
from .family_simulation import advance_annual_life_course, advance_npc_relationships, apply_family_death_status, apply_recognized_succession, resolve_birth, review_conceptions
from .government import allocate_response, attention_from_evidence
from .faction_relations import apply_relation_event
from .crime_custody import create_custody_record
from .recruitment import deterministic_candidate
from .people import apply_age_development, deterministic_body_mass_kg, deterministic_name, deterministic_sex
from .agriculture import crop_record, harvest_quote
from .manpower import combat_ready_count, is_faction_member
from .world_health import (
    annual_voluntary_departure_refs, civilian_annual_demography, institutional_stress_milli,
    living_member_count, retirement_due, sustainable_recruitment_gap, training_intensity_for_stress,
)
from .world_history import record_event
from .weather import weather_snapshot
from .enterprise_operations import (
    operate_apothecary_month, operate_workshop_month,
    operate_brotherhood_livelihood_month, operate_criminal_enterprise_month,
)
from .equipment_state import hydrate_equipment_ledger, compact_equipment_ledger
from .equipment_lifecycle import repair_quote as equipment_repair_quote, repair_material_requirements
from .strategic_autonomy import (
    choose_friendly_aid_target, choose_hostile_action, choose_investment_priority,
    stable_permille, tournament_travel_interested, tournament_entrant_interested,
    tournament_match_relation_event, tournament_spectator_interested,
)
from .travel import latest_safe_departure, shortest_route, travel_plan
from .faction_politics import conflict_stage, cross_camp_pressure, faction_camp

_SCHEDULER_PATH = "state/martial-world/scheduler.json"
_CONTRACT_INDEX_PATH = "state/martial-world/contracts/index.json"
_RELATIONS_PATH = "state/martial-world/faction-relations.json"
_GEOGRAPHY_PATH = "game/data/martial-world/geography.json"
_REGIONAL_ECONOMY_PATH = "game/data/martial-world/regional-economy.json"
_SOCIAL_PATH = "state/martial-world/social.json"
_META_PATH = "state/meta.json"
_ROUTE_OPERATIONS_PATH = "state/martial-world/route-operations.json"
_COMMITMENTS_PATH = "state/martial-world/commitments.json"
_EQUIPMENT_LEDGER_PATH = "state/martial-world/equipment-ledger.json"
_LOCAL_SITES_PATH = "game/data/martial-world/local-sites.json"
_COMBATS_PATH = "state/martial-world/combats.json"
_SCENE_PATH = "state/scene.json"
_TRAVEL_DATA_PATH = "game/data/martial-world/travel.json"
_TOURNAMENTS_PATH = "state/martial-world/tournaments.json"
_REPUTATION_PATH = "state/martial-world/reputation.json"
_FAMILY_PATH = "state/martial-world/family.json"
_PERSON_ROUTES_PATH = "state/martial-world/person-routes.json"
_GOVERNMENT_PATH = "state/martial-world/government.json"
_GOVERNMENT_TROOPS_PATH = "game/data/martial-world/government-troops.json"
_CIVILIANS_PATH = "state/martial-world/civilian-populations.json"
_CUSTODY_PATH = "state/martial-world/custody.json"
_INDEPENDENTS_PATH = "state/martial-world/independent-people.json"
_WORLD_HISTORY_PATH = "state/martial-world/world-history.json"
_DEPLOYMENTS_PATH = "state/martial-world/deployments.json"
_PROJECTS_PATH = "state/martial-world/projects.json"


def _arrival_site(local_sites: Mapping[str, Any], place_ref: str) -> str | None:
    sites = local_sites.get("sites", {}) if isinstance(local_sites, Mapping) else {}
    if not isinstance(sites, Mapping):
        return None
    rows = [
        str(site_ref) for site_ref, row in sites.items()
        if isinstance(site_ref, str) and isinstance(row, Mapping) and row.get("parent_place_ref") == place_ref
    ]
    public = [
        site_ref for site_ref in rows
        if isinstance(sites.get(site_ref), Mapping)
        and str(sites[site_ref].get("public_access", "public")) not in {"restricted_by_faction_policy", "private"}
    ]
    ordered = sorted(public or rows)
    return ordered[0] if ordered else None


def _tournament_venue_site(local_sites: Mapping[str, Any], place_ref: str) -> str | None:
    """Return the host city's actual tournament ground when one is authored."""
    sites = local_sites.get("sites", {}) if isinstance(local_sites, Mapping) else {}
    if not isinstance(sites, Mapping):
        return _arrival_site(local_sites, place_ref)
    grounds = sorted(
        str(site_ref) for site_ref, row in sites.items()
        if isinstance(site_ref, str) and isinstance(row, Mapping)
        and row.get("parent_place_ref") == place_ref and row.get("site_type") == "tournament_ground"
    )
    return grounds[0] if grounds else _arrival_site(local_sites, place_ref)


def _tournament_organizer_ref(host_place: str, *, great: bool) -> str:
    """Tournament host identity; purse funding remains entry-fee based."""
    if great or host_place == "luoyang":
        return "government.imperial"
    return f"government.{host_place}"


def _credit_cargo_to_inventory(inventory: dict[str, Any], *, item_ref: str, quantity: int) -> None:
    qty = max(0, int(quantity))
    if qty <= 0:
        return
    if item_ref == "food_ration_day":
        inventory["food_ration_days"] = max(0, int(inventory.get("food_ration_days", 0))) + qty
        return
    if item_ref in {"metal_kg", "timber_kg", "hardwood_kg", "leather_kg", "cloth_m", "charcoal_kg", "rope_m", "stone_kg"}:
        raw = inventory.setdefault("raw_materials", {})
        if isinstance(raw, dict):
            raw[item_ref] = max(0, int(raw.get(item_ref, 0))) + qty
        return
    equipment = inventory.setdefault("equipment", {})
    if isinstance(equipment, dict):
        equipment[item_ref] = max(0, int(equipment.get(item_ref, 0))) + qty


def _social_event(
    social_state: Mapping[str, Any], *, observer_ref: str, subject_ref: str,
    event_kind: str, severity_milli: int, player_ref: str,
) -> dict[str, Any]:
    """Apply one known current-world social consequence without event history."""
    return apply_relationship_event(
        social_state,
        observer_ref=observer_ref,
        subject_ref=subject_ref,
        event_kind=event_kind,
        observer_knows=True,
        severity_milli=severity_milli,
        protected_player_ref=player_ref or "pc_wei_tang",
    )["state_after"]


def _reputation_after_points(
    state: Mapping[str, Any], person_ref: str, *, tournament_points: int = 0,
    contract_points: int = 0, duel_points: int = 0,
) -> dict[str, Any]:
    return add_public_points(
        state, person_ref, tournament_points=tournament_points,
        contract_points=contract_points, duel_points=duel_points,
    )
def _relations_by_faction(relations: Mapping[str, Any]) -> dict[str, list[Mapping[str, Any]]]:
    out: dict[str, list[Mapping[str, Any]]] = {}
    rows = relations.get("edges", [])
    if not isinstance(rows, list):
        return out
    for edge in rows:
        if not isinstance(edge, Mapping):
            continue
        src = edge.get("from_faction")
        if isinstance(src, str):
            out.setdefault(src, []).append(edge)
    return out


def _market_path(region_id: str) -> str:
    return f"state/martial-world/markets/{region_id}.json"


def _route_lookup(geography: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = geography.get("routes", [])
    if not isinstance(rows, list):
        return {}
    return {
        str(row["id"]): row
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("id"), str)
    }


def _place_to_region(geography: Mapping[str, Any]) -> dict[str, str]:
    places = geography.get("places", {})
    if not isinstance(places, Mapping):
        return {}
    out: dict[str, str] = {}
    for place_id, row in places.items():
        if not isinstance(row, Mapping):
            continue
        region = row.get("climate_profile")
        if isinstance(place_id, str) and isinstance(region, str):
            out[place_id] = region
    return out




def _chunk_contains_final_owner(
    schedule: Mapping[str, Any], events: Sequence[Mapping[str, Any]], *, class_id: str,
) -> bool:
    """Return whether this resumable recurring chunk contains the class's last owner.

    Some recurring consequences are class-level reductions that must run exactly
    once *after* every per-owner reducer at the same boundary.  Bounded scheduler
    chunking is an execution detail, so those reductions cannot run once per
    transaction or their outcomes would depend on ``_MAX_OWNERS_PER_FRONTIER_CHUNK``.
    """
    recurring = schedule.get("recurring", {}) if isinstance(schedule, Mapping) else {}
    row = recurring.get(class_id) if isinstance(recurring, Mapping) else None
    if not isinstance(row, Mapping):
        return False
    owners = row.get("owner_refs", [])
    if not isinstance(owners, Sequence) or isinstance(owners, (str, bytes)):
        return False
    ordered = sorted(str(x) for x in owners if isinstance(x, str))
    if not ordered:
        return False
    processed = {
        str(event.get("owner_ref")) for event in events
        if isinstance(event, Mapping)
        and event.get("schedule_class") == class_id
        and isinstance(event.get("owner_ref"), str)
    }
    return ordered[-1] in processed


def _event_order(event: Mapping[str, Any]) -> tuple[int, str, str]:
    # Resource settlement precedes strategic review at a shared frontier so the
    # review sees the state that actually exists after upkeep/market production.
    order = {
        "regional_market_cycle": 10,
        "faction_upkeep": 20,
        "faction_member_cycle": 25,
        "equipment_maintenance_review": 30,
        "faction_review": 40,
        "trade_demand_review": 50,
        "tournament_delegation_departure": 56,
        "tournament_trip_departure": 57,
        "tournament_travel_arrival": 58,
        "tournament_delegation_arrival": 58,
        "tournament_return_arrival": 59,
        "route_activity_cycle": 60,
        "faction_operation_arrival": 62,
        "faction_operation_return": 63,
        "autonomous_project_due": 65,
        "annual_faction_life_review": 70,
    }
    kind = str(event.get("kind", ""))
    return (order.get(kind, 100), str(event.get("owner_ref", "")), str(event.get("event_id", "")))


def settle_martial_world_frontier(
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

    def apply_directed_relation_event(source_faction: str, target_faction: str, event_kind: str) -> None:
        nonlocal world_history
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
        before_pair_hostility = max(
            max(0, int(prior.get("hostility", 0))) if isinstance(prior, Mapping) else 0,
            max(0, int(reverse.get("hostility", 0))) if isinstance(reverse, Mapping) else 0,
        )
        before_stage = conflict_stage({"hostility": before_pair_hostility})
        updated = apply_relation_event(prior, from_faction=source_faction, to_faction=target_faction, event_kind=event_kind)
        edges[:] = [
            edge for edge in edges
            if not (isinstance(edge, Mapping) and edge.get("from_faction") == source_faction and edge.get("to_faction") == target_faction)
        ]
        edges.append(updated)
        edges.sort(key=lambda edge: (str(edge.get("from_faction", "")), str(edge.get("to_faction", ""))))
        relation_index[source_faction] = [edge for edge in edges if isinstance(edge, Mapping) and edge.get("from_faction") == source_faction]
        writes[_RELATIONS_PATH] = relations_state
        after_pair_hostility = max(
            max(0, int(updated.get("hostility", 0))),
            max(0, int(reverse.get("hostility", 0))) if isinstance(reverse, Mapping) else 0,
        )
        after_stage = conflict_stage({"hostility": after_pair_hostility})
        if before_stage != "war" and after_stage == "war":
            war_a = min(source_faction, target_faction)
            war_b = max(source_faction, target_faction)
            world_history = record_event(
                world_history, at=at_iso, kind="faction_war_started",
                faction_ref=war_a, target_faction_ref=war_b,
                faction_camp=faction_camp(war_a) or "unclassified",
                target_faction_camp=faction_camp(war_b) or "unclassified",
                hostility=after_pair_hostility, trigger_event=event_kind,
            )
            writes[_WORLD_HISTORY_PATH] = world_history
        elif before_stage == "war" and after_stage != "war":
            war_a = min(source_faction, target_faction)
            war_b = max(source_faction, target_faction)
            world_history = record_event(
                world_history, at=at_iso, kind="faction_war_deescalated",
                faction_ref=war_a, target_faction_ref=war_b,
                faction_camp=faction_camp(war_a) or "unclassified",
                target_faction_camp=faction_camp(war_b) or "unclassified",
                hostility=after_pair_hostility, new_stage=after_stage,
                trigger_event=event_kind,
            )
            writes[_WORLD_HISTORY_PATH] = world_history

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
        world_history = copy.deepcopy(dict(read_json(_WORLD_HISTORY_PATH)))
    except FileNotFoundError:
        world_history = {"schema": "jianghu-world-history-1.0", "recent": [], "counters": {}}

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
            row = read_json(path)
            if not isinstance(row, Mapping) or row.get("faction_id") != fid:
                raise ValueError("jianghu faction owner invalid")
            faction_cache[fid] = (path, hydrate_faction_state(row))
        return faction_cache[fid]

    def load_inventory(fid: str) -> tuple[str, dict[str, Any]]:
        if fid not in inventory_cache:
            path = inventory_path(fid)
            row = read_json(path)
            if not isinstance(row, Mapping) or row.get("faction_ref") != fid:
                raise ValueError("jianghu inventory owner invalid")
            inventory_cache[fid] = (path, hydrate_inventory_state(row))
        return inventory_cache[fid]

    def load_roster(fid: str) -> tuple[str, dict[str, Any]]:
        if fid not in roster_cache:
            _fpath, faction = load_faction(fid)
            path = roster_path(fid)
            row = read_json(path)
            if not isinstance(row, Mapping) or row.get("faction_ref") != fid:
                raise ValueError("jianghu roster invalid")
            roster_cache[fid] = (path, hydrate_roster_state(row, faction=faction))
        return roster_cache[fid]

    def load_market(region: str) -> tuple[str, dict[str, Any]]:
        if region not in market_cache:
            path = _market_path(region)
            row = read_json(path)
            if not isinstance(row, Mapping):
                raise ValueError("jianghu market invalid")
            market_cache[region] = (path, copy.deepcopy(dict(row)))
        return market_cache[region]

    try:
        commitments_state = copy.deepcopy(dict(read_json(_COMMITMENTS_PATH)))
    except FileNotFoundError:
        commitments_state = {"schema": "jianghu-commitment-state-1.0", "commitments": {}, "person_index": {}}
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
            and row.get("status") not in {"released", "escaped", "executed"}
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

    def unavailable_person_refs(state: Mapping[str, Any] | None = None) -> set[str]:
        return commitment_person_refs(state) | custody_person_refs() | active_combat_person_refs()

    def active_agriculture_mu(fid: str) -> int:
        total = 0
        rows = schedule.get("one_off", {}) if isinstance(schedule, Mapping) else {}
        if isinstance(rows, Mapping):
            for raw in rows.values():
                if isinstance(raw, Mapping) and raw.get("kind") == "agriculture_harvest_due" and raw.get("faction_ref") == fid:
                    total += max(0, int(raw.get("planted_mu", 0)))
        for raw in pending_one_off_events:
            if isinstance(raw, Mapping) and raw.get("kind") == "agriculture_harvest_due" and raw.get("faction_ref") == fid:
                total += max(0, int(raw.get("planted_mu", 0)))
        return total

    def eligible_crop(refs: Sequence[str], month: int, *, stable: str | None = None) -> str | None:
        eligible: list[str] = []
        for ref in refs:
            try:
                row = crop_record(ref)
            except KeyError:
                continue
            months = row.get("planting_months", []) if isinstance(row, Mapping) else []
            if isinstance(months, list) and month in [int(x) for x in months]:
                eligible.append(ref)
        if not eligible:
            return None
        if stable is None:
            return eligible[0]
        digest = hashlib.sha256(stable.encode("utf-8")).digest()
        return eligible[int.from_bytes(digest[:4], "big") % len(eligible)]


    def person_place(person: Mapping[str, Any], *, home_place: str = "", home_site_ref: str = "") -> str:
        """Resolve current settlement while preserving sparse implicit-home state.

        Persistent faction people intentionally omit ``location_ref`` while they
        are at their ordinary faction home.  An explicit location is therefore
        a displacement override, not a required home coordinate.  Treating the
        missing override as "nowhere" silently removes most of the canonical
        world from travel, trade, defense and tournament planning.
        """
        location_ref = str(person.get("location_ref") or "")
        if not location_ref:
            return str(home_place or "")
        if home_site_ref and location_ref == str(home_site_ref):
            return str(home_place or location_ref)
        site = site_rows.get(location_ref) if isinstance(site_rows, Mapping) else None
        if isinstance(site, Mapping) and site.get("parent_place_ref"):
            return str(site.get("parent_place_ref"))
        return location_ref

    def active_strategic_operations(fid: str) -> int:
        rows = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
        if not isinstance(rows, Mapping):
            return 0
        return sum(
            1 for row in rows.values()
            if isinstance(row, Mapping) and row.get("faction_ref") == fid
            and row.get("operation_kind") in {"formal_challenge", "faction_raid", "faction_war_strike"}
            and row.get("status") not in {"completed", "cancelled"}
        )

    def start_strategic_operation(fid: str, intent: Mapping[str, Any]) -> dict[str, Any]:
        nonlocal commitments_state, world_history
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
        available = [p for p in usable_martial_people(roster, exclude_committed=blocked) if person_place(p, home_place=source_place, home_site_ref=str(faction.get("local_site_ref") or "")) == source_place]
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
        elif kind == "faction_war_strike":
            # A declared war front is materially larger than a feud raid.  The
            # muster scales with risk appetite and current hostility, but remains
            # bounded so exact multi-person combat is finite and the faction
            # cannot commit an unlimited share of its roster in one review.
            desired = max(10, min(24, 10 + risk // 10 + max(0, hostility - 65) // 4))
        else:
            desired = max(3, min(6, 3 + risk // 30))
        participants = [str(p["person_id"]) for p in available[:desired] if isinstance(p.get("person_id"), str)]
        if len(participants) < desired:
            return {"result": "insufficient_available_fighters"}
        travel_days = max(1, (int(float(plan.get("travel_hours", 0)) * 1000) + 23999) // 24000)
        # Expedition bodies carry enough food for the outbound and return legs
        # plus one contact day. This prevents a faction from launching an
        # operation whose survivors have no physically funded way home.
        food_need = len(participants) * (travel_days * 2 + 1)
        food_before = max(0, int(inventory.get("food_ration_days", 0)))
        if food_before < food_need:
            return {"result": "insufficient_travel_provisions"}
        # Formal public travel pays ordinary route tolls in both directions. A
        # covert/hostile raid does not receive a lawful toll abstraction here.
        toll = (max(0, int(plan.get("toll_cash", 0))) * 2) if kind == "formal_challenge" else 0
        cash_before = max(0, int(faction.get("treasury_cash", 0)))
        if cash_before < toll:
            return {"result": "insufficient_travel_cash"}
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
        inventory["food_ration_days"] = food_before - food_need
        faction["treasury_cash"] = cash_before - toll
        if toll > 0:
            source_region = place_region.get(source_place)
            if isinstance(source_region, str):
                try:
                    mpath, market = load_market(source_region)
                    market["cash_pool"] = max(0, int(market.get("cash_pool", 0))) + toll
                    writes[mpath] = market; market_cache[source_region] = (mpath, market)
                except FileNotFoundError:
                    pass
        deployments[op_ref] = {
            "deployment_ref": op_ref, "faction_ref": fid, "target_faction_ref": target_fid,
            "operation_kind": kind, "participant_refs": participants,
            "source_place_ref": source_place, "target_place_ref": target_place,
            "started_at": at_iso, "departure_at": departure_at.isoformat(), "arrival_at": str(plan.get("arrival_at")),
            "travel_hours": float(plan.get("travel_hours", 0)), "route_refs": list(plan.get("edges", [])),
            "status": "traveling_outbound", "targeting_intent": "disable" if kind == "formal_challenge" else "lethal",
        }
        pause_people_for_commitment(fid, participants)
        writes[_DEPLOYMENTS_PATH] = deployments_state; writes[_COMMITMENTS_PATH] = commitments_state
        writes[fpath] = faction; writes[ipath] = inventory
        faction_cache[fid] = (fpath, faction); inventory_cache[fid] = (ipath, inventory)
        pending_one_off_events.append({
            "event_id": f"operation_arrival:{op_ref}", "kind": "faction_operation_arrival",
            "due_at": str(plan.get("arrival_at")), "owner_ref": op_ref,
            "requires_player_decision": False,
        })
        world_history = record_event(
            world_history, at=at_iso, kind=f"{kind}_mobilized", faction_ref=fid,
            target_faction_ref=target_fid, participant_count=len(participants),
            source_camp=faction_camp(fid) or "unclassified", target_camp=faction_camp(target_fid) or "unclassified",
            departure_at=departure_at.isoformat(), arrival_at=str(plan.get("arrival_at")),
        )
        writes[_WORLD_HISTORY_PATH] = world_history
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
        return {"result": "departed", "operation_ref": op_ref, "target_faction_ref": target_fid, "participant_count": len(participants)}

    def start_autonomous_investment(fid: str, intent: Mapping[str, Any]) -> dict[str, Any]:
        nonlocal commitments_state, world_history
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
        transport_assets = inventory.get("transport_assets", {}) if isinstance(inventory.get("transport_assets"), Mapping) else {}
        project_upkeep = monthly_upkeep_quote(
            faction, riding_horses=max(0, int(transport_assets.get("riding_horses", 0))),
            pack_animals=max(0, int(transport_assets.get("pack_animals", 0))),
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
        try:
            commitments_after = reserve_resources(
                commitments_state, resources=[("person", ref, fid) for ref in worker_refs], actor_ref=worker_refs[0], owner_ref=fid,
                activity_ref=project_ref, activity_kind="construction" if out.get("project_type") in {"building_upgrade", "building_expansion"} else "enterprise_setup",
                started_at=at_iso, location_ref=str(faction.get("local_site_ref") or faction.get("headquarters") or ""),
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
        out.update({"project_ref": project_ref, "faction_ref": fid, "started_at": at_iso, "planned_days": int(days_needed)})
        registry[project_ref] = out
        pause_people_for_commitment(fid, worker_refs)
        writes[_PROJECTS_PATH] = projects_state; writes[_COMMITMENTS_PATH] = commitments_state; writes[fpath] = faction; writes[ipath] = inventory
        faction_cache[fid] = (fpath, faction); inventory_cache[fid] = (ipath, inventory)
        due = at + timedelta(days=max(1, int(days_needed)))
        pending_one_off_events.append({"event_id": f"autonomous_project_due:{project_ref}", "kind": "autonomous_project_due", "due_at": due.isoformat(), "owner_ref": project_ref, "requires_player_decision": False})
        world_history = record_event(world_history, at=at_iso, kind="infrastructure_project_started", faction_ref=fid, project_ref=project_ref)
        writes[_WORLD_HISTORY_PATH] = world_history
        return {
            "result": "project_started", "project_ref": project_ref,
            "project_type": out.get("project_type"), "planned_days": int(days_needed),
            "material_purchase_cash": material_cash_spent,
            "project_overhead_cash": overhead_cash_spent,
            "total_cash_spent": max(0, starting_cash - max(0, int(faction.get("treasury_cash", 0)))),
        }

    def plan_tournament_trip(
        fid: str, *, person_ref: str, tournament_ref: str, host_place: str,
        registration_closes_on: str, competition_date: str, entry_fee_cash: int,
        arrival_lead_hours_min: int, arrival_lead_hours_max: int,
        host_cash_per_person_day: int, minimum_host_days: int,
    ) -> dict[str, Any]:
        """Schedule a future departure instead of parking entrants at the host for months.

        Registration opening is notice/planning time.  A distant faction does
        not reserve the fighter or fee until the real departure frontier, where
        current health, commitments, food, tolls and treasury are rechecked.
        """
        try:
            _fpath, faction = load_faction(fid)
        except (KeyError, FileNotFoundError, ValueError):
            return {"result": "owner_unresolved"}
        source_place = str(faction.get("headquarters") or "")
        if not source_place or not host_place or source_place == host_place:
            return {"result": "travel_not_required"}
        try:
            close_at = datetime.fromisoformat(
                str(registration_closes_on) + ("T18:00:00" if len(str(registration_closes_on)) == 10 else "")
            )
        except ValueError:
            return {"result": "registration_close_invalid"}
        lo = max(0, int(arrival_lead_hours_min)); hi = max(lo, int(arrival_lead_hours_max))
        span = hi - lo
        lead = lo + (stable_permille("tournament-arrival-lead", tournament_ref, fid, person_ref) * span // 999 if span > 0 else 0)
        target_arrival = close_at - timedelta(hours=lead)
        try:
            safe = latest_safe_departure(
                world_seed=world_seed, not_before=at + timedelta(minutes=1), target_arrival=target_arrival,
                start=source_place, end=host_place, mode="foot",
            )
        except (KeyError, ValueError):
            return {"result": "no_registered_route"}
        if not bool(safe.get("reachable", False)):
            return {
                "result": "cannot_arrive_before_registration_close",
                "earliest_arrival_at": str(safe.get("earliest_arrival_at") or ""),
                "registration_closes_at": close_at.isoformat(),
            }
        departure_at = datetime.fromisoformat(str(safe.get("departure_at")))
        event_id = f"tournament_trip_departure:{tournament_ref}:{person_ref}"
        pending_one_off_events.append({
            "event_id": event_id, "kind": "tournament_trip_departure",
            "due_at": departure_at.isoformat(), "owner_ref": person_ref,
            "faction_ref": fid, "person_ref": person_ref,
            "tournament_ref": tournament_ref, "host_place": host_place,
            "registration_closes_on": registration_closes_on,
            "competition_date": competition_date, "entry_fee_cash": max(0, int(entry_fee_cash)),
            "host_cash_per_person_day": max(0, int(host_cash_per_person_day)),
            "minimum_host_days": max(1, int(minimum_host_days)),
            "requires_player_decision": False,
        })
        return {
            "result": "departure_planned", "person_ref": person_ref,
            "departure_at": departure_at.isoformat(), "target_arrival_at": target_arrival.isoformat(),
        }

    def start_tournament_trip(
        fid: str, *, person_ref: str, tournament_ref: str, host_place: str,
        registration_closes_on: str, entry_fee_cash: int,
        host_cash_per_person_day: int, minimum_host_days: int,
    ) -> dict[str, Any]:
        """Start one real nonteleporting faction-sponsored tournament journey.

        The sponsoring faction reserves the entrant fee at departure so many
        simultaneous travelers cannot oversubscribe the same treasury.  The
        reserved fee enters the tournament only after lawful physical arrival
        and registration; otherwise it is refunded to the sponsor.
        """
        nonlocal commitments_state, world_history
        try:
            fpath, faction = load_faction(fid); rpath, roster = load_roster(fid); ipath, inventory = load_inventory(fid)
        except (KeyError, FileNotFoundError, ValueError):
            return {"result": "owner_unresolved"}
        source_place = str(faction.get("headquarters") or "")
        if not source_place or not host_place or source_place == host_place:
            return {"result": "travel_not_required"}
        rows = roster.get("people", []) if isinstance(roster, Mapping) else []
        person = next((p for p in rows if isinstance(p, Mapping) and p.get("person_id") == person_ref), None) if isinstance(rows, list) else None
        if not isinstance(person, Mapping) or person_ref == player_ref:
            return {"result": "entrant_unavailable"}
        if person_ref in unavailable_person_refs() or person_place(person, home_place=source_place, home_site_ref=str(faction.get("local_site_ref") or "")) != source_place:
            return {"result": "entrant_unavailable"}
        health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
        if health.get("status") in {"dead", "incapacitated"} or bool(person.get("retired_from_field", False)):
            return {"result": "entrant_ineligible"}
        try:
            plan = travel_plan(world_seed=world_seed, start_at=at, start=source_place, end=host_place, mode="foot")
        except (KeyError, ValueError):
            return {"result": "no_registered_route"}
        try:
            close_at = datetime.fromisoformat(
                str(registration_closes_on) + ("T18:00:00" if len(str(registration_closes_on)) == 10 else "")
            )
        except ValueError:
            close_at = at
        arrival_at = datetime.fromisoformat(str(plan.get("arrival_at")))
        if arrival_at > close_at:
            return {"result": "cannot_arrive_before_registration_close"}
        travel_days = max(1, (int(float(plan.get("travel_hours", 0)) * 1000) + 23999) // 24000)
        food_need = travel_days * 2
        if max(0, int(inventory.get("food_ration_days", 0))) < food_need:
            return {"result": "insufficient_travel_provisions"}
        toll = max(0, int(plan.get("toll_cash", 0))) * 2
        fee = max(0, int(entry_fee_cash))
        host_reserve = max(0, int(host_cash_per_person_day)) * max(1, int(minimum_host_days))
        transport = inventory.get("transport_assets", {}) if isinstance(inventory.get("transport_assets"), Mapping) else {}
        quote = monthly_upkeep_quote(
            faction,
            riding_horses=max(0, int(transport.get("riding_horses", 0))),
            pack_animals=max(0, int(transport.get("pack_animals", 0))),
        )
        policy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
        reserve_months = max(2, int(policy.get("reserve_cash_months", 6)))
        reserve_floor = max(0, int(quote.get("total_cash", 0))) * reserve_months
        treasury = max(0, int(faction.get("treasury_cash", 0)))
        if treasury - toll - fee - host_reserve < reserve_floor:
            return {"result": "entry_and_travel_cash_reserved"}
        op_ref = f"tournament_trip:{tournament_ref}:{person_ref}"
        deployments = deployments_state.setdefault("deployments", {})
        if not isinstance(deployments, dict) or op_ref in deployments:
            return {"result": "trip_already_active"}
        try:
            commitments_state = reserve_resources(
                commitments_state, resources=[("person", person_ref, fid)], actor_ref=person_ref,
                owner_ref=fid, activity_ref=op_ref, activity_kind="tournament_trip",
                started_at=at_iso, location_ref=source_place,
            )
        except ValueError:
            return {"result": "entrant_unavailable"}
        inventory["food_ration_days"] = max(0, int(inventory.get("food_ration_days", 0))) - food_need
        faction["treasury_cash"] = treasury - toll - fee - host_reserve
        if toll > 0:
            source_region = place_region.get(source_place)
            if isinstance(source_region, str):
                try:
                    mpath, market = load_market(source_region); market["cash_pool"] = max(0, int(market.get("cash_pool", 0))) + toll
                    writes[mpath] = market; market_cache[source_region] = (mpath, market)
                except FileNotFoundError:
                    # Never destroy a toll because a market owner is absent.
                    faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + toll
                    toll = 0
        deployments[op_ref] = {
            "deployment_ref": op_ref, "faction_ref": fid, "operation_kind": "tournament_travel",
            "tournament_ref": tournament_ref, "participant_refs": [person_ref],
            "source_place_ref": source_place, "source_site_ref": str(faction.get("local_site_ref") or ""), "target_place_ref": host_place,
            "started_at": at_iso, "arrival_at": arrival_at.isoformat(), "travel_hours": float(plan.get("travel_hours", 0)),
            "route_refs": list(plan.get("edges", [])), "status": "traveling_outbound",
            "entry_fee_reserved_cash": fee,
            "host_spend_reserved_cash": host_reserve,
        }
        pause_people_for_commitment(fid, [person_ref])
        writes[_DEPLOYMENTS_PATH] = deployments_state; writes[_COMMITMENTS_PATH] = commitments_state
        writes[fpath] = faction; writes[ipath] = inventory; faction_cache[fid] = (fpath, faction); inventory_cache[fid] = (ipath, inventory)
        pending_one_off_events.append({
            "event_id": f"tournament_travel_arrival:{op_ref}", "kind": "tournament_travel_arrival",
            "due_at": arrival_at.isoformat(), "owner_ref": op_ref, "requires_player_decision": False,
        })
        world_history = record_event(
            world_history, at=at_iso, kind="tournament_travel_departed", faction_ref=fid,
            person_ref=person_ref, tournament_ref=tournament_ref, entry_fee_reserved_cash=fee,
            host_spend_reserved_cash=host_reserve,
        )
        writes[_WORLD_HISTORY_PATH] = world_history
        return {
            "result": "travel_started", "trip_ref": op_ref, "person_ref": person_ref,
            "arrival_at": arrival_at.isoformat(), "entry_fee_reserved_cash": fee,
            "host_spend_reserved_cash": host_reserve,
        }

    def _tournament_delegate_roles(person: Mapping[str, Any]) -> tuple[bool, bool]:
        offices = {str(x) for x in person.get("standing_offices", []) if isinstance(x, str)} if isinstance(person.get("standing_offices"), list) else set()
        leader = "leader" in offices
        senior_offices = {"leader", "deputy_leader", "chief_instructor", "chief_physician", "chief_steward", "treasurer", "quartermaster"}
        grade = str(person.get("membership_grade") or "")
        senior = bool(offices & senior_offices) or grade in {"elder", "elite"}
        return leader, senior

    def _add_tournament_delegation_presence(
        tournament_ref: str, faction_ref: str, *, entrant_refs: Sequence[str] = (),
        spectator_refs: Sequence[str] = (), leader_refs: Sequence[str] = (),
        senior_refs: Sequence[str] = (),
    ) -> None:
        registry = tournament_state.get("tournaments", {}) if isinstance(tournament_state, Mapping) else {}
        tournament = registry.get(tournament_ref) if isinstance(registry, Mapping) else None
        if not isinstance(tournament, dict) or not faction_ref:
            return
        merged = tournament_merge_delegation_presence(
            tournament, faction_ref=faction_ref, camp=faction_camp(faction_ref),
            entrant_refs=entrant_refs, spectator_refs=spectator_refs,
            leader_refs=leader_refs, senior_refs=senior_refs,
        )
        registry[tournament_ref] = merged
        writes[_TOURNAMENTS_PATH] = tournament_state

    def fund_public_tournament_attendance(
        tournament_ref: str, *, tournament_kind: str, attendance_date: str,
        delegate_count: int,
    ) -> dict[str, int]:
        """Charge aggregate public spectators once per event day into the purse.

        Civilians remain aggregate. Their paid ticket cash is conserved out of
        the host regional market cash pool and into tournament prize escrow.
        Venue capacity limits official spectators; inability to pay reduces paid
        attendance instead of minting ticket revenue. Repeated competition
        sessions on the same date reuse the stored daily attendance receipt.
        """
        registry=tournament_state.get("tournaments",{}) if isinstance(tournament_state,Mapping) else {}
        tournament=registry.get(tournament_ref) if isinstance(registry,Mapping) else None
        if not isinstance(tournament,Mapping):
            return {"public_spectator_count":0,"public_spectator_overflow":0,"public_ticket_cash":0,"venue_capacity":0}
        receipts=tournament.get("public_attendance_by_date",{}) if isinstance(tournament.get("public_attendance_by_date"),Mapping) else {}
        existing=receipts.get(attendance_date) if isinstance(receipts,Mapping) else None
        if isinstance(existing,Mapping):
            return {
                "public_spectator_count":max(0,int(existing.get("public_spectator_count",0))),
                "public_spectator_overflow":max(0,int(existing.get("public_spectator_overflow",0))),
                "public_ticket_cash":max(0,int(existing.get("public_ticket_cash",0))),
                "venue_capacity":max(0,int(existing.get("venue_capacity",0))),
            }
        profile=tournament_event_profile(tournament_kind)
        venue_ref=str(tournament.get("venue_site_ref") or "")
        venue_row=site_rows.get(venue_ref) if isinstance(site_rows,Mapping) else None
        venue_capacity=max(0,int(venue_row.get("capacity",0))) if isinstance(venue_row,Mapping) else 0
        host_place=str(tournament.get("host_place_ref") or "")
        civilian_places=civilian_state.get("places",{}) if isinstance(civilian_state,Mapping) else {}
        host_civilians=civilian_places.get(host_place,{}) if isinstance(civilian_places,Mapping) else {}
        host_population=max(0,int(host_civilians.get("current_population",0))) if isinstance(host_civilians,Mapping) else 0
        demand_permille=25 if tournament_kind=="great_jianghu_tournament" else 10
        demand=host_population*demand_permille//1000
        seat_limit=max(0,venue_capacity-max(0,int(delegate_count))) if venue_capacity>0 else 0
        possible=min(seat_limit,demand)
        ticket=max(0,int(profile.get("public_spectator_ticket_cash_per_day",0)))
        paid=possible; ticket_cash=0
        host_region=str(tournament.get("host_region") or "")
        if ticket>0 and possible>0 and host_region:
            try:
                mpath,market=load_market(host_region)
            except FileNotFoundError:
                market=None; mpath=""
            if isinstance(market,dict):
                paid=min(possible,max(0,int(market.get("cash_pool",0)))//ticket)
                ticket_cash=paid*ticket
                market["cash_pool"]=max(0,int(market.get("cash_pool",0)))-ticket_cash
                writes[mpath]=market; market_cache[host_region]=(mpath,market)
            else:
                paid=0
        elif ticket>0:
            paid=0
        overflow=max(0,demand-paid)
        updated=dict(tournament)
        if ticket_cash>0:
            updated=tournament_add_attendance_prize_cash(
                updated,amount_cash=ticket_cash,source_kind="public_spectator_ticket",
            )
        receipt={
            "public_spectator_count":paid,"public_spectator_overflow":overflow,
            "public_ticket_cash":ticket_cash,"venue_capacity":venue_capacity,
        }
        updated_receipts=dict(receipts) if isinstance(receipts,Mapping) else {}
        updated_receipts[attendance_date]=receipt
        updated["public_attendance_by_date"]=updated_receipts
        updated["peak_public_spectator_count"]=max(max(0,int(updated.get("peak_public_spectator_count",0))),paid)
        updated["peak_delegate_count"]=max(max(0,int(updated.get("peak_delegate_count",0))),max(0,int(delegate_count)))
        registry[tournament_ref]=updated
        writes[_TOURNAMENTS_PATH]=tournament_state
        return dict(receipt)


    def plan_tournament_delegation_trip(
        fid: str, *, candidate_refs: Sequence[str], tournament_ref: str, host_place: str,
        competition_date: str, convergence_days_before: int, host_cash_per_person_day: int,
        delegate_ticket_cash_per_day: int, minimum_host_days: int,
    ) -> dict[str, Any]:
        """Plan one real spectator/representative delegation per faction.

        The plan does not reserve people or money.  It schedules a departure
        frontier close to the official convergence window; that frontier
        rechecks the exact roster, commitments, food, tolls and treasury.
        """
        try:
            _fpath, faction = load_faction(fid)
        except (KeyError, FileNotFoundError, ValueError):
            return {"result": "owner_unresolved"}
        refs = [str(x) for x in candidate_refs if isinstance(x, str) and x and str(x) != player_ref]
        if not refs:
            return {"result": "no_delegates_nominated"}
        source_place = str(faction.get("headquarters") or "")
        if not source_place or not host_place:
            return {"result": "travel_endpoint_unresolved"}
        try:
            competition_at = datetime.fromisoformat(str(competition_date) + ("T09:00:00" if len(str(competition_date)) == 10 else ""))
        except ValueError:
            return {"result": "competition_date_invalid"}
        convergence_start = competition_at - timedelta(days=max(0, int(convergence_days_before)))
        lead_hours = 8 + stable_permille("tournament-delegation-arrival-lead", tournament_ref, fid) * 28 // 999
        target_arrival = convergence_start - timedelta(hours=lead_hours)
        if source_place == host_place:
            departure_at = max(at + timedelta(minutes=1), target_arrival)
        else:
            try:
                safe = latest_safe_departure(
                    world_seed=world_seed, not_before=at + timedelta(minutes=1), target_arrival=target_arrival,
                    start=source_place, end=host_place, mode="foot",
                )
            except (KeyError, ValueError):
                return {"result": "no_registered_route"}
            if not bool(safe.get("reachable", False)):
                return {
                    "result": "cannot_arrive_before_tournament",
                    "faction_ref": fid,
                    "earliest_arrival_at": str(safe.get("earliest_arrival_at") or ""),
                    "competition_at": competition_at.isoformat(),
                }
            departure_at = datetime.fromisoformat(str(safe.get("departure_at")))
        event_id = f"tournament_delegation_departure:{tournament_ref}:{fid}"
        pending_one_off_events.append({
            "event_id": event_id, "kind": "tournament_delegation_departure",
            "due_at": departure_at.isoformat(), "owner_ref": fid, "faction_ref": fid,
            "candidate_refs": refs, "tournament_ref": tournament_ref,
            "host_place": host_place, "competition_date": competition_date,
            "latest_arrival_at": competition_at.isoformat(),
            "host_cash_per_person_day": max(0, int(host_cash_per_person_day)),
            "delegate_ticket_cash_per_day": max(0, int(delegate_ticket_cash_per_day)),
            "minimum_host_days": max(1, int(minimum_host_days)),
            "requires_player_decision": False,
        })
        return {
            "result": "delegation_departure_planned", "faction_ref": fid,
            "candidate_count": len(refs), "departure_at": departure_at.isoformat(),
            "target_arrival_at": target_arrival.isoformat(),
        }

    def start_tournament_delegation_trip(
        fid: str, *, candidate_refs: Sequence[str], tournament_ref: str, host_place: str,
        host_cash_per_person_day: int, delegate_ticket_cash_per_day: int, minimum_host_days: int,
        latest_arrival_at: str = "",
    ) -> dict[str, Any]:
        """Commit the largest currently affordable named spectator delegation."""
        nonlocal commitments_state, world_history
        try:
            fpath, faction = load_faction(fid); rpath, roster = load_roster(fid); ipath, inventory = load_inventory(fid)
        except (KeyError, FileNotFoundError, ValueError):
            return {"result": "owner_unresolved"}
        source_place = str(faction.get("headquarters") or "")
        source_site = str(faction.get("local_site_ref") or "")
        if not source_place or not host_place:
            return {"result": "travel_endpoint_unresolved"}
        rows = roster.get("people", []) if isinstance(roster, Mapping) else []
        by_ref = {str(p.get("person_id")): p for p in rows if isinstance(p, Mapping) and isinstance(p.get("person_id"), str)} if isinstance(rows, list) else {}
        blocked = unavailable_person_refs()
        refs: list[str] = []
        leader_refs: list[str] = []
        senior_refs: list[str] = []
        for ref in [str(x) for x in candidate_refs if isinstance(x, str)]:
            person = by_ref.get(ref)
            if not isinstance(person, Mapping) or ref == player_ref or ref in blocked:
                continue
            if person_place(person, home_place=source_place, home_site_ref=source_site) != source_place:
                continue
            health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
            if health.get("status") in {"dead", "incapacitated"}:
                continue
            if at.year - int(person.get("birth_year", at.year)) < 14:
                continue
            refs.append(ref)
            is_leader, is_senior = _tournament_delegate_roles(person)
            if is_leader:
                leader_refs.append(ref)
            if is_senior:
                senior_refs.append(ref)
        if not refs:
            return {"result": "delegation_unavailable"}
        local = source_place == host_place
        if local:
            plan = {"arrival_at": at.isoformat(), "travel_hours": 1.0, "toll_cash": 0, "edges": []}
        else:
            try:
                plan = travel_plan(world_seed=world_seed, start_at=at, start=source_place, end=host_place, mode="foot")
            except (KeyError, ValueError):
                return {"result": "no_registered_route"}
        if latest_arrival_at:
            try:
                planned_arrival = datetime.fromisoformat(str(plan.get("arrival_at")))
                latest_arrival = datetime.fromisoformat(str(latest_arrival_at))
            except (TypeError, ValueError):
                return {"result": "tournament_arrival_deadline_invalid"}
            if planned_arrival >= latest_arrival:
                return {
                    "result": "cannot_arrive_before_tournament",
                    "arrival_at": planned_arrival.isoformat(),
                    "latest_arrival_at": latest_arrival.isoformat(),
                }
        travel_days = 0 if local else max(1, (int(float(plan.get("travel_hours", 0)) * 1000) + 23999) // 24000)
        transport = inventory.get("transport_assets", {}) if isinstance(inventory.get("transport_assets"), Mapping) else {}
        quote = monthly_upkeep_quote(
            faction, riding_horses=max(0, int(transport.get("riding_horses", 0))),
            pack_animals=max(0, int(transport.get("pack_animals", 0))),
        )
        policy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
        reserve_floor = max(0, int(quote.get("total_cash", 0))) * max(2, int(policy.get("reserve_cash_months", 6)))
        treasury = max(0, int(faction.get("treasury_cash", 0)))
        food_before = max(0, int(inventory.get("food_ration_days", 0)))
        toll_each = 0 if local else max(0, int(plan.get("toll_cash", 0))) * 2
        # Event/lodging spending is real for local and traveling delegations.
        # Travel tolls are zero for locals, but attending a major gathering is
        # not free just because the faction already lives in the host city.
        host_each = max(0, int(host_cash_per_person_day)) * max(1, int(minimum_host_days))
        ticket_each = max(0, int(delegate_ticket_cash_per_day)) * max(1, int(minimum_host_days))
        # There is no delegation cap.  If the originally nominated party is too
        # expensive today, shrink only from the lowest-priority tail until the
        # remaining real party is supportable.
        while refs:
            count = len(refs)
            food_need = count * travel_days * 2
            cash_need = count * (toll_each + host_each + ticket_each)
            if food_before >= food_need and treasury - cash_need >= reserve_floor:
                break
            removed = refs.pop()
            if removed in leader_refs:
                leader_refs.remove(removed)
            if removed in senior_refs:
                senior_refs.remove(removed)
        if not refs:
            return {"result": "delegation_not_affordable"}
        food_need = len(refs) * travel_days * 2
        toll = len(refs) * toll_each
        host_reserve = len(refs) * host_each
        delegate_ticket_reserve = len(refs) * ticket_each
        op_ref = f"tournament_delegation:{tournament_ref}:{fid}"
        deployments = deployments_state.setdefault("deployments", {})
        if not isinstance(deployments, dict) or op_ref in deployments:
            return {"result": "delegation_already_active"}
        try:
            commitments_state = reserve_resources(
                commitments_state, resources=[("person", ref, fid) for ref in refs],
                actor_ref=leader_refs[0] if leader_refs else refs[0], owner_ref=fid,
                activity_ref=op_ref, activity_kind="tournament_delegation",
                started_at=at_iso, location_ref=source_place,
            )
        except ValueError:
            return {"result": "delegation_unavailable"}
        inventory["food_ration_days"] = food_before - food_need
        faction["treasury_cash"] = treasury - toll - host_reserve - delegate_ticket_reserve
        if toll > 0:
            source_region = place_region.get(source_place)
            if isinstance(source_region, str):
                try:
                    mpath, market = load_market(source_region)
                    market["cash_pool"] = max(0, int(market.get("cash_pool", 0))) + toll
                    writes[mpath] = market; market_cache[source_region] = (mpath, market)
                except FileNotFoundError:
                    faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + toll
                    toll = 0
        local_host_spend = 0
        local_delegate_ticket = 0
        if local and host_reserve > 0:
            host_region = place_region.get(host_place)
            if isinstance(host_region, str):
                try:
                    mpath, market = load_market(host_region)
                    market["cash_pool"] = max(0, int(market.get("cash_pool", 0))) + host_reserve
                    writes[mpath] = market; market_cache[host_region] = (mpath, market)
                    local_host_spend = host_reserve
                except FileNotFoundError:
                    faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + host_reserve
                    host_reserve = 0
        if local and delegate_ticket_reserve > 0:
            registry = tournament_state.get("tournaments", {}) if isinstance(tournament_state, Mapping) else {}
            tournament = registry.get(tournament_ref) if isinstance(registry, Mapping) else None
            if isinstance(tournament, Mapping):
                funded = tournament_add_attendance_prize_cash(
                    tournament, amount_cash=delegate_ticket_reserve,
                    source_kind="faction_delegate_ticket",
                )
                registry[tournament_ref] = funded
                writes[_TOURNAMENTS_PATH] = tournament_state
                local_delegate_ticket = delegate_ticket_reserve
            else:
                faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + delegate_ticket_reserve
                delegate_ticket_reserve = 0
        deployment = {
            "deployment_ref": op_ref, "faction_ref": fid, "operation_kind": "tournament_delegation",
            "tournament_ref": tournament_ref, "participant_refs": refs, "leader_refs": leader_refs, "senior_refs": senior_refs,
            "source_place_ref": source_place, "source_site_ref": source_site,
            "target_place_ref": host_place, "started_at": at_iso,
            "arrival_at": str(plan.get("arrival_at")), "travel_hours": float(plan.get("travel_hours", 1.0)),
            "route_refs": list(plan.get("edges", [])), "status": "traveling_outbound" if not local else "at_tournament",
            "host_spend_reserved_cash": 0 if local else host_reserve,
            "host_spend_per_person_cash": host_each,
            "host_spend_cash": local_host_spend,
            "delegate_ticket_reserved_cash": 0 if local else delegate_ticket_reserve,
            "delegate_ticket_per_person_cash": ticket_each,
            "delegate_ticket_cash": local_delegate_ticket,
        }
        deployments[op_ref] = deployment
        pause_people_for_commitment(fid, refs)
        writes[_DEPLOYMENTS_PATH] = deployments_state; writes[_COMMITMENTS_PATH] = commitments_state
        writes[fpath] = faction; writes[ipath] = inventory
        faction_cache[fid] = (fpath, faction); inventory_cache[fid] = (ipath, inventory)
        if local:
            venue = str((tournament_state.get("tournaments", {}) or {}).get(tournament_ref, {}).get("venue_site_ref") or _tournament_venue_site(local_sites, host_place) or host_place)
            if isinstance(rows, list):
                for i, raw in enumerate(rows):
                    if isinstance(raw, Mapping) and str(raw.get("person_id")) in refs:
                        person = copy.deepcopy(dict(raw)); person["location_ref"] = venue; rows[i] = person
                roster["people"] = rows; writes[rpath] = roster; roster_cache[fid] = (rpath, roster)
            _add_tournament_delegation_presence(
                tournament_ref, fid, spectator_refs=refs, leader_refs=leader_refs, senior_refs=senior_refs,
            )
        else:
            pending_one_off_events.append({
                "event_id": f"tournament_delegation_arrival:{op_ref}", "kind": "tournament_delegation_arrival",
                "due_at": str(plan.get("arrival_at")), "owner_ref": op_ref, "requires_player_decision": False,
            })
        world_history = record_event(
            world_history, at=at_iso, kind="tournament_delegation_departed" if not local else "tournament_delegation_present",
            faction_ref=fid, tournament_ref=tournament_ref, participant_count=len(refs), leader_count=len(leader_refs),
            senior_count=len(senior_refs),
            host_spend_reserved_cash=0 if local else host_reserve, host_spend_cash=local_host_spend,
            delegate_ticket_reserved_cash=0 if local else delegate_ticket_reserve,
            delegate_ticket_cash=local_delegate_ticket,
        )
        writes[_WORLD_HISTORY_PATH] = world_history
        return {
            "result": "delegation_present" if local else "delegation_departed", "delegation_ref": op_ref,
            "participant_count": len(refs), "leader_count": len(leader_refs), "senior_count": len(senior_refs), "toll_cash": toll,
            "host_spend_reserved_cash": 0 if local else host_reserve, "host_spend_cash": local_host_spend,
            "delegate_ticket_reserved_cash": 0 if local else delegate_ticket_reserve,
            "delegate_ticket_cash": local_delegate_ticket,
        }

    def schedule_tournament_returns(tournament_ref: str) -> int:
        """Turn all surviving tournament-trip deployments into return journeys."""
        nonlocal commitments_state
        deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
        if not isinstance(deployments, dict):
            return 0
        count = 0
        for op_ref, raw in list(deployments.items()):
            if not isinstance(raw, Mapping) or raw.get("operation_kind") not in {"tournament_travel", "tournament_delegation"} or raw.get("tournament_ref") != tournament_ref:
                continue
            op = copy.deepcopy(dict(raw)); refs = [str(x) for x in op.get("participant_refs", []) if isinstance(x, str)]
            alive_refs: list[str] = []
            for ref in refs:
                try:
                    _ofid, _rp, _ros, _ord, person = load_person_ref(ref)
                except (KeyError, FileNotFoundError, ValueError):
                    continue
                health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
                if health.get("status") != "dead": alive_refs.append(ref)
            if not alive_refs:
                commitments_state = release_resources(commitments_state, activity_ref=str(op_ref)); deployments.pop(op_ref, None); continue
            return_at = at + timedelta(hours=max(1.0, float(op.get("travel_hours", 24.0))))
            op["participant_refs"] = alive_refs; op["status"] = "traveling_return"; op["return_arrival_at"] = return_at.isoformat(); deployments[op_ref] = op
            pending_one_off_events.append({"event_id": f"tournament_return_arrival:{op_ref}", "kind": "tournament_return_arrival", "due_at": return_at.isoformat(), "owner_ref": op_ref, "requires_player_decision": False})
            count += len(alive_refs)
        writes[_DEPLOYMENTS_PATH] = deployments_state; writes[_COMMITMENTS_PATH] = commitments_state
        return count

    def execute_friendly_aid(fid: str, target_fid: str) -> dict[str, Any]:
        """Transfer bounded real silver between autonomous NPC factions."""
        nonlocal world_history
        if not target_fid or target_fid == fid:
            return {"result": "no_aid_target"}
        # House Tang's treasury remains protected player authority. NPC factions
        # may still aid each other without creating silver from a relationship.
        if fid == "house_tang" or target_fid == "house_tang":
            return {"result": "player_faction_diplomacy_protected"}
        try:
            fpath, faction = load_faction(fid); tfpath, target = load_faction(target_fid)
            _ipath, inventory = load_inventory(fid); _tipath, target_inventory = load_inventory(target_fid)
        except (KeyError, FileNotFoundError, ValueError):
            return {"result": "aid_target_unresolved"}
        def monthly_cash_need(row: Mapping[str, Any], inv: Mapping[str, Any]) -> int:
            transport = inv.get("transport_assets", {}) if isinstance(inv.get("transport_assets"), Mapping) else {}
            quote = monthly_upkeep_quote(
                row, riding_horses=int(transport.get("riding_horses", 0)),
                pack_animals=int(transport.get("pack_animals", 0)),
            )
            return max(1, int(quote.get("total_cash", 1)))
        src_monthly = monthly_cash_need(faction, inventory); dst_monthly = monthly_cash_need(target, target_inventory)
        src_cash = max(0, int(faction.get("treasury_cash", 0))); dst_cash = max(0, int(target.get("treasury_cash", 0)))
        reserve_floor = src_monthly * 8
        spendable = max(0, src_cash - reserve_floor)
        # Aid is a response to a real weaker reserve position, not a random gift.
        need = max(0, dst_monthly * 4 - dst_cash)
        if spendable <= 0 or need <= 0:
            return {"result": "target_not_in_need"}
        amount = min(need, max(100, spendable // 4), max(250, src_monthly * 2))
        amount = max(0, int(amount))
        if amount <= 0:
            return {"result": "aid_not_affordable"}
        faction["treasury_cash"] = src_cash - amount
        target["treasury_cash"] = dst_cash + amount
        faction_cache[fid] = (fpath, faction); faction_cache[target_fid] = (tfpath, target)
        writes[fpath] = faction; writes[tfpath] = target
        apply_directed_relation_event(fid, target_fid, "silver_aid_given")
        apply_directed_relation_event(target_fid, fid, "silver_aid_received")
        world_history = record_event(
            world_history, at=at_iso, kind="faction_aid", faction_ref=fid,
            target_faction_ref=target_fid, cash=amount,
        )
        writes[_WORLD_HISTORY_PATH] = world_history
        return {"result": "aid_transferred", "target_faction_ref": target_fid, "cash": amount}

    def start_monthly_merchant_trade(fid: str) -> dict[str, Any]:
        """Start one conserved cross-region merchant caravan when a real spread exists.

        The trade enterprise supplies organizational capacity only.  The actual
        shipment spends faction silver into a finite source market, commits real
        members, consumes round-trip provisions and tolls, travels on a registered
        route, then sells against finite destination stock/cash before returning.
        """
        nonlocal commitments_state, world_history
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
        transport = inventory.get("transport_assets", {}) if isinstance(inventory.get("transport_assets"), Mapping) else {}
        pack_animals = max(0, int(transport.get("pack_animals", 0)))
        if pack_animals <= 0:
            return {"result": "no_pack_transport"}
        try:
            smpath, source_market = load_market(source_region)
        except FileNotFoundError:
            return {"result": "source_market_unavailable"}

        policy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
        transport_quote = monthly_upkeep_quote(
            faction,
            riding_horses=max(0, int(transport.get("riding_horses", 0))),
            pack_animals=pack_animals,
        )
        reserve_months = max(2, int(policy.get("reserve_cash_months", 6)))
        reserve_floor = max(1, int(transport_quote.get("total_cash", 1))) * reserve_months
        treasury = max(0, int(faction.get("treasury_cash", 0)))
        monthly_cap = max(0, scale * efficiency // 1000)
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
                toll_cash = max(0, int(route.get("toll_cash", 0))) * 2
                capital_after_toll = max(0, spendable - toll_cash)
                if capital_after_toll < buy_unit:
                    continue
                destination_cash = max(0, int(destination_market.get("cash_pool", 0)))
                max_by_dest_cash = destination_cash // max(1, sell_unit)
                # One pack animal can support a bounded aggregate shipment. The
                # item unit is already the economy's registered physical unit;
                # this cap exists to stop a tiny faction moving a regional stock
                # pile merely because it has enough silver.
                max_by_transport = max(1, pack_animals * 40)
                quantity = min(available_qty, capital_after_toll // buy_unit, max_by_dest_cash, max_by_transport)
                if quantity <= 0:
                    continue
                purchase_cash = buy_unit * quantity
                expected_sale = sell_unit * quantity
                expected_profit = expected_sale - purchase_cash - toll_cash
                if expected_profit <= max(50, purchase_cash * 20 // 1000):
                    continue
                speed = max(1.0, float(travel_data.get("mode_speed_km_per_day", {}).get("convoy", 24))) if isinstance(travel_data, Mapping) else 24.0
                terrain_map = travel_data.get("terrain_time_milli", {}) if isinstance(travel_data, Mapping) else {}
                road_map = travel_data.get("road_time_milli", {}) if isinstance(travel_data, Mapping) else {}
                terrain_milli = int(terrain_map.get(str(route.get("terrain", "plain")), 1000)) if isinstance(terrain_map, Mapping) else 1000
                road_milli = int(road_map.get(str(route.get("road_quality", "maintained")), 1000)) if isinstance(road_map, Mapping) else 1000
                required_hours = max(1, int((float(route.get("distance_km", 0)) * 24.0 / speed * terrain_milli * road_milli / 1_000_000.0) + float(route.get("fixed_delay_hours", 0)) + 0.999999))
                candidates.append({
                    "route_ref": str(route_id), "destination_place_ref": destination_place,
                    "destination_region": destination_region, "destination_market_path": dmpath,
                    "item_ref": item_ref, "quantity": quantity, "buy_unit": buy_unit, "sell_unit": sell_unit,
                    "purchase_cash": purchase_cash, "expected_sale_cash": expected_sale,
                    "expected_profit_cash": expected_profit, "toll_cash": toll_cash,
                    "required_hours": required_hours,
                })
        if not candidates:
            return {"result": "no_profitable_cross_region_trade"}
        candidates.sort(key=lambda row: (-int(row["expected_profit_cash"]), int(row["required_hours"]), str(row["route_ref"]), str(row["item_ref"])))
        chosen = candidates[0]

        blocked = unavailable_person_refs()
        people = [
            p for p in usable_martial_people(roster, exclude_committed=blocked)
            if person_place(p, home_place=source_place, home_site_ref=str(faction.get("local_site_ref") or "")) == source_place and str(p.get("person_id") or "") != player_ref
            and at.year - int(p.get("birth_year", at.year)) >= 16 and not bool(p.get("retired_from_field", False))
        ]
        people.sort(key=lambda p: (
            -int((p.get("professional_skills") or {}).get("commerce", 0)) if isinstance(p.get("professional_skills"), Mapping) else 0,
            -person_combat_index(p), str(p.get("person_id", "")),
        ))
        desired_people = min(max(1, 1 + level // 2), max(1, pack_animals))
        participants = [str(p["person_id"]) for p in people[:desired_people] if isinstance(p.get("person_id"), str)]
        if not participants:
            return {"result": "no_available_trade_staff"}
        travel_days = max(1, (int(chosen["required_hours"]) + 23) // 24)
        food_need = len(participants) * travel_days * 2
        food_before = max(0, int(inventory.get("food_ration_days", 0)))
        if food_before < food_need:
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
                resources=[("person", ref, fid) for ref in participants] + [("transport_asset", f"{fid}:merchant_convoy", fid)],
                actor_ref=participants[0], owner_ref=fid, activity_ref=movement_ref,
                activity_kind="merchant_trade", started_at=at_iso, location_ref=source_place,
            )
        except ValueError:
            return {"result": "trade_resources_unavailable"}
        source_market = copy.deepcopy(dict(purchased["market_state_after"]))
        faction["treasury_cash"] = int(purchased["buyer_cash_after"]) - toll_cash
        inventory["food_ration_days"] = food_before - food_need
        # Route tolls remain tracked currency. Split round-trip tolls between
        # the two surrounding market authorities rather than deleting silver.
        if toll_cash > 0:
            source_toll = toll_cash // 2; destination_toll = toll_cash - source_toll
            source_market["cash_pool"] = max(0, int(source_market.get("cash_pool", 0))) + source_toll
            dmpath, destination_market = load_market(str(chosen["destination_region"]))
            destination_market["cash_pool"] = max(0, int(destination_market.get("cash_pool", 0))) + destination_toll
            writes[dmpath] = destination_market; market_cache[str(chosen["destination_region"])] = (dmpath, destination_market)
        movements[movement_ref] = {
            "movement_ref": movement_ref, "movement_kind": "merchant_trade", "route_ref": route_ref,
            "origin_place_ref": source_place, "destination_place_ref": str(chosen["destination_place_ref"]),
            "source_region": source_region, "destination_region": str(chosen["destination_region"]),
            "item_ref": item_ref, "quantity": quantity,
            "cargo_value_cash": int(chosen["purchase_cash"]), "beneficiary_ref": fid,
            "participant_refs": participants, "started_at": at_iso, "elapsed_hours": 0,
            "required_hours": int(chosen["required_hours"]), "known_escort_count": len(participants),
            "toll_cash": toll_cash, "purchase_cash": int(chosen["purchase_cash"]),
            "expected_sale_cash": int(chosen["expected_sale_cash"]), "trade_leg": "outbound",
            "status": "active", "repelled_outlaw_refs": [],
        }
        pause_people_for_commitment(fid, participants)
        writes[fpath] = faction; faction_cache[fid] = (fpath, faction)
        writes[ipath] = inventory; inventory_cache[fid] = (ipath, inventory)
        writes[smpath] = source_market; market_cache[source_region] = (smpath, source_market)
        writes[_COMMITMENTS_PATH] = commitments_state; writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
        world_history = record_event(
            world_history, at=at_iso, kind="merchant_trade_departed", faction_ref=fid,
            route_ref=route_ref, destination_region=str(chosen["destination_region"]),
            item_ref=item_ref, quantity=quantity, purchase_cash=int(chosen["purchase_cash"]), toll_cash=toll_cash,
        )
        writes[_WORLD_HISTORY_PATH] = world_history
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
        faction, _summary = advance_faction_training_epoch(
            faction, roster, at_iso=at_iso,
            refresh_environment=False,
        )
        people = roster.get("people", []) if isinstance(roster, Mapping) else []
        if not isinstance(people, list):
            return
        snapshot = [copy.deepcopy(dict(x)) for x in people if isinstance(x, Mapping)]
        after_people: list[Any] = []
        for raw in people:
            if not isinstance(raw, Mapping) or raw.get("person_id") not in refs:
                after_people.append(raw)
                continue
            person = apply_institutional_training(raw, faction=faction, roster_people=snapshot)
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

    def load_person_ref(person_ref: str) -> tuple[str, str, dict[str, Any], int, dict[str, Any]]:
        bucket = hashlib.sha256(person_ref.encode("utf-8")).hexdigest()[:2]
        shard = read_json(f"state/martial-world/person-routes/{bucket}.json")
        route = shard.get("people", {}).get(person_ref) if isinstance(shard, Mapping) and isinstance(shard.get("people"), Mapping) else None
        if isinstance(route, list) and len(route) == 2 and isinstance(route[0], str) and isinstance(route[1], int):
            fid, ordinal = str(route[0]), int(route[1]); rpath, roster = load_roster(fid)
            rows = roster.get("people", []) if isinstance(roster, Mapping) else []
            if not isinstance(rows, list) or ordinal < 0 or ordinal >= len(rows) or not isinstance(rows[ordinal], Mapping) or rows[ordinal].get("person_id") != person_ref:
                raise ValueError("jianghu person route identity mismatch")
            return fid, rpath, roster, ordinal, copy.deepcopy(dict(rows[ordinal]))
        # Independent martial people deliberately have no faction route. They
        # remain exact current people so government, custody and future
        # recruitment can still find the same identity after a departure.
        rows = independent_state.get("people", []) if isinstance(independent_state, Mapping) else []
        if isinstance(rows, list):
            for ordinal, raw in enumerate(rows):
                if isinstance(raw, Mapping) and raw.get("person_id") == person_ref:
                    return "", _INDEPENDENTS_PATH, independent_state, ordinal, copy.deepcopy(dict(raw))
        raise KeyError(person_ref)

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
            faction, _summary = advance_faction_training_epoch(
                faction, roster, at_iso=at_iso,
                refresh_environment=False,
            )
            people = roster.get("people", []) if isinstance(roster, Mapping) else []
            if not isinstance(people, list):
                continue
            snapshot = [copy.deepcopy(dict(x)) for x in people if isinstance(x, Mapping)]
            after_people: list[Any] = []
            for person in people:
                if not isinstance(person, Mapping) or person.get("person_id") not in local_refs:
                    after_people.append(person)
                    continue
                caught = apply_institutional_training(person, faction=faction, roster_people=snapshot)
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
        # Temporarily expose the released after-image to availability checks so
        # training resumes only when no custody or other reservation remains.
        still_blocked = commitment_person_refs(released) | custody_person_refs()
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
                faction, _summary = advance_faction_training_epoch(
                    faction, roster, at_iso=at_iso,
                    refresh_environment=False,
                )
                people = roster.get("people", []) if isinstance(roster, Mapping) else []
                if not isinstance(people, list):
                    continue
                snapshot = [copy.deepcopy(dict(x)) for x in people if isinstance(x, Mapping)]
                after_people: list[Any] = []
                for person in people:
                    if not isinstance(person, Mapping) or person.get("person_id") not in local_refs:
                        after_people.append(person)
                        continue
                    caught = apply_institutional_training(person, faction=faction, roster_people=snapshot)
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

    def close_dead_current_authorities(dead_refs: Sequence[str]) -> None:
        """Close current authorities immediately for deaths at this frontier.

        Permanent kinship facts remain in family state, but current offices,
        courtships, custody and finite availability reservations cannot continue
        to treat a dead body as an active participant.
        """
        nonlocal family_state, commitments_state, custody_state, social_state
        dead = {str(x) for x in dead_refs if isinstance(x, str)}
        if not dead:
            return

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
                rows[cid] = row
        writes[_COMMITMENTS_PATH] = commitments_state

        # Current courtship/social authorities end at death.
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

        # A dead detainee leaves custody; a dead captor releases a living
        # detainee. Training resumption is delayed until all same-frontier
        # availability mutations have settled.
        prior_custody = [row for row in custody_state.get("records", []) if isinstance(row, Mapping)]
        custody_state["records"] = [
            row for row in prior_custody
            if str(row.get("person_ref")) not in dead and str(row.get("captor_ref")) not in dead
        ]
        released = {
            str(row.get("person_ref")) for row in prior_custody
            if str(row.get("captor_ref")) in dead
            and isinstance(row.get("person_ref"), str)
            and str(row.get("person_ref")) not in dead
        }
        if len(custody_state["records"]) != len(prior_custody):
            writes[_CUSTODY_PATH] = custody_state
            pending_training_resume_refs.update(released)

        # Family status and recognized hereditary succession are faction-local.
        by_faction: dict[str, set[str]] = {}
        for ref in sorted(dead):
            try:
                fid, _rpath, _roster, _ordinal, _person = load_person_ref(ref)
            except (KeyError, ValueError, FileNotFoundError):
                continue
            by_faction.setdefault(fid, set()).add(ref)
        for fid, local_dead in by_faction.items():
            fpath, faction = load_faction(fid)
            rpath, roster = load_roster(fid)
            people = roster.get("people", []) if isinstance(roster, Mapping) else []
            if not isinstance(people, list):
                continue
            family_state = apply_family_death_status(
                family_state, dead_refs=sorted(local_dead), faction_ref=fid,
                roster_people=[p for p in people if isinstance(p, Mapping)],
            )
            cleaned: list[Any] = []
            for raw in people:
                if isinstance(raw, Mapping) and str(raw.get("person_id")) in local_dead and raw.get("standing_offices"):
                    person = copy.deepcopy(dict(raw)); person["standing_offices"] = []; cleaned.append(person)
                else:
                    cleaned.append(raw)
            roster["people"] = cleaned
            succession = apply_recognized_succession(
                family_state, faction_ref=fid, roster_people=[p for p in cleaned if isinstance(p, Mapping)], year=at.year,
            )
            roster["people"] = succession["people_after"]
            # The environment after death begins exactly at this frontier.
            faction, _rotation = advance_faction_training_epoch(
                faction, roster, at_iso=at_iso, refresh_environment=True,
            )
            writes[fpath] = faction
            writes[rpath] = compact_roster_state(roster, faction=faction)
            faction_cache[fid] = (fpath, faction)
            roster_cache[fid] = (rpath, hydrate_roster_state(writes[rpath], faction=faction))
            successor_ref = succession.get("successor_ref")
            if successor_ref and (fid == "house_tang" or successor_ref == player_ref):
                notice = {"kind": "succession_notice", "faction_ref": fid, "successor_ref": successor_ref, "delivered_to_player": True}
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
                if not isinstance(row, Mapping) or row.get("status") != "married" or row.get("faction_ref") != fid:
                    continue
                for ref in row.get("spouse_refs", []):
                    if isinstance(ref, str):
                        refs.add(ref)
        return refs

    def all_existing_names() -> set[str]:
        nonlocal global_names_cache
        if global_names_cache is None:
            names: set[str] = set()
            for owner in scheduled_faction_ids:
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
            try:
                civic = read_json("state/martial-world/civic-people.json")
            except FileNotFoundError:
                civic = {}
            rows = civic.get("people", []) if isinstance(civic, Mapping) else []
            if isinstance(rows, list):
                names.update(str(p.get("name")) for p in rows if isinstance(p, Mapping) and isinstance(p.get("name"), str) and p.get("name"))
            global_names_cache = names
        return global_names_cache

    def set_person_route(person_ref: str, route: tuple[str, int] | None) -> None:
        bucket = hashlib.sha256(person_ref.encode("utf-8")).hexdigest()[:2]
        shard_path = f"state/martial-world/person-routes/{bucket}.json"
        raw_shard = writes.get(shard_path)
        shard = copy.deepcopy(dict(raw_shard)) if isinstance(raw_shard, Mapping) else copy.deepcopy(dict(read_json(shard_path)))
        routes = shard.setdefault("people", {})
        if not isinstance(routes, dict):
            raise ValueError("jianghu person route shard invalid")
        existed = person_ref in routes
        if route is None:
            routes.pop(person_ref, None)
        else:
            routes[person_ref] = [str(route[0]), int(route[1])]
        writes[shard_path] = shard
        if existed == (route is not None):
            return
        raw_index = writes.get(_PERSON_ROUTES_PATH)
        index = copy.deepcopy(dict(raw_index)) if isinstance(raw_index, Mapping) else copy.deepcopy(dict(read_json(_PERSON_ROUTES_PATH)))
        count = max(0, int(index.get("person_count", 0)))
        index["person_count"] = count + (1 if route is not None else -1)
        writes[_PERSON_ROUTES_PATH] = index

    def register_new_person_route(fid: str, person_ref: str, ordinal: int) -> None:
        bucket = hashlib.sha256(person_ref.encode("utf-8")).hexdigest()[:2]
        shard_path = f"state/martial-world/person-routes/{bucket}.json"
        raw_shard = writes.get(shard_path)
        shard = raw_shard if isinstance(raw_shard, Mapping) else read_json(shard_path)
        routes = shard.get("people", {}) if isinstance(shard, Mapping) else {}
        if isinstance(routes, Mapping) and person_ref in routes:
            raise ValueError("jianghu duplicate person route")
        set_person_route(person_ref, (fid, ordinal))

    def rewrite_faction_person_routes(fid: str, old_people: Sequence[Mapping[str, Any]], new_people: Sequence[Mapping[str, Any]]) -> None:
        old_refs = {str(p.get("person_id")) for p in old_people if isinstance(p.get("person_id"), str)}
        new_refs = [str(p.get("person_id")) for p in new_people if isinstance(p.get("person_id"), str)]
        for ref in sorted(old_refs - set(new_refs)):
            set_person_route(ref, None)
        for ordinal, ref in enumerate(new_refs):
            set_person_route(ref, (fid, ordinal))

    outlaw_by_route: dict[str, list[Mapping[str, Any]]] = {}
    outlaw_routes_seeded = False

    def outlaws_for_route(route_id: str) -> list[Mapping[str, Any]]:
        nonlocal outlaw_routes_seeded
        if not outlaw_routes_seeded:
            for fid in scheduled_faction_ids:
                try:
                    _p, faction = load_faction(fid)
                except (FileNotFoundError, ValueError):
                    continue
                if faction_type(str(faction.get("faction_id") or "")) != "outlaw_faction":
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
        contract_after["expired_count"] = max(0, int(contract_after.get("expired_count", 0))) + 1
        expired_contracts.append(cid)
        world_history = record_event(
            world_history, at=at_iso, kind="contract_expired", contract_ref=cid, issuer_ref=issuer, refunded_cash=escrow,
        )
        refunded_cash += escrow
    if expired_contracts:
        writes[_CONTRACT_INDEX_PATH] = contract_after
        writes[_WORLD_HISTORY_PATH] = world_history
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

    # Market cycles first, once per region even when multiple event rows share it.
    settled_regions: set[str] = set()
    for event in sorted_events:
        if event.get("kind") != "regional_market_cycle":
            continue
        region = event.get("owner_ref")
        if not isinstance(region, str) or region in settled_regions:
            continue
        path, market = load_market(region)
        before = int(market.get("cycles_settled", 0))
        after = settle_cycles(market, cycles=1)
        writes[path] = after
        market_cache[region] = (path, after)
        settled_regions.add(region)
        reviews.append({
            "kind": "regional_market_cycle",
            "event_id": event.get("event_id"),
            "region_id": region,
            "cycles_before": before,
            "cycles_after": int(after.get("cycles_settled", before)),
        })

    # Regional government response closes the warrant loop without inventing
    # omniscient police. A warrant acts only in its persisted jurisdiction and
    # only when the subject is physically found there at a monthly search
    # frontier. Ordinary forces remain finite aggregate headcounts.
    government_regions: set[str] = set()
    for event in sorted_events:
        if event.get("kind") != "regional_market_cycle":
            continue
        region = event.get("owner_ref")
        if not isinstance(region, str) or region in government_regions:
            continue
        government_regions.add(region)
        capacities = government_state.setdefault("regional_capacity", {})
        warrants = government_state.setdefault("warrants", {})
        attention_rows = government_state.setdefault("attention", {})
        if not all(isinstance(x, dict) for x in (capacities, warrants, attention_rows)):
            raise ValueError("jianghu government state invalid")
        defaults = government_troops.get("default_regional_capacity", {}) if isinstance(government_troops, Mapping) else {}
        recovery = government_troops.get("monthly_reconstitution", {}) if isinstance(government_troops, Mapping) else {}
        current = capacities.get(region, {}) if isinstance(capacities.get(region), Mapping) else {}
        capacity = {
            tier: min(max(0, int(defaults.get(tier, 0))), max(0, int(current.get(tier, defaults.get(tier, 0)))) + max(0, int(recovery.get(tier, 0))))
            for tier in ("militia", "standard", "elite")
        }
        contacts = 0; detained = 0
        resolution_cfg = government_troops.get("contact_resolution", {}) if isinstance(government_troops, Mapping) else {}
        power_by_tier = {tier: max(1, int(resolution_cfg.get(f"{tier}_power", {"militia": 35, "standard": 65, "elite": 95}[tier]))) for tier in ("militia", "standard", "elite")}
        advantage = max(1000, int(resolution_cfg.get("detention_advantage_milli", 1800)))
        for warrant_ref in sorted(warrants):
            raw = warrants.get(warrant_ref)
            if not isinstance(raw, Mapping) or raw.get("status") not in {"active", "pursuing"} or raw.get("jurisdiction_ref") != region:
                continue
            subject_ref = raw.get("subject_ref")
            if not isinstance(subject_ref, str):
                continue
            try:
                _fid, _rpath, _roster, _ordinal, subject = load_person_ref(subject_ref)
            except (KeyError, ValueError, FileNotFoundError):
                continue
            # A person reserved to an active route/deployment/construction
            # commitment is not physically available at their last ordinary
            # roster site.  The commitment owner is the current occupancy
            # authority until that activity releases them, so government
            # search must not "find" a travelling person at home.
            if subject_ref in unavailable_person_refs():
                continue
            site = site_rows.get(str(subject.get("location_ref")))
            place = str(site.get("parent_place_ref") or "") if isinstance(site, Mapping) else ""
            if place_region.get(place) != region:
                continue
            att = attention_rows.get(subject_ref, {}) if isinstance(attention_rows.get(subject_ref), Mapping) else {}
            attention = max(0, int(att.get("attention", 0)))
            allocated = allocate_response(attention, capacity)
            deployment = allocated["allocated"]
            if int(deployment.get("exact_headcount", 0)) <= 0:
                continue
            capacity = dict(allocated["capacity_after"]); contacts += 1
            warrant = copy.deepcopy(dict(raw)); warrant["status"] = "pursuing"; warrant["last_contact_at"] = at_iso
            warrant["last_deployment"] = {tier: int(deployment.get(tier, 0)) for tier in ("militia", "standard", "elite")}
            if subject_ref == player_ref:
                warrants[warrant_ref] = warrant
                notice = {
                    "kind": "government_summons", "warrant_ref": warrant_ref, "subject_ref": subject_ref,
                    "region_ref": region, "deployed_headcount": int(deployment["exact_headcount"]),
                    "requires_player_decision": True, "delivered_to_player": True,
                }
                handoff = classify_handoff(notice); handoffs.append({**notice, "handoff": handoff})
                continue
            response_power = sum(int(deployment.get(tier, 0)) * power_by_tier[tier] for tier in power_by_tier)
            target_power = person_combat_index(subject)
            if response_power * 1000 >= target_power * advantage:
                active_custody = [
                    row for row in custody_state.get("records", [])
                    if isinstance(row, Mapping) and row.get("person_ref") == subject_ref
                    and row.get("status") not in {"released", "escaped", "executed"}
                ]
                if not active_custody:
                    custody_state["records"].append(create_custody_record(
                        person_ref=subject_ref,
                        captor_ref=f"government:{region}",
                        at=at_iso,
                        location_ref=str(subject.get("location_ref") or ""),
                        basis=f"active_warrant:{warrant_ref}",
                    ))
                    # Custody is its own current authority. Faction members
                    # pause institutional training; independent people have no
                    # faction training clock to pause.
                    if _fid:
                        pause_people_for_commitment(_fid, [subject_ref])
                writes[_CUSTODY_PATH] = custody_state
                warrants.pop(warrant_ref, None)
                detained += 1
                continue
            else:
                warrant["failed_contacts"] = max(0, int(warrant.get("failed_contacts", 0))) + 1
            warrants[warrant_ref] = warrant
        capacities[region] = capacity
        writes[_GOVERNMENT_PATH] = government_state
        if contacts or detained:
            reviews.append({"kind": "government_response", "region_ref": region, "contacts": contacts, "detentions": detained})

    # Faction upkeep consumes conserved cash and food.  Shortage is explicit in
    # the review rather than allowing stock/treasury to become negative.
    upheld_factions: set[str] = set()
    upkeep_pressure: dict[str, dict[str, int]] = {}
    for event in sorted_events:
        if event.get("kind") != "faction_upkeep":
            continue
        fid = event.get("owner_ref")
        if not isinstance(fid, str) or fid in upheld_factions:
            continue
        fpath, faction = load_faction(fid)
        ipath, inventory = load_inventory(fid)
        transport = inventory.get("transport_assets", {})
        if not isinstance(transport, Mapping):
            transport = {}
        quote = monthly_upkeep_quote(
            faction,
            riding_horses=int(transport.get("riding_horses", 0)),
            pack_animals=int(transport.get("pack_animals", 0)),
        )
        food_before = max(0, int(inventory.get("food_ration_days", 0)))
        cash_before = max(0, int(faction.get("treasury_cash", 0)))
        # Human ration stock and animal fodder are different authorities.
        # Animal feed is already purchased as the explicit animal_feed_cash
        # component of upkeep at the registered horse-feed price.  Charging the
        # same animals again against human food_ration_days double-counted feed
        # and made transport-heavy factions starve even while paying for fodder.
        food_due = int(quote["food_ration_days"])
        cash_due = int(quote["total_cash"])
        food_paid = min(food_before, food_due)
        cash_paid = min(cash_before, cash_due)
        inventory["food_ration_days"] = food_before - food_paid
        faction["treasury_cash"] = cash_before - cash_paid
        # Ordinary upkeep cash is not destroyed. It pays the surrounding
        # aggregate regional economy for supplies, maintenance and services.
        # Food itself is physically consumed from faction stores.
        region = place_region.get(str(faction.get("headquarters", "")))
        if cash_paid > 0 and isinstance(region, str):
            try:
                mpath, upkeep_market = load_market(region)
            except FileNotFoundError:
                upkeep_market = None; mpath = ""
            if isinstance(upkeep_market, dict):
                upkeep_market["cash_pool"] = max(0, int(upkeep_market.get("cash_pool", 0))) + cash_paid
                market_cache[region] = (mpath, upkeep_market)
                writes[mpath] = upkeep_market
        pressure = institutional_stress_milli(
            food_due=food_due, food_paid=food_paid, cash_due=cash_due, cash_paid=cash_paid,
        )
        upkeep_pressure[fid] = {
            "food_due": food_due, "food_paid": food_paid, "cash_due": cash_due,
            "cash_paid": cash_paid, "stress_milli": pressure,
        }
        if pressure >= 400:
            world_history = record_event(
                world_history, at=at_iso, kind="faction_shortage_crisis", faction_ref=fid,
                stress_milli=pressure, food_shortfall=food_due-food_paid, cash_shortfall=cash_due-cash_paid,
            )
            writes[_WORLD_HISTORY_PATH] = world_history
        writes[ipath] = inventory
        writes[fpath] = faction
        upheld_factions.add(fid)
        reviews.append({
            "kind": "faction_upkeep",
            "event_id": event.get("event_id"),
            "faction_ref": fid,
            "food_due": food_due,
            "food_consumed": food_paid,
            "food_shortfall": food_due - food_paid,
            "cash_due": cash_due,
            "cash_paid": cash_paid,
            "cash_shortfall": cash_due - cash_paid,
        })


    # One monthly faction member cycle settles both ordinary compensation and
    # standing institutional training. This is intentionally one faction-level
    # wake, not one scheduler event per person.
    member_cycled: set[str] = set()
    for event in sorted_events:
        if event.get("kind") != "faction_member_cycle":
            continue
        fid = event.get("owner_ref")
        if not isinstance(fid, str) or fid in member_cycled:
            continue
        fpath, faction = load_faction(fid)
        rpath, roster = load_roster(fid)
        paid = settle_monthly_compensation(faction, roster)
        faction = copy.deepcopy(dict(paid["faction"]))
        roster = copy.deepcopy(dict(paid["roster"]))
        pressure_row = upkeep_pressure.get(fid, {})
        current_stress = institutional_stress_milli(
            food_due=max(0, int(pressure_row.get("food_due", 0))),
            food_paid=max(0, int(pressure_row.get("food_paid", 0))),
            cash_due=max(0, int(pressure_row.get("cash_due", 0))),
            cash_paid=max(0, int(pressure_row.get("cash_paid", 0))),
            stipend_due=max(0, int(paid.get("due_cash", 0))),
            stipend_paid=max(0, int(paid.get("paid_cash", 0))),
        )
        desired_intensity = training_intensity_for_stress(current_stress)
        epoch = copy.deepcopy(dict(faction.get("training_epoch", {}))) if isinstance(faction.get("training_epoch"), Mapping) else {}
        old_intensity = max(0, int(epoch.get("intensity_milli", 1000)))
        intensity_changed = old_intensity != desired_intensity
        # Settle the month under the intensity/environment that actually ruled
        # that elapsed period, then start a fresh bounded epoch whose intensity
        # reflects the shortage/stipend pressure just observed at this frontier.
        # This simultaneously prevents retroactive stress effects and stops
        # training_epoch from accumulating one multi-kilobyte history segment
        # per faction per month forever.
        faction, roster, training_summary = settle_and_reset_faction_training_cycle(
            faction, roster, at_iso=at_iso, next_intensity_milli=desired_intensity,
        )
        roster_for_cache = roster
        # Death can originate from exact combat or lawful execution between annual
        # life frontiers. Close only current authorities here so family, social, office, and custody state cannot keep treating a dead person as
        # active. Permanent parentage/household ancestry remains intact.
        dead_refs = {
            str(p.get("person_id")) for p in roster_for_cache.get("people", [])
            if isinstance(p, Mapping) and isinstance(p.get("person_id"), str)
            and isinstance(p.get("health"), Mapping) and p.get("health", {}).get("status") == "dead"
        }
        if dead_refs:
            family_state = apply_family_death_status(family_state, dead_refs=sorted(dead_refs), faction_ref=fid, roster_people=[p for p in roster_for_cache.get("people", []) if isinstance(p, Mapping)])
            writes[_FAMILY_PATH] = family_state
            courtships = social_state.get("courtships", {}) if isinstance(social_state, Mapping) else {}
            if isinstance(courtships, dict):
                for pair_ref in list(courtships):
                    row = courtships.get(pair_ref); refs = row.get("person_refs", []) if isinstance(row, Mapping) else []
                    if any(str(ref) in dead_refs for ref in refs): courtships.pop(pair_ref, None)
            relationships = social_state.get("relationships", {}) if isinstance(social_state, Mapping) else {}
            if isinstance(relationships, dict):
                for edge_ref in list(relationships):
                    if any(part in dead_refs for part in str(edge_ref).split("|", 1)): relationships.pop(edge_ref, None)
            writes[_SOCIAL_PATH] = social_state
            prior_custody_rows = [row for row in custody_state.get("records", []) if isinstance(row, Mapping)]
            custody_rows = [
                row for row in prior_custody_rows
                if str(row.get("person_ref")) not in dead_refs and str(row.get("captor_ref")) not in dead_refs
            ]
            if len(custody_rows) != len(prior_custody_rows):
                custody_state["records"] = custody_rows; writes[_CUSTODY_PATH] = custody_state
                released_by_captor_death = {
                    str(row.get("person_ref")) for row in prior_custody_rows
                    if str(row.get("captor_ref")) in dead_refs
                    and isinstance(row.get("person_ref"), str)
                    and str(row.get("person_ref")) not in dead_refs
                }
                pending_training_resume_refs.update(released_by_captor_death)
            people_rows = roster_for_cache.get("people", [])
            if isinstance(people_rows, list):
                cleaned=[]
                for raw in people_rows:
                    if isinstance(raw, Mapping) and str(raw.get("person_id")) in dead_refs and raw.get("standing_offices"):
                        person=copy.deepcopy(dict(raw)); person["standing_offices"]=[]; cleaned.append(person)
                    else: cleaned.append(raw)
                roster_for_cache["people"] = cleaned
        monthly_succession_ref = None
        if dead_refs:
            succession = apply_recognized_succession(
                family_state, faction_ref=fid,
                roster_people=[p for p in roster_for_cache.get("people", []) if isinstance(p, Mapping)],
                year=at.year,
            )
            roster_for_cache["people"] = succession["people_after"]
            monthly_succession_ref = succession.get("successor_ref")
            if monthly_succession_ref:
                notice = {
                    "kind": "succession_notice", "faction_ref": fid,
                    "successor_ref": monthly_succession_ref, "delivered_to_player": True,
                }
                handoff = classify_handoff(notice); handoffs.append({**notice, "handoff": handoff})

        # Severe or prolonged shortage can now cause real membership turnover.
        # Exact leavers remain persistent people in the independent martial pool
        # and may later join another faction; identities are never deleted or
        # regenerated from a civilian template.
        departure_refs = annual_voluntary_departure_refs(
            [p for p in roster_for_cache.get("people", []) if isinstance(p, Mapping)],
            faction_ref=fid, year=at.year, hardship_milli=current_stress,
            # A person cannot resign from a faction while physically committed
            # to travel, custody, combat, escort duty, a tournament, or another
            # finite activity.  Their membership may be reconsidered after the
            # commitment settles and availability is restored.
            protected_refs=sorted(
                family_bound_refs(fid)
                | unavailable_person_refs()
                | ({player_ref} if player_ref else set())
            ),
            maximum=(max(1, int(faction.get("population", 0)) // 80) if current_stress >= 400 else 0),
            period_key=f"{at.year:04d}-{at.month:02d}",
        ) if current_stress >= 400 else []
        if departure_refs:
            leaving = set(departure_refs)
            pre_departure_people = [copy.deepcopy(dict(p)) for p in roster_for_cache.get("people", []) if isinstance(p, Mapping)]
            kept: list[Any] = []
            independent_rows = independent_state.setdefault("people", [])
            if not isinstance(independent_rows, list):
                raise ValueError("jianghu independent people invalid")
            for raw in roster_for_cache.get("people", []):
                if not isinstance(raw, Mapping) or str(raw.get("person_id")) not in leaving:
                    kept.append(raw); continue
                person = compact_person_state(raw, faction_ref=fid)
                person.pop("membership_grade", None)
                person.pop("standing_duty_ref", None)
                person["standing_offices"] = []
                person["location_ref"] = str(raw.get("location_ref") or faction.get("local_site_ref") or faction.get("headquarters") or "")
                person["former_faction_ref"] = fid
                person["independent_since"] = at_iso
                independent_rows.append(person)
            roster_for_cache["people"] = kept
            rewrite_faction_person_routes(fid, pre_departure_people, [p for p in kept if isinstance(p, Mapping)])
            faction = reconcile_faction_population(faction, roster_for_cache)
            faction_cache[fid] = (fpath, faction)
            writes[_INDEPENDENTS_PATH] = independent_state
            world_history = record_event(
                world_history, at=at_iso, kind="faction_departure", faction_ref=fid,
                count=len(departure_refs), person_refs=sorted(departure_refs), reason="institutional_hardship",
            )
            writes[_WORLD_HISTORY_PATH] = world_history

        relationship_review = advance_npc_relationships(
            family_state, social_state, faction_ref=fid,
            roster_people=[p for p in roster_for_cache.get("people", []) if isinstance(p, Mapping)],
            at_iso=at_iso, player_ref=player_ref or None,
            residence_ref=str(faction.get("local_site_ref") or faction.get("headquarters") or "") or None,
            exclude_refs=sorted(unavailable_person_refs()),
        )
        if relationship_review["courtships_started"] or relationship_review["marriages_created"]:
            family_state = relationship_review["family_after"]
            social_state = relationship_review["social_after"]
            writes[_FAMILY_PATH] = family_state
            writes[_SOCIAL_PATH] = social_state
        family_review = review_conceptions(
            family_state, faction_ref=fid,
            roster_people=[p for p in roster_for_cache.get("people", []) if isinstance(p, Mapping)],
            at_iso=at_iso, player_ref=player_ref or None,
            exclude_refs=sorted(unavailable_person_refs()),
        )
        if family_review["conceived_refs"]:
            family_state = family_review["family_after"]
            pending_one_off_events.extend(family_review["one_off_events"])
            writes[_FAMILY_PATH] = family_state
        roster = compact_roster_state(roster_for_cache, faction=faction)
        writes[fpath] = faction
        writes[rpath] = roster
        faction_cache[fid] = (fpath, faction)
        # Caches always hold logical hydrated state.  Persisted after-images may
        # be sparse, but downstream work at the same frontier still needs
        # derived faction/location/martial defaults.
        roster_cache[fid] = (rpath, hydrate_roster_state(roster, faction=faction))
        member_cycled.add(fid)
        reviews.append({
            "kind": "faction_member_cycle",
            "event_id": event.get("event_id"),
            "faction_ref": fid,
            "stipend_due_cash": int(paid["due_cash"]),
            "stipend_paid_cash": int(paid["paid_cash"]),
            "stipend_shortfall_cash": int(paid["shortfall_cash"]),
            "institutional_stress_milli": current_stress,
            "training_intensity_milli": desired_intensity,
            "departures": len(departure_refs),
            "courtships_started": len(relationship_review["courtships_started"]),
            "succession_ref": monthly_succession_ref,
            "marriages_created": len(relationship_review["marriages_created"]),
            "conceptions_created": len(family_review["conceived_refs"]),
            **training_summary,
        })

    # Strategic review consumes the post-upkeep state, never a stale pre-cost
    # snapshot.  It produces lawful priorities, not free outcomes.
    reviewed_factions: set[str] = set()
    for event in sorted_events:
        if event.get("kind") != "faction_review":
            continue
        fid = event.get("owner_ref")
        if not isinstance(fid, str) or fid in reviewed_factions:
            continue
        fpath, faction = load_faction(fid)
        ipath, inventory = load_inventory(fid)
        pop = max(1, int(faction.get("population", 0)))
        food_reserve_days = max(0, int(inventory.get("food_ration_days", 0))) // pop
        transport = inventory.get("transport_assets", {})
        if not isinstance(transport, Mapping):
            transport = {}
        upkeep = monthly_upkeep_quote(
            faction,
            riding_horses=int(transport.get("riding_horses", 0)),
            pack_animals=int(transport.get("pack_animals", 0)),
        )
        _rpath, review_roster = load_roster(fid)
        review_people = review_roster.get("people", []) if isinstance(review_roster, Mapping) else []
        stipend_due = sum(
            monthly_stipend(person)
            for person in review_people
            if isinstance(person, Mapping)
            and not (
                isinstance(person.get("health"), Mapping)
                and person.get("health", {}).get("status") == "dead"
            )
        )
        # Reserve calculations must include both institutional overhead and the
        # explicit member allowance bill.  Using upkeep alone made factions look
        # richer than they were, so agriculture/projects could spend cash that
        # was already needed for the next payroll frontier.
        monthly_cash = max(1, int(upkeep["total_cash"]) + stipend_due)
        cash_reserve_months = max(0, int(faction.get("treasury_cash", 0))) // monthly_cash
        active_for_faction = sum(
            1 for row in active_contracts.values()
            if isinstance(row, Mapping)
            and (
                fid in {row.get("issuer_ref"), row.get("beneficiary_ref")}
                or (row.get("status") == "offered" and row.get("beneficiary_ref") in {None, ""})
            )
            and row.get("status") not in {"settled", "failed", "expired", "cancelled"}
        )
        hostile = sum(
            1 for edge in relation_index.get(fid, [])
            if int(edge.get("hostility", 0)) >= 30
        )
        buildings = faction.get("buildings", {})
        if not isinstance(buildings, Mapping):
            buildings = {}
        # Capacity is physical, not a second formula inferred from building level.
        training_capacity = max(0,
            training_domain_capacity(buildings, "sword", faction.get("infrastructure",{}))
            + training_domain_capacity(buildings, "qi", faction.get("infrastructure",{}))
        )
        season_id = f"{at.year:04d}-Q{((at.month - 1) // 3) + 1}"
        months_left_in_season = 3 - ((at.month - 1) % 3)
        living_population = living_member_count([p for p in review_people if isinstance(p, Mapping)])
        housing_capacity = residential_capacity(buildings, faction.get("infrastructure", {}))
        desired_recruitment_gap = sustainable_recruitment_gap(
            faction, living_population=living_population, residential_capacity=housing_capacity,
            food_reserve_days=food_reserve_days, cash_reserve_months=cash_reserve_months,
        )
        recruitment_capacity = monthly_recruitment_tranche(
            faction, season_id=season_id, months_left_in_season=months_left_in_season,
            desired_gap=desired_recruitment_gap,
        )
        enterprises = faction.get("enterprises", {}) if isinstance(faction.get("enterprises"), Mapping) else {}
        active_enterprises = sum(1 for level in enterprises.values() if isinstance(level, int) and not isinstance(level, bool) and level > 0)
        agriculture_scale = enterprise_scale_value(faction, "agriculture_landholding") if int(enterprises.get("agriculture_landholding", 0)) > 0 else 0
        landholding_units = (agriculture_scale + 49) // 50 if agriculture_scale > 0 else 0
        project_rows = projects_state.get("projects", {}) if isinstance(projects_state, Mapping) else {}
        active_projects_for_faction = sum(1 for row in project_rows.values() if isinstance(row, Mapping) and row.get("faction_ref") == fid and not row.get("completed")) if isinstance(project_rows, Mapping) else 0
        external_holdings = 1 if landholding_units > 0 else 0
        admin_workload = administrative_workload_units(
            population=pop, active_enterprises=active_enterprises, landholding_units=landholding_units,
            active_contracts=active_for_faction, active_projects=active_projects_for_faction, external_holdings=external_holdings,
        )
        admin_capability = staffed_administrative_capability(
            [p for p in review_people if isinstance(p, Mapping)],
            main_hall_level=max(0, int(buildings.get("main_hall", 0))),
            infrastructure=faction.get("infrastructure",{}), unavailable_refs=unavailable_person_refs(),
        )
        admin_factor = administration_factor_milli(workload_units=admin_workload, capability_units=admin_capability)
        recruitment_capacity = recruitment_capacity * admin_factor // 1000
        injured_martial = sum(
            1 for person in review_people
            if isinstance(person, Mapping)
            and is_faction_member(person)
            and (
                person.get("health", {}).get("status") not in {None, "ready"}
                or bool(person.get("health", {}).get("injuries"))
            )
        )
        institutional = institutional_status(
            faction, review_roster, year=at.year, social=social_state,
            unavailable_refs=sorted(unavailable_person_refs()),
        )
        region = place_region.get(str(faction.get("headquarters", "")))
        market_shortages = 0
        if isinstance(region, str):
            try:
                _mpath, regional_market = load_market(region)
                stock = regional_market.get("stock", {}) if isinstance(regional_market, Mapping) else {}
                if isinstance(stock, Mapping):
                    market_shortages = sum(1 for qty in stock.values() if int(qty) <= 0)
            except FileNotFoundError:
                pass
        review = autonomy_review(
            faction,
            food_reserve_days=food_reserve_days,
            cash_reserve_months=cash_reserve_months,
            injured_martial=injured_martial,
            open_contracts=active_for_faction,
            recruitment_capacity=recruitment_capacity,
            training_capacity=training_capacity,
            office_vacancies=int(institutional["office_vacancies"]),
            known_hostile_relations=hostile,
            market_shortages=market_shortages,
            active_projects=active_projects_for_faction,
            institutional_stress_milli=max(0, int(upkeep_pressure.get(fid, {}).get("stress_milli", 0))),
        )
        review["administrative_workload_units"] = admin_workload
        review["administrative_capability_units"] = admin_capability
        review["administration_factor_milli"] = admin_factor

        # Execute only priorities with a real conserved production path.  These
        # reducers mutate the same treasury/inventory/market/population owners
        # used by player commands; no autonomous-action diary is persisted.
        executed_actions: list[dict[str, Any]] = []
        market_sale_done = False
        for action in review["ordered_actions"]:
            if action == "secure_food":
                if not isinstance(region, str):
                    executed_actions.append({"action": action, "result": "no_regional_market"})
                    continue
                try:
                    mpath, regional_market = load_market(region)
                except FileNotFoundError:
                    executed_actions.append({"action": action, "result": "no_regional_market"})
                    continue
                food = secure_food_purchase(faction, inventory, regional_market, region_id=region, target_reserve_days=60)
                faction = food["faction"]; inventory = food["inventory"]; regional_market = food["market"]
                faction_cache[fid] = (fpath, faction); inventory_cache[fid] = (ipath, inventory); market_cache[region] = (mpath, regional_market)
                writes[fpath] = faction; writes[ipath] = inventory; writes[mpath] = regional_market
                executed_actions.append({
                    "action": action, "result": str(food.get("reason", "evaluated")),
                    "quantity": int(food.get("quantity", 0)), "cash_spent": int(food.get("cash_spent", 0)),
                })
                continue

            if action in {"preserve_or_earn_cash", "address_market_shortage"}:
                if market_sale_done:
                    executed_actions.append({"action": action, "result": "already_sold_this_frontier"})
                    continue
                if not isinstance(region, str):
                    executed_actions.append({"action": action, "result": "no_regional_market"})
                    continue
                try:
                    mpath, regional_market = load_market(region)
                except FileNotFoundError:
                    executed_actions.append({"action": action, "result": "no_regional_market"})
                    continue
                trade_level = max(0, int(enterprises.get("trade_merchant_business", 0)))
                trade_scale = enterprise_scale_value(faction, "trade_merchant_business") if trade_level > 0 else 0
                trade_efficiency = enterprise_operating_efficiency_milli("trade_merchant_business", trade_level) if trade_level > 0 else 0
                agriculture_level = max(0, int(enterprises.get("agriculture_landholding", 0)))
                agriculture_scale = enterprise_scale_value(faction, "agriculture_landholding") if agriculture_level > 0 else 0
                agriculture_efficiency = enterprise_operating_efficiency_milli("agriculture_landholding", agriculture_level) if agriculture_level > 0 else 0
                if trade_scale > 0 and trade_efficiency > 0:
                    monthly_trade_value_cap = trade_scale * trade_efficiency // 1000
                    allowed_sale_items = None
                    sale_channel = "merchant_surplus_sale"
                elif agriculture_scale > 0 and agriculture_efficiency > 0:
                    # A landholding can sell its own grain locally without
                    # pretending to own a general merchant enterprise.  Keep
                    # the sixty-person-day food reserve and never liquidate
                    # unrelated construction/craft stock through this path.
                    monthly_trade_value_cap = None
                    allowed_sale_items = {"food_ration_day"}
                    sale_channel = "agriculture_local_sale"
                else:
                    executed_actions.append({"action": action, "result": "no_sale_enterprise_capacity"})
                    continue
                sale = sell_surplus_to_market(
                    faction, inventory, regional_market, region_id=region,
                    shortage_only=(action == "address_market_shortage"),
                    max_trade_value_cash=monthly_trade_value_cap,
                    allowed_items=allowed_sale_items,
                )
                faction = sale["faction"]; inventory = sale["inventory"]; regional_market = sale["market"]
                faction_cache[fid] = (fpath, faction); inventory_cache[fid] = (ipath, inventory); market_cache[region] = (mpath, regional_market)
                writes[fpath] = faction; writes[ipath] = inventory; writes[mpath] = regional_market
                market_sale_done = int(sale.get("quantity", 0)) > 0
                executed_actions.append({
                    "action": action, "result": str(sale.get("reason", "evaluated")),
                    "item_ref": sale.get("item_ref"), "quantity": int(sale.get("quantity", 0)),
                    "cash_earned": int(sale.get("cash_earned", 0)), "sale_channel": sale_channel,
                })
                continue

            if action == "address_hostile_relation":
                policy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
                intent = choose_hostile_action(
                    relation_index.get(fid, []), faction_ref=fid, year=at.year, month=at.month,
                    risk_tolerance=int(policy.get("risk_tolerance", 50)),
                    active_strategic_operations=active_strategic_operations(fid),
                )
                if intent is None:
                    executed_actions.append({"action": action, "result": "no_bounded_hostile_action"})
                else:
                    outcome = start_strategic_operation(fid, intent)
                    executed_actions.append({"action": action, **outcome, "intent": str(intent.get("action")), "target_faction_ref": intent.get("target_faction_ref")})
                continue

            if action == "invest_growth":
                intent = choose_investment_priority(
                    faction, living_population=living_population, residential_capacity=housing_capacity,
                    training_capacity=training_capacity, cash_reserve_months=cash_reserve_months,
                    active_projects=active_projects_for_faction,
                    stress_milli=max(0, int(upkeep_pressure.get(fid, {}).get("stress_milli", 0))),
                )
                if intent is None:
                    executed_actions.append({"action": action, "result": "no_viable_investment"})
                else:
                    outcome = start_autonomous_investment(fid, intent)
                    # Project start is deliberately transactional and replaces the
                    # cached faction/inventory with committed after-images. Refresh
                    # this monthly review's local references immediately so later
                    # agriculture/production and the final writes cannot resurrect
                    # the pre-project treasury or material stock.
                    if outcome.get("result") == "project_started":
                        fpath, faction = load_faction(fid)
                        ipath, inventory = load_inventory(fid)
                    executed_actions.append({"action": action, **outcome, "investment_kind": str(intent.get("kind")), "building_type": intent.get("building_type"), "enterprise_type": intent.get("enterprise_type")})
                continue

            if action == "consider_diplomacy":
                target_fid = choose_friendly_aid_target(
                    relation_index.get(fid, []), faction_ref=fid, year=at.year, month=at.month,
                    cash_reserve_months=cash_reserve_months,
                )
                outcome = execute_friendly_aid(fid, target_fid) if target_fid else {"result": "no_friendly_aid_pressure"}
                executed_actions.append({"action": action, **outcome})
                continue

            if action == "recruit":
                requested = max(0, int(recruitment_capacity))
                if requested <= 0:
                    executed_actions.append({"action": action, "result": "no_current_capacity"})
                    continue
                # Recruitment never digs a faction deeper into an unresolved
                # subsistence crisis merely because a policy target exists.
                current_pop = max(1, int(faction.get("population", 0)))
                current_food_days = max(0, int(inventory.get("food_ration_days", 0))) // current_pop
                current_cash_months = max(0, int(faction.get("treasury_cash", 0))) // max(1, monthly_cash)
                if current_food_days < 30 or current_cash_months < 1:
                    executed_actions.append({"action": action, "result": "deferred_for_reserves"})
                    continue
                people = review_roster.get("people", []) if isinstance(review_roster, Mapping) else []
                if not isinstance(people, list):
                    raise ValueError("jianghu roster people invalid")
                policy = faction.get("recruitment_policy", {}) if isinstance(faction.get("recruitment_policy"), Mapping) else {}
                minimum_martial = max(0, int(policy.get("minimum_martial_aptitude", 0)))
                minimum_qi = max(0, int(policy.get("minimum_qi_aptitude", 0)))
                epoch_days = max(0, int((faction.get("training_epoch") or {}).get("elapsed_training_days", 0))) if isinstance(faction.get("training_epoch"), Mapping) else 0
                admitted_refs: list[str] = []
                external_refs: list[str] = []

                # Existing independents are conserved exact people and are considered
                # before materializing a new recruit from the aggregate civilian pool.
                # Recruitment therefore supports real faction-to-independent-to-faction
                # turnover without deleting or rerolling a human identity.
                admitted_refs: list[str] = []
                external_refs: list[str] = []
                remaining = requested
                examined = 0
                headquarters = str(faction.get("headquarters", ""))
                home_site = str(faction.get("local_site_ref") or headquarters)
                target_region = place_region.get(headquarters)
                independent_rows = independent_state.get("people", []) if isinstance(independent_state, Mapping) else []
                if remaining > 0 and isinstance(independent_rows, list):
                    eligible_independent: list[tuple[int, str, int, Mapping[str, Any]]] = []
                    for index, raw in enumerate(independent_rows):
                        if not isinstance(raw, Mapping) or not isinstance(raw.get("person_id"), str):
                            continue
                        if raw.get("retired_from_field"):
                            continue
                        health = raw.get("health", {}) if isinstance(raw.get("health"), Mapping) else {}
                        if health.get("status") == "dead":
                            continue
                        age = max(0, at.year - int(raw.get("birth_year", at.year)))
                        if age < 8:
                            continue
                        apt = raw.get("aptitudes", {}) if isinstance(raw.get("aptitudes"), Mapping) else {}
                        if int(apt.get("martial", 0)) < minimum_martial or int(apt.get("qi", 0)) < minimum_qi:
                            continue
                        former_faction = str(raw.get("former_faction_ref") or "")
                        if former_faction == fid:
                            try:
                                independent_since = datetime.fromisoformat(str(raw.get("independent_since")))
                            except (TypeError, ValueError):
                                independent_since = at
                            # A member who just left cannot churn back into the
                            # same institution at its very next monthly review.
                            # A later readmission remains possible after a real
                            # cooling period; another faction may recruit them
                            # sooner if geography and policy fit.
                            if at - independent_since < timedelta(days=365):
                                continue
                        location_ref = str(raw.get("location_ref") or "")
                        site = site_rows.get(location_ref) if isinstance(site_rows, Mapping) else None
                        place_ref = str(site.get("parent_place_ref")) if isinstance(site, Mapping) and site.get("parent_place_ref") else location_ref
                        source_region = place_region.get(place_ref)
                        if target_region and source_region and target_region != source_region:
                            continue
                        martial = raw.get("martial_skills", {}) if isinstance(raw.get("martial_skills"), Mapping) else {}
                        peak = max([int(v) for v in martial.values() if isinstance(v, int) and not isinstance(v, bool)] or [0])
                        affinity = int.from_bytes(hashlib.sha256(f"{fid}|{raw['person_id']}|{at.year}|{at.month}".encode("utf-8")).digest()[:4], "big") % 1000
                        transfer_bonus = 1500 if former_faction and former_faction != fid else 0
                        score = peak * 20 + int(apt.get("martial", 0)) * 8 + int(apt.get("qi", 0)) * 4 + affinity + transfer_bonus
                        eligible_independent.append((-score, str(raw["person_id"]), index, raw))
                    eligible_independent.sort(key=lambda item: (item[0], item[1]))
                    chosen = eligible_independent[:remaining]
                    chosen_indexes = {item[2] for item in chosen}
                    transfer_refs: list[str] = []
                    readmission_refs: list[str] = []
                    for _score, person_ref, _index, raw in chosen:
                        person = copy.deepcopy(dict(raw))
                        former_faction = str(person.get("former_faction_ref") or "")
                        if former_faction and former_faction != fid:
                            transfer_refs.append(person_ref)
                        elif former_faction == fid:
                            readmission_refs.append(person_ref)
                        martial = person.get("martial_skills", {}) if isinstance(person.get("martial_skills"), Mapping) else {}
                        peak = max([int(v) for v in martial.values() if isinstance(v, int) and not isinstance(v, bool)] or [0])
                        person["membership_grade"] = "full" if peak >= 60 else ("junior" if peak >= 25 else "probationary")
                        person["joined_year"] = at.year
                        person["location_ref"] = home_site
                        person.pop("former_faction_ref", None)
                        person.pop("independent_since", None)
                        person.pop("retired_from_field", None)
                        people.append(person)
                        register_new_person_route(fid, person_ref, len(people) - 1)
                        admitted_refs.append(person_ref)
                    if chosen_indexes:
                        independent_state["people"] = [row for i, row in enumerate(independent_rows) if i not in chosen_indexes]
                        writes[_INDEPENDENTS_PATH] = independent_state
                        remaining -= len(chosen_indexes)
                        world_history = record_event(
                            world_history, at=at_iso, kind="independent_recruitment", faction_ref=fid,
                            count=len(chosen_indexes), transfer_count=len(transfer_refs), readmission_count=len(readmission_refs),
                            person_refs=sorted(admitted_refs),
                        )
                        if transfer_refs:
                            world_history = record_event(
                                world_history, at=at_iso, kind="faction_transfer", target_faction_ref=fid,
                                count=len(transfer_refs), person_refs=sorted(transfer_refs),
                            )
                        if readmission_refs:
                            world_history = record_event(
                                world_history, at=at_iso, kind="faction_readmission", faction_ref=fid,
                                count=len(readmission_refs), person_refs=sorted(readmission_refs),
                            )
                        writes[_WORLD_HISTORY_PATH] = world_history

                pools = civilian_state.get("places", {}) if isinstance(civilian_state.get("places"), Mapping) else {}
                pool = pools.get(headquarters) if isinstance(pools, Mapping) else None
                if remaining > 0 and isinstance(pool, dict):
                    available_civilians = max(0, int(pool.get("current_population", 0)) - int(pool.get("reserved_for_recruitment", 0)))
                    cursor = max(0, int(pool.get("recruitment_ordinal_cursor", 0)))
                    accepted_candidates: list[Mapping[str, Any]] = []
                    limit = min(available_civilians, max(remaining, remaining * 12))
                    for offset in range(limit):
                        candidate = deterministic_candidate(
                            world_seed=world_seed, origin_population_id=headquarters, ordinal=cursor + offset,
                        )
                        examined += 1
                        if int(candidate.get("age", 0)) < 8:
                            continue
                        apt = candidate.get("aptitudes", {}) if isinstance(candidate.get("aptitudes"), Mapping) else {}
                        if int(apt.get("martial", 0)) < minimum_martial or int(apt.get("qi", 0)) < minimum_qi:
                            continue
                        accepted_candidates.append(candidate)
                        if len(accepted_candidates) >= remaining:
                            break
                    pool["recruitment_ordinal_cursor"] = cursor + examined
                    names = all_existing_names()
                    for candidate in accepted_candidates:
                        ordinal_seed = int(candidate.get("origin_ordinal", 0))
                        person_ref = "mw.recruit." + hashlib.sha256((world_seed + "|" + headquarters + "|" + str(ordinal_seed)).encode("utf-8")).hexdigest()[:24]
                        age = max(0, int(candidate.get("age", 0)))
                        sex = deterministic_sex(stable=person_ref, faction_id=fid)
                        name = None
                        for attempt in range(128):
                            proposal = deterministic_name(stable=f"{person_ref}:{attempt}", sex=sex)
                            if proposal not in names:
                                name = proposal; names.add(proposal); break
                        if name is None:
                            continue
                        professional = {"medicine": 0, "administration": 0, "commerce": 0, "crafting": 0, "instruction": 0}
                        developed = apply_age_development(
                            age=age, attributes=candidate.get("attributes", {}), martial_skills=candidate.get("martial_skills", {}),
                            professional_skills=professional, qi=0, qi_control=0,
                        )
                        peak = max(developed["martial_skills"].values(), default=0)
                        person = {
                            "person_id": person_ref, "name": name, "birth_year": at.year - age, "sex": sex,
                            "body_mass_kg": deterministic_body_mass_kg(stable=person_ref, sex=sex, age=age),
                            "appearance": int(candidate.get("appearance", 50)), "aptitudes": copy.deepcopy(dict(candidate.get("aptitudes", {}))),
                            "attributes": developed["attributes"], "martial_skills": developed["martial_skills"],
                            "professional_skills": developed["professional_skills"], "qi": developed["qi"], "qi_control": developed["qi_control"],
                            "membership_grade": "junior" if peak >= 25 else "probationary",
                            "joined_year": at.year, "personal_cash": 0,
                        }
                        if epoch_days > 0:
                            person["training_state"] = {"institutional_days_applied": epoch_days}
                        people.append(person)
                        register_new_person_route(fid, person_ref, len(people) - 1)
                        external_refs.append(person_ref)
                    if external_refs:
                        pool["current_population"] = max(0, int(pool.get("current_population", 0)) - len(external_refs))
                        writes[_CIVILIANS_PATH] = civilian_state

                total_joined = len(admitted_refs) + len(external_refs)
                if total_joined > 0:
                    review_roster["people"] = people
                    faction["population"] = max(0, int(faction.get("population", 0))) + total_joined
                    previous_season = faction.get("recruitment_season", {}) if isinstance(faction.get("recruitment_season"), Mapping) else {}
                    used = max(0, int(previous_season.get("intake_used", 0))) if previous_season.get("season_id") == season_id else 0
                    faction["recruitment_season"] = {"season_id": season_id, "intake_used": used + total_joined}
                    faction, _rotation = advance_faction_training_epoch(
                        faction, review_roster, at_iso=at_iso, refresh_environment=True,
                    )
                    faction_cache[fid] = (fpath, faction); roster_cache[fid] = (_rpath, review_roster)
                    writes[fpath] = faction; writes[_rpath] = review_roster
                    world_history = record_event(
                        world_history, at=at_iso, kind="faction_recruitment", faction_ref=fid,
                        independent_count=len(admitted_refs), civilian_count=len(external_refs),
                        person_refs=sorted(admitted_refs + external_refs),
                    )
                    writes[_WORLD_HISTORY_PATH] = world_history
                executed_actions.append({
                    "action": action, "result": "recruited" if total_joined else "no_eligible_candidates",
                    "recruited_independent": admitted_refs, "recruited_external": external_refs, "examined": examined,
                })
                continue

        # Contract-capable factions may autonomously take at most one funded
        # escort job per monthly review.  The contract uses real people, source
        # cargo and the universal commitment registry; no abstract escort force
        # or free shipment is created merely because the AI selected the job.
        autonomous_contract: dict[str, Any] | None = None
        if "evaluate_contracts" in review["ordered_actions"]:
            escort_level = max(0, int(enterprises.get("escort_service", 0)))
            escort_capacity = enterprise_scale_value(faction, "escort_service") if escort_level > 0 else 0
            current_escort_jobs = sum(
                1 for row in active_contracts.values()
                if isinstance(row, Mapping) and row.get("beneficiary_ref") == fid
                and row.get("status") in {"accepted", "in_progress", "objective_resolved"}
            )
            sites = site_rows
            movements = route_ops_state.setdefault("movements", {})
            if not isinstance(movements, dict):
                raise ValueError("jianghu route movement state invalid")
            if escort_capacity <= current_escort_jobs:
                executed_actions.append({"action": "evaluate_contracts", "result": "escort_capacity_full", "capacity": escort_capacity})
            for cid in ([] if escort_capacity <= current_escort_jobs else sorted(str(x) for x in active_contracts)):
                contract = active_contracts.get(cid)
                if not isinstance(contract, Mapping) or contract.get("status") != "offered" or contract.get("beneficiary_ref") not in {None, ""}:
                    continue
                objective = contract.get("objective", {}) if isinstance(contract.get("objective"), Mapping) else {}
                if objective.get("kind") != "escort_shipment":
                    continue
                route_ref = str(objective.get("route_ref") or "")
                route = route_index.get(route_ref)
                if not isinstance(route, Mapping):
                    continue
                source_region = str(objective.get("source_region") or "")
                destination_region = str(objective.get("destination_region") or "")
                ends = [str(route.get("from") or ""), str(route.get("to") or "")]
                origin = next((x for x in ends if place_region.get(x) == source_region), None)
                destination = next((x for x in ends if x != origin and place_region.get(x) == destination_region), None)
                if not origin or not destination or str(faction.get("headquarters", "")) != origin:
                    continue
                minimum = max(1, int(objective.get("minimum_escort_count", 1)))
                available = []
                for person in usable_martial_people(review_roster, exclude_committed=unavailable_person_refs()):
                    ref = str(person.get("person_id", ""))
                    if not ref or ref == player_ref:
                        continue
                    site = sites.get(str(person.get("location_ref"))) if isinstance(sites, Mapping) else None
                    if not isinstance(site, Mapping) or site.get("parent_place_ref") != origin:
                        continue
                    available.append(person)
                available.sort(key=lambda p: (-person_combat_index(p), str(p.get("person_id", ""))))
                if len(available) < minimum:
                    continue
                participants = [str(p["person_id"]) for p in available[:minimum]]
                item_ref = str(objective.get("item_ref") or "")
                quantity = max(0, int(objective.get("quantity", 0)))
                try:
                    mpath, source_market = load_market(source_region)
                except FileNotFoundError:
                    continue
                stock = source_market.get("stock", {}) if isinstance(source_market, Mapping) else {}
                if not isinstance(stock, dict) or quantity <= 0 or int(stock.get(item_ref, 0)) < quantity:
                    continue
                toll_cash = max(0, int(route.get("toll_cash", 0)))
                if max(0, int(source_market.get("cash_pool", 0))) < toll_cash:
                    continue
                try:
                    next_commitments = reserve_resources(
                        commitments_state,
                        resources=[("person", ref, fid) for ref in participants],
                        actor_ref=participants[0], owner_ref=fid, activity_ref=cid,
                        activity_kind="contract_escort", started_at=at_iso, location_ref=origin,
                    )
                    accepted = contract_transition(contract, at=at_iso, to_status="accepted", actor_ref=participants[0], participants=participants)
                    accepted["beneficiary_ref"] = fid
                    started = contract_transition(accepted, at=at_iso, to_status="in_progress", actor_ref=participants[0])
                except ValueError:
                    continue
                stock[item_ref] = int(stock.get(item_ref, 0)) - quantity
                if stock[item_ref] <= 0:
                    stock.pop(item_ref, None)
                if toll_cash > 0:
                    source_market["cash_pool"] = max(0, int(source_market.get("cash_pool", 0))) - toll_cash
                    try:
                        dmpath, destination_market_for_toll = load_market(destination_region)
                    except FileNotFoundError:
                        destination_market_for_toll = None; dmpath = ""
                    if isinstance(destination_market_for_toll, dict):
                        destination_market_for_toll["cash_pool"] = max(0, int(destination_market_for_toll.get("cash_pool", 0))) + toll_cash
                        writes[dmpath] = destination_market_for_toll
                        market_cache[destination_region] = (dmpath, destination_market_for_toll)
                    else:
                        # If no destination market owner exists, keep the toll in
                        # the source aggregate economy rather than destroying cash.
                        source_market["cash_pool"] = max(0, int(source_market.get("cash_pool", 0))) + toll_cash
                speed = max(1.0, float(travel_data.get("mode_speed_km_per_day", {}).get("convoy", 24))) if isinstance(travel_data, Mapping) else 24.0
                terrain_map = travel_data.get("terrain_time_milli", {}) if isinstance(travel_data, Mapping) else {}
                road_map = travel_data.get("road_time_milli", {}) if isinstance(travel_data, Mapping) else {}
                terrain = int(terrain_map.get(str(route.get("terrain", "plain")), 1000)) if isinstance(terrain_map, Mapping) else 1000
                road = int(road_map.get(str(route.get("road_quality", "maintained")), 1000)) if isinstance(road_map, Mapping) else 1000
                required_hours = max(1, int((float(route.get("distance_km", 0)) * 24.0 / speed * terrain * road / 1_000_000.0) + float(route.get("fixed_delay_hours", 0)) + 0.999999))
                movement = {
                    "movement_ref": cid, "contract_ref": cid, "route_ref": route_ref,
                    "origin_place_ref": origin, "destination_place_ref": destination,
                    "source_region": source_region, "destination_region": destination_region,
                    "item_ref": item_ref, "quantity": quantity,
                    "cargo_value_cash": max(0, int(objective.get("cargo_value_cash", 0))),
                    "beneficiary_ref": fid, "participant_refs": participants,
                    "started_at": at_iso, "elapsed_hours": 0, "required_hours": required_hours,
                    "known_escort_count": len(participants), "toll_cash": toll_cash, "status": "active", "repelled_outlaw_refs": [],
                }
                started_objective = copy.deepcopy(dict(objective)); started_objective["cargo_committed"] = True
                started["objective"] = started_objective
                active_after[cid] = started
                active_contracts = active_after
                contract_index = contract_after
                movements[cid] = movement
                commitments_state = next_commitments
                pause_people_for_commitment(fid, participants)
                writes[_CONTRACT_INDEX_PATH] = contract_after
                writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                writes[_COMMITMENTS_PATH] = commitments_state
                writes[mpath] = source_market
                market_cache[source_region] = (mpath, source_market)
                autonomous_contract = {"contract_ref": cid, "participant_refs": participants, "route_ref": route_ref}
                break

        # School tuition and rental property are finite regional services, not
        # passive Level stipends. Paying students are bounded aggregate local
        # demand, but instruction capacity comes from real facilities and actual
        # instructors. Rental units are the enterprise's explicit physical scale.
        school_level = max(0, int(enterprises.get("school_tuition", 0)))
        if school_level > 0 and isinstance(region, str):
            school = school_tuition_snapshot(faction, [p for p in review_people if isinstance(p, Mapping)])
            pools = civilian_state.get("places", {}) if isinstance(civilian_state.get("places"), Mapping) else {}
            local_pool = pools.get(str(faction.get("headquarters", ""))) if isinstance(pools, Mapping) else None
            local_population = max(0, int(local_pool.get("current_population", 0))) if isinstance(local_pool, Mapping) else 0
            demand = max(0, local_population // 750)
            served = min(max(0, int(school.get("served_capacity", 0))), demand)
            if served > 0:
                try:
                    mpath, service_market = load_market(region)
                except FileNotFoundError:
                    service_market = None; mpath = ""
                if isinstance(service_market, dict):
                    school_eff = max(1, enterprise_operating_efficiency_milli("school_tuition", school_level))
                    standard_monthly_labor = general_labor_cash_per_hour * 8 * 30
                    base_tuition = max(1, standard_monthly_labor * 150 // 1000)
                    tuition_per_student = max(1, base_tuition * school_eff // 1000)
                    affordable_students = min(served, max(0, int(service_market.get("cash_pool", 0))) // tuition_per_student)
                    if affordable_students > 0:
                        gross = affordable_students * tuition_per_student
                        operating_cost = gross * 250 // 1000
                        faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + gross - operating_cost
                        service_market["cash_pool"] = max(0, int(service_market.get("cash_pool", 0))) - gross + operating_cost
                        faction_cache[fid] = (fpath, faction); market_cache[region] = (mpath, service_market)
                        writes[fpath] = faction; writes[mpath] = service_market
                        executed_actions.append({
                            "action": "operate_school_tuition", "result": "served",
                            "paying_students": affordable_students, "gross_cash": gross,
                            "operating_cost_cash": operating_cost, "net_cash": gross - operating_cost,
                        })

        rent_level = max(0, int(enterprises.get("property_rent", 0)))
        rent_units = enterprise_scale_value(faction, "property_rent") if rent_level > 0 else 0
        if rent_level > 0 and rent_units > 0 and isinstance(region, str):
            try:
                mpath, rent_market = load_market(region)
            except FileNotFoundError:
                rent_market = None; mpath = ""
            if isinstance(rent_market, dict):
                pools = civilian_state.get("places", {}) if isinstance(civilian_state.get("places"), Mapping) else {}
                local_pool = pools.get(str(faction.get("headquarters", ""))) if isinstance(pools, Mapping) else None
                local_population = max(0, int(local_pool.get("current_population", 0))) if isinstance(local_pool, Mapping) else 0
                occupancy_milli = min(1000, 600 + local_population // 1000)
                rent_eff = max(1, enterprise_operating_efficiency_milli("property_rent", rent_level))
                base_monthly_unit_rent = max(1, int((economy_rules.get("consumables", {}) or {}).get("lodging_common_person_night", {}).get("base_value_cash", 40))) * 30 if isinstance(economy_rules, Mapping) else 1200
                gross_target = rent_units * base_monthly_unit_rent * occupancy_milli * rent_eff // 1_000_000
                gross = min(max(0, gross_target), max(0, int(rent_market.get("cash_pool", 0))))
                if gross > 0:
                    maintenance = gross * 200 // 1000
                    faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + gross - maintenance
                    rent_market["cash_pool"] = max(0, int(rent_market.get("cash_pool", 0))) - gross + maintenance
                    faction_cache[fid] = (fpath, faction); market_cache[region] = (mpath, rent_market)
                    writes[fpath] = faction; writes[mpath] = rent_market
                    executed_actions.append({
                        "action": "operate_property_rent", "result": "occupied", "property_units": rent_units,
                        "occupancy_milli": occupancy_milli, "gross_cash": gross, "maintenance_cash": maintenance,
                        "net_cash": gross - maintenance,
                    })

        # Some smaller brotherhoods are authored as pooled-labour/brokerage
        # societies rather than capital-owning merchant houses.  Their real
        # ``trade_service`` assignees supply the labour and lose the registered
        # 42% duty share of training time.  Cash comes only from the finite
        # regional market, so this is a conserved livelihood rather than an
        # arbitrary monthly faction stipend.
        active_enterprise_count = sum(
            1 for level in enterprises.values()
            if isinstance(level, int) and not isinstance(level, bool) and level > 0
        )
        blocked_service_refs = unavailable_person_refs()
        trade_service_people = [
            p for p in review_people
            if isinstance(p, Mapping)
            and p.get("standing_duty_ref") == "trade_service"
            and isinstance(p.get("person_id"), str)
            and str(p.get("person_id")) not in blocked_service_refs
            and is_faction_member(p)
            and (not isinstance(p.get("health"), Mapping) or p.get("health", {}).get("status") not in {"dead", "incapacitated"})
        ]
        average_trade_commerce = (
            sum(
                max(0, int((p.get("professional_skills", {}) if isinstance(p.get("professional_skills"), Mapping) else {}).get("commerce", 0)))
                for p in trade_service_people
            ) // len(trade_service_people)
            if trade_service_people else 0
        )
        if faction_type(str(faction.get("faction_id") or "")) == "brotherhood_society" and active_enterprise_count == 0 and isinstance(region, str):
            try:
                mpath, livelihood_market = load_market(region)
            except FileNotFoundError:
                livelihood_market = None; mpath = ""
            if isinstance(livelihood_market, dict):
                livelihood = operate_brotherhood_livelihood_month(
                    livelihood_market,
                    worker_count=len(trade_service_people),
                    average_commerce=average_trade_commerce,
                    general_labor_cash_per_hour=general_labor_cash_per_hour,
                )
                earned = max(0, int(livelihood.get("cash_earned", 0)))
                if earned > 0:
                    faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + earned
                    livelihood_market = livelihood["market"]
                    faction_cache[fid] = (fpath, faction); market_cache[region] = (mpath, livelihood_market)
                    writes[fpath] = faction; writes[mpath] = livelihood_market
                executed_actions.append({
                    "action": "operate_brotherhood_livelihood",
                    "result": str(livelihood.get("reason", "evaluated")),
                    "worker_count": int(livelihood.get("worker_count", 0)),
                    "labor_hours": int(livelihood.get("labor_hours", 0)),
                    "cash_earned": earned,
                })

        # Registered outlaw cells have a routine economic side in addition to
        # discrete route robberies.  Real trade-service operators represent
        # smugglers/fences/collectors; registered cell scale and enterprise
        # level bound throughput; regional aggregate cash is the conserved
        # counterparty.  No cell, no worker, or no market cash means no income.
        criminal_level = max(0, int(enterprises.get("criminal_enterprise", 0)))
        criminal_scale = enterprise_scale_value(faction, "criminal_enterprise") if criminal_level > 0 else 0
        if criminal_level > 0 and criminal_scale > 0 and isinstance(region, str):
            try:
                mpath, criminal_market = load_market(region)
            except FileNotFoundError:
                criminal_market = None; mpath = ""
            if isinstance(criminal_market, dict):
                autonomy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
                criminal = operate_criminal_enterprise_month(
                    criminal_market,
                    enterprise_level=criminal_level,
                    registered_ventures=criminal_scale,
                    worker_count=len(trade_service_people),
                    average_commerce=average_trade_commerce,
                    risk_tolerance=max(0, int(autonomy.get("risk_tolerance", 50))),
                    general_labor_cash_per_hour=general_labor_cash_per_hour,
                )
                earned = max(0, int(criminal.get("cash_earned", 0)))
                if earned > 0:
                    faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + earned
                    criminal_market = criminal["market"]
                    faction_cache[fid] = (fpath, faction); market_cache[region] = (mpath, criminal_market)
                    writes[fpath] = faction; writes[mpath] = criminal_market
                executed_actions.append({
                    "action": "operate_criminal_enterprise",
                    "result": str(criminal.get("reason", "evaluated")),
                    "active_ventures": int(criminal.get("active_ventures", 0)),
                    "worker_count": int(criminal.get("worker_count", 0)),
                    "cash_earned": earned,
                })

        # Agriculture is an actual seasonal enterprise. Managed acreage is a
        # finite operating scale, not a Level-derived abstraction. Planting
        # spends seed and aggregate local farm-labor cash into the regional
        # economy, reserves acreage via exact harvest obligations, and later
        # produces physical food/herbs rather than passive cash income.
        agriculture_level = max(0, int(enterprises.get("agriculture_landholding", 0)))
        managed_land_mu = enterprise_scale_value(faction, "agriculture_landholding") if agriculture_level > 0 else 0
        planted_now: list[dict[str, Any]] = []
        if agriculture_level > 0 and managed_land_mu > 0 and isinstance(region, str):
            free_land_mu = max(0, managed_land_mu - active_agriculture_mu(fid))
            try:
                mpath, agriculture_market = load_market(region)
            except FileNotFoundError:
                agriculture_market = None
                mpath = ""
            if free_land_mu > 0 and isinstance(agriculture_market, dict):
                food_crop = eligible_crop(("staple_grain", "legumes", "vegetables"), at.month)
                medicine_level = max(0, int(enterprises.get("medicine_apothecary", 0)))
                herb_crop = eligible_crop(
                    ("ginseng", "astragalus", "angelica", "licorice", "ginger", "coptis", "safflower", "notoginseng", "corydalis"),
                    at.month, stable=f"{world_seed}|{fid}|{at.year:04d}-{at.month:02d}|herb",
                ) if medicine_level > 0 else None
                allocations: list[tuple[str, int]] = []
                if food_crop is not None:
                    food_share = free_land_mu if herb_crop is None else max(1, free_land_mu * 4 // 5)
                    allocations.append((food_crop, food_share))
                if herb_crop is not None:
                    herb_share = free_land_mu - sum(qty for _ref, qty in allocations)
                    if herb_share > 0:
                        allocations.append((herb_crop, herb_share))
                policy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
                reserve_months = max(1, min(6, int(policy.get("reserve_cash_months", 2))))
                reserve_cash = monthly_cash * reserve_months
                spendable = max(0, int(faction.get("treasury_cash", 0)) - reserve_cash)
                agriculture_efficiency = max(1, enterprise_operating_efficiency_milli("agriculture_landholding", agriculture_level))
                holdings = faction.get("holdings", {}) if isinstance(faction.get("holdings"), Mapping) else {}
                rural_place = str(holdings.get("rural_place_ref") or faction.get("headquarters", ""))
                for crop_ref, desired_mu in allocations:
                    if desired_mu <= 0 or spendable <= 0:
                        continue
                    crop = crop_record(crop_ref)
                    base_labor_hours_per_mu = max(0, int(crop.get("labor_days_per_mu", 0))) * 8
                    labor_hours_per_mu = max(1, (base_labor_hours_per_mu * 1000 + agriculture_efficiency - 1) // agriculture_efficiency)
                    seed_per_mu = max(0, int(crop.get("seed_cost_cash_per_mu", 0)))
                    cost_per_mu = seed_per_mu + labor_hours_per_mu * general_labor_cash_per_hour
                    affordable_mu = desired_mu if cost_per_mu <= 0 else min(desired_mu, spendable // cost_per_mu)
                    if affordable_mu <= 0:
                        continue
                    cost_cash = affordable_mu * cost_per_mu
                    quote = harvest_quote(
                        world_seed=world_seed, place_id=rural_place, crop_ref=crop_ref, planted_mu=affordable_mu,
                        planted_at=at, agriculture_level=agriculture_level, labor_coverage_milli=1000,
                    )
                    faction["treasury_cash"] = int(faction.get("treasury_cash", 0)) - cost_cash
                    agriculture_market["cash_pool"] = max(0, int(agriculture_market.get("cash_pool", 0))) + cost_cash
                    spendable -= cost_cash
                    event_id = f"agriculture_harvest:{fid}:{crop_ref}:{at.date().isoformat()}"
                    pending_one_off_events.append({
                        "event_id": event_id,
                        "kind": "agriculture_harvest_due",
                        "due_at": str(quote["harvest_at"]),
                        "faction_ref": fid,
                        "crop_ref": crop_ref,
                        "planted_mu": affordable_mu,
                        "planted_at": at_iso,
                        "place_id": rural_place,
                        "agriculture_level": agriculture_level,
                        "labor_coverage_milli": 1000,
                        "requires_player_decision": False,
                    })
                    planted_now.append({
                        "crop_ref": crop_ref, "planted_mu": affordable_mu,
                        "cost_cash": cost_cash, "harvest_at": quote["harvest_at"],
                    })
                if planted_now:
                    faction_cache[fid] = (fpath, faction)
                    market_cache[region] = (mpath, agriculture_market)
                    writes[fpath] = faction
                    writes[mpath] = agriculture_market
                    executed_actions.append({"action": "operate_agriculture", "result": "planted", "crops": planted_now})

        # Workshops and apothecaries are real operating enterprises.  Monthly
        # settlement consumes actual worker duty time, stations and physical
        # inputs; produced stock may be sold only into finite regional demand.
        if isinstance(region, str):
            blocked_ops = unavailable_person_refs()
            workshop_level = max(0, int(enterprises.get("crafting_workshop", 0)))
            if workshop_level > 0:
                workers = [
                    p for p in review_people if isinstance(p, Mapping)
                    and p.get("standing_duty_ref") == "workshop_service"
                    and isinstance(p.get("person_id"), str) and p.get("person_id") not in blocked_ops
                ]
                workers.sort(key=lambda p: (-int((p.get("professional_skills") or {}).get("crafting", 0)), str(p.get("person_id"))))
                physical = workshop_capacity(buildings, faction.get("infrastructure", {}))
                scale_stations = enterprise_scale_value(faction, "crafting_workshop")
                stations = min(max(0, int(physical.get("craft_workstations", 0))), max(0, scale_stations))
                active_workers = workers[:stations]
                if active_workers:
                    best_skill = max(int((p.get("professional_skills") or {}).get("crafting", 0)) for p in active_workers)
                    profile = faction_profile(fid) or {}
                    training = profile.get("training", {}) if isinstance(profile, Mapping) and isinstance(profile.get("training"), Mapping) else {}
                    weapon_domain = max(("sword","spear","bow","hidden_weapons"), key=lambda k: int(training.get(k,0)))
                    if weapon_domain == "sword": recipe_ref = "jian"
                    elif weapon_domain == "spear": recipe_ref = "spear"
                    elif weapon_domain == "bow":
                        equip = inventory.get("equipment", {}) if isinstance(inventory.get("equipment"), Mapping) else {}
                        recipe_ref = "arrows" if int(equip.get("item_arrow",0)) < max(24, int(equip.get("weapon_bow",0))*24) else "bow"
                    else: recipe_ref = "needle"
                    try:
                        mpath, production_market = load_market(region)
                        reserve = max(2, int(faction.get("population",0))*3//5) if recipe_ref not in {"arrows","needle"} else max(24, int(faction.get("population",0))*2)
                        op = operate_workshop_month(
                            inventory, production_market, region_id=region, recipe_ref=recipe_ref,
                            workshop_level=workshop_level, crafting_skill=best_skill,
                            available_worker_hours=len(active_workers)*105, reserve_quantity=reserve,
                            max_batches=max(1, stations),
                        )
                    except (KeyError, ValueError):
                        op = {"batches":0,"reason":"no_qualified_recipe"}
                    if int(op.get("batches",0))>0:
                        inventory=op["inventory"]; production_market=op["market"]
                        faction["treasury_cash"] = max(0,int(faction.get("treasury_cash",0))) + int(op.get("cash_earned",0))
                        inventory_cache[fid]=(ipath,inventory); faction_cache[fid]=(fpath,faction); market_cache[region]=(mpath,production_market)
                        writes[ipath]=inventory; writes[fpath]=faction; writes[mpath]=production_market
                        executed_actions.append({"action":"operate_crafting_workshop",**{k:op.get(k) for k in ("recipe_ref","batches","produced","sold","cash_earned","labor_hours")}})

            medicine_level = max(0, int(enterprises.get("medicine_apothecary", 0)))
            if medicine_level > 0:
                med_workers = [
                    p for p in review_people if isinstance(p, Mapping)
                    and p.get("standing_duty_ref") == "infirmary_service"
                    and isinstance(p.get("person_id"), str) and p.get("person_id") not in blocked_ops
                ]
                med_workers.sort(key=lambda p: (-int((p.get("professional_skills") or {}).get("medicine", 0)), str(p.get("person_id"))))
                inf = infirmary_capacity(buildings, faction.get("infrastructure", {}))
                stations = min(max(0,int(inf.get("apothecary_workstations",0))), max(0,enterprise_scale_value(faction,"medicine_apothecary")))
                med_active=med_workers[:stations]
                if med_active:
                    med_skill=max(int((p.get("professional_skills") or {}).get("medicine",0)) for p in med_active)
                    candidates=[("stamina_tonic",1,35),("pain_tonic",1,35),("blood_tonic",2,55),("wound_salve",2,55),("detox_medicine",2,55),("bone_medicine",3,80),("internal_injury_medicine",3,80),("nerve_antidote",3,80),("blood_cardiac_antidote",3,80)]
                    meds=inventory.get("medicines",{}) if isinstance(inventory.get("medicines"),Mapping) else {}
                    allowed=[ref for ref,lvl,skill in candidates if medicine_level>=lvl and med_skill>=skill]
                    allowed.sort(key=lambda ref:(int(meds.get(ref,0)),ref))
                    if allowed:
                        try:
                            mpath, apoth_market=load_market(region)
                            recipe_ref=allowed[0]
                            op=operate_apothecary_month(
                                inventory,apoth_market,recipe_ref=recipe_ref,apothecary_level=medicine_level,medicine_skill=med_skill,
                                available_worker_hours=len(med_active)*105,reserve_doses=max(5,int(faction.get("population",0))//10),max_batches=max(1,stations),
                            )
                        except (KeyError,ValueError):
                            op={"batches":0,"reason":"inputs_or_recipe"}
                        if int(op.get("batches",0))>0:
                            inventory=op["inventory"]; apoth_market=op["market"]
                            faction["treasury_cash"] = max(0,int(faction.get("treasury_cash",0))) + int(op.get("cash_earned",0))
                            inventory_cache[fid]=(ipath,inventory); faction_cache[fid]=(fpath,faction); market_cache[region]=(mpath,apoth_market)
                            writes[ipath]=inventory; writes[fpath]=faction; writes[mpath]=apoth_market
                            executed_actions.append({"action":"operate_medicine_apothecary",**{k:op.get(k) for k in ("recipe_ref","batches","produced","sold","cash_earned","labor_hours")}})

        # Merchant enterprises are not passive local-sale multipliers. Once a
        # month they may commit one real caravan to a profitable adjacent
        # regional spread. The route owner handles danger, delivery, sale and
        # return on later physical frontiers.
        if max(0, int(enterprises.get("trade_merchant_business", 0))) > 0:
            merchant_trade = start_monthly_merchant_trade(fid)
            executed_actions.append({"action": "operate_trade_merchant_business", **merchant_trade})

        if autonomous_contract:
            _rpath, review_roster = load_roster(fid)
        infirmary = infirmary_capacity(buildings, faction.get("infrastructure", {}))
        lifecycle = advance_institution(
            faction, review_roster, year=at.year, month=at.month, social=social_state, player_ref=player_ref or None,
            unavailable_refs=sorted(unavailable_person_refs()),
            infirmary_beds=max(0, int(infirmary.get("beds", 0))),
        )
        lifecycle_roster = lifecycle["roster"]
        lifecycle_summary = lifecycle["summary"]
        roster_changed = lifecycle_roster != review_roster
        if roster_changed:
            writes[_rpath] = lifecycle_roster
            roster_cache[fid] = (_rpath, lifecycle_roster)
        environment_changed = bool(
            roster_changed
            or lifecycle_summary.get("appointments")
            or lifecycle_summary.get("recovered_refs")
            or lifecycle_summary.get("promoted_refs")
            or lifecycle_summary.get("duty_changes")
            or max(0, int(enterprises.get("school_tuition", 0))) > 0
        )
        if environment_changed:
            faction, _rotation = advance_faction_training_epoch(
                faction, lifecycle_roster, at_iso=at_iso,
                refresh_environment=True,
            )
        if training_capacity > 0:
            executed_actions.append({"action": "train_members", "result": "standing_epoch_advanced"})
        if lifecycle_summary["recovered_refs"]:
            executed_actions.append({"action": "recover_injured", "count": len(lifecycle_summary["recovered_refs"])})
        if lifecycle_summary["appointments"]:
            executed_actions.append({"action": "fill_offices", "count": len(lifecycle_summary["appointments"])})
        if lifecycle_summary["promoted_refs"]:
            executed_actions.append({"action": "membership_promotion", "count": len(lifecycle_summary["promoted_refs"])})
        if autonomous_contract:
            executed_actions.append({"action": "evaluate_contracts", "result": "escort_started", **autonomous_contract})

        # Every action emitted by autonomy_review is backed by a production
        # evaluator in this frontier.  Record a compact no-change result for
        # lifecycle/contract actions whose evaluator lawfully found nothing to
        # mutate; never leave "ordered" pseudo-actions that no runtime path
        # actually consumed.
        executed_names = {
            str(row.get("action")) for row in executed_actions
            if isinstance(row, Mapping) and isinstance(row.get("action"), str)
        }
        for action in review["ordered_actions"]:
            if action not in executed_names:
                executed_actions.append({"action": action, "result": "evaluated_no_change"})
        writes[fpath] = faction
        reviewed_factions.add(fid)
        reviews.append({
            "kind": "faction_review",
            "event_id": event.get("event_id"),
            "faction_ref": fid,
            "food_reserve_days": food_reserve_days,
            "cash_reserve_months": cash_reserve_months,
            "office_vacancies": int(institutional["office_vacancies"]),
            "market_shortages": market_shortages,
            "ordered_actions": list(review["ordered_actions"]),
            "executed_actions": executed_actions,
        })

    # Spectator/representative delegations are one bounded group movement per
    # faction.  Planning happened at registration opening; departure rechecks
    # the current named people and current conserved resources.
    for event in sorted_events:
        if event.get("kind") != "tournament_delegation_departure":
            continue
        fid = str(event.get("faction_ref") or event.get("owner_ref") or "")
        tref = str(event.get("tournament_ref") or "")
        registry = tournament_state.get("tournaments", {}) if isinstance(tournament_state, Mapping) else {}
        tournament = registry.get(tref) if isinstance(registry, Mapping) else None
        if not fid or not isinstance(tournament, Mapping) or tournament.get("status") == "completed":
            reviews.append({
                "kind": "tournament_delegation_departure", "event_id": event.get("event_id"),
                "tournament_ref": tref, "faction_ref": fid, "result": "tournament_unavailable",
            })
            continue
        outcome = start_tournament_delegation_trip(
            fid, candidate_refs=[str(x) for x in event.get("candidate_refs", []) if isinstance(x, str)],
            tournament_ref=tref, host_place=str(event.get("host_place") or tournament.get("host_place_ref") or ""),
            host_cash_per_person_day=max(0, int(event.get("host_cash_per_person_day", 0))),
            delegate_ticket_cash_per_day=max(0, int(event.get("delegate_ticket_cash_per_day", 0))),
            minimum_host_days=max(1, int(event.get("minimum_host_days", 1))),
            latest_arrival_at=str(event.get("latest_arrival_at") or ""),
        )
        reviews.append({
            "kind": "tournament_delegation_departure", "event_id": event.get("event_id"),
            "tournament_ref": tref, "faction_ref": fid, **dict(outcome),
        })

    # Registration opening creates future travel plans; the actual departure
    # frontier rechecks current faction/person resources so a months-old plan
    # cannot reserve a fighter or spend money prematurely.
    for event in sorted_events:
        if event.get("kind") != "tournament_trip_departure":
            continue
        fid = str(event.get("faction_ref") or "")
        person_ref = str(event.get("person_ref") or event.get("owner_ref") or "")
        tref = str(event.get("tournament_ref") or "")
        registry = tournament_state.get("tournaments", {}) if isinstance(tournament_state, Mapping) else {}
        tournament = registry.get(tref) if isinstance(registry, Mapping) else None
        if not fid or not person_ref or not isinstance(tournament, Mapping) or tournament.get("status") != "registration_open":
            reviews.append({
                "kind": "tournament_trip_departure", "event_id": event.get("event_id"),
                "tournament_ref": tref, "faction_ref": fid, "person_ref": person_ref,
                "result": "registration_not_open",
            })
            continue
        outcome = start_tournament_trip(
            fid, person_ref=person_ref, tournament_ref=tref,
            host_place=str(event.get("host_place") or tournament.get("host_place_ref") or ""),
            registration_closes_on=str(event.get("registration_closes_on") or tournament.get("registration_closes_on") or ""),
            entry_fee_cash=max(0, int(event.get("entry_fee_cash", tournament.get("entry_fee_cash", 0)))),
            host_cash_per_person_day=max(0, int(event.get("host_cash_per_person_day", 0))),
            minimum_host_days=max(1, int(event.get("minimum_host_days", 1))),
        )
        reviews.append({
            "kind": "tournament_trip_departure", "event_id": event.get("event_id"),
            "tournament_ref": tref, "faction_ref": fid, "person_ref": person_ref,
            **dict(outcome),
        })

    # Distant tournament entrants travel under real commitments. They register
    # only after physically reaching the host before the registration deadline.
    # Entry sponsorship is faction-funded: the fee was reserved from treasury
    # at departure and is transferred only on successful physical registration.
    def refund_reserved_tournament_fee(fid: str, op: Mapping[str, Any]) -> int:
        amount = max(0, int(op.get("entry_fee_reserved_cash", 0)))
        if amount <= 0 or not fid:
            return 0
        try:
            fpath, faction = load_faction(fid)
        except (KeyError, FileNotFoundError, ValueError):
            return 0
        faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + amount
        writes[fpath] = faction; faction_cache[fid] = (fpath, faction)
        return amount

    def send_tournament_trip_home(op_ref: str, op: Mapping[str, Any], *, refund_fee: bool) -> tuple[dict[str, Any], int]:
        fid = str(op.get("faction_ref") or "")
        current = copy.deepcopy(dict(op))
        refunded = refund_reserved_tournament_fee(fid, current) if refund_fee else 0
        host_refunded = refund_reserved_host_spend(fid, op_ref, current) if refund_fee else 0
        if refunded > 0:
            current["entry_fee_reserved_cash"] = 0
        if host_refunded > 0:
            current["host_spend_reserved_cash"] = 0
            current["host_spend_refunded_cash"] = host_refunded
        current["status"] = "traveling_return"
        return_at = at + timedelta(hours=max(1.0, float(current.get("travel_hours", 24.0))))
        current["return_arrival_at"] = return_at.isoformat()
        deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
        if isinstance(deployments, dict):
            deployments[op_ref] = current
        pending_one_off_events.append({
            "event_id": f"tournament_return_arrival:{op_ref}", "kind": "tournament_return_arrival",
            "due_at": return_at.isoformat(), "owner_ref": op_ref, "requires_player_decision": False,
        })
        writes[_DEPLOYMENTS_PATH] = deployments_state
        return current, refunded

    def _refund_faction_cash(fid: str, amount_cash: int) -> int:
        amount=max(0,int(amount_cash))
        if amount<=0 or not fid:
            return 0
        try:
            fpath,faction=load_faction(fid)
        except (KeyError,FileNotFoundError,ValueError):
            return 0
        faction["treasury_cash"]=max(0,int(faction.get("treasury_cash",0)))+amount
        writes[fpath]=faction; faction_cache[fid]=(fpath,faction)
        return amount

    def refund_reserved_host_spend(fid: str, op_ref: str, op: Mapping[str, Any], *, amount_cash: int | None = None) -> int:
        reserved=max(0,int(op.get("host_spend_reserved_cash",0)))
        amount=reserved if amount_cash is None else min(reserved,max(0,int(amount_cash)))
        refunded=_refund_faction_cash(fid,amount)
        deployments=deployments_state.get("deployments",{}) if isinstance(deployments_state,Mapping) else {}
        current=deployments.get(op_ref) if isinstance(deployments,Mapping) else None
        if isinstance(current,dict) and refunded>0:
            current["host_spend_reserved_cash"]=max(0,int(current.get("host_spend_reserved_cash",0))-refunded)
        return refunded

    def refund_reserved_delegate_ticket(fid: str, op_ref: str, op: Mapping[str, Any], *, amount_cash: int | None = None) -> int:
        reserved=max(0,int(op.get("delegate_ticket_reserved_cash",0)))
        amount=reserved if amount_cash is None else min(reserved,max(0,int(amount_cash)))
        refunded=_refund_faction_cash(fid,amount)
        deployments=deployments_state.get("deployments",{}) if isinstance(deployments_state,Mapping) else {}
        current=deployments.get(op_ref) if isinstance(deployments,Mapping) else None
        if isinstance(current,dict) and refunded>0:
            current["delegate_ticket_reserved_cash"]=max(0,int(current.get("delegate_ticket_reserved_cash",0))-refunded)
        return refunded

    for event in sorted_events:
        if event.get("kind") != "tournament_delegation_arrival":
            continue
        op_ref = str(event.get("owner_ref") or "")
        deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
        op = deployments.get(op_ref) if isinstance(deployments, Mapping) else None
        if not isinstance(op, Mapping) or op.get("operation_kind") != "tournament_delegation" or op.get("status") != "traveling_outbound":
            reviews.append({"kind": "tournament_delegation_arrival", "event_id": event.get("event_id"), "result": "delegation_not_active"})
            continue
        fid = str(op.get("faction_ref") or ""); tref = str(op.get("tournament_ref") or "")
        refs = [str(x) for x in op.get("participant_refs", []) if isinstance(x, str)]
        leader_refs = [str(x) for x in op.get("leader_refs", []) if isinstance(x, str)]
        senior_refs = [str(x) for x in op.get("senior_refs", []) if isinstance(x, str)]
        tournaments = tournament_state.get("tournaments", {}) if isinstance(tournament_state, Mapping) else {}
        tournament = tournaments.get(tref) if isinstance(tournaments, Mapping) else None
        if not fid or not refs or not isinstance(tournament, Mapping) or tournament.get("status") == "completed":
            refunded = refund_reserved_host_spend(fid, op_ref, op)
            ticket_refunded = refund_reserved_delegate_ticket(fid, op_ref, op)
            _current, _fee_refund = send_tournament_trip_home(op_ref, op, refund_fee=False)
            reviews.append({
                "kind": "tournament_delegation_arrival", "event_id": event.get("event_id"),
                "delegation_ref": op_ref, "result": "tournament_unavailable_returning",
                "host_spend_refunded_cash": refunded, "delegate_ticket_refunded_cash": ticket_refunded,
            })
            continue
        host_place = str(tournament.get("host_place_ref") or op.get("target_place_ref") or "")
        venue = str(tournament.get("venue_site_ref") or _tournament_venue_site(local_sites, host_place) or host_place)
        rpath, roster = load_roster(fid); rows = roster.get("people", []) if isinstance(roster, Mapping) else []
        present_refs: list[str] = []
        if isinstance(rows, list):
            for i, raw in enumerate(rows):
                if not isinstance(raw, Mapping) or str(raw.get("person_id")) not in refs:
                    continue
                health = raw.get("health", {}) if isinstance(raw.get("health"), Mapping) else {}
                if health.get("status") == "dead":
                    continue
                person = copy.deepcopy(dict(raw)); person["location_ref"] = venue; rows[i] = person
                present_refs.append(str(person.get("person_id")))
            roster["people"] = rows; writes[rpath] = roster; roster_cache[fid] = (rpath, roster)
        if not present_refs:
            refunded = refund_reserved_host_spend(fid, op_ref, op)
            ticket_refunded = refund_reserved_delegate_ticket(fid, op_ref, op)
            _current, _fee_refund = send_tournament_trip_home(op_ref, op, refund_fee=False)
            reviews.append({
                "kind": "tournament_delegation_arrival", "event_id": event.get("event_id"),
                "delegation_ref": op_ref, "result": "delegation_missing_returning",
                "host_spend_refunded_cash": refunded, "delegate_ticket_refunded_cash": ticket_refunded,
            })
            continue
        planned_count=max(1,len(refs)); present_count=len(present_refs)
        host_reserved=max(0,int(op.get("host_spend_reserved_cash",0)))
        host_per=max(0,int(op.get("host_spend_per_person_cash",0)))
        host_spend=min(host_reserved,host_per*present_count if host_per>0 else host_reserved*present_count//planned_count)
        host_refund=max(0,host_reserved-host_spend)
        if host_refund>0:
            _refund_faction_cash(fid,host_refund)
        ticket_reserved=max(0,int(op.get("delegate_ticket_reserved_cash",0)))
        ticket_per=max(0,int(op.get("delegate_ticket_per_person_cash",0)))
        delegate_ticket_cash=min(ticket_reserved,ticket_per*present_count if ticket_per>0 else ticket_reserved*present_count//planned_count)
        delegate_ticket_refund=max(0,ticket_reserved-delegate_ticket_cash)
        if delegate_ticket_refund>0:
            _refund_faction_cash(fid,delegate_ticket_refund)
        host_region = str(tournament.get("host_region") or "")
        if host_spend > 0 and host_region:
            try:
                mpath, market = load_market(host_region)
                market["cash_pool"] = max(0, int(market.get("cash_pool", 0))) + host_spend
                writes[mpath] = market; market_cache[host_region] = (mpath, market)
            except FileNotFoundError:
                _refund_faction_cash(fid,host_spend)
                host_spend = 0
        if delegate_ticket_cash>0:
            tournament=tournament_add_attendance_prize_cash(
                tournament,amount_cash=delegate_ticket_cash,source_kind="faction_delegate_ticket",
            )
            tournaments[tref]=tournament
            writes[_TOURNAMENTS_PATH]=tournament_state
        current = copy.deepcopy(dict(op)); current["status"] = "at_tournament"; current["arrived_at"] = at_iso
        current["participant_refs"] = present_refs; current["host_spend_reserved_cash"] = 0
        current["delegate_ticket_reserved_cash"] = 0; current["host_spend_cash"] = host_spend
        current["delegate_ticket_cash"] = delegate_ticket_cash
        deployments[op_ref] = current; writes[_DEPLOYMENTS_PATH] = deployments_state
        present_set = set(present_refs)
        _add_tournament_delegation_presence(
            tref, fid, spectator_refs=present_refs,
            leader_refs=[ref for ref in leader_refs if ref in present_set],
            senior_refs=[ref for ref in senior_refs if ref in present_set],
        )
        world_history = record_event(
            world_history, at=at_iso, kind="tournament_delegation_arrived", faction_ref=fid,
            tournament_ref=tref, participant_count=len(present_refs),
            leader_count=len([ref for ref in leader_refs if ref in present_set]),
            senior_count=len([ref for ref in senior_refs if ref in present_set]),
            host_spend_cash=host_spend, delegate_ticket_cash=delegate_ticket_cash,
            host_spend_refunded_cash=host_refund, delegate_ticket_refunded_cash=delegate_ticket_refund,
        )
        writes[_WORLD_HISTORY_PATH] = world_history
        reviews.append({
            "kind": "tournament_delegation_arrival", "event_id": event.get("event_id"),
            "delegation_ref": op_ref, "tournament_ref": tref, "faction_ref": fid,
            "participant_count": len(present_refs), "host_spend_cash": host_spend,
            "delegate_ticket_cash": delegate_ticket_cash,
            "host_spend_refunded_cash": host_refund, "delegate_ticket_refunded_cash": delegate_ticket_refund,
            "prize_cash": max(0,int(tournament.get("prize_escrow_cash",0))),
            "result": "arrived",
        })

    for event in sorted_events:
        if event.get("kind") != "tournament_travel_arrival":
            continue
        op_ref = str(event.get("owner_ref") or "")
        deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
        op = deployments.get(op_ref) if isinstance(deployments, Mapping) else None
        if not isinstance(op, Mapping) or op.get("operation_kind") != "tournament_travel" or op.get("status") != "traveling_outbound":
            reviews.append({"kind": "tournament_travel_arrival", "event_id": event.get("event_id"), "result": "trip_not_active"}); continue
        fid = str(op.get("faction_ref") or ""); tref = str(op.get("tournament_ref") or ""); refs = [str(x) for x in op.get("participant_refs", []) if isinstance(x, str)]
        tournaments = tournament_state.get("tournaments", {}) if isinstance(tournament_state, Mapping) else {}
        tournament = tournaments.get(tref) if isinstance(tournaments, Mapping) else None
        if not fid or len(refs) != 1 or not isinstance(tournament, Mapping) or tournament.get("status") != "registration_open":
            _current, refunded = send_tournament_trip_home(op_ref, op, refund_fee=True)
            reviews.append({"kind": "tournament_travel_arrival", "event_id": event.get("event_id"), "trip_ref": op_ref, "result": "registration_closed_returning", "entry_fee_refunded_cash": refunded}); continue
        person_ref = refs[0]; host_place = str(tournament.get("host_place_ref") or op.get("target_place_ref") or ""); venue = str(tournament.get("venue_site_ref") or _tournament_venue_site(local_sites, host_place) or host_place)
        rpath, roster = load_roster(fid); rows = roster.get("people", []) if isinstance(roster, Mapping) else []
        idx = next((i for i, raw in enumerate(rows) if isinstance(raw, Mapping) and raw.get("person_id") == person_ref), None) if isinstance(rows, list) else None
        if idx is None:
            _current, refunded = send_tournament_trip_home(op_ref, op, refund_fee=True)
            reviews.append({"kind": "tournament_travel_arrival", "event_id": event.get("event_id"), "trip_ref": op_ref, "result": "entrant_missing_returning", "entry_fee_refunded_cash": refunded}); continue
        person = copy.deepcopy(dict(rows[idx])); person["location_ref"] = venue
        health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
        alive = health.get("status") != "dead"; medically_eligible = health.get("status") not in {"dead", "incapacitated"}
        host_region = str(tournament.get("host_region") or "")
        try:
            mpath, organizer_market = load_market(host_region) if host_region else ("", None)
        except FileNotFoundError:
            organizer_market = None; mpath = ""
        if not isinstance(organizer_market, dict):
            _current, refunded = send_tournament_trip_home(op_ref, op, refund_fee=True)
            reviews.append({"kind": "tournament_travel_arrival", "event_id": event.get("event_id"), "trip_ref": op_ref, "result": "organizer_market_missing_returning", "entry_fee_refunded_cash": refunded}); continue
        reserved_fee = max(0, int(op.get("entry_fee_reserved_cash", 0)))
        try:
            audience = (reputation_state.get("audiences", {}) or {}).get(person_ref, {}) if isinstance(reputation_state.get("audiences", {}), Mapping) else {}
            qualifying = int(audience.get("public_score", public_score(audience))) if isinstance(audience, Mapping) else 0
            reg = tournament_register(
                tournament, entrant_ref=person_ref, qualifying_score=qualifying,
                payer_cash=reserved_fee, alive=alive, medically_eligible=medically_eligible,
            )
        except (TypeError, ValueError):
            _current, refunded = send_tournament_trip_home(op_ref, op, refund_fee=True)
            reviews.append({"kind": "tournament_travel_arrival", "event_id": event.get("event_id"), "trip_ref": op_ref, "result": "registration_failed_returning", "entry_fee_refunded_cash": refunded}); continue
        tournament = dict(reg["tournament_after"]); tournament["registrations"][-1]["faction_ref"] = fid
        tournaments[tref] = tournament
        entrant_is_leader, entrant_is_senior = _tournament_delegate_roles(person)
        _add_tournament_delegation_presence(
            tref, fid, entrant_refs=[person_ref],
            leader_refs=[person_ref] if entrant_is_leader else (),
            senior_refs=[person_ref] if entrant_is_senior else (),
        )
        tournament = dict(tournaments[tref])
        rows[idx] = person; roster["people"] = rows
        host_spend = max(0, int(op.get("host_spend_reserved_cash", 0)))
        organizer_market["cash_pool"] = (
            max(0, int(organizer_market.get("cash_pool", 0)))
            + host_spend
        )
        writes[mpath] = organizer_market; market_cache[host_region] = (mpath, organizer_market)
        current = copy.deepcopy(dict(op)); current["status"] = "at_tournament"; current["arrived_at"] = at_iso
        current["entry_fee_reserved_cash"] = 0; current["host_spend_reserved_cash"] = 0; current["host_spend_cash"] = host_spend
        deployments[op_ref] = current
        writes[rpath] = roster; roster_cache[fid] = (rpath, roster); writes[_TOURNAMENTS_PATH] = tournament_state; writes[_DEPLOYMENTS_PATH] = deployments_state
        world_history = record_event(
            world_history, at=at_iso, kind="tournament_travel_arrived", faction_ref=fid,
            person_ref=person_ref, tournament_ref=tref,
            entry_fee_cash=int(tournament.get("entry_fee_cash", 0)),
            prize_contribution_cash=int(reg.get("prize_contribution_cash", 0)),
            host_spend_cash=host_spend,
        )
        writes[_WORLD_HISTORY_PATH] = world_history
        reviews.append({
            "kind": "tournament_travel_arrival", "event_id": event.get("event_id"), "trip_ref": op_ref,
            "tournament_ref": tref, "faction_ref": fid, "person_ref": person_ref,
            "entry_fee_cash": int(tournament.get("entry_fee_cash", 0)),
            "host_spend_cash": host_spend,
            "prize_cash": int(tournament.get("prize_escrow_cash", 0)), "result": "registered",
        })

    for event in sorted_events:
        if event.get("kind") != "tournament_return_arrival":
            continue
        op_ref = str(event.get("owner_ref") or ""); deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}; op = deployments.get(op_ref) if isinstance(deployments, Mapping) else None
        if not isinstance(op, Mapping) or op.get("operation_kind") not in {"tournament_travel", "tournament_delegation"} or op.get("status") != "traveling_return":
            reviews.append({"kind": "tournament_return_arrival", "event_id": event.get("event_id"), "result": "trip_not_returning"}); continue
        fid = str(op.get("faction_ref") or ""); refs = [str(x) for x in op.get("participant_refs", []) if isinstance(x, str)]; source_place = str(op.get("source_place_ref") or ""); source_site = str(op.get("source_site_ref") or "") or _arrival_site(local_sites, source_place)
        if fid and source_site:
            rpath, roster = load_roster(fid); rows = roster.get("people", []) if isinstance(roster, Mapping) else []
            if isinstance(rows, list):
                out_rows: list[Any] = []
                for raw in rows:
                    if isinstance(raw, Mapping) and str(raw.get("person_id")) in refs:
                        person = copy.deepcopy(dict(raw)); person["location_ref"] = source_site; out_rows.append(person)
                    else: out_rows.append(raw)
                roster["people"] = out_rows; writes[rpath] = roster; roster_cache[fid] = (rpath, roster)
        commitments_state = settle_and_resume_people(refs, activity_ref=op_ref, commitments_state=commitments_state); deployments.pop(op_ref, None)
        writes[_DEPLOYMENTS_PATH] = deployments_state; writes[_COMMITMENTS_PATH] = commitments_state
        world_history = record_event(world_history, at=at_iso, kind="tournament_travel_returned", faction_ref=fid, tournament_ref=op.get("tournament_ref"), participant_count=len(refs)); writes[_WORLD_HISTORY_PATH] = world_history
        reviews.append({"kind": "tournament_return_arrival", "event_id": event.get("event_id"), "trip_ref": op_ref, "faction_ref": fid, "returned_count": len(refs), "result": "completed"})

    # Autonomous strategic operations are finite deployment owners. Arrival uses
    # actual participants/defenders and the exact combat resolver; surviving
    # attackers then require real return travel before their commitment ends.
    strategic_frontier_used: set[str] = set()
    for event in sorted_events:
        if event.get("kind") != "faction_operation_arrival":
            continue
        op_ref = str(event.get("owner_ref") or "")
        deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
        op = deployments.get(op_ref) if isinstance(deployments, Mapping) else None
        if not isinstance(op, Mapping) or op.get("status") != "traveling_outbound":
            reviews.append({"kind": "faction_operation_arrival", "event_id": event.get("event_id"), "result": "operation_not_active"})
            continue
        fid = str(op.get("faction_ref") or ""); target_fid = str(op.get("target_faction_ref") or "")
        kind = str(op.get("operation_kind") or "")
        if not fid or not target_fid or kind not in {"formal_challenge", "faction_raid", "faction_war_strike"}:
            reviews.append({"kind": "faction_operation_arrival", "event_id": event.get("event_id"), "result": "operation_invalid"})
            continue
        _fp, source_faction = load_faction(fid); _tfp, target_faction = load_faction(target_fid)
        rpath, source_roster = load_roster(fid); trpath, target_roster = load_roster(target_fid)
        target_place = str(op.get("target_place_ref") or target_faction.get("headquarters") or "")
        target_site = _arrival_site(local_sites, target_place) or str(target_faction.get("local_site_ref") or target_place)
        participant_refs = [str(x) for x in op.get("participant_refs", []) if isinstance(x, str)]
        source_people = source_roster.get("people", []) if isinstance(source_roster, Mapping) else []
        target_people = target_roster.get("people", []) if isinstance(target_roster, Mapping) else []
        if not isinstance(source_people, list) or not isinstance(target_people, list):
            raise ValueError("jianghu strategic roster invalid")
        attackers: list[dict[str, Any]] = []
        for raw in source_people:
            if not isinstance(raw, Mapping) or str(raw.get("person_id")) not in participant_refs:
                continue
            health = raw.get("health", {}) if isinstance(raw.get("health"), Mapping) else {}
            if health.get("status") == "dead":
                continue
            person = copy.deepcopy(dict(raw)); person["location_ref"] = target_site; attackers.append(person)
        attacker_refs = [str(p["person_id"]) for p in attackers if isinstance(p.get("person_id"), str)]
        blocked_defenders = unavailable_person_refs() | strategic_frontier_used | set(attacker_refs)
        defenders = [
            p for p in usable_martial_people(target_roster, exclude_committed=blocked_defenders)
            if person_place(p, home_place=target_place, home_site_ref=str(target_faction.get("local_site_ref") or "")) == target_place
            and at.year - int(p.get("birth_year", at.year)) >= 16
            and not bool(p.get("retired_from_field", False))
            and str(p.get("person_id")) != player_ref
        ]
        defenders.sort(key=lambda p: (-person_combat_index(p), str(p.get("person_id", ""))))
        defender_need = 1 if kind == "formal_challenge" else (max(1, min(20, len(attacker_refs) + 4)) if kind == "faction_war_strike" else max(1, min(8, len(attacker_refs) + 1)))
        defenders = defenders[:defender_need]
        defender_refs = [str(p["person_id"]) for p in defenders if isinstance(p.get("person_id"), str)]
        # Write physical arrival even if the target has nobody currently able to
        # answer; attackers did not teleport directly into a combat result.
        source_by_ref = {str(p.get("person_id")): i for i, p in enumerate(source_people) if isinstance(p, Mapping) and isinstance(p.get("person_id"), str)}
        for person in attackers:
            idx = source_by_ref.get(str(person.get("person_id")))
            if idx is not None: source_people[idx] = person
        source_roster["people"] = source_people; writes[rpath] = source_roster; roster_cache[fid] = (rpath, source_roster)
        winner_side = None; exchanges = 0; newly_dead: set[str] = set(); combat_resolved = False
        if attacker_refs and defender_refs:
            people_map = {str(p["person_id"]): copy.deepcopy(dict(p)) for p in attackers + defenders}
            doctrines = {
                fid: source_faction.get("doctrine", {}) if isinstance(source_faction.get("doctrine"), Mapping) else {},
                target_fid: target_faction.get("doctrine", {}) if isinstance(target_faction.get("doctrine"), Mapping) else {},
            }
            result = simulate_exact_combat(
                combat_ref=f"combat:{op_ref}", side_a_refs=attacker_refs, side_b_refs=defender_refs,
                people=people_map, equipment_ledger=equipment_ledger, doctrines=doctrines,
                zone_ref=target_site, started_at=at_iso,
                objective={"kind": kind, "source_faction_ref": fid, "target_faction_ref": target_fid},
                targeting_intent="disable" if kind == "formal_challenge" else "lethal",
                max_exchanges=160 if kind == "formal_challenge" else (128 if kind == "faction_war_strike" else 96),
            )
            equipment_ledger = copy.deepcopy(dict(result["equipment_ledger_after"])); writes[_EQUIPMENT_LEDGER_PATH] = equipment_ledger
            after = result["people_after"]; winner_side = result.get("winner_side"); exchanges = int(result.get("exchanges", 0)); combat_resolved = bool(result.get("resolved"))
            # Persist attacker/defender injuries and deaths into their exact rosters.
            for owner_fid, owner_path, owner_roster in ((fid, rpath, source_roster), (target_fid, trpath, target_roster)):
                rows = owner_roster.get("people", []) if isinstance(owner_roster, Mapping) else []
                if not isinstance(rows, list): continue
                changed = False; replaced: list[Any] = []
                for raw in rows:
                    ref = raw.get("person_id") if isinstance(raw, Mapping) else None
                    if isinstance(ref, str) and ref in after:
                        replaced.append(copy.deepcopy(dict(after[ref]))); changed = True
                    else: replaced.append(raw)
                if changed:
                    owner_roster["people"] = replaced; writes[owner_path] = owner_roster; roster_cache[owner_fid] = (owner_path, owner_roster)
            newly_dead = {
                str(ref) for ref, person in after.items()
                if isinstance(ref, str) and isinstance(person, Mapping)
                and isinstance(person.get("health"), Mapping) and person.get("health", {}).get("status") == "dead"
            }
            if newly_dead:
                close_dead_current_authorities(sorted(newly_dead))
                for owner_fid in (fid, target_fid):
                    ofpath, ofaction = load_faction(owner_fid); orpath, oroster = load_roster(owner_fid)
                    ofaction = reconcile_faction_population(ofaction, oroster)
                    writes[ofpath] = ofaction; faction_cache[owner_fid] = (ofpath, ofaction)
                world_history = record_event(world_history, at=at_iso, kind="combat_deaths", faction_ref=fid, target_faction_ref=target_fid, person_refs=sorted(newly_dead), count=len(newly_dead))
        strategic_frontier_used.update(attacker_refs); strategic_frontier_used.update(defender_refs)
        if kind in {"faction_raid", "faction_war_strike"}:
            relation_event = "war_battle" if kind == "faction_war_strike" else "armed_raid"
            apply_directed_relation_event(fid, target_fid, relation_event)
            apply_directed_relation_event(target_fid, fid, relation_event)
        else:
            apply_directed_relation_event(fid, target_fid, "tournament_sportsmanship")
            apply_directed_relation_event(target_fid, fid, "tournament_sportsmanship")
        # A death is a stronger grievance than the controlled challenge/raid
        # frame that produced it.  Apply the existing member-killed relation
        # event once per bereaved institution, regardless of casualty count, so
        # real combat can escalate a feud without multiplying hostility merely
        # because several people died in the same encounter.
        if newly_dead:
            attacker_death = any(ref in newly_dead for ref in attacker_refs)
            defender_death = any(ref in newly_dead for ref in defender_refs)
            if attacker_death:
                apply_directed_relation_event(fid, target_fid, "member_killed")
            if defender_death:
                apply_directed_relation_event(target_fid, fid, "member_killed")
        surviving_attackers = [ref for ref in attacker_refs if ref not in newly_dead]
        if surviving_attackers:
            travel_hours = max(1.0, float(op.get("travel_hours", 24.0)))
            return_at = at + timedelta(hours=travel_hours)
            current = copy.deepcopy(dict(op)); current["participant_refs"] = surviving_attackers
            current["status"] = "traveling_return"; current["return_arrival_at"] = return_at.isoformat()
            deployments[op_ref] = current
            pending_one_off_events.append({"event_id": f"operation_return:{op_ref}", "kind": "faction_operation_return", "due_at": return_at.isoformat(), "owner_ref": op_ref, "requires_player_decision": False})
        else:
            commitments_state = release_resources(commitments_state, activity_ref=op_ref)
            deployments.pop(op_ref, None)
        world_history = record_event(
            world_history, at=at_iso, kind=f"{kind}_contact", faction_ref=fid, target_faction_ref=target_fid,
            source_camp=faction_camp(fid) or "unclassified", target_camp=faction_camp(target_fid) or "unclassified",
            participant_count=len(attacker_refs), defender_count=len(defender_refs), deaths=len(newly_dead), winner_side=winner_side,
        )
        writes[_WORLD_HISTORY_PATH] = world_history; writes[_DEPLOYMENTS_PATH] = deployments_state; writes[_COMMITMENTS_PATH] = commitments_state
        review_row = {"kind": "faction_operation_arrival", "event_id": event.get("event_id"), "operation_ref": op_ref, "operation_kind": kind, "faction_ref": fid, "target_faction_ref": target_fid, "source_camp": faction_camp(fid) or "unclassified", "target_camp": faction_camp(target_fid) or "unclassified", "attacker_count": len(attacker_refs), "defender_count": len(defender_refs), "exchanges": exchanges, "combat_resolved": combat_resolved, "winner_side": winner_side, "deaths": len(newly_dead), "result": "returning" if surviving_attackers else "closed"}
        reviews.append(review_row)
        if target_fid == "house_tang":
            notice = {**review_row, "kind": "faction_war_result" if kind == "faction_war_strike" else ("faction_attack_result" if kind == "faction_raid" else "faction_challenge_result"), "delivered_to_player": True, "requires_player_decision": False}
            handoff = classify_handoff(notice); handoffs.append({**notice, "handoff": handoff})

    for event in sorted_events:
        if event.get("kind") != "faction_operation_return":
            continue
        op_ref = str(event.get("owner_ref") or "")
        deployments = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
        op = deployments.get(op_ref) if isinstance(deployments, Mapping) else None
        if not isinstance(op, Mapping) or op.get("status") != "traveling_return":
            reviews.append({"kind": "faction_operation_return", "event_id": event.get("event_id"), "result": "operation_not_returning"}); continue
        fid = str(op.get("faction_ref") or ""); source_place = str(op.get("source_place_ref") or "")
        source_site = _arrival_site(local_sites, source_place)
        if fid and source_site:
            rpath, roster = load_roster(fid); rows = roster.get("people", []) if isinstance(roster, Mapping) else []
            refs = {str(x) for x in op.get("participant_refs", []) if isinstance(x, str)}
            if isinstance(rows, list):
                out_rows: list[Any] = []
                for raw in rows:
                    if isinstance(raw, Mapping) and str(raw.get("person_id")) in refs:
                        person = copy.deepcopy(dict(raw)); person["location_ref"] = source_site; out_rows.append(person)
                    else: out_rows.append(raw)
                roster["people"] = out_rows; writes[rpath] = roster; roster_cache[fid] = (rpath, roster)
        refs = [str(x) for x in op.get("participant_refs", []) if isinstance(x, str)]
        commitments_state = settle_and_resume_people(refs, activity_ref=op_ref, commitments_state=commitments_state)
        deployments.pop(op_ref, None)
        writes[_DEPLOYMENTS_PATH] = deployments_state; writes[_COMMITMENTS_PATH] = commitments_state
        world_history = record_event(world_history, at=at_iso, kind="faction_operation_returned", faction_ref=fid, operation_kind=op.get("operation_kind"), participant_count=len(refs))
        writes[_WORLD_HISTORY_PATH] = world_history
        reviews.append({"kind": "faction_operation_return", "event_id": event.get("event_id"), "operation_ref": op_ref, "faction_ref": fid, "returned_count": len(refs), "result": "completed"})

    # Autonomous projects use the same project reducers as player-issued work.
    # Funds/materials were consumed at start; due settlement applies only actual
    # committed labor/time and mutates the one existing facility/enterprise.
    for event in sorted_events:
        if event.get("kind") != "autonomous_project_due":
            continue
        project_ref = str(event.get("owner_ref") or "")
        registry = projects_state.get("projects", {}) if isinstance(projects_state, Mapping) else {}
        row = registry.get(project_ref) if isinstance(registry, Mapping) else None
        if not isinstance(row, Mapping):
            reviews.append({"kind": "autonomous_project_due", "event_id": event.get("event_id"), "result": "project_missing"}); continue
        project = copy.deepcopy(dict(row)); fid = str(project.get("faction_ref") or "")
        if not fid:
            reviews.append({"kind": "autonomous_project_due", "event_id": event.get("event_id"), "result": "project_owner_missing"}); continue
        fpath, faction = load_faction(fid); rpath, roster = load_roster(fid)
        faction, _training = advance_faction_training_epoch(faction, roster, at_iso=at_iso, refresh_environment=False)
        days = max(1, int(project.get("planned_days", project.get("minimum_calendar_days", 1))))
        general_workers = len(project.get("general_worker_refs", [])); skilled_workers = len(project.get("skilled_worker_refs", [])); management_workers = len(project.get("management_worker_refs", []))
        try:
            if project.get("project_type") == "building_upgrade":
                updated = advance_building_upgrade(project, elapsed_calendar_days=days, general_labor_hours=general_workers*8*days, skilled_labor_hours=skilled_workers*6*days)
            elif project.get("project_type") == "building_expansion":
                updated = advance_building_expansion(project, elapsed_calendar_days=days, general_labor_hours=general_workers*8*days, skilled_labor_hours=skilled_workers*6*days)
            elif project.get("project_type") == "enterprise_scale_expansion":
                updated = advance_enterprise_scale_expansion(project, elapsed_calendar_days=days, management_labor_hours=management_workers*4*days, general_setup_labor_hours=general_workers*4*days)
            else:
                raise ValueError("unsupported autonomous project")
        except ValueError:
            reviews.append({"kind": "autonomous_project_due", "event_id": event.get("event_id"), "project_ref": project_ref, "result": "project_progress_invalid"}); continue
        if not updated.get("completed"):
            registry[project_ref] = updated
            next_due = at + timedelta(days=1)
            pending_one_off_events.append({"event_id": f"autonomous_project_due:{project_ref}", "kind": "autonomous_project_due", "due_at": next_due.isoformat(), "owner_ref": project_ref, "requires_player_decision": False})
            writes[_PROJECTS_PATH] = projects_state
            reviews.append({"kind": "autonomous_project_due", "event_id": event.get("event_id"), "project_ref": project_ref, "result": "work_remaining"}); continue
        if updated["project_type"] == "building_upgrade":
            faction.setdefault("buildings", {})[updated["building_type"]] = int(updated["target_level"])
        elif updated["project_type"] == "building_expansion":
            facilities = faction.setdefault("infrastructure", {}).setdefault("facilities", {})
            facility = facilities.setdefault(updated["building_type"], {})
            facility["footprint_m2"] = max(0, int(facility.get("footprint_m2", 0))) + int(updated["additional_footprint_m2"])
        else:
            scales = faction.setdefault("enterprise_scale", {}); erow = scales.setdefault(updated["enterprise_type"], {})
            basis = str(updated["scale_basis"]); erow[basis] = max(0, int(erow.get(basis, 0))) + int(updated["additional_scale"])
            if updated["enterprise_type"] == "agriculture_landholding":
                holdings = faction.setdefault("holdings", {})
                holdings["cultivated_land_mu"] = min(max(0, int(holdings.get("rural_land_mu", 0))), max(0, int(holdings.get("cultivated_land_mu", 0))) + int(updated["additional_scale"]))
        faction, _rotation = advance_faction_training_epoch(faction, roster, at_iso=at_iso, refresh_environment=True)
        writes[fpath] = faction; faction_cache[fid] = (fpath, faction)
        worker_refs = list(dict.fromkeys(project.get("skilled_worker_refs", []) + project.get("management_worker_refs", []) + project.get("general_worker_refs", [])))
        commitments_state = settle_and_resume_people(worker_refs, activity_ref=project_ref, commitments_state=commitments_state)
        registry.pop(project_ref, None)
        writes[_PROJECTS_PATH] = projects_state; writes[_COMMITMENTS_PATH] = commitments_state
        world_history = record_event(world_history, at=at_iso, kind="infrastructure_project_completed", faction_ref=fid, project_ref=project_ref, project_type=updated.get("project_type"), building_type=updated.get("building_type"), enterprise_type=updated.get("enterprise_type"))
        writes[_WORLD_HISTORY_PATH] = world_history
        reviews.append({"kind": "autonomous_project_due", "event_id": event.get("event_id"), "project_ref": project_ref, "faction_ref": fid, "project_type": updated.get("project_type"), "result": "completed"})

    # Equipment maintenance is real workshop labor and material replacement.
    # Full-condition items are free to ignore; damaged issued/personal durable
    # items are repaired only when an actual workshop, craftsman, repair bay and
    # recipe-compatible materials are present.
    for event in sorted_events:
        if event.get("kind") != "equipment_maintenance_review":
            continue
        fid = str(event.get("owner_ref") or "")
        if not fid:
            continue
        fpath, faction = load_faction(fid); rpath, roster = load_roster(fid); ipath, inventory = load_inventory(fid)
        buildings = faction.get("buildings", {}) if isinstance(faction.get("buildings"), Mapping) else {}
        workshop_level = max(0, int(buildings.get("armory_workshop", 0)))
        physical = workshop_capacity(buildings, faction.get("infrastructure", {})) if workshop_level > 0 else {}
        repair_bays = max(0, int(physical.get("repair_bays", 0)))
        workers = [
            p for p in usable_martial_people(roster, exclude_committed=unavailable_person_refs())
            if p.get("standing_duty_ref") == "workshop_service"
        ]
        workers.sort(key=lambda p: (-int((p.get("professional_skills") or {}).get("crafting", 0)), str(p.get("person_id", ""))))
        active_workers = workers[:repair_bays]
        if not active_workers:
            reviews.append({"kind": "equipment_maintenance_review", "event_id": event.get("event_id"), "faction_ref": fid, "result": "no_repair_capacity"})
            continue
        crafting_skill = max(int((p.get("professional_skills") or {}).get("crafting", 0)) for p in active_workers)
        logical = hydrate_equipment_ledger(equipment_ledger)
        loadouts = logical.get("person_loadouts", {}) if isinstance(logical, Mapping) else {}
        faction_refs = {
            str(p.get("person_id")) for p in roster.get("people", [])
            if isinstance(p, Mapping) and isinstance(p.get("person_id"), str) and is_faction_member(p)
        } if isinstance(roster.get("people", []), list) else set()
        candidates: list[tuple[int, str, str]] = []
        if isinstance(loadouts, Mapping):
            for person_ref in sorted(faction_refs):
                row = loadouts.get(person_ref)
                if not isinstance(row, Mapping):
                    continue
                items = row.get("items", {}) if isinstance(row.get("items"), Mapping) else {}
                cond = row.get("condition_milli", {}) if isinstance(row.get("condition_milli"), Mapping) else {}
                for item_ref, qty in items.items():
                    if int(qty) <= 0:
                        continue
                    current = max(0, min(1000, int(cond.get(item_ref, 1000))))
                    if current < 1000:
                        candidates.append((current, person_ref, str(item_ref)))
        candidates.sort()
        raw_materials = inventory.get("raw_materials", {}) if isinstance(inventory.get("raw_materials"), Mapping) else {}
        raw_materials = {str(k): max(0, int(v)) for k, v in raw_materials.items()}
        labor_hours_left = len(active_workers) * 105
        repaired: list[dict[str, Any]] = []
        for current, person_ref, item_ref in candidates:
            if labor_hours_left <= 0 or len(repaired) >= max(1, repair_bays * 8):
                break
            try:
                quote = equipment_repair_quote(integrity_milli=current, target_integrity_milli=1000, crafting_skill=crafting_skill)
                req = repair_material_requirements(item_ref=item_ref, integrity_restored_milli=int(quote["integrity_restored_milli"]), quantity=1)
            except (KeyError, ValueError):
                continue
            hours = max(0, int(quote.get("crafting_hours", 0)))
            if hours > labor_hours_left or any(raw_materials.get(ref, 0) < int(qty) for ref, qty in req.items()):
                continue
            for ref, qty in req.items():
                raw_materials[ref] = raw_materials.get(ref, 0) - int(qty)
            prow = loadouts.get(person_ref)
            if not isinstance(prow, dict):
                prow = copy.deepcopy(dict(prow or {})); loadouts[person_ref] = prow
            conditions = prow.setdefault("condition_milli", {})
            if not isinstance(conditions, dict):
                conditions = {}; prow["condition_milli"] = conditions
            conditions[item_ref] = 1000
            labor_hours_left -= hours
            repaired.append({"person_ref": person_ref, "item_ref": item_ref, "integrity_before_milli": current, "labor_hours": hours, "materials": req})
        if repaired:
            inventory["raw_materials"] = raw_materials
            equipment_ledger = compact_equipment_ledger(logical)
            writes[ipath] = inventory; inventory_cache[fid] = (ipath, inventory); writes[_EQUIPMENT_LEDGER_PATH] = equipment_ledger
            reviews.append({"kind": "equipment_maintenance_review", "event_id": event.get("event_id"), "faction_ref": fid, "result": "repaired", "repair_count": len(repaired), "repairs": repaired[:16]})
        else:
            reviews.append({"kind": "equipment_maintenance_review", "event_id": event.get("event_id"), "faction_ref": fid, "result": "no_repairable_damage"})

    # Trade demand is derived from finite post-cycle market stock.  It creates
    # opportunities only; contract escrow must still come from a real issuer.
    # Trade-demand publication is a region-class reduction, not a per-region
    # side effect. Run it exactly once in the chunk containing the final region
    # owner, after all regional market cycles for this monthly boundary are
    # settled. This makes bounded scheduler chunk size semantically invisible.
    if (
        any(event.get("kind") == "trade_demand_review" for event in sorted_events)
        and _chunk_contains_final_owner(schedule, sorted_events, class_id="region_monthly")
    ):
        try:
            economy_data = read_json(_REGIONAL_ECONOMY_PATH)
        except FileNotFoundError:
            economy_data = {"regions": {}}
        regions = economy_data.get("regions", {}) if isinstance(economy_data, Mapping) else {}
        states: dict[str, Mapping[str, Any]] = {}
        if isinstance(regions, Mapping):
            for region in sorted(str(x) for x in regions):
                try:
                    _path, market = load_market(region)
                except FileNotFoundError:
                    continue
                states[region] = market
        opportunities = trade_shipment_opportunities(
            market_states=states,
            route_rows=list(route_index.values()),
            place_to_region=place_region,
        )
        # Turn bounded real shipment demand into funded public escort offers.
        # Regional market cash is the issuer treasury; no merchant NPC ledger is
        # needed merely to fund an ordinary caravan contract.
        contract_after = copy.deepcopy(dict(contract_index))
        active_after = contract_after.setdefault("active", {})
        created_contracts: list[str] = []
        existing_sources = {
            str(row.get("source_ref"))
            for row in active_after.values()
            if isinstance(row, Mapping) and isinstance(row.get("source_ref"), str)
        }
        terrain_threat = {"plain": 10, "hills": 18, "forest": 22, "marsh": 24, "mountain": 30, "desert": 28}
        for opp in opportunities[:16]:
            if not isinstance(opp, Mapping):
                continue
            src = str(opp.get("source_region", "")); dst = str(opp.get("destination_region", ""))
            route_id = str(opp.get("route_id", "")); item_ref = str(opp.get("item_ref", ""))
            qty = max(0, int(opp.get("quantity", 0))); distance_tenths = max(0, int(opp.get("distance_km_tenths", 0)))
            route = route_index.get(route_id)
            if not src or not dst or not route_id or qty <= 0 or not isinstance(route, Mapping):
                continue
            source_ref = f"trade:{route_id}:{src}:{dst}:{item_ref}:{at.date().isoformat()}"
            if source_ref in existing_sources:
                continue
            try:
                mpath, market = load_market(src)
            except FileNotFoundError:
                continue
            stock = market.get("stock", {}) if isinstance(market, Mapping) else {}
            try:
                unit = unit_market_price_cash(src, item_ref, stock if isinstance(stock, Mapping) else {})
            except (KeyError, TypeError, ValueError):
                continue
            cargo_value = unit * qty
            terrain = str(route.get("terrain", "plain"))
            outlaw_pressure = route_threat_score(outlaws_for_route(route_id), route_id=route_id)
            threat = min(100, terrain_threat.get(terrain, 15) + distance_tenths // 250 + outlaw_pressure // 6)
            escort_count = max(2, min(8, 2 + threat // 20))
            # Convoy duration uses the registered route geometry/terrain/road
            # model rather than a separate escort-only speed approximation.
            try:
                travel_data = read_json("game/data/martial-world/travel.json")
                speed = max(1.0, float(travel_data.get("mode_speed_km_per_day", {}).get("convoy", 24)))
                terrain_milli = int(travel_data.get("terrain_time_milli", {}).get(terrain, 1000))
                road_milli = int(travel_data.get("road_time_milli", {}).get(str(route.get("road_quality", "maintained")), 1000))
                normal_hours = max(1, int((float(route.get("distance_km", 0)) * 24.0 / speed * terrain_milli * road_milli / 1_000_000.0) + float(route.get("fixed_delay_hours", 0)) + 0.999999))
            except (TypeError, ValueError):
                normal_hours = max(1, (distance_tenths + 34) // 35)
            quote = escort_quote(
                distance_km_tenths=distance_tenths,
                cargo_value_cash=cargo_value,
                threat_score=threat,
                escort_count=escort_count,
                normal_travel_hours=normal_hours,
                deadline_hours=normal_hours,
            )
            reward = int(quote["total_reward_cash"])
            if int(market.get("cash_pool", 0)) < reward:
                continue
            objective = {
                "kind": "escort_shipment",
                "route_ref": route_id,
                "source_region": src,
                "destination_region": dst,
                "item_ref": item_ref,
                "quantity": qty,
                "cargo_value_cash": cargo_value,
                "minimum_escort_count": escort_count,
            }
            try:
                funded = funded_contract_offer(
                    issuer_cash=int(market.get("cash_pool", 0)),
                    contract_type="escort",
                    issuer_ref=f"market:{src}",
                    beneficiary_ref=None,
                    offered_at=at_iso,
                    expires_at=(at.replace(microsecond=0) + timedelta(days=30)).isoformat(),
                    reward_cash=reward,
                    objective=objective,
                    source_ref=source_ref,
                )
            except (KeyError, TypeError, ValueError):
                continue
            contract = dict(funded["contract"])
            active_after[contract["contract_id"]] = contract
            market["cash_pool"] = int(funded["issuer_cash_after"])
            writes[mpath] = market
            market_cache[src] = (mpath, market)
            created_contracts.append(contract["contract_id"])
            pending_one_off_events.append({
                "event_id": f"contract_expiry_due:{contract['contract_id']}",
                "kind": "contract_expiry_due",
                "due_at": str(contract["expires_at"]),
                "owner_ref": str(contract["contract_id"]),
                "requires_player_decision": False,
            })
            existing_sources.add(source_ref)
        if created_contracts:
            writes[_CONTRACT_INDEX_PATH] = contract_after
        reviews.append({
            "kind": "trade_demand_review",
            "opportunity_count": len(opportunities),
            "opportunities": opportunities[:64],
            "truncated": len(opportunities) > 64,
            "funded_contracts_created": created_contracts,
        })

    # Active route operations are physical world owners.  Daily route frontiers
    # advance only movements on that exact route, evaluate finite outlaw forces,
    # resolve autonomous NPC contacts through exact combat, and stop for a hard
    # player handoff when the player is present.  Resolved contacts do not become
    # an append-only history; current injuries/equipment/cargo/cash are authority.
    route_events = [e for e in sorted_events if e.get("kind") == "route_activity_cycle"]
    if route_events:
        movements = route_ops_state.setdefault("movements", {})
        contacts = route_ops_state.setdefault("contacts", {})
        if not isinstance(movements, dict) or not isinstance(contacts, dict):
            raise ValueError("jianghu route operations invalid")
        # Route owners are resumably chunked at the same timestamp. Keep the
        # per-day outlaw raid budget as a tiny current-day accumulator so
        # changing transaction chunk size cannot grant extra attacks.
        attack_tracker = route_ops_state.get("daily_outlaw_attack_budget", {})
        if not isinstance(attack_tracker, Mapping) or attack_tracker.get("date") != at.date().isoformat():
            attack_tracker = {"date": at.date().isoformat(), "counts": {}}
        else:
            attack_tracker = copy.deepcopy(dict(attack_tracker))
        outlaw_attack_counts = attack_tracker.setdefault("counts", {})
        if not isinstance(outlaw_attack_counts, dict):
            raise ValueError("jianghu route outlaw attack budget invalid")

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

        def _close_merchant_trade(movement: Mapping[str, Any], *, success: bool, outlaw_fid: str | None = None) -> dict[str, Any]:
            nonlocal commitments_state, world_history
            movement_ref = str(movement.get("movement_ref") or "")
            beneficiary = str(movement.get("beneficiary_ref") or "")
            participants = [str(x) for x in movement.get("participant_refs", []) if isinstance(x, str)]
            if not movement_ref or not beneficiary:
                return {"closed": False, "reason": "merchant_movement_invalid"}
            leg = str(movement.get("trade_leg") or "outbound")
            item_ref = str(movement.get("item_ref") or "")
            quantity = max(0, int(movement.get("quantity", 0)))
            purchase_cash = max(0, int(movement.get("purchase_cash", 0)))
            toll_cash = max(0, int(movement.get("toll_cash", 0)))

            if leg == "outbound" and not success:
                # A defeated caravan loses its carried goods to the attackers,
                # but surviving staff still need real route time to get home.
                if outlaw_fid and quantity > 0:
                    oipath, outlaw_inventory = load_inventory(outlaw_fid)
                    _credit_cargo_to_inventory(outlaw_inventory, item_ref=item_ref, quantity=quantity)
                    writes[oipath] = outlaw_inventory; inventory_cache[outlaw_fid] = (oipath, outlaw_inventory)
                world_history = record_event(
                    world_history, at=at_iso, kind="merchant_trade_robbed", faction_ref=beneficiary,
                    route_ref=str(movement.get("route_ref") or ""), outlaw_faction_ref=outlaw_fid,
                    item_ref=item_ref, quantity=quantity, cash_loss=purchase_cash + toll_cash,
                )
                writes[_WORLD_HISTORY_PATH] = world_history
                if not participants:
                    commitments_state = release_resources(commitments_state, activity_ref=movement_ref)
                    movements.pop(movement_ref, None)
                    writes[_COMMITMENTS_PATH] = commitments_state; writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                    return {"closed": True, "success": False, "cargo_lost": quantity}
                returning = copy.deepcopy(dict(movement))
                returning["quantity"] = 0; returning["cargo_value_cash"] = 0
                returning["trade_leg"] = "return"; returning["status"] = "active"; returning["elapsed_hours"] = 0
                returning["robbed_by_faction_ref"] = outlaw_fid; returning["sale_cash"] = 0
                movements[movement_ref] = returning
                writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                return {"closed": False, "success": False, "returning_after_loss": True, "cargo_lost": quantity}

            if leg == "outbound":
                destination_region = str(movement.get("destination_region") or "")
                destination_place = str(movement.get("destination_place_ref") or "")
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
                returning = copy.deepcopy(dict(movement))
                returning["trade_leg"] = "return"; returning["status"] = "active"; returning["elapsed_hours"] = 0
                returning["sale_cash"] = sale_cash
                if sale_succeeded:
                    returning["quantity"] = 0; returning["cargo_value_cash"] = 0
                else:
                    returning["sale_failed"] = True
                movements[movement_ref] = returning
                world_history = record_event(
                    world_history, at=at_iso, kind=("merchant_trade_sold" if sale_succeeded else "merchant_trade_sale_deferred"),
                    faction_ref=beneficiary, route_ref=str(movement.get("route_ref") or ""),
                    destination_region=destination_region, item_ref=item_ref, quantity=quantity,
                    purchase_cash=purchase_cash, sale_cash=sale_cash,
                    cash_profit=(sale_cash - purchase_cash - toll_cash) if sale_succeeded else None,
                )
                writes[_WORLD_HISTORY_PATH] = world_history; writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                return {"closed": False, "success": sale_succeeded, "returning": True, "sale_cash": sale_cash}

            # Return leg. Any unsold cargo remains a real faction asset rather
            # than vanishing because the destination market ran out of cash.
            source_place = str(movement.get("origin_place_ref") or "")
            _move_faction_people(beneficiary, participants, source_place)
            if quantity > 0 and item_ref:
                ipath, inventory = load_inventory(beneficiary)
                _credit_cargo_to_inventory(inventory, item_ref=item_ref, quantity=quantity)
                writes[ipath] = inventory; inventory_cache[beneficiary] = (ipath, inventory)
            commitments_state = settle_and_resume_people(
                participants, activity_ref=movement_ref, commitments_state=commitments_state,
            )
            sale_cash = max(0, int(movement.get("sale_cash", 0)))
            movements.pop(movement_ref, None)
            world_history = record_event(
                world_history, at=at_iso, kind="merchant_trade_completed", faction_ref=beneficiary,
                route_ref=str(movement.get("route_ref") or ""), item_ref=item_ref,
                purchase_cash=purchase_cash, sale_cash=sale_cash,
                cash_profit=sale_cash - purchase_cash - toll_cash,
                unsold_quantity=quantity,
            )
            writes[_WORLD_HISTORY_PATH] = world_history
            writes[_COMMITMENTS_PATH] = commitments_state; writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
            return {"closed": True, "success": sale_cash > 0, "sale_cash": sale_cash, "unsold_quantity": quantity}

        def _close_escort(movement: Mapping[str, Any], *, success: bool, outlaw_fid: str | None = None) -> dict[str, Any]:
            nonlocal commitments_state, contract_index, active_contracts, reputation_state, social_state, world_history
            if movement.get("movement_kind") == "merchant_trade":
                return _close_merchant_trade(movement, success=success, outlaw_fid=outlaw_fid)
            cid = str(movement.get("contract_ref") or movement.get("movement_ref") or "")
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
                destination_region = str(movement.get("destination_region") or "")
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
                destination_place = str(movement.get("destination_place_ref") or "")
                arrival = _arrival_site(local_sites, destination_place)
                commitments_state = settle_and_resume_people(participants, activity_ref=cid, commitments_state=commitments_state)
                if beneficiary and arrival:
                    rpath, roster = load_roster(beneficiary)
                    rows = roster.get("people", []) if isinstance(roster, Mapping) else []
                    if isinstance(rows, list):
                        new_rows = []
                        refs = set(participants)
                        for raw in rows:
                            if isinstance(raw, Mapping) and raw.get("person_id") in refs:
                                person = copy.deepcopy(dict(raw)); person["location_ref"] = arrival; new_rows.append(person)
                            else:
                                new_rows.append(raw)
                        roster["people"] = new_rows
                        writes[rpath] = roster
                        roster_cache[beneficiary] = (rpath, roster)
                paid = int(payment["paid_cash"])
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

                # A completed escort is real prolonged shared travel.  If the
                # convoy also repelled an outlaw contact, it was shared danger
                # as well. Bound pairwise updates to the actual small escort
                # party so current relationships grow without schedule bloat.
                social_party = participants[:12]
                shared_danger = bool(movement.get("repelled_outlaw_refs"))
                for observer_ref in social_party:
                    for subject_ref in social_party:
                        if observer_ref == subject_ref:
                            continue
                        social_state = _social_event(
                            social_state, observer_ref=observer_ref, subject_ref=subject_ref,
                            event_kind="shared_travel", severity_milli=350, player_ref=player_ref,
                        )
                        if shared_danger:
                            social_state = _social_event(
                                social_state, observer_ref=observer_ref, subject_ref=subject_ref,
                                event_kind="shared_danger", severity_milli=550, player_ref=player_ref,
                            )
                if len(social_party) > 1:
                    writes[_SOCIAL_PATH] = social_state
                outcome = {"closed": True, "success": True, "paid_cash": paid, "delivered_quantity": quantity}
            else:
                failed = contract_transition(contract, at=at_iso, to_status="failed", actor_ref=outlaw_fid or beneficiary or None)
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
                if outlaw_fid and quantity > 0:
                    ipath, outlaw_inventory = load_inventory(outlaw_fid)
                    _credit_cargo_to_inventory(outlaw_inventory, item_ref=item_ref, quantity=quantity)
                    writes[ipath] = outlaw_inventory
                    inventory_cache[outlaw_fid] = (ipath, outlaw_inventory)
                commitments_state = settle_and_resume_people(participants, activity_ref=cid, commitments_state=commitments_state)
                outcome = {"closed": True, "success": False, "refunded_cash": int(payment["refunded_cash"]), "cargo_lost": quantity}
            active_after.pop(cid, None)
            metric_key = "completed_count" if success else "failed_count"
            contract_after[metric_key] = max(0, int(contract_after.get(metric_key, 0))) + 1
            world_history = record_event(
                world_history, at=at_iso, kind=("contract_completed" if success else "contract_failed"),
                contract_ref=cid, beneficiary_ref=beneficiary or None, participant_refs=participants,
                outlaw_faction_ref=outlaw_fid, quantity=quantity,
            )
            writes[_WORLD_HISTORY_PATH] = world_history
            contract_index = contract_after
            active_contracts = active_after
            movements.pop(cid, None)
            for contact_ref, contact in list(contacts.items()):
                if isinstance(contact, Mapping) and contact.get("movement_ref") == cid:
                    contacts.pop(contact_ref, None)
            writes[_CONTRACT_INDEX_PATH] = contract_after
            writes[_COMMITMENTS_PATH] = commitments_state
            writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
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
            road_quality = str(route.get("road_quality") or "minor_road")
            traffic_milli = {"trunk_road":850,"major_road":700,"regional_road":500,"minor_road":280,"trail":100}.get(road_quality,350)
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
                "movements_advanced": 0, "hostile_contacts": 0, "completed_movements": 0,
            }
            route_movements = [
                (mid, movement) for mid, movement in sorted(movements.items())
                if isinstance(movement, Mapping) and movement.get("route_ref") == rid
            ]
            for movement_ref, raw_movement in route_movements:
                movement = copy.deepcopy(dict(raw_movement))
                status = str(movement.get("status", "active"))
                participants = [str(x) for x in movement.get("participant_refs", []) if isinstance(x, str)]
                beneficiary = str(movement.get("beneficiary_ref") or "")

                # A player contact waits on the authoritative exact-combat owner.
                if status == "contact_pending":
                    combat_ref = str(movement.get("combat_ref") or "")
                    combat = combats.get(combat_ref)
                    if isinstance(combat, Mapping) and combat.get("status") == "resolved":
                        winner = combat.get("winner_side")
                        outlaw_fid = str(movement.get("contact_outlaw_faction_ref") or "") or None
                        outcome = _close_escort(movement, success=(winner == "side_a"), outlaw_fid=outlaw_fid if winner != "side_a" else None)
                        combats.pop(combat_ref, None)
                        writes[_COMBATS_PATH] = combats_state
                        route_review["completed_movements"] += int(bool(outcome.get("closed")))
                    continue

                if status != "active":
                    continue
                people: dict[str, Mapping[str, Any]] = {}
                if beneficiary:
                    _rpath, escort_roster = load_roster(beneficiary)
                    by_ref = {str(p.get("person_id")): p for p in escort_roster.get("people", []) if isinstance(p, Mapping) and isinstance(p.get("person_id"), str)}
                    people.update({ref: by_ref[ref] for ref in participants if ref in by_ref})
                escorts = [people[ref] for ref in participants if ref in people]
                escort_index = max(1, sum(person_combat_index(p) for p in escorts) // max(1, len(escorts)))
                repelled = {str(x) for x in movement.get("repelled_outlaw_refs", []) if isinstance(x, str)}
                chosen: tuple[str, list[dict[str, Any]], dict[str, Any]] | None = None
                # Multiple outlaw factions on the same road do not each get an
                # independent certainty check. First establish one deterministic
                # contact opportunity for this caravan-day from real route
                # pressure, patrol suppression, visibility and night conditions.
                threat_milli = max(0, int(exposure.get("threat_milli", 0)))
                contact_permille = min(450, threat_milli * 3 // 10)
                contact_due = (
                    contact_permille > 0
                    and stable_permille(f"{world_seed}|route-contact|{movement_ref}|{at.date().isoformat()}") < contact_permille
                )
                for outlaw in ([] if not contact_due else sorted(outlaw_rows, key=lambda f: str(f.get("faction_id", "")))):
                    outlaw_fid = str(outlaw.get("faction_id") or "")
                    # A faction cannot raid its own caravan/escort movement.
                    # This matters for outlaw institutions that also operate a
                    # lawful merchant or escort enterprise: without this guard
                    # the route-pressure chooser can select the same faction as
                    # both beneficiary and attacker, producing a nonsensical
                    # self-robbery that creates danger without any grievance or
                    # inter-faction consequence.
                    if not outlaw_fid or outlaw_fid == beneficiary or outlaw_fid in repelled:
                        continue
                    outlaw_enterprises = outlaw.get("enterprises", {}) if isinstance(outlaw.get("enterprises"), Mapping) else {}
                    criminal_level = max(0, int(outlaw_enterprises.get("criminal_enterprise", 0)))
                    criminal_scale = enterprise_scale_value(outlaw, "criminal_enterprise") if criminal_level > 0 else 0
                    daily_raid_capacity = max(1, min(6, (criminal_scale + 2) // 3)) if criminal_scale > 0 else 1
                    if outlaw_attack_counts.get(outlaw_fid, 0) >= daily_raid_capacity:
                        continue
                    _orp, outlaw_roster = load_roster(outlaw_fid)
                    available = usable_martial_people(outlaw_roster, exclude_committed=unavailable_person_refs())
                    available.sort(key=lambda p: (-person_combat_index(p), str(p.get("person_id", ""))))
                    cell_force_cap = max(2, 4 + criminal_scale * 2) if criminal_scale > 0 else max(2, len(participants) + 1)
                    attack_count = min(len(available), max(2, len(participants) + 1), cell_force_cap)
                    attackers = available[:attack_count]
                    if not attackers:
                        continue
                    own_index = max(1, sum(person_combat_index(p) for p in attackers) // len(attackers))
                    _oipath, outlaw_inventory = load_inventory(outlaw_fid)
                    population = max(1, int(outlaw.get("population", 1)))
                    food_days = max(0, int(outlaw_inventory.get("food_ration_days", 0))) // population
                    policy = outlaw.get("outlaw_policy", {}) if isinstance(outlaw.get("outlaw_policy"), Mapping) else {}
                    autonomy = outlaw.get("autonomy_policy", {}) if isinstance(outlaw.get("autonomy_policy"), Mapping) else {}
                    decision = attack_decision(
                        own_available_martial=len(attackers), own_combat_index=own_index,
                        known_escort_count=max(1, len(participants)), known_escort_combat_index=escort_index,
                        cargo_value_cash=max(0, int(movement.get("cargo_value_cash", 0))),
                        food_reserve_days=food_days, treasury_cash=max(0, int(outlaw.get("treasury_cash", 0))),
                        minimum_attack_advantage_milli=max(500, int(policy.get("minimum_attack_advantage_milli", 1000))),
                        risk_tolerance=max(0, int(autonomy.get("risk_tolerance", 50))),
                    )
                    if decision.get("attack"):
                        chosen = (outlaw_fid, attackers, decision)
                        break

                if chosen is not None:
                    outlaw_fid, attackers, decision = chosen
                    outlaw_attack_counts[outlaw_fid] = outlaw_attack_counts.get(outlaw_fid, 0) + 1
                    attacker_refs = [str(p["person_id"]) for p in attackers]
                    people.update({str(p["person_id"]): p for p in attackers})
                    contact_ref = f"contact:{movement_ref}:{at.date().isoformat()}:{outlaw_fid}"
                    if beneficiary:
                        apply_directed_relation_event(beneficiary, outlaw_fid, "armed_raid")
                    route_review["hostile_contacts"] += 1
                    world_history = record_event(
                        world_history, at=at_iso, kind="route_raid", route_ref=str(rid), outlaw_faction_ref=outlaw_fid,
                        beneficiary_ref=beneficiary or None, attacker_refs=attacker_refs, witness_milli=int(exposure.get("witness_milli",0)),
                    )
                    writes[_WORLD_HISTORY_PATH] = world_history
                    if isinstance(source_region, str) and int(exposure.get("witness_milli",0)) >= 350:
                        attention_rows = government_state.setdefault("attention", {})
                        warrants = government_state.setdefault("warrants", {})
                        if not isinstance(attention_rows, dict) or not isinstance(warrants, dict):
                            raise ValueError("jianghu government state invalid")
                        confidence = max(35, min(100, int(exposure.get("witness_milli",0))//10))
                        for attacker_ref in attacker_refs:
                            prior = attention_rows.get(attacker_ref, {}) if isinstance(attention_rows.get(attacker_ref), Mapping) else {}
                            prior_offenses = max(0, int(prior.get("prior_offenses",0)))
                            added = attention_from_evidence([{
                                "kind":"robbery", "publicly_delivered":True, "confidence":confidence,
                            }], prior_offenses=prior_offenses)
                            total = min(300, max(0,int(prior.get("attention",0))) + added)
                            attention_rows[attacker_ref] = {
                                "attention": total, "bounty_cash": max(0,int(prior.get("bounty_cash",0))),
                                "prior_offenses": prior_offenses + 1, "last_updated_at": at_iso, "last_evidence_ref": contact_ref,
                            }
                            if total >= 40:
                                warrant_ref=f"warrant:{attacker_ref}"
                                existing=warrants.get(warrant_ref,{}) if isinstance(warrants.get(warrant_ref),Mapping) else {}
                                warrants[warrant_ref]={
                                    "subject_ref":attacker_ref,"offense":"robbery","bounty_cash":max(500,total*25),
                                    "status":str(existing.get("status") or "active") if str(existing.get("status") or "active") in {"active","pursuing"} else "active",
                                    "evidence_ref":contact_ref,"issued_at":str(existing.get("issued_at") or at_iso),
                                    "jurisdiction_ref":source_region,
                                }
                        writes[_GOVERNMENT_PATH]=government_state
                    doctrines: dict[str, Mapping[str, Any]] = {}
                    if beneficiary:
                        _fp, bf = load_faction(beneficiary); doctrines[beneficiary] = bf.get("doctrine", {}) if isinstance(bf.get("doctrine"), Mapping) else {}
                    _ofp, ofaction = load_faction(outlaw_fid); doctrines[outlaw_fid] = ofaction.get("doctrine", {}) if isinstance(ofaction.get("doctrine"), Mapping) else {}
                    if player_ref and player_ref in participants:
                        # A player-facing combat can remain unresolved across
                        # turns, so the outlaw attackers need the same finite
                        # availability reservation as the convoy. Otherwise the
                        # scheduler could use the same bodies in another route
                        # action while this combat owner is still live.
                        commitments_state = reserve_resources(
                            commitments_state,
                            resources=[("person", ref, outlaw_fid) for ref in attacker_refs],
                            actor_ref=attacker_refs[0], owner_ref=outlaw_fid,
                            activity_ref=contact_ref, activity_kind="route_attack",
                            started_at=at_iso, location_ref=str(rid),
                        )
                        pause_people_for_commitment(outlaw_fid, attacker_refs)
                        writes[_COMMITMENTS_PATH] = commitments_state
                        combat_ref = f"combat:{contact_ref}"
                        combat = initialize_combat(
                            combat_ref=combat_ref, side_a_refs=participants, side_b_refs=attacker_refs,
                            people=people, zone_ref=str(rid), started_at=at_iso,
                            objective={"kind": "protect_cargo", "movement_ref": movement_ref},
                            awareness_mode="mutual", initial_range_band=2, equipment_ledger=equipment_ledger,
                        )
                        combats[combat_ref] = combat
                        contacts[contact_ref] = {
                            "contact_ref": contact_ref, "movement_ref": movement_ref, "route_ref": rid,
                            "outlaw_faction_ref": outlaw_fid, "escort_refs": participants,
                            "attacker_refs": attacker_refs, "combat_ref": combat_ref, "status": "active",
                        }
                        movement["status"] = "contact_pending"; movement["contact_ref"] = contact_ref
                        movement["combat_ref"] = combat_ref; movement["contact_outlaw_faction_ref"] = outlaw_fid
                        movement["contact_attacker_refs"] = attacker_refs
                        movements[movement_ref] = movement
                        writes[_COMBATS_PATH] = combats_state
                        writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                        try:
                            scene = copy.deepcopy(dict(read_json(_SCENE_PATH)))
                            scene["active_combat_ref"] = combat_ref
                            scene["present_person_ids"] = participants + attacker_refs
                            scene["visible_person_ids"] = participants + attacker_refs
                            writes[_SCENE_PATH] = scene
                        except FileNotFoundError:
                            pass
                        row = {
                            "kind": "hostile_contact", "event_id": contact_ref, "route_ref": rid,
                            "combat_ref": combat_ref, "movement_ref": movement_ref,
                            "outlaw_faction_ref": outlaw_fid, "requires_player_decision": True,
                            "delivered_to_player": True,
                        }
                        handoff = classify_handoff(row); handoffs.append({**row, "handoff": handoff})
                        route_review["player_contact"] = contact_ref
                        continue
                    result = simulate_exact_combat(
                        combat_ref=f"combat:{contact_ref}", side_a_refs=participants, side_b_refs=attacker_refs,
                        people=people, equipment_ledger=equipment_ledger, doctrines=doctrines,
                        zone_ref=str(rid), started_at=at_iso,
                        objective={"kind": "protect_cargo", "movement_ref": movement_ref},
                        # Background route predation is an interception window,
                        # not an off-screen deathmatch. Four exact exchanges
                        # establish physical contact, injuries and reaction
                        # saturation. If no decisive result exists after that
                        # bounded window, the interception failed and the
                        # attackers disengage. Player-present contacts remain
                        # normal full exact-combat owners above.
                        targeting_intent="disable", max_exchanges=4,
                    )
                    equipment_ledger = copy.deepcopy(dict(result["equipment_ledger_after"]))
                    people_after = result["people_after"]
                    _update_roster_people(beneficiary, people_after)
                    _update_roster_people(outlaw_fid, people_after)
                    writes[_EQUIPMENT_LEDGER_PATH] = equipment_ledger
                    newly_dead = {
                        str(ref) for ref, person in people_after.items()
                        if isinstance(ref, str) and isinstance(person, Mapping)
                        and isinstance(person.get("health"), Mapping) and person.get("health", {}).get("status") == "dead"
                    }
                    if newly_dead:
                        surviving_participants = [ref for ref in participants if ref not in newly_dead]
                        movement["participant_refs"] = surviving_participants
                        movement["known_escort_count"] = len(surviving_participants)
                        close_dead_current_authorities(sorted(newly_dead))
                        participants = surviving_participants
                    if (result.get("resolved") and result.get("winner_side") == "side_b") or not participants:
                        outcome = _close_escort(movement, success=False, outlaw_fid=outlaw_fid)
                        route_review["completed_movements"] += int(bool(outcome.get("closed")))
                        continue
                    repelled.add(outlaw_fid)
                    movement["repelled_outlaw_refs"] = sorted(repelled)

                # Surviving convoy advances by the route scheduler's bounded
                # daily quantum. Strategic/player travel uses exact duration;
                # background escort traffic intentionally resolves at daily
                # causal wakes to avoid hourly world-microticks.
                movement["elapsed_hours"] = min(
                    max(1, int(movement.get("required_hours", 1))),
                    max(0, int(movement.get("elapsed_hours", 0))) + 24,
                )
                route_review["movements_advanced"] += 1
                if int(movement["elapsed_hours"]) >= max(1, int(movement.get("required_hours", 1))):
                    outcome = _close_escort(movement, success=True)
                    route_review["completed_movements"] += int(bool(outcome.get("closed")))
                else:
                    movements[movement_ref] = movement
                    writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
            reviews.append(route_review)
        if route_events:
            if _chunk_contains_final_owner(schedule, sorted_events, class_id="route_daily"):
                route_ops_state.pop("daily_outlaw_attack_budget", None)
            else:
                route_ops_state["daily_outlaw_attack_budget"] = attack_tracker
            writes[_ROUTE_OPERATIONS_PATH] = route_ops_state

    # Exact agriculture harvest obligations. Planting already paid seed and
    # aggregate local labor. Harvest creates only the physical yield calculated
    # from the snapshotted quality/input conditions plus deterministic climate.
    for event in sorted_events:
        if event.get("kind") != "agriculture_harvest_due":
            continue
        fid = str(event.get("faction_ref") or "")
        crop_ref = str(event.get("crop_ref") or "")
        if not fid or not crop_ref:
            reviews.append({"kind": "agriculture_harvest_due", "event_id": event.get("event_id"), "result": "invalid_obligation"})
            continue
        try:
            planted_at = datetime.fromisoformat(str(event.get("planted_at")))
            quote = harvest_quote(
                world_seed=world_seed, place_id=str(event.get("place_id") or ""), crop_ref=crop_ref,
                planted_mu=max(1, int(event.get("planted_mu", 0))), planted_at=planted_at,
                agriculture_level=max(0, int(event.get("agriculture_level", 0))),
                labor_coverage_milli=max(0, int(event.get("labor_coverage_milli", 1000))),
            )
        except (KeyError, TypeError, ValueError):
            reviews.append({"kind": "agriculture_harvest_due", "event_id": event.get("event_id"), "faction_ref": fid, "result": "invalid_crop_obligation"})
            continue
        ipath, inventory = load_inventory(fid)
        output = max(0, int(quote.get("output_units", 0)))
        crop = crop_record(crop_ref)
        if "yield_food_per_mu" in crop:
            inventory["food_ration_days"] = max(0, int(inventory.get("food_ration_days", 0))) + output
            output_kind = "food_ration_day"
        else:
            herbs = inventory.setdefault("herbs", {})
            if not isinstance(herbs, dict):
                raise ValueError("jianghu inventory herbs invalid")
            herbs[crop_ref] = max(0, int(herbs.get(crop_ref, 0))) + output
            output_kind = "herb_unit"
        inventory_cache[fid] = (ipath, inventory)
        writes[ipath] = inventory
        reviews.append({
            "kind": "agriculture_harvest_due", "event_id": event.get("event_id"), "faction_ref": fid,
            "crop_ref": crop_ref, "planted_mu": int(event.get("planted_mu", 0)),
            "output_kind": output_kind, "output_units": output, "result": "harvested",
        })

    # Exact one-off family obligations. Pregnancies are compact current facts;
    # the scheduler carries only the unresolved due birth and removes it on
    # settlement, so there is no life-event execution history.
    for event in sorted_events:
        if event.get("kind") != "family_birth_due":
            continue
        fid = event.get("owner_ref"); marriage_ref = event.get("marriage_ref"); child_ref = event.get("child_ref")
        if not isinstance(fid, str) or not isinstance(marriage_ref, str) or not isinstance(child_ref, str):
            continue
        fpath, faction = load_faction(fid)
        rpath, roster = load_roster(fid)
        resolved = resolve_birth(
            family_state, marriage_ref=marriage_ref, child_ref=child_ref, faction_ref=fid,
            roster_people=[p for p in roster.get("people", []) if isinstance(p, Mapping)], birth_at=at_iso,
            existing_world_names=all_existing_names(),
        )
        family_state = resolved["family_after"]
        writes[_FAMILY_PATH] = family_state
        child = resolved.get("birth")
        if isinstance(child, Mapping):
            child_name = child.get("name")
            if isinstance(child_name, str) and child_name:
                all_existing_names().add(child_name)
            roster["people"] = resolved["people_after"]
            faction["population"] = max(0, int(faction.get("population", 0))) + 1
            writes[fpath] = faction; faction_cache[fid] = (fpath, faction)
            writes[rpath] = compact_roster_state(roster, faction=faction)
            roster_cache[fid] = (rpath, roster)
            ordinal = len(resolved["people_after"]) - 1
            register_new_person_route(fid, child_ref, ordinal)
            world_history = record_event(
                world_history, at=at_iso, kind="birth", faction_ref=fid, child_ref=child_ref, marriage_ref=marriage_ref,
            )
            writes[_WORLD_HISTORY_PATH] = world_history
            in_player_household = any(
                isinstance(row, Mapping) and player_ref and player_ref in row.get("member_refs", []) and child_ref in row.get("member_refs", [])
                for row in family_state.get("households", {}).values()
            ) if isinstance(family_state.get("households"), Mapping) else False
            notice = {"kind": "family_checkin", "event_id": event.get("event_id"), "child_ref": child_ref, "faction_ref": fid, "delivered_to_player": bool(in_player_household)}
            handoff = classify_handoff(notice); reviews.append({"kind": "family_birth_due", "event_id": event.get("event_id"), "child_ref": child_ref, "result": "birth", "handoff": handoff})
            if handoff["class"] != "internal": handoffs.append({**notice, "handoff": handoff})
        else:
            reviews.append({"kind": "family_birth_due", "event_id": event.get("event_id"), "child_ref": child_ref, "result": "pregnancy_ended_without_birth"})

    # Public martial championships have one fully mechanical production format:
    # individual exact combat. Registrations are sponsored by real faction
    # treasuries, entry fees build the prize escrow, injuries persist, and the
    # live tournament owner is deleted after payout instead of becoming history.
    tournament_events = [
        e for e in sorted_events
        if e.get("kind") in {
            "tournament_advance_notice",
            "tournament_registration_open", "tournament_registration_close",
            "tournament_convergence_day",
            "regional_martial_tournament", "great_jianghu_tournament",
            "tournament_competition_continue",
        }
    ]
    if tournament_events:
        tournaments = tournament_state.setdefault("tournaments", {})
        if not isinstance(tournaments, dict):
            raise ValueError("jianghu tournament registry invalid")

        def _tref(kind: str, competition_date: str) -> str:
            return f"tournament:{kind}:{competition_date}:individual"

        def _registration_owner_map(tournament: Mapping[str, Any]) -> dict[str, str]:
            result: dict[str, str] = {}
            rows = tournament.get("registrations", []) if isinstance(tournament, Mapping) else []
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, Mapping):
                        continue
                    ref = row.get("entrant_ref"); fid = row.get("faction_ref")
                    if isinstance(ref, str) and isinstance(fid, str):
                        result[ref] = fid
            return result

        def _write_tournament_people(owner_map: Mapping[str, str], people_after: Mapping[str, Mapping[str, Any]]) -> None:
            by_faction: dict[str, dict[str, Mapping[str, Any]]] = {}
            for ref, person in people_after.items():
                fid = owner_map.get(str(ref))
                if isinstance(fid, str) and isinstance(person, Mapping):
                    by_faction.setdefault(fid, {})[str(ref)] = person
            for fid, replacements in by_faction.items():
                rpath, roster = load_roster(fid)
                rows = roster.get("people", []) if isinstance(roster, Mapping) else []
                if not isinstance(rows, list):
                    continue
                changed = False; after_rows: list[Any] = []
                for raw in rows:
                    ref = raw.get("person_id") if isinstance(raw, Mapping) else None
                    if isinstance(ref, str) and ref in replacements:
                        after_rows.append(copy.deepcopy(dict(replacements[ref]))); changed = True
                    else:
                        after_rows.append(raw)
                if changed:
                    roster["people"] = after_rows
                    writes[rpath] = roster
                    roster_cache[fid] = (rpath, roster)

        for event in tournament_events:
            kind = str(event.get("kind", ""))
            if kind == "tournament_advance_notice":
                tournament_kind = str(event.get("tournament_kind") or "")
                competition_date = str(event.get("competition_date") or "")
                host = str(event.get("host_place_id") or "")
                if tournament_kind == "great_jianghu_tournament" and competition_date and host:
                    notice = {
                        "kind": "tournament_registration",
                        "phase": "advance_notice",
                        "tournament_kind": tournament_kind,
                        "competition_date": competition_date,
                        "registration_opens_on": str(event.get("registration_opens_on") or ""),
                        "registration_closes_on": str(event.get("registration_closes_on") or ""),
                        "host_place_id": host,
                        "delivered_to_player": True,
                        "requires_player_decision": False,
                    }
                    world_history = record_event(
                        world_history, at=at_iso, kind="great_tournament_advance_notice",
                        tournament_kind=tournament_kind, competition_date=competition_date,
                        host_place_ref=host,
                        registration_opens_on=str(event.get("registration_opens_on") or ""),
                        registration_closes_on=str(event.get("registration_closes_on") or ""),
                    )
                    writes[_WORLD_HISTORY_PATH] = world_history
                    handoff = classify_handoff(notice)
                    reviews.append({
                        "kind": "tournament_advance_notice", "event_id": event.get("event_id"),
                        "tournament_kind": tournament_kind, "competition_date": competition_date,
                        "host_place_id": host, "handoff": handoff,
                    })
                    if handoff["class"] != "internal":
                        handoffs.append({**notice, "handoff": handoff})
                continue
            if kind == "tournament_registration_open":
                tournament_kind = str(event.get("tournament_kind") or "")
                competition_date = str(event.get("competition_date") or "")
                if tournament_kind not in {"regional_martial_tournament", "great_jianghu_tournament"} or not competition_date:
                    continue
                tref = _tref(tournament_kind, competition_date)
                if tref in tournaments:
                    continue
                host = str(event.get("host_place_id") or "")
                host_region = place_region.get(host)
                if not host or not isinstance(host_region, str):
                    reviews.append({"kind": "tournament_registration_open", "event_id": event.get("event_id"), "result": "host_unresolved"})
                    continue
                try:
                    profile = tournament_event_profile(tournament_kind)
                    mpath, organizer_market = load_market(host_region)
                    is_great = tournament_kind == "great_jianghu_tournament"
                    opened = open_tournament(
                        event_id=tref, format_ref="individual",
                        organizer_ref=_tournament_organizer_ref(host, great=is_great),
                        great=is_great,
                    )
                except (FileNotFoundError, KeyError, ValueError):
                    reviews.append({"kind": "tournament_registration_open", "event_id": event.get("event_id"), "result": "host_services_unavailable"})
                    continue
                tournament = dict(opened)
                tournament.update({
                    "tournament_ref": tref, "tournament_kind": tournament_kind,
                    "competition_date": competition_date,
                    "registration_closes_on": str(event.get("registration_closes_on") or ""),
                    "host_place_ref": host, "host_region": host_region,
                    "venue_site_ref": _tournament_venue_site(local_sites, host) or host,
                })
                fee = max(0, int(tournament.get("entry_fee_cash", 0)))
                prestige = int(profile.get("prestige_weight", 50))
                allows_outlaws = bool(profile.get("allows_outlaw_factions", False))
                entrants = 0

                # Local host-city factions may sponsor any number of eligible
                # entrants.  There is no field cap or local slot allocation;
                # marginal nominations simply become less attractive while
                # treasury reserve and medical/physical eligibility remain hard.
                for fid in scheduled_faction_ids:
                    fpath, faction = load_faction(fid)
                    if faction.get("headquarters") != host:
                        continue
                    if faction_type(fid) == "outlaw_faction" and not allows_outlaws:
                        continue
                    rpath, roster = load_roster(fid); ipath, inventory = load_inventory(fid)
                    people = usable_martial_people(roster, exclude_committed=unavailable_person_refs())
                    people = [
                        p for p in people
                        if at.year - int(p.get("birth_year", at.year)) >= 16
                        and not bool(p.get("retired_from_field", False))
                        and person_place(p, home_place=str(faction.get("headquarters") or ""), home_site_ref=str(faction.get("local_site_ref") or "")) == host
                        and str(p.get("person_id")) != player_ref
                    ]
                    people.sort(key=lambda p: (-person_combat_index(p), str(p.get("person_id", ""))))
                    if not people:
                        continue
                    policy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
                    transport = inventory.get("transport_assets", {}) if isinstance(inventory.get("transport_assets"), Mapping) else {}
                    quote = monthly_upkeep_quote(
                        faction,
                        riding_horses=max(0, int(transport.get("riding_horses", 0))),
                        pack_animals=max(0, int(transport.get("pack_animals", 0))),
                    )
                    reserve_months = max(2, int(policy.get("reserve_cash_months", 6)))
                    reserve_floor = max(0, int(quote.get("total_cash", 0))) * reserve_months
                    local_registered = 0
                    best_combat = person_combat_index(people[0]) if people else 0.0
                    for candidate_index, candidate in enumerate(people):
                        ref = str(candidate.get("person_id") or "")
                        if not ref:
                            continue
                        if not tournament_entrant_interested(
                            faction_ref=fid, person_ref=ref, tournament_ref=tref,
                            tournament_kind=tournament_kind, entrant_order=candidate_index,
                            training_priority=int(policy.get("training_priority", 50)),
                            risk_tolerance=int(policy.get("risk_tolerance", 50)),
                            prestige_weight=prestige,
                            faction_type=faction_type(fid),
                            living_members=max(0, int(faction.get("population", 0))),
                            major_sect_population_threshold=int(profile.get("major_sect_population_threshold", 100)),
                            major_sect_competitor_floor=int(profile.get("major_sect_competitor_floor", 0)),
                            major_institution_population_threshold=int(profile.get("major_institution_population_threshold", 100)),
                            major_institution_competitor_floor=int(profile.get("major_institution_competitor_floor", 0)),
                            ordinary_competitor_floor=int(profile.get("ordinary_competitor_floor", 0)),
                            candidate_combat_index=person_combat_index(candidate),
                            best_combat_index=best_combat,
                            additional_competitor_interest_permille=int(profile.get("additional_competitor_interest_permille", 0)),
                            additional_competitor_decay_permille=int(profile.get("additional_competitor_decay_permille", 0)),
                            additional_competitor_relative_strength_permille=int(profile.get("additional_competitor_relative_strength_permille", 0)),
                        ):
                            continue
                        treasury = max(0, int(faction.get("treasury_cash", 0)))
                        if treasury - fee < reserve_floor:
                            break
                        audience = reputation_state.get("audiences", {}).get(ref, {}) if isinstance(reputation_state.get("audiences"), Mapping) else {}
                        qualifying = max(0, int(audience.get("public_score", 0))) if isinstance(audience, Mapping) else 0
                        try:
                            reg = tournament_register(
                                tournament, entrant_ref=ref, qualifying_score=qualifying,
                                payer_cash=treasury, alive=True, medically_eligible=True,
                            )
                        except ValueError:
                            continue
                        tournament = dict(reg["tournament_after"])
                        tournament["registrations"][-1]["faction_ref"] = fid
                        tournaments[tref] = tournament
                        entrant_is_leader, entrant_is_senior = _tournament_delegate_roles(candidate)
                        _add_tournament_delegation_presence(
                            tref, fid, entrant_refs=[ref],
                            leader_refs=[ref] if entrant_is_leader else (),
                            senior_refs=[ref] if entrant_is_senior else (),
                        )
                        tournament = dict(tournaments[tref])
                        faction["treasury_cash"] = int(reg["payer_cash_after"])
                        entrants += 1; local_registered += 1
                    if local_registered:
                        writes[fpath] = faction; faction_cache[fid] = (fpath, faction)

                # Distant factions may send multiple sponsored entrants.  No
                # reserved travel slots exist.  Interest creates candidates;
                # every actual trip still proves route time, provisions, tolls,
                # health, commitment availability and faction operating reserve.
                travel_planned = 0
                planned_competitors_by_faction: dict[str, set[str]] = {}
                for registration in tournament.get("registrations", []):
                    if isinstance(registration, Mapping) and isinstance(registration.get("faction_ref"), str) and isinstance(registration.get("entrant_ref"), str):
                        planned_competitors_by_faction.setdefault(str(registration["faction_ref"]), set()).add(str(registration["entrant_ref"]))
                travel_candidates: list[tuple[float, str, str, int]] = []
                for fid in scheduled_faction_ids:
                    _fpath, faction = load_faction(fid)
                    if faction.get("headquarters") == host:
                        continue
                    if faction_type(fid) == "outlaw_faction" and not allows_outlaws:
                        continue
                    policy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
                    source_place = str(faction.get("headquarters") or "")
                    try:
                        route_hint = shortest_route(start=source_place, end=host, mode="foot") if source_place and source_place != host else {"baseline_hours": 0}
                        travel_days_hint = max(0, (int(float(route_hint.get("baseline_hours", 0)) * 1000) + 23999) // 24000)
                    except (KeyError, ValueError):
                        travel_days_hint = 999
                    if not tournament_travel_interested(
                        faction_ref=fid, tournament_ref=tref, tournament_kind=tournament_kind,
                        training_priority=int(policy.get("training_priority", 50)),
                        risk_tolerance=int(policy.get("risk_tolerance", 50)),
                        entry_fee_cash=fee,
                        current_prize_cash=int(tournament.get("prize_escrow_cash", 0)),
                        prestige_weight=prestige,
                        faction_type=faction_type(fid),
                        living_members=max(0, int(faction.get("population", 0))),
                        faction_interest_floor_permille=int(profile.get("faction_interest_floor_permille", 0)),
                        major_sect_population_threshold=int(profile.get("major_sect_population_threshold", 100)),
                        travel_days_hint=travel_days_hint,
                    ):
                        continue
                    _rpath, roster = load_roster(fid)
                    people = [
                        p for p in usable_martial_people(roster, exclude_committed=unavailable_person_refs())
                        if person_place(p, home_place=str(faction.get("headquarters") or ""), home_site_ref=str(faction.get("local_site_ref") or "")) == str(faction.get("headquarters") or "")
                        and at.year - int(p.get("birth_year", at.year)) >= 16
                        and not bool(p.get("retired_from_field", False))
                        and str(p.get("person_id")) != player_ref
                    ]
                    people.sort(key=lambda p: (-person_combat_index(p), str(p.get("person_id", ""))))
                    best_combat = person_combat_index(people[0]) if people else 0.0
                    for candidate_index, person in enumerate(people):
                        person_ref = str(person.get("person_id") or "")
                        if not person_ref:
                            continue
                        if tournament_entrant_interested(
                            faction_ref=fid, person_ref=person_ref, tournament_ref=tref,
                            tournament_kind=tournament_kind, entrant_order=candidate_index,
                            training_priority=int(policy.get("training_priority", 50)),
                            risk_tolerance=int(policy.get("risk_tolerance", 50)),
                            prestige_weight=prestige,
                            faction_type=faction_type(fid),
                            living_members=max(0, int(faction.get("population", 0))),
                            major_sect_population_threshold=int(profile.get("major_sect_population_threshold", 100)),
                            major_sect_competitor_floor=int(profile.get("major_sect_competitor_floor", 0)),
                            major_institution_population_threshold=int(profile.get("major_institution_population_threshold", 100)),
                            major_institution_competitor_floor=int(profile.get("major_institution_competitor_floor", 0)),
                            ordinary_competitor_floor=int(profile.get("ordinary_competitor_floor", 0)),
                            candidate_combat_index=person_combat_index(person),
                            best_combat_index=best_combat,
                            additional_competitor_interest_permille=int(profile.get("additional_competitor_interest_permille", 0)),
                            additional_competitor_decay_permille=int(profile.get("additional_competitor_decay_permille", 0)),
                            additional_competitor_relative_strength_permille=int(profile.get("additional_competitor_relative_strength_permille", 0)),
                        ):
                            travel_candidates.append((-person_combat_index(person), fid, person_ref, candidate_index))
                travel_candidates.sort(key=lambda row: (row[0], row[1], row[2], row[3]))
                # Attendance spending is derived from the size of the uncapped
                # planned field rather than a fixed fictional stay.  A larger
                # bracket really occupies more competition days and therefore
                # costs every visiting delegation more lodging/event cash.
                estimated_field_size = entrants + len(travel_candidates)
                budgeted_host_days = tournament_estimated_host_days(tournament_kind, estimated_field_size)
                for _neg_score, fid, person_ref, _candidate_index in travel_candidates:
                    outcome = plan_tournament_trip(
                        fid, person_ref=person_ref, tournament_ref=tref,
                        host_place=host, registration_closes_on=str(event.get("registration_closes_on") or ""),
                        competition_date=competition_date, entry_fee_cash=fee,
                        arrival_lead_hours_min=int(profile.get("arrival_lead_hours_min", 12)),
                        arrival_lead_hours_max=int(profile.get("arrival_lead_hours_max", 36)),
                        host_cash_per_person_day=int(profile.get("attendee_host_cash_per_person_day", 0)),
                        minimum_host_days=budgeted_host_days,
                    )
                    if outcome.get("result") == "departure_planned":
                        travel_planned += 1
                        planned_competitors_by_faction.setdefault(fid, set()).add(person_ref)

                # Tournament attendance includes real faction delegations, not
                # only bracket entrants.  The Great event makes every faction
                # evaluate a delegation; regional events use ordinary interest.
                delegation_attempts = 0
                delegation_planned = 0
                spectator_people_nominated = 0
                delegation_failures: dict[str, int] = {}
                office_priority = {
                    "leader": 1000, "deputy_leader": 900, "chief_instructor": 850,
                    "chief_physician": 800, "chief_steward": 760, "treasurer": 720,
                    "quartermaster": 680,
                }
                grade_priority = {"elder": 600, "elite": 520, "senior": 440, "full": 300, "junior": 180, "probationary": 80}
                for fid in scheduled_faction_ids:
                    fpath, faction = load_faction(fid)
                    ftype = faction_type(fid)
                    if ftype == "outlaw_faction" and not allows_outlaws:
                        continue
                    policy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
                    if tournament_kind != "great_jianghu_tournament" and faction.get("headquarters") != host:
                        source_place = str(faction.get("headquarters") or "")
                        try:
                            route_hint = shortest_route(start=source_place, end=host, mode="foot") if source_place and source_place != host else {"baseline_hours": 0}
                            travel_days_hint = max(0, (int(float(route_hint.get("baseline_hours", 0)) * 1000) + 23999) // 24000)
                        except (KeyError, ValueError):
                            travel_days_hint = 999
                        if not tournament_travel_interested(
                            faction_ref=fid, tournament_ref=tref, tournament_kind=tournament_kind,
                            training_priority=int(policy.get("training_priority", 50)),
                            risk_tolerance=int(policy.get("risk_tolerance", 50)), entry_fee_cash=fee,
                            current_prize_cash=int(tournament.get("prize_escrow_cash", 0)), prestige_weight=prestige,
                            faction_type=ftype, living_members=max(0, int(faction.get("population", 0))),
                            faction_interest_floor_permille=int(profile.get("faction_interest_floor_permille", 0)),
                            major_sect_population_threshold=int(profile.get("major_sect_population_threshold", 100)),
                            travel_days_hint=travel_days_hint,
                        ):
                            continue
                    delegation_attempts += 1
                    rpath, roster = load_roster(fid)
                    competitors = planned_competitors_by_faction.get(fid, set())
                    home_place = str(faction.get("headquarters") or "")
                    home_site = str(faction.get("local_site_ref") or "")
                    candidates = [
                        p for p in usable_martial_people(roster, exclude_committed=unavailable_person_refs())
                        if person_place(p, home_place=home_place, home_site_ref=home_site) == home_place
                        and at.year - int(p.get("birth_year", at.year)) >= 14
                        and str(p.get("person_id") or "") != player_ref
                        and str(p.get("person_id") or "") not in competitors
                    ]
                    def _spectator_priority(person: Mapping[str, Any]) -> tuple[int, int, int, str]:
                        offices = person.get("standing_offices", []) if isinstance(person.get("standing_offices"), list) else []
                        office = max((office_priority.get(str(ref), 0) for ref in offices), default=0)
                        grade = grade_priority.get(str(person.get("membership_grade") or ""), 0)
                        professional = person.get("professional_skills", {}) if isinstance(person.get("professional_skills"), Mapping) else {}
                        useful = max(int(professional.get("medicine", 0)), int(professional.get("administration", 0)), int(professional.get("commerce", 0)), int(professional.get("instruction", 0)))
                        return (-office, -grade, -useful, str(person.get("person_id", "")))
                    candidates.sort(key=_spectator_priority)
                    selected: list[str] = []
                    for spectator_order, person in enumerate(candidates):
                        ref = str(person.get("person_id") or "")
                        offices = person.get("standing_offices", []) if isinstance(person.get("standing_offices"), list) else []
                        if not ref or not tournament_spectator_interested(
                            faction_ref=fid, person_ref=ref, tournament_ref=tref, tournament_kind=tournament_kind,
                            spectator_order=spectator_order, is_leader="leader" in offices, faction_type=ftype,
                            living_members=max(0, int(faction.get("population", 0))),
                            spectator_delegation_floor=int(profile.get("spectator_delegation_floor", 0)),
                            major_spectator_population_threshold=int(profile.get("major_spectator_population_threshold", 100)),
                            major_spectator_delegation_floor=int(profile.get("major_spectator_delegation_floor", 0)),
                            major_sect_spectator_delegation_floor=int(profile.get("major_sect_spectator_delegation_floor", 0)),
                            leader_attendance_permille=int(profile.get("leader_attendance_permille", 0)),
                            spectator_marginal_interest_permille=int(profile.get("spectator_marginal_interest_permille", 0)),
                            spectator_marginal_decay_permille=int(profile.get("spectator_marginal_decay_permille", 0)),
                        ):
                            continue
                        selected.append(ref)
                    if not selected:
                        delegation_failures["no_available_delegates"] = delegation_failures.get("no_available_delegates", 0) + 1
                        continue
                    outcome = plan_tournament_delegation_trip(
                        fid, candidate_refs=selected, tournament_ref=tref, host_place=host,
                        competition_date=competition_date, convergence_days_before=int(profile.get("convergence_days_before", 0)),
                        host_cash_per_person_day=int(profile.get("attendee_host_cash_per_person_day", 0)),
                        delegate_ticket_cash_per_day=int(profile.get("faction_delegate_ticket_cash_per_day", 0)),
                        minimum_host_days=budgeted_host_days,
                    )
                    if outcome.get("result") == "delegation_departure_planned":
                        delegation_planned += 1
                        spectator_people_nominated += len(selected)
                    else:
                        reason = str(outcome.get("result") or "planning_failed")
                        delegation_failures[reason] = delegation_failures.get(reason, 0) + 1

                tournaments[tref] = tournament
                writes[_TOURNAMENTS_PATH] = tournament_state
                writes[mpath] = organizer_market; market_cache[host_region] = (mpath, organizer_market)
                notice = {
                    "kind": "tournament_registration", "tournament_ref": tref,
                    "tournament_kind": tournament_kind, "host_place_id": host,
                    "organizer_ref": tournament.get("organizer_ref"),
                    "competition_date": competition_date, "entry_fee_cash": fee,
                    "prize_cash": int(tournament.get("prize_escrow_cash", 0)),
                    "local_paid_registrations": entrants, "traveling_entrants_planned": travel_planned,
                    "faction_attendance_attempts": delegation_attempts,
                    "spectator_delegations_planned": delegation_planned,
                    "spectator_people_nominated": spectator_people_nominated,
                    "delegation_failure_counts": dict(sorted(delegation_failures.items())),
                    "estimated_uncapped_field_size": estimated_field_size,
                    "budgeted_host_days": budgeted_host_days,
                    "field_size_cap": None,
                    "delivered_to_player": True, "requires_player_decision": False,
                }
                world_history = record_event(
                    world_history, at=at_iso, kind="tournament_registration_opened",
                    tournament_ref=tref, tournament_kind=tournament_kind,
                    host_place_ref=host, organizer_ref=str(tournament.get("organizer_ref") or ""),
                    local_paid_registrations=entrants,
                    traveling_entrants_planned=travel_planned,
                    faction_attendance_attempts=delegation_attempts,
                    spectator_delegations_planned=delegation_planned,
                    spectator_people_nominated=spectator_people_nominated,
                    delegation_failure_counts=dict(sorted(delegation_failures.items())),
                    estimated_uncapped_field_size=estimated_field_size,
                    budgeted_host_days=budgeted_host_days,
                    prize_cash=max(0, int(tournament.get("prize_escrow_cash", 0))),
                )
                writes[_WORLD_HISTORY_PATH] = world_history
                handoff = classify_handoff(notice); reviews.append({
                    "kind": "tournament_registration_open", "event_id": event.get("event_id"),
                    "tournament_ref": tref, "local_paid_registrations": entrants,
                    "traveling_entrants_planned": travel_planned,
                    "faction_attendance_attempts": delegation_attempts,
                    "spectator_delegations_planned": delegation_planned,
                    "spectator_people_nominated": spectator_people_nominated,
                    "delegation_failure_counts": dict(sorted(delegation_failures.items())),
                    "prize_cash": int(tournament.get("prize_escrow_cash", 0)), "handoff": handoff,
                }); handoffs.append({**notice, "handoff": handoff})
                continue

            if kind == "tournament_registration_close":
                tournament_kind = str(event.get("tournament_kind") or ""); competition_date = str(event.get("competition_date") or "")
                tref = _tref(tournament_kind, competition_date)
                tournament = tournaments.get(tref)
                if isinstance(tournament, Mapping) and tournament.get("status") == "registration_open":
                    tournaments[tref] = close_registration(tournament)
                    writes[_TOURNAMENTS_PATH] = tournament_state
                    registrations = tournaments[tref].get("registrations", []) if isinstance(tournaments[tref], Mapping) else []
                    entrant_factions = {
                        str(row.get("faction_ref")) for row in registrations
                        if isinstance(row, Mapping) and isinstance(row.get("faction_ref"), str)
                    } if isinstance(registrations, list) else set()
                    world_history = record_event(
                        world_history, at=at_iso, kind="tournament_registration_closed",
                        tournament_ref=tref, tournament_kind=tournament_kind,
                        entrant_count=len(registrations) if isinstance(registrations, list) else 0,
                        entrant_faction_count=len(entrant_factions),
                        prize_cash=max(0, int(tournaments[tref].get("prize_escrow_cash", 0))),
                        entry_fees_collected_cash=max(0, int(tournaments[tref].get("entry_fees_collected_cash", 0))),
                    )
                    writes[_WORLD_HISTORY_PATH] = world_history
                    reviews.append({"kind": "tournament_registration_close", "event_id": event.get("event_id"), "tournament_ref": tref, "entrant_count": len(registrations) if isinstance(registrations, list) else 0, "entrant_faction_count": len(entrant_factions)})
                continue

            if kind == "tournament_convergence_day":
                tournament_kind = str(event.get("tournament_kind") or "")
                competition_date = str(event.get("competition_date") or "")
                tref = _tref(tournament_kind, competition_date)
                tournament = tournaments.get(tref)
                if not isinstance(tournament, Mapping) or tournament.get("status") not in {"bracket_ready", "in_progress"}:
                    reviews.append({
                        "kind": kind, "event_id": event.get("event_id"),
                        "tournament_ref": tref, "result": "tournament_not_ready_for_convergence",
                    })
                    continue
                profile = tournament_event_profile(tournament_kind)
                registrations = [
                    dict(row) for row in tournament.get("registrations", [])
                    if isinstance(row, Mapping) and isinstance(row.get("faction_ref"), str)
                    and isinstance(row.get("entrant_ref"), str)
                ]
                registration_by_faction: dict[str, list[dict[str, Any]]] = {}
                for row in registrations:
                    registration_by_faction.setdefault(str(row["faction_ref"]), []).append(row)
                for rows in registration_by_faction.values():
                    rows.sort(key=lambda row: (-int(row.get("public_qualifying_score", 0)), str(row.get("entrant_ref", ""))))

                # Presence is delegation-wide.  A faction may attend the Great
                # Tournament for diplomacy/observation even if it could not
                # sponsor a fighter, and leaders/elders are legitimate meeting
                # representatives rather than forcing every conversation through
                # the faction's highest-seeded competitor.
                presence_by_faction: dict[str, dict[str, Any]] = {}
                raw_delegations = tournament.get("delegations", {}) if isinstance(tournament.get("delegations"), Mapping) else {}
                for fid, raw in raw_delegations.items():
                    if not isinstance(fid, str) or not isinstance(raw, Mapping):
                        continue
                    entrants = [str(x) for x in raw.get("entrant_refs", []) if isinstance(x, str)]
                    spectators = [str(x) for x in raw.get("spectator_refs", []) if isinstance(x, str)]
                    leaders = [str(x) for x in raw.get("leader_refs", []) if isinstance(x, str)]
                    seniors = [str(x) for x in raw.get("senior_refs", []) if isinstance(x, str)]
                    if not entrants and not spectators:
                        continue
                    presence_by_faction[fid] = {
                        "entrant_refs": entrants, "spectator_refs": spectators,
                        "leader_refs": leaders, "senior_refs": seniors,
                        "camp": str(raw.get("camp") or faction_camp(fid)),
                    }
                for fid, rows in registration_by_faction.items():
                    row = presence_by_faction.setdefault(fid, {
                        "entrant_refs": [], "spectator_refs": [], "leader_refs": [], "senior_refs": [], "camp": faction_camp(fid),
                    })
                    row["entrant_refs"] = sorted(set(row.get("entrant_refs", [])) | {str(r["entrant_ref"]) for r in rows})

                day_index = max(1, int(event.get("convergence_day_index", 1)))
                day_count = max(day_index, int(event.get("convergence_day_count", day_index)))
                day_theme = tournament_convergence_day_theme(tournament_kind, day_index)
                contacts_per_faction = max(0, int(profile.get("convergence_contacts_per_faction_per_day", 1)))

                def current_edge(source: str, target: str) -> Mapping[str, Any] | None:
                    return next((
                        edge for edge in relation_index.get(source, [])
                        if isinstance(edge, Mapping) and edge.get("to_faction") == target
                    ), None)

                senior_factions = {
                    fid for fid, row in presence_by_faction.items()
                    if isinstance(row, Mapping) and (row.get("leader_refs") or row.get("senior_refs"))
                }
                camp_by_faction = {fid: faction_camp(fid) or "unclassified" for fid in presence_by_faction}
                hostility_by_pair: dict[tuple[str, str], int] = {}
                for fa in sorted(presence_by_faction):
                    for edge in relation_index.get(fa, []):
                        if not isinstance(edge, Mapping):
                            continue
                        fb = str(edge.get("to_faction") or "")
                        if fb not in presence_by_faction or fa == fb:
                            continue
                        pair = (fa, fb) if fa < fb else (fb, fa)
                        reverse = current_edge(fb, fa)
                        hostility_by_pair[pair] = max(
                            hostility_by_pair.get(pair, 0),
                            max(0, int(edge.get("hostility", 0))),
                            max(0, int(reverse.get("hostility", 0))) if isinstance(reverse, Mapping) else 0,
                        )
                pairs = tournament_themed_convergence_pairs(
                    sorted(presence_by_faction), tournament_ref=tref, day_index=day_index,
                    tournament_kind=tournament_kind, theme=day_theme,
                    contacts_per_faction=contacts_per_faction,
                    senior_faction_refs=sorted(senior_factions),
                    camp_by_faction=camp_by_faction,
                    hostility_by_pair=hostility_by_pair,
                )

                def delegation_representative(fid: str) -> str:
                    row = presence_by_faction.get(fid, {})
                    for key in ("leader_refs", "senior_refs", "entrant_refs", "spectator_refs"):
                        refs = [str(x) for x in row.get(key, []) if isinstance(x, str)] if isinstance(row, Mapping) else []
                        if refs:
                            return sorted(refs)[0]
                    return ""

                meaningful_contacts = 0
                tense_contacts = 0
                mediated_contacts = 0
                senior_contacts = 0
                new_person_contacts = 0
                for fa, fb in pairs:
                    edge_ab = current_edge(fa, fb)
                    edge_ba = current_edge(fb, fa)
                    hostility = max(
                        int(edge_ab.get("hostility", 0)) if isinstance(edge_ab, Mapping) else 0,
                        int(edge_ba.get("hostility", 0)) if isinstance(edge_ba, Mapping) else 0,
                    )
                    has_prior = isinstance(edge_ab, Mapping) or isinstance(edge_ba, Mapping)
                    camp_a = faction_camp(fa)
                    camp_b = faction_camp(fb)
                    same_camp = camp_a == camp_b and bool(camp_a)
                    camp_pressure = cross_camp_pressure(camp_a, camp_b)
                    roll = stable_permille("tournament-convergence-contact", tref, day_index, fa, fb)
                    rep_a = delegation_representative(fa)
                    rep_b = delegation_representative(fb)
                    leaders_a = set(presence_by_faction.get(fa, {}).get("leader_refs", []))
                    leaders_b = set(presence_by_faction.get(fb, {}).get("leader_refs", []))
                    seniors_a = set(presence_by_faction.get(fa, {}).get("senior_refs", [])) | leaders_a
                    seniors_b = set(presence_by_faction.get(fb, {}).get("senior_refs", [])) | leaders_b
                    senior_pair = rep_a in seniors_a and rep_b in seniors_b
                    # The Great Tournament's private-negotiation day is one of
                    # the few lawful neutral spaces where an existing feud or
                    # war can cool without pretending camp identity made the
                    # factions friends.  It requires real senior delegations, a
                    # preexisting relationship edge, and a bounded successful
                    # mediation roll.
                    respect = max(
                        int(edge_ab.get("respect", 0)) if isinstance(edge_ab, Mapping) else 0,
                        int(edge_ba.get("respect", 0)) if isinstance(edge_ba, Mapping) else 0,
                    )
                    trust = max(
                        int(edge_ab.get("trust", 0)) if isinstance(edge_ab, Mapping) else 0,
                        int(edge_ba.get("trust", 0)) if isinstance(edge_ba, Mapping) else 0,
                    )
                    mediation_threshold = min(360, max(40, 80 + max(0, respect) * 3 + max(0, trust)))
                    can_mediate = (
                        tournament_kind == "great_jianghu_tournament"
                        and day_theme == "private_negotiations_and_rivalry_mediation"
                        and has_prior and senior_pair and hostility >= 30
                    )
                    # Camp identity never creates a grievance merely because
                    # two factions coexist in the world. This *is* a real
                    # face-to-face encounter, however, so political-cultural
                    # pressure may make the meeting itself become a small
                    # recorded grievance. One bad exchange can seed rivalry;
                    # it cannot jump directly to feud or war.
                    tension_threshold = min(
                        900,
                        hostility * 8 + camp_pressure * 6
                        + (120 if hostility >= 20 else 0),
                    )
                    contact_threshold = (760 if tournament_kind == "great_jianghu_tournament" else 430) + (80 if same_camp else 0)
                    if can_mediate and stable_permille("tournament-mediation", tref, fa, fb) < mediation_threshold:
                        relation_event = "tournament_mediation"
                        mediated_contacts += 1
                    elif (hostility > 0 or camp_pressure > 0) and roll < tension_threshold:
                        relation_event = "tournament_tension"
                        tense_contacts += 1
                    elif has_prior or roll < min(920, contact_threshold):
                        relation_event = "tournament_contact"
                    else:
                        continue
                    apply_directed_relation_event(fa, fb, relation_event)
                    apply_directed_relation_event(fb, fa, relation_event)
                    meaningful_contacts += 1
                    if senior_pair:
                        senior_contacts += 1
                    if rep_a and rep_b and rep_a != rep_b:
                        severity = 500 if rep_a in leaders_a or rep_b in leaders_b else 350
                        social_state = _social_event(
                            social_state, observer_ref=rep_a, subject_ref=rep_b,
                            event_kind="conversation", severity_milli=severity, player_ref=player_ref,
                        )
                        social_state = _social_event(
                            social_state, observer_ref=rep_b, subject_ref=rep_a,
                            event_kind="conversation", severity_milli=severity, player_ref=player_ref,
                        )
                        new_person_contacts += 1
                if new_person_contacts:
                    writes[_SOCIAL_PATH] = social_state
                spectator_count = sum(len(set(row.get("spectator_refs", []))) for row in presence_by_faction.values())
                delegate_count = sum(len(set(row.get("entrant_refs", [])) | set(row.get("spectator_refs", []))) for row in presence_by_faction.values())
                leader_delegate_count = sum(len(set(row.get("leader_refs", []))) for row in presence_by_faction.values())
                senior_delegate_count = sum(len(set(row.get("senior_refs", [])) | set(row.get("leader_refs", []))) for row in presence_by_faction.values())
                attendance=fund_public_tournament_attendance(
                    tref,tournament_kind=tournament_kind,attendance_date=at_iso[:10],
                    delegate_count=delegate_count,
                )
                public_spectator_count=int(attendance["public_spectator_count"])
                public_spectator_overflow=int(attendance["public_spectator_overflow"])
                public_ticket_cash=int(attendance["public_ticket_cash"])
                venue_capacity=int(attendance["venue_capacity"])
                tournament=tournaments.get(tref,tournament)
                camp_counts: dict[str, int] = {}
                for fid in presence_by_faction:
                    camp = faction_camp(fid) or "unclassified"
                    camp_counts[camp] = camp_counts.get(camp, 0) + 1
                if isinstance(tournament, dict):
                    tournament["peak_delegate_count"] = max(max(0, int(tournament.get("peak_delegate_count", 0))), delegate_count)
                    tournament["peak_faction_count"] = max(max(0, int(tournament.get("peak_faction_count", 0))), len(presence_by_faction))
                    tournament["peak_public_spectator_count"] = max(max(0, int(tournament.get("peak_public_spectator_count", 0))), public_spectator_count)
                    tournaments[tref] = tournament
                    writes[_TOURNAMENTS_PATH] = tournament_state
                world_history = record_event(
                    world_history, at=at_iso, kind="tournament_convergence_day",
                    tournament_ref=tref, tournament_kind=tournament_kind,
                    convergence_day=day_index, convergence_theme=day_theme, entrant_count=len(registrations),
                    spectator_count=spectator_count, delegate_count=delegate_count,
                    leader_delegate_count=leader_delegate_count, senior_delegate_count=senior_delegate_count,
                    public_spectator_count=public_spectator_count,
                    public_spectator_overflow=public_spectator_overflow,
                    public_ticket_cash=public_ticket_cash,
                    prize_cash=max(0,int(tournament.get("prize_escrow_cash",0))),
                    faction_count=len(presence_by_faction), meaningful_contacts=meaningful_contacts,
                    tense_contacts=tense_contacts, mediated_contacts=mediated_contacts, senior_contacts=senior_contacts,
                    camp_counts=dict(sorted(camp_counts.items())),
                )
                writes[_WORLD_HISTORY_PATH] = world_history
                notice = {
                    "kind": "great_tournament_convergence" if tournament_kind == "great_jianghu_tournament" else "regional_tournament_convergence",
                    "tournament_ref": tref, "host_place_id": tournament.get("host_place_ref"),
                    "convergence_day": day_index, "convergence_days": day_count,
                    "convergence_theme": day_theme,
                    "entrant_count": len(registrations), "spectator_count": spectator_count,
                    "delegate_count": delegate_count, "leader_delegate_count": leader_delegate_count,
                    "senior_delegate_count": senior_delegate_count,
                    "public_spectator_count": public_spectator_count,
                    "public_spectator_overflow": public_spectator_overflow,
                    "public_ticket_cash": public_ticket_cash,
                    "prize_cash": max(0,int(tournament.get("prize_escrow_cash",0))),
                    "venue_capacity": venue_capacity,
                    "faction_count": len(presence_by_faction),
                    "camp_counts": dict(sorted(camp_counts.items())),
                    "meaningful_contacts": meaningful_contacts, "tense_contacts": tense_contacts,
                    "mediated_contacts": mediated_contacts, "senior_contacts": senior_contacts,
                    "opening_assembly": day_index == day_count,
                    "delivered_to_player": tournament_kind == "great_jianghu_tournament",
                    "requires_player_decision": False,
                }
                handoff = classify_handoff(notice)
                reviews.append({
                    "kind": kind, "event_id": event.get("event_id"), "tournament_ref": tref,
                    "entrant_count": len(registrations), "spectator_count": spectator_count,
                    "delegate_count": delegate_count, "leader_delegate_count": leader_delegate_count,
                    "senior_delegate_count": senior_delegate_count,
                    "public_spectator_count": public_spectator_count,
                    "public_spectator_overflow": public_spectator_overflow,
                    "public_ticket_cash": public_ticket_cash,
                    "prize_cash": max(0,int(tournament.get("prize_escrow_cash",0))),
                    "faction_count": len(presence_by_faction),
                    "meaningful_contacts": meaningful_contacts, "tense_contacts": tense_contacts,
                    "mediated_contacts": mediated_contacts, "senior_contacts": senior_contacts, "handoff": handoff,
                })
                if handoff["class"] != "internal":
                    handoffs.append({**notice, "handoff": handoff})
                continue

            # Competition day. Large paid fields have no entrant cap. They
            # advance through finite venue throughput across as many real days
            # as needed, resuming the same bracket frontier each day.
            competition_date = str(event.get("due_at") or at_iso)[:10]
            if kind == "tournament_competition_continue":
                tref = str(event.get("tournament_ref") or event.get("owner_ref") or "")
            else:
                tref = _tref(kind, competition_date)
            tournament = tournaments.get(tref)
            if not isinstance(tournament, Mapping):
                reviews.append({"kind": kind, "event_id": event.get("event_id"), "result": "tournament_not_opened"})
                continue
            if tournament.get("status") == "registration_open":
                tournament = close_registration(tournament)
            tournament_kind = str(tournament.get("tournament_kind") or (kind if kind != "tournament_competition_continue" else ""))
            if tournament_kind not in {"regional_martial_tournament", "great_jianghu_tournament"}:
                reviews.append({"kind": kind, "event_id": event.get("event_id"), "tournament_ref": tref, "result": "tournament_kind_invalid"})
                continue
            profile = tournament_event_profile(tournament_kind)
            delegation_rows = tournament.get("delegations", {}) if isinstance(tournament.get("delegations"), Mapping) else {}
            competition_delegate_count = sum(
                len(set(row.get("entrant_refs", [])) | set(row.get("spectator_refs", [])))
                for row in delegation_rows.values() if isinstance(row, Mapping)
            ) if isinstance(delegation_rows, Mapping) else 0
            competition_attendance = fund_public_tournament_attendance(
                tref, tournament_kind=tournament_kind, attendance_date=competition_date,
                delegate_count=competition_delegate_count,
            )
            tournament = tournaments.get(tref, tournament)
            public_spectator_count = max(0, int(competition_attendance.get("public_spectator_count", 0)))
            public_spectator_overflow = max(0, int(competition_attendance.get("public_spectator_overflow", 0)))
            public_ticket_cash = max(0, int(competition_attendance.get("public_ticket_cash", 0)))
            matches_this_session = max(1, int(profile.get("matches_per_competition_session", 16)))
            sessions_per_day = max(1, int(profile.get("competition_sessions_per_day", 1)))
            session_index = max(1, int(event.get("competition_session_index", 1)))
            max_match_exchanges = max(1, int(profile.get("max_exchanges_per_match", 96)))
            owner_map = _registration_owner_map(tournament)
            people: dict[str, Mapping[str, Any]] = {}
            doctrines: dict[str, Mapping[str, Any]] = {}
            blocked_at_competition = unavailable_person_refs()
            trip_rows = deployments_state.get("deployments", {}) if isinstance(deployments_state, Mapping) else {}
            tournament_trip_refs = {
                str(ref) for row in trip_rows.values() if isinstance(row, Mapping)
                and row.get("operation_kind") == "tournament_travel" and row.get("tournament_ref") == tref and row.get("status") == "at_tournament"
                for ref in row.get("participant_refs", []) if isinstance(ref, str)
            } if isinstance(trip_rows, Mapping) else set()
            blocked_at_competition -= tournament_trip_refs
            host_place = str(tournament.get("host_place_ref") or "")
            for ref, fid in sorted(owner_map.items()):
                _rpath, roster = load_roster(fid)
                match = next((p for p in roster.get("people", []) if isinstance(p, Mapping) and p.get("person_id") == ref), None)
                # A registration is not a month-long reservation.  If an entrant
                # is committed elsewhere or has physically left the host by
                # competition day, they are unavailable and forfeit normally.
                if isinstance(match, Mapping) and ref not in blocked_at_competition:
                    site = site_rows.get(str(match.get("location_ref")))
                    if not host_place or (isinstance(site, Mapping) and site.get("parent_place_ref") == host_place):
                        people[ref] = copy.deepcopy(dict(match))
                _fpath, faction = load_faction(fid)
                doctrines[fid] = faction.get("doctrine", {}) if isinstance(faction.get("doctrine"), Mapping) else {}
            advanced = advance_individual_competition(
                tournament, people=people, equipment_ledger=equipment_ledger,
                doctrines=doctrines, combats_state=combats_state,
                zone_ref=str(tournament.get("venue_site_ref") or tournament.get("host_place_ref") or "tournament_venue"),
                at_iso=at_iso, player_ref=player_ref or None,
                max_matches=matches_this_session, max_exchanges=max_match_exchanges,
            )
            # Keep one compact current-event accumulator for faction-wide
            # performance.  Every actual match win contributes one point to
            # the sponsoring institution.  This lets the final public result
            # show which factions demonstrated roster depth, not only which
            # single person won the championship, without persisting a
            # per-match historical ledger.
            live_after = dict(advanced["tournament_after"])
            existing_performance = tournament.get("faction_performance_points", {})
            faction_performance: dict[str, int] = {
                str(fid): max(0, int(points))
                for fid, points in existing_performance.items()
                if isinstance(fid, str)
            } if isinstance(existing_performance, Mapping) else {}
            for winner_ref, points in advanced.get("winner_points", {}).items():
                winner_faction = str(owner_map.get(str(winner_ref)) or "")
                if winner_faction and int(points) > 0:
                    faction_performance[winner_faction] = faction_performance.get(winner_faction, 0) + int(points)
            live_after["faction_performance_points"] = dict(sorted(faction_performance.items()))
            advanced["tournament_after"] = live_after
            # Only matches that actually occurred create sportsmanship evidence.
            # Forfeits and merely sharing a bracket do not manufacture trust.
            # A real witnessed match between existing/cross-camp rivals may
            # instead sharpen a bounded martial rivalry while still increasing
            # respect; one tournament fight cannot manufacture a feud or war.
            for pair in advanced.get("resolved_pairs", []):
                if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                    continue
                fa = str(owner_map.get(str(pair[0])) or "")
                fb = str(owner_map.get(str(pair[1])) or "")
                if fa and fb and fa != fb:
                    edge_ab = next((
                        edge for edge in relation_index.get(fa, [])
                        if isinstance(edge, Mapping) and edge.get("to_faction") == fb
                    ), None)
                    edge_ba = next((
                        edge for edge in relation_index.get(fb, [])
                        if isinstance(edge, Mapping) and edge.get("to_faction") == fa
                    ), None)
                    hostility = max(
                        max(0, int(edge_ab.get("hostility", 0))) if isinstance(edge_ab, Mapping) else 0,
                        max(0, int(edge_ba.get("hostility", 0))) if isinstance(edge_ba, Mapping) else 0,
                    )
                    relation_event = tournament_match_relation_event(
                        faction_a=fa, faction_b=fb, tournament_ref=tref,
                        person_a=str(pair[0]), person_b=str(pair[1]), hostility=hostility,
                    )
                    apply_directed_relation_event(fa, fb, relation_event)
                    apply_directed_relation_event(fb, fa, relation_event)
            _write_tournament_people(owner_map, advanced["people_after"])
            equipment_ledger = copy.deepcopy(dict(advanced["equipment_ledger_after"]))
            combats_state = copy.deepcopy(dict(advanced["combats_state_after"]))
            combats = combats_state.setdefault("combats", {})
            if not isinstance(combats, dict):
                raise ValueError("jianghu combat state invalid")
            writes[_EQUIPMENT_LEDGER_PATH] = equipment_ledger
            writes[_COMBATS_PATH] = combats_state
            public_audience = f"public:{host_place}" if host_place else "public:tournament"
            for ref, points in advanced.get("winner_points", {}).items():
                ref = str(ref); points = int(points)
                reputation_state = _reputation_after_points(reputation_state, ref, tournament_points=points)
                if points > 0:
                    reputation_state = apply_personal_fame_evidence(
                        reputation_state, audience_ref=public_audience, person_ref=ref,
                        evidence_kind="tournament_placement", delivered=True,
                    )
                    owner_faction = str(owner_map.get(ref) or "")
                    if owner_faction:
                        reputation_state = apply_faction_awareness_evidence(
                            reputation_state, audience_ref=public_audience, faction_ref=owner_faction,
                            evidence_kind="public_tournament", delivered=True,
                        )
                        reputation_state = apply_faction_reputation_evidence(
                            reputation_state, audience_ref=public_audience, faction_ref=owner_faction,
                            axis_deltas={"martial_respect": min(5, max(1, points))}, delivered=True,
                        )
            if advanced["waiting_for_player"]:
                live = dict(advanced["tournament_after"]); tournaments[tref] = live
                combat_ref = str(advanced.get("combat_ref") or "")
                pair = [str(x) for x in live.get("active_pair", []) if isinstance(x, str)]
                if combat_ref and len(pair) == 2:
                    resources = [("person", ref, str(owner_map.get(ref) or "")) for ref in pair]
                    commitments_state = reserve_resources(
                        commitments_state, resources=resources,
                        actor_ref=player_ref or pair[0], owner_ref=tref,
                        activity_ref=combat_ref, activity_kind="tournament_match",
                        started_at=at_iso, location_ref=str(live.get("venue_site_ref") or live.get("host_place_ref") or ""),
                    )
                    for fid in sorted(set(str(owner_map.get(ref) or "") for ref in pair if owner_map.get(ref))):
                        pause_people_for_commitment(fid, [ref for ref in pair if owner_map.get(ref) == fid])
                    writes[_COMMITMENTS_PATH] = commitments_state
                writes[_TOURNAMENTS_PATH] = tournament_state; writes[_REPUTATION_PATH] = reputation_state
                notice = {"kind": "tournament_match_due", "tournament_ref": tref, "combat_ref": advanced.get("combat_ref"), "requires_player_decision": True, "delivered_to_player": True}
                handoff = classify_handoff(notice); reviews.append({"kind": kind, "event_id": event.get("event_id"), "tournament_ref": tref, "result": "awaiting_player_match", "handoff": handoff}); handoffs.append({**notice, "handoff": handoff})
                continue
            if advanced.get("continuation_required"):
                live = dict(advanced["tournament_after"])
                completed_days = max(0, int(tournament.get("competition_days_completed", 0)))
                if session_index < sessions_per_day:
                    next_session = session_index + 1
                    next_due = at + timedelta(hours=2)
                    result_kind = "competition_session_complete"
                else:
                    live["competition_days_completed"] = completed_days + 1
                    next_session = 1
                    next_due = (at + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
                    result_kind = "competition_day_complete"
                tournaments[tref] = live
                pending_one_off_events.append({
                    "event_id": f"tournament_competition_continue:{tref}:{next_due.isoformat()}",
                    "kind": "tournament_competition_continue",
                    "due_at": next_due.isoformat(),
                    "owner_ref": tref,
                    "tournament_ref": tref,
                    "competition_session_index": next_session,
                    "requires_player_decision": False,
                })
                writes[_TOURNAMENTS_PATH] = tournament_state
                writes[_REPUTATION_PATH] = reputation_state
                reviews.append({
                    "kind": kind, "event_id": event.get("event_id"), "tournament_ref": tref,
                    "result": result_kind,
                    "competition_day": completed_days + 1,
                    "competition_session": session_index,
                    "sessions_per_day": sessions_per_day,
                    "matches_resolved": int(advanced.get("matches_resolved_count", 0)),
                    "public_spectator_count": public_spectator_count,
                    "public_spectator_overflow": public_spectator_overflow,
                    "public_ticket_cash": public_ticket_cash,
                    "prize_cash": max(0,int(live.get("prize_escrow_cash",0))),
                    "next_competition_at": next_due.isoformat(),
                })
                continue
            champion = advanced.get("champion_ref")
            live = dict(advanced["tournament_after"])
            competition_days = max(0, int(tournament.get("competition_days_completed", 0))) + 1
            prize = max(0, int(live.get("prize_escrow_cash", 0)))
            champion_faction_prize = 0
            champion_personal_prize = 0
            performance_points = live.get("faction_performance_points", {}) if isinstance(live.get("faction_performance_points"), Mapping) else {}
            standings_limit = max(0, int(profile.get("public_faction_standings_count", 0)))
            top_faction_performance = faction_performance_standings(
                performance_points, owner_map, limit=standings_limit,
            )
            delegation_rows = live.get("delegations", {}) if isinstance(live.get("delegations"), Mapping) else {}
            attending_factions = {
                str(fid) for fid, row in delegation_rows.items()
                if isinstance(fid, str) and isinstance(row, Mapping)
                and max(0, int(row.get("present_count", 0))) > 0
            }
            attending_factions.update(str(fid) for fid in owner_map.values() if isinstance(fid, str) and fid)

            # The Great Tournament exists to establish institutional martial
            # standing in front of the Jianghu, not merely an individual
            # champion.  Deliver the compact top-faction table only to public
            # audiences and institutions that actually had a delegation there.
            # This changes current reputation/awareness; it does not create a
            # giant per-match witness history or make absent factions omniscient.
            max_performance_respect = max(0, int(profile.get("faction_performance_max_martial_respect", 0)))
            recognized_performance: list[dict[str, Any]] = []
            for rank_index, standing in enumerate(top_faction_performance):
                faction_ref = str(standing.get("faction_ref") or "")
                if not faction_ref:
                    continue
                rank = rank_index + 1
                if max_performance_respect > 0 and standings_limit > 0:
                    delta = max(1, max_performance_respect - ((rank - 1) * max_performance_respect // max(1, standings_limit)))
                else:
                    delta = 0
                enriched = dict(standing)
                enriched["rank"] = rank
                enriched["camp"] = faction_camp(faction_ref) or "unclassified"
                enriched["martial_respect_delta"] = delta
                recognized_performance.append(enriched)
                if delta <= 0:
                    continue
                reputation_state = apply_faction_awareness_evidence(
                    reputation_state, audience_ref=public_audience, faction_ref=faction_ref,
                    evidence_kind="public_tournament", delivered=True,
                )
                reputation_state = apply_faction_reputation_evidence(
                    reputation_state, audience_ref=public_audience, faction_ref=faction_ref,
                    axis_deltas={"martial_respect": delta}, delivered=True,
                )
                for audience_fid in sorted(attending_factions):
                    faction_audience = f"faction:{audience_fid}"
                    reputation_state = apply_faction_awareness_evidence(
                        reputation_state, audience_ref=faction_audience, faction_ref=faction_ref,
                        evidence_kind="public_tournament", delivered=True,
                    )
                    reputation_state = apply_faction_reputation_evidence(
                        reputation_state, audience_ref=faction_audience, faction_ref=faction_ref,
                        axis_deltas={"martial_respect": delta}, delivered=True,
                    )
            top_faction_performance = recognized_performance
            placement_awards: list[dict[str, Any]] = []
            payout_rows = tournament_placement_payouts(live)
            if prize > 0 and not payout_rows:
                raise ValueError("funded tournament completed without earned placements")
            faction_share = max(0, min(1000, int(live.get("placement_faction_share_permille", 700))))
            personal_share = max(0, min(1000, int(live.get("placement_personal_share_permille", 300))))
            if faction_share + personal_share != 1000:
                raise ValueError("tournament placement payout shares invalid")
            total_prize_paid = 0
            placement_reputation_points = {"first": 4, "second": 3, "third": 3, "fourth": 2}
            for award in payout_rows:
                place = str(award.get("place") or "")
                ref = str(award.get("entrant_ref") or "")
                gross = max(0, int(award.get("cash", 0)))
                if not place or not ref or gross <= 0:
                    continue
                fid = str(owner_map.get(ref) or "")
                faction_cash = 0
                personal_cash = gross
                if fid:
                    faction_cash = gross * faction_share // 1000
                    personal_cash = gross - faction_cash
                    fpath, faction = load_faction(fid)
                    faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + faction_cash
                    writes[fpath] = faction; faction_cache[fid] = (fpath, faction)
                    rpath, roster = load_roster(fid)
                    rows = roster.get("people", []) if isinstance(roster, Mapping) else []
                    if isinstance(rows, list):
                        for i, raw in enumerate(rows):
                            if isinstance(raw, Mapping) and raw.get("person_id") == ref:
                                person = copy.deepcopy(dict(raw))
                                person["personal_cash"] = max(0, int(person.get("personal_cash", 0))) + personal_cash
                                rows[i] = person
                                break
                        writes[rpath] = roster; roster_cache[fid] = (rpath, roster)
                else:
                    # An unaffiliated entrant has no faction treasury share;
                    # preserve the full earned placement prize as personal cash.
                    personal_cash = gross
                    owner_fid = next((
                        owner for owner, (_path, roster) in roster_cache.items()
                        if isinstance(roster, Mapping) and any(
                            isinstance(row, Mapping) and row.get("person_id") == ref
                            for row in roster.get("people", [])
                        )
                    ), None)
                    if owner_fid:
                        rpath, roster = load_roster(owner_fid)
                        rows = roster.get("people", []) if isinstance(roster, Mapping) else []
                        for i, raw in enumerate(rows if isinstance(rows, list) else []):
                            if isinstance(raw, Mapping) and raw.get("person_id") == ref:
                                person = copy.deepcopy(dict(raw))
                                person["personal_cash"] = max(0, int(person.get("personal_cash", 0))) + personal_cash
                                rows[i] = person
                                break
                        writes[rpath] = roster; roster_cache[owner_fid] = (rpath, roster)
                total_prize_paid += gross
                evidence_kind = "tournament_win" if place == "first" else "tournament_placement"
                reputation_state = _reputation_after_points(
                    reputation_state, ref,
                    tournament_points=max(1, int(placement_reputation_points.get(place, 1))),
                )
                reputation_state = apply_personal_fame_evidence(
                    reputation_state, audience_ref=public_audience, person_ref=ref,
                    evidence_kind=evidence_kind, delivered=True,
                )
                placement_awards.append({
                    "place": place, "entrant_ref": ref, "faction_ref": fid or None,
                    "gross_prize_cash": gross, "faction_prize_cash": faction_cash,
                    "personal_prize_cash": personal_cash,
                })
            if total_prize_paid != prize:
                raise ValueError("tournament prize escrow not fully paid to placements")
            live["prize_escrow_cash"] = 0
            champion_award = next((row for row in placement_awards if row.get("place") == "first"), None)
            if isinstance(champion_award, Mapping):
                champion_faction_prize = max(0, int(champion_award.get("faction_prize_cash", 0)))
                champion_personal_prize = max(0, int(champion_award.get("personal_prize_cash", 0)))
            if isinstance(champion, str) and champion:
                champion_faction = str(owner_map.get(champion) or "")
                if champion_faction:
                    reputation_state = apply_faction_awareness_evidence(
                        reputation_state, audience_ref=public_audience, faction_ref=champion_faction,
                        evidence_kind="public_tournament", delivered=True,
                    )
                    reputation_state = apply_faction_reputation_evidence(
                        reputation_state, audience_ref=public_audience, faction_ref=champion_faction,
                        axis_deltas={"martial_respect": 6}, delivered=True,
                    )
                    for audience_fid in sorted(attending_factions):
                        faction_audience = f"faction:{audience_fid}"
                        reputation_state = apply_personal_fame_evidence(
                            reputation_state, audience_ref=faction_audience, person_ref=champion,
                            evidence_kind="tournament_win", delivered=True,
                        )
                        reputation_state = apply_faction_awareness_evidence(
                            reputation_state, audience_ref=faction_audience, faction_ref=champion_faction,
                            evidence_kind="public_tournament", delivered=True,
                        )
                        reputation_state = apply_faction_reputation_evidence(
                            reputation_state, audience_ref=faction_audience, faction_ref=champion_faction,
                            axis_deltas={"martial_respect": 6}, delivered=True,
                        )
            returning_travelers = schedule_tournament_returns(tref)
            tournament_kind = str(live.get("tournament_kind") or kind)
            world_history = record_event(
                world_history, at=at_iso, kind="tournament_result", tournament_ref=tref,
                tournament_kind=tournament_kind, champion_ref=champion, prize_cash=prize,
                placements=dict(live.get("placements", {})) if isinstance(live.get("placements"), Mapping) else {},
                placement_awards=placement_awards,
                champion_faction_prize_cash=champion_faction_prize, champion_personal_prize_cash=champion_personal_prize,
                entrant_count=len(live.get("registrations", [])) if isinstance(live.get("registrations"), list) else 0,
                entry_fees_collected_cash=max(0, int(live.get("entry_fees_collected_cash", 0))),
                faction_delegate_ticket_cash_collected=max(0, int(live.get("delegate_ticket_cash_collected", 0))),
                public_ticket_cash_collected=max(0, int(live.get("public_ticket_cash_collected", 0))),
                attending_faction_count=max(0, int(live.get("peak_faction_count", 0))),
                peak_delegate_count=max(0, int(live.get("peak_delegate_count", 0))),
                peak_public_spectator_count=max(0, int(live.get("peak_public_spectator_count", 0))),
                top_faction_performance=top_faction_performance,
                performance_witness_faction_count=len(attending_factions),
                returning_travelers=returning_travelers, competition_days=competition_days,
            )
            tournaments.pop(tref, None)
            writes[_TOURNAMENTS_PATH] = tournament_state; writes[_REPUTATION_PATH] = reputation_state; writes[_WORLD_HISTORY_PATH] = world_history
            notice = {
                "kind": "tournament_result", "tournament_ref": tref,
                "champion_ref": champion, "prize_cash": prize,
                "placements": dict(live.get("placements", {})) if isinstance(live.get("placements"), Mapping) else {},
                "placement_awards": placement_awards,
                "entry_fees_collected_cash": max(0, int(live.get("entry_fees_collected_cash", 0))),
                "faction_delegate_ticket_cash_collected": max(0, int(live.get("delegate_ticket_cash_collected", 0))),
                "public_ticket_cash_collected": max(0, int(live.get("public_ticket_cash_collected", 0))),
                "champion_faction_prize_cash": champion_faction_prize,
                "champion_personal_prize_cash": champion_personal_prize,
                "attending_faction_count": max(0, int(live.get("peak_faction_count", 0))),
                "peak_delegate_count": max(0, int(live.get("peak_delegate_count", 0))),
                "peak_public_spectator_count": max(0, int(live.get("peak_public_spectator_count", 0))),
                "top_faction_performance": top_faction_performance,
                "performance_witness_faction_count": len(attending_factions),
                "returning_travelers": returning_travelers,
                "delivered_to_player": True, "requires_player_decision": False,
            }
            handoff = classify_handoff(notice); reviews.append({"kind": kind, "event_id": event.get("event_id"), "tournament_ref": tref, "champion_ref": champion, "handoff": handoff}); handoffs.append({**notice, "handoff": handoff})

    # Annual public ranking publication is a bounded read model over compact
    # evidence aggregates. Only the current top table is persisted.
    for event in sorted_events:
        if event.get("kind") != "jianghu_ranking_publication":
            continue
        audiences = reputation_state.get("audiences", {}) if isinstance(reputation_state, Mapping) else {}
        records = [
            {"person_id": str(ref), **dict(row)}
            for ref, row in audiences.items()
            if isinstance(ref, str) and isinstance(row, Mapping)
        ] if isinstance(audiences, Mapping) else []
        rows = publish_rankings(records)[:100]
        rankings = reputation_state.setdefault("rankings", {})
        if isinstance(rankings, dict):
            rankings["public"] = {"published_at": at_iso, "rows": rows}
        writes[_REPUTATION_PATH] = reputation_state
        notice = {"kind": "ranking_publication", "published_at": at_iso, "top": rows[:10], "delivered_to_player": True, "requires_player_decision": False}
        handoff = classify_handoff(notice); reviews.append({"kind": "jianghu_ranking_publication", "event_id": event.get("event_id"), "ranked_count": len(rows), "handoff": handoff}); handoffs.append({**notice, "handoff": handoff})

    # Annual life course updates existing people only: physical maturation and
    # deterministic natural mortality. It never creates courtships or marriages.
    for event in sorted_events:
        if event.get("kind") != "annual_faction_life_review":
            continue
        fid = event.get("owner_ref")
        if not isinstance(fid, str):
            continue
        fpath, faction = load_faction(fid); rpath, roster = load_roster(fid)
        # Close the current institutional segment before a death can alter the
        # instructor/office environment for future training.
        faction, _training = advance_faction_training_epoch(
            faction, roster, at_iso=at_iso, refresh_environment=False,
        )
        before_people = [p for p in roster.get("people", []) if isinstance(p, Mapping)]
        life = advance_annual_life_course(
            before_people, year=at.year, player_ref=player_ref or None,
            exclude_death_refs=sorted(committed_person_refs() | active_combat_person_refs()),
        )
        roster["people"] = life["people_after"]
        died = list(life["died_refs"]); matured = list(life["matured_refs"])
        retired_refs: list[str] = []
        retired_people: list[Any] = []
        for raw in roster.get("people", []):
            if not isinstance(raw, Mapping):
                retired_people.append(raw); continue
            person = copy.deepcopy(dict(raw))
            if (
                not person.get("retired_from_field")
                and retirement_due(person, year=at.year)
                and (person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}).get("status") != "dead"
            ):
                person["retired_from_field"] = True
                person.pop("standing_duty_ref", None)
                if isinstance(person.get("person_id"), str):
                    retired_refs.append(str(person["person_id"]))
            retired_people.append(person)
        roster["people"] = retired_people
        succession_ref = None
        if died:
            family_state = apply_family_death_status(family_state, dead_refs=died, faction_ref=fid, roster_people=[p for p in roster.get("people", []) if isinstance(p, Mapping)])
            writes[_FAMILY_PATH] = family_state
            dead_set = set(died)
            courtships = social_state.get("courtships", {}) if isinstance(social_state, Mapping) else {}
            if isinstance(courtships, dict):
                for pair_ref in list(courtships):
                    row = courtships.get(pair_ref)
                    refs = row.get("person_refs", []) if isinstance(row, Mapping) else []
                    if any(str(ref) in dead_set for ref in refs):
                        courtships.pop(pair_ref, None)
            relationships = social_state.get("relationships", {}) if isinstance(social_state, Mapping) else {}
            if isinstance(relationships, dict):
                for edge_ref in list(relationships):
                    parts = str(edge_ref).split("|", 1)
                    if any(part in dead_set for part in parts):
                        relationships.pop(edge_ref, None)
            writes[_SOCIAL_PATH] = social_state
            prior_custody_rows = [row for row in custody_state.get("records", []) if isinstance(row, Mapping)]
            active_custody = []
            for row in prior_custody_rows:
                if str(row.get("person_ref")) in dead_set or str(row.get("captor_ref")) in dead_set:
                    continue
                active_custody.append(row)
            if len(active_custody) != len(prior_custody_rows):
                custody_state["records"] = active_custody
                writes[_CUSTODY_PATH] = custody_state
                released_by_captor_death = {
                    str(row.get("person_ref")) for row in prior_custody_rows
                    if str(row.get("captor_ref")) in dead_set
                    and isinstance(row.get("person_ref"), str)
                    and str(row.get("person_ref")) not in dead_set
                }
                pending_training_resume_refs.update(released_by_captor_death)
            succession = apply_recognized_succession(
                family_state, faction_ref=fid,
                roster_people=[p for p in roster.get("people", []) if isinstance(p, Mapping)],
                year=at.year,
            )
            roster["people"] = succession["people_after"]
            succession_ref = succession.get("successor_ref")
        annual_departure_refs = annual_voluntary_departure_refs(
            [p for p in roster.get("people", []) if isinstance(p, Mapping)],
            faction_ref=fid, year=at.year, hardship_milli=0,
            protected_refs=sorted(
                family_bound_refs(fid)
                | unavailable_person_refs()
                | ({player_ref} if player_ref else set())
            ),
            maximum=max(1, living_member_count([p for p in roster.get("people", []) if isinstance(p, Mapping)]) // 200),
            period_key=f"annual-{at.year}",
        )
        if annual_departure_refs:
            leaving = set(annual_departure_refs)
            pre_departure_people = [copy.deepcopy(dict(p)) for p in roster.get("people", []) if isinstance(p, Mapping)]
            kept: list[Any] = []
            independent_rows = independent_state.setdefault("people", [])
            if not isinstance(independent_rows, list):
                raise ValueError("jianghu independent people invalid")
            for raw in roster.get("people", []):
                if not isinstance(raw, Mapping) or str(raw.get("person_id")) not in leaving:
                    kept.append(raw); continue
                person = compact_person_state(raw, faction_ref=fid)
                person.pop("membership_grade", None)
                person.pop("standing_duty_ref", None)
                person["standing_offices"] = []
                person["location_ref"] = str(raw.get("location_ref") or faction.get("local_site_ref") or faction.get("headquarters") or "")
                person["former_faction_ref"] = fid
                person["independent_since"] = at_iso
                independent_rows.append(person)
            roster["people"] = kept
            rewrite_faction_person_routes(fid, pre_departure_people, [p for p in kept if isinstance(p, Mapping)])
            writes[_INDEPENDENTS_PATH] = independent_state
            world_history = record_event(
                world_history, at=at_iso, kind="faction_departure", faction_ref=fid,
                count=len(annual_departure_refs), person_refs=sorted(annual_departure_refs), reason="ordinary_turnover",
            )
        faction = reconcile_faction_population(faction, roster)
        if died:
            world_history = record_event(
                world_history, at=at_iso, kind="natural_death", faction_ref=fid,
                count=len(died), person_refs=sorted(died),
            )
        if retired_refs:
            world_history = record_event(
                world_history, at=at_iso, kind="field_retirement", faction_ref=fid,
                count=len(retired_refs), person_refs=sorted(retired_refs),
            )
        if succession_ref:
            world_history = record_event(
                world_history, at=at_iso, kind="leadership_succession", faction_ref=fid, successor_ref=succession_ref,
            )
        writes[_WORLD_HISTORY_PATH] = world_history
        if died or matured or retired_refs or annual_departure_refs:
            faction, _rotation = advance_faction_training_epoch(
                faction, roster, at_iso=at_iso, refresh_environment=True,
            )
        writes[fpath] = faction; faction_cache[fid] = (fpath, faction)
        writes[rpath] = compact_roster_state(roster, faction=faction); roster_cache[fid] = (rpath, roster)
        reviews.append({
            "kind": "annual_faction_life_review", "event_id": event.get("event_id"),
            "faction_ref": fid, "matured_count": len(matured), "natural_death_count": len(died),
            "retired_count": len(retired_refs), "departed_count": len(annual_departure_refs),
            "succession_ref": succession_ref,
        })
        recurrence_days = max(1, int(event.get("recurrence_days", 365)))
        pending_one_off_events.append({
            "event_id": str(event.get("event_id") or f"annual_faction_life_review:{fid}"),
            "kind": "annual_faction_life_review", "owner_ref": fid,
            "due_at": (at + timedelta(days=recurrence_days)).isoformat(),
            "recurrence_days": recurrence_days, "requires_player_decision": False,
        })
        if died and fid == "house_tang":
            notice = {"kind": "family_death_notice", "faction_ref": fid, "person_refs": sorted(died), "delivered_to_player": True, "requires_player_decision": False}
            handoff = classify_handoff(notice); handoffs.append({**notice, "handoff": handoff})
        if succession_ref and (fid == "house_tang" or succession_ref == player_ref):
            notice = {"kind": "succession_notice", "faction_ref": fid, "successor_ref": succession_ref, "delivered_to_player": True}
            handoff = classify_handoff(notice); handoffs.append({**notice, "handoff": handoff})

    # Civilian populations remain aggregate, but they are not immortal seed
    # numbers.  One public year-end boundary applies compact births/deaths/net
    # migration to each settlement without materializing exact civilians.
    for event in sorted_events:
        if event.get("kind") != "year_end_faction_accounts":
            continue
        places = civilian_state.get("places", {}) if isinstance(civilian_state, Mapping) else {}
        if not isinstance(places, dict):
            raise ValueError("jianghu civilian populations invalid")
        total_births = total_deaths = total_migration = 0
        for place_ref, row in places.items():
            if not isinstance(row, dict):
                continue
            population = max(0, int(row.get("current_population", 0)))
            result = civilian_annual_demography(str(place_ref), population, year=at.year)
            row["current_population"] = int(result["population_after"])
            total_births += int(result["births"]); total_deaths += int(result["deaths"]); total_migration += int(result["net_migration"])
        writes[_CIVILIANS_PATH] = civilian_state
        world_history = record_event(
            world_history, at=at_iso, kind="civilian_demographic_cycle", births=total_births, deaths=total_deaths, net_migration=total_migration,
        )
        writes[_WORLD_HISTORY_PATH] = world_history
        reviews.append({"kind": "civilian_demographic_cycle", "births": total_births, "deaths": total_deaths, "net_migration": total_migration})

    # Calendar rows are public institutions.  Starting/closing them is not by
    # itself acceptance, registration, travel, or a player decision.
    known_internal = {
        "regional_market_cycle", "faction_upkeep", "faction_member_cycle", "equipment_maintenance_review",
        "faction_review", "trade_demand_review", "route_activity_cycle",
        "annual_faction_life_review", "family_birth_due", "contract_expiry_due", "agriculture_harvest_due",
        "tournament_advance_notice", "tournament_registration_open", "tournament_registration_close",
        "regional_martial_tournament", "great_jianghu_tournament", "tournament_competition_continue",
        "jianghu_ranking_publication", "year_end_faction_accounts",
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

    schedule_after = settle_schedule(schedule, through=at, processed_events=sorted_events)
    active_route_ids = sorted({
        str(row.get("route_ref"))
        for row in route_ops_state.get("movements", {}).values()
        if isinstance(row, Mapping) and isinstance(row.get("route_ref"), str)
        and str(row.get("status", "active")) in {"active", "contact_pending"}
    }) if isinstance(route_ops_state.get("movements"), Mapping) else []
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


__all__ = ["settle_martial_world_frontier"]
