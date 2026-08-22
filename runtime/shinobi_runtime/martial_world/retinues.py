"""Persistent zero-time Jianghu retinue selection.

A retinue is an identity/coordination owner, not an activity. Membership never
reserves a person's hours. Actual escort, travel, combat and other commitments
remain responsible for finite time and for pausing institutional training.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

_CRITICAL_OFFICES = {
    "leader", "deputy_leader", "chief_instructor", "chief_martial_instructor",
    "chief_physician", "master_weaponsmith",
}
_DISCRETIONARY_THIRD_SCORE = 420


def _office_keys(person: Mapping[str, Any]) -> set[str]:
    return {
        str(x).split(":", 1)[0]
        for x in person.get("standing_offices", [])
        if isinstance(x, str)
    }


def _ready(person: Mapping[str, Any], *, year: int) -> bool:
    if bool(person.get("retired_from_field", False)):
        return False
    # Permanent attachment to Wei must not hollow out the House's standing
    # command, medical or instruction backbone merely because an office-holder
    # is individually excellent at the role.
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
    # Lower leader capability means greater complement need. Keep one direct
    # protector in the mix, but do not waste the entire retinue duplicating the
    # leader's strongest weapon skill.
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
    """Select a complementary two/three-person field party from conserved people.

    ``requested_count`` may be 2 or 3 for an exact player request. Zero means
    the player delegated the two-versus-three choice to the authorized chooser:
    two strong complements are mandatory, and a third is added only when the
    next missing role has a materially capable non-reserved candidate.
    """
    raw_count = int(requested_count)
    if raw_count not in {0, 2, 3}:
        raise ValueError("retinue requested count invalid")
    discretionary = raw_count == 0
    target_count = 3 if discretionary else raw_count
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
                (_scores(person)[role], str(person["person_id"]), person)
                for person in candidates
                if str(person["person_id"]) not in chosen
            ),
            key=lambda row: (-row[0], row[1]),
        )
        if not ranked:
            continue
        score, person_ref, _person = ranked[0]
        if discretionary and len(chosen) >= 2 and score < _DISCRETIONARY_THIRD_SCORE:
            break
        chosen.append(person_ref)
        assigned[person_ref] = role
    return chosen, assigned


__all__ = ["select_retinue_members"]
