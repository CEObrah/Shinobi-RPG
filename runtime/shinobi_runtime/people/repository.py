"""Persistent Jianghu person-sheet resolution through deterministic roster routes."""
from __future__ import annotations
import copy
from typing import Any, Mapping, Optional
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.martial_world.health import combat_status_families, functional_penalties, vision_state
from shinobi_runtime.martial_world.live_state import roster_person


class RepositoryPersonSheetResolver:
    def __init__(self, repository: RepositoryStore) -> None:
        self.repository = repository

    def __call__(self, person_id: str) -> Optional[Mapping[str, Any]]:
        try:
            _path, _roster, _ordinal, person = roster_person(self.repository, person_id)
        except (FileNotFoundError, KeyError):
            return None
        person = copy.deepcopy(person)
        person.pop("__state_defaults", None)
        health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
        wounds = health.get("injuries", []) if isinstance(health.get("injuries"), list) else []
        person["derived_condition"] = {
            "vision": vision_state(wounds),
            "functional_penalties": functional_penalties(wounds),
            "combat_status_families": list(combat_status_families(wounds)),
        }
        return person
