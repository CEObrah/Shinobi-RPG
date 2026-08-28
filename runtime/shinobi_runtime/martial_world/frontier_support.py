"""Small deterministic helpers shared by Jianghu frontier domain reducers.

These helpers own no campaign state and perform no I/O.  Keeping them outside
any one domain reducer prevents route/tournament extraction from creating a
second implementation of common frontier mechanics.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .rankings import add_public_points
from .economy_catalog import raw_material_refs
from .relationships import apply_relationship_event


def person_place(
    person: Mapping[str, Any], *, local_sites: Mapping[str, Any],
    home_place: str = "", home_site_ref: str = "",
) -> str:
    """Resolve a person's current settlement from sparse home/location state."""
    location_ref = str(person.get("location_ref") or "")
    if not location_ref:
        return str(home_place or "")
    if home_site_ref and location_ref == str(home_site_ref):
        return str(home_place or location_ref)
    sites = local_sites.get("sites", {}) if isinstance(local_sites, Mapping) else {}
    site = sites.get(location_ref) if isinstance(sites, Mapping) else None
    if isinstance(site, Mapping) and site.get("parent_place_ref"):
        return str(site.get("parent_place_ref"))
    return location_ref


def arrival_site(local_sites: Mapping[str, Any], place_ref: str) -> str | None:
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


def lodging_site(local_sites: Mapping[str, Any], place_ref: str) -> str | None:
    """Choose one real public inn for a traveling party, deterministically."""
    sites = local_sites.get("sites", {}) if isinstance(local_sites, Mapping) else {}
    if not isinstance(sites, Mapping):
        return None
    inns = sorted(
        str(site_ref) for site_ref, row in sites.items()
        if isinstance(site_ref, str) and isinstance(row, Mapping)
        and row.get("parent_place_ref") == place_ref and row.get("site_type") == "inn"
        and str(row.get("public_access", "public")) not in {"restricted_by_faction_policy", "private"}
    )
    # No authored public inn means there is no inn purchase.  Callers must
    # fall back to a field camp instead of relabeling an arbitrary public site
    # (clinic, apothecary, caravan yard, etc.) as lodging.
    return inns[0] if inns else None


def tournament_venue_site(local_sites: Mapping[str, Any], place_ref: str) -> str | None:
    """Return the host city's actual tournament ground when one is authored."""
    sites = local_sites.get("sites", {}) if isinstance(local_sites, Mapping) else {}
    if not isinstance(sites, Mapping):
        return arrival_site(local_sites, place_ref)
    grounds = sorted(
        str(site_ref) for site_ref, row in sites.items()
        if isinstance(site_ref, str) and isinstance(row, Mapping)
        and row.get("parent_place_ref") == place_ref and row.get("site_type") == "tournament_ground"
    )
    return grounds[0] if grounds else arrival_site(local_sites, place_ref)


def tournament_organizer_ref(host_place: str, *, great: bool) -> str:
    """Tournament host identity; purse funding remains entry-fee based."""
    if great or host_place == "luoyang":
        return "government.imperial"
    return f"government.{host_place}"


def credit_cargo_to_inventory(inventory: dict[str, Any], *, item_ref: str, quantity: int) -> None:
    qty = max(0, int(quantity))
    if qty <= 0:
        return
    if item_ref == "food_ration_day":
        inventory["food_ration_days"] = max(0, int(inventory.get("food_ration_days", 0))) + qty
        return
    if item_ref in raw_material_refs():
        raw = inventory.setdefault("raw_materials", {})
        if isinstance(raw, dict):
            raw[item_ref] = max(0, int(raw.get(item_ref, 0))) + qty
        return
    equipment = inventory.setdefault("equipment", {})
    if isinstance(equipment, dict):
        equipment[item_ref] = max(0, int(equipment.get(item_ref, 0))) + qty


def social_event(
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


def reputation_after_points(
    state: Mapping[str, Any], person_ref: str, *, tournament_points: int = 0,
    contract_points: int = 0, duel_points: int = 0,
) -> dict[str, Any]:
    return add_public_points(
        state, person_ref, tournament_points=tournament_points,
        contract_points=contract_points, duel_points=duel_points,
    )


def chunk_contains_final_owner(
    schedule: Mapping[str, Any], events: Sequence[Mapping[str, Any]], *, class_id: str,
) -> bool:
    """Return whether this resumable recurring chunk contains its class's last owner."""
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


def relations_by_faction(relations: Mapping[str, Any]) -> dict[str, list[Mapping[str, Any]]]:
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


def market_path(region_id: str) -> str:
    return f"state/martial-world/markets/{region_id}.json"


def route_lookup(geography: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = geography.get("routes", [])
    if not isinstance(rows, list):
        return {}
    return {
        str(row["id"]): row
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("id"), str)
    }


def place_to_region(geography: Mapping[str, Any]) -> dict[str, str]:
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


def event_order(event: Mapping[str, Any]) -> tuple[int, str, str]:
    # Resource settlement precedes strategic review at a shared frontier.
    order = {
        "regional_market_cycle": 10,
        "faction_upkeep": 20,
        "faction_member_cycle": 25,
        "equipment_maintenance_review": 30,
        "faction_review": 40,
        "custody_captor_review": 42,
        "custody_response_due": 43,
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


__all__ = [
    "person_place",
    "arrival_site", "lodging_site", "tournament_venue_site", "tournament_organizer_ref",
    "credit_cargo_to_inventory", "social_event", "reputation_after_points",
    "chunk_contains_final_owner",
    "relations_by_faction", "market_path", "route_lookup", "place_to_region", "event_order",
]
