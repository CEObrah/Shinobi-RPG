"""Persistent travel-team and temporary escort staffing selection.

A standing retinue is Wei's persistent personal field team. Membership is an
identity/coordination relationship, not a fixed set of slots or an activity, so
it never reserves a person's hours by itself. Permanent companions are selected
for long-term field compatibility as well as raw capability. Temporary contract manpower is selected
separately and exists only inside the mission commitment that actually consumes
time.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

_CRITICAL_OFFICES = {
    "leader", "deputy_leader", "chief_martial_instructor",
    "chief_physician",
}
_PERMANENT_TEAM_MIN_AGE = 16
_PERMANENT_TEAM_MAX_AGE_GAP = 18
_PERMANENT_TEAM_MIN_TENURE_YEARS = 1
_PERMANENT_TEAM_GRADES = {"junior", "full", "senior", "elite"}


def _office_keys(person: Mapping[str, Any]) -> set[str]:
    return {
        str(x).split(":", 1)[0]
        for x in person.get("standing_offices", [])
        if isinstance(x, str)
    }


def _person_age(person: Mapping[str, Any], *, year: int) -> int | None:
    birth = person.get("birth_year")
    if not isinstance(birth, int):
        return None
    return year - birth


def _ready(person: Mapping[str, Any], *, year: int) -> bool:
    if bool(person.get("retired_from_field", False)):
        return False
    if _office_keys(person) & _CRITICAL_OFFICES:
        return False
    age = _person_age(person, year=year)
    if age is None or age < 14:
        return False
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    if str(health.get("status") or "ready") in {"dead", "incapacitated"}:
        return False
    if max(0, int(health.get("consciousness", 100))) <= 0:
        return False
    return True


def permanent_team_age_bounds(leader: Mapping[str, Any], *, year: int) -> tuple[int, int]:
    """Return the socially plausible age band for a long-term field companion."""
    leader_age = _person_age(leader, year=year)
    if leader_age is None:
        raise ValueError("permanent team leader age unresolved")
    minimum = max(_PERMANENT_TEAM_MIN_AGE, leader_age - 10)
    maximum = leader_age + _PERMANENT_TEAM_MAX_AGE_GAP
    return minimum, maximum


def permanent_team_member_eligible(
    leader: Mapping[str, Any], person: Mapping[str, Any], *, year: int
) -> bool:
    """Whether one current person is a plausible permanent field companion."""
    if not _ready(person, year=year):
        return False
    age = _person_age(person, year=year)
    if age is None:
        return False
    minimum, maximum = permanent_team_age_bounds(leader, year=year)
    if age < minimum or age > maximum:
        return False
    if str(person.get("membership_grade") or "") not in _PERMANENT_TEAM_GRADES:
        return False
    joined_year = person.get("joined_year")
    if isinstance(joined_year, int) and year - joined_year < _PERMANENT_TEAM_MIN_TENURE_YEARS:
        return False
    return True


def _scores(person: Mapping[str, Any]) -> dict[str, int]:
    attrs = person.get("attributes", {}) if isinstance(person.get("attributes"), Mapping) else {}
    martial = person.get("martial_skills", {}) if isinstance(person.get("martial_skills"), Mapping) else {}
    prof = person.get("professional_skills", {}) if isinstance(person.get("professional_skills"), Mapping) else {}
    return {
        "protective_guard": (
            max(int(martial.get("sword", 0)), int(martial.get("unarmed", 0))) * 4
            + int(attrs.get("endurance", 0)) * 2
            + int(attrs.get("perception", 0))
            + int(attrs.get("willpower", 0))
        ),
        "scout": (
            int(martial.get("stealth_scouting", 0)) * 5
            + int(attrs.get("perception", 0)) * 2
            + int(attrs.get("speed", 0))
            + int(attrs.get("dexterity", 0))
        ),
        "field_medic": (
            int(prof.get("medicine", 0)) * 6
            + int(attrs.get("perception", 0)) * 2
            + int(attrs.get("intelligence", 0)) * 2
            + int(attrs.get("willpower", 0))
        ),
        "field_deputy": (
            int(martial.get("command", 0)) * 5
            + int(attrs.get("intelligence", 0)) * 2
            + int(attrs.get("perception", 0))
            + int(attrs.get("willpower", 0)) * 2
        ),
    }


def _potential_bonus(person: Mapping[str, Any], role: str) -> int:
    """Bounded long-horizon aptitude value for permanent-companion selection.

    Permanent retinues are developmental relationships, so potential matters.
    Temporary mission staffing deliberately does not use this bonus because a
    one-mission escort needs whoever can perform the job now.
    """
    apt = person.get("aptitudes", {}) if isinstance(person.get("aptitudes"), Mapping) else {}
    physical = max(0, int(apt.get("physical", 0)))
    martial = max(0, int(apt.get("martial", 0)))
    qi = max(0, int(apt.get("qi", 0)))
    cognitive = max(0, int(apt.get("cognitive", 0)))
    leadership = max(0, int(apt.get("leadership", 0)))
    weighted = {
        "protective_guard": (physical * 3 + martial * 4 + qi + cognitive) // 9,
        "scout": (physical * 2 + martial * 3 + qi + cognitive * 3) // 9,
        "field_medic": (physical + martial + qi * 2 + cognitive * 5) // 9,
        "field_deputy": (martial + qi + cognitive * 3 + leadership * 4) // 9,
    }.get(role, 0)
    # At max ordinary/prodigy aptitude this contributes materially, but current
    # competence still dominates the appointment decision.
    return weighted * 2


def _leader_need_order(leader: Mapping[str, Any]) -> list[str]:
    martial = leader.get("martial_skills", {}) if isinstance(leader.get("martial_skills"), Mapping) else {}
    prof = leader.get("professional_skills", {}) if isinstance(leader.get("professional_skills"), Mapping) else {}
    needs = {
        "field_medic": max(0, 120 - int(prof.get("medicine", 0))) * 5,
        "scout": max(0, 110 - int(martial.get("stealth_scouting", 0))) * 4,
        "field_deputy": max(0, 100 - int(martial.get("command", 0))) * 3,
        "protective_guard": 180,
    }
    return sorted(needs, key=lambda role: (-needs[role], role))


def select_retinue_members(
    leader: Mapping[str, Any],
    people: Sequence[Mapping[str, Any]],
    *,
    year: int,
    unavailable_refs: Sequence[str] = (),
    target_count: int = 3,
) -> tuple[list[str], dict[str, str]]:
    """Select Wei's persistent complementary travel team.

    Permanent members must be established trusted personnel within a plausible
    long-term cohort for the leader. Once a person is inside that lawful cohort,
    current capability plus role-relevant aptitude determines selection; age
    only establishes the lawful cohort and does not itself add score. Specialist labels
    are relative assignments, not artificial mastery thresholds. If fewer than
    the requested number of lawful cohort members exist, selection blocks rather
    than reaching across generations or taking an unproven new recruit.
    """
    target_count = int(target_count)
    if target_count < 0:
        raise ValueError("permanent team target count invalid")
    leader_ref = str(leader.get("person_id") or "")
    faction_ref = str(leader.get("faction_ref") or "")
    if _person_age(leader, year=year) is None:
        raise ValueError("permanent team leader age unresolved")
    unavailable = {str(x) for x in unavailable_refs if isinstance(x, str)}
    candidates = [
        p for p in people
        if isinstance(p, Mapping)
        and isinstance(p.get("person_id"), str)
        and p.get("person_id") != leader_ref
        and p.get("person_id") not in unavailable
        and str(p.get("faction_ref") or faction_ref) == faction_ref
        and permanent_team_member_eligible(leader, p, year=year)
    ]
    roles = _leader_need_order(leader)
    chosen: list[str] = []
    assigned: dict[str, str] = {}
    for role in roles:
        if len(chosen) >= target_count:
            break
        ranked = sorted(
            (
                (_scores(person)[role] + _potential_bonus(person, role), str(person["person_id"]))
                for person in candidates
                if str(person["person_id"]) not in chosen
            ),
            key=lambda row: (-row[0], row[1]),
        )
        if not ranked:
            continue
        _score, person_ref = ranked[0]
        chosen.append(person_ref)
        assigned[person_ref] = role
    return chosen, assigned


def select_mission_escort_reinforcements(
    leader: Mapping[str, Any],
    people: Sequence[Mapping[str, Any]],
    *,
    needed_count: int,
    year: int,
    unavailable_refs: Sequence[str] = (),
    exclude_refs: Sequence[str] = (),
) -> list[str]:
    """Select temporary House manpower for one escort mission."""
    needed = int(needed_count)
    if needed < 0:
        raise ValueError("mission escort reinforcement count invalid")
    if needed == 0:
        return []
    leader_ref = str(leader.get("person_id") or "")
    faction_ref = str(leader.get("faction_ref") or "")
    blocked = {
        str(x)
        for x in (*unavailable_refs, *exclude_refs)
        if isinstance(x, str) and x
    }
    blocked.add(leader_ref)
    ranked = sorted(
        (
            (_scores(person)["protective_guard"], str(person["person_id"]))
            for person in people
            if isinstance(person, Mapping)
            and isinstance(person.get("person_id"), str)
            and str(person.get("person_id")) not in blocked
            and str(person.get("faction_ref") or faction_ref) == faction_ref
            and _ready(person, year=year)
        ),
        key=lambda row: (-row[0], row[1]),
    )
    return [person_ref for _score, person_ref in ranked[:needed]]


__all__ = [
    "permanent_team_age_bounds",
    "permanent_team_member_eligible",
    "select_mission_escort_reinforcements",
    "select_retinue_members",
]
