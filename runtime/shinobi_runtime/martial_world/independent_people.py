"""Sparse exact martial people currently outside any faction.

These are not civilian aggregate population and not a historical archive. A person
moves here only after a causal faction exit and may later be recruited by another
faction without changing identity or creating population.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .civic import compact_civic_person, hydrate_civic_person

_INDEPENDENT_PATH = "state/martial-world/independent-people.json"


def hydrate_independent_person(person: Mapping[str, Any]) -> dict[str, Any]:
    out = hydrate_civic_person(person)
    out.pop("faction_ref", None)
    out.pop("membership_grade", None)
    return out


def compact_independent_person(person: Mapping[str, Any]) -> dict[str, Any]:
    out = compact_civic_person(person)
    out.pop("faction_ref", None)
    out.pop("membership_grade", None)
    return out


def independent_person(repository: Any, person_ref: str) -> tuple[str, dict[str, Any], int, dict[str, Any]]:
    owner = copy.deepcopy(repository.read_json(_INDEPENDENT_PATH))
    rows = owner.get("people", []) if isinstance(owner, Mapping) else []
    if not isinstance(rows, list):
        raise ValueError("jianghu independent people invalid")
    for ordinal, row in enumerate(rows):
        if isinstance(row, Mapping) and row.get("person_id") == person_ref:
            return _INDEPENDENT_PATH, owner, ordinal, hydrate_independent_person(row)
    raise KeyError(person_ref)


def set_independent_person(owner: Mapping[str, Any], ordinal: int, person: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(owner))
    rows = out.get("people")
    if out.get("schema") != "jianghu-independent-people-state-1.0" or not isinstance(rows, list) or ordinal < 0 or ordinal >= len(rows):
        raise ValueError("jianghu independent people owner invalid")
    rows[ordinal] = compact_independent_person(person)
    return out


__all__ = [
    "compact_independent_person",
    "hydrate_independent_person",
    "independent_person",
    "set_independent_person",
]
