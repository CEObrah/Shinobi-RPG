"""Single physical-presence authority for exact Jianghu people.

`state/scene.json` is presentation/continuity only.  Mechanics that need to know
where an exact person physically is must derive that answer from exact person
state plus the active owners that can temporarily supersede a stored endpoint:
custody, exact combat, and route movement.

This module never reveals that information to a player by itself.  Read APIs
must still apply the normal observation/knowledge boundary before exposing an
NPC's effective presence.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from shinobi_runtime.martial_world.route_activity import ROUTE_SERVICE_STATUSES

_ROUTE_OPERATIONS = "state/martial-world/route-operations.json"
_CUSTODY = "state/martial-world/custody.json"
_COMBATS = "state/martial-world/combats.json"
_TERMINAL_CUSTODY = frozenset({"released", "escaped", "rescued", "executed"})


def _read_optional(read_json: Callable[[str], Any], path: str) -> Any:
    try:
        return read_json(path)
    except (FileNotFoundError, KeyError):
        return None


def combat_person_arrived(combat: Mapping[str, Any], person_ref: str) -> bool:
    """Return whether one registered combat member has reached the exact combat space.

    A side member may be registered ahead of time as a reinforcement. Membership
    reserves that person for the combat but must not teleport them into its exact
    geometry before ``reinforcement_at_ms``. Invalid reinforcing timing fails
    closed as not-yet-arrived.
    """
    combatants = combat.get("combatants") if isinstance(combat.get("combatants"), Mapping) else {}
    state = combatants.get(person_ref) if isinstance(combatants, Mapping) else None
    if not isinstance(state, Mapping):
        return True
    statuses = {
        str(value)
        for value in state.get("status_families", [])
        if isinstance(value, str)
    } if isinstance(state.get("status_families"), list) else set()
    if "reinforcing" not in statuses:
        return True
    elapsed = combat.get("elapsed_ms", 0)
    due = state.get("reinforcement_at_ms")
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, int)
        or isinstance(due, bool)
        or not isinstance(due, int)
    ):
        return False
    return max(0, elapsed) >= max(0, due)


def active_custody_for_person(read_json: Callable[[str], Any], person_ref: str) -> tuple[str, Mapping[str, Any]] | None:
    state = _read_optional(read_json, _CUSTODY)
    rows = state.get("records", []) if isinstance(state, Mapping) else []
    if not isinstance(rows, list):
        return None
    # The custody lifecycle is exact and should permit only one active owner;
    # newest storage order wins defensively if old data contains duplicates.
    for row in reversed(rows):
        if (
            isinstance(row, Mapping)
            and str(row.get("person_ref") or "") == person_ref
            and str(row.get("status") or "") not in _TERMINAL_CUSTODY
        ):
            return str(row.get("custody_id") or f"custody:{person_ref}"), row
    return None


def active_combat_for_person(read_json: Callable[[str], Any], person_ref: str) -> tuple[str, Mapping[str, Any]] | None:
    state = _read_optional(read_json, _COMBATS)
    rows = state.get("combats", {}) if isinstance(state, Mapping) else {}
    if not isinstance(rows, Mapping):
        return None
    for combat_ref, combat in rows.items():
        if not isinstance(combat_ref, str) or not isinstance(combat, Mapping) or combat.get("status") != "active":
            continue
        combatants = combat.get("combatants") if isinstance(combat.get("combatants"), Mapping) else {}
        sides = combat.get("sides") if isinstance(combat.get("sides"), Mapping) else {}
        registered = person_ref in combatants
        if not registered:
            for members in sides.values():
                if isinstance(members, list) and person_ref in members:
                    registered = True
                    break
        if registered and combat_person_arrived(combat, person_ref):
            return combat_ref, combat
    return None


def active_route_for_person(read_json: Callable[[str], Any], person_ref: str) -> tuple[str, Mapping[str, Any]] | None:
    state = _read_optional(read_json, _ROUTE_OPERATIONS)
    rows = state.get("movements", {}) if isinstance(state, Mapping) else {}
    if not isinstance(rows, Mapping):
        return None
    for movement_ref, movement in rows.items():
        if not isinstance(movement_ref, str) or not isinstance(movement, Mapping):
            continue
        if str(movement.get("status") or "") not in ROUTE_SERVICE_STATUSES:
            continue
        participants = movement.get("participant_refs") if isinstance(movement.get("participant_refs"), list) else []
        # protected/captive/rescued refs may be stored separately by some route
        # owners but are physically carried by the movement all the same.
        carried = []
        for key in ("protected_person_refs", "captive_refs", "rescued_refs"):
            values = movement.get(key)
            if isinstance(values, list):
                carried.extend(str(x) for x in values if isinstance(x, str))
        if person_ref in participants or person_ref in carried:
            return movement_ref, movement
    return None


def effective_person_presence(
    read_json: Callable[[str], Any],
    person_ref: str,
    *,
    person: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the single mechanical physical owner of one exact person.

    Precedence is custody > arrived exact combat > route > stored person
    location. Combat may itself occur on a route. Its exact ``zone_ref`` is the
    most specific live physical location once that person has actually reached
    the combat timeline. A future registered reinforcement remains in its prior
    physical owner until the reinforcement clock arrives.
    """
    ref = str(person_ref)
    custody = active_custody_for_person(read_json, ref)
    if custody is not None:
        owner_ref, row = custody
        location = row.get("location_ref") or row.get("detention_site_ref")
        display = str(location) if isinstance(location, str) and location else None

        # A custody record may explicitly bind the prisoner to the exact route
        # movement carrying them home. Custody still owns restraint/agency, but
        # the route owner owns physical geometry. Treating that movement ref as
        # a detention *site* makes the prisoner mechanically non-colocated with
        # the guards in the same convoy. Only accept this handoff when the live
        # route carrying this exact person has the same owner ref saved by
        # custody, so a stale route cannot override a real static stockade.
        movement = active_route_for_person(read_json, ref)
        if movement is not None and display == str(movement[0]):
            movement_ref, movement_row = movement
            route_ref = movement_row.get("route_ref")
            if not isinstance(route_ref, str) or not route_ref:
                route_refs = movement_row.get("route_refs") if isinstance(movement_row.get("route_refs"), list) else []
                index = max(0, int(movement_row.get("route_index", 0) or 0))
                route_ref = route_refs[index] if index < len(route_refs) and isinstance(route_refs[index], str) else None
            status = str(movement_row.get("status") or "")
            rest_place = movement_row.get("rest_place_ref")
            if status in {"lodging_rest", "field_rest"} and isinstance(rest_place, str) and rest_place:
                physical_location = rest_place
                physical_space = f"site:{rest_place}" if rest_place.startswith("site.") else f"movement:{movement_ref}:rest:{rest_place}"
            else:
                physical_location = route_ref
                physical_space = f"movement:{movement_ref}"
            return {
                "person_ref": ref,
                "location_ref": physical_location,
                "space_ref": physical_space,
                "presence_kind": "custody",
                "owner_ref": owner_ref,
                "physical_owner_ref": movement_ref,
                "available_for_site_activity": False,
            }

        return {
            "person_ref": ref,
            "location_ref": display,
            # Custody owns availability/agency, not a fictitious private geometry.
            # A detained person remains physically at the exact detention site so
            # guards, rescuers, combatants, and other site mechanics can establish
            # real co-presence through the same universal resolver.
            "space_ref": f"site:{display}" if display else f"custody:{owner_ref}",
            "presence_kind": "custody",
            "owner_ref": owner_ref,
            "available_for_site_activity": False,
        }

    combat = active_combat_for_person(read_json, ref)
    if combat is not None:
        owner_ref, row = combat
        location = row.get("zone_ref") or row.get("location_ref") or row.get("site_ref")
        display = str(location) if isinstance(location, str) and location else None
        return {
            "person_ref": ref,
            "location_ref": display,
            "space_ref": f"combat:{owner_ref}",
            "presence_kind": "combat",
            "owner_ref": owner_ref,
            "available_for_site_activity": False,
        }

    movement = active_route_for_person(read_json, ref)
    if movement is not None:
        owner_ref, row = movement
        route_ref = row.get("route_ref")
        if not isinstance(route_ref, str) or not route_ref:
            route_refs = row.get("route_refs") if isinstance(row.get("route_refs"), list) else []
            index = max(0, int(row.get("route_index", 0) or 0))
            route_ref = route_refs[index] if index < len(route_refs) and isinstance(route_refs[index], str) else None
        status = str(row.get("status") or "")
        rest_place = row.get("rest_place_ref")
        if status in {"lodging_rest", "field_rest"} and isinstance(rest_place, str) and rest_place:
            display = rest_place
            space_ref = f"site:{rest_place}" if rest_place.startswith("site.") else f"movement:{owner_ref}:rest:{rest_place}"
            available = rest_place.startswith("site.")
        else:
            display = route_ref
            # Sharing a road identifier is not co-presence. Only people owned by
            # the same exact movement are physically traveling together.
            space_ref = f"movement:{owner_ref}"
            available = False
        return {
            "person_ref": ref,
            "location_ref": display,
            "space_ref": space_ref,
            "presence_kind": "route",
            "owner_ref": owner_ref,
            "available_for_site_activity": available,
        }

    location = None
    if isinstance(person, Mapping):
        raw = person.get("location_ref")
        location = str(raw) if isinstance(raw, str) and raw else None
    return {
        "person_ref": ref,
        "location_ref": location,
        "space_ref": f"site:{location}" if isinstance(location, str) and location else None,
        "presence_kind": "person",
        "owner_ref": ref,
        "available_for_site_activity": True,
    }


def physical_unavailable_person_refs(read_json: Callable[[str], Any]) -> set[str]:
    """Return exact people whose bodies are owned away from ordinary site life.

    Registered future combat reinforcements remain unavailable for unrelated
    activities even before arrival. Their *location*, however, remains owned by
    route/person state until ``combat_person_arrived`` becomes true.
    """
    refs: set[str] = set()
    custody = _read_optional(read_json, _CUSTODY)
    for row in custody.get("records", []) if isinstance(custody, Mapping) and isinstance(custody.get("records"), list) else []:
        if isinstance(row, Mapping) and str(row.get("status") or "") not in _TERMINAL_CUSTODY and isinstance(row.get("person_ref"), str):
            refs.add(str(row["person_ref"]))
    combats = _read_optional(read_json, _COMBATS)
    rows = combats.get("combats", {}) if isinstance(combats, Mapping) else {}
    if isinstance(rows, Mapping):
        for combat in rows.values():
            if not isinstance(combat, Mapping) or combat.get("status") != "active":
                continue
            combatants = combat.get("combatants") if isinstance(combat.get("combatants"), Mapping) else {}
            refs.update(str(x) for x in combatants if isinstance(x, str))
            sides = combat.get("sides") if isinstance(combat.get("sides"), Mapping) else {}
            for members in sides.values():
                if isinstance(members, list):
                    refs.update(str(x) for x in members if isinstance(x, str))
    routes = _read_optional(read_json, _ROUTE_OPERATIONS)
    movements = routes.get("movements", {}) if isinstance(routes, Mapping) else {}
    if isinstance(movements, Mapping):
        for movement in movements.values():
            if not isinstance(movement, Mapping) or str(movement.get("status") or "") not in ROUTE_SERVICE_STATUSES:
                continue
            for key in ("participant_refs", "protected_person_refs", "captive_refs", "rescued_refs"):
                values = movement.get(key)
                if isinstance(values, list):
                    refs.update(str(x) for x in values if isinstance(x, str))
    return refs


def same_effective_location(
    read_json: Callable[[str], Any],
    left_ref: str,
    right_ref: str,
    *,
    left_person: Mapping[str, Any] | None = None,
    right_person: Mapping[str, Any] | None = None,
) -> bool:
    left = effective_person_presence(read_json, left_ref, person=left_person)
    right = effective_person_presence(read_json, right_ref, person=right_person)
    return bool(left.get("space_ref") and left.get("space_ref") == right.get("space_ref"))


__all__ = [
    "active_combat_for_person", "active_custody_for_person", "active_route_for_person",
    "combat_person_arrived", "effective_person_presence", "physical_unavailable_person_refs",
    "same_effective_location",
]
