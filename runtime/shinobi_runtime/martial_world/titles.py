"""Knowledge-aware Jianghu social-title derivation.

Titles are presentation derived from current office/family identity plus what the
observer actually knows. They are not persisted on every person and never grant
knowledge merely because the runtime knows an identity.
"""
from __future__ import annotations
from typing import Any, Mapping, Sequence

_OFFICE_TITLES = {
    "emperor": "Emperor",
    "prince": "Prince",
    "princess": "Princess",
    "imperial_minister": "Minister",
    "grand_minister": "Grand Minister",
    "magistrate": "Magistrate",
    "leader": None,  # faction identity supplies House Head/Sect Leader/etc.
    "chief_physician": "Physician",
    "physician": "Physician",
    "escort_chief": "Escort Chief",
    "chief_instructor": "Chief Instructor",
    "elder": "Elder",
}


def _office_roots(person: Mapping[str, Any]) -> list[str]:
    return [str(x).split(":", 1)[0] for x in person.get("standing_offices", []) if isinstance(x, str)]


def _household_for(person_ref: str, family_state: Mapping[str, Any]) -> Mapping[str, Any] | None:
    households = family_state.get("households", {}) if isinstance(family_state, Mapping) else {}
    if not isinstance(households, Mapping):
        return None
    for row in households.values():
        if isinstance(row, Mapping) and person_ref in row.get("member_refs", []):
            return row
    return None


def derive_social_titles(
    person: Mapping[str, Any], *, faction_identity: Mapping[str, Any] | None = None,
    family_state: Mapping[str, Any] | None = None, observer_knows_identity: bool,
    observer_knows_office: bool = False, observer_knows_faction: bool = False,
) -> list[str]:
    """Return lawful contextual titles ordered from strongest to weakest.

    Identity-dependent titles are withheld when identity is unknown. A visible or
    separately learned office can be supplied via ``observer_knows_office``.
    """
    if not observer_knows_identity and not observer_knows_office:
        return []
    titles: list[str] = []
    offices = _office_roots(person)
    identity = faction_identity if isinstance(faction_identity, Mapping) else {}
    for office in offices:
        if office == "leader":
            if observer_knows_identity and observer_knows_faction:
                leader_title = identity.get("leader_title")
                if isinstance(leader_title, str) and leader_title:
                    titles.append(leader_title)
            continue
        title = _OFFICE_TITLES.get(office)
        if title and (observer_knows_office or observer_knows_identity):
            titles.append(title)

    # Young Master/Lady is a ruling-house family relationship, not a generic
    # faction membership grade. Do not call every retainer a Young Master.
    if observer_knows_identity and observer_knows_faction and str(identity.get("faction_type")) == "martial_house":
        household = _household_for(str(person.get("person_id") or ""), family_state or {})
        if isinstance(household, Mapping):
            head = str(household.get("head_ref") or "")
            pid = str(person.get("person_id") or "")
            if pid and pid != head:
                sex = str(person.get("sex") or "")
                titles.append("Young Lady" if sex == "female" else "Young Master")

    grade = str(person.get("membership_grade") or "")
    display = identity.get("display_titles", {}) if isinstance(identity.get("display_titles"), Mapping) else {}
    if observer_knows_identity and observer_knows_faction and grade and isinstance(display.get(grade), str):
        titles.append(str(display[grade]))
    # Stable de-duplication.
    return list(dict.fromkeys(titles))


def preferred_social_title(*args: Any, **kwargs: Any) -> str | None:
    titles = derive_social_titles(*args, **kwargs)
    return titles[0] if titles else None


__all__ = ["derive_social_titles", "preferred_social_title"]
