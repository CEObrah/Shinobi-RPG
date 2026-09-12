"""Deterministic sparse Jianghu training progression.

The faction stores only the current training epoch clock and intensity. Current
facilities, curriculum, instructors, duty load, and roster are authoritative
owners and are derived when training is materialized. Per-person fractional
progress remains on the person because discarding it would change future growth.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from .infrastructure import training_domain_capacity, enterprise_scale_value
from .health import functional_capacity_factors, functional_penalties
from .security import institutional_guard_duty_milli
from .duties import derive_duty_assignments, duty_time_share_for_ref, routine_service_overhead_milli
from .manpower import combat_ready_count, is_faction_member
from .character_rules import attribute_keys, martial_discipline_keys, professional_skill_keys
from .rule_catalogs import development_rules
from .qi import person_current_qi_milli, set_person_current_qi_milli


def _clamp(low: int, high: int, value: int) -> int:
    return max(low, min(high, value))


def training_gain_milli(
    *,
    current_skill: int,
    aptitude: int,
    hours_milli: int,
    instructor_skill: int | None,
    instruction_skill: int = 0,
    facility_level: int = 0,
    health_milli: int = 1000,
    novelty_milli: int = 1000,
    recovery_milli: int = 1000,
) -> int:
    """Return development progress in thousandths of one capability point."""
    if hours_milli <= 0:
        return 0
    rules = development_rules().get("training_gain", {})
    cur = max(0, current_skill)
    apt = _clamp(0, 200, aptitude)
    facility_factors = tuple(int(x) for x in rules.get("facility_factor_milli_by_level", [700,850,1000,1100,1200,1300]))
    if len(facility_factors) != 6:
        raise ValueError("development facility factor registry invalid")
    facility = _clamp(0, len(facility_factors)-1, facility_level)
    quadratic = max(1, int(rules.get("aptitude_quadratic_divisor",100)))
    aptitude_factor = (
        int(rules.get("aptitude_baseline_milli",700))
        + int(rules.get("aptitude_linear_milli_per_point",2)) * apt
        + (apt * apt) // quadratic
    )
    facility_factor = facility_factors[facility]
    if instructor_skill is None:
        instructor_factor = int(rules.get("instructor_none_milli",850))
    else:
        gap = _clamp(int(rules.get("instructor_gap_min",-100)), int(rules.get("instructor_gap_max",150)), instructor_skill-cur)
        instructor_factor = _clamp(
            int(rules.get("instructor_factor_min_milli",700)), int(rules.get("instructor_factor_max_milli",1600)),
            int(rules.get("instructor_base_milli",1000)) + gap * int(rules.get("instructor_gap_milli_per_point",3))
            + max(0,instruction_skill) * int(rules.get("instruction_milli_per_point",2)),
        )
    health = _clamp(int(rules.get("health_min_milli",0)), int(rules.get("health_max_milli",1200)), health_milli)
    novelty = _clamp(int(rules.get("novelty_min_milli",0)), int(rules.get("novelty_max_milli",1400)), novelty_milli)
    recovery = _clamp(int(rules.get("recovery_min_milli",0)), int(rules.get("recovery_max_milli",1200)), recovery_milli)
    difficulty_milli = (int(rules.get("difficulty_base_milli",1000)) + int(rules.get("difficulty_linear_per_skill",12))*cur
        + (cur*cur)//max(1,int(rules.get("difficulty_quadratic_divisor",40))))
    numerator = hours_milli * int(rules.get("base_progress_rate",20)) * aptitude_factor * facility_factor * instructor_factor * health * novelty * recovery
    return max(0, numerator // (1000**6 * difficulty_milli))


def instructor_capacity(*, instruction_skill: int, facility_level: int, group_drill: bool) -> int:
    rules = development_rules().get("instructor_capacity", {})
    instruction=max(0,instruction_skill); facility=max(0,min(5,facility_level))
    if group_drill:
        return max(1,(int(rules.get("group_base",20))+instruction//max(1,int(rules.get("group_instruction_divisor",2))))*max(1,facility)//max(1,int(rules.get("group_facility_divisor",3))))
    return max(1,int(rules.get("individual_base",4))+instruction//max(1,int(rules.get("individual_instruction_divisor",20))))


MARTIAL_KEYS = martial_discipline_keys()
ATTRIBUTE_KEYS = attribute_keys()
PROFESSIONAL_KEYS = professional_skill_keys()
_ALL_TAUGHT_DOMAINS = MARTIAL_KEYS + ("qi", "qi_control") + PROFESSIONAL_KEYS
_FACILITY_FOR_DOMAIN = {
    "sword": "training_hall", "spear": "training_hall", "unarmed": "training_hall",
    "bow": "training_grounds", "hidden_weapons": "training_grounds", "stealth_scouting": "training_grounds", "command": "training_grounds",
    "qi": "qi_hall", "qi_control": "qi_hall",
    "medicine": "infirmary_apothecary", "crafting": "armory_workshop", "administration": "library_records",
    "commerce": "main_hall", "instruction": "training_hall",
}
_RELEVANT_FACILITIES = tuple(sorted(set(_FACILITY_FOR_DOMAIN.values()) | {"training_grounds", "training_hall"}))


def _health_factor(person: Mapping[str, Any]) -> int:
    health = person.get("health", {})
    if not isinstance(health, Mapping):
        return 1000
    if health.get("status") in {"dead", "incapacitated"} or int(health.get("consciousness", 100)) <= 0:
        return 0
    fatigue = max(0, int(person.get("fatigue_milli", 0)))
    shock = max(0, int(health.get("shock", 0)))
    return max(100, min(1000, 1000 - fatigue // 2 - shock * 3))


def _functional_training_factor(person: Mapping[str, Any], domain: str) -> int:
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    wounds = health.get("injuries", []) if isinstance(health.get("injuries"), list) else []
    rows = [row for row in wounds if isinstance(row, Mapping)]
    # Injury-derived functional capacity is the identity transform for a healthy
    # person. Monthly training evaluates this per curriculum domain, so the
    # zero-injury fast path removes repeated anatomy reductions without changing
    # any progression arithmetic or persistence semantics.
    if not rows:
        return 1000
    penalties = functional_penalties(rows)
    capacity = functional_capacity_factors(rows)
    vision = max(0, min(1000, int(capacity.get("vision_milli", 1000))))
    standing = max(0, min(1000, int(capacity.get("standing_milli", 1000))))
    running = max(0, min(1000, int(capacity.get("running_milli", 1000))))
    combat_move = max(0, min(1000, int(capacity.get("combat_movement_milli", 1000))))
    fine_manual = max(0, min(1000, int(capacity.get("fine_manual_milli", 1000))))
    respiratory = max(0, min(1000, int(capacity.get("respiratory_milli", 1000))))
    control_loss = max(int(penalties.get("weapon_control", 0)), int(penalties.get("grip", 0)), int(penalties.get("arm", 0)))
    weapon_control = max(0, 1000 - max(0, min(100, control_loss)) * 10)
    key = domain.split(":", 1)[-1]
    if key in {"sword", "spear", "hidden_weapons", "unarmed", "dexterity"}:
        factor = (weapon_control * 45 + combat_move * 25 + standing * 20 + vision * 10) // 100
    elif key == "bow":
        factor = (weapon_control * 45 + vision * 35 + standing * 20) // 100
    elif key in {"speed", "stealth_scouting"}:
        factor = (running * 55 + combat_move * 30 + respiratory * 15) // 100
    elif key in {"strength", "endurance"}:
        factor = (standing * 35 + running * 25 + respiratory * 40) // 100
    elif key in {"crafting", "medicine"}:
        factor = (fine_manual * 75 + vision * 25) // 100
    elif key == "perception":
        factor = vision
    elif key in {"qi", "qi_control"}:
        factor = 650 + respiratory * 35 // 100
    elif key in {"instruction", "administration", "commerce", "intelligence", "willpower", "command"}:
        factor = 850 + vision * 15 // 100
    else:
        factor = (weapon_control + combat_move + vision) // 3
    # Physical disability can make a physical discipline nearly unavailable, but
    # it never erases learned skill or unrelated intellectual development.
    return max(50, min(1000, int(factor)))


def _is_martial(person: Mapping[str, Any]) -> bool:
    return is_faction_member(person)


def _domain_value(person: Mapping[str, Any], domain: str) -> int:
    if domain in MARTIAL_KEYS:
        skills = person.get("martial_skills", {})
        return max(0, int(skills.get(domain, 0))) if isinstance(skills, Mapping) else 0
    if domain in PROFESSIONAL_KEYS:
        skills = person.get("professional_skills", {})
        return max(0, int(skills.get(domain, 0))) if isinstance(skills, Mapping) else 0
    return max(0, int(person.get(domain, 0)))


def _facility_level(facilities: Mapping[str, Any], domain: str) -> int:
    return max(0, min(5, int(facilities.get(_FACILITY_FOR_DOMAIN.get(domain, "training_hall"), 0))))


def training_epoch_elapsed_days(faction: Mapping[str, Any]) -> int:
    """Derive whole institutional-training days from the owner-local epoch clock."""
    epoch = faction.get("training_epoch", {})
    if not isinstance(epoch, Mapping):
        return 0
    started_raw = epoch.get("started_at")
    settled_raw = epoch.get("settled_through")
    if not isinstance(started_raw, str) or not isinstance(settled_raw, str):
        return 0
    try:
        started = datetime.fromisoformat(started_raw)
        settled = datetime.fromisoformat(settled_raw)
    except ValueError:
        return 0
    if settled <= started:
        return 0
    return max(0, int((settled - started).total_seconds()) // 86400)


def institutional_training_pause_refs(
    faction: Mapping[str, Any], people: Sequence[Mapping[str, Any]], *,
    unavailable_refs: Sequence[str] = (),
) -> list[str]:
    """Derive exact people who cannot participate in the home institution epoch.

    Finite activity owners provide ``unavailable_refs``. Physical absence is
    derived from the person's current hydrated location relative to the faction's
    registered training site. Neither condition is persistent person state.
    """
    paused = {str(ref) for ref in unavailable_refs if isinstance(ref, str) and ref}
    training_site = str(faction.get("local_site_ref") or "")
    if training_site:
        for raw in people:
            if not isinstance(raw, Mapping):
                continue
            ref = str(raw.get("person_id") or "")
            location = str(raw.get("location_ref") or "")
            if ref and location and location != training_site:
                paused.add(ref)
    return sorted(paused)


def _eligible_student_refs(people: Sequence[Mapping[str, Any]]) -> list[str]:
    refs = []
    for row in people:
        if not isinstance(row, Mapping) or not isinstance(row.get("person_id"), str):
            continue
        training_state = row.get("training_state", {}) if isinstance(row.get("training_state"), Mapping) else {}
        if (
            not _is_martial(row)
            or _health_factor(row) <= 0
            or bool(row.get("retired_from_field", False))
            or training_state.get("institutional_paused") is True
        ):
            continue
        refs.append(str(row["person_id"]))
    return sorted(set(refs))


def _instructor_candidates(
    people: Sequence[Mapping[str, Any]], *, domain: str, facility_level: int
) -> list[dict[str, int | str]]:
    rows: list[dict[str, int | str]] = []
    for row in people:
        if not isinstance(row, Mapping) or not isinstance(row.get("person_id"), str) or _health_factor(row) <= 0:
            continue
        training_state = row.get("training_state", {}) if isinstance(row.get("training_state"), Mapping) else {}
        if training_state.get("institutional_paused") is True or bool(row.get("retired_from_field", False)):
            continue
        if domain in MARTIAL_KEYS or domain in {"qi", "qi_control"}:
            if not _is_martial(row):
                continue
        prof = row.get("professional_skills", {}) if isinstance(row.get("professional_skills"), Mapping) else {}
        instruction = max(0, int(prof.get("instruction", 0)))
        value = _domain_value(row, domain)
        offices = {str(x).split(":", 1)[0] for x in row.get("standing_offices", []) if isinstance(x, str)}
        designated = bool(offices & {"chief_martial_instructor", "chief_physician"})
        if value <= 0 or (instruction <= 0 and not designated):
            continue
        rows.append({
            "instructor_ref": str(row["person_id"]),
            "instructor_skill": value,
            "instruction_skill": instruction,
            "capacity": instructor_capacity(instruction_skill=instruction, facility_level=facility_level, group_drill=True),
        })
    rows.sort(key=lambda r: (-int(r["instructor_skill"]), -int(r["instruction_skill"]), str(r["instructor_ref"])))
    return rows


def _ranked_students(refs: Sequence[str], *, epoch_key: str, domain: str) -> list[str]:
    def key(ref: str) -> tuple[bytes, str]:
        return hashlib.sha256(f"{epoch_key}|{domain}|{ref}".encode("utf-8")).digest(), ref
    return sorted((str(x) for x in refs), key=key)


def _plan_from_snapshot(
    eligible_refs: Sequence[str], candidates: Sequence[Mapping[str, Any]], *, epoch_key: str, domain: str
) -> tuple[dict[str, dict[str, int | str]], dict[str, int]]:
    ranked = _ranked_students(eligible_refs, epoch_key=epoch_key, domain=domain)
    assignments: dict[str, dict[str, int | str]] = {}
    loads: dict[str, int] = {}
    cursor = 0
    for candidate in candidates:
        instructor_ref = str(candidate.get("instructor_ref", ""))
        capacity = max(0, int(candidate.get("capacity", 0)))
        assigned = 0
        while cursor < len(ranked) and assigned < capacity:
            student_ref = ranked[cursor]
            cursor += 1
            if student_ref == instructor_ref:
                continue
            assignments[student_ref] = {
                "instructor_ref": instructor_ref,
                "instructor_skill": int(candidate.get("instructor_skill", 0)),
                "instruction_skill": int(candidate.get("instruction_skill", 0)),
                "capacity": capacity,
            }
            assigned += 1
        loads[instructor_ref] = assigned
        if cursor >= len(ranked):
            break
    return assignments, loads


def _instruction_plan(
    people: Sequence[Mapping[str, Any]], *, domain: str, facility_level: int, epoch_key: str
) -> tuple[dict[str, dict[str, int | str]], dict[str, int]]:
    return _plan_from_snapshot(
        _eligible_student_refs(people),
        _instructor_candidates(people, domain=domain, facility_level=facility_level),
        epoch_key=epoch_key,
        domain=domain,
    )


def institutional_instruction_assignment(
    people: Sequence[Mapping[str, Any]], *, student_ref: str, domain: str, facility_level: int, epoch_key: str
) -> dict[str, int | str | None]:
    assignments, _ = _instruction_plan(people, domain=domain, facility_level=facility_level, epoch_key=epoch_key)
    row = assignments.get(student_ref)
    if row is None:
        return {"instructor_ref": None, "instructor_skill": None, "instruction_skill": 0, "capacity": 0}
    return dict(row)


def institutional_teaching_duty_milli(
    person_ref: str,
    people: Sequence[Mapping[str, Any]],
    *,
    domains: Sequence[tuple[str, int]],
    epoch_key: str,
) -> int:
    duty = 0
    for domain, facility_level in domains:
        assignments, loads = _instruction_plan(people, domain=domain, facility_level=facility_level, epoch_key=epoch_key)
        load = max(0, int(loads.get(person_ref, 0)))
        if load <= 0:
            continue
        capacities = [int(row["capacity"]) for row in assignments.values() if row.get("instructor_ref") == person_ref]
        capacity = max(capacities, default=load)
        duty += min(300, load * 300 // max(1, capacity))
    return min(600, duty)


def _derived_curriculum_weights(training: Mapping[str, Any], person: Mapping[str, Any], *, duty_ref: str = "") -> dict[str, int]:
    """Derive one person's finite institutional curriculum.

    Static faction curriculum lists what the institution *can* teach.  It is
    not a mandate that every member simultaneously studies every profession.
    Core combat/cultivation subjects are shared; command, scouting and
    professions are selected by actual office/duty.  This keeps a Junior cook
    a martial member without making every cook a physician/merchant/commander,
    and prevents professional curriculum keys from being misinterpreted as
    martial skills.
    """
    raw = {str(k): max(0, int(v)) for k, v in training.items() if not isinstance(v, bool)}
    offices = {str(x).split(":", 1)[0] for x in person.get("standing_offices", []) if isinstance(x, str)}
    duty = str(duty_ref or "")

    # Shared martial foundation.  Weapon breadth remains authored by faction
    # identity, so a Sword+Unarmed school does not regenerate Spear/Bow/etc.
    w: dict[str, int] = {}
    for domain in ("sword", "spear", "bow", "hidden_weapons", "unarmed", "qi", "qi_control"):
        value = raw.get(domain, 0)
        if value > 0:
            w[domain] = value

    # Scouting and command are role-dependent field subjects, not universal
    # full-rate curriculum.  Existing specialist offices make the assignment
    # explicit; senior command offices also receive command development.
    if raw.get("stealth_scouting", 0) > 0 and offices & {"scout_leader", "field_commander", "deputy_field_commander"}:
        w["stealth_scouting"] = raw["stealth_scouting"]
    if raw.get("command", 0) > 0 and offices & {
        "leader", "deputy_leader", "field_commander",
        "deputy_field_commander", "chief_martial_instructor", "scout_leader",
    }:
        w["command"] = raw["command"]

    sword, spear, bow = w.get("sword", 0), w.get("spear", 0), w.get("bow", 0)
    hidden, unarmed = w.get("hidden_weapons", 0), w.get("unarmed", 0)
    stealth, command = w.get("stealth_scouting", 0), w.get("command", 0)
    qi, qc = w.get("qi", 0), w.get("qi_control", 0)
    medicine = raw.get("medicine", 0)
    w.update({
        "attribute:strength": (spear + unarmed + sword // 2) // 4,
        "attribute:speed": (sword + unarmed + stealth) // 5,
        "attribute:dexterity": (sword + bow + hidden) // 5,
        "attribute:endurance": (sword + spear + bow + unarmed + stealth) // 8,
        "attribute:perception": (bow + hidden + stealth + command // 2) // 5,
        "attribute:intelligence": (command + medicine + qc // 2) // 5,
        "attribute:willpower": (qi + qc + unarmed) // 5,
    })

    # Professions are attached to real work/office assignments.  The static
    # weights describe institutional teaching quality/emphasis only after the
    # person has a causal reason to spend finite time on that profession.
    if raw.get("medicine", 0) > 0 and (duty == "infirmary_service" or offices & {"chief_physician"}):
        w["professional:medicine"] = raw["medicine"]
    if raw.get("administration", 0) > 0 and offices & {
        "leader", "deputy_leader", "chief_steward", "treasurer", "quartermaster",
        "field_commander", "deputy_field_commander", "archivist",
    }:
        w["professional:administration"] = raw["administration"]
    if raw.get("commerce", 0) > 0 and (duty == "trade_service" or offices & {"treasurer", "chief_steward"}):
        w["professional:commerce"] = raw["commerce"]
    if raw.get("crafting", 0) > 0 and (duty == "workshop_service" or offices & {"quartermaster"}):
        w["professional:crafting"] = raw["crafting"]
    if raw.get("instruction", 0) > 0 and offices & {"chief_martial_instructor"}:
        w["professional:instruction"] = raw["instruction"]
    return {k: v for k, v in w.items() if v > 0}


def school_tuition_snapshot(faction: Mapping[str, Any], roster_people: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return finite external-school capacity and exact instructor duty.

    Paying students remain aggregate local demand. Their teachers do not: the
    duty map contains only actual persistent instructors whose time is consumed.
    """
    enterprises = faction.get("enterprises", {}) if isinstance(faction.get("enterprises"), Mapping) else {}
    level = max(0, int(enterprises.get("school_tuition", 0)))
    if level <= 0:
        return {"enterprise_level": 0, "paying_student_slots": 0, "physical_capacity": 0, "instructor_capacity": 0, "served_capacity": 0, "instructor_duty_milli": {}}
    slots = max(0, enterprise_scale_value(faction, "school_tuition"))
    if slots <= 0:
        return {"enterprise_level": level, "paying_student_slots": 0, "physical_capacity": 0, "instructor_capacity": 0, "served_capacity": 0, "instructor_duty_milli": {}}
    training = faction.get("training", {}) if isinstance(faction.get("training"), Mapping) else {}
    facilities = faction.get("buildings", {}) if isinstance(faction.get("buildings"), Mapping) else {}
    infrastructure = faction.get("infrastructure", {}) if isinstance(faction.get("infrastructure"), Mapping) else {}
    domains = [d for d in ("sword", "spear", "bow", "hidden_weapons", "unarmed", "qi", "qi_control") if int(training.get(d, 0)) > 0]
    physical = max((training_domain_capacity(facilities, d, infrastructure) for d in domains), default=0)
    by_ref: dict[str, dict[str, int]] = {}
    for domain in domains:
        facility_level = _facility_level(facilities, domain)
        for row in _instructor_candidates(roster_people, domain=domain, facility_level=facility_level):
            ref = str(row.get("instructor_ref", ""))
            if not ref:
                continue
            current = by_ref.get(ref)
            candidate = {
                "capacity": max(1, int(row.get("capacity", 1))),
                "skill": max(0, int(row.get("instructor_skill", 0))),
                "instruction": max(0, int(row.get("instruction_skill", 0))),
            }
            if current is None or (candidate["instruction"], candidate["skill"], candidate["capacity"]) > (current["instruction"], current["skill"], current["capacity"]):
                by_ref[ref] = candidate
    ranked = sorted(by_ref.items(), key=lambda x: (-x[1]["instruction"], -x[1]["skill"], x[0]))
    target = min(slots, physical)
    remaining = target
    duties: dict[str, int] = {}
    instructor_capacity_total = 0
    for ref, row in ranked:
        cap = max(1, int(row["capacity"]))
        instructor_capacity_total += cap
        if remaining <= 0:
            continue
        load = min(remaining, cap)
        remaining -= load
        # A fully loaded external instructor spends roughly 8 hours/week on
        # paying students, about 36% of the 22.4h/week institutional training
        # budget. Partial loads scale linearly and never exceed 500 milli.
        duties[ref] = min(500, max(80, 360 * load // cap))
    served = max(0, target - remaining)
    return {
        "enterprise_level": level, "paying_student_slots": slots,
        "physical_capacity": physical, "instructor_capacity": instructor_capacity_total,
        "served_capacity": served, "instructor_duty_milli": duties,
    }


def _environment_payload(
    faction: Mapping[str, Any], roster_people: Sequence[Mapping[str, Any]], *, year: int, month: int = 1,
) -> dict[str, Any]:
    training = faction.get("training", {}) if isinstance(faction.get("training"), Mapping) else {}
    facilities_raw = faction.get("buildings", {}) if isinstance(faction.get("buildings"), Mapping) else {}
    facilities = {k: max(0, int(facilities_raw.get(k, 0))) for k in _RELEVANT_FACILITIES}
    infrastructure = faction.get("infrastructure", {}) if isinstance(faction.get("infrastructure"), Mapping) else {}
    eligible_refs = _eligible_student_refs(roster_people)
    eligible_count = len(eligible_refs)
    # Institutional teaching is a transient bounded projection of the current
    # roster/facilities, never a saved assignment table. Exact instructor
    # identities are used only while deriving finite teaching quality/capacity.
    profiles: dict[str, dict[str, int]] = {}
    for domain in _ALL_TAUGHT_DOMAINS:
        rows = _instructor_candidates(roster_people, domain=domain, facility_level=_facility_level(facilities, domain))
        if not rows or eligible_count <= 0:
            continue
        physical_capacity = training_domain_capacity(facilities, domain, infrastructure)
        remaining = min(eligible_count, physical_capacity)
        covered = 0
        weighted_skill = 0
        weighted_instruction = 0
        eligible_set = set(eligible_refs)
        for candidate in rows:
            ref = str(candidate.get("instructor_ref", ""))
            capacity = max(0, int(candidate.get("capacity", 0)))
            # An instructor cannot consume one of their own teaching slots.
            effective_capacity = max(0, capacity - (1 if ref in eligible_set else 0))
            slots = min(remaining, effective_capacity)
            if slots <= 0:
                continue
            covered += slots
            remaining -= slots
            weighted_skill += slots * max(0, int(candidate.get("instructor_skill", 0)))
            weighted_instruction += slots * max(0, int(candidate.get("instruction_skill", 0)))
            if remaining <= 0:
                break
        if covered > 0:
            profiles[domain] = {
                "coverage_milli": min(1000, covered * 1000 // eligible_count),
                "facility_capacity": physical_capacity,
                "instructor_skill": weighted_skill // covered,
                "instruction_skill": weighted_instruction // covered,
            }
    eligible_guards = combat_ready_count(
        [p for p in roster_people if isinstance(p, Mapping)], year=year, minimum_age=14, minimum_combat_skill=20,
    )
    guard_duty_milli=institutional_guard_duty_milli(facilities,infrastructure=infrastructure,eligible_guard_count=eligible_guards,threat_milli=int(faction.get("security_threat_milli",500) or 500))
    school_snapshot = school_tuition_snapshot(faction, roster_people)
    duty_projection = derive_duty_assignments(
        faction, roster_people, year=year, month=month,
        protected_refs=("pc_wei_tang",),
    )
    living_population = sum(
        1 for person in roster_people
        if isinstance(person, Mapping) and is_faction_member(person)
        and (not isinstance(person.get("health"), Mapping) or person.get("health", {}).get("status") not in {"dead", "incapacitated"})
    )
    routine_overhead = routine_service_overhead_milli(faction, living_population=living_population)
    return {
        "curriculum": {str(k): max(0, int(v)) for k, v in training.items() if not isinstance(v, bool)},
        "facilities": facilities,
        "intensity_milli": _clamp(0, 2000, int((faction.get("training_epoch") or {}).get("intensity_milli", 1000))) if isinstance(faction.get("training_epoch"), Mapping) else 1000,
        "eligible_student_count": eligible_count,
        "instruction_profiles": profiles,
        "guard_duty_milli": guard_duty_milli,
        "routine_service_overhead_milli": routine_overhead,
        "external_teaching_duty_milli": school_snapshot.get("instructor_duty_milli", {}),
        "routine_duty_by_person": duty_projection.get("assignments", {}),
    }



def training_environment_projection(
    faction: Mapping[str, Any], roster_people: Sequence[Mapping[str, Any]], *, at_iso: str | None = None,
) -> dict[str, Any]:
    """Derive the current institutional environment without persisting it.

    This is intentionally a projection. Callers that mutate any input to the
    environment must settle/reset the old training epoch at that causal
    frontier before applying the mutation.
    """
    raw = at_iso
    if raw is None and isinstance(faction.get("training_epoch"), Mapping):
        raw = str((faction.get("training_epoch") or {}).get("settled_through") or (faction.get("training_epoch") or {}).get("started_at") or "")
    try:
        current = datetime.fromisoformat(str(raw or ""))
        year, month = current.year, current.month
    except ValueError:
        year, month = 0, 1
    return _environment_payload(faction, roster_people, year=year, month=month)

def _fingerprint(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _snapshot_segment(
    faction: Mapping[str, Any], roster_people: Sequence[Mapping[str, Any]], *, start_day: int, started_at: str,
) -> dict[str, Any]:
    try:
        environment_at = datetime.fromisoformat(str(started_at))
        environment_year = environment_at.year
        environment_month = environment_at.month
    except ValueError:
        environment_year = 0
        environment_month = 1
    payload = _environment_payload(faction, roster_people, year=environment_year, month=environment_month)
    fp = _fingerprint(payload)
    return {
        "segment_id": f"training.{faction.get('faction_id','')}.{start_day}.{fp[:12]}",
        "start_day": max(0, int(start_day)),
        "end_day": max(0, int(start_day)),
        "started_at": str(started_at),
        "settled_through": str(started_at),
        "environment_fingerprint": fp,
        **payload,
    }


def advance_faction_training_epoch(
    faction: Mapping[str, Any], roster: Mapping[str, Any], *, at_iso: str,
    refresh_environment: bool = True,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Advance only the faction training clock.

    Training conditions are owned by the faction, its facilities, curriculum,
    instructors and roster. They are derived when training is materialized and
    are never copied into save state. Callers that change a training input must
    settle/reset the roster at that exact frontier before applying the change.
    """
    del refresh_environment
    out = copy.deepcopy(dict(faction))
    epoch = copy.deepcopy(dict(out.get("training_epoch", {}))) if isinstance(out.get("training_epoch"), Mapping) else {}
    previous_raw = epoch.get("settled_through") or epoch.get("started_at") or at_iso
    try:
        previous = datetime.fromisoformat(str(previous_raw)); current = datetime.fromisoformat(str(at_iso))
    except ValueError as exc:
        raise ValueError("jianghu training epoch timestamp invalid") from exc
    if current < previous:
        raise ValueError("jianghu training epoch cannot move backward")
    elapsed_days = max(0, int((current - previous).total_seconds()) // 86400)
    epoch.setdefault("started_at", previous.isoformat())
    epoch.setdefault("intensity_milli", 1000)
    epoch["settled_through"] = current.isoformat()
    epoch.pop("elapsed_training_days", None)
    total_after = training_epoch_elapsed_days({"training_epoch": epoch})
    for derived in ("history", "current_environment", "curriculum_ref"):
        epoch.pop(derived, None)
    out["training_epoch"] = epoch
    people = roster.get("people", []) if isinstance(roster, Mapping) else []
    eligible = len(_eligible_student_refs([p for p in people if isinstance(p, Mapping)])) if isinstance(people, list) else 0
    return out, {
        "training_epoch_days_added": elapsed_days,
        "training_epoch_days_total": total_after,
        "training_people_eligible": eligible,
        "training_environment_segments": 0,
        "personal_training_records_written": 0,
    }


def settle_and_reset_faction_training_cycle(
    faction: Mapping[str, Any], roster: Mapping[str, Any], *, at_iso: str,
    next_intensity_milli: int | None = None, paused_refs: Sequence[str] = (),
) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
    """Materialize pending training and keep only the next epoch clock.

    ``paused_refs`` is a derived current-activity projection.  It is injected
    before the training environment is snapshotted so unavailable people cannot
    contribute as students *or instructors*.  The marker is intentionally
    compacted away before persistence by ``compact_person_state``.
    """
    paused = {str(ref) for ref in paused_refs if isinstance(ref, str) and ref}
    work_roster = copy.deepcopy(dict(roster))
    people = work_roster.get("people", []) if isinstance(work_roster, Mapping) else []
    if not isinstance(people, list):
        raise ValueError("jianghu roster invalid")
    if paused:
        projected: list[Any] = []
        for raw in people:
            if not isinstance(raw, Mapping) or str(raw.get("person_id") or "") not in paused:
                projected.append(raw)
                continue
            person = copy.deepcopy(dict(raw))
            state = copy.deepcopy(dict(person.get("training_state", {}))) if isinstance(person.get("training_state"), Mapping) else {}
            state["institutional_paused"] = True
            person["training_state"] = state
            projected.append(person)
        work_roster["people"] = projected
        people = projected
    advanced, summary = advance_faction_training_epoch(faction, work_roster, at_iso=at_iso)
    snapshot = [copy.deepcopy(dict(p)) for p in people if isinstance(p, Mapping)]
    epoch = advanced.get("training_epoch", {}) if isinstance(advanced.get("training_epoch"), Mapping) else {}
    started_raw = str(epoch.get("started_at") or epoch.get("settled_through") or at_iso)
    segment = _snapshot_segment(advanced, snapshot, start_day=0, started_at=started_raw)
    settled_people: list[Any] = []
    settled_count = 0
    for raw in people:
        if not isinstance(raw, Mapping):
            settled_people.append(raw)
            continue
        person = _apply_institutional_training_with_segment(raw, faction=advanced, segment=segment)
        state = copy.deepcopy(dict(person.get("training_state", {}))) if isinstance(person.get("training_state"), Mapping) else {}
        state.pop("institutional_days_applied", None)
        if state:
            person["training_state"] = state
        else:
            person.pop("training_state", None)
        settled_people.append(person)
        settled_count += 1
    roster_after = copy.deepcopy(dict(work_roster)); roster_after["people"] = settled_people
    intensity = _clamp(0, 2000, int(next_intensity_milli)) if next_intensity_milli is not None else _clamp(0, 2000, int((advanced.get("training_epoch") or {}).get("intensity_milli", 1000)))
    reset_faction = copy.deepcopy(dict(advanced))
    reset_faction["training_epoch"] = {
        "started_at": str(at_iso), "settled_through": str(at_iso),
        "intensity_milli": intensity,
    }
    return reset_faction, roster_after, {
        **summary,
        "training_people_settled": settled_count,
        "training_history_segments_pruned": 0,
        "training_epoch_days_total": 0,
        "training_environment_segments": 0,
        "personal_training_records_written": settled_count,
    }


def _segment_instruction_profile(segment: Mapping[str, Any], *, domain: str) -> dict[str, Any]:
    profiles = segment.get("instruction_profiles", {})
    row = profiles.get(domain) if isinstance(profiles, Mapping) else None
    if not isinstance(row, Mapping):
        return {"coverage_milli": 0, "instructor_skill": None, "instruction_skill": 0}
    return {
        "coverage_milli": _clamp(0, 1000, int(row.get("coverage_milli", 0))),
        "instructor_skill": max(0, int(row.get("instructor_skill", 0))),
        "instruction_skill": max(0, int(row.get("instruction_skill", 0))),
    }


def _development_factor_milli(person: Mapping[str, Any], domain: str, segment: Mapping[str, Any]) -> int:
    """Age/development gate for current capability gain, without capping eventual mastery."""
    try:
        year = int(str(segment.get("started_at", "")).split("-", 1)[0])
    except (TypeError, ValueError):
        year = 0
    birth = person.get("birth_year")
    if not isinstance(birth, int) or year <= 0:
        return 1000
    age = max(0, year - birth)
    if domain.startswith("attribute:"):
        key = domain.split(":", 1)[1]
        if key in {"intelligence", "perception", "willpower"}:
            return 700 if age <= 6 else 800 if age <= 9 else 900 if age <= 12 else 1000
        return 250 if age <= 6 else 350 if age <= 9 else 500 if age <= 12 else 700 if age <= 15 else 850 if age <= 17 else 1000
    if domain in {"qi", "qi_control"}:
        if domain == "qi":
            return 900 if age <= 6 else 950 if age <= 12 else 1000
        return 600 if age <= 6 else 700 if age <= 9 else 800 if age <= 12 else 900 if age <= 15 else 1000
    if domain.startswith("professional:"):
        return 250 if age <= 6 else 350 if age <= 9 else 500 if age <= 12 else 700 if age <= 15 else 850 if age <= 17 else 1000
    # Technical martial proficiency is less body-limited than raw physical
    # development.  A child still lacks adult reach/force/endurance in combat,
    # but can learn timing, geometry, coordination and weapon handling quickly.
    # By early adolescence the technical-learning curve is effectively adult;
    # physical attributes remain strongly age-gated above.
    return 700 if age <= 6 else 800 if age <= 9 else 900 if age <= 12 else 1000


def _apply_one_gain(
    out: dict[str, Any], *, domain: str, hours_milli: int, segment: Mapping[str, Any],
    residual: dict[str, int], evidence: Mapping[str, Any], health: int,
    instructor_skill: int | None = None, instruction_skill: int = 0,
) -> None:
    if hours_milli <= 0:
        return
    hours_milli = hours_milli * _development_factor_milli(out, domain, segment) // 1000
    if hours_milli <= 0:
        return
    facilities = segment.get("facilities", {}) if isinstance(segment.get("facilities"), Mapping) else {}
    aptitudes = out.get("aptitudes", {}) if isinstance(out.get("aptitudes"), Mapping) else {}
    martial = out.setdefault("martial_skills", {})
    attrs = out.setdefault("attributes", {})
    professions = out.setdefault("professional_skills", {})
    if domain.startswith("attribute:"):
        key = domain.split(":", 1)[1]
        current = max(0, int(attrs.get(key, 0)))
        aptitude = int(aptitudes.get("cognitive" if key in {"perception", "intelligence", "willpower"} else "physical", 100))
        instructor_skill = None; instruction_skill = 0
        facility = int(facilities.get("training_grounds", 0)); novelty = 1000
    elif domain.startswith("professional:"):
        key = domain.split(":", 1)[1]
        current = max(0, int(professions.get(key, 0))); aptitude = int(aptitudes.get("cognitive", 100))
        facility = _facility_level(facilities, key); novelty = min(1400, 1000 + max(0, int(evidence.get(key, 0))) // 20)
    elif domain in {"qi", "qi_control"}:
        key = domain
        current = max(0, int(out.get(key, 0))); aptitude = int(aptitudes.get("qi", 100)); facility = _facility_level(facilities, key); novelty = 1000
    else:
        key = domain
        current = max(0, int(martial.get(key, 0))); aptitude = int(aptitudes.get("leadership" if key == "command" else "martial", 100)); facility = _facility_level(facilities, key); novelty = min(1400, 1000 + max(0, int(evidence.get(key, 0))) // 20)
    gain = training_gain_milli(
        current_skill=current, aptitude=aptitude, hours_milli=hours_milli,
        instructor_skill=instructor_skill, instruction_skill=instruction_skill,
        facility_level=facility, health_milli=health, novelty_milli=novelty,
    )
    carry = max(0, int(residual.get(domain, 0))) + gain
    points, remainder = divmod(carry, 1000); residual[domain] = remainder
    if not points:
        return
    if domain.startswith("attribute:"):
        attrs[key] = current + points
    elif domain.startswith("professional:"):
        professions[key] = current + points
    elif domain in {"qi", "qi_control"}:
        prior_current_qi_milli = person_current_qi_milli(out) if key == "qi" else 0
        out[key] = current + points
        if key == "qi":
            set_person_current_qi_milli(out, prior_current_qi_milli + points * 1000)
    else:
        martial[key] = current + points


def _apply_segment(
    out: dict[str, Any], state: dict[str, Any], segment: Mapping[str, Any], *, days: int
) -> None:
    if days <= 0:
        return
    if state.get("institutional_paused") is True:
        return
    curriculum = segment.get("curriculum", {}) if isinstance(segment.get("curriculum"), Mapping) else {}
    person_ref = str(out.get("person_id", ""))
    routine_map = segment.get("routine_duty_by_person", {}) if isinstance(segment.get("routine_duty_by_person"), Mapping) else {}
    duty_ref = str(routine_map.get(person_ref) or "")
    weights = _derived_curriculum_weights(curriculum, out, duty_ref=duty_ref)
    focus = state.get("focus")
    if isinstance(focus, str) and focus not in {"", "standing_faction_curriculum"} and focus in MARTIAL_KEYS + ("qi", "qi_control"):
        weights = {focus: 100}
    if not weights:
        return
    residual = copy.deepcopy(dict(state.get("residual_milli", {}))) if isinstance(state.get("residual_milli"), Mapping) else {}
    evidence = state.get("evidence_milli", {}) if isinstance(state.get("evidence_milli"), Mapping) else {}
    health = _health_factor(out)
    intensity = _clamp(0, 2000, int(segment.get("intensity_milli", 1000)))
    total_hours = days * 3_200 * intensity // 1000
    # Routine work is a derived monthly assignment, never persistent person
    # state. Exact productive workers still consume finite time.
    duty_share = duty_time_share_for_ref(duty_ref)
    total_hours = total_hours * max(0, 1000 - duty_share) // 1000
    # Generic cooking, cleaning, records, storage and stable work is real but
    # does not need named persistent assignments. Its aggregate person-time is
    # derived from the institution and shared across members.
    routine_overhead = min(350, max(0, int(segment.get("routine_service_overhead_milli", 0))))
    if is_faction_member(out) and routine_overhead > 0:
        total_hours = total_hours * max(0, 1000 - routine_overhead) // 1000
    # Security positions are staffed by real rotating members. Charge the
    # compact cohort-equivalent duty share against institutional training rather
    # than storing hourly guard schedules.
    guard_duty=min(600,max(0,int(segment.get("guard_duty_milli",0))))
    if is_faction_member(out):
        total_hours = total_hours * max(0,1000-guard_duty) // 1000
    external_map = segment.get("external_teaching_duty_milli", {}) if isinstance(segment.get("external_teaching_duty_milli"), Mapping) else {}
    external_duty = min(500, max(0, int(external_map.get(person_ref, 0))))
    if external_duty > 0:
        total_hours = total_hours * max(0, 1000 - external_duty) // 1000

    general_hours = total_hours
    total_weight = max(1, sum(weights.values()))
    for domain, weight in sorted(weights.items()):
        hours = general_hours * weight // total_weight
        hours = hours * _functional_training_factor(out, domain) // 1000
        if hours <= 0:
            continue
        if domain.startswith("attribute:"):
            _apply_one_gain(
                out, domain=domain, hours_milli=hours, segment=segment, residual=residual,
                evidence=evidence, health=health, instructor_skill=None, instruction_skill=0,
            )
            continue
        taught_domain = domain.split(":", 1)[1] if domain.startswith("professional:") else domain
        profile = _segment_instruction_profile(segment, domain=taught_domain)
        instructed_hours = hours * int(profile["coverage_milli"]) // 1000
        if instructed_hours > 0:
            _apply_one_gain(
                out, domain=domain, hours_milli=instructed_hours, segment=segment,
                residual=residual, evidence=evidence, health=health,
                instructor_skill=profile.get("instructor_skill"),
                instruction_skill=int(profile.get("instruction_skill", 0)),
            )
        self_hours = max(0, hours - instructed_hours)
        if self_hours > 0:
            _apply_one_gain(
                out, domain=domain, hours_milli=self_hours, segment=segment,
                residual=residual, evidence=evidence, health=health,
                instructor_skill=None, instruction_skill=0,
            )
    state["residual_milli"] = residual


def _apply_institutional_training_with_segment(
    person: Mapping[str, Any], *, faction: Mapping[str, Any], segment: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply one already-derived faction environment to one person."""
    out = copy.deepcopy(dict(person))
    if not _is_martial(out):
        return out
    epoch = faction.get("training_epoch", {})
    if not isinstance(epoch, Mapping):
        return out
    elapsed_days = training_epoch_elapsed_days(faction)
    state = copy.deepcopy(dict(out.get("training_state", {}))) if isinstance(out.get("training_state"), Mapping) else {}
    applied_days = max(0, int(state.get("institutional_days_applied", 0)))
    if elapsed_days <= applied_days:
        return out
    if state.get("institutional_paused") is True:
        state["institutional_days_applied"] = elapsed_days
        out["training_state"] = state
        return out
    started_raw = str(epoch.get("started_at") or epoch.get("settled_through") or "")
    try:
        epoch_started = datetime.fromisoformat(started_raw)
    except ValueError:
        epoch_started = None
    cursor = applied_days
    while cursor < elapsed_days:
        if epoch_started is None:
            slice_end = elapsed_days
            slice_segment = segment
        else:
            current_at = epoch_started + timedelta(days=cursor)
            next_year = datetime(current_at.year + 1, 1, 1, tzinfo=current_at.tzinfo)
            boundary_days = max(1, int((next_year - current_at).total_seconds()) // 86400)
            slice_end = min(elapsed_days, cursor + boundary_days)
            slice_segment = dict(segment); slice_segment["started_at"] = current_at.isoformat()
        _apply_segment(out, state, slice_segment, days=slice_end - cursor)
        cursor = slice_end
    state["institutional_days_applied"] = elapsed_days
    out["training_state"] = state
    return out


def institutional_training_segment(
    faction: Mapping[str, Any], roster_people: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Derive one reusable current institutional-training segment for a roster."""
    epoch = faction.get("training_epoch", {})
    if not isinstance(epoch, Mapping):
        return None
    started_raw = str(epoch.get("started_at") or epoch.get("settled_through") or "")
    return _snapshot_segment(faction, roster_people, start_day=0, started_at=started_raw)


def apply_institutional_training(
    person: Mapping[str, Any], *, faction: Mapping[str, Any], roster_people: Sequence[Mapping[str, Any]],
    segment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project one person through the current owner-derived training environment."""
    current_segment = segment if isinstance(segment, Mapping) else institutional_training_segment(faction, roster_people)
    if not isinstance(current_segment, Mapping):
        return copy.deepcopy(dict(person))
    return _apply_institutional_training_with_segment(person, faction=faction, segment=current_segment)




def apply_deliberate_training_session(
    person: Mapping[str, Any], *, domain: str, hours_milli: int, at_iso: str,
    instructor_skill: int | None = None, instruction_skill: int = 0,
    facilities: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply one exact finite training session through the normal gain curve.

    This is used by directly observed instruction such as a public martial
    lecture.  It shares aptitude/difficulty/health/carry mechanics with
    institutional training and never grants a flat event bonus.
    """
    out = copy.deepcopy(dict(person))
    state = copy.deepcopy(dict(out.get("training_state", {}))) if isinstance(out.get("training_state"), Mapping) else {}
    residual = state.setdefault("residual_milli", {})
    if not isinstance(residual, dict):
        residual = {}; state["residual_milli"] = residual
    evidence = state.setdefault("evidence_milli", {})
    if not isinstance(evidence, dict):
        evidence = {}; state["evidence_milli"] = evidence
    before = _domain_value(out, domain)
    segment = {"started_at": str(at_iso), "facilities": dict(facilities or {})}
    health = _health_factor(out) * _functional_training_factor(out, domain) // 1000
    _apply_one_gain(
        out, domain=str(domain), hours_milli=max(0, int(hours_milli)), segment=segment,
        residual=residual, evidence=evidence, health=health,
        instructor_skill=instructor_skill, instruction_skill=max(0, int(instruction_skill)),
    )
    out["training_state"] = state
    return {
        "person_after": out, "domain": str(domain), "before": before,
        "after": _domain_value(out, domain),
        "residual_milli": max(0, int(residual.get(str(domain), 0))),
    }

__all__ = [
    "ATTRIBUTE_KEYS", "MARTIAL_KEYS", "PROFESSIONAL_KEYS",
    "advance_faction_training_epoch", "apply_institutional_training", "institutional_training_segment", "settle_and_reset_faction_training_cycle",
    "institutional_training_pause_refs",
    "institutional_instruction_assignment", "institutional_teaching_duty_milli",
    "instructor_capacity", "training_gain_milli", "school_tuition_snapshot", "training_epoch_elapsed_days",
    "training_environment_projection",
]

__all__ = list(globals().get("__all__", [])) + ["apply_deliberate_training_session"]
