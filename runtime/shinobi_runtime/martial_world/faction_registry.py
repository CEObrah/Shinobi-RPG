"""Current Jianghu faction-existence registry.

The authored world seed answers which institutions existed at campaign creation.
This sparse mutable owner answers which factions exist *now*.  Simulation loops
must enumerate this registry so destruction, splintering, mergers, and future
player-founded organizations do not require rewriting the scheduler.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping, Sequence

REGISTRY_PATH = "state/martial-world/faction-registry.json"
SCHEMA = "jianghu-faction-registry-1.0"


def current_faction_refs(read_json: Callable[[str], Any]) -> list[str]:
    state = read_json(REGISTRY_PATH)
    if not isinstance(state, Mapping) or state.get("schema") != SCHEMA:
        raise ValueError("jianghu faction registry invalid")
    refs = state.get("faction_refs")
    if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes)):
        raise ValueError("jianghu faction registry refs invalid")
    normalized = [str(ref) for ref in refs if isinstance(ref, str) and ref]
    if len(normalized) != len(refs) or len(set(normalized)) != len(normalized):
        raise ValueError("jianghu faction registry refs invalid")
    return sorted(normalized)


def current_faction_refs_at_place(
    read_json: Callable[[str], Any], *, place_ref: str, sites: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return active factions whose current headquarters is in ``place_ref``.

    Authored settlement indexes are bootstrap geography only. Current presence
    follows the mutable faction registry and each live faction owner after
    foundations, splits, headquarters moves, mergers, claims, and extinctions.
    """
    if not isinstance(place_ref, str) or not place_ref:
        return []
    from .faction_state import faction_path, hydrate_faction_state

    site_rows = sites if isinstance(sites, Mapping) else {}
    out: list[str] = []
    for faction_ref in current_faction_refs(read_json):
        try:
            raw = read_json(faction_path(faction_ref))
        except FileNotFoundError:
            continue
        if not isinstance(raw, Mapping):
            continue
        try:
            faction = hydrate_faction_state(raw)
        except (KeyError, TypeError, ValueError):
            continue
        headquarters = str(faction.get("headquarters") or "")
        local_site_ref = str(faction.get("local_site_ref") or "")
        local_site = site_rows.get(local_site_ref) if local_site_ref else None
        local_parent = str(local_site.get("parent_place_ref") or "") if isinstance(local_site, Mapping) else ""
        current_place = headquarters or local_parent
        if current_place == place_ref:
            out.append(faction_ref)
    return sorted(set(out))


def register_faction(state: Mapping[str, Any], faction_ref: str) -> dict[str, Any]:
    if not isinstance(faction_ref, str) or not faction_ref:
        raise ValueError("jianghu faction ref invalid")
    out = deepcopy(dict(state))
    if out.get("schema") != SCHEMA:
        raise ValueError("jianghu faction registry invalid")
    refs = out.get("faction_refs")
    if not isinstance(refs, list):
        raise ValueError("jianghu faction registry refs invalid")
    if faction_ref not in refs:
        refs.append(faction_ref)
        refs.sort()
    dormant = out.setdefault("dormant_estate_refs", [])
    if not isinstance(dormant, list):
        raise ValueError("jianghu dormant estate refs invalid")
    out["dormant_estate_refs"] = sorted(ref for ref in dormant if ref != faction_ref)
    return out


def unregister_faction(state: Mapping[str, Any], faction_ref: str) -> dict[str, Any]:
    out = deepcopy(dict(state))
    if out.get("schema") != SCHEMA:
        raise ValueError("jianghu faction registry invalid")
    refs = out.get("faction_refs")
    if not isinstance(refs, list):
        raise ValueError("jianghu faction registry refs invalid")
    out["faction_refs"] = sorted(ref for ref in refs if ref != faction_ref)
    dormant = out.setdefault("dormant_estate_refs", [])
    if not isinstance(dormant, list):
        raise ValueError("jianghu dormant estate refs invalid")
    if faction_ref not in dormant:
        dormant.append(faction_ref)
    out["dormant_estate_refs"] = sorted(set(str(ref) for ref in dormant if isinstance(ref, str) and ref))
    return out


__all__ = ["REGISTRY_PATH", "SCHEMA", "current_faction_refs", "current_faction_refs_at_place", "register_faction", "unregister_faction"]
