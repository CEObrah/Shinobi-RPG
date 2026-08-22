"""Bounded deterministic monthly institutional progression for Jianghu factions.

This module turns already-saved capabilities, health, membership, social trust and
institutional staffing needs into current consequences. It never grants stats,
creates people, or keeps a decision-history diary.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from .health import recovery_advance, wound_requires_persistence
from .membership import grade_eligibility, select_office_candidate
from .manpower import is_faction_member
from .duties import reassign_standing_duties

_GRADES = ("probationary", "junior", "full", "senior", "elite", "elder")
_GRADE_MIN_SERVICE = {"probationary":0,"junior":30,"full":365,"senior":1095,"elite":1825,"elder":3650}
_MARTIAL = ("sword", "spear", "bow", "hidden_weapons", "unarmed", "stealth_scouting", "command")
_CORE_OFFICES = (
    ("leader", "command"),
    ("deputy_leader", "command"),
    ("chief_martial_instructor", "instruction"),
    ("field_commander", "command"),
    ("deputy_field_commander", "command"),
    ("scout_leader", "stealth_scouting"),
    ("chief_steward", "administration"),
    ("treasurer", "commerce"),
    ("quartermaster", "administration"),
    ("chief_physician", "medicine"),
    ("archivist", "administration"),
)


def _alive(person: Mapping[str, Any]) -> bool:
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    return health.get("status") != "dead" and int(health.get("consciousness", 100)) > 0


def _primary_discipline(faction: Mapping[str, Any]) -> str:
    training = faction.get("training", {}) if isinstance(faction.get("training"), Mapping) else {}
    return max(_MARTIAL, key=lambda k: (int(training.get(k, 0)), -_MARTIAL.index(k)))


def _service_days(person: Mapping[str, Any], year: int) -> int:
    birth = int(person.get("birth_year", year))
    default_join = birth + 16
    joined = int(person.get("joined_year", default_join))
    joined = max(birth, joined)
    return max(0, (int(year) - joined) * 365)


def _office_key(value: object) -> str:
    return str(value).split(":", 1)[0]


def _trust(social: Mapping[str, Any], a: str, b: str) -> int:
    rows = social.get("relationships", {}) if isinstance(social.get("relationships"), Mapping) else {}
    ab = rows.get(f"{a}|{b}", {}) if isinstance(rows, Mapping) else {}
    ba = rows.get(f"{b}|{a}", {}) if isinstance(rows, Mapping) else {}
    av = int(ab.get("trust", 0)) if isinstance(ab, Mapping) else 0
    bv = int(ba.get("trust", 0)) if isinstance(ba, Mapping) else 0
    if av and bv:
        return min(av, bv)
    return max(av, bv)


def institutional_status(
    faction: Mapping[str, Any], roster: Mapping[str, Any], *, year: int,
    social: Mapping[str, Any], unavailable_refs: Sequence[str] = (),
) -> dict[str, int]:
    people = roster.get("people", []) if isinstance(roster, Mapping) else []
    if not isinstance(people, list):
        people = []
    living = [p for p in people if isinstance(p, Mapping) and _alive(p)]
    unavailable = {str(x) for x in unavailable_refs}
    available = [p for p in living if str(p.get("person_id", "")) not in unavailable]
    # Existing offices remain current authority during temporary unavailability;
    # custody/deployment blocks new selection rather than silently deposing an
    # officeholder.
    occupied = {_office_key(o) for p in living for o in (p.get("standing_offices", []) if isinstance(p.get("standing_offices"), list) else [])}
    office_vacancies = sum(1 for office, _ in _CORE_OFFICES if office not in occupied)
    return {"office_vacancies": office_vacancies}


def advance_institution(
    faction: Mapping[str, Any], roster: Mapping[str, Any], *, year: int,
    social: Mapping[str, Any], player_ref: str | None = None,
    unavailable_refs: Sequence[str] = (), infirmary_beds: int = 0, month: int = 1,
) -> dict[str, Any]:
    """Apply bounded current consequences and return after-images plus summary."""
    out = copy.deepcopy(dict(roster))
    people = out.get("people")
    if not isinstance(people, list):
        raise ValueError("jianghu roster people invalid")
    unavailable = {str(x) for x in unavailable_refs}
    primary = _primary_discipline(faction)
    promoted: list[str] = []
    recovered: list[str] = []

    # Monthly recovery consumes no history. Infirmary beds are real: the most
    # serious current casualties receive the full institutional recovery
    # interval, while overflow receives only ordinary unsupported recovery.
    severity_rows=[]
    for raw in people:
        if not isinstance(raw,Mapping):continue
        wounds=raw.get("health",{}).get("injuries",[]) if isinstance(raw.get("health"),Mapping) else []
        if not isinstance(wounds,list) or not wounds:continue
        severity=sum(max(0,int(w.get("severity",0))) for w in wounds if isinstance(w,Mapping))
        pid=str(raw.get("person_id") or "")
        severity_rows.append((-severity,pid))
    severity_rows.sort()
    admitted={pid for _sev,pid in severity_rows[:max(0,int(infirmary_beds))] if pid}
    for idx, raw in enumerate(people):
        if not isinstance(raw, Mapping):
            continue
        p = copy.deepcopy(dict(raw))
        health = copy.deepcopy(dict(p.get("health", {}))) if isinstance(p.get("health"), Mapping) else {}
        wounds = health.get("injuries", [])
        if isinstance(wounds, list) and wounds:
            recovery_hours=(30*24 if str(p.get("person_id") or "") in admitted else 20*24)
            advanced = [recovery_advance(w, elapsed_hours=recovery_hours) for w in wounds if isinstance(w, Mapping)]
            remaining = [
                w for w in advanced
                if int(w.get("healing_progress_milli", 0)) < 100000 or wound_requires_persistence(w)
            ]
            acute_remaining = [w for w in remaining if not bool(w.get("healed"))]
            if remaining != wounds:
                health["injuries"] = remaining
                if not acute_remaining and health.get("status") not in {"dead"}:
                    health["status"] = "ready"
                    health["consciousness"] = max(1, int(health.get("consciousness", 100)))
                p["health"] = health
                people[idx] = p
                if isinstance(p.get("person_id"), str):
                    recovered.append(str(p["person_id"]))

    # One-grade-at-a-time promotion. Current grade is never a source of stats.
    living_member_count = sum(1 for p in people if isinstance(p, Mapping) and _alive(p))
    elder_cap = max(1, living_member_count // 50) if living_member_count >= 25 else 0
    elder_count = sum(1 for p in people if isinstance(p, Mapping) and p.get("membership_grade") == "elder" and _alive(p))
    for idx, raw in enumerate(people):
        if len(promoted) >= 12 or not isinstance(raw, Mapping):
            continue
        p = copy.deepcopy(dict(raw))
        pid = p.get("person_id")
        if not isinstance(pid, str) or pid == player_ref or pid in unavailable or not is_faction_member(p) or not _alive(p):
            continue
        grade = str(p.get("membership_grade", "probationary"))
        if grade not in _GRADES or grade == "elder":
            continue
        target = _GRADES[_GRADES.index(grade) + 1]
        check = grade_eligibility(
            p, target_grade=target, service_days=_service_days(p, year), primary_discipline=primary,
            discipline_clean=True, elder_open_seat=(elder_count < elder_cap),
        )
        if check["eligible"]:
            p["membership_grade"] = target
            people[idx] = p
            promoted.append(pid)
            if target == "elder":
                elder_count += 1

    # Vacancies are actual vacancies among living officeholders. Dead holders
    # lose current office authority before replacements are selected.
    for idx, raw in enumerate(people):
        if not isinstance(raw, Mapping):
            continue
        if _alive(raw):
            continue
        offices = raw.get("standing_offices", [])
        if isinstance(offices, list) and offices:
            p = copy.deepcopy(dict(raw)); p["standing_offices"] = []
            people[idx] = p

    living = [p for p in people if isinstance(p, Mapping) and _alive(p)]
    by_id = {str(p["person_id"]): i for i, p in enumerate(people) if isinstance(p, Mapping) and isinstance(p.get("person_id"), str)}
    occupied = {_office_key(o) for p in living for o in (p.get("standing_offices", []) if isinstance(p.get("standing_offices"), list) else [])}
    appointments: list[dict[str, str]] = []
    service = {str(p["person_id"]): _service_days(p, year) for p in living if isinstance(p.get("person_id"), str)}
    leader_refs = [
        str(p["person_id"]) for p in living
        if isinstance(p.get("person_id"), str)
        and "leader" in (p.get("standing_offices", []) if isinstance(p.get("standing_offices"), list) else [])
    ]
    trust = {
        str(p["person_id"]): (
            max((_trust(social, leader_ref, str(p["person_id"])) for leader_ref in leader_refs), default=0)
            if str(p["person_id"]) not in leader_refs else 100
        )
        for p in living if isinstance(p.get("person_id"), str)
    }
    eligible_candidates = [p for p in living if is_faction_member(p) and str(p.get("membership_grade")) in {"full", "senior", "elite", "elder"} and p.get("person_id") != player_ref and str(p.get("person_id", "")) not in unavailable]
    for office, skill in _CORE_OFFICES:
        if office in occupied or not eligible_candidates:
            continue
        candidate = select_office_candidate(eligible_candidates, relevant_skill_key=skill, service_days=service, trust=trust)
        if candidate is None:
            continue
        idx = by_id[candidate]
        p = copy.deepcopy(dict(people[idx]))
        offices = list(p.get("standing_offices", [])) if isinstance(p.get("standing_offices"), list) else []
        offices.append(office); p["standing_offices"] = sorted(set(str(x) for x in offices))
        people[idx] = p
        occupied.add(office)
        appointments.append({"office": office, "person_ref": candidate})

    # Routine institutional work is a current assignment, not a permanent
    # social caste. Reassess it from the now-current grades, offices, skills
    # and availability. This is sparse: only the current duty lives on a
    # person; the monthly review does not append duty history.
    duty_rotation = reassign_standing_duties(
        faction,
        [p for p in people if isinstance(p, Mapping)],
        year=year,
        month=month,
        unavailable_refs=sorted(unavailable),
        protected_refs=([player_ref] if isinstance(player_ref, str) and player_ref else ["pc_wei_tang"]),
    )
    rotated_rows = duty_rotation["people_after"]
    if len(rotated_rows) == len(people):
        people[:] = rotated_rows

    return {
        "roster": out,
        "summary": {
            "promoted_refs": promoted,
            "appointments": appointments,
            "recovered_refs": recovered,
            "duty_changes": duty_rotation["changes"],
            "duty_shortages": duty_rotation["shortages"],
            "duty_assigned_counts": duty_rotation["assigned_counts"],
        },
    }


__all__ = ["advance_institution", "institutional_status"]
