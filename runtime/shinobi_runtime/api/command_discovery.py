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
    exact = {
        "advance_time": "time",
        "jianghu_interaction_resolution": "interaction",
        "jianghu_scene_session_resolution": "interaction",
        "jianghu_combat_resolution": "combat",
        "jianghu_local_travel_resolution": "travel",
        "jianghu_strategic_travel_resolution": "travel",
        "jianghu_training_focus_resolution": "training",
        "jianghu_medicine_resolution": "medicine",
        "jianghu_contract_resolution": "contracts",
        "jianghu_tournament_resolution": "tournaments",
        "jianghu_calendar_event_resolution": "calendar_events",
        "jianghu_library_research_resolution": "knowledge",
        "jianghu_faction_lifecycle_resolution": "factions",
        "jianghu_social_resolution": "social",
        "jianghu_family_resolution": "social",
        "jianghu_diplomacy_resolution": "social",
        "jianghu_public_disclosure_resolution": "social",
        "jianghu_crime_report_resolution": "social",
        "jianghu_security_resolution": "social",
        "jianghu_custody_resolution": "social",
        "jianghu_deployment_resolution": "institutions",
        "jianghu_institutional_operation_resolution": "institutions",
        "jianghu_infrastructure_resolution": "institutions",
        "jianghu_recruitment_resolution": "institutions",
        "jianghu_retinue_resolution": "institutions",
        "jianghu_service_purchase_resolution": "economy",
        "jianghu_market_trade_resolution": "economy",
        "jianghu_equipment_resolution": "economy",
        "jianghu_property_transfer_resolution": "economy",
        "jianghu_production_resolution": "economy",
    }
    return exact.get(command_type, "other")


def grouped_commands(surface: Mapping[str, Any]) -> dict[str, list[str]]:
    supported = sorted({str(x) for x in surface.get("supported_command_types", []) if isinstance(x, str)})
    grouped: dict[str, list[str]] = {}
    for name in supported:
        grouped.setdefault(command_domain(name), []).append(name)
    return grouped


def compact_commands(surface: Mapping[str, Any]) -> dict[str, Any]:
    grouped = grouped_commands(surface)
    families = {name: {"operation_count": len(values)} for name, values in sorted(grouped.items())}
    out: dict[str, Any] = {
        "mechanic_families": families,
        "family_count": len(families),
        "operation_count": sum(len(values) for values in grouped.values()),
        "catalog_role": "mechanical consequences and durable writes only; never a whitelist of fictional actions",
        "scene_only_action_rule": "ordinary reversible scene realization and conversation may require no command",
        "family_lookup": "Only when a hard consequence is implicated, call get_command_family for the one relevant mechanic family.",
        "contract_lookup": "Then call get_command_contract for one selected mechanical operation before preview.",
        "limits": surface.get("limits", {}),
    }
    overrides = surface.get("availability_overrides")
    if isinstance(overrides, Mapping) and overrides:
        # Exceptional availability can name a small set of temporarily legal operations;
        # ordinary turns stay family-count only.
        out["availability_overrides"] = dict(overrides)
    return out


def compact_command_family(surface: Mapping[str, Any], family: str) -> dict[str, Any]:
    grouped = grouped_commands(surface)
    if family not in grouped:
        raise KeyError(family)
    command_types = grouped[family]
    out: dict[str, Any] = {
        "family": family,
        "operation_count": len(command_types),
        "command_types": command_types,
        "catalog_role": "mechanical consequence candidates for an already-understood natural-language intent",
        "contract_lookup": "Call get_command_contract for the one selected mechanical operation before preview.",
    }
    overrides = surface.get("availability_overrides")
    if isinstance(overrides, Mapping):
        family_overrides = {name: overrides[name] for name in command_types if name in overrides}
        if family_overrides:
            out["availability_overrides"] = family_overrides
    return out


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
    out["semantic_action_contract"] = {
        "intent_before_mechanics": True,
        "attempt_is_not_outcome": True,
        "ordinary_reversible_scene_action_needs_command": False,
        "mechanic_discovery_after_interpretation": True,
        "compound_declaration_preserves_scene_components": True,
        "player_authored_external_outcomes_forbidden": True,
        "gm_private_director_truth_may_exceed_player_knowledge": True,
        "player_output_remains_knowledge_bounded": True,
        "unsupported_rule": "Only an unsupported hard mechanical consequence fails closed; plausible conversation and reversible scene behavior do not.",
    }
    return out


__all__ = ["build_scene_header", "command_domain", "grouped_commands", "compact_commands", "compact_command_family", "compact_play_context"]
