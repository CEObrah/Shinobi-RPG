"""Sparse deterministic living-world health and turnover helpers.

These helpers deliberately derive pressure from current conserved facts.  They do
not maintain a parallel simulation ledger.  Exact people are moved only when an
individual consequence matters; ordinary civilian demography remains aggregate.
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from .life_course import effective_cultivation
from .manpower import age_at_year, is_faction_member, is_living_and_conscious


def living_member_count(people: Sequence[Mapping[str, Any]]) -> int:
    count = 0
    for p in people:
        if not isinstance(p, Mapping) or not is_faction_member(p):
            continue
        health = p.get("health", {}) if isinstance(p.get("health"), Mapping) else {}
        if health.get("status") != "dead":
            count += 1
    return count


def institutional_stress_milli(*, food_due: int, food_paid: int, cash_due: int, cash_paid: int,
                                stipend_due: int = 0, stipend_paid: int = 0) -> int:
    """0=healthy, 1000=severe current institutional crisis."""
    ratios: list[int] = []
    if food_due > 0:
        ratios.append(max(0, min(1000, 1000 - max(0, food_paid) * 1000 // food_due)))
    if cash_due > 0:
        ratios.append(max(0, min(1000, 1000 - max(0, cash_paid) * 1000 // cash_due)))
    if stipend_due > 0:
        ratios.append(max(0, min(1000, 1000 - max(0, stipend_paid) * 1000 // stipend_due)))
    return max(ratios, default=0)


def training_intensity_for_stress(stress_milli: int) -> int:
    stress = max(0, min(1000, int(stress_milli)))
    if stress >= 750:
        return 350
    if stress >= 400:
        return 600
    if stress >= 150:
        return 800
    return 1000


def sustainable_recruitment_gap(
    faction: Mapping[str, Any], *, living_population: int, residential_capacity: int,
    food_reserve_days: int, cash_reserve_months: int,
) -> int:
    """Allow replacement plus bounded organic growth when the institution can support it."""
    policy = faction.get("recruitment_policy", {}) if isinstance(faction.get("recruitment_policy"), Mapping) else {}
    baseline = max(0, int(policy.get("target_membership", living_population)))
    cap = max(0, int(residential_capacity))
    if cap <= 0:
        cap = max(baseline, living_population)
    replacement = max(0, baseline - living_population)
    if food_reserve_days < 45 or cash_reserve_months < 2:
        return replacement
    # A healthy institution may deliberately grow, but never fill all housing at
    # once.  The 90% occupancy ceiling leaves guest/emergency capacity.
    sustainable_cap = max(baseline, cap * 9 // 10)
    priority = max(0, min(100, int((faction.get("autonomy_policy") or {}).get("recruitment_priority", 50)))) if isinstance(faction.get("autonomy_policy"), Mapping) else 50
    annual_growth_headroom = max(1, living_population * (10 + priority // 2) // 1000)  # ~1-6%/yr
    desired = min(sustainable_cap, max(baseline, living_population + annual_growth_headroom))
    return max(replacement, desired - living_population)


def _stable_permille(key: str) -> int:
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big") % 1000


def retirement_due(person: Mapping[str, Any], *, year: int) -> bool:
    if not is_faction_member(person) or not is_living_and_conscious(person):
        return False
    age = age_at_year(person, year)
    cultivation = effective_cultivation(max(0, int(person.get("qi", 0))), max(0, int(person.get("qi_control", 0))))
    threshold = min(82, 58 + int(cultivation // 12))
    return age >= threshold


def annual_voluntary_departure_refs(
    people: Sequence[Mapping[str, Any]], *, faction_ref: str, year: int,
    hardship_milli: int = 0, protected_refs: Sequence[str] = (), maximum: int | None = None,
    period_key: str | None = None,
) -> list[str]:
    """Bounded normal churn plus crisis-driven departures from exact current members."""
    protected = {str(x) for x in protected_refs if isinstance(x, str)}
    hardship = max(0, min(1000, int(hardship_milli)))
    rows: list[tuple[int, str]] = []
    for p in people:
        if not isinstance(p, Mapping) or not is_faction_member(p) or not is_living_and_conscious(p):
            continue
        ref = p.get("person_id")
        if not isinstance(ref, str) or ref in protected:
            continue
        if p.get("standing_offices") or p.get("retired_from_field"):
            continue
        grade = str(p.get("membership_grade") or "probationary")
        if grade in {"elite", "elder"}:
            continue
        age = age_at_year(p, year)
        if age < 16:
            continue
        # About 0.35% baseline annual churn, rising to several percent in a
        # severe sustained crisis. Probationary/junior members are more mobile.
        base = 3 if grade in {"full", "senior"} else 5
        threshold = min(80, base + hardship * 65 // 1000)
        roll = _stable_permille(f"leave|{faction_ref}|{period_key or year}|{ref}")
        if roll < threshold:
            rows.append((roll, ref))
    rows.sort()
    limit = max(1, len(people) // 50) if maximum is None else max(0, int(maximum))
    return [ref for _roll, ref in rows[:limit]]


def civilian_annual_demography(place_ref: str, population: int, *, year: int) -> dict[str, int]:
    """Compact aggregate birth/death/migration for ordinary settlement populations."""
    pop = max(0, int(population))
    if pop <= 0:
        return {"births": 0, "deaths": 0, "net_migration": 0, "population_after": 0}
    # Pre-modern turnover is high even where net growth is modest. Stable local
    # variation prevents every settlement from sharing one demographic rate.
    birth_permille = 24 + _stable_permille(f"civ-birth|{place_ref}|{year}") % 9   # 24-32/1000
    death_permille = 20 + _stable_permille(f"civ-death|{place_ref}|{year}") % 9   # 20-28/1000
    migration_permille = (_stable_permille(f"civ-move|{place_ref}|{year}") % 9) - 4
    births = pop * birth_permille // 1000
    deaths = min(pop + births, pop * death_permille // 1000)
    migration = pop * abs(migration_permille) // 1000
    net_migration = migration if migration_permille >= 0 else -min(pop + births - deaths, migration)
    after = max(0, pop + births - deaths + net_migration)
    return {"births": births, "deaths": deaths, "net_migration": net_migration, "population_after": after}


__all__ = [
    "annual_voluntary_departure_refs", "civilian_annual_demography", "institutional_stress_milli",
    "living_member_count", "retirement_due", "sustainable_recruitment_gap", "training_intensity_for_stress",
]
