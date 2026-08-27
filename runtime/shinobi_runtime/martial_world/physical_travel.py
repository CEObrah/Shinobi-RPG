"""Shared physical route-journey mechanics.

Purpose owners such as contracts and deployments explain *why* people travel.
``route-operations.json`` alone owns their current physical road movement.  The
helpers here keep route timing exact without creating a second mobile-party save
model.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from .route_activity import compact_route_movement_roles
from .scheduler import route_ids_needing_service, sync_route_activity, upsert_one_off_event

_ROUTE_OPS = "state/martial-world/route-operations.json"
_SCHEDULER = "state/martial-world/scheduler.json"


def _dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _segment_seconds(segment: Mapping[str, Any]) -> int:
    return max(60, int(round(float(segment.get("hours", 0.0)) * 3600.0)))


def build_route_journey(
    *,
    movement_ref: str,
    movement_kind: str,
    purpose_ref: str,
    plan: Mapping[str, Any],
    participants: Sequence[str],
    leader_ref: str,
    beneficiary_ref: str,
    started_at: datetime,
    mode: str,
    destination_site_ref: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one current physical journey from an already deterministic route plan."""
    route_refs = [str(x) for x in plan.get("edges", []) if isinstance(x, str) and x]
    nodes = [str(x) for x in plan.get("nodes", []) if isinstance(x, str) and x]
    segments = [dict(x) for x in plan.get("segments", []) if isinstance(x, Mapping)]
    if not route_refs or len(nodes) != len(route_refs) + 1 or len(segments) != len(route_refs):
        raise ValueError("physical route journey plan invalid")
    segment_seconds = [_segment_seconds(row) for row in segments]
    segment_provisioning_seconds = [max(_segment_seconds(row), int(round(float(row.get("provisioning_hours", row.get("hours", 0.0))) * 3600.0))) for row in segments]
    current_weather = copy.deepcopy(dict(segments[0].get("weather", {}))) if isinstance(segments[0].get("weather"), Mapping) else {}
    segment_starts = [max(0, min(1000, int(row.get("edge_start_milli", 0)))) for row in segments]
    segment_ends = [max(0, min(1000, int(row.get("edge_end_milli", 1000)))) for row in segments]
    row: dict[str, Any] = {
        "movement_kind": str(movement_kind),
        "purpose_ref": str(purpose_ref),
        "route_ref": route_refs[0],
        "route_refs": route_refs,
        "route_index": 0,
        "journey_nodes": nodes,
        "segment_required_seconds": segment_seconds,
        "segment_provisioning_seconds": segment_provisioning_seconds,
        "provisioning_seconds": sum(segment_provisioning_seconds),
        "segment_edge_start_milli": segment_starts,
        "segment_edge_end_milli": segment_ends,
        "origin_place_ref": nodes[0],
        "destination_place_ref": nodes[-1],
        "segment_origin_place_ref": nodes[0],
        "segment_destination_place_ref": nodes[1],
        "participant_refs": [str(x) for x in participants if isinstance(x, str) and x],
        "escort_refs": [str(x) for x in participants if isinstance(x, str) and x],
        "leader_ref": str(leader_ref),
        "beneficiary_ref": str(beneficiary_ref),
        "mode": str(mode),
        "started_at": started_at.isoformat(),
        "last_progress_at": started_at.isoformat(),
        "elapsed_seconds": 0,
        "required_seconds": segment_seconds[0],
        "edge_start_milli": segment_starts[0],
        "edge_end_milli": segment_ends[0],
        "route_weather": current_weather,
        "status": "active",
    }
    if destination_site_ref:
        row["destination_site_ref"] = str(destination_site_ref)
    if isinstance(extra, Mapping):
        row.update(copy.deepcopy(dict(extra)))
    return compact_route_movement_roles(row)


def movement_required_seconds(movement: Mapping[str, Any]) -> int:
    if "required_seconds" not in movement:
        raise ValueError("route movement required_seconds missing")
    return max(1, int(movement["required_seconds"]))


def movement_elapsed_seconds(movement: Mapping[str, Any]) -> int:
    if "elapsed_seconds" not in movement:
        raise ValueError("route movement elapsed_seconds missing")
    return max(0, int(movement["elapsed_seconds"]))


def advance_movement_progress(movement: Mapping[str, Any], *, at: datetime) -> tuple[dict[str, Any], int]:
    """Advance one current movement by its exact elapsed campaign interval."""
    out = copy.deepcopy(dict(movement))
    required = movement_required_seconds(out)
    elapsed = min(required, movement_elapsed_seconds(out))
    raw_last = out.get("last_progress_at")
    if not isinstance(raw_last, str) or not raw_last:
        raise ValueError("route movement last_progress_at missing")
    try:
        last = _dt(raw_last)
    except ValueError as exc:
        raise ValueError("route movement last_progress_at invalid") from exc
    delta = min(max(0, required - elapsed), max(0, int((at - last).total_seconds())))
    elapsed += delta
    out["elapsed_seconds"] = elapsed
    out["required_seconds"] = required
    out["last_progress_at"] = at.isoformat()
    return out, delta


def movement_complete(movement: Mapping[str, Any]) -> bool:
    return movement_elapsed_seconds(movement) >= movement_required_seconds(movement)


def begin_next_segment(movement: Mapping[str, Any], *, at: datetime) -> dict[str, Any] | None:
    """Advance a multi-edge journey to its next route without changing purpose."""
    route_refs = [str(x) for x in movement.get("route_refs", []) if isinstance(x, str)]
    nodes = [str(x) for x in movement.get("journey_nodes", []) if isinstance(x, str)]
    seconds = [max(1, int(x)) for x in movement.get("segment_required_seconds", []) if isinstance(x, int)]
    starts = [max(0, min(1000, int(x))) for x in movement.get("segment_edge_start_milli", []) if isinstance(x, int)]
    ends = [max(0, min(1000, int(x))) for x in movement.get("segment_edge_end_milli", []) if isinstance(x, int)]
    current = max(0, int(movement.get("route_index", 0)))
    nxt = current + 1
    if not route_refs or nxt >= len(route_refs):
        return None
    if len(nodes) != len(route_refs) + 1 or len(seconds) != len(route_refs):
        raise ValueError("physical route journey path invalid")
    if len(starts) != len(route_refs): starts = [0] * len(route_refs)
    if len(ends) != len(route_refs): ends = [1000] * len(route_refs)
    out = copy.deepcopy(dict(movement))
    out["route_index"] = nxt
    out["route_ref"] = route_refs[nxt]
    out["segment_origin_place_ref"] = nodes[nxt]
    out["segment_destination_place_ref"] = nodes[nxt + 1]
    out["elapsed_seconds"] = 0
    out["required_seconds"] = seconds[nxt]
    out["edge_start_milli"] = starts[nxt]
    out["edge_end_milli"] = ends[nxt]
    out["route_weather"] = {}
    out["last_progress_at"] = at.isoformat()
    out["status"] = "active"
    return out


def refresh_current_segment(
    movement: Mapping[str, Any], *, at: datetime, world_seed: str,
) -> dict[str, Any]:
    """Re-evaluate the current committed edge under actual departure weather.

    The strategic path is unchanged. Only the current edge's environmental
    travel time/weather is refreshed when that edge really begins.
    """
    from .travel import route_segment_plan

    out = copy.deepcopy(dict(movement))
    route_ref = str(out.get("route_ref") or "")
    origin = str(out.get("segment_origin_place_ref") or "")
    destination = str(out.get("segment_destination_place_ref") or "")
    if not route_ref or not origin or not destination:
        return out
    segment = route_segment_plan(
        world_seed=world_seed, start_at=at, edge_id=route_ref, origin=origin, destination=destination,
        mode=str(out.get("mode") or "foot"),
        party_speed_milli=max(50, int(out.get("party_speed_milli", 1000))),
        encumbrance_milli=max(700, int(out.get("party_encumbrance_milli", 1000))),
    )
    required = _segment_seconds(segment)
    index = max(0, int(out.get("route_index", 0)))
    seconds = [max(1, int(x)) for x in out.get("segment_required_seconds", []) if isinstance(x, int)]
    if len(seconds) > index:
        seconds[index] = required; out["segment_required_seconds"] = seconds
    out["required_seconds"] = required
    out["elapsed_seconds"] = 0
    out["route_weather"] = copy.deepcopy(dict(segment.get("weather", {})))
    out["edge_start_milli"] = int(segment.get("edge_start_milli", out.get("edge_start_milli", 0)))
    out["edge_end_milli"] = int(segment.get("edge_end_milli", out.get("edge_end_milli", 1000)))
    out["last_progress_at"] = at.isoformat()
    return out


def exact_segment_due_event(movement_ref: str, movement: Mapping[str, Any], *, at: datetime) -> dict[str, Any]:
    remaining = max(1, movement_required_seconds(movement) - movement_elapsed_seconds(movement))
    route_ref = str(movement.get("route_ref") or "")
    index = max(0, int(movement.get("route_index", 0)))
    if not route_ref:
        raise ValueError("physical route journey route missing")
    return {
        "event_id": f"route_segment_due:{movement_ref}:{index}",
        "kind": "route_activity_cycle",
        "due_at": (at + timedelta(seconds=remaining)).isoformat(),
        "owner_ref": route_ref,
        "movement_ref": str(movement_ref),
        "exact_segment_due": True,
        "requires_player_decision": False,
    }


def stage_route_journey(
    *,
    route_state: Mapping[str, Any],
    schedule: Mapping[str, Any],
    movement_ref: str,
    movement: Mapping[str, Any],
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Atomically add one movement and its demand-driven route obligations."""
    route_after = copy.deepcopy(dict(route_state))
    movements = route_after.setdefault("movements", {})
    if not isinstance(movements, dict):
        raise ValueError("route movements invalid")
    if movement_ref in movements:
        raise ValueError("route movement already exists")
    movements[movement_ref] = compact_route_movement_roles(movement)
    active_routes = route_ids_needing_service(movements)
    schedule_after = sync_route_activity(schedule, active_route_ids=active_routes, now=now)
    schedule_after = upsert_one_off_event(
        schedule_after, exact_segment_due_event(movement_ref, movement, at=now)
    )
    return route_after, schedule_after


__all__ = [
    "advance_movement_progress",
    "begin_next_segment",
    "build_route_journey",
    "exact_segment_due_event",
    "movement_complete",
    "movement_elapsed_seconds",
    "movement_required_seconds", "refresh_current_segment",
    "stage_route_journey",
]
