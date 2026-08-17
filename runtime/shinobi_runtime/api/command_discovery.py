"""Compact semantic-command discovery for the public MCP handoff.

The runtime may keep rich command descriptors internally for validation and
preview construction. Live ChatGPT context receives only command names grouped
by player intent plus material availability overrides. The selected command's
full descriptor is retrieved separately through get_command_contract.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_DOMAIN_PREFIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("time", ("advance_time", "scene_boundary", "downtime")),
    ("missions", ("mission_",)),
    ("training", ("training_", "breakthrough_", "technique_learning", "team_training", "team_development")),
    ("teams", ("team_",)),
    ("travel", ("travel_", "formation_movement")),
    ("combat", ("combat_", "battlefield_", "formation_combat", "conflict_", "special_combat_state_")),
    ("population", ("population_", "recruitment_", "person_materialization", "person_exactification")),
    ("medical", ("medical_", "recovery_")),
    ("social", ("relationship_", "reputation_", "family_", "career_", "office_", "institution_affiliation", "promotion_exam_")),
    ("economy", ("asset_", "purchase_", "service_", "inventory_", "commerce_", "manufacturing_")),
    ("information", ("information_", "investigation_", "security_network_")),
    ("institutions", ("institution_", "governance_", "legal_", "commitment_", "custody_")),
    ("diplomacy", ("diplomacy_",)),
    ("forces", ("formation_", "force_", "command_")),
    ("special", ("research_", "seal_", "summon_", "puppet_", "ocular_", "biological_", "jinchuriki_")),
)


def command_domain(command_type: str) -> str:
    for domain, prefixes in _DOMAIN_PREFIXES:
        if any(command_type == prefix or command_type.startswith(prefix) for prefix in prefixes):
            return domain
    return "other"


def compact_commands(command_surface: Mapping[str, Any]) -> dict[str, Any]:
    supported = [str(x) for x in command_surface.get("supported_command_types", []) if isinstance(x, str)]
    grouped: dict[str, list[str]] = {}
    for command_type in sorted(set(supported)):
        grouped.setdefault(command_domain(command_type), []).append(command_type)

    overrides: dict[str, str] = {}
    records = command_surface.get("command_types")
    if isinstance(records, Mapping):
        for command_type in supported:
            record = records.get(command_type)
            if not isinstance(record, Mapping):
                continue
            availability = record.get("availability")
            if isinstance(availability, str) and availability not in {
                "subject_to_domain_authority_and_state",
                "available",
            }:
                overrides[command_type] = availability

    result: dict[str, Any] = {
        "supported_command_types": sorted(set(supported)),
        "intent_domains": grouped,
        "availability_overrides": overrides,
        "contract_lookup": "Call get_command_contract for the one selected command before preview.",
    }
    for key in (
        "active_mission_owner_ids",
        "known_unsupported_intents",
        "limits",
        "availability_scope",
        "temporarily_available_command_types",
        "hidden_internal_command_types",
    ):
        if key in command_surface:
            result[key] = command_surface[key]
    return result


def compact_play_context(context: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(context)
    commands = context.get("commands")
    if isinstance(commands, Mapping):
        result["commands"] = compact_commands(commands)
    return result


__all__ = ["command_domain", "compact_commands", "compact_play_context"]
