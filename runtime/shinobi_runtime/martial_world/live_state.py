"""Direct persistent Jianghu state routing helpers."""
from __future__ import annotations

import copy, hashlib
from typing import Any, Mapping

from shinobi_runtime.martial_world.person_state import (
    compact_person_state,
    home_location_ref,
    hydrate_person_state,
)
from shinobi_runtime.martial_world.faction_state import read_faction, roster_path
from shinobi_runtime.martial_world.training import apply_institutional_training
from shinobi_runtime.martial_world.civic import civic_person, set_civic_person
from shinobi_runtime.martial_world.independent_people import independent_person, set_independent_person


def person_route(repository: Any, person_ref: str) -> tuple[str, int]:
    bucket = hashlib.sha256(person_ref.encode("utf-8")).hexdigest()[:2]
    shard = repository.read_json(f"state/martial-world/person-routes/{bucket}.json")
    rows = shard.get("people") if isinstance(shard, Mapping) else None
    route = rows.get(person_ref) if isinstance(rows, Mapping) else None
    if not isinstance(route, list) or len(route) != 2:
        raise KeyError(person_ref)
    faction_ref, ordinal = route
    if not isinstance(faction_ref, str) or not faction_ref or isinstance(ordinal, bool) or not isinstance(ordinal, int):
        raise ValueError("jianghu person route invalid")
    return faction_ref, ordinal


def roster_person(repository: Any, person_ref: str) -> tuple[str, dict[str, Any], int, dict[str, Any]]:
    try:
        faction_ref, ordinal = person_route(repository, person_ref)
    except (FileNotFoundError, KeyError):
        try:
            return independent_person(repository, person_ref)
        except (FileNotFoundError, KeyError):
            return civic_person(repository, person_ref)
    path = roster_path(faction_ref)
    roster = copy.deepcopy(repository.read_json(path))
    people = roster.get("people") if isinstance(roster, Mapping) else None
    if not isinstance(people, list) or ordinal < 0 or ordinal >= len(people):
        raise ValueError("jianghu person roster invalid")
    raw_person = people[ordinal]
    if not isinstance(raw_person, Mapping) or raw_person.get("person_id") != person_ref:
        raise ValueError("jianghu person route identity mismatch")
    if roster.get("faction_ref") != faction_ref:
        raise ValueError("jianghu person roster faction invalid")
    _, faction = read_faction(repository, faction_ref)
    person = hydrate_person_state(
        raw_person,
        faction_ref=faction_ref,
        home_location=home_location_ref(faction),
        include_storage_defaults=True,
    )
    person = apply_institutional_training(person, faction=faction, roster_people=people)
    return path, roster, ordinal, person


def set_roster_person(roster: Mapping[str, Any], ordinal: int, person: Mapping[str, Any]) -> dict[str, Any]:
    if roster.get("schema") == "jianghu-civic-people-state-1.0":
        return set_civic_person(roster, ordinal, person)
    if roster.get("schema") == "jianghu-independent-people-state-1.0":
        return set_independent_person(roster, ordinal, person)
    out = copy.deepcopy(dict(roster))
    people = out.get("people")
    if not isinstance(people, list) or ordinal < 0 or ordinal >= len(people):
        raise ValueError("jianghu roster ordinal invalid")
    faction_ref = out.get("faction_ref")
    if not isinstance(faction_ref, str) or not faction_ref:
        raise ValueError("jianghu roster faction invalid")
    people[ordinal] = compact_person_state(person, faction_ref=faction_ref)
    return out


def age_at_year(person: Mapping[str, Any], year: int) -> int:
    birth = int(person.get("birth_year", year))
    return max(0, int(year) - birth)


def player_view_from_person(person: Mapping[str, Any]) -> dict[str, Any]:
    """Build the bounded player view from the same authoritative person owner."""
    return {
        "person_id": person.get("person_id"),
        "name": person.get("name"),
        "official_rank_or_status": person.get("membership_grade"),
        "current_location_id": person.get("location_ref"),
        "condition": copy.deepcopy(person.get("health", {})),
        "attributes": copy.deepcopy(person.get("attributes", {})),
        "martial_skills": copy.deepcopy(person.get("martial_skills", {})),
        "professional_skills": copy.deepcopy(person.get("professional_skills", {})),
        "aptitudes": copy.deepcopy(person.get("aptitudes", {})),
        "appearance": int(person.get("appearance", 0)),
        "body_mass_kg": int(person.get("body_mass_kg", 70)),
        "qi": int(person.get("qi", 0)),
        "qi_control": int(person.get("qi_control", 0)),
        "current_qi": int(person.get("current_qi", person.get("qi", 0))),
        "personal_cash": int(person.get("personal_cash", 0)),
        "faction_ref": person.get("faction_ref"),
        "affiliation_ref": person.get("affiliation_ref"),
        "social_rank": person.get("social_rank"),
        "standing_offices": copy.deepcopy(person.get("standing_offices", [])),
        "standing_retinues": copy.deepcopy(person.get("standing_retinues", [])),
        "combat_doctrine_ref": person.get("combat_doctrine_ref"),
    }


__all__ = ["age_at_year", "person_route", "player_view_from_person", "roster_person", "set_roster_person"]
