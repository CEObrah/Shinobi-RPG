"""Compact Jianghu semantic-command discovery and player-facing turn context."""
from __future__ import annotations

import re
from typing import Any, Mapping

from shinobi_runtime.martial_world.geography import load_static_geography

_WORLD_TIME_RE = re.compile(
    r"^(?P<era>[A-Za-z][A-Za-z0-9_]*)-(?P<year>[0-9]{4,})-(?P<month>[0-9]{2})-"
    r"(?P<day>[0-9]{2})T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})$"
)


def command_domain(command_type: str) -> str:
    if command_type == "advance_time":
        return "time"
    if command_type.startswith("jianghu_training"):
        return "training"
    if command_type.startswith("jianghu_service") or command_type.startswith("jianghu_local_travel") or command_type.startswith("jianghu_market_trade"):
        return "local_world"
    if command_type.startswith("jianghu_contract"):
        return "contracts"
    if command_type.startswith("jianghu_tournament"):
        return "tournaments"
    if command_type.startswith("jianghu_calendar"):
        return "calendar_events"
    if command_type.startswith("jianghu_deployment"):
        return "field_command"
    if command_type.startswith("jianghu_infrastructure"):
        return "infrastructure"
    if command_type.startswith("jianghu_recruitment"):
        return "recruitment"
    return "other"


def compact_commands(surface: Mapping[str, Any]) -> dict[str, Any]:
    supported = sorted({str(x) for x in surface.get("supported_command_types", []) if isinstance(x, str)})
    grouped: dict[str, list[str]] = {}
    for name in supported:
        grouped.setdefault(command_domain(name), []).append(name)
    return {
        "supported_command_types": supported,
        "intent_domains": grouped,
        "availability_overrides": dict(surface.get("availability_overrides", {})) if isinstance(surface.get("availability_overrides"), Mapping) else {},
        "contract_lookup": "Call get_command_contract for the one selected command before preview.",
        "limits": surface.get("limits", {}),
    }


def _ordinal(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def _location_text(location_id: object) -> str:
    raw = str(location_id or "").strip()
    if not raw:
        return "Location unavailable"
    geography = load_static_geography()
    places = geography.get("places", {}) if isinstance(geography, Mapping) else {}
    if isinstance(places, Mapping):
        place = places.get(raw)
        if isinstance(place, Mapping) and isinstance(place.get("name"), str) and place.get("name"):
            return str(place["name"])
    routes = geography.get("routes", []) if isinstance(geography, Mapping) else []
    if isinstance(routes, list):
        for route in routes:
            if not isinstance(route, Mapping) or route.get("id") != raw:
                continue
            origin_ref = str(route.get("from") or "")
            destination_ref = str(route.get("to") or "")
            origin = places.get(origin_ref) if isinstance(places, Mapping) else None
            destination = places.get(destination_ref) if isinstance(places, Mapping) else None
            origin_name = origin.get("name") if isinstance(origin, Mapping) else None
            destination_name = destination.get("name") if isinstance(destination, Mapping) else None
            if isinstance(origin_name, str) and origin_name and isinstance(destination_name, str) and destination_name:
                return f"{origin_name} to {destination_name} Road"
            return raw
    return raw


def build_scene_header(world_time: object, location_id: object) -> dict[str, str]:
    """Build a deterministic display header without inventing calendar conversion."""
    raw_time = str(world_time or "").strip()
    match = _WORLD_TIME_RE.fullmatch(raw_time)
    if match is None:
        date_text = raw_time or "World date unavailable"
        time_text = "Time unavailable"
    else:
        era = match.group("era")
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        date_text = f"{era} {year}, {_ordinal(month)} month, {_ordinal(day)} day"
        time_text = f"{match.group('hour')}:{match.group('minute')}"
    location_text = _location_text(location_id)
    return {
        "date_text": date_text,
        "time_text": time_text,
        "location_text": location_text,
        "text": f"{date_text} | {time_text} | {location_text}",
    }


def compact_play_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Return the bounded wire context with command schemas demand-loaded."""
    out = dict(context)
    surface = out.get("commands", {})
    if isinstance(surface, Mapping):
        out["commands"] = compact_commands(surface)

    campaign = out.get("campaign", {})
    scene = out.get("scene", {})
    player = out.get("player", {})
    world_time = campaign.get("world_time") if isinstance(campaign, Mapping) else None
    if not world_time and isinstance(scene, Mapping):
        world_time = scene.get("world_time")
    location_id = scene.get("location_id") if isinstance(scene, Mapping) else None
    if not location_id and isinstance(player, Mapping):
        location_id = player.get("current_location_id")
    out["scene_header"] = build_scene_header(world_time, location_id)
    out["presentation_contract"] = {
        "ic_turn_header_required": True,
        "ic_turn_header_position": "first_visible_line",
        "ic_turn_header_source": "scene_header.text",
        "ic_turn_header_render_exactly": True,
        "calendar_conversion_rule": "Do not convert the canonical era unless a registered mapping explicitly provides that conversion.",
    }
    return out


__all__ = ["build_scene_header", "command_domain", "compact_commands", "compact_play_context"]
