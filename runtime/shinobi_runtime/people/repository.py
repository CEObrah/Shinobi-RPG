"""Persistent Jianghu person-sheet resolution through deterministic roster routes."""
from __future__ import annotations
import copy
from typing import Any, Mapping, Optional
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.martial_world.health import combat_status_families, functional_penalties, vision_state
from shinobi_runtime.martial_world.live_state import roster_person


def _active_household_members(family_state: Mapping[str, Any], player_id: str) -> list[str]:
    households = family_state.get("households", {}) if isinstance(family_state, Mapping) else {}
    if not isinstance(households, Mapping):
        return []
    for household in households.values():
        if not isinstance(household, Mapping) or household.get("status") != "active":
            continue
        members = household.get("member_refs", [])
        if isinstance(members, list) and player_id in members:
            return [str(ref) for ref in members if isinstance(ref, str)]
    return []


def _parent_refs(family_state: Mapping[str, Any], person_id: str) -> list[str]:
    parentage = family_state.get("parentage", {}) if isinstance(family_state, Mapping) else {}
    row = parentage.get(person_id, {}) if isinstance(parentage, Mapping) else {}
    refs = row.get("parent_refs", []) if isinstance(row, Mapping) else []
    return [str(ref) for ref in refs if isinstance(ref, str)] if isinstance(refs, list) else []


def _sexed_relation(sex: Any, male: str, female: str, neutral: str) -> str:
    return male if sex == "male" else female if sex == "female" else neutral


def _kinship_to_player(
    *,
    target: Mapping[str, Any],
    player: Mapping[str, Any],
    family_state: Mapping[str, Any],
    household_members: list[str],
) -> str | None:
    """Derive only direct, player-known kinship inside the player's active household."""
    target_id = str(target.get("person_id") or "")
    player_id = str(player.get("person_id") or "")
    if not target_id or not player_id or target_id == player_id:
        return None
    if target_id not in household_members:
        return None

    player_parents = _parent_refs(family_state, player_id)
    target_parents = _parent_refs(family_state, target_id)
    if target_id in player_parents:
        return _sexed_relation(target.get("sex"), "father", "mother", "parent")
    if player_id in target_parents:
        return _sexed_relation(target.get("sex"), "son", "daughter", "child")

    marriages = family_state.get("marriages", {}) if isinstance(family_state, Mapping) else {}
    if isinstance(marriages, Mapping):
        for marriage in marriages.values():
            if not isinstance(marriage, Mapping) or marriage.get("status") != "married":
                continue
            spouses = marriage.get("spouse_refs", [])
            if isinstance(spouses, list) and player_id in spouses and target_id in spouses:
                return _sexed_relation(target.get("sex"), "husband", "wife", "spouse")

    if player_parents and target_parents and set(player_parents) == set(target_parents):
        target_birth = target.get("birth_year")
        player_birth = player.get("birth_year")
        age_prefix = ""
        if isinstance(target_birth, int) and not isinstance(target_birth, bool) and isinstance(player_birth, int) and not isinstance(player_birth, bool):
            if target_birth > player_birth:
                age_prefix = "younger_"
            elif target_birth < player_birth:
                age_prefix = "older_"
        sibling = _sexed_relation(target.get("sex"), "brother", "sister", "sibling")
        return f"{age_prefix}{sibling}"
    return None


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

        # Kinship is a player-facing projection derived from the canonical family owner.
        # Restrict it to the player's active household so hidden/unknown biological
        # relationships elsewhere in family state cannot leak through a person read.
        try:
            meta = self.repository.read_json("state/meta.json")
            player_id = str(meta.get("player_id") or "") if isinstance(meta, Mapping) else ""
            family_state = self.repository.read_json("state/martial-world/family.json")
            household_members = _active_household_members(family_state, player_id)
            if player_id and household_members:
                if person_id == player_id:
                    player = person
                else:
                    _player_path, _player_roster, _player_ordinal, player = roster_person(self.repository, player_id)
                relation = _kinship_to_player(
                    target=person,
                    player=player,
                    family_state=family_state,
                    household_members=household_members,
                )
                if relation:
                    person["kinship_to_player"] = relation
                if person_id == player_id:
                    known_relations: dict[str, str] = {}
                    for relative_id in household_members:
                        if relative_id == player_id:
                            continue
                        try:
                            _relative_path, _relative_roster, _relative_ordinal, relative = roster_person(self.repository, relative_id)
                        except (FileNotFoundError, KeyError, ValueError):
                            continue
                        relative_relation = _kinship_to_player(
                            target=relative,
                            player=player,
                            family_state=family_state,
                            household_members=household_members,
                        )
                        if relative_relation:
                            known_relations[relative_id] = relative_relation
                    if known_relations:
                        person["known_family_relations"] = known_relations
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            pass
        return person
