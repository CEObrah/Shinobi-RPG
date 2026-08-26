"""Closed deterministic Jianghu combat-doctrine templates.

Faction doctrine is authored directly on each faction. Individual doctrine is a
static personal behavior template. The only bespoke team-level layer in this
campaign is the player's standing retinue doctrine; ordinary patrols, escorts,
and ad-hoc NPC groups do not receive generated team doctrine records.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[3]
_REGISTRY = _ROOT / "game" / "data" / "martial-world" / "combat-doctrines.json"

FACTION_TEAM_FIELDS = (
    "offensive_pressure",
    "defensive_caution",
    "qi_conservation",
    "formation_cohesion",
    "mutual_support",
    "individual_initiative",
    "scouting_emphasis",
    "ambush_emphasis",
    "ranged_emphasis",
    "close_combat_emphasis",
    "reserve_preference",
    "concentration_of_force",
    "pursuit",
    "withdrawal_discipline",
    "casualty_preservation",
    "civilian_restraint",
    "prisoner_preference",
    "lethality_threshold",
)

INDIVIDUAL_TOP_LEVEL_FIELDS = (
    "engagement",
    "defense",
    "resource_discipline",
    "force_policy",
    "targeting",
)

_ENGAGEMENT_ENUMS = {
    "range_preference": {"adaptive", "close", "reach", "ranged"},
    "initiative_posture": {"reactive", "balanced", "assertive"},
    "commitment_posture": {"measured", "balanced", "committed"},
    "pursuit_posture": {"restrained", "balanced", "persistent"},
    "movement_economy": {"minimal_required", "balanced", "mobile"},
    "finishing_window": {"cautious", "commit_decisively"},
}
_DEFENSE_ENUMS = {
    "primary_response": {"adaptive", "distance", "dodge", "parry", "block"},
    "counterattack_posture": {"rare", "selective", "active"},
}
_RESOURCE_FIELDS = {"qi_conservation", "fatigue_reserve"}
_TARGETING_FIELDS = {"disable_priority", "lethal_priority"}
_FORCE_POLICY_FIELDS = {"default", "formal_spar", "tournament_nonlethal", "capture_objective", "lethal_attack", "ambush", "battlefield"}
_FORCE_INTENTS = {"disable", "lethal"}
_FORCE_CONTEXTS = {"default", "formal_spar", "tournament_nonlethal", "capture_objective", "lethal_attack", "ambush", "battlefield"}
_FORCE_VALUES = {"disable", "lethal"}

_RETINUE_TOP_LEVEL_FIELDS = {"principal", "allocation", "formation", "temporary_members"}
_RETINUE_PRINCIPAL_FIELDS = {"protection_priority", "engagement_policy", "strongest_enemy_policy"}
_RETINUE_ALLOCATION_FIELDS = {"guards_first", "equal_threat_policy", "numerical_superiority_policy", "outnumbered_policy"}
_RETINUE_FORMATION_FIELDS = {"encirclement_response", "rear_exposure_priority", "cohesion", "break_for_pursuit"}
_RETINUE_TEMP_FIELDS = {"inherit_retinue_coordination"}
_RETINUE_ENUMS = {
    "engagement_policy": {"reserve_until_threat_overflow"},
    "strongest_enemy_policy": {"never_forced"},
    "equal_threat_policy": {"one_threat_per_available_member"},
    "numerical_superiority_policy": {"defeat_in_detail"},
    "outnumbered_policy": {"compact_sector_defense"},
    "encirclement_response": {"back_to_back_outward_sectors"},
}


def _bounded_int(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
        raise ValueError(f"combat doctrine {field} must be integer 0..100")
    return value


def _closed_mapping(value: Any, allowed: set[str], *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != allowed:
        raise ValueError(f"combat doctrine {field} shape invalid")
    return value


@lru_cache(maxsize=1)
def doctrine_registry() -> Mapping[str, Any]:
    raw = json.loads(_REGISTRY.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping) or raw.get("schema") != "jianghu-combat-doctrines-3.0":
        raise ValueError("jianghu combat doctrine registry invalid")
    for key in ("player_retinue_templates", "individual_templates", "generic_intent_priorities"):
        if not isinstance(raw.get(key), Mapping):
            raise ValueError(f"jianghu combat doctrine registry missing {key}")
    if len(raw["player_retinue_templates"]) != 1:
        raise ValueError("campaign must contain exactly one bespoke player-retinue doctrine template")
    for ref, row in raw["player_retinue_templates"].items():
        if not isinstance(ref, str) or not isinstance(row, Mapping):
            raise ValueError("jianghu player retinue doctrine template invalid")
        validate_player_retinue_doctrine(row)
    for ref, row in raw["individual_templates"].items():
        if not isinstance(ref, str) or not isinstance(row, Mapping):
            raise ValueError("jianghu individual doctrine template invalid")
        validate_individual_doctrine(row)
    return raw


def validate_faction_doctrine(doctrine: Mapping[str, Any]) -> dict[str, int]:
    """Validate the authored institutional doctrine shape used by factions."""
    unknown = set(doctrine) - set(FACTION_TEAM_FIELDS)
    if unknown:
        raise ValueError(f"faction combat doctrine has unknown fields: {sorted(unknown)}")
    missing = set(FACTION_TEAM_FIELDS) - set(doctrine)
    if missing:
        raise ValueError(f"faction combat doctrine missing fields: {sorted(missing)}")
    return {key: _bounded_int(doctrine[key], field=key) for key in FACTION_TEAM_FIELDS}


def validate_player_retinue_doctrine(doctrine: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the sole bespoke persistent-team doctrine used by Wei's retinue."""
    if set(doctrine) != _RETINUE_TOP_LEVEL_FIELDS:
        raise ValueError("player retinue combat doctrine top-level shape invalid")
    principal = _closed_mapping(doctrine.get("principal"), _RETINUE_PRINCIPAL_FIELDS, field="principal")
    allocation = _closed_mapping(doctrine.get("allocation"), _RETINUE_ALLOCATION_FIELDS, field="allocation")
    formation = _closed_mapping(doctrine.get("formation"), _RETINUE_FORMATION_FIELDS, field="formation")
    temporary = _closed_mapping(doctrine.get("temporary_members"), _RETINUE_TEMP_FIELDS, field="temporary_members")
    out = {
        "principal": {
            "protection_priority": _bounded_int(principal["protection_priority"], field="protection_priority"),
            "engagement_policy": str(principal["engagement_policy"]),
            "strongest_enemy_policy": str(principal["strongest_enemy_policy"]),
        },
        "allocation": {
            "guards_first": allocation["guards_first"],
            "equal_threat_policy": str(allocation["equal_threat_policy"]),
            "numerical_superiority_policy": str(allocation["numerical_superiority_policy"]),
            "outnumbered_policy": str(allocation["outnumbered_policy"]),
        },
        "formation": {
            "encirclement_response": str(formation["encirclement_response"]),
            "rear_exposure_priority": _bounded_int(formation["rear_exposure_priority"], field="rear_exposure_priority"),
            "cohesion": _bounded_int(formation["cohesion"], field="cohesion"),
            "break_for_pursuit": formation["break_for_pursuit"],
        },
        "temporary_members": {
            "inherit_retinue_coordination": temporary["inherit_retinue_coordination"],
        },
    }
    for key in ("guards_first", "break_for_pursuit", "inherit_retinue_coordination"):
        container = out["allocation"] if key == "guards_first" else out["formation"] if key == "break_for_pursuit" else out["temporary_members"]
        if not isinstance(container[key], bool):
            raise ValueError(f"player retinue doctrine {key} must be boolean")
    for section in out.values():
        if not isinstance(section, Mapping):
            continue
        for key, value in section.items():
            allowed = _RETINUE_ENUMS.get(key)
            if allowed is not None and value not in allowed:
                raise ValueError(f"player retinue doctrine {key} invalid")
    return out


def validate_individual_doctrine(doctrine: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(doctrine) - set(INDIVIDUAL_TOP_LEVEL_FIELDS)
    if unknown:
        raise ValueError(f"individual combat doctrine has unknown fields: {sorted(unknown)}")
    out: dict[str, Any] = {}
    engagement = doctrine.get("engagement", {})
    if engagement:
        if not isinstance(engagement, Mapping) or set(engagement) - set(_ENGAGEMENT_ENUMS):
            raise ValueError("individual combat doctrine engagement invalid")
        out["engagement"] = {}
        for key, allowed in _ENGAGEMENT_ENUMS.items():
            if key in engagement:
                value = engagement[key]
                if value not in allowed:
                    raise ValueError(f"individual combat doctrine {key} invalid")
                out["engagement"][key] = str(value)
    defense = doctrine.get("defense", {})
    if defense:
        if not isinstance(defense, Mapping) or set(defense) - set(_DEFENSE_ENUMS):
            raise ValueError("individual combat doctrine defense invalid")
        out["defense"] = {}
        for key, allowed in _DEFENSE_ENUMS.items():
            if key in defense:
                value = defense[key]
                if value not in allowed:
                    raise ValueError(f"individual combat doctrine {key} invalid")
                out["defense"][key] = str(value)
    resources = doctrine.get("resource_discipline", {})
    if resources:
        if not isinstance(resources, Mapping) or set(resources) - _RESOURCE_FIELDS:
            raise ValueError("individual combat doctrine resource discipline invalid")
        out["resource_discipline"] = {
            key: _bounded_int(value, field=key) for key, value in resources.items()
        }
    force_policy = doctrine.get("force_policy", {})
    if force_policy:
        force_policy = _closed_mapping(force_policy, _FORCE_POLICY_FIELDS, field="force_policy")
        normalized_force = {}
        for key in sorted(_FORCE_POLICY_FIELDS):
            value = force_policy[key]
            if value not in _FORCE_INTENTS:
                raise ValueError(f"individual combat doctrine force policy {key} invalid")
            normalized_force[key] = str(value)
        out["force_policy"] = normalized_force
    targeting = doctrine.get("targeting", {})
    if targeting:
        if not isinstance(targeting, Mapping) or set(targeting) - _TARGETING_FIELDS:
            raise ValueError("individual combat doctrine targeting invalid")
        normalized: dict[str, list[str]] = {}
        for key in _TARGETING_FIELDS:
            rows = targeting.get(key, [])
            if not isinstance(rows, list) or any(not isinstance(x, str) or not x for x in rows):
                raise ValueError(f"individual combat doctrine {key} invalid")
            normalized[key] = list(rows)
        out["targeting"] = normalized
    return out



def resolve_force_intent(doctrine: Mapping[str, Any] | None, context: str) -> str:
    """Resolve a closed situational force policy without prose inference."""
    allowed_contexts = _FORCE_POLICY_FIELDS
    key = context if context in allowed_contexts else "default"
    if isinstance(doctrine, Mapping):
        policy = doctrine.get("force_policy")
        if isinstance(policy, Mapping):
            value = policy.get(key, policy.get("default", "disable"))
            if value in _FORCE_INTENTS:
                return str(value)
    return "lethal" if key in {"lethal_attack", "ambush", "battlefield"} else "disable"

def resolve_player_retinue_doctrine(ref: str | None) -> Mapping[str, Any] | None:
    if not isinstance(ref, str) or not ref:
        return None
    row = doctrine_registry()["player_retinue_templates"].get(ref)
    if row is None:
        raise KeyError(ref)
    return copy.deepcopy(validate_player_retinue_doctrine(row))


def resolve_individual_doctrine(ref: str | None) -> Mapping[str, Any] | None:
    if not isinstance(ref, str) or not ref:
        return None
    row = doctrine_registry()["individual_templates"].get(ref)
    if row is None:
        raise KeyError(ref)
    return copy.deepcopy(validate_individual_doctrine(row))


__all__ = [
    "FACTION_TEAM_FIELDS",
    "doctrine_registry",
    "resolve_individual_doctrine",
    "resolve_force_intent",
    "resolve_player_retinue_doctrine",
    "validate_faction_doctrine",
    "validate_individual_doctrine",
    "validate_player_retinue_doctrine",
]
