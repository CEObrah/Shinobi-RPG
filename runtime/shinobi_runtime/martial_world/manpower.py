"""Derived Jianghu faction manpower.

Every persistent faction person is a martial-faction member. Membership grade is
rank only; it is never a proxy for current deployability. Combat manpower is a
read-time deterministic projection from the exact roster: age/development,
health, current capability and availability all matter.
"""
from __future__ import annotations
from typing import Any, Mapping, Sequence

from .health import functional_capacity_factors, functional_penalties

MEMBERSHIP_GRADES = frozenset({"probationary", "junior", "full", "senior", "elite", "elder"})
COMBAT_DISCIPLINES = ("sword", "spear", "bow", "hidden_weapons", "unarmed")


def is_faction_member(person: Mapping[str, Any]) -> bool:
    return str(person.get("membership_grade") or "") in MEMBERSHIP_GRADES


def age_at_year(person: Mapping[str, Any], year: int) -> int:
    try:
        return max(0, int(year) - int(person.get("birth_year", year)))
    except (TypeError, ValueError):
        return 0


def is_living_and_conscious(person: Mapping[str, Any]) -> bool:
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    return health.get("status") not in {"dead", "incapacitated"} and int(health.get("consciousness", 100)) > 0


def combat_skill_peak(person: Mapping[str, Any]) -> int:
    skills = person.get("martial_skills", {}) if isinstance(person.get("martial_skills"), Mapping) else {}
    return max((max(0, int(skills.get(key, 0))) for key in COMBAT_DISCIPLINES), default=0)


def combat_readiness_score(person: Mapping[str, Any], *, year: int) -> int:
    """Current physical/martial readiness score, not rank or permanent power."""
    if not is_faction_member(person) or not is_living_and_conscious(person) or bool(person.get("retired_from_field", False)):
        return 0
    age = age_at_year(person, year)
    if age < 12:
        return 0
    skills = person.get("martial_skills", {}) if isinstance(person.get("martial_skills"), Mapping) else {}
    attrs = person.get("attributes", {}) if isinstance(person.get("attributes"), Mapping) else {}
    combat = combat_skill_peak(person)
    field = max(0, int(skills.get("stealth_scouting", 0)))
    physical = (
        max(0, int(attrs.get("strength", 0)))
        + max(0, int(attrs.get("speed", 0)))
        + max(0, int(attrs.get("dexterity", 0)))
        + max(0, int(attrs.get("endurance", 0)))
        + max(0, int(attrs.get("perception", 0)))
    ) // 5
    age_factor = 700 if age < 14 else 850 if age < 16 else 1000
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    fatigue = max(0, int(person.get("fatigue_milli", 0)))
    shock = max(0, int(health.get("shock", 0)))
    health_factor = max(200, 1000 - fatigue // 2 - shock * 3)
    wounds = health.get("injuries", []) if isinstance(health.get("injuries"), list) else []
    wound_rows = [row for row in wounds if isinstance(row, Mapping)]
    penalties = functional_penalties(wound_rows)
    capacity = functional_capacity_factors(wound_rows)
    # Field readiness uses the same derived bodily functions as travel, duties
    # and exact combat. Learned martial skill is unchanged; current ability to
    # perceive, move, stand and control a weapon determines deployable readiness.
    vision_factor = max(0, int(capacity.get("vision_milli", 1000)))
    mobility_factor = max(0, int(capacity.get("field_mobility_milli", 1000)))
    standing_factor = max(0, int(capacity.get("standing_milli", 1000)))
    control_loss = max(int(penalties.get("weapon_control", 0)), int(penalties.get("grip", 0)), int(penalties.get("arm", 0)))
    control_factor = max(0, 1000 - control_loss * 7)
    function_factor = max(50, (vision_factor * 15 + mobility_factor * 40 + standing_factor * 15 + control_factor * 30) // 100)
    score = (combat * 60 + field * 10 + physical * 30) // 100
    return max(0, score * age_factor // 1000 * health_factor // 1000 * function_factor // 1000)


def combat_eligible(
    person: Mapping[str, Any], *, year: int, unavailable_refs: set[str] | frozenset[str] = frozenset(),
    minimum_age: int = 14, minimum_combat_skill: int = 20,
) -> bool:
    ref = str(person.get("person_id") or "")
    if not ref or ref in unavailable_refs or not is_faction_member(person) or not is_living_and_conscious(person) or bool(person.get("retired_from_field", False)):
        return False
    if age_at_year(person, year) < max(0, int(minimum_age)):
        return False
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    wounds = health.get("injuries", []) if isinstance(health.get("injuries"), list) else []
    capacity = functional_capacity_factors([row for row in wounds if isinstance(row, Mapping)])
    # Ordinary autonomous field selection requires at least crude self-propelled
    # mobility. A unilateral amputation remains technically eligible but ranks
    # extremely poorly; bilateral non-mobility is not a lawful expedition choice.
    # Exact combat can still represent a trapped/crawling person when causality
    # puts them in a fight.
    if int(capacity.get("field_mobility_milli", 1000)) < 50:
        return False
    return combat_skill_peak(person) >= max(0, int(minimum_combat_skill))


def combat_ready_members(
    people: Sequence[Mapping[str, Any]], *, year: int, unavailable_refs: set[str] | frozenset[str] = frozenset(),
    minimum_age: int = 14, minimum_combat_skill: int = 20,
) -> list[Mapping[str, Any]]:
    unavailable = set(map(str, unavailable_refs))
    rows = [
        person for person in people
        if isinstance(person, Mapping)
        and combat_eligible(
            person, year=year, unavailable_refs=unavailable,
            minimum_age=minimum_age, minimum_combat_skill=minimum_combat_skill,
        )
    ]
    return sorted(rows, key=lambda p: (-combat_readiness_score(p, year=year), str(p.get("person_id") or "")))


def combat_ready_count(
    people: Sequence[Mapping[str, Any]], *, year: int, unavailable_refs: set[str] | frozenset[str] = frozenset(),
    minimum_age: int = 14, minimum_combat_skill: int = 20,
) -> int:
    return len(combat_ready_members(
        people, year=year, unavailable_refs=unavailable_refs,
        minimum_age=minimum_age, minimum_combat_skill=minimum_combat_skill,
    ))


__all__ = [
    "MEMBERSHIP_GRADES", "age_at_year", "combat_eligible", "combat_readiness_score",
    "combat_ready_count", "combat_ready_members", "combat_skill_peak", "is_faction_member",
    "is_living_and_conscious",
]
