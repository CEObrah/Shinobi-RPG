"""Derived public route intelligence for contracts and travel planning.

Nothing in this module is campaign state.  It projects authored geography and
public faction presence into the information a competent traveler can know
before accepting a route job.  Secret plans, hidden pursuers, exact rosters and
combat ratings never appear here.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping

from .faction_registry import current_faction_refs
from .faction_state import faction_path, faction_profile, hydrate_faction_state, resolved_faction_type

_ROOT = Path(__file__).resolve().parents[3]
_MW = _ROOT / "game" / "data" / "martial-world"


@lru_cache(maxsize=1)
def _geography() -> Mapping[str, Any]:
    return json.loads((_MW / "geography.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _settlement_index() -> Mapping[str, Any]:
    return json.loads((_MW / "settlement-faction-index.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _world_factions() -> Mapping[str, Any]:
    data = json.loads((_MW / "world-seed.json").read_text(encoding="utf-8"))
    rows = data.get("martial_factions", {}) if isinstance(data, Mapping) else {}
    return rows if isinstance(rows, Mapping) else {}


def _current_profiles(read_json: Callable[[str], Any] | None) -> Mapping[str, Mapping[str, Any]]:
    if read_json is None:
        return {str(fid): raw for fid, raw in _world_factions().items() if isinstance(fid, str) and isinstance(raw, Mapping)}
    rows: dict[str, Mapping[str, Any]] = {}
    for fid in current_faction_refs(read_json):
        try:
            raw = read_json(faction_path(fid))
            faction = hydrate_faction_state(raw)
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            continue
        rows[fid] = faction
    return rows


def _name(fid: str) -> str:
    row = faction_profile(fid)
    if isinstance(row, Mapping) and isinstance(row.get("name"), str):
        return str(row["name"])
    return fid


def _public_attack_patterns(profile: Mapping[str, Any]) -> list[str]:
    faction_type = str(profile.get("type") or "")
    subtype = str(profile.get("outlaw_subtype") or "")
    if faction_type != "outlaw_faction":
        return []
    by_subtype = {
        "road_band": ["road robbery", "cargo theft", "extortion"],
        "mountain_stronghold": ["road ambush", "kidnapping", "ransom"],
        "river_pirates": ["river or ferry seizure", "cargo theft", "ransom"],
        "urban_gang": ["theft", "extortion", "kidnapping"],
        "smuggling_ring": ["cargo theft", "contraband diversion", "bribery or extortion"],
    }
    return list(by_subtype.get(subtype, ["robbery", "extortion", "kidnapping for ransom"]))


def route_intelligence_brief(
    route_ref: str, *, source_place_ref: str | None = None,
    destination_place_ref: str | None = None, read_json: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Return public, non-omniscient route intelligence for one authored road.

    ``known_route_threats`` contains only organizations whose authored operating
    territory includes the road.  ``settlement_presence`` is broader: it names
    institutions publicly based at the route endpoints, because a traveler can
    reasonably know who has local presence without assuming those institutions
    intend violence.
    """
    geo = _geography()
    routes = geo.get("routes", []) if isinstance(geo, Mapping) else []
    route = next(
        (row for row in routes if isinstance(row, Mapping) and row.get("id") == route_ref),
        None,
    ) if isinstance(routes, list) else None
    if not isinstance(route, Mapping):
        raise KeyError(route_ref)

    ends = [str(route.get("from") or ""), str(route.get("to") or "")]
    src = str(source_place_ref or "")
    dst = str(destination_place_ref or "")
    if src not in ends:
        src = ends[0]
    if dst not in ends or dst == src:
        dst = ends[1] if len(ends) > 1 else ""

    current_profiles = _current_profiles(read_json)
    threats: list[dict[str, Any]] = []
    for fid, raw in sorted(current_profiles.items(), key=lambda row: str(row[0])):
        if resolved_faction_type(raw) != "outlaw_faction":
            continue
        operating = raw.get("operating_routes", [])
        if not isinstance(operating, list) or route_ref not in operating:
            continue
        threats.append({
            "faction_ref": fid,
            "name": str(raw.get("name") or _name(fid)),
            "faction_type": "outlaw_faction",
            "outlaw_subtype": str(raw.get("outlaw_subtype") or ""),
            "headquarters": str(raw.get("headquarters") or ""),
            "known_for": _public_attack_patterns(raw),
            "information_confidence": "established_public_presence",
        })

    settlement_presence: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    if read_json is None:
        # Static fallback keeps pure-data callers/tests useful. Live API callers
        # supply current state and therefore never resurrect destroyed factions.
        index = _settlement_index().get("by_place", {}) if isinstance(_settlement_index(), Mapping) else {}
        by_place = index if isinstance(index, Mapping) else {}
        for place in (src, dst):
            refs = by_place.get(place, []) if isinstance(by_place, Mapping) else []
            for fid in refs if isinstance(refs, list) else []:
                profile = current_profiles.get(str(fid), {})
                if not isinstance(fid, str) or not isinstance(profile, Mapping) or (place, fid) in seen:
                    continue
                seen.add((place, fid))
                settlement_presence.append({
                    "place_ref": place, "faction_ref": fid,
                    "name": str(profile.get("name") or _name(fid)),
                    "faction_type": resolved_faction_type(profile),
                })
    else:
        for place in (src, dst):
            for fid, profile in sorted(current_profiles.items()):
                if (place, fid) in seen or str(profile.get("headquarters") or "") != place:
                    continue
                seen.add((place, fid))
                settlement_presence.append({
                    "place_ref": place, "faction_ref": fid,
                    "name": str(profile.get("name") or _name(fid)),
                    "faction_type": resolved_faction_type(profile),
                })

    return {
        "route_ref": route_ref,
        "source_place_ref": src,
        "destination_place_ref": dst,
        "places_crossed": [place for place in (src, dst) if place],
        "distance_km": float(route.get("distance_km", 0) or 0),
        "terrain": str(route.get("terrain") or ""),
        "road_quality": str(route.get("road_quality") or ""),
        "known_route_threats": threats,
        "settlement_presence": settlement_presence,
        "information_rule": (
            "Public route presence only. This does not reveal hidden pursuit, secret intent, "
            "exact personnel, concealed identities, or future attacks."
        ),
    }


def journey_intelligence_brief(
    route_refs: list[str] | tuple[str, ...], *, source_place_ref: str,
    destination_place_ref: str, read_json: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Aggregate public intelligence across an already-derived physical path.

    The active path may cross several settlements. This projection names only
    publicly established route operators and institutions based at settlements
    actually crossed. It never turns future attacks, hidden pursuers or exact
    combat capability into player knowledge.
    """
    refs=[str(x) for x in route_refs if isinstance(x,str) and x]
    if not refs:
        raise ValueError("journey route refs required")
    legs=[]; places=[]; threats={}; presence={}; total_distance=0.0
    current=str(source_place_ref or "")
    for ref in refs:
        leg=route_intelligence_brief(ref,source_place_ref=current or None,read_json=read_json)
        # Orient each leg from the settlement reached by the prior leg.
        src=str(leg.get("source_place_ref") or ""); dst=str(leg.get("destination_place_ref") or "")
        if current and src != current:
            leg=route_intelligence_brief(ref,source_place_ref=current,read_json=read_json)
            src=str(leg.get("source_place_ref") or ""); dst=str(leg.get("destination_place_ref") or "")
        legs.append(leg); total_distance += float(leg.get("distance_km",0) or 0)
        if src and (not places or places[-1] != src): places.append(src)
        if dst: places.append(dst)
        current=dst
        for row in leg.get("known_route_threats",[]) if isinstance(leg.get("known_route_threats"),list) else []:
            if not isinstance(row,Mapping): continue
            fid=str(row.get("faction_ref") or "")
            if not fid: continue
            item=threats.setdefault(fid,dict(row)); item.setdefault("route_refs",[])
            if ref not in item["route_refs"]: item["route_refs"].append(ref)
        for row in leg.get("settlement_presence",[]) if isinstance(leg.get("settlement_presence"),list) else []:
            if not isinstance(row,Mapping): continue
            key=(str(row.get("place_ref") or ""),str(row.get("faction_ref") or ""))
            if all(key): presence[key]=dict(row)
    if destination_place_ref and (not places or places[-1] != destination_place_ref):
        places.append(str(destination_place_ref))
    return {
        "source_place_ref":str(source_place_ref or (places[0] if places else "")),
        "destination_place_ref":str(destination_place_ref or (places[-1] if places else "")),
        "route_refs":refs,
        "places_crossed":places,
        "distance_km":round(total_distance,1),
        "known_route_threats":[threats[fid] for fid in sorted(threats)],
        "settlement_presence":[presence[key] for key in sorted(presence)],
        "legs":legs,
        "information_rule":"Public presence along the chosen route only. Hidden pursuit, secret intent, exact personnel, concealed identities, and future attacks remain unknown.",
    }


__all__ = ["journey_intelligence_brief", "route_intelligence_brief"]
