"""Monthly faction strategic autonomy and institutional production frontier.

This reducer turns one faction review into conserved actions using the current
faction, roster, inventory, market, project, custody and relationship owners.
It does not own chronology or persist a separate AI-plan ledger.
"""
from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from .agriculture import monthly_enterprise_settlement
from .aggregate_transport import civilian_available_capacity, freight_crew_required, make_transport_reservation
from .autonomous_factions import (
    liquidate_inventory_to_market, monthly_recruitment_tranche, procure_project_materials,
    secure_food_purchase, sell_surplus_to_market,
)
from .commitments import reserve_resources
from .calendar_modifiers import recruitment_capacity_milli
from .compensation import monthly_stipend
from .contracts import transition as contract_transition
from .escort import compact_started_escort_objective, hydrate_contract_escort_objective
from .duties import derive_duty_assignments
from .enterprise_operations import (
    operate_apothecary_month, operate_poison_apothecary_month, operate_brotherhood_livelihood_month,
    operate_criminal_enterprise_month, operate_workshop_month,
)
from .faction_state import allows_independent_recruitment, faction_admission_policy, faction_presentation_identity, resolved_faction_type
from .factions import autonomy_review
from .infrastructure import (
    administration_factor_milli, administrative_workload_units, enterprise_operating_efficiency_milli,
    enterprise_scale_value, infirmary_capacity, residential_capacity, staffed_administrative_capability,
    training_domain_capacity, workshop_capacity,
)
from .institutional_lifecycle import advance_institution, apply_autonomous_clinical_treatment, institutional_status
from .manpower import is_faction_member
from .people import apply_age_development, deterministic_body_mass_kg, deterministic_name, deterministic_sex
from .physical_travel import build_route_journey
from .person_state import hydrate_person_state
from .recruitment import deterministic_candidate
from .strategic_autonomy import choose_friendly_aid_target, choose_hostile_action, choose_investment_priority
from .social_causality import decision_refs, internal_action_consensus, obligations_for_actor, personal_aid_duty_target, resolve_personal_obligation
from .training import advance_faction_training_epoch, school_tuition_snapshot, training_epoch_elapsed_days
from .travel import travel_plan
from .travel_provisions import planned_journey_seconds, provisioning_journey_seconds, reserve_faction_rations
from .upkeep import monthly_upkeep_quote
from .world_health import living_member_count, sustainable_recruitment_gap

_CONTRACT_INDEX_PATH = "state/martial-world/contracts/index.json"
_ROUTE_OPERATIONS_PATH = "state/martial-world/route-operations.json"
_CIVILIANS_PATH = "state/martial-world/civilian-populations.json"
_INDEPENDENTS_PATH = "state/martial-world/independent-people.json"


def _hydrate_recruited_independent(
    raw: Mapping[str, Any], *, faction_ref: str, home_site: str,
) -> dict[str, Any]:
    """Cross one exact independent into faction-owned logical person state.

    Independent storage is sparse and can retain the compact
    ``training_carry_milli`` vector from a prior faction. A newly recruited
    person can immediately be selected for another same-frontier institutional
    action, so normalize that carry before any faction training/duty/commitment
    logic can touch the row.
    """
    person = hydrate_person_state(
        raw, faction_ref=faction_ref, home_location=home_site,
    )
    person["location_ref"] = home_site
    return person


def settle_faction_autonomy_frontier(
    *,
    sorted_events: Sequence[Mapping[str, Any]],
    at: datetime,
    at_iso: str,
    writes: dict[str, Any],
    reviews: list[dict[str, Any]],
    active_contracts: Mapping[str, Any],
    active_after: dict[str, Any],
    contract_after: dict[str, Any],
    contract_index: Mapping[str, Any],
    commitments_state: Mapping[str, Any],
    upkeep_pressure: Mapping[str, Any],
    relation_index: Mapping[str, Any],
    coalition_targets_by_faction: Mapping[str, set[str]],
    projects_state: dict[str, Any],
    route_ops_state: dict[str, Any],
    custody_state: Mapping[str, Any],
    social_state: Mapping[str, Any],
    family_state: Mapping[str, Any],
    civilian_state: dict[str, Any],
    independent_state: dict[str, Any],
    travel_data: Mapping[str, Any],
    economy_rules: Mapping[str, Any],
    geography: Mapping[str, Any],
    place_region: Mapping[str, str],
    route_index: Mapping[str, Any],
    site_rows: Mapping[str, Any],
    world_seed: str,
    player_ref: str,
    general_labor_cash_per_hour: int,
    faction_cache: dict[str, tuple[str, dict[str, Any]]],
    inventory_cache: dict[str, tuple[str, dict[str, Any]]],
    market_cache: dict[str, tuple[str, dict[str, Any]]],
    roster_cache: dict[str, tuple[str, dict[str, Any]]],
    load_faction: Callable[[str], tuple[str, dict[str, Any]]],
    load_inventory: Callable[[str], tuple[str, dict[str, Any]]],
    load_market: Callable[[str], tuple[str, dict[str, Any]]],
    load_roster: Callable[[str], tuple[str, dict[str, Any]]],
    load_person_ref: Callable[[str], tuple[str, str, dict[str, Any], int, dict[str, Any]]],
    unavailable_person_refs: Callable[[], set[str]],
    usable_martial_people: Callable[..., list[dict[str, Any]]],
    person_combat_index: Callable[[Mapping[str, Any]], int],
    active_strategic_operations: Callable[[str], list[Mapping[str, Any]]],
    all_existing_names: Callable[[], set[str]],
    pause_people_for_commitment: Callable[[str, Sequence[str]], None],
    start_monthly_merchant_trade: Callable[[str], dict[str, Any]],
    start_custody_rescue_operation: Callable[[str, Mapping[str, Any]], dict[str, Any]],
    start_strategic_operation: Callable[..., dict[str, Any]],
    start_autonomous_investment: Callable[[str, Mapping[str, Any]], dict[str, Any]],
    execute_friendly_aid: Callable[[str, str], dict[str, Any]],
    prepare_patient_for_treatment: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    get_commitments_state: Callable[[], Mapping[str, Any]] | None = None,
    set_commitments_state: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    clinical_physiology_rebases: dict[str, dict[str, Any]] = {}
    try:
        player_faction_ref = str(load_person_ref(player_ref)[0]) if player_ref else ""
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        player_faction_ref = ""

    def refresh_commitments() -> Mapping[str, Any]:
        nonlocal commitments_state
        if get_commitments_state is not None:
            commitments_state = get_commitments_state()
        return commitments_state

    def publish_commitments(value: Mapping[str, Any]) -> None:
        nonlocal commitments_state
        commitments_state = value
        if set_commitments_state is not None:
            set_commitments_state(value)

    # Callback-owned activities and this reducer must share one live commitment
    # authority. Several callbacks reserve people by replacing the bridge's
    # immutable commitment after-image; a stale snapshot here would otherwise
    # let a later action in the same frontier reserve the same person again.
    refresh_commitments()
    social_after = copy.deepcopy(dict(social_state))
    reviewed_factions: set[str] = set()
    newly_dead_refs: set[str] = set()
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
        transport = inventory.get("transport_capacity", {})
        if not isinstance(transport, Mapping):
            transport = {}
        upkeep = monthly_upkeep_quote(
            faction,
            rider_capacity_slots=int(transport.get("rider_slots", 0)),
            freight_capacity_kg=int(transport.get("freight_capacity_kg", 0)),
        )
        _rpath, review_roster = load_roster(fid)
        review_people = review_roster.get("people", []) if isinstance(review_roster, Mapping) else []
        senior_decision_refs = decision_refs([p for p in review_people if isinstance(p, Mapping)])

        def decision_consensus(target_fid: str, action_kind: str) -> dict[str, Any]:
            try:
                _target_path, target_roster = load_roster(target_fid)
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                return {"support_refs": [], "oppose_refs": [], "neutral_refs": senior_decision_refs, "pressure": 0, "scores": {}}
            target_people = target_roster.get("people", []) if isinstance(target_roster, Mapping) else []
            target_refs = [
                str(person.get("person_id")) for person in target_people
                if isinstance(person, Mapping) and isinstance(person.get("person_id"), str)
                and not (isinstance(person.get("health"), Mapping) and person.get("health", {}).get("status") == "dead")
            ]
            person_factions = {ref: target_fid for ref in target_refs}
            return internal_action_consensus(
                social_after, family_state, decision_person_refs=senior_decision_refs,
                person_faction_by_ref=person_factions, target_faction_ref=target_fid,
                target_member_refs=target_refs, action_kind=action_kind,
            )

        def personal_aid_target() -> tuple[str, str] | None:
            if cash_reserve_months < 1:
                return None
            counterparty_factions: dict[str, str] = {}
            for decision_ref in senior_decision_refs:
                for obligation in obligations_for_actor(social_after, decision_ref):
                    counterparty = str(obligation.get("counterparty_ref") or "")
                    if not counterparty or counterparty in counterparty_factions:
                        continue
                    try:
                        other_fid, _path, _owner, _ordinal, _person = load_person_ref(counterparty)
                    except (FileNotFoundError, KeyError, TypeError, ValueError):
                        continue
                    counterparty_factions[counterparty] = str(other_fid or "")
            chosen = personal_aid_duty_target(
                social_after, decision_person_refs=senior_decision_refs,
                counterparty_faction_by_ref=counterparty_factions,
                relation_edges=[row for row in relation_index.get(fid, []) if isinstance(row, Mapping)],
                own_faction_ref=fid,
            )
            if not isinstance(chosen, Mapping):
                return None
            return str(chosen.get("target_faction_ref") or ""), str(chosen.get("obligation_ref") or "")

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
        recruitment_capacity = recruitment_capacity * recruitment_capacity_milli(at) // 1000
        institutional = institutional_status(
            faction, review_roster, year=at.year, social=social_after,
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
            open_contracts=active_for_faction,
            recruitment_capacity=recruitment_capacity,
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
                criminal_level = max(0, int(enterprises.get("criminal_enterprise", 0)))
                criminal_scale = enterprise_scale_value(faction, "criminal_enterprise") if criminal_level > 0 else 0
                criminal_efficiency = enterprise_operating_efficiency_milli("criminal_enterprise", criminal_level) if criminal_level > 0 else 0
                if trade_scale > 0 and trade_efficiency > 0:
                    monthly_trade_value_cap = trade_scale * trade_efficiency // 1000
                    allowed_sale_items = None
                    sale_channel = "merchant_surplus_sale"
                elif criminal_scale > 0 and criminal_efficiency > 0:
                    monthly_trade_value_cap = max(500, criminal_scale * criminal_efficiency // 1000)
                    sale = liquidate_inventory_to_market(
                        faction, inventory, regional_market, region_id=region,
                        max_trade_value_cash=monthly_trade_value_cap,
                        maximum_fraction_milli=max(150, min(650, 700 - int((faction.get("autonomy_policy", {}) or {}).get("financial_caution", 50)) * 5)),
                    )
                    faction = sale["faction"]; inventory = sale["inventory"]; regional_market = sale["market"]
                    faction_cache[fid] = (fpath, faction); inventory_cache[fid] = (ipath, inventory); market_cache[region] = (mpath, regional_market)
                    writes[fpath] = faction; writes[ipath] = inventory; writes[mpath] = regional_market
                    market_sale_done = int(sale.get("quantity", 0)) > 0
                    executed_actions.append({
                        "action": action, "result": str(sale.get("reason", "evaluated")),
                        "item_ref": sale.get("item_ref"), "quantity": int(sale.get("quantity", 0)),
                        "cash_earned": int(sale.get("cash_earned", 0)), "sale_channel": "criminal_fence_sale",
                    })
                    continue
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
                faction_type = resolved_faction_type(faction)
                eligible_raid_target_refs: set[str] | None = None
                if faction_type == "outlaw_faction":
                    local_places = {str(faction.get("headquarters") or "")}
                    local_places.discard("")
                    for route_ref in [
                        str(x) for x in faction.get("operating_routes", [])
                        if isinstance(x, str) and x
                    ]:
                        route = route_index.get(route_ref) if isinstance(route_index, Mapping) else None
                        if not isinstance(route, Mapping):
                            continue
                        local_places.update(
                            str(route[key]) for key in ("from", "to") if route.get(key)
                        )
                    eligible_raid_target_refs = set()
                    for edge in relation_index.get(fid, []):
                        if not isinstance(edge, Mapping):
                            continue
                        target_ref = str(edge.get("to_faction") or "")
                        if not target_ref or target_ref == fid:
                            continue
                        try:
                            _target_path, target_faction = load_faction(target_ref)
                        except (FileNotFoundError, KeyError, ValueError):
                            continue
                        if str(target_faction.get("headquarters") or "") in local_places:
                            eligible_raid_target_refs.add(target_ref)
                intent = choose_hostile_action(
                    relation_index.get(fid, []), faction_ref=fid, year=at.year, month=at.month,
                    risk_tolerance=int(policy.get("risk_tolerance", 50)),
                    active_strategic_operations=active_strategic_operations(fid),
                    faction_type=faction_type,
                    outlaw_subtype=str(faction.get("outlaw_subtype") or ""),
                    eligible_raid_target_refs=eligible_raid_target_refs,
                    coalition_target_refs=set(coalition_targets_by_faction.get(fid, set())),
                )
                if intent is None:
                    executed_actions.append({"action": action, "result": "no_bounded_hostile_action"})
                else:
                    target_fid=str(intent.get("target_faction_ref") or "")
                    camp=decision_consensus(target_fid, "hostile")
                    if int(camp.get("pressure", 0)) <= -45 and len(camp.get("oppose_refs", [])) > len(camp.get("support_refs", [])):
                        executed_actions.append({
                            "action": action, "result": "internal_loyalty_conflict",
                            "intent": str(intent.get("action")), "target_faction_ref": target_fid,
                            "internal_support_count": len(camp.get("support_refs", [])),
                            "internal_oppose_count": len(camp.get("oppose_refs", [])),
                            "internal_pressure": int(camp.get("pressure", 0)),
                        })
                    else:
                        outcome = start_strategic_operation(fid, intent)
                        refresh_commitments()
                        executed_actions.append({
                            "action": action, **outcome, "intent": str(intent.get("action")),
                            "target_faction_ref": target_fid,
                            "internal_support_count": len(camp.get("support_refs", [])),
                            "internal_oppose_count": len(camp.get("oppose_refs", [])),
                            "internal_pressure": int(camp.get("pressure", 0)),
                        })
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
                    refresh_commitments()
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
                personal_aid = personal_aid_target()
                personal_obligation_ref = personal_aid[1] if personal_aid else ""
                target_fid = personal_aid[0] if personal_aid else choose_friendly_aid_target(
                    relation_index.get(fid, []), faction_ref=fid, year=at.year, month=at.month,
                    cash_reserve_months=cash_reserve_months,
                )
                if target_fid:
                    camp=decision_consensus(target_fid, "aid")
                    if int(camp.get("pressure", 0)) <= -45 and len(camp.get("oppose_refs", [])) > len(camp.get("support_refs", [])):
                        outcome={"result": "internal_loyalty_conflict", "target_faction_ref": target_fid}
                    else:
                        outcome=execute_friendly_aid(fid, target_fid)
                        if outcome.get("result") == "aid_transferred" and personal_obligation_ref:
                            settled = resolve_personal_obligation(
                                social_after, obligation_ref_value=personal_obligation_ref,
                            )
                            if settled.get("resolved"):
                                social_after = settled["state_after"]
                                outcome = {**outcome, "personal_obligation_repaid_ref": personal_obligation_ref}
                    executed_actions.append({
                        "action": action, **outcome,
                        "internal_support_count": len(camp.get("support_refs", [])),
                        "internal_oppose_count": len(camp.get("oppose_refs", [])),
                        "internal_pressure": int(camp.get("pressure", 0)),
                    })
                else:
                    executed_actions.append({"action": action, "result": "no_friendly_aid_pressure"})
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
                admission = faction_admission_policy(fid, faction)
                minimum_entry_age = max(0, int(admission.get("minimum_entry_age", 8)))
                allowed_sexes = {str(value) for value in admission.get("allowed_sexes", []) if isinstance(value, str)}
                minimum_martial = max(0, int(policy.get("minimum_martial_aptitude", 0)))
                minimum_qi = max(0, int(policy.get("minimum_qi_aptitude", 0)))
                epoch_days = training_epoch_elapsed_days(faction)
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
                    # Exact independents remain physical people. A stale sparse
                    # location_ref must not let a traveler, combatant, or captive
                    # be recruited as though they were still locally available.
                    blocked_independent_refs = unavailable_person_refs()
                    eligible_independent: list[tuple[int, str, int, Mapping[str, Any]]] = []
                    for index, raw in enumerate(independent_rows):
                        if not isinstance(raw, Mapping) or not isinstance(raw.get("person_id"), str):
                            continue
                        if str(raw.get("person_id")) in blocked_independent_refs:
                            continue
                        if raw.get("retired_from_field"):
                            continue
                        health = raw.get("health", {}) if isinstance(raw.get("health"), Mapping) else {}
                        if health.get("status") == "dead":
                            continue
                        if not allows_independent_recruitment(raw, target_faction_ref=fid):
                            continue
                        age = max(0, at.year - int(raw.get("birth_year", at.year)))
                        if age < minimum_entry_age:
                            continue
                        raw_sex = str(raw.get("sex") or "")
                        if allowed_sexes and raw_sex not in allowed_sexes:
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
                        # Independent people are stored sparsely. In particular,
                        # fractional training carry can live in the compact
                        # ``training_carry_milli`` vector. Once the person joins a
                        # faction they immediately become eligible for same-frontier
                        # training settlement, duties, routes and other finite-body
                        # commitments. Hydrate at the ownership-transfer boundary so
                        # those mechanics see one logical training representation
                        # rather than layering ``training_state.residual_milli`` next
                        # to an older packed carry.
                        person = _hydrate_recruited_independent(
                            raw, faction_ref=fid, home_site=home_site,
                        )
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
                        admitted_refs.append(person_ref)
                    if chosen_indexes:
                        independent_state["people"] = [row for i, row in enumerate(independent_rows) if i not in chosen_indexes]
                        writes[_INDEPENDENTS_PATH] = independent_state
                        remaining -= len(chosen_indexes)
                pools = civilian_state.get("places", {}) if isinstance(civilian_state.get("places"), Mapping) else {}
                pool = pools.get(headquarters) if isinstance(pools, Mapping) else None
                if remaining > 0 and isinstance(pool, dict):
                    available_civilians = max(0, int(pool.get("current_population", 0)) - int(pool.get("reserved_for_recruitment", 0)))
                    cursor = max(0, int(pool.get("identity_ordinal_cursor", 0)))
                    accepted_candidates: list[Mapping[str, Any]] = []
                    limit = min(available_civilians, max(remaining, remaining * 12))
                    for offset in range(limit):
                        candidate = deterministic_candidate(
                            world_seed=world_seed, origin_population_id=headquarters, ordinal=cursor + offset,
                        )
                        examined += 1
                        if int(candidate.get("age", 0)) < minimum_entry_age:
                            continue
                        apt = candidate.get("aptitudes", {}) if isinstance(candidate.get("aptitudes"), Mapping) else {}
                        if int(apt.get("martial", 0)) < minimum_martial or int(apt.get("qi", 0)) < minimum_qi:
                            continue
                        accepted_candidates.append(candidate)
                        if len(accepted_candidates) >= remaining:
                            break
                    pool["identity_ordinal_cursor"] = cursor + examined
                    names = all_existing_names()
                    for candidate in accepted_candidates:
                        ordinal_seed = int(candidate.get("origin_ordinal", 0))
                        person_ref = "mw.recruit." + hashlib.sha256((world_seed + "|" + headquarters + "|" + str(ordinal_seed)).encode("utf-8")).hexdigest()[:24]
                        age = max(0, int(candidate.get("age", 0)))
                        sex = deterministic_sex(stable=person_ref, faction_id=fid, admission_policy=admission)
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
                executed_actions.append({
                    "action": action, "result": "recruited" if total_joined else "no_eligible_candidates",
                    "recruited_independent": admitted_refs, "recruited_external": external_refs, "examined": examined,
                })
                continue

        # Contract-capable factions may autonomously take at most one funded
        # escort job per monthly review.  The contract uses real people, source
        # cargo and derived personnel availability; no abstract escort force
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
                raw_objective = contract.get("objective", {}) if isinstance(contract.get("objective"), Mapping) else {}
                try:
                    objective = hydrate_contract_escort_objective(raw_objective, geography=geography, travel=travel_data)
                except (KeyError, TypeError, ValueError):
                    continue
                if objective.get("kind") != "escort_shipment":
                    continue
                route_ref = str(objective.get("route_ref") or "")
                route = route_index.get(route_ref)
                if not isinstance(route, Mapping):
                    continue
                origin = str(objective.get("source_place_ref") or "")
                destination = str(objective.get("destination_place_ref") or "")
                ends = {str(route.get("from") or ""), str(route.get("to") or "")}
                if not origin or not destination or origin == destination or {origin, destination} != ends or str(faction.get("headquarters", "")) != origin:
                    continue
                source_region = str(place_region.get(origin) or "")
                destination_region = str(place_region.get(destination) or "")
                if not source_region or not destination_region:
                    continue
                minimum = max(1, int(objective.get("minimum_escort_count", 1)))
                available_by_location: dict[str, list[dict[str, Any]]] = {}
                for person in usable_martial_people(review_roster, exclude_committed=unavailable_person_refs()):
                    ref = str(person.get("person_id", ""))
                    if not ref or ref == player_ref:
                        continue
                    exact_location = str(person.get("location_ref") or "")
                    site = sites.get(exact_location) if isinstance(sites, Mapping) else None
                    at_origin = exact_location == origin or (
                        isinstance(site, Mapping) and str(site.get("parent_place_ref") or "") == origin
                    )
                    if not at_origin:
                        continue
                    available_by_location.setdefault(exact_location, []).append(person)

                # Autonomous escort acceptance must also form one physical party.
                # A city-scale strategic origin does not make people in different
                # local sites co-located. Pick the strongest deterministic exact
                # muster group that can actually staff the contract.
                viable_groups: list[tuple[int, str, list[dict[str, Any]]]] = []
                for exact_location, rows in available_by_location.items():
                    rows.sort(key=lambda p: (-person_combat_index(p), str(p.get("person_id", ""))))
                    if len(rows) < minimum:
                        continue
                    strength = sum(person_combat_index(p) for p in rows[:minimum])
                    viable_groups.append((strength, exact_location, rows))
                if not viable_groups:
                    continue
                viable_groups.sort(key=lambda item: (-item[0], item[1]))
                _strength, muster_location, available = viable_groups[0]
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
                        refresh_commitments(),
                        resources=[("person", ref, fid) for ref in participants],
                        actor_ref=participants[0], owner_ref=fid, activity_ref=cid,
                        activity_kind="contract_escort", started_at=at_iso, location_ref=muster_location,
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
                freight_required = max(0, int(objective.get("freight_capacity_kg", 0)))
                crew_required = max(0, int(objective.get("civilian_crew_count", freight_crew_required(freight_required))))
                pools = civilian_state.get("places", {}) if isinstance(civilian_state.get("places"), Mapping) else {}
                origin_pool = pools.get(origin, {}) if isinstance(pools, Mapping) else {}
                population = max(0, int(origin_pool.get("current_population", 0))) if isinstance(origin_pool, Mapping) else 0
                available_logistics = civilian_available_capacity(
                    place_ref=origin, place_population=population, route_operations=route_ops_state,
                )
                if freight_required > int(available_logistics.get("freight_capacity_kg", 0)) or crew_required > int(available_logistics.get("crew_capacity", 0)):
                    continue
                try:
                    route_plan = travel_plan(
                        world_seed=world_seed, start_at=at, start=origin, end=destination, mode="convoy",
                    )
                    inventory, provision_reservation = reserve_faction_rations(
                        inventory, faction_ref=fid, participant_count=len(participants),
                        travel_seconds=provisioning_journey_seconds(route_plan),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                movement = build_route_journey(
                    movement_ref=cid, movement_kind="escort_contract", purpose_ref=cid,
                    plan=route_plan, participants=participants, leader_ref=participants[0],
                    beneficiary_ref=fid, started_at=at, mode="convoy",
                    extra={
                        "contract_ref": cid, "item_ref": item_ref, "quantity": quantity,
                        "escort_refs": participants, "protected_person_refs": [],
                        "provision_reservation": provision_reservation,
                        "transport_reservation": make_transport_reservation(
                            provider_kind="civilian_logistics", provider_ref=origin,
                            freight_capacity_kg=freight_required, crew_capacity=crew_required,
                        ),
                    },
                )
                started["objective"] = compact_started_escort_objective(objective)
                active_after[cid] = started
                active_contracts = active_after
                contract_index = contract_after
                movements[cid] = movement
                publish_commitments(next_commitments)
                pause_people_for_commitment(fid, participants)
                writes[_CONTRACT_INDEX_PATH] = contract_after
                writes[_ROUTE_OPERATIONS_PATH] = route_ops_state
                writes[ipath] = inventory
                inventory_cache[fid] = (ipath, inventory)
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
        routine_work = derive_duty_assignments(
            faction, [p for p in review_people if isinstance(p, Mapping)],
            year=at.year, month=at.month, unavailable_refs=sorted(blocked_service_refs),
            protected_refs=([player_ref] if player_ref else ["pc_wei_tang"]),
        )
        routine_assignments = routine_work.get("assignments", {})
        trade_service_people = [
            p for p in review_people
            if isinstance(p, Mapping)
            and routine_assignments.get(str(p.get("person_id") or "")) == "trade_service"
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
        if resolved_faction_type(faction) == "brotherhood_society" and active_enterprise_count == 0 and isinstance(region, str):
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

        # Agriculture is an owner-level enterprise, not a collection of virtual
        # crop entities. Managed land produces a deterministic monthly flow.
        agriculture_level = max(0, int(enterprises.get("agriculture_landholding", 0)))
        managed_land_mu = enterprise_scale_value(faction, "agriculture_landholding") if agriculture_level > 0 else 0
        if agriculture_level > 0 and managed_land_mu > 0 and isinstance(region, str):
            try:
                mpath, agriculture_market = load_market(region)
            except FileNotFoundError:
                agriculture_market = None; mpath = ""
            if isinstance(agriculture_market, dict):
                policy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
                reserve_months = max(1, min(6, int(policy.get("reserve_cash_months", 2))))
                reserve_cash = monthly_cash * reserve_months
                spendable = max(0, int(faction.get("treasury_cash", 0)) - reserve_cash)
                result = monthly_enterprise_settlement(
                    world_seed=world_seed, faction_ref=fid, at=at, managed_land_mu=managed_land_mu,
                    agriculture_level=agriculture_level, medicine_level=max(0, int(enterprises.get("medicine_apothecary", 0))),
                    available_cash=spendable, labor_cash_per_hour=general_labor_cash_per_hour,
                    operating_efficiency_milli=max(1, enterprise_operating_efficiency_milli("agriculture_landholding", agriculture_level)),
                )
                spent=max(0,int(result.get("cash_spent",0)))
                food=max(0,int(result.get("food_ration_days",0))); herbs=max(0,int(result.get("herb_units",0)))
                if spent>0 or food>0 or herbs>0:
                    faction["treasury_cash"] = max(0, int(faction.get("treasury_cash",0)) - spent)
                    agriculture_market["cash_pool"] = max(0, int(agriculture_market.get("cash_pool",0))) + spent
                    ipath, agriculture_inventory = load_inventory(fid)
                    agriculture_inventory["food_ration_days"] = max(0,int(agriculture_inventory.get("food_ration_days",0))) + food
                    herb_ref=result.get("herb_ref")
                    if herbs>0 and isinstance(herb_ref,str) and herb_ref:
                        herb_stock=agriculture_inventory.setdefault("herbs",{})
                        if not isinstance(herb_stock,dict): raise ValueError("jianghu inventory herbs invalid")
                        herb_stock[herb_ref]=max(0,int(herb_stock.get(herb_ref,0)))+herbs
                    faction_cache[fid]=(fpath,faction); market_cache[region]=(mpath,agriculture_market); inventory_cache[fid]=(ipath,agriculture_inventory)
                    writes[fpath]=faction; writes[mpath]=agriculture_market; writes[ipath]=agriculture_inventory
                executed_actions.append({
                    "action":"operate_agriculture","result":"settled","managed_land_mu":managed_land_mu,
                    "operated_land_mu":int(result.get("operated_land_mu",0)),"cash_spent":spent,
                    "food_ration_days":food,"herb_ref":result.get("herb_ref"),"herb_units":herbs,
                })

        # Workshops and apothecaries are real operating enterprises.  Monthly
        # settlement consumes actual worker duty time, stations and physical
        # inputs; produced stock may be sold only into finite regional demand.
        if isinstance(region, str):
            blocked_ops = unavailable_person_refs()
            workshop_level = max(0, int(enterprises.get("crafting_workshop", 0)))
            if workshop_level > 0:
                workers = [
                    p for p in review_people if isinstance(p, Mapping)
                    and routine_assignments.get(str(p.get("person_id") or "")) == "workshop_service"
                    and isinstance(p.get("person_id"), str) and p.get("person_id") not in blocked_ops
                ]
                workers.sort(key=lambda p: (-int((p.get("professional_skills") or {}).get("crafting", 0)), str(p.get("person_id"))))
                physical = workshop_capacity(buildings, faction.get("infrastructure", {}))
                scale_stations = enterprise_scale_value(faction, "crafting_workshop")
                stations = min(max(0, int(physical.get("craft_workstations", 0))), max(0, scale_stations))
                active_workers = workers[:stations]
                if active_workers:
                    best_skill = max(int((p.get("professional_skills") or {}).get("crafting", 0)) for p in active_workers)
                    # Workshop doctrine follows the current institution rather
                    # than a static seed profile, including future founded factions.
                    training = faction.get("training", {}) if isinstance(faction.get("training"), Mapping) else {}
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
                    and routine_assignments.get(str(p.get("person_id") or "")) == "infirmary_service"
                    and isinstance(p.get("person_id"), str) and p.get("person_id") not in blocked_ops
                ]
                med_workers.sort(key=lambda p: (-int((p.get("professional_skills") or {}).get("medicine", 0)), str(p.get("person_id"))))
                inf = infirmary_capacity(buildings, faction.get("infrastructure", {}))
                stations = min(max(0,int(inf.get("apothecary_workstations",0))), max(0,enterprise_scale_value(faction,"medicine_apothecary")))
                med_active=med_workers[:stations]
                if med_active:
                    med_skill=max(int((p.get("professional_skills") or {}).get("medicine",0)) for p in med_active)
                    available_apothecary_hours=len(med_active)*105
                    identity=faction_presentation_identity(fid,faction)
                    apothecary_policy=identity.get("apothecary_policy",{}) if isinstance(identity,Mapping) else {}
                    poison_targets=apothecary_policy.get("poison_reserve_targets",{}) if isinstance(apothecary_policy,Mapping) else {}
                    if isinstance(poison_targets,Mapping) and poison_targets and available_apothecary_hours>0:
                        poison_stock=inventory.get("poisons",{}) if isinstance(inventory.get("poisons"),Mapping) else {}
                        deficits=[]
                        for pref,raw_target in poison_targets.items():
                            target=max(0,int(raw_target)); current=max(0,int(poison_stock.get(f"poison_{pref}",0)))
                            if target>current: deficits.append((target-current,str(pref),target))
                        if deficits:
                            _deficit,poison_recipe,poison_target=max(deficits,key=lambda row:(row[0],row[1]))
                            try:
                                pop=operate_poison_apothecary_month(
                                    inventory,recipe_ref=poison_recipe,apothecary_level=medicine_level,medicine_skill=med_skill,
                                    available_worker_hours=available_apothecary_hours,reserve_doses=poison_target,max_batches=max(1,stations),
                                )
                            except (KeyError,ValueError):
                                pop={"batches":0,"reason":"inputs_or_recipe","labor_hours":0}
                            if int(pop.get("batches",0))>0:
                                inventory=pop["inventory"]; available_apothecary_hours=max(0,available_apothecary_hours-int(pop.get("labor_hours",0)))
                                inventory_cache[fid]=(ipath,inventory); writes[ipath]=inventory
                                executed_actions.append({"action":"operate_poison_apothecary",**{k:pop.get(k) for k in ("recipe_ref","batches","produced","labor_hours","output_item")}})
                    candidates=[("stamina_tonic",1,35),("pain_tonic",1,35),("blood_tonic",2,55),("wound_salve",2,55),("detox_medicine",2,55),("bone_medicine",3,80),("internal_injury_medicine",3,80),("nerve_antidote",3,80),("blood_cardiac_antidote",3,80)]
                    meds=inventory.get("medicines",{}) if isinstance(inventory.get("medicines"),Mapping) else {}
                    allowed=[ref for ref,lvl,skill in candidates if medicine_level>=lvl and med_skill>=skill]
                    allowed.sort(key=lambda ref:(int(meds.get(ref,0)),ref))
                    if allowed and available_apothecary_hours>0:
                        try:
                            mpath, apoth_market=load_market(region)
                            recipe_ref=allowed[0]
                            op=operate_apothecary_month(
                                inventory,apoth_market,recipe_ref=recipe_ref,apothecary_level=medicine_level,medicine_skill=med_skill,
                                available_worker_hours=available_apothecary_hours,reserve_doses=max(5,int(faction.get("population",0))//10),max_batches=max(1,stations),
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
            refresh_commitments()
            if merchant_trade.get("result") == "merchant_trade_started":
                # The route callback owns the conserved purchase/toll transfer,
                # provision reservation and participant pause.  Those callback
                # paths may replace cache objects while this monthly review still
                # holds older locals.  Reload every owner the callback can change
                # before later lifecycle work and the final faction write, or a
                # stale pre-trade object can resurrect debited silver/provisions.
                fpath, faction = load_faction(fid)
                ipath, inventory = load_inventory(fid)
                _rpath, review_roster = load_roster(fid)
                review_people = review_roster.get("people", []) if isinstance(review_roster, Mapping) else []
            executed_actions.append({"action": "operate_trade_merchant_business", **merchant_trade})

        if autonomous_contract:
            _rpath, review_roster = load_roster(fid)
        infirmary = infirmary_capacity(buildings, faction.get("infrastructure", {}))
        clinical = apply_autonomous_clinical_treatment(
            faction, review_roster, inventory, at_iso=at_iso,
            unavailable_refs=sorted(unavailable_person_refs()),
            treatment_stations=max(0, int(infirmary.get("treatment_stations", 0))),
            prepare_patient=prepare_patient_for_treatment,
        )
        review_roster = clinical["roster"]
        if clinical["inventory"] != inventory:
            inventory = clinical["inventory"]
            writes[ipath] = inventory
            inventory_cache[fid] = (ipath, inventory)
        if clinical["treated_refs"]:
            for ref, carry in (clinical.get("physiology_rebases", {}) or {}).items():
                if isinstance(ref, str) and isinstance(carry, Mapping):
                    clinical_physiology_rebases[ref] = dict(carry)
            writes[_rpath] = review_roster
            roster_cache[fid] = (_rpath, review_roster)
            executed_actions.append({"action": "clinical_treatment", "count": len(clinical["treated_refs"]), "doses_used": int(clinical["doses_used"])})
        lifecycle = advance_institution(
            faction, review_roster, year=at.year, month=at.month, social=social_after, player_ref=player_ref or None,
            unavailable_refs=sorted(unavailable_person_refs()),
            infirmary_beds=max(0, int(infirmary.get("beds", 0))),
        )
        lifecycle_roster = lifecycle["roster"]
        lifecycle_summary = lifecycle["summary"]
        newly_dead_refs.update(
            str(ref) for ref in lifecycle_summary.get("died_refs", [])
            if isinstance(ref, str) and ref
        )
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
        if lifecycle_summary["recovered_refs"]:
            executed_actions.append({"action": "health_recovery", "count": len(lifecycle_summary["recovered_refs"])})
        if lifecycle_summary["appointments"]:
            executed_actions.append({"action": "office_appointment", "count": len(lifecycle_summary["appointments"])})
        if lifecycle_summary["promoted_refs"]:
            executed_actions.append({"action": "membership_promotion", "count": len(lifecycle_summary["promoted_refs"])})
        if autonomous_contract:
            executed_actions.append({"action": "evaluate_contracts", "result": "escort_started", **autonomous_contract})

        # Known live captivity is not a standing task row. It is derived from
        # the real custody owner each faction review, so an institution that was
        # initially exhausted can reconsider once people/provisions recover.
        for custody_record in [row for row in custody_state.get("records", []) if isinstance(row, Mapping)]:
            if custody_record.get("status") in {"released", "escaped", "rescued", "executed"}:
                continue
            informed = {
                str(x) for x in custody_record.get("informed_faction_refs", [])
                if isinstance(x, str) and x
            }
            if fid not in informed or fid == str(custody_record.get("holder_faction_ref") or ""):
                continue
            if fid == player_faction_ref:
                # The player's House may identify the duty, but it may not
                # autonomously commit the player's institution to a rescue.
                # Time progression converts this review into a hard assignment
                # offer so the player can accept, decline, discuss or delegate.
                executed_actions.append({
                    "action": "respond_known_captivity",
                    "person_ref": str(custody_record.get("person_ref") or ""),
                    "holder_faction_ref": str(custody_record.get("holder_faction_ref") or ""),
                    "custody_ref": str(custody_record.get("custody_id") or ""),
                    "result": "player_decision_required",
                })
                continue
            response = start_custody_rescue_operation(fid, custody_record)
            refresh_commitments()
            executed_actions.append({
                "action": "respond_known_captivity",
                "person_ref": str(custody_record.get("person_ref") or ""),
                "result": str(response.get("result") or "response_deferred"),
                **({"operation_ref": str(response.get("operation_ref"))} if response.get("operation_ref") else {}),
            })

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

    refresh_commitments()
    return {
        "active_contracts": active_contracts,
        "contract_index": contract_index,
        "commitments_state": commitments_state,
        "social_state": social_after,
        "newly_dead_refs": sorted(newly_dead_refs),
        "clinical_physiology_rebases": clinical_physiology_rebases,
    }


__all__ = ["_hydrate_recruited_independent", "settle_faction_autonomy_frontier"]
