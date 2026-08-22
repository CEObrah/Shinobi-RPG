"""Physical cargo, person and mixed escort planning and monthly demand.

Escort contracts protect a real movement, not an abstract percentage of regional
wealth. Aggregate markets may request ordinary cargo lots or aggregate civilian
travel parties. Named persistent principals are supported by exact person refs,
but ordinary civilian parties remain aggregate unless identity materially
matters. There are no fictional maximums on convoy or escort size.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .contracts import escort_quote, funded_contract_offer
from .regional_economy import trade_shipment_opportunities, unit_market_price_cash
from .scheduler import upsert_one_off_event

_ROOT = Path(__file__).resolve().parents[3]
_MW = _ROOT / "game" / "data" / "martial-world"
_CONTRACTS = "state/martial-world/contracts/index.json"
_SCHEDULER = "state/martial-world/scheduler.json"
_CIVILIANS = "state/martial-world/civilian-populations.json"
_GEOGRAPHY = "game/data/martial-world/geography.json"
_TRAVEL = "game/data/martial-world/travel.json"
_REGIONAL = "game/data/martial-world/regional-economy.json"


def _policy() -> Mapping[str, Any]:
    data = json.loads((_MW / "contracts.json").read_text(encoding="utf-8"))
    row = data.get("finite_types", {}).get("escort", {})
    if not isinstance(row, Mapping):
        raise ValueError("escort contract policy invalid")
    return row


def _stable_int(*parts: object) -> int:
    raw = "|".join(str(x) for x in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def cargo_unit_mass_grams(item_ref: str) -> int:
    value = _policy().get("cargo_unit_mass_grams", {}).get(item_ref)
    if value is None:
        raise KeyError(item_ref)
    return max(1, int(value))


def cargo_mass_kg(item_ref: str, quantity: int) -> int:
    grams = cargo_unit_mass_grams(item_ref) * max(0, int(quantity))
    return (grams + 999) // 1000


def ordinary_public_lot_quantity(item_ref: str, aggregate_quantity: int) -> int:
    """Convert aggregate shortage demand into one normal public shipment lot.

    The target is a publication norm, not a physical maximum. Explicit larger
    commissions remain valid and simply require more transport and security.
    """
    requested = max(0, int(aggregate_quantity))
    if requested <= 0:
        return 0
    target_kg = max(1, int(_policy().get("standard_public_convoy_target_payload_kg", 12000)))
    per_unit = cargo_unit_mass_grams(item_ref)
    target_quantity = max(1, target_kg * 1000 // per_unit)
    return min(requested, target_quantity)


def route_travel_hours(route: Mapping[str, Any], travel: Mapping[str, Any]) -> int:
    speed = max(1.0, float(travel.get("mode_speed_km_per_day", {}).get("convoy", 24)))
    terrain = int(travel.get("terrain_time_milli", {}).get(str(route.get("terrain", "plain")), 1000))
    road = int(travel.get("road_time_milli", {}).get(str(route.get("road_quality", "maintained")), 1000))
    hours = (
        float(route.get("distance_km", 0)) * 24.0 / speed
        * terrain * road / 1_000_000.0
        + float(route.get("fixed_delay_hours", 0))
    )
    return max(1, int(hours + 0.999999))


def route_transport_plan(*, cargo_kg: int, route: Mapping[str, Any]) -> dict[str, int | str]:
    cargo = max(0, int(cargo_kg))
    if cargo <= 0:
        return {
            "transport_mode": "travel_party",
            "wagon_count": 0,
            "pack_animal_count": 0,
            "draft_animal_count": 0,
            "civilian_crew_count": 0,
        }
    policy = _policy()
    profiles = policy.get("transport_profiles", {}) if isinstance(policy.get("transport_profiles"), Mapping) else {}
    road_payload = policy.get("road_payload_milli", {}) if isinstance(policy.get("road_payload_milli"), Mapping) else {}
    allowed = {str(x) for x in route.get("allowed_modes", []) if isinstance(x, str)} if isinstance(route.get("allowed_modes"), list) else set()
    road_quality = str(route.get("road_quality") or "maintained")
    road_milli = max(0, int(road_payload.get(road_quality, 1000)))
    wagon = profiles.get("wagon", {}) if isinstance(profiles.get("wagon"), Mapping) else {}
    pack = profiles.get("pack", {}) if isinstance(profiles.get("pack"), Mapping) else {}
    if "convoy" in allowed and road_milli > 0:
        payload = max(1, max(1, int(wagon.get("payload_kg", 1200))) * road_milli // 1000)
        wagons = (cargo + payload - 1) // payload
        draft = wagons * max(0, int(wagon.get("draft_animals_per_vehicle", 2)))
        drivers = wagons * max(0, int(wagon.get("drivers_per_vehicle", 1)))
        group = max(1, int(wagon.get("handlers_per_vehicles", 4)))
        handlers = (wagons + group - 1) // group
        return {
            "transport_mode": "wagon_convoy",
            "wagon_count": wagons,
            "pack_animal_count": 0,
            "draft_animal_count": draft,
            "civilian_crew_count": drivers + handlers,
        }
    payload = max(1, int(pack.get("payload_kg", 120)))
    animals = (cargo + payload - 1) // payload
    group = max(1, int(pack.get("handlers_per_animals", 3)))
    handlers = (animals + group - 1) // group
    return {
        "transport_mode": "pack_train",
        "wagon_count": 0,
        "pack_animal_count": animals,
        "draft_animal_count": 0,
        "civilian_crew_count": handlers,
    }


def minimum_martial_escorts(
    *, transport: Mapping[str, Any], protected_people: int,
    distance_km_tenths: int, terrain: str, threat_score: int,
) -> int:
    """Derive security manpower without a maximum-count shortcut."""
    p = _policy().get("escort_count_policy", {})
    if not isinstance(p, Mapping):
        p = {}
    total = max(1, int(p.get("base_martial_escorts", 2)))
    wagons = max(0, int(transport.get("wagon_count", 0)))
    packs = max(0, int(transport.get("pack_animal_count", 0)))
    protected = max(0, int(protected_people))
    km = (max(0, int(distance_km_tenths)) + 9) // 10
    if wagons:
        step = max(1, int(p.get("wagons_per_extra_escort", 4)))
        total += (wagons + step - 1) // step
    if packs:
        step = max(1, int(p.get("pack_animals_per_extra_escort", 12)))
        total += (packs + step - 1) // step
    if protected > 1:
        step = max(1, int(p.get("protected_people_per_extra_escort", 4)))
        total += (protected - 1) // step
    if km:
        total += km // max(1, int(p.get("distance_km_per_extra_escort", 300)))
    total += max(0, int(threat_score)) // max(1, int(p.get("threat_score_per_extra_escort", 25)))
    terrain_extra = p.get("terrain_extra", {}) if isinstance(p.get("terrain_extra"), Mapping) else {}
    total += max(0, int(terrain_extra.get(str(terrain), 0)))
    return max(1, total)


def route_threat_estimate(route: Mapping[str, Any]) -> int:
    terrain = {
        "plain": 10, "river_plain": 10, "hills": 18, "forest": 22,
        "marsh": 24, "mountain": 30, "highland": 30, "desert": 28,
    }.get(str(route.get("terrain") or "plain"), 15)
    distance_tenths = max(0, int(round(float(route.get("distance_km", 0)) * 10)))
    road = {"imperial_road": -4, "maintained": 0, "rough": 6, "trail": 10, "mountain_path": 12}.get(str(route.get("road_quality") or "maintained"), 0)
    return max(0, min(100, terrain + distance_tenths // 250 + road))


def plan_escort_objective(
    *, kind: str, route: Mapping[str, Any], travel: Mapping[str, Any],
    source_region: str, destination_region: str,
    item_ref: str = "", quantity: int = 0, cargo_value_cash: int = 0,
    protected_person_refs: Sequence[str] = (), protected_people_count: int = 0,
    civilian_party_kind: str | None = None,
) -> dict[str, Any]:
    if kind not in {"escort_shipment", "escort_person", "escort_party", "escort_mixed_convoy"}:
        raise ValueError("unsupported escort objective kind")
    refs = []
    for ref in protected_person_refs:
        if isinstance(ref, str) and ref and ref not in refs:
            refs.append(ref)
    protected = max(len(refs), max(0, int(protected_people_count)))
    qty = max(0, int(quantity))
    cargo_kg = cargo_mass_kg(item_ref, qty) if item_ref and qty > 0 else 0
    if kind == "escort_shipment" and cargo_kg <= 0:
        raise ValueError("shipment escort requires cargo")
    if kind in {"escort_person", "escort_party"} and protected <= 0:
        raise ValueError("person escort requires protected people")
    if kind == "escort_mixed_convoy" and (cargo_kg <= 0 or protected <= 0):
        raise ValueError("mixed escort requires cargo and protected people")
    transport = route_transport_plan(cargo_kg=cargo_kg, route=route)
    distance = max(0, int(round(float(route.get("distance_km", 0)) * 10)))
    threat = route_threat_estimate(route)
    minimum = minimum_martial_escorts(
        transport=transport, protected_people=protected,
        distance_km_tenths=distance, terrain=str(route.get("terrain") or "plain"),
        threat_score=threat,
    )
    escort_kind = "cargo" if kind == "escort_shipment" else ("mixed" if kind == "escort_mixed_convoy" else "person")
    out: dict[str, Any] = {
        "kind": kind,
        "escort_policy_version": 3,
        "escort_kind": escort_kind,
        "route_ref": str(route.get("id") or ""),
        "source_region": source_region,
        "destination_region": destination_region,
        "distance_km_tenths": distance,
        "expected_travel_hours": route_travel_hours(route, travel),
        "terrain": str(route.get("terrain") or "plain"),
        "road_quality": str(route.get("road_quality") or ""),
        "estimated_toll_cash": max(0, int(route.get("toll_cash", 0))),
        "item_ref": item_ref or None,
        "quantity": qty,
        "cargo_mass_kg": cargo_kg,
        "cargo_value_cash": max(0, int(cargo_value_cash)),
        "protected_person_refs": refs,
        "protected_people_count": protected,
        "minimum_escort_count": minimum,
        "threat_score": threat,
        **transport,
    }
    if civilian_party_kind:
        out["civilian_party_kind"] = str(civilian_party_kind)
    return out


def quote_escort_objective(objective: Mapping[str, Any]) -> dict[str, int]:
    return escort_quote(
        distance_km_tenths=max(0, int(objective.get("distance_km_tenths", 0))),
        cargo_value_cash=max(0, int(objective.get("cargo_value_cash", 0))),
        threat_score=max(0, int(objective.get("threat_score", 0))),
        escort_count=max(1, int(objective.get("minimum_escort_count", 1))),
        normal_travel_hours=max(1, int(objective.get("expected_travel_hours", 1))),
        deadline_hours=max(1, int(objective.get("deadline_hours", objective.get("expected_travel_hours", 1)))),
    )


def active_retinue_party(deployments: Mapping[str, Any], *, leader_ref: str, principals: Sequence[str]) -> list[str]:
    refs: list[str] = []
    for ref in principals:
        if isinstance(ref, str) and ref and ref not in refs:
            refs.append(ref)
    rows = deployments.get("deployments", {}) if isinstance(deployments, Mapping) else {}
    if isinstance(rows, Mapping):
        for key in sorted(str(x) for x in rows if isinstance(x, str)):
            row = rows.get(key)
            if not isinstance(row, Mapping):
                continue
            if row.get("operation_kind") != "standing_retinue" or row.get("status") != "active" or row.get("leader_ref") != leader_ref:
                continue
            for ref in row.get("member_refs", []) if isinstance(row.get("member_refs"), list) else []:
                if isinstance(ref, str) and ref and ref not in refs:
                    refs.append(ref)
    return refs


def _route_index(geography: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = geography.get("routes", []) if isinstance(geography, Mapping) else []
    return {str(row.get("id")): row for row in rows if isinstance(row, Mapping) and isinstance(row.get("id"), str)} if isinstance(rows, list) else {}


def _place_regions(geography: Mapping[str, Any]) -> dict[str, str]:
    places = geography.get("places", {}) if isinstance(geography, Mapping) else {}
    if not isinstance(places, Mapping):
        return {}
    return {str(ref): str(row.get("climate_profile")) for ref, row in places.items() if isinstance(ref, str) and isinstance(row, Mapping) and isinstance(row.get("climate_profile"), str)}


def _market_path(region: str) -> str:
    return f"state/martial-world/markets/{region}.json"


def _party_demand(
    *, geography: Mapping[str, Any], civilian_state: Mapping[str, Any], at: datetime,
) -> list[dict[str, Any]]:
    """Create aggregate paid-protection demand from real civilian populations.

    Most civilian travel does not hire martial escorts. The monthly incidence
    rises with population but remains probabilistic and deterministic, preventing
    every mapped route from turning into a paid contract board entry. No named
    person or population is moved at offer time.
    """
    regions = _place_regions(geography)
    places = civilian_state.get("places", {}) if isinstance(civilian_state, Mapping) else {}
    routes = geography.get("routes", []) if isinstance(geography, Mapping) else []
    if not isinstance(places, Mapping) or not isinstance(routes, list):
        return []
    kinds = (
        "merchant_and_family", "pilgrims", "scholars_and_attendants",
        "physician_and_attendants", "wedding_procession", "official_envoy_party",
        "civilian_travelers",
    )
    out: list[dict[str, Any]] = []
    for route in sorted((r for r in routes if isinstance(r, Mapping)), key=lambda r: str(r.get("id") or "")):
        a = str(route.get("from") or ""); b = str(route.get("to") or "")
        if not a or not b or a not in places or b not in places:
            continue
        for source, destination in ((a, b), (b, a)):
            pop = max(0, int((places.get(source) or {}).get("current_population", 0))) if isinstance(places.get(source), Mapping) else 0
            if pop < 500:
                continue
            roll = _stable_int("escort-party", route.get("id"), source, destination, at.year, at.month)
            incidence_permille = min(220, max(20, pop // 500))
            if roll % 1000 >= incidence_permille:
                continue
            party_kind = kinds[(roll // 7) % len(kinds)]
            if party_kind == "wedding_procession":
                party_size = 8 + roll % 13
            elif party_kind == "pilgrims":
                party_size = 6 + roll % 15
            elif party_kind == "official_envoy_party":
                party_size = 3 + roll % 6
            else:
                party_size = 3 + min(12, max(0, pop // 30000)) + roll % 6
            out.append({
                "route_id": str(route.get("id") or ""),
                "source_place": source,
                "destination_place": destination,
                "source_region": regions.get(source, ""),
                "destination_region": regions.get(destination, ""),
                "protected_people_count": party_size,
                "civilian_party_kind": party_kind,
            })
    return out


def settle_monthly_escort_demand(
    *, read_json: Callable[[str], Mapping[str, Any]], events: Sequence[Mapping[str, Any]],
    at: datetime, schedule_after: Mapping[str, Any],
) -> dict[str, Any]:
    """Publish funded physical escort offers for due monthly demand reviews."""
    due = [e for e in events if isinstance(e, Mapping) and e.get("kind") == "trade_demand_review"]
    if not due:
        return {"writes": {}, "reviews": [], "schedule_after": copy.deepcopy(dict(schedule_after))}
    geography = read_json(_GEOGRAPHY); travel = read_json(_TRAVEL)
    route_rows = geography.get("routes", []) if isinstance(geography, Mapping) else []
    route_rows = [r for r in route_rows if isinstance(r, Mapping)] if isinstance(route_rows, list) else []
    route_index = _route_index(geography); place_regions = _place_regions(geography)
    regional = read_json(_REGIONAL)
    region_rows = regional.get("regions", {}) if isinstance(regional, Mapping) else {}
    market_states: dict[str, Mapping[str, Any]] = {}
    for region in sorted(str(x) for x in region_rows if isinstance(x, str)):
        try:
            market_states[region] = read_json(_market_path(region))
        except FileNotFoundError:
            continue
    opportunities = trade_shipment_opportunities(
        market_states=market_states, route_rows=route_rows, place_to_region=place_regions,
    )
    try:
        civilian_state = read_json(_CIVILIANS)
    except FileNotFoundError:
        civilian_state = {"places": {}}
    party_rows = _party_demand(geography=geography, civilian_state=civilian_state, at=at)
    contract_index = copy.deepcopy(dict(read_json(_CONTRACTS)))
    active = contract_index.setdefault("active", {})
    if not isinstance(active, dict):
        raise ValueError("jianghu contract index invalid")
    existing_sources = {str(row.get("source_ref")) for row in active.values() if isinstance(row, Mapping)}
    writes: dict[str, Any] = {}
    created: list[str] = []
    schedule = copy.deepcopy(dict(schedule_after))
    at_iso = at.isoformat()

    def fund_market_offer(*, source_region: str, source_ref: str, objective: Mapping[str, Any]) -> None:
        nonlocal schedule
        if not source_region or source_ref in existing_sources:
            return
        path = _market_path(source_region)
        market = copy.deepcopy(dict(writes.get(path, read_json(path))))
        quote = quote_escort_objective(objective)
        reward = max(0, int(quote.get("total_reward_cash", 0)))
        if reward <= 0 or max(0, int(market.get("cash_pool", 0))) < reward:
            return
        funded = funded_contract_offer(
            issuer_cash=max(0, int(market.get("cash_pool", 0))),
            contract_type="escort", issuer_ref=f"market:{source_region}", beneficiary_ref=None,
            offered_at=at_iso, expires_at=(at.replace(microsecond=0) + timedelta(days=30)).isoformat(),
            reward_cash=reward, objective=objective, source_ref=source_ref,
        )
        contract = dict(funded["contract"])
        active[contract["contract_id"]] = contract
        market["cash_pool"] = int(funded["issuer_cash_after"])
        writes[path] = market
        created.append(str(contract["contract_id"])); existing_sources.add(source_ref)
        schedule = upsert_one_off_event(schedule, {
            "event_id": f"contract_expiry_due:{contract['contract_id']}",
            "kind": "contract_expiry_due", "due_at": str(contract["expires_at"]),
            "owner_ref": str(contract["contract_id"]), "requires_player_decision": False,
        })

    for opp in opportunities:
        src = str(opp.get("source_region") or ""); dst = str(opp.get("destination_region") or "")
        route_ref = str(opp.get("route_id") or ""); item_ref = str(opp.get("item_ref") or "")
        route = route_index.get(route_ref)
        if not src or not dst or not item_ref or not isinstance(route, Mapping):
            continue
        aggregate_qty = max(0, int(opp.get("quantity", 0)))
        qty = ordinary_public_lot_quantity(item_ref, aggregate_qty)
        if qty <= 0:
            continue
        stock = market_states.get(src, {}).get("stock", {}) if isinstance(market_states.get(src), Mapping) else {}
        try:
            unit = unit_market_price_cash(src, item_ref, stock if isinstance(stock, Mapping) else {})
        except (KeyError, TypeError, ValueError):
            continue
        roll = _stable_int("escort-mixed", route_ref, src, dst, item_ref, at.year, at.month)
        protected = 2 + roll % 5 if roll % 4 == 0 else 0
        kind = "escort_mixed_convoy" if protected else "escort_shipment"
        objective = plan_escort_objective(
            kind=kind, route=route, travel=travel, source_region=src, destination_region=dst,
            item_ref=item_ref, quantity=qty, cargo_value_cash=unit * qty,
            protected_people_count=protected,
            civilian_party_kind="merchant_principals" if protected else None,
        )
        source_ref = f"trade:{route_ref}:{src}:{dst}:{item_ref}:{at.date().isoformat()}"
        fund_market_offer(source_region=src, source_ref=source_ref, objective=objective)

    for party in party_rows:
        route_ref = str(party.get("route_id") or ""); route = route_index.get(route_ref)
        src = str(party.get("source_region") or ""); dst = str(party.get("destination_region") or "")
        if not src or not dst or not isinstance(route, Mapping):
            continue
        objective = plan_escort_objective(
            kind="escort_party", route=route, travel=travel,
            source_region=src, destination_region=dst,
            protected_people_count=max(1, int(party.get("protected_people_count", 1))),
            civilian_party_kind=str(party.get("civilian_party_kind") or "civilian_travelers"),
        )
        objective["source_place_ref"] = str(party.get("source_place") or "")
        objective["destination_place_ref"] = str(party.get("destination_place") or "")
        source_ref = f"travel-party:{route_ref}:{party.get('source_place')}:{party.get('destination_place')}:{at.date().isoformat()}"
        fund_market_offer(source_region=src, source_ref=source_ref, objective=objective)

    if created:
        writes[_CONTRACTS] = contract_index
    writes[_SCHEDULER] = schedule
    return {
        "writes": writes,
        "schedule_after": schedule,
        "reviews": [{
            "kind": "trade_demand_review",
            "aggregate_shipment_opportunity_count": len(opportunities),
            "civilian_party_opportunity_count": len(party_rows),
            "funded_contracts_created": created,
        }],
    }


__all__ = [
    "active_retinue_party", "cargo_mass_kg", "cargo_unit_mass_grams",
    "minimum_martial_escorts", "ordinary_public_lot_quantity",
    "plan_escort_objective", "quote_escort_objective", "route_transport_plan",
    "route_travel_hours", "route_threat_estimate", "settle_monthly_escort_demand",
]
