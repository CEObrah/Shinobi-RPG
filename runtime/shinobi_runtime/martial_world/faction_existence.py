"""Current faction birth/death semantics.

A faction owner may outlive the institution as a dormant estate because dead
people, abandoned property, treasury cash, and equipment are still exact world
facts. ``faction-registry.json`` alone answers which institutions currently
exist and therefore receive autonomous/scheduled turns.

Founding/splitting/merging must conserve exact people and assets before calling
``mark_faction_active``/``register_faction``. This module never spawns bodies,
money, equipment, land, or sites.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Mapping

from .faction_registry import REGISTRY_PATH, register_faction, unregister_faction

_EXTINCT = "extinct"


def faction_is_active(faction: Mapping[str, Any]) -> bool:
    """Whether this owner represents a currently living institution."""
    return str(faction.get("status") or "active") != _EXTINCT


def mark_faction_extinct(faction: Mapping[str, Any]) -> dict[str, Any]:
    """Turn a faction owner into a dormant estate without deleting assets."""
    out = copy.deepcopy(dict(faction))
    fid = out.get("faction_id")
    if not isinstance(fid, str) or not fid:
        raise ValueError("jianghu faction_id missing")
    out["status"] = _EXTINCT
    # These are operational clocks/policies for a living institution. Static
    # identity/profile data still hydrates for historical reads, while treasury,
    # buildings, holdings, enterprise property and inventory remain conserved.
    out.pop("recruitment_season", None)
    return out


def mark_faction_active(faction: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical active state for a newly founded/split institution.

    Active is the default and is omitted from sparse hot state. Callers must
    already have transferred exact people/assets into the new owner.
    """
    out = copy.deepcopy(dict(faction))
    fid = out.get("faction_id")
    if not isinstance(fid, str) or not fid:
        raise ValueError("jianghu faction_id missing")
    out.pop("status", None)
    return out


def register_materialized_faction_bundle(
    *,
    registry: Mapping[str, Any],
    faction: Mapping[str, Any],
    roster: Mapping[str, Any],
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    """Register one already-conserved newly materialized institution.

    This is deliberately not a spawner. The caller must first move exact people,
    cash, equipment and site rights from their prior owners. This function only
    proves that the resulting faction/roster/inventory bundle is internally
    coherent and then makes that institution current.
    """
    fid = faction.get("faction_id")
    if not isinstance(fid, str) or not fid:
        raise ValueError("jianghu faction_id missing")
    if not faction_is_active(faction):
        raise ValueError("cannot register extinct faction")
    if roster.get("faction_ref") != fid:
        raise ValueError("jianghu faction roster identity mismatch")
    if inventory.get("faction_ref") != fid:
        raise ValueError("jianghu faction inventory identity mismatch")
    people = roster.get("people")
    if not isinstance(people, list) or not any(
        isinstance(row, Mapping)
        and not (
            isinstance(row.get("health"), Mapping)
            and row.get("health", {}).get("status") == "dead"
        )
        for row in people
    ):
        raise ValueError("new faction requires at least one living exact member")
    if int(faction.get("treasury_cash", 0)) < 0:
        raise ValueError("jianghu faction treasury invalid")
    return register_faction(registry, fid)


def settle_extinctions_from_touched_rosters(
    *,
    read_json: Callable[[str], Mapping[str, Any]],
    writes: dict[str, Any],
    relations_state: Mapping[str, Any],
    load_faction: Callable[[str], tuple[str, dict[str, Any]]],
    relations_path: str = "state/martial-world/faction-relations.json",
    social_path: str = "state/martial-world/social.json",
) -> dict[str, Any]:
    """Retire factions whose touched roster has no living members.

    Only touched rosters are inspected because an untouched active faction was
    already structurally valid before this frontier. The faction owner and its
    inventory/roster remain addressable as a dormant estate; only current
    institutional existence and current diplomacy are removed.
    """
    raw_registry = writes.get(REGISTRY_PATH)
    if not isinstance(raw_registry, Mapping):
        raw_registry = read_json(REGISTRY_PATH)
    registry = copy.deepcopy(dict(raw_registry))
    refs = registry.get("faction_refs")
    if not isinstance(refs, list):
        raise ValueError("jianghu faction registry refs invalid")
    active = set(str(ref) for ref in refs if isinstance(ref, str))
    extinct: list[str] = []
    faction_updates: dict[str, tuple[str, dict[str, Any]]] = {}

    for path, record in list(writes.items()):
        if not isinstance(record, Mapping) or not path.startswith("state/martial-world/people/"):
            continue
        fid = record.get("faction_ref")
        if not isinstance(fid, str) or fid not in active:
            continue
        people = record.get("people")
        if not isinstance(people, list):
            continue
        if any(
            isinstance(row, Mapping)
            and not (
                isinstance(row.get("health"), Mapping)
                and row.get("health", {}).get("status") == "dead"
            )
            for row in people
        ):
            continue
        fpath, faction = load_faction(fid)
        extinct_faction = mark_faction_extinct(faction)
        writes[fpath] = extinct_faction
        faction_updates[fid] = (fpath, extinct_faction)
        registry = unregister_faction(registry, fid)
        active.discard(fid)
        extinct.append(fid)

    relations_after = copy.deepcopy(dict(writes.get(relations_path) or relations_state))
    if extinct:
        edges = relations_after.get("edges")
        dead = set(extinct)
        if isinstance(edges, list):
            relations_after["edges"] = [
                copy.deepcopy(dict(row))
                for row in edges
                if isinstance(row, Mapping)
                and row.get("from_faction") not in dead
                and row.get("to_faction") not in dead
            ]
        coalitions = relations_after.get("coalitions")
        if isinstance(coalitions, dict):
            for ref, row in list(coalitions.items()):
                members = {str(x) for x in row.get("member_faction_refs", []) if isinstance(x, str)} if isinstance(row, Mapping) else set()
                target = str(row.get("target_faction_ref") or "") if isinstance(row, Mapping) else ""
                if not isinstance(row, Mapping) or target in dead or bool(members & dead):
                    coalitions.pop(ref, None)
            if not coalitions:
                relations_after.pop("coalitions", None)
        writes[relations_path] = relations_after
        writes[REGISTRY_PATH] = registry

        # A loyalty vow to an institution is a current actionable commitment,
        # not a memorial-history record. Once the institution ceases to exist,
        # close that exact faction-scoped vow immediately. Person-scoped vows
        # are handled by the death lifecycle and remain independent.
        raw_social = writes.get(social_path)
        if not isinstance(raw_social, Mapping):
            raw_social = read_json(social_path)
        if not isinstance(raw_social, Mapping):
            raise ValueError("jianghu social authority invalid during faction extinction")
        social_after = copy.deepcopy(dict(raw_social))
        vows = social_after.get("vows", {})
        if not isinstance(vows, dict):
            raise ValueError("jianghu social vows invalid during faction extinction")
        for ref, row in list(vows.items()):
            if not isinstance(row, Mapping) or str(row.get("faction_ref") or "") in dead:
                if isinstance(row, Mapping) and not row.get("faction_ref"):
                    continue
                vows.pop(ref, None)
        if not vows:
            social_after.pop("vows", None)
        if social_after != raw_social:
            writes[social_path] = social_after

    return {
        "registry": registry,
        "relations": relations_after,
        "extinct_refs": sorted(extinct),
        "faction_updates": faction_updates,
    }


__all__ = ["faction_is_active", "mark_faction_active", "mark_faction_extinct", "register_materialized_faction_bundle", "settle_extinctions_from_touched_rosters"]
