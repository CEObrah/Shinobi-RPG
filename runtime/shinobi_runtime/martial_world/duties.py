"""Derived faction work allocation.

Membership grade is rank, not employment. Generic institutional chores are
represented only as shared owner-level time overhead. Exact named workers are
derived for the few specialist functions where their current capability directly
changes a real output: trade, workshop production, and infirmary work. No duty
assignment is canonical save-state truth.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from .faction_state import resolved_faction_type
from .health import functional_capacity_factors
from .infrastructure import (
    administrative_workload_units,
    enterprise_scale_value,
    facility_physical_effects,
    infirmary_capacity,
    transport_yard_capacity,
    workshop_capacity,
)
from .manpower import age_at_year, is_faction_member, is_living_and_conscious
from .rule_catalogs import office_catalog

_MW = Path(__file__).resolve().parents[3] / "game" / "data" / "martial-world"
_GRADE_INDEX = {"probationary": 0, "junior": 1, "full": 2, "senior": 3, "elite": 4, "elder": 5}
_OFFICE_SKILL = {
    office: str(rule["relevant_skill"])
    for office, rule in office_catalog().items()
    if isinstance(rule.get("relevant_skill"), str) and rule.get("relevant_skill")
}


@lru_cache(maxsize=1)
def duty_catalog() -> Mapping[str, Any]:
    data = json.loads((_MW / "duty-roles.json").read_text(encoding="utf-8"))
    rows = data.get("duties", {}) if isinstance(data, Mapping) else {}
    return rows if isinstance(rows, Mapping) else {}


def duty_definition(duty_ref: str | None) -> Mapping[str, Any] | None:
    if not isinstance(duty_ref, str) or not duty_ref:
        return None
    row = duty_catalog().get(duty_ref)
    return row if isinstance(row, Mapping) else None


def _ceil_div(value: int, divisor: int) -> int:
    return 0 if value <= 0 else (value + divisor - 1) // divisor


def routine_service_overhead_milli(faction: Mapping[str, Any], *, living_population: int) -> int:
    """Derive routine institutional work as shared time overhead.

    Generic chores are real, but individual kitchen/stable/storehouse labels did
    not create distinct gameplay. Their total person-time is derived from the
    current institution and spread across the living membership instead of being
    persisted on arbitrary people.
    """
    pop = max(0, int(living_population))
    if pop <= 0:
        return 0
    buildings = faction.get("buildings", {}) if isinstance(faction.get("buildings"), Mapping) else {}
    infra = faction.get("infrastructure", {}) if isinstance(faction.get("infrastructure"), Mapping) else {}
    enterprises = faction.get("enterprises", {}) if isinstance(faction.get("enterprises"), Mapping) else {}
    person_milli = 0

    if int(buildings.get("residential_compound", 0)) > 0:
        person_milli += max(1, _ceil_div(pop, 40)) * 300
        person_milli += max(1, _ceil_div(pop, 55)) * 280

    if int(buildings.get("main_hall", 0)) > 0 or int(buildings.get("library_records", 0)) > 0:
        active_enterprises = sum(
            1 for level in enterprises.values()
            if isinstance(level, int) and not isinstance(level, bool) and level > 0
        )
        agriculture_scale = enterprise_scale_value(faction, "agriculture_landholding") if int(enterprises.get("agriculture_landholding", 0)) > 0 else 0
        workload = administrative_workload_units(
            population=pop,
            active_enterprises=active_enterprises,
            landholding_units=_ceil_div(agriculture_scale, 50),
            active_contracts=0,
            active_projects=0,
            external_holdings=1 if agriculture_scale > 0 else 0,
        )
        hall = facility_physical_effects(buildings, infra, "main_hall")
        stations = max(0, int(hall.get("administrative_workstations", 0)))
        if stations > 0:
            person_milli += min(stations, max(1, _ceil_div(workload, 12))) * 400

    if int(buildings.get("storehouse", 0)) > 0:
        person_milli += max(1, _ceil_div(pop, 60)) * 340

    if int(buildings.get("transport_yard", 0)) > 0:
        cap = transport_yard_capacity(buildings, infra)
        transport_units = max(0, int(cap.get("mount_or_pack_slots", 0))) + max(0, int(cap.get("wagon_slots", 0))) * 4
        if transport_units > 0:
            handlers = max(1, min(max(1, pop // 5), _ceil_div(transport_units, 60)))
            person_milli += handlers * 340

    return max(0, min(350, person_milli // pop))


def duty_staffing_requirements(faction: Mapping[str, Any], *, living_population: int | None = None) -> dict[str, int]:
    """Return only specialist assignments whose exact worker matters.

    Trade, workshop and infirmary output depends on the capabilities of the
    people actually doing the work. Generic institutional service is handled by
    :func:`routine_service_overhead_milli` and is deliberately not a person role.
    """
    pop = max(0, int(living_population if living_population is not None else faction.get("population", faction.get("exact_population", 0))))
    buildings = faction.get("buildings", {}) if isinstance(faction.get("buildings"), Mapping) else {}
    infra = faction.get("infrastructure", {}) if isinstance(faction.get("infrastructure"), Mapping) else {}
    enterprises = faction.get("enterprises", {}) if isinstance(faction.get("enterprises"), Mapping) else {}
    req: dict[str, int] = {}

    if int(enterprises.get("trade_merchant_business", 0)) > 0:
        capital = enterprise_scale_value(faction, "trade_merchant_business")
        req["trade_service"] = max(1, min(max(1, pop // 5), _ceil_div(max(1, capital), 200_000)))

    active_enterprises = sum(
        1 for level in enterprises.values()
        if isinstance(level, int) and not isinstance(level, bool) and level > 0
    )
    if resolved_faction_type(faction) == "brotherhood_society" and active_enterprises == 0 and pop > 0:
        req["trade_service"] = max(req.get("trade_service", 0), max(1, _ceil_div(pop, 3)))

    criminal_level = max(0, int(enterprises.get("criminal_enterprise", 0)))
    if criminal_level > 0:
        cells = max(0, enterprise_scale_value(faction, "criminal_enterprise"))
        if cells > 0:
            req["trade_service"] = max(req.get("trade_service", 0), min(cells, max(1, pop // 3)))

    if int(buildings.get("armory_workshop", 0)) > 0:
        stations = max(0, int(workshop_capacity(buildings, infra).get("craft_workstations", 0)))
        operating = enterprise_scale_value(faction, "crafting_workshop") if int(enterprises.get("crafting_workshop", 0)) > 0 else 0
        basis = min(stations, operating) if operating > 0 else stations
        if basis > 0:
            req["workshop_service"] = max(1, min(basis, _ceil_div(basis, 5)))

    if int(buildings.get("infirmary_apothecary", 0)) > 0:
        infirmary = infirmary_capacity(buildings, infra)
        stations = max(0, int(infirmary.get("treatment_stations", 0)) + int(infirmary.get("apothecary_workstations", 0)))
        operating = enterprise_scale_value(faction, "medicine_apothecary") if int(enterprises.get("medicine_apothecary", 0)) > 0 else 0
        basis = min(stations, operating) if operating > 0 else stations
        if basis > 0:
            req["infirmary_service"] = max(1, min(basis, _ceil_div(basis, 5)))

    return {key: max(0, int(value)) for key, value in req.items() if int(value) > 0}

def _office_names(person: Mapping[str, Any]) -> set[str]:
    rows = person.get("standing_offices", []) if isinstance(person.get("standing_offices"), list) else []
    return {str(value).split(":", 1)[0] for value in rows if isinstance(value, str)}


def _stable_noise(*, faction_ref: str, duty_ref: str, person_ref: str, year: int, month: int, period_months: int, span: int) -> int:
    index = year * 12 + max(0, month - 1)
    bucket = index // max(1, period_months)
    digest = hashlib.sha256(f"{faction_ref}|{duty_ref}|{bucket}|{person_ref}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % max(1, span)


def _duty_function_factor(person: Mapping[str, Any], skill_key: str) -> int:
    """Return bodily capacity for the three real specialist work families."""
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    wounds = health.get("injuries", []) if isinstance(health.get("injuries"), list) else []
    capacity = functional_capacity_factors([row for row in wounds if isinstance(row, Mapping)])
    fine_manual = int(capacity.get("fine_manual_milli", 1000))
    vision = int(capacity.get("vision_milli", 1000))
    general = int(capacity.get("general_work_milli", 1000))
    if skill_key in {"medicine", "crafting"}:
        return max(0, min(1000, (fine_manual * 75 + vision * 25) // 100))
    if skill_key == "commerce":
        return max(0, min(1000, (general * 70 + vision * 30) // 100))
    return max(0, min(1000, general))

def _candidate_score(
    person: Mapping[str, Any], *, faction_ref: str, duty_ref: str, year: int, month: int,
) -> int | None:
    row = duty_definition(duty_ref)
    if row is None or not is_faction_member(person) or not is_living_and_conscious(person) or bool(person.get("retired_from_field", False)):
        return None
    ref = str(person.get("person_id") or "")
    if not ref:
        return None
    age = age_at_year(person, year)
    minimum_age = max(0, int(row.get("minimum_age", 12)))
    if age < minimum_age:
        return None

    grade = str(person.get("membership_grade") or "probationary")
    grade_index = _GRADE_INDEX.get(grade, 0)
    offices = _office_names(person)
    prof = person.get("professional_skills", {}) if isinstance(person.get("professional_skills"), Mapping) else {}
    attrs = person.get("attributes", {}) if isinstance(person.get("attributes"), Mapping) else {}
    skill_key = row.get("professional_skill")
    if not isinstance(skill_key, str) or not skill_key:
        return None
    period = max(1, int(row.get("rotation_period_months", 12)))
    function_factor = _duty_function_factor(person, skill_key)
    if function_factor <= 100:
        return None
    skill = max(0, int(prof.get(skill_key, 0)))
    minimum_skill = max(0, int(row.get("minimum_professional_skill", 10)))
    if skill < minimum_skill:
        return None
    intelligence = max(0, int(attrs.get("intelligence", 0)))
    dexterity = max(0, int(attrs.get("dexterity", 0)))
    relevant_attr = dexterity if skill_key in {"medicine", "crafting"} else intelligence
    score = skill * 14 + relevant_attr * 3 + min(200, grade_index * 25)
    for office in offices:
        if _OFFICE_SKILL.get(office) == skill_key:
            score += 650
    score += _stable_noise(
        faction_ref=faction_ref, duty_ref=duty_ref, person_ref=ref,
        year=year, month=month, period_months=period, span=90,
    )
    return score * function_factor // 1000


def derive_duty_assignments(
    faction: Mapping[str, Any], people: Sequence[Mapping[str, Any]], *, year: int, month: int = 1,
    unavailable_refs: Sequence[str] = (), protected_refs: Sequence[str] = ("pc_wei_tang",),
) -> dict[str, Any]:
    """Derive current exact work assignments without creating save-state roles.

    The assignment is a calculation over real faction scale and real people. It
    exists only for the settlement being resolved. Projects, escorts and other
    finite activities remain separate real owners and their people are excluded.
    """
    faction_ref = str(faction.get("faction_id") or "")
    unavailable = {str(x) for x in unavailable_refs}
    protected = {str(x) for x in protected_refs}
    by_ref = {
        str(p.get("person_id")): p for p in people
        if isinstance(p, Mapping) and isinstance(p.get("person_id"), str)
    }
    # A paused institutional-training state is already proof that a real finite
    # activity owns the person's time. Do not assign them to routine work too.
    for ref, person in by_ref.items():
        tstate = person.get("training_state", {}) if isinstance(person.get("training_state"), Mapping) else {}
        if tstate.get("institutional_paused") is True:
            unavailable.add(ref)

    living_population = sum(1 for p in people if isinstance(p, Mapping) and is_faction_member(p) and is_living_and_conscious(p))
    requirements = duty_staffing_requirements(faction, living_population=living_population)
    assigned: dict[str, str] = {}
    counts: dict[str, int] = {}
    shortages: dict[str, int] = {}
    used: set[str] = set()
    duty_order = sorted(
        requirements,
        key=lambda ref: (
            0 if isinstance(duty_definition(ref).get("professional_skill") if duty_definition(ref) else None, str) else 1,
            ref,
        ),
    )
    for duty_ref in duty_order:
        need = max(0, int(requirements[duty_ref]))
        candidates: list[tuple[int, str]] = []
        for ref, person in by_ref.items():
            if ref in used or ref in unavailable or ref in protected:
                continue
            score = _candidate_score(person, faction_ref=faction_ref, duty_ref=duty_ref, year=year, month=month)
            if score is not None:
                candidates.append((-score, ref))
        candidates.sort()
        selected = [ref for _score, ref in candidates[:need]]
        for ref in selected:
            assigned[ref] = duty_ref
            used.add(ref)
        counts[duty_ref] = len(selected)
        if len(selected) < need:
            shortages[duty_ref] = need - len(selected)
    return {
        "assignments": assigned,
        "requirements": requirements,
        "assigned_counts": counts,
        "shortages": shortages,
    }


def duty_time_share_for_ref(duty_ref: str | None) -> int:
    row = duty_definition(duty_ref)
    return max(0, min(800, int(row.get("time_share_milli", 0)))) if row is not None else 0


__all__ = [
    "derive_duty_assignments", "duty_catalog", "duty_definition", "duty_staffing_requirements",
    "duty_time_share_for_ref", "routine_service_overhead_milli",
]
