"""Direct persistent Jianghu state routing helpers."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from shinobi_runtime.martial_world.person_state import (
    compact_person_state,
    home_location_ref,
    hydrate_person_state,
)
from shinobi_runtime.martial_world.faction_state import read_faction, roster_path
from shinobi_runtime.martial_world.training import apply_institutional_training, institutional_training_segment
from shinobi_runtime.martial_world.civic import civic_person, set_civic_person
from shinobi_runtime.martial_world.independent_people import independent_person, set_independent_person
from shinobi_runtime.martial_world.qi import person_current_qi_milli


_PERSON_ROUTE_CACHE: dict[str, tuple[tuple[int, int], tuple[str, ...], dict[str, tuple[str, int]]]] = {}


def _base_repository(repository: Any) -> Any | None:
    """Find the concrete repository behind bounded read overlays."""
    seen: set[int] = set()
    current = repository
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "root", None) is not None:
            return current
        next_obj = None
        for name in ("repository", "_repository", "base", "_base", "_overlay", "_owner"):
            candidate = getattr(current, name, None)
            if candidate is not None and candidate is not current:
                next_obj = candidate
                break
        if next_obj is None:
            read_json = getattr(current, "_read_json", None)
            candidate = getattr(read_json, "__self__", None)
            if candidate is not None and candidate is not current:
                next_obj = candidate
        current = next_obj
    return None


def _base_person_routes(repository: Any) -> tuple[Any, list[str], dict[str, tuple[str, int]]]:
    """Build an in-memory person lookup from authoritative roster owners.

    Filesystem-backed repositories use a cache keyed by roster file metadata.
    Lightweight read views used by tests/tools may expose an in-memory ``rows``
    mapping instead; those are scanned directly. No person-route owner is ever
    required in campaign state.
    """
    base = _base_repository(repository)
    if base is not None:
        roster_dir = Path(base.root) / "state" / "martial-world" / "people"
        directory_stat = roster_dir.stat()
        # RepositoryStore replaces owner images atomically. Replacing, adding, or
        # removing a roster entry changes the parent directory metadata, so one
        # directory stat is enough to invalidate the RAM index. Do not restat all
        # 240 roster files on every exact-person lookup.
        signature = (directory_stat.st_mtime_ns, directory_stat.st_ctime_ns)
        key = str(Path(base.root).resolve())
        cached = _PERSON_ROUTE_CACHE.get(key)
        if cached is not None and cached[0] == signature:
            return base, list(cached[1]), cached[2]
        paths = sorted(roster_dir.glob("*.json"), key=lambda row: row.name)
        routes: dict[str, tuple[str, int]] = {}
        faction_refs: list[str] = []
        for path in paths:
            try:
                owner = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"jianghu roster invalid: {path.name}") from exc
            if not isinstance(owner, Mapping):
                raise ValueError(f"jianghu roster invalid: {path.name}")
            faction_ref = owner.get("faction_ref")
            people = owner.get("people")
            if not isinstance(faction_ref, str) or not faction_ref or not isinstance(people, list):
                raise ValueError(f"jianghu roster owner invalid: {path.name}")
            faction_refs.append(faction_ref)
            for ordinal, raw in enumerate(people):
                person_ref = raw.get("person_id") if isinstance(raw, Mapping) else None
                if not isinstance(person_ref, str) or not person_ref:
                    continue
                if person_ref in routes:
                    raise ValueError(f"duplicate jianghu person identity: {person_ref}")
                routes[person_ref] = (faction_ref, ordinal)
        frozen_refs = tuple(sorted(set(faction_refs)))
        _PERSON_ROUTE_CACHE[key] = (signature, frozen_refs, routes)
        return base, list(frozen_refs), routes

    rows = getattr(repository, "rows", None)
    if not isinstance(rows, Mapping):
        raise TypeError("jianghu person lookup requires roster-backed read access")
    roster_paths = sorted(
        str(path) for path in rows
        if isinstance(path, str)
        and path.startswith("state/martial-world/people/")
        and path.endswith(".json")
    )
    routes: dict[str, tuple[str, int]] = {}
    faction_refs: list[str] = []
    for path in roster_paths:
        owner = repository.read_json(path)
        if not isinstance(owner, Mapping):
            continue
        faction_ref = owner.get("faction_ref")
        people = owner.get("people")
        if not isinstance(faction_ref, str) or not faction_ref or not isinstance(people, list):
            continue
        faction_refs.append(faction_ref)
        for ordinal, raw in enumerate(people):
            person_ref = raw.get("person_id") if isinstance(raw, Mapping) else None
            if not isinstance(person_ref, str) or not person_ref:
                continue
            if person_ref in routes:
                raise ValueError(f"duplicate jianghu person identity: {person_ref}")
            routes[person_ref] = (faction_ref, ordinal)
    return repository, sorted(set(faction_refs)), routes

def _derived_person_routes(repository: Any) -> dict[str, tuple[str, int]]:
    """Return the base roster-derived lookup for diagnostics and normal reads."""
    _base, _paths, routes = _base_person_routes(repository)
    return routes


def _staged_roster_paths(repository: Any) -> list[str]:
    """Return only roster owners explicitly changed by bounded overlays.

    The committed filesystem cache cannot know about a brand-new faction roster
    that exists only in a transaction after-image.  Overlay objects already
    expose their changed paths, so inspect those paths only rather than scanning
    the repository.  Walking the bounded overlay chain also covers nested views
    used by frontier settlement and tests.
    """
    out: set[str] = set()
    seen: set[int] = set()
    current = repository
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        candidates: set[str] = set()
        changed = getattr(current, "changed_paths", None)
        if isinstance(changed, (list, tuple, set, frozenset)):
            candidates.update(str(path) for path in changed if isinstance(path, str))
        for attr in ("_images", "_image_json"):
            images = getattr(current, attr, None)
            if isinstance(images, Mapping):
                candidates.update(str(path) for path in images if isinstance(path, str))
        out.update(
            path for path in candidates
            if path.startswith("state/martial-world/people/") and path.endswith(".json")
        )
        next_obj = None
        for name in ("repository", "_repository", "base", "_base", "_overlay", "_owner"):
            candidate = getattr(current, name, None)
            if candidate is not None and candidate is not current:
                next_obj = candidate
                break
        current = next_obj
    return sorted(out)


def _route_matches_view(
    repository: Any, person_ref: str, route: tuple[str, int], *,
    roster_owner_cache: dict[str, dict[str, Any]] | None = None,
) -> bool:
    faction_ref, ordinal = route
    path = roster_path(faction_ref)
    try:
        if roster_owner_cache is not None and path in roster_owner_cache:
            roster = roster_owner_cache[path]
        else:
            raw = repository.read_json(path)
            roster = raw if isinstance(raw, dict) else dict(raw)
            if roster_owner_cache is not None:
                roster_owner_cache[path] = roster
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        return False
    people = roster.get("people") if isinstance(roster, Mapping) else None
    return bool(
        isinstance(people, list)
        and 0 <= ordinal < len(people)
        and isinstance(people[ordinal], Mapping)
        and people[ordinal].get("person_id") == person_ref
    )


def person_route(
    repository: Any, person_ref: str, *,
    roster_owner_cache: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, int]:
    """Resolve one person from authoritative rosters, honoring staged overlays.

    The cached base lookup makes ordinary reads O(1). If an overlay has moved,
    added, or removed a person in the current transaction, verify the candidate
    against that overlay and only then perform a bounded roster scan.
    """
    base, faction_refs, routes = _base_person_routes(repository)
    matches: set[tuple[str, int]] = set()
    candidate = routes.get(person_ref)
    if candidate is not None:
        # A concrete base repository is exactly what the index was built from;
        # no second owner read is needed. Overlay views must verify the candidate
        # because the current transaction may already have moved this person.
        if repository is base or _route_matches_view(
            repository, person_ref, candidate, roster_owner_cache=roster_owner_cache,
        ):
            matches.add(candidate)

    # Only fall back across committed-era rosters when the cached candidate did
    # not survive the overlay.  This handles ordinary same-transaction moves
    # without imposing a repository scan on the normal O(1) read path.
    if not matches:
        for faction_ref in faction_refs:
            path = roster_path(faction_ref)
            try:
                if roster_owner_cache is not None and path in roster_owner_cache:
                    roster = roster_owner_cache[path]
                else:
                    raw = repository.read_json(path)
                    roster = raw if isinstance(raw, dict) else dict(raw)
                    if roster_owner_cache is not None:
                        roster_owner_cache[path] = roster
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                continue
            people = roster.get("people") if isinstance(roster, Mapping) else None
            if not isinstance(people, list):
                continue
            for ordinal, raw in enumerate(people):
                if isinstance(raw, Mapping) and raw.get("person_id") == person_ref:
                    matches.add((faction_ref, ordinal))

    # A newly founded/split faction may have no committed-era roster path yet.
    # Search only roster owners explicitly present in staged after-images.  Do
    # this even when the cached candidate still matches so a staged duplicate
    # identity fails closed rather than being hidden by an early return.
    for path in _staged_roster_paths(repository):
        try:
            if roster_owner_cache is not None and path in roster_owner_cache:
                roster = roster_owner_cache[path]
            else:
                raw = repository.read_json(path)
                roster = raw if isinstance(raw, dict) else dict(raw)
                if roster_owner_cache is not None:
                    roster_owner_cache[path] = roster
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            continue
        faction_ref = roster.get("faction_ref") if isinstance(roster, Mapping) else None
        people = roster.get("people") if isinstance(roster, Mapping) else None
        if not isinstance(faction_ref, str) or not faction_ref or not isinstance(people, list):
            continue
        for ordinal, raw in enumerate(people):
            if isinstance(raw, Mapping) and raw.get("person_id") == person_ref:
                matches.add((faction_ref, ordinal))

    if not matches:
        raise KeyError(person_ref)
    if len(matches) != 1:
        raise ValueError(f"duplicate jianghu person identity: {person_ref}")
    return next(iter(matches))


def roster_person(
    repository: Any, person_ref: str, *,
    training_segment_cache: dict[str, Mapping[str, Any] | None] | None = None,
    roster_owner_cache: dict[str, dict[str, Any]] | None = None,
    faction_cache: dict[str, Mapping[str, Any]] | None = None,
) -> tuple[str, dict[str, Any], int, dict[str, Any]]:
    try:
        faction_ref, ordinal = person_route(
            repository, person_ref, roster_owner_cache=roster_owner_cache,
        )
    except (FileNotFoundError, KeyError):
        try:
            return independent_person(repository, person_ref)
        except (FileNotFoundError, KeyError):
            return civic_person(repository, person_ref)
    path = roster_path(faction_ref)
    if roster_owner_cache is not None and path in roster_owner_cache:
        roster = roster_owner_cache[path]
    else:
        roster_raw = repository.read_json(path)
        roster = roster_raw if isinstance(roster_raw, dict) else dict(roster_raw)
        if roster_owner_cache is not None:
            roster_owner_cache[path] = roster
        else:
            roster = copy.deepcopy(roster)
    people = roster.get("people") if isinstance(roster, Mapping) else None
    if not isinstance(people, list) or ordinal < 0 or ordinal >= len(people):
        raise ValueError("jianghu person roster invalid")
    raw_person = people[ordinal]
    if not isinstance(raw_person, Mapping) or raw_person.get("person_id") != person_ref:
        raise ValueError("jianghu person route identity mismatch")
    if roster.get("faction_ref") != faction_ref:
        raise ValueError("jianghu person roster faction invalid")
    if faction_cache is not None and faction_ref in faction_cache:
        faction = faction_cache[faction_ref]
    else:
        _, faction = read_faction(repository, faction_ref)
        if faction_cache is not None:
            faction_cache[faction_ref] = faction
    person = hydrate_person_state(
        raw_person,
        faction_ref=faction_ref,
        home_location=home_location_ref(faction),
        include_storage_defaults=True,
    )
    segment = None
    if training_segment_cache is not None:
        if faction_ref not in training_segment_cache:
            training_segment_cache[faction_ref] = institutional_training_segment(faction, people)
        segment = training_segment_cache.get(faction_ref)
    person = apply_institutional_training(
        person, faction=faction, roster_people=people, segment=segment,
    )
    return path, roster, ordinal, person


def set_roster_person(
    roster: Mapping[str, Any], ordinal: int, person: Mapping[str, Any], *, mutate: bool = False,
) -> dict[str, Any]:
    if roster.get("schema") == "jianghu-civic-people-state-1.0":
        if mutate:
            raise ValueError("mutable roster fast path supports faction rosters only")
        return set_civic_person(roster, ordinal, person)
    if roster.get("schema") == "jianghu-independent-people-state-1.0":
        if mutate:
            raise ValueError("mutable roster fast path supports faction rosters only")
        return set_independent_person(roster, ordinal, person)
    if mutate:
        if not isinstance(roster, dict):
            raise ValueError("mutable roster fast path requires dict owner")
        out = roster
    else:
        out = copy.deepcopy(dict(roster))
    people = out.get("people")
    if not isinstance(people, list) or ordinal < 0 or ordinal >= len(people):
        raise ValueError("jianghu roster ordinal invalid")
    faction_ref = out.get("faction_ref")
    if not isinstance(faction_ref, str) or not faction_ref:
        raise ValueError("jianghu roster faction invalid")
    people[ordinal] = compact_person_state(person, faction_ref=faction_ref)
    return out


def age_at_year(person: Mapping[str, Any], year: int) -> int:
    birth = int(person.get("birth_year", year))
    return max(0, int(year) - birth)


def player_view_from_person(person: Mapping[str, Any]) -> dict[str, Any]:
    """Build the bounded player view from the same authoritative person owner."""
    return {
        "person_id": person.get("person_id"),
        "name": person.get("name"),
        "official_rank_or_status": person.get("membership_grade"),
        "current_location_id": person.get("location_ref"),
        "condition": copy.deepcopy(person.get("health", {})),
        "attributes": copy.deepcopy(person.get("attributes", {})),
        "martial_skills": copy.deepcopy(person.get("martial_skills", {})),
        "professional_skills": copy.deepcopy(person.get("professional_skills", {})),
        "aptitudes": copy.deepcopy(person.get("aptitudes", {})),
        "appearance": int(person.get("appearance", 0)),
        "body_mass_kg": int(person.get("body_mass_kg", 70)),
        "qi": int(person.get("qi", 0)),
        "qi_control": int(person.get("qi_control", 0)),
        "current_qi": person_current_qi_milli(person) // 1000,
        "current_qi_milli": person_current_qi_milli(person),
        "personal_cash": int(person.get("personal_cash", 0)),
        "faction_ref": person.get("faction_ref"),
        "affiliation_ref": person.get("affiliation_ref"),
        "social_rank": person.get("social_rank"),
        "standing_offices": copy.deepcopy(person.get("standing_offices", [])),
        "standing_retinues": copy.deepcopy(person.get("standing_retinues", [])),
        "combat_doctrine_ref": person.get("combat_doctrine_ref"),
    }


__all__ = ["age_at_year", "person_route", "player_view_from_person", "roster_person", "set_roster_person"]
