"""Deterministic route-graph travel using real-place coordinates and exact edge distances."""
from __future__ import annotations

import heapq
import json
from functools import lru_cache
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from .weather import weather_snapshot
from .environment import edge_weighted_terrain_time_milli
from .geography import load_static_geography

_ROOT = Path(__file__).resolve().parents[3]
_MW = _ROOT / "game" / "data" / "martial-world"


@lru_cache(maxsize=None)
def _load(name: str) -> Mapping[str, Any]:
    # Game travel data is immutable during one runtime process. Geography is
    # composed separately so route extensions and route-frontier settlement see
    # the same canonical edge set.
    if name == "geography.json":
        return load_static_geography()
    return json.loads((_MW / name).read_text(encoding="utf-8"))


def _edge_hours(edge: Mapping[str, Any], mode: str) -> float:
    travel = _load("travel.json")
    mode_speed = float(travel["mode_speed_km_per_day"][mode])
    terrain = float(edge_weighted_terrain_time_milli(edge, travel["terrain_time_milli"])) / 1000.0
    road = float(travel["road_time_milli"][edge["road_quality"]]) / 1000.0
    crossing = float(edge.get("fixed_delay_hours", 0))
    return float(edge["distance_km"]) / mode_speed * 24.0 * terrain * road + crossing


@lru_cache(maxsize=None)
def shortest_route(*, start: str, end: str, mode: str = "foot") -> dict[str, Any]:
    geo = _load("geography.json")
    if start == end:
        return {"nodes": [start], "edges": [], "distance_km": 0.0, "baseline_hours": 0.0}
    adjacency: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
    for edge in geo["routes"]:
        a, b = edge["from"], edge["to"]
        if mode not in edge.get("allowed_modes", []):
            continue
        adjacency.setdefault(a, []).append((b, edge))
        adjacency.setdefault(b, []).append((a, edge))
    queue: list[tuple[float, str]] = [(0.0, start)]
    previous: dict[str, tuple[str, Mapping[str, Any]]] = {}
    best = {start: 0.0}
    while queue:
        cost, node = heapq.heappop(queue)
        if node == end:
            break
        if cost != best.get(node):
            continue
        for nxt, edge in adjacency.get(node, []):
            new = cost + _edge_hours(edge, mode)
            if new < best.get(nxt, float("inf")):
                best[nxt] = new
                previous[nxt] = (node, edge)
                heapq.heappush(queue, (new, nxt))
    if end not in best:
        raise ValueError("no registered route")
    nodes = [end]
    edges = []
    cur = end
    while cur != start:
        prev, edge = previous[cur]
        edges.append(edge)
        nodes.append(prev)
        cur = prev
    nodes.reverse(); edges.reverse()
    return {
        "nodes": nodes,
        "edges": [e["id"] for e in edges],
        "distance_km": round(sum(float(e["distance_km"]) for e in edges), 1),
        "baseline_hours": round(best[end], 2),
    }


def route_segment_plan(
    *, world_seed: str, start_at: datetime, edge_id: str, origin: str, destination: str,
    mode: str, party_speed_milli: int = 1000, encumbrance_milli: int = 1000,
) -> dict[str, Any]:
    """Re-evaluate one registered edge at the time it actually begins.

    The route path remains the committed strategic choice. Weather and ground
    are current environmental facts, so future segments are not frozen to the
    conditions predicted when the journey first departed.
    """
    geo = _load("geography.json")
    edge = next((row for row in geo.get("routes", []) if isinstance(row, Mapping) and row.get("id") == edge_id), None)
    if not isinstance(edge, Mapping):
        raise ValueError("registered route edge missing")
    if {str(edge.get("from") or ""), str(edge.get("to") or "")} != {str(origin), str(destination)}:
        raise ValueError("route segment endpoints mismatch")
    if mode not in edge.get("allowed_modes", []):
        raise ValueError("route mode unavailable")
    travel = _load("travel.json")
    weather = weather_snapshot(world_seed=world_seed, at=start_at, place_id=origin)
    base = _edge_hours(edge, mode)
    weather_factor = int(travel["weather_time_milli"].get(weather["condition"], 1000)) / 1000.0
    ground_factor = int(travel["ground_time_milli"].get(weather["ground"], 1000)) / 1000.0
    speed_factor = max(50, min(1500, party_speed_milli)) / 1000.0
    load_factor = max(700, min(2500, encumbrance_milli)) / 1000.0
    hours = round(base * weather_factor * ground_factor * load_factor / speed_factor, 4)
    worst_weather = max([1000, *[int(x) for x in travel.get("weather_time_milli", {}).values()]]) / 1000.0
    worst_ground = max([1000, *[int(x) for x in travel.get("ground_time_milli", {}).values()]]) / 1000.0
    provisioning_hours = round(base * worst_weather * worst_ground * load_factor / speed_factor, 4)
    edge_start_milli, edge_end_milli = (0, 1000) if origin == str(edge.get("from") or "") else (1000, 0)
    return {
        "edge_id": edge_id, "distance_km": edge["distance_km"],
        "origin_place_ref": origin, "destination_place_ref": destination,
        "edge_start_milli": edge_start_milli, "edge_end_milli": edge_end_milli,
        "weather": weather, "hours": hours, "provisioning_hours": provisioning_hours,
        "toll_cash": int(edge.get("toll_cash", 0)),
    }


def travel_plan(
    *,
    world_seed: str,
    start_at: datetime,
    start: str,
    end: str,
    mode: str,
    party_speed_milli: int = 1000,
    encumbrance_milli: int = 1000,
) -> dict[str, Any]:
    route = shortest_route(start=start, end=end, mode=mode)
    now = start_at
    total_hours = 0.0
    total_provisioning_hours = 0.0
    segments = []
    for edge_index, edge_id in enumerate(route["edges"]):
        origin = route["nodes"][edge_index]
        segment = route_segment_plan(
            world_seed=world_seed, start_at=now, edge_id=edge_id, origin=origin,
            destination=route["nodes"][edge_index + 1], mode=mode,
            party_speed_milli=party_speed_milli, encumbrance_milli=encumbrance_milli,
        )
        segments.append(segment)
        hours = float(segment["hours"])
        total_hours += hours
        total_provisioning_hours += float(segment.get("provisioning_hours", hours))
        now = now + timedelta(hours=hours)
    return {
        "nodes": route["nodes"],
        "edges": route["edges"],
        "distance_km": route["distance_km"],
        "travel_hours": round(total_hours, 2),
        "arrival_at": now.isoformat(),
        "toll_cash": sum(s["toll_cash"] for s in segments),
        "segments": segments,
        "provisioning_seconds": max(0, int(round(total_provisioning_hours * 3600.0))),
    }


def latest_safe_departure(
    *,
    world_seed: str,
    not_before: datetime,
    target_arrival: datetime,
    start: str,
    end: str,
    mode: str = "foot",
    party_speed_milli: int = 1000,
    encumbrance_milli: int = 1000,
    refinements: int = 9,
) -> dict[str, Any]:
    """Find a verified safe just-in-time departure for a weathered route.

    Date-specific weather makes travel duration discontinuous.  Solving
    ``departure = target - travel_time(departure)`` with a couple of fixed-point
    iterations can oscillate between different seasonal weather regimes and
    schedule a journey that actually arrives late.  Tournament planning needs a
    stronger invariant: return only a departure whose *recomputed* physical
    route arrives by the requested target.

    ``not_before`` is first tested as the known-safe lower bound.  The caller's
    preparation window is responsible for making that bound feasible.  A
    bounded binary refinement then moves that verified-safe bound toward the
    target.  Even if weather makes the arrival predicate locally irregular, the
    function only ever returns a departure that was itself evaluated and proven
    safe; it may be slightly earlier than the theoretical latest departure, but
    it cannot become a late phantom itinerary.
    """
    if target_arrival <= not_before:
        raise ValueError("target arrival must follow planning start")
    safe_at = not_before
    safe_plan = travel_plan(
        world_seed=world_seed, start_at=safe_at, start=start, end=end, mode=mode,
        party_speed_milli=party_speed_milli, encumbrance_milli=encumbrance_milli,
    )
    safe_arrival = datetime.fromisoformat(str(safe_plan["arrival_at"]))
    if safe_arrival > target_arrival:
        return {
            "reachable": False,
            "earliest_departure_at": safe_at.isoformat(),
            "earliest_arrival_at": safe_arrival.isoformat(),
            "target_arrival_at": target_arrival.isoformat(),
            "plan": safe_plan,
        }
    # Departing at the target itself cannot be useful for any non-local route.
    # Keep a one-minute strict upper bound so every probe is a real journey.
    high = target_arrival - timedelta(minutes=1)
    if high <= safe_at:
        high = safe_at
    for _ in range(max(0, int(refinements))):
        if high <= safe_at + timedelta(minutes=2):
            break
        midpoint = safe_at + (high - safe_at) / 2
        plan = travel_plan(
            world_seed=world_seed, start_at=midpoint, start=start, end=end, mode=mode,
            party_speed_milli=party_speed_milli, encumbrance_milli=encumbrance_milli,
        )
        arrival = datetime.fromisoformat(str(plan["arrival_at"]))
        if arrival <= target_arrival:
            safe_at = midpoint
            safe_plan = plan
            safe_arrival = arrival
        else:
            high = midpoint
    return {
        "reachable": True,
        "departure_at": safe_at.isoformat(),
        "arrival_at": safe_arrival.isoformat(),
        "target_arrival_at": target_arrival.isoformat(),
        "plan": safe_plan,
    }
