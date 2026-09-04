"""Exact due-birth frontier for persistent Jianghu family state."""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .death_lifecycle import exact_person_index, is_living
from .faction_registry import current_faction_refs
from .faction_state import compact_faction_state, faction_path, hydrate_faction_state, roster_path
from .family_simulation import newborn_person
from .handoffs import classify_handoff
from .physical_presence import active_route_for_person, effective_person_presence
from .person_state import compact_roster_state, hydrate_roster_state, reconcile_faction_population

_FAMILY = "state/martial-world/family.json"
_META = "state/meta.json"
_INDEPENDENTS = "state/martial-world/independent-people.json"
_CIVIC = "state/martial-world/civic-people.json"
_ROUTE_OPERATIONS = "state/martial-world/route-operations.json"


def _record(read_json: Callable[[str], Any], writes: Mapping[str, Any], path: str) -> Any:
    if path in writes:
        return copy.deepcopy(writes[path])
    return copy.deepcopy(read_json(path))


def _world_names(read_json: Callable[[str], Any], writes: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    view = lambda path: _record(read_json, writes, path)
    for fid in current_faction_refs(view):
        try:
            owner = view(roster_path(fid))
        except FileNotFoundError:
            continue
        people = owner.get("people", []) if isinstance(owner, Mapping) else []
        if isinstance(people, list):
            names.update(
                str(person.get("name")) for person in people
                if isinstance(person, Mapping) and isinstance(person.get("name"), str) and person.get("name")
            )
    for path in (_INDEPENDENTS, _CIVIC):
        try:
            owner = view(path)
        except FileNotFoundError:
            continue
        people = owner.get("people", []) if isinstance(owner, Mapping) else []
        if isinstance(people, list):
            names.update(
                str(person.get("name")) for person in people
                if isinstance(person, Mapping) and isinstance(person.get("name"), str) and person.get("name")
            )
    return names


def _parent_claim_factions(family: Mapping[str, Any], parents: Sequence[Mapping[str, Any]]) -> set[str]:
    claims = family.get("succession_claims", {}) if isinstance(family, Mapping) else {}
    out: set[str] = set()
    for parent in parents:
        ref = str(parent.get("person_id") or "")
        fid = str(parent.get("faction_ref") or "")
        offices = parent.get("standing_offices", []) if isinstance(parent.get("standing_offices"), list) else []
        if fid and "leader" in offices:
            out.add(fid)
        if isinstance(claims, Mapping):
            if any(
                isinstance(row, Mapping) and row.get("faction_ref") == fid and str(row.get("person_ref") or "") == ref
                for row in claims.values()
            ):
                if fid:
                    out.add(fid)
    return out


def settle_due_births(
    *, read_json: Callable[[str], Any], writes: dict[str, Any],
    events: Sequence[Mapping[str, Any]], at: datetime,
) -> dict[str, Any]:
    due = [row for row in events if isinstance(row, Mapping) and row.get("kind") == "family_birth_due"]
    if not due:
        return {"reviews": [], "handoffs": []}
    raw_family = _record(read_json, writes, _FAMILY)
    family = copy.deepcopy(dict(raw_family)) if isinstance(raw_family, Mapping) else {}
    try:
        meta = _record(read_json, writes, _META)
        player_ref = str(meta.get("player_id") or "") if isinstance(meta, Mapping) else ""
    except FileNotFoundError:
        player_ref = ""
    names = _world_names(read_json, writes)
    reviews: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []

    for event in sorted(due, key=lambda row: (str(row.get("owner_ref") or ""), str(row.get("event_id") or ""))):
        marriage_ref = event.get("marriage_ref")
        child_ref = event.get("child_ref")
        if not isinstance(marriage_ref, str) or not isinstance(child_ref, str):
            continue
        marriages = family.get("marriages", {}) if isinstance(family.get("marriages"), Mapping) else {}
        marriage = marriages.get(marriage_ref) if isinstance(marriages, Mapping) else None
        pregnancy = marriage.get("pregnancy") if isinstance(marriage, Mapping) else None
        if not isinstance(marriage, Mapping) or not isinstance(pregnancy, Mapping) or pregnancy.get("child_ref") != child_ref:
            reviews.append({"kind": "family_birth_due", "event_id": event.get("event_id"), "child_ref": child_ref, "result": "pregnancy_unresolved"})
            continue
        try:
            if at < datetime.fromisoformat(str(pregnancy.get("due_at"))):
                raise ValueError("jianghu birth before due date")
        except ValueError:
            raise

        view = lambda path: _record(read_json, writes, path)
        faction_refs = current_faction_refs(view)
        index = exact_person_index(read_json=read_json, writes=writes, faction_refs=faction_refs)
        mother_ref = str(pregnancy.get("mother_ref") or "")
        father_ref = str(pregnancy.get("father_ref") or "")
        mother_route = index.get(mother_ref)
        father_route = index.get(father_ref)
        marriage_after = copy.deepcopy(dict(marriage))
        marriage_after.pop("pregnancy", None)

        mother = copy.deepcopy(mother_route.get("person", {})) if isinstance(mother_route, Mapping) else None
        father = copy.deepcopy(father_route.get("person", {})) if isinstance(father_route, Mapping) else None
        if not isinstance(mother, Mapping) or not isinstance(father, Mapping) or not is_living(mother):
            if isinstance(marriages, dict):
                marriages[marriage_ref] = marriage_after
            writes[_FAMILY] = family
            reviews.append({"kind": "family_birth_due", "event_id": event.get("event_id"), "child_ref": child_ref, "result": "pregnancy_ended_without_birth"})
            continue
        if child_ref in index:
            raise ValueError("jianghu duplicate child identity")

        # A cross-faction newborn joins the mother's current faction when she
        # has one. If she is outside a faction but the other spouse belongs to
        # one, that exact institution becomes the deterministic household owner.
        destination_fid = str(mother.get("faction_ref") or father.get("faction_ref") or event.get("owner_ref") or "")
        if not destination_fid:
            if isinstance(marriages, dict):
                marriages[marriage_ref] = marriage_after
            writes[_FAMILY] = family
            reviews.append({"kind": "family_birth_due", "event_id": event.get("event_id"), "child_ref": child_ref, "result": "birth_owner_unresolved"})
            continue

        fpath = faction_path(destination_fid)
        rpath = roster_path(destination_fid)
        faction = hydrate_faction_state(_record(read_json, writes, fpath))
        roster = hydrate_roster_state(_record(read_json, writes, rpath), faction=faction)
        roster_people = roster.get("people", []) if isinstance(roster.get("people"), list) else []

        households = family.setdefault("households", {})
        household_ref = None
        if isinstance(households, Mapping):
            household_ref = next((
                hid for hid, row in households.items()
                if isinstance(row, Mapping) and row.get("faction_ref") == destination_fid
                and (mother_ref in row.get("member_refs", []) or father_ref in row.get("member_refs", []))
            ), None)
        residence = None
        if isinstance(household_ref, str) and isinstance(households.get(household_ref), Mapping):
            residence = households[household_ref].get("residence_ref")
        mother_presence = effective_person_presence(view, mother_ref, person=mother)
        mother_presence_location = mother_presence.get("location_ref")
        # Stored person location remains a durable endpoint/fallback while a
        # finite route owner carries the body.  At a site/custody/combat space,
        # however, the exact physical site/zone is the best newborn location.
        # A traveling newborn is attached to the mother's movement below, so
        # the finite route owner supersedes this fallback until arrival.
        newborn_residence = (
            str(mother_presence_location)
            if isinstance(mother_presence_location, str)
            and mother_presence_location
            and mother_presence.get("presence_kind") != "route"
            else str(residence) if isinstance(residence, str)
            else str(mother.get("location_ref") or father.get("location_ref") or "") or None
        )
        child = newborn_person(
            child_ref=child_ref, mother=mother, father=father, birth_at=at.isoformat(),
            existing_names=names,
            residence_ref=newborn_residence,
        )
        roster_people.append(child)
        roster["people"] = roster_people
        names.add(str(child.get("name") or ""))

        # Birth does not break physical causality. If the mother is already
        # carried by an active route owner, the newborn begins life in that same
        # exact movement as a protected dependent. Merely writing the child into
        # the faction roster would otherwise leave the baby at the mother's stale
        # pre-trip endpoint while the mother keeps traveling.
        mother_route = active_route_for_person(view, mother_ref)
        if mother_route is not None:
            movement_ref, _movement = mother_route
            route_state = copy.deepcopy(_record(read_json, writes, _ROUTE_OPERATIONS))
            movements = route_state.get("movements", {}) if isinstance(route_state, Mapping) else {}
            movement = movements.get(movement_ref) if isinstance(movements, dict) else None
            if not isinstance(movement, dict):
                raise ValueError("jianghu birth route owner unresolved")
            participants = movement.setdefault("participant_refs", [])
            protected = movement.setdefault("protected_person_refs", [])
            if not isinstance(participants, list) or not isinstance(protected, list):
                raise ValueError("jianghu birth route participant owner invalid")
            if child_ref not in participants:
                participants.append(child_ref)
            if child_ref not in protected:
                protected.append(child_ref)
            movement["participant_refs"] = list(dict.fromkeys(str(x) for x in participants if isinstance(x, str) and x))
            movement["protected_person_refs"] = list(dict.fromkeys(str(x) for x in protected if isinstance(x, str) and x))
            movements[movement_ref] = movement
            writes[_ROUTE_OPERATIONS] = route_state

        parentage = family.setdefault("parentage", {})
        if isinstance(parentage, dict):
            parentage[child_ref] = {"parent_refs": [mother_ref, father_ref]}

        claims = family.setdefault("succession_claims", {})
        if isinstance(claims, dict):
            for claim_fid in sorted(_parent_claim_factions(family, [mother, father])):
                priorities = [
                    max(0, int(row.get("priority", 0))) for row in claims.values()
                    if isinstance(row, Mapping) and row.get("faction_ref") == claim_fid
                ]
                claim_key = f"claim:{child_ref}" if claim_fid == destination_fid else f"claim:{claim_fid}:{child_ref}"
                claims.setdefault(claim_key, {
                    "faction_ref": claim_fid,
                    "person_ref": child_ref,
                    "priority": (max(priorities) if priorities else 0) + 1,
                    "basis": "lineal_descendant",
                })

        if isinstance(household_ref, str) and isinstance(households, dict):
            row = copy.deepcopy(dict(households[household_ref]))
            members = row.setdefault("member_refs", [])
            if isinstance(members, list) and child_ref not in members:
                members.append(child_ref)
            households[household_ref] = row

        marriage_after["last_birth_at"] = at.isoformat()
        if isinstance(marriages, dict):
            marriages[marriage_ref] = marriage_after
        writes[_FAMILY] = family
        faction = reconcile_faction_population(faction, roster)
        writes[fpath] = compact_faction_state(faction)
        writes[rpath] = compact_roster_state(roster, faction=faction)

        in_player_household = any(
            isinstance(row, Mapping) and player_ref and player_ref in row.get("member_refs", []) and child_ref in row.get("member_refs", [])
            for row in households.values()
        ) if isinstance(households, Mapping) else False
        notice = {
            "kind": "family_checkin", "event_id": event.get("event_id"), "child_ref": child_ref,
            "faction_ref": destination_fid, "delivered_to_player": bool(in_player_household),
            "requires_player_decision": False,
        }
        handoff = classify_handoff(notice)
        reviews.append({
            "kind": "family_birth_due", "event_id": event.get("event_id"), "child_ref": child_ref,
            "result": "birth", "faction_ref": destination_fid,
            "birth_presence_kind": str(mother_presence.get("presence_kind") or "person"),
            "handoff": handoff,
        })
        if handoff["class"] != "internal":
            handoffs.append({**notice, "handoff": handoff})
    return {"reviews": reviews, "handoffs": handoffs}


__all__ = ["settle_due_births"]
