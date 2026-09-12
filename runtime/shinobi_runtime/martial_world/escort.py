"""Physical cargo, person and mixed escort planning and monthly demand.

Escort contracts protect a real movement, not an abstract percentage of regional
wealth. Aggregate markets may request ordinary cargo lots or aggregate civilian
travel parties. Named persistent principals are supported by exact person refs,
and ordinary civilian parties are materialized into exact civic people when a
mission starts, because travel/combat can physically affect them. There are no
fictional maximums on convoy or escort size.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from functools import lru_cache
from typing import Any, Callable, Mapping, Sequence

from .contracts import escort_quote, funded_contract_offer
from .calendar_modifiers import escort_demand_milli
from .regional_economy import trade_shipment_opportunities, unit_market_price_cash
from .scheduler import upsert_one_off_event
from .recruitment import deterministic_candidate
from .people import apply_age_development, deterministic_body_mass_kg, deterministic_name, deterministic_sex
from .civic import compact_civic_person

_ROOT = Path(__file__).resolve().parents[3]
_MW = _ROOT / "game" / "data" / "martial-world"
_CONTRACTS = "state/martial-world/contracts/index.json"
_SCHEDULER = "state/martial-world/scheduler.json"
_CIVILIANS = "state/martial-world/civilian-populations.json"
_GEOGRAPHY = "game/data/martial-world/geography.json"
_TRAVEL = "game/data/martial-world/travel.json"
_REGIONAL = "game/data/martial-world/regional-economy.json"


@lru_cache(maxsize=1)
def _policy() -> Mapping[str, Any]:
    data = json.loads((_MW / "contracts.json").read_text(encoding="utf-8"))
    row = data.get("finite_types", {}).get("escort", {})
    if not isinstance(row, Mapping):
        raise ValueError("escort contract policy invalid")
    return row


def _stable_int(*parts: object) -> int:
    raw = "|".join(str(x) for x in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")




def materialize_civilian_identities(
    civilian_state: Mapping[str, Any], civic_state: Mapping[str, Any], *,
    world_seed: str, source_place_ref: str, count: int, current_year: int,
    civilian_party_kind: str | None = None,
) -> dict[str, Any]:
    """Promote aggregate civilians to exact civic identities when bodies matter.

    Aggregate population is efficient while nobody needs to address a particular
    body. Risky travel, consequential witnessing, recruitment, custody, or other
    exact interaction makes a civilian independently actable, so that body earns a
    persistent identity at the causal boundary. The aggregate pool keeps only one
    deterministic identity cursor shared by every materialization path.
    """
    need = max(0, int(count))
    civilians = copy.deepcopy(dict(civilian_state))
    civic = copy.deepcopy(dict(civic_state))
    if need == 0:
        return {"civilian_state": civilians, "civic_state": civic, "person_refs": []}
    places = civilians.get("places", {})
    pool = places.get(source_place_ref) if isinstance(places, Mapping) else None
    if not isinstance(pool, dict):
        raise ValueError("jianghu civilian pool unresolved")
    available = max(0, int(pool.get("current_population", 0)) - int(pool.get("reserved_for_recruitment", 0)))
    if available < need:
        raise ValueError("jianghu civilian pool insufficient")
    rows = civic.get("people")
    if civic.get("schema") != "jianghu-civic-people-state-1.0" or not isinstance(rows, list):
        raise ValueError("jianghu civic people state invalid")
    cursor = max(0, int(pool.get("identity_ordinal_cursor", 0)))
    existing_ids = {str(row.get("person_id")) for row in rows if isinstance(row, Mapping) and isinstance(row.get("person_id"), str)}
    existing_names = {str(row.get("name")) for row in rows if isinstance(row, Mapping) and isinstance(row.get("name"), str)}
    refs: list[str] = []
    background = "merchant_caravan" if civilian_party_kind == "merchant_principals" else "civilian_common"
    for offset in range(need):
        ordinal = cursor + offset
        candidate = deterministic_candidate(
            world_seed=world_seed, origin_population_id=source_place_ref, ordinal=ordinal, background=background,
        )
        pid = "civic.person." + hashlib.sha256(
            f"{world_seed}|{source_place_ref}|{ordinal}".encode("utf-8")
        ).hexdigest()[:24]
        if pid in existing_ids:
            raise ValueError("jianghu civic identity conflict")
        age = max(0, int(candidate.get("age", 0)))
        sex = deterministic_sex(stable=pid, faction_id="civilian")
        name = ""
        for attempt in range(128):
            proposed = deterministic_name(stable=f"{pid}:{attempt}", sex=sex)
            if proposed not in existing_names:
                name = proposed
                break
        if not name:
            raise ValueError("jianghu civic name space exhausted")
        existing_names.add(name); existing_ids.add(pid)
        professional = {"medicine": 0, "administration": 0, "commerce": 0, "crafting": 0, "instruction": 0}
        developed = apply_age_development(
            age=age, attributes=candidate.get("attributes", {}), martial_skills=candidate.get("martial_skills", {}),
            professional_skills=professional, qi=0, qi_control=0,
        )
        person = {
            "person_id": pid, "name": name, "birth_year": int(current_year) - age, "sex": sex,
            "body_mass_kg": deterministic_body_mass_kg(stable=pid, sex=sex, age=age),
            "appearance": int(candidate.get("appearance", 50)),
            "aptitudes": copy.deepcopy(dict(candidate.get("aptitudes", {}))),
            "attributes": developed["attributes"], "martial_skills": developed["martial_skills"],
            "professional_skills": developed["professional_skills"], "qi": developed["qi"],
            "qi_control": developed["qi_control"], "affiliation_ref": f"civilian:{source_place_ref}",
            "social_rank": "commoner", "home_place_ref": source_place_ref, "location_ref": source_place_ref,
            "personal_cash": 0,
        }
        rows.append(compact_civic_person(person)); refs.append(pid)
    pool["current_population"] = max(0, int(pool.get("current_population", 0)) - need)
    pool["identity_ordinal_cursor"] = cursor + need
    return {"civilian_state": civilians, "civic_state": civic, "person_refs": refs}


def compact_started_escort_objective(objective: Mapping[str, Any]) -> dict[str, Any]:
    """Keep contractual terms after departure; execution facts live on movement."""
    out: dict[str, Any] = {}
    for key in (
        "kind", "source_place_ref", "destination_place_ref",
        "item_ref", "quantity", "protected_person_refs", "civilian_party_kind",
    ):
        value = objective.get(key)
        if value is None or value == "" or value is False or value == 0 or value == []:
            continue
        out[key] = copy.deepcopy(value)
    return out


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
    """Describe aggregate freight load, never exact carts/animals."""
    cargo=max(0,int(cargo_kg))
    if cargo<=0: return {"transport_mode":"travel_party","freight_capacity_kg":0,"civilian_crew_count":0}
    policy=_policy(); road_payload=policy.get("road_payload_milli",{}) if isinstance(policy.get("road_payload_milli"),Mapping) else {}
    road_quality=str(route.get("road_quality") or "maintained"); road_milli=max(250,int(road_payload.get(road_quality,1000)))
    required=(cargo*1000+road_milli-1)//road_milli
    aggregate=policy.get("aggregate_transport",{}) if isinstance(policy.get("aggregate_transport"),Mapping) else {}
    crew_kg=max(1,int(aggregate.get("freight_kg_per_civilian_crew",720))); crew=(required+crew_kg-1)//crew_kg
    return {"transport_mode":"aggregate_freight","freight_capacity_kg":required,"civilian_crew_count":crew}


def minimum_martial_escorts(
    *, transport: Mapping[str, Any], protected_people: int,
    distance_km_tenths: int, terrain: str, threat_score: int,
) -> int:
    """Derive security manpower without a maximum-count shortcut."""
    p = _policy().get("escort_count_policy", {})
    if not isinstance(p, Mapping):
        p = {}
    total = max(1, int(p.get("base_martial_escorts", 2)))
    freight_kg = max(0, int(transport.get("freight_capacity_kg", 0)))
    protected = max(0, int(protected_people))
    km = (max(0, int(distance_km_tenths)) + 9) // 10
    if freight_kg:
        step = max(1, int(p.get("freight_kg_per_extra_escort", 1440)))
        total += (freight_kg + step - 1) // step
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


def _journey_route_rows(
    *, geography: Mapping[str, Any], travel: Mapping[str, Any],
    source_place_ref: str, destination_place_ref: str, cargo_kg: int = 0,
) -> tuple[list[Mapping[str, Any]], list[str], str]:
    """Return one deterministic physical path without persisting a path snapshot on the contract.

    Cargo prefers convoy-capable roads when a complete convoy path exists. If it
    does not, the same shipment can still move as a pack train over the foot
    graph. Passenger-only parties use the foot graph. The chosen path only
    becomes state once a real movement starts.
    """
    routes = geography.get("routes", []) if isinstance(geography, Mapping) else []
    if not isinstance(routes, list):
        raise ValueError("escort geography routes invalid")
    edge_rows = [row for row in routes if isinstance(row, Mapping) and isinstance(row.get("id"), str)]

    def solve(mode: str) -> tuple[list[Mapping[str, Any]], list[str]] | None:
        import heapq
        adjacency: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
        for row in edge_rows:
            allowed = row.get("allowed_modes", [])
            if not isinstance(allowed, list) or mode not in {str(x) for x in allowed}:
                continue
            a = str(row.get("from") or ""); b = str(row.get("to") or "")
            if not a or not b:
                continue
            adjacency.setdefault(a, []).append((b, row)); adjacency.setdefault(b, []).append((a, row))
        best: dict[str, int] = {source_place_ref: 0}
        previous: dict[str, tuple[str, Mapping[str, Any]]] = {}
        queue: list[tuple[int, str]] = [(0, source_place_ref)]
        while queue:
            cost, node = heapq.heappop(queue)
            if cost != best.get(node):
                continue
            if node == destination_place_ref:
                break
            for nxt, edge in adjacency.get(node, []):
                edge_cost = route_travel_hours(edge, travel)
                new_cost = cost + edge_cost
                if new_cost < best.get(nxt, 10**18):
                    best[nxt] = new_cost; previous[nxt] = (node, edge); heapq.heappush(queue, (new_cost, nxt))
        if destination_place_ref not in best:
            return None
        nodes = [destination_place_ref]; path: list[Mapping[str, Any]] = []; cur = destination_place_ref
        while cur != source_place_ref:
            prev, edge = previous[cur]; path.append(edge); nodes.append(prev); cur = prev
        path.reverse(); nodes.reverse(); return path, nodes

    modes = ["convoy", "foot"] if cargo_kg > 0 else ["foot"]
    for mode in modes:
        solved = solve(mode)
        if solved is not None:
            rows, nodes = solved
            if rows or source_place_ref == destination_place_ref:
                return rows, nodes, mode
    raise ValueError("no registered escort journey")


def plan_escort_journey_objective(
    *, kind: str, geography: Mapping[str, Any], travel: Mapping[str, Any],
    source_place_ref: str, destination_place_ref: str,
    item_ref: str = "", quantity: int = 0, cargo_value_cash: int = 0,
    protected_person_refs: Sequence[str] = (), protected_people_count: int = 0,
    civilian_party_kind: str | None = None,
) -> dict[str, Any]:
    """Hydrate one endpoint-to-endpoint escort bargain into current journey facts.

    The contract owns endpoints and terms. Route legs, transport estimates and
    risk are projections until departure, then the movement owner records the
    actual chosen/current leg.
    """
    if kind not in {"escort_shipment", "escort_person", "escort_party", "escort_mixed_convoy"}:
        raise ValueError("unsupported escort objective kind")
    if not source_place_ref or not destination_place_ref or source_place_ref == destination_place_ref:
        raise ValueError("escort journey endpoints invalid")
    refs=[]
    for ref in protected_person_refs:
        if isinstance(ref, str) and ref and ref not in refs:
            refs.append(ref)
    protected=max(len(refs),max(0,int(protected_people_count))); qty=max(0,int(quantity))
    cargo_kg=cargo_mass_kg(item_ref,qty) if item_ref and qty>0 else 0
    if kind == "escort_shipment" and cargo_kg <= 0: raise ValueError("shipment escort requires cargo")
    if kind in {"escort_person","escort_party"} and protected <= 0: raise ValueError("person escort requires protected people")
    if kind == "escort_mixed_convoy" and (cargo_kg <= 0 or protected <= 0): raise ValueError("mixed escort requires cargo and protected people")
    rows,nodes,mode=_journey_route_rows(geography=geography,travel=travel,source_place_ref=source_place_ref,destination_place_ref=destination_place_ref,cargo_kg=cargo_kg)
    if not rows: raise ValueError("escort journey has no movement")
    places=geography.get("places",{}) if isinstance(geography,Mapping) else {}
    src_region=str((places.get(source_place_ref) or {}).get("climate_profile") or "") if isinstance(places,Mapping) else ""
    dst_region=str((places.get(destination_place_ref) or {}).get("climate_profile") or "") if isinstance(places,Mapping) else ""
    road_rank={"imperial_road":0,"maintained":1,"rough":2,"trail":3,"mountain_path":4}
    worst=max(rows,key=lambda row:(road_rank.get(str(row.get("road_quality") or "maintained"),2),route_threat_estimate(row)))
    transport_route={"allowed_modes":["convoy"] if mode=="convoy" else ["foot"],"road_quality":str(worst.get("road_quality") or "maintained")}
    transport=route_transport_plan(cargo_kg=cargo_kg,route=transport_route)
    distance=max(0,int(round(sum(float(row.get("distance_km",0) or 0) for row in rows)*10)))
    expected=sum(route_travel_hours(row,travel) for row in rows)
    threat=min(100,max(route_threat_estimate(row) for row in rows)+max(0,len(rows)-1)*3)
    worst_terrain=max(rows,key=route_threat_estimate)
    minimum=minimum_martial_escorts(transport=transport,protected_people=protected,distance_km_tenths=distance,terrain=str(worst_terrain.get("terrain") or "plain"),threat_score=threat)
    escort_kind="cargo" if kind=="escort_shipment" else ("mixed" if kind=="escort_mixed_convoy" else "person")
    out={
        "kind":kind,"escort_kind":escort_kind,"source_place_ref":source_place_ref,"destination_place_ref":destination_place_ref,
        "source_region":src_region,"destination_region":dst_region,"route_refs":[str(row.get("id")) for row in rows],"places_crossed":nodes,
        "distance_km_tenths":distance,"expected_travel_hours":max(1,int(expected)),"terrain":str(worst_terrain.get("terrain") or "plain"),
        "road_quality":str(worst.get("road_quality") or ""),"estimated_toll_cash":sum(max(0,int(row.get("toll_cash",0))) for row in rows),
        "item_ref":item_ref or None,"quantity":qty,"cargo_mass_kg":cargo_kg,"cargo_value_cash":max(0,int(cargo_value_cash)),
        "protected_person_refs":refs,"protected_people_count":protected,"minimum_escort_count":minimum,"threat_score":threat,
        **transport,
    }
    if len(rows)==1: out["route_ref"]=str(rows[0].get("id") or "")
    if civilian_party_kind: out["civilian_party_kind"]=str(civilian_party_kind)
    return out


def plan_escort_objective(
    *, kind: str, route: Mapping[str, Any], travel: Mapping[str, Any],
    source_place_ref: str, destination_place_ref: str,
    item_ref: str = "", quantity: int = 0, cargo_value_cash: int = 0,
    protected_person_refs: Sequence[str] = (), protected_people_count: int = 0,
    civilian_party_kind: str | None = None,
) -> dict[str, Any]:
    """Plan one current single-edge escort objective from explicit places."""
    if kind not in {"escort_shipment", "escort_person", "escort_party", "escort_mixed_convoy"}:
        raise ValueError("unsupported escort objective kind")
    ends = {str(route.get("from") or ""), str(route.get("to") or "")}
    if not source_place_ref or not destination_place_ref or source_place_ref == destination_place_ref or {source_place_ref, destination_place_ref} != ends:
        raise ValueError("escort route endpoints invalid")
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
        "escort_kind": escort_kind,
        "route_ref": str(route.get("id") or ""),
        "source_place_ref": source_place_ref,
        "destination_place_ref": destination_place_ref,
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



def compact_offered_escort_objective(
    objective: Mapping[str, Any], *, geography: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist only the escort bargain, never its derived route/logistics snapshot."""
    geo=geography
    if geo is None: geo=json.loads((_MW / "geography.json").read_text(encoding="utf-8"))
    places=geo.get("places",{}) if isinstance(geo,Mapping) else {}
    source_place=str(objective.get("source_place_ref") or ""); destination_place=str(objective.get("destination_place_ref") or "")
    if not (source_place and destination_place and source_place != destination_place and isinstance(places,Mapping) and source_place in places and destination_place in places):
        raise ValueError("escort objective endpoints invalid")
    refs=[str(x) for x in objective.get("protected_person_refs",[]) if isinstance(x,str)] if isinstance(objective.get("protected_person_refs"),list) else []
    protected=max(len(refs),max(0,int(objective.get("protected_people_count",0))))
    out={"kind":str(objective.get("kind") or "escort_shipment"),"source_place_ref":source_place,"destination_place_ref":destination_place}
    item_ref=str(objective.get("item_ref") or ""); quantity=max(0,int(objective.get("quantity",0)))
    if item_ref: out["item_ref"]=item_ref
    if quantity: out["quantity"]=quantity
    if refs: out["protected_person_refs"]=refs
    if protected>len(refs): out["protected_people_count"]=protected
    party_kind=str(objective.get("civilian_party_kind") or "")
    if party_kind: out["civilian_party_kind"]=party_kind
    deadline=max(0,int(objective.get("deadline_hours",0)))
    if deadline: out["deadline_hours"]=deadline
    return out


def hydrate_contract_escort_objective(
    objective: Mapping[str, Any], *, geography: Mapping[str, Any] | None = None,
    travel: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive current path/logistics from endpoint-owned escort terms."""
    geo=geography
    if geo is None: geo=json.loads((_MW / "geography.json").read_text(encoding="utf-8"))
    travel_data=travel
    if travel_data is None: travel_data=json.loads((_MW / "travel.json").read_text(encoding="utf-8"))
    places=geo.get("places",{}) if isinstance(geo,Mapping) else {}
    source_place=str(objective.get("source_place_ref") or ""); destination_place=str(objective.get("destination_place_ref") or "")
    if not (source_place and destination_place and source_place != destination_place and isinstance(places,Mapping) and source_place in places and destination_place in places):
        raise ValueError("escort objective endpoints invalid")
    refs=[str(x) for x in objective.get("protected_person_refs",[]) if isinstance(x,str)] if isinstance(objective.get("protected_person_refs"),list) else []
    protected=max(len(refs),max(0,int(objective.get("protected_people_count",0))))
    hydrated=plan_escort_journey_objective(
        kind=str(objective.get("kind") or "escort_shipment"),geography=geo,travel=travel_data,
        source_place_ref=source_place,destination_place_ref=destination_place,
        item_ref=str(objective.get("item_ref") or ""),quantity=max(0,int(objective.get("quantity",0))),
        cargo_value_cash=max(0,int(objective.get("cargo_value_cash",0))),protected_person_refs=refs,protected_people_count=protected,
        civilian_party_kind=str(objective.get("civilian_party_kind") or "") or None,
    )
    deadline=max(0,int(objective.get("deadline_hours",0)))
    if deadline: hydrated["deadline_hours"]=deadline
    return hydrated

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
    *, geography: Mapping[str, Any], civilian_state: Mapping[str, Any], at: datetime, demand_milli: int = 1000,
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
            incidence_permille = min(350, max(20, pop // 500) * max(0,int(demand_milli)) // 1000)
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
    demand_pressure = escort_demand_milli(at, review_window_days=30)
    party_rows = _party_demand(geography=geography, civilian_state=civilian_state, at=at, demand_milli=demand_pressure)
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
            reward_cash=reward, objective=compact_offered_escort_objective(objective, geography=geography), source_ref=source_ref,
        )
        contract = dict(funded["contract"])
        contract_ref = str(contract.pop("contract_id"))
        active[contract_ref] = contract
        market["cash_pool"] = int(funded["issuer_cash_after"])
        writes[path] = market
        created.append(contract_ref); existing_sources.add(source_ref)
        schedule = upsert_one_off_event(schedule, {
            "event_id": f"contract_expiry_due:{contract_ref}",
            "kind": "contract_expiry_due", "due_at": str(contract["expires_at"]),
            "owner_ref": contract_ref, "requires_player_decision": False,
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
        ends = [str(route.get("from") or ""), str(route.get("to") or "")]
        source_place = next((ref for ref in ends if place_regions.get(ref) == src), "")
        destination_place = next((ref for ref in ends if ref != source_place and place_regions.get(ref) == dst), "")
        if not source_place or not destination_place:
            continue
        objective = plan_escort_objective(
            kind=kind, route=route, travel=travel, source_place_ref=source_place, destination_place_ref=destination_place,
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
            source_place_ref=str(party.get("source_place") or ""), destination_place_ref=str(party.get("destination_place") or ""),
            protected_people_count=max(1, int(party.get("protected_people_count", 1))),
            civilian_party_kind=str(party.get("civilian_party_kind") or "civilian_travelers"),
        )
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
    "compact_offered_escort_objective", "hydrate_contract_escort_objective",
    "minimum_martial_escorts", "ordinary_public_lot_quantity",
    "compact_started_escort_objective", "materialize_civilian_identities",
    "plan_escort_objective", "quote_escort_objective", "route_transport_plan",
    "route_travel_hours", "route_threat_estimate", "settle_monthly_escort_demand",
]
