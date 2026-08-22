"""Persistent travel-team and temporary escort staffing selection.

A standing retinue is Wei's persistent three-person field team. Membership is an
identity/coordination relationship, not an activity, so it never reserves a
person's hours by itself. Temporary contract manpower is selected separately and
exists only inside the mission commitment that actually consumes time.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

_CRITICAL_OFFICES = {
    "leader", "deputy_leader", "chief_instructor", "chief_martial_instructor",
    "chief_physician", "master_weaponsmith",
}


def _office_keys(person: Mapping[str, Any]) -> set[str]:
    return {
        str(x).split(":", 1)[0]
        for x in person.get("standing_offices", [])
        if isinstance(x, str)
    }


def _ready(person: Mapping[str, Any], *, year: int) -> bool:
    if bool(person.get("retired_from_field", False)):
        return False
    # Permanent attachment or routine mission staffing must not hollow out the
    # House's standing command, medical or instruction backbone merely because
    # an office-holder is individually excellent in the field.
    if _office_keys(person) & _CRITICAL_OFFICES:
        return False
    birth = person.get("birth_year")
    if not isinstance(birth, int) or year - birth < 14:
        return False
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    if str(health.get("status") or "ready") in {"dead", "incapacitated"}:
        return False
    if max(0, int(health.get("consciousness", 100))) <= 0:
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


def _leader_need_order(leader: Mapping[str, Any]) -> list[str]:
    martial = leader.get("martial_skills", {}) if isinstance(leader.get("martial_skills"), Mapping) else {}
    prof = leader.get("professional_skills", {}) if isinstance(leader.get("professional_skills"), Mapping) else {}
    # The permanent trio should complement Wei instead of duplicating his
    # strongest weapon skill: medicine, scouting/awareness and field command are
    # weighted by his own gaps, with direct protection always remaining useful.
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
    requested_count: int,
    year: int,
    unavailable_refs: Sequence[str] = (),
) -> tuple[list[str], dict[str, str]]:
    """Select Wei's persistent complementary travel team.

    Zero means the authorized chooser owns the identities but not the headcount:
    the standing travel team is still exactly three people. Exact two/three
    remains readable for backward-compatible tests/state, but the live retinue
    command now requests three for new assignments. This helper never expands a
    standing retinue to satisfy a caravan's escort minimum.
    """
    raw_count = int(requested_count)
    if raw_count not in {0, 2, 3}:
        raise ValueError("retinue requested count invalid")
    target_count = 3 if raw_count == 0 else raw_count
    leader_ref = str(leader.get("person_id") or "")
    faction_ref = str(leader.get("faction_ref") or "")
    unavailable = {str(x) for x in unavailable_refs if isinstance(x, str)}
    candidates = [
        p for p in people
        if isinstance(p, Mapping)
        and isinstance(p.get("person_id"), str)
        and p.get("person_id") != leader_ref
        and p.get("person_id") not in unavailable
        and str(p.get("faction_ref") or faction_ref) == faction_ref
        and _ready(p, year=year)
    ]
    roles = _leader_need_order(leader)
    chosen: list[str] = []
    assigned: dict[str, str] = {}
    for role in roles:
        if len(chosen) >= target_count:
            break
        ranked = sorted(
            (
                (_scores(person)[role], str(person["person_id"]))
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
    """Select temporary House manpower for one escort mission.

    These people are not added to the standing retinue. The contract commitment
    owns their time only for that mission, after which they return to ordinary
    House duties. Selection is deterministic, same-faction, readiness-gated and
    biased toward protective capability because the permanent trio already
    carries specialist travel roles.
    """
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


__all__ = ["select_mission_escort_reinforcements", "select_retinue_members"]
