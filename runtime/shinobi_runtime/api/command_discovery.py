"""Compact Jianghu semantic-command discovery and player-facing turn context."""
from __future__ import annotations

import re
from typing import Any, Mapping

from shinobi_runtime.api.gm_scene_context import build_gm_scene_context
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
        "intent_orchestration_rule": "Interpret the full natural-language objective first; one player intent and one narrated scene may span several exact consequence operations, with fresh context between writes.",
        "scene_boundary_rule": "Runtime operation boundaries never start or end narrative scenes; gm_scene_context.scene_direction.scene_lifecycle gives the LLM the presentation-session affordance when continuity needs persistence.",
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




def _pick(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: mapping.get(key) for key in keys if key in mapping and mapping.get(key) not in (None, "", [], {})}


def _refs(value: object, maximum: int = 24) -> list[str]:
    """Return a bounded, order-preserving unique string-ref list."""
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item and item not in out:
            out.append(item)
        if len(out) >= maximum:
            break
    return out


_GM_PRIVATE_BULK_KEYS = frozenset({
    "attributes", "martial_skills", "skills", "capabilities", "equipment_manifest",
    "inventory", "participant_sheets", "focus_participants", "participants",
    "positions", "team_plans", "obstacles", "raw_state", "full_state",
})


def _compact_gm_private_extension(value: Any, *, depth: int = 0) -> Any:
    """Preserve bounded unknown backstage semantics without leaking mechanical dumps.

    Director packets evolve. A destructive whitelist can silently erase a new
    motive/causal field before the GM sees it, so unknown semantic extensions
    survive in bounded form while known high-volume mechanical keys remain
    demand-loaded through exact reads.
    """
    if depth >= 3:
        if isinstance(value, str):
            return value[:1200]
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return None
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:32]:
            key_text = str(key)
            if key_text in _GM_PRIVATE_BULK_KEYS:
                continue
            compact = _compact_gm_private_extension(item, depth=depth + 1)
            if compact not in (None, {}, []):
                out[key_text] = compact
        return out
    if isinstance(value, list):
        rows = []
        for item in value[:16]:
            compact = _compact_gm_private_extension(item, depth=depth + 1)
            if compact not in (None, {}, []):
                rows.append(compact)
        return rows
    if isinstance(value, tuple):
        return _compact_gm_private_extension(list(value), depth=depth)
    if isinstance(value, str):
        return value[:1200]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return None


def _compact_gm_private_director_context(value: Any) -> Any:
    """Keep backstage character direction without duplicating complete person sheets."""
    if not isinstance(value, Mapping):
        return value
    out = _pick(value, (
        "privacy", "scope", "candidate_present_people_count",
        "present_people_context_count", "present_people_context_truncated",
        "selection_rule", "mechanical_consequence_authority", "director_rule",
    ))
    handled = {
        "privacy", "scope", "candidate_present_people_count",
        "present_people_context_count", "present_people_context_truncated",
        "selection_rule", "mechanical_consequence_authority", "director_rule",
        "present_people", "relationship_edges", "combat",
    }
    for key, item in value.items():
        if key in handled:
            continue
        compact = _compact_gm_private_extension(item)
        if compact not in (None, {}, []):
            out[str(key)] = compact
    rows: list[dict[str, Any]] = []
    people = value.get("present_people")
    if isinstance(people, list):
        for source in people[:16]:
            if not isinstance(source, Mapping):
                continue
            row = _pick(source, ("person_ref", "name"))
            truth = source.get("character_truth")
            if isinstance(truth, Mapping):
                row["character_truth"] = _pick(truth, (
                    "person_id", "name", "faction_ref", "membership_grade",
                    "standing_offices", "health", "fatigue", "goal_state",
                    "current_equipment_state",
                ))
            cognition = source.get("cognition")
            if isinstance(cognition, Mapping):
                row["cognition"] = {
                    key: item for key, item in cognition.items()
                    if key not in {"privacy", "use_rule"}
                }
            if row:
                rows.append(row)
    if rows:
        out["present_people"] = rows
    edges = value.get("relationship_edges")
    if isinstance(edges, list):
        out["relationship_edges"] = [dict(row) for row in edges[:24] if isinstance(row, Mapping)]
    out["capability_detail"] = "demand_load_exact_person_when_material"
    return out



def _compact_active_scene_session(value: object) -> dict[str, Any] | None:
    """Keep lifecycle/presence truth hot without repeating the whole thread ledger."""
    if not isinstance(value, Mapping):
        return None
    out = _pick(value, (
        "schema", "authority", "mechanical_consequence_authority",
        "session_ref", "kind", "status", "location_ref", "process_ref",
        "started_at", "soft_end_at", "last_updated_at", "purpose",
        "participant_count", "durable_participant_count",
        "physical_scene_viable", "lifecycle_reconciliation_recommended",
        "lifecycle_reconciliation_reason", "participant_projection_rule",
        "physically_absent_participant_count",
    ))
    participants = value.get("participant_refs")
    if isinstance(participants, list):
        out["participant_refs"] = _refs(participants, 24)
        out["participant_count"] = len(participants)
        if len(participants) > 24:
            out["participant_refs_truncated"] = True
    absent = value.get("physically_absent_participant_refs")
    if isinstance(absent, list):
        out["physically_absent_participant_refs"] = _refs(absent, 24)
        out["physically_absent_participant_count"] = len(absent)
        if len(absent) > 24:
            out["physically_absent_participant_refs_truncated"] = True
    agenda = value.get("agenda")
    if isinstance(agenda, list):
        out["agenda"] = [item for item in agenda[:12] if isinstance(item, str)]
        out["agenda_count"] = len(agenda)
        if len(agenda) > 12:
            out["agenda_truncated"] = True
    open_refs = value.get("open_thread_refs", value.get("open_question_refs"))
    if isinstance(open_refs, list):
        out["open_thread_count"] = len([ref for ref in open_refs if isinstance(ref, str) and ref])
        # Exact live thread identity is supplied by active_threads/read hints.
        # Durable opaque refs are intentionally not repeated here because they
        # may belong to physically absent participants.
        out["open_thread_detail"] = "use_active_threads_or_exact_scene_open_threads_read"
    return out

def _compact_scene(scene: Mapping[str, Any], *, gm_scene_context_available: bool = False) -> dict[str, Any]:
    out = dict(scene)
    # Scene/site presence can be much larger than the immediate writer cast.
    # Keep the hot wire bounded while preserving exact counts and demand-load
    # semantics; build_gm_scene_context already consumed the full fresh source.
    for key in (
        "present_person_ids", "visible_person_ids", "derived_present_person_ids",
        "scene_session_person_ids", "event_present_person_ids",
    ):
        values = scene.get(key)
        if not isinstance(values, list):
            continue
        unique: list[str] = []
        for ref in values:
            if isinstance(ref, str) and ref and ref not in unique:
                unique.append(ref)
        out[key] = unique[:24]
        out[f"{key}_count"] = len(unique)
        if len(unique) > 24:
            out[f"{key}_truncated"] = True
    if "gm_private_director_context" in out:
        director_packet = out.get("gm_private_director_context")
        canonical_director_packet = isinstance(director_packet, Mapping) and any(
            key in director_packet
            for key in ("privacy", "scope", "present_people", "director_rule", "selection_rule")
        )
        if gm_scene_context_available and canonical_director_packet:
            # Canonical generated packets are already represented in the primary
            # writer workspace. Avoid duplicating that bounded packet on the wire.
            out["gm_private_director_context"] = {
                "available_in_gm_scene_context": True,
                "privacy": "gm_private_not_player_knowledge",
            }
        else:
            # Preserve bounded unknown/private extensions when the source is not
            # one of our canonical generated director packets. This keeps MCP
            # compaction non-destructive for forward-compatible GM truth.
            out["gm_private_director_context"] = _compact_gm_private_director_context(
                director_packet
            )
    # Combat observation remains causal evidence, but exact participant sheets
    # belong in GM-private/direct reads rather than being repeated in the scene.
    combat = out.get("combat_observation_context")
    if isinstance(combat, Mapping):
        compact = _pick(combat, (
            "combat_ref", "status", "elapsed_ms", "elapsed_seconds",
            "material_beats", "recent_material_events", "recent_events",
            "player_visible_geometry", "nearby_threats", "narration_contract",
        ))
        out["combat_observation_context"] = compact
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
    out = {"gm_scene_context": build_gm_scene_context(context), **dict(context)}
    compact_session = _compact_active_scene_session(context.get("active_scene_session"))
    if compact_session is not None:
        out["active_scene_session"] = compact_session
    scene_source = context.get("scene")
    if isinstance(scene_source, Mapping):
        out["scene"] = _compact_scene(
            scene_source,
            gm_scene_context_available=bool(out.get("gm_scene_context")),
        )
    person_reads = context.get("person_reads")
    if isinstance(person_reads, Mapping):
        refs = person_reads.get("suggested_owner_ids")
        compact_reads = _pick(person_reads, ("roster_query_available", "use"))
        if isinstance(refs, list):
            compact_reads["suggested_owner_ids"] = _refs(refs, 12)
            compact_reads["suggested_owner_count"] = len(refs)
            if len(refs) > 12:
                compact_reads["suggested_owner_ids_truncated"] = True
        out["person_reads"] = compact_reads
    narration = context.get("narration")
    if isinstance(narration, Mapping) and out.get("gm_scene_context"):
        out["narration"] = {
            "setting": narration.get("setting"),
            "gm_scene_context_is_primary_writer_workspace": True,
            "hard_consequences_require_runtime": True,
            "player_output_boundary": narration.get("player_output_boundary"),
        }
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
