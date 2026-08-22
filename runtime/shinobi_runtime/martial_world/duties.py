"""Deterministic rotating faction duties orthogonal to martial membership grade.

Every person on a martial-faction roster is a faction member.  ``membership_grade``
is rank only.  Routine work such as kitchens, records, medicine, workshops and
transport is represented by one current ``standing_duty_ref`` assignment.  The
assignment is periodically re-derived from real institutional need, current
capability and availability so it cannot become a twenty-year caste label.

Only the current assignment persists.  Rotation history and daily schedules do
not.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from .faction_state import resolved_faction_type
from .health import functional_capacity_factors, functional_penalties
from .infrastructure import (
    administrative_workload_units,
    enterprise_scale_value,
    facility_physical_effects,
    infirmary_capacity,
    transport_yard_capacity,
    workshop_capacity,
)
from .manpower import age_at_year, combat_readiness_score, is_faction_member, is_living_and_conscious

_MW = Path(__file__).resolve().parents[3] / "game" / "data" / "martial-world"
_GRADE_INDEX = {"probationary": 0, "junior": 1, "full": 2, "senior": 3, "elite": 4, "elder": 5}
_HIGH_OFFICES = frozenset({
    "leader", "deputy_leader", "senior_elder", "elder", "field_commander",
    "deputy_field_commander", "chief_martial_instructor", "discipline_instructor",
    "scout_leader", "chief_steward", "treasurer", "quartermaster",
})
_OFFICE_SKILL = {
    "chief_physician": "medicine",
    "chief_apothecary": "medicine",
    "master_weaponsmith": "crafting",
    "archivist": "administration",
    "chief_steward": "administration",
    "quartermaster": "administration",
    "treasurer": "commerce",
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


def duty_time_share_milli(person: Mapping[str, Any]) -> int:
    row = duty_definition(person.get("standing_duty_ref"))
    return max(0, min(800, int(row.get("time_share_milli", 0)))) if row is not None else 0


def duty_training_availability_milli(person: Mapping[str, Any]) -> int:
    return max(0, 1000 - duty_time_share_milli(person))


def _ceil_div(value: int, divisor: int) -> int:
    return 0 if value <= 0 else (value + divisor - 1) // divisor


def duty_staffing_requirements(faction: Mapping[str, Any]) -> dict[str, int]:
    """Return current routine staffing need from real faction scale.

    These are *people required to keep the institution routinely staffed*, not
    free workers granted by a building level.  Production projects and contracts
    may still reserve additional exact people through commitments.
    """
    pop = max(0, int(faction.get("population", faction.get("exact_population", 0))))
    buildings = faction.get("buildings", {}) if isinstance(faction.get("buildings"), Mapping) else {}
    infra = faction.get("infrastructure", {}) if isinstance(faction.get("infrastructure"), Mapping) else {}
    enterprises = faction.get("enterprises", {}) if isinstance(faction.get("enterprises"), Mapping) else {}
    req: dict[str, int] = {}

    if pop > 0 and int(buildings.get("residential_compound", 0)) > 0:
        req["kitchen_service"] = max(1, _ceil_div(pop, 40))
        req["general_household_service"] = max(1, _ceil_div(pop, 55))

    if int(buildings.get("main_hall", 0)) > 0 or int(buildings.get("library_records", 0)) > 0:
        active_enterprises = sum(1 for level in enterprises.values() if isinstance(level, int) and not isinstance(level, bool) and level > 0)
        agriculture_scale = enterprise_scale_value(faction, "agriculture_landholding") if int(enterprises.get("agriculture_landholding", 0)) > 0 else 0
        landholding_units = _ceil_div(agriculture_scale, 50)
        workload = administrative_workload_units(
            population=pop,
            active_enterprises=active_enterprises,
            landholding_units=landholding_units,
            active_contracts=0,
            active_projects=0,
            external_holdings=1 if agriculture_scale > 0 else 0,
        )
        hall = facility_physical_effects(buildings, infra, "main_hall")
        stations = max(0, int(hall.get("administrative_workstations", 0)))
        if stations > 0:
            req["records_administration"] = min(stations, max(1, _ceil_div(workload, 12)))

    if int(enterprises.get("trade_merchant_business", 0)) > 0:
        capital = enterprise_scale_value(faction, "trade_merchant_business")
        req["trade_service"] = max(1, min(max(1, pop // 5), _ceil_div(max(1, capital), 200_000)))

    # Brotherhood/society factions are explicitly authored around dues,
    # pooled labour, and brokerage.  Several of the smaller societies have no
    # separate merchant enterprise because the *membership itself* is the
    # livelihood (porters, stonecutters, woodcutters, ferry hands, etc.).  Give
    # that livelihood real people and real time instead of treating it as free
    # passive income.  The already-registered trade_service duty is the right
    # current-state representation: it requires Commerce competence and costs
    # 42% of institutional training time for each assigned member.
    active_enterprises = sum(
        1 for level in enterprises.values()
        if isinstance(level, int) and not isinstance(level, bool) and level > 0
    )
    if resolved_faction_type(faction) == "brotherhood_society" and active_enterprises == 0 and pop > 0:
        pooled_workers = max(1, _ceil_div(pop, 3))
        req["trade_service"] = max(req.get("trade_service", 0), pooled_workers)

    # A criminal enterprise likewise needs actual operators for fencing,
    # smuggling, collections, and arranging illicit ventures.  Route raids use
    # separate exact commitments; this standing duty represents the routine
    # economic work that makes a registered criminal cell an operating
    # enterprise rather than a decorative level number.
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

    if int(buildings.get("storehouse", 0)) > 0 and pop > 0:
        req["storehouse_logistics"] = max(1, _ceil_div(pop, 60))

    if int(buildings.get("transport_yard", 0)) > 0:
        cap = transport_yard_capacity(buildings, infra)
        transport_units = max(0, int(cap.get("mount_or_pack_slots", 0))) + max(0, int(cap.get("wagon_slots", 0))) * 4
        if transport_units > 0:
            req["transport_stable_service"] = max(1, min(max(1, pop // 5), _ceil_div(transport_units, 60)))

    return {key: max(0, int(value)) for key, value in req.items() if int(value) > 0}


def _office_names(person: Mapping[str, Any]) -> set[str]:
    rows = person.get("standing_offices", []) if isinstance(person.get("standing_offices"), list) else []
    return {str(value).split(":", 1)[0] for value in rows if isinstance(value, str)}


def _stable_noise(*, faction_ref: str, duty_ref: str, person_ref: str, year: int, month: int, period_months: int, span: int) -> int:
    index = year * 12 + max(0, month - 1)
    bucket = index // max(1, period_months)
    digest = hashlib.sha256(f"{faction_ref}|{duty_ref}|{bucket}|{person_ref}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % max(1, span)


def _duty_function_factor(person: Mapping[str, Any], duty_ref: str, skill_key: str | None) -> int:
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    wounds = health.get("injuries", []) if isinstance(health.get("injuries"), list) else []
    rows = [row for row in wounds if isinstance(row, Mapping)]
    capacity = functional_capacity_factors(rows)
    fine_manual = int(capacity.get("fine_manual_milli", 1000))
    manual = int(capacity.get("manual_milli", 1000))
    vision = int(capacity.get("vision_milli", 1000))
    standing = int(capacity.get("standing_milli", 1000))
    mounted_stability = int(capacity.get("mounted_stability_milli", 1000))
    field = int(capacity.get("field_mobility_milli", 1000))
    labor = int(capacity.get("labor_milli", 1000))
    general = int(capacity.get("general_work_milli", 1000))
    if skill_key in {"medicine", "crafting"}:
        return max(0, min(1000, (fine_manual * 75 + vision * 25) // 100))
    if duty_ref == "stable_service":
        return max(0, min(1000, (mounted_stability * 45 + labor * 35 + manual * 20) // 100))
    if duty_ref == "transport_service":
        return max(0, min(1000, (mounted_stability * 35 + field * 25 + labor * 25 + manual * 15) // 100))
    if duty_ref == "security_service":
        return max(0, min(1000, (field * 60 + standing * 25 + vision * 15) // 100))
    if duty_ref == "field_labor":
        return max(0, min(1000, labor))
    if duty_ref in {"kitchen_service", "storehouse_service"}:
        return max(0, min(1000, (manual * 45 + standing * 25 + general * 20 + vision * 10) // 100))
    if skill_key in {"administration", "commerce", "instruction"}:
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
    period = max(1, int(row.get("rotation_period_months", 3)))
    specialist = isinstance(skill_key, str) and bool(skill_key)
    function_factor = _duty_function_factor(person, duty_ref, str(skill_key) if specialist else None)
    if function_factor <= 100:
        return None

    if specialist:
        skill = max(0, int(prof.get(str(skill_key), 0)))
        minimum_skill = max(0, int(row.get("minimum_professional_skill", 10)))
        if skill < minimum_skill:
            return None
        intelligence = max(0, int(attrs.get("intelligence", 0)))
        dexterity = max(0, int(attrs.get("dexterity", 0)))
        relevant_attr = dexterity if skill_key in {"medicine", "crafting"} else intelligence
        score = skill * 14 + relevant_attr * 3 + min(200, grade_index * 25)
        # An office aligned with the same profession is evidence that this is a
        # deliberate specialist responsibility, not a random chore.
        for office in offices:
            if _OFFICE_SKILL.get(office) == skill_key:
                score += 650
        # Small annual tie-break variation prevents permanent arbitrary ties,
        # while competence dominates so the best physician is not rotated out
        # just for novelty.
        score += _stable_noise(
            faction_ref=faction_ref, duty_ref=duty_ref, person_ref=ref,
            year=year, month=month, period_months=period, span=90,
        )
        return score * function_factor // 1000

    # Mundane/general work deliberately prefers lower-grade members who are not
    # currently valuable combat manpower.  As somebody becomes a senior/elite
    # fighter their readiness and grade naturally push them out of kitchen,
    # stable, storehouse and generic household rotations.
    if offices & _HIGH_OFFICES:
        return None
    attrs_avg = (
        max(0, int(attrs.get("strength", 0)))
        + max(0, int(attrs.get("dexterity", 0)))
        + max(0, int(attrs.get("endurance", 0)))
    ) // 3
    readiness = combat_readiness_score(person, year=year)
    score = 900 + attrs_avg * 2 - grade_index * 120 - readiness * 5
    score += _stable_noise(
        faction_ref=faction_ref, duty_ref=duty_ref, person_ref=ref,
        year=year, month=month, period_months=period, span=420,
    )
    return score * function_factor // 1000


def reassign_standing_duties(
    faction: Mapping[str, Any], people: Sequence[Mapping[str, Any]], *, year: int, month: int = 1,
    unavailable_refs: Sequence[str] = (), protected_refs: Sequence[str] = ("pc_wei_tang",),
) -> dict[str, Any]:
    """Recompute current routine assignments without storing schedule history.

    Skilled duties are allocated first so a scarce physician or craftsman is not
    consumed by generic chores.  Each person receives at most one standing duty.
    Temporary deployments/projects remain separate commitment owners and simply
    make a person ineligible for reassignment while unavailable.
    """
    faction_ref = str(faction.get("faction_id") or "")
    unavailable = {str(x) for x in unavailable_refs}
    protected = {str(x) for x in protected_refs}
    requirements = duty_staffing_requirements(faction)
    assigned: dict[str, str] = {}
    counts: dict[str, int] = {}
    shortages: dict[str, int] = {}

    # Exact mutable row copies are produced only once.  No duty history is kept.
    rows = [dict(p) if isinstance(p, Mapping) else p for p in people]
    by_ref = {
        str(p.get("person_id")): p for p in rows
        if isinstance(p, Mapping) and isinstance(p.get("person_id"), str)
    }

    duty_order = sorted(
        requirements,
        key=lambda ref: (
            0 if isinstance(duty_definition(ref).get("professional_skill") if duty_definition(ref) else None, str) else 1,
            ref,
        ),
    )
    used: set[str] = set()
    for duty_ref in duty_order:
        need = max(0, int(requirements[duty_ref]))
        candidates: list[tuple[int, str]] = []
        for ref, person in by_ref.items():
            if ref in used or ref in unavailable or ref in protected:
                continue
            score = _candidate_score(person, faction_ref=faction_ref, duty_ref=duty_ref, year=year, month=month)
            if score is None:
                continue
            candidates.append((-score, ref))
        candidates.sort()
        selected = [ref for _score, ref in candidates[:need]]
        for ref in selected:
            assigned[ref] = duty_ref
            used.add(ref)
        counts[duty_ref] = len(selected)
        if len(selected) < need:
            shortages[duty_ref] = need - len(selected)

    changes: list[dict[str, str | None]] = []
    for person in rows:
        if not isinstance(person, dict) or not isinstance(person.get("person_id"), str):
            continue
        ref = str(person["person_id"])
        old = person.get("standing_duty_ref") if isinstance(person.get("standing_duty_ref"), str) else None
        # A currently unavailable person keeps their current assignment.  Their
        # temporary commitment owns the absence, and reassignment waits until
        # they return instead of silently changing their job mid-deployment.
        if ref in unavailable:
            continue
        new = assigned.get(ref)
        if new is None:
            person.pop("standing_duty_ref", None)
        else:
            person["standing_duty_ref"] = new
        if old != new:
            changes.append({"person_ref": ref, "from": old, "to": new})

    return {
        "people_after": rows,
        "requirements": requirements,
        "assigned_counts": counts,
        "shortages": shortages,
        "changes": changes,
    }


__all__ = [
    "duty_catalog", "duty_definition", "duty_staffing_requirements",
    "duty_time_share_milli", "duty_training_availability_milli",
    "reassign_standing_duties",
]
