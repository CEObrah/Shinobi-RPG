"""Persistent zero-time Jianghu retinue selection and training policy.

A retinue is an identity/coordination owner, not an activity. Membership never
reserves a person's hours. Actual escort, travel, combat and other commitments
remain responsible for finite time and for pausing institutional training.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

_CRITICAL_OFFICES = {
    "leader", "deputy_leader", "chief_instructor", "chief_martial_instructor",
    "chief_physician", "master_weaponsmith",
}


def _ready(person: Mapping[str, Any], *, year: int) -> bool:
    if bool(person.get("retired_from_field", False)):
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


def _critical_penalty(person: Mapping[str, Any]) -> int:
    offices = {
        str(x).split(":", 1)[0]
        for x in person.get("standing_offices", [])
        if isinstance(x, str)
    }
    return 180 if offices & _CRITICAL_OFFICES else 0


def _scores(person: Mapping[str, Any]) -> dict[str, int]:
    attrs = person.get("attributes", {}) if isinstance(person.get("attributes"), Mapping) else {}
    martial = person.get("martial_skills", {}) if isinstance(person.get("martial_skills"), Mapping) else {}
    prof = person.get("professional_skills", {}) if isinstance(person.get("professional_skills"), Mapping) else {}
    penalty = _critical_penalty(person)
    return {
        "protective_guard": (
            max(int(martial.get("sword", 0)), int(martial.get("unarmed", 0))) * 4
            + int(attrs.get("endurance", 0)) * 2
            + int(attrs.get("perception", 0))
            + int(attrs.get("willpower", 0))
            - penalty
        ),
        "scout": (
            int(martial.get("stealth_scouting", 0)) * 5
            + int(attrs.get("perception", 0)) * 2
            + int(attrs.get("speed", 0))
            + int(attrs.get("dexterity", 0))
            - penalty
        ),
        "field_medic": (
            int(prof.get("medicine", 0)) * 6
            + int(attrs.get("perception", 0)) * 2
            + int(attrs.get("intelligence", 0)) * 2
            + int(attrs.get("willpower", 0))
            - penalty
        ),
        "field_deputy": (
            int(martial.get("command", 0)) * 5
            + int(attrs.get("intelligence", 0)) * 2
            + int(attrs.get("perception", 0))
            + int(attrs.get("willpower", 0)) * 2
            - penalty
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
    """Select a small complementary field party from conserved current people."""
    count = max(2, min(3, int(requested_count)))
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
        if len(chosen) >= count:
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
        _score, person_ref, _person = ranked[0]
        chosen.append(person_ref)
        assigned[person_ref] = role
    return chosen, assigned


def retinue_training_focus(person: Mapping[str, Any], role: str) -> str | None:
    """Return a same-budget institutional focus for roles with a direct discipline.

    Field medicine remains on the ordinary faction curriculum because the
    current personal-focus contract intentionally covers martial/Qi disciplines
    only. This function never creates extra training hours.
    """
    martial = person.get("martial_skills", {}) if isinstance(person.get("martial_skills"), Mapping) else {}
    if role == "scout":
        return "stealth_scouting"
    if role == "field_deputy":
        return "command"
    if role == "protective_guard":
        return "sword" if int(martial.get("sword", 0)) >= int(martial.get("unarmed", 0)) else "unarmed"
    return None


def apply_retinue_focus(person: Mapping[str, Any], role: str) -> tuple[dict[str, Any], str | None, str | None]:
    out = copy.deepcopy(dict(person))
    state = copy.deepcopy(dict(out.get("training_state", {}))) if isinstance(out.get("training_state"), Mapping) else {}
    prior = state.get("focus") if isinstance(state.get("focus"), str) else None
    focus = retinue_training_focus(out, role)
    if focus is not None:
        state["focus"] = focus
        out["training_state"] = state
    return out, prior, focus


__all__ = ["apply_retinue_focus", "retinue_training_focus", "select_retinue_members"]
