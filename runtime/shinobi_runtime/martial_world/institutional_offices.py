"""Derived formal-office requirements for Jianghu institutions.

A function does not automatically deserve a persistent title.  Small gangs and
schools derive routine responsibility from current people and standing duties;
formal offices appear only when the faction's type, population, and supporting
infrastructure make the institution complex enough to need them.
"""
from __future__ import annotations

from typing import Any, Mapping

from .faction_state import living_roster_population, resolved_faction_type

# Stable office ordering is also the deterministic appointment priority.
_OFFICE_SKILL = {
    "leader": "command",
    "deputy_leader": "command",
    "chief_martial_instructor": "instruction",
    "field_commander": "command",
    "deputy_field_commander": "command",
    "scout_leader": "stealth_scouting",
    "chief_steward": "administration",
    "treasurer": "commerce",
    "quartermaster": "administration",
    "chief_physician": "medicine",
    "archivist": "administration",
}
ORDERED_CORE_OFFICES = tuple(_OFFICE_SKILL)

_THRESHOLDS: dict[str, dict[str, int]] = {
    "outlaw_faction": {
        "deputy_leader": 15, "chief_martial_instructor": 60,
        "field_commander": 25, "deputy_field_commander": 90,
        "scout_leader": 20, "chief_steward": 60, "treasurer": 75,
        "quartermaster": 45,
    },
    "martial_school": {
        "deputy_leader": 15, "chief_martial_instructor": 12,
        "field_commander": 45, "deputy_field_commander": 100,
        "scout_leader": 60, "chief_steward": 35, "treasurer": 50,
        "quartermaster": 60,
    },
    "escort_agency": {
        "deputy_leader": 15, "chief_martial_instructor": 35,
        "field_commander": 25, "deputy_field_commander": 90,
        "scout_leader": 30, "chief_steward": 35, "treasurer": 35,
        "quartermaster": 45,
    },
    "brotherhood_society": {
        "deputy_leader": 20, "chief_martial_instructor": 50,
        "field_commander": 70, "deputy_field_commander": 140,
        "scout_leader": 50, "chief_steward": 40, "treasurer": 30,
        "quartermaster": 70,
    },
    "contract_hall": {
        "deputy_leader": 20, "chief_martial_instructor": 40,
        "field_commander": 50, "deputy_field_commander": 100,
        "scout_leader": 35, "chief_steward": 25, "treasurer": 20,
        "quartermaster": 45,
    },
    "martial_house": {
        "deputy_leader": 25, "chief_martial_instructor": 30,
        "field_commander": 40, "deputy_field_commander": 100,
        "scout_leader": 50, "chief_steward": 35, "treasurer": 40,
        "quartermaster": 55,
    },
    "sect": {
        "deputy_leader": 30, "chief_martial_instructor": 20,
        "field_commander": 50, "deputy_field_commander": 120,
        "scout_leader": 60, "chief_steward": 40, "treasurer": 50,
        "quartermaster": 65,
    },
}


def _building_level(faction: Mapping[str, Any], key: str) -> int:
    buildings = faction.get("buildings", {}) if isinstance(faction.get("buildings"), Mapping) else {}
    try:
        return max(0, int(buildings.get(key, 0)))
    except (TypeError, ValueError):
        return 0


def required_institutional_offices(
    faction: Mapping[str, Any], roster: Mapping[str, Any]
) -> tuple[tuple[str, str], ...]:
    population = living_roster_population(roster)
    if population <= 0:
        return ()
    faction_type = resolved_faction_type(faction)
    thresholds = _THRESHOLDS.get(faction_type, {})
    required = {"leader"}
    for office, minimum in thresholds.items():
        if population >= minimum:
            required.add(office)

    # A formal medical/archive head requires both enough people to justify the
    # hierarchy and real institutional infrastructure. Small groups can still
    # have a best healer or record-keeper through derived duty assignment.
    if _building_level(faction, "infirmary_apothecary") > 0 and population >= 20:
        required.add("chief_physician")
    library_min = 25 if faction_type == "contract_hall" else 35
    if _building_level(faction, "library_records") >= 2 and population >= library_min:
        required.add("archivist")

    return tuple((office, _OFFICE_SKILL[office]) for office in ORDERED_CORE_OFFICES if office in required)


def required_office_names(faction: Mapping[str, Any], roster: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(office for office, _skill in required_institutional_offices(faction, roster))


__all__ = ["ORDERED_CORE_OFFICES", "required_institutional_offices", "required_office_names"]
