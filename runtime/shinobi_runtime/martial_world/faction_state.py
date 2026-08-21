"""Sparse Jianghu faction-state hydration and canonical storage.

Static identity, doctrine, curriculum, and policy live in game data keyed by
faction_id. Mutable faction owners persist current facts plus only campaign
policy overrides that differ from that static profile.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from .faction_politics import faction_camp

_ROOT = Path(__file__).resolve().parents[3]
_WORLD_SEED = _ROOT / "game" / "data" / "martial-world" / "world-seed.json"

_STATIC_SCALARS = ("name", "type", "outlaw_subtype")
_STATIC_MAPPINGS = ("training", "doctrine", "recruitment_policy", "autonomy_policy", "outlaw_policy")
_STATIC_LISTS = ("operating_routes",)


def faction_path(faction_ref: str) -> str:
    return f"state/martial-world/factions/{faction_ref}.json"


def roster_path(faction_ref: str) -> str:
    return f"state/martial-world/people/{faction_ref}.json"


def inventory_path(faction_ref: str) -> str:
    return f"state/martial-world/inventories/{faction_ref}.json"


@lru_cache(maxsize=1)
def _static_factions() -> dict[str, dict[str, Any]]:
    data = json.loads(_WORLD_SEED.read_text(encoding="utf-8"))
    rows = data.get("martial_factions", {}) if isinstance(data, Mapping) else {}
    if not isinstance(rows, Mapping):
        raise ValueError("jianghu world seed faction table invalid")
    return {str(fid): copy.deepcopy(dict(row)) for fid, row in rows.items() if isinstance(row, Mapping)}


def faction_profile(faction_ref: str) -> dict[str, Any] | None:
    row = _static_factions().get(faction_ref)
    return copy.deepcopy(row) if row is not None else None



def faction_type(faction_ref: str | None) -> str:
    """Return the authored static institution type from the faction profile.

    ``type`` is intentionally hydrated from ``world-seed.json`` rather than
    duplicated into mutable faction state.
    """
    if not isinstance(faction_ref, str) or not faction_ref:
        return ""
    profile = faction_profile(faction_ref)
    value = profile.get("type") if isinstance(profile, Mapping) else None
    return str(value) if isinstance(value, str) else ""


def resolved_faction_type(faction: Mapping[str, Any]) -> str:
    """Resolve the logical type from an already-hydrated view or its ID."""
    value = faction.get("type") if isinstance(faction, Mapping) else None
    if isinstance(value, str) and value:
        return value
    fid = faction.get("faction_id") if isinstance(faction, Mapping) else None
    return faction_type(str(fid)) if isinstance(fid, str) else ""

def _merge_mapping(base: Any, override: Any) -> dict[str, Any]:
    result = copy.deepcopy(dict(base)) if isinstance(base, Mapping) else {}
    if isinstance(override, Mapping):
        result.update(copy.deepcopy(dict(override)))
    return result


def hydrate_faction_state(faction: Mapping[str, Any]) -> dict[str, Any]:
    """Return the logical faction view from sparse hot state."""
    out = copy.deepcopy(dict(faction))
    fid = out.get("faction_id")
    if not isinstance(fid, str) or not fid:
        raise ValueError("jianghu faction_id missing")
    profile = faction_profile(fid)
    if profile is None:
        return out
    for key in _STATIC_SCALARS:
        if key in profile:
            out.setdefault(key, copy.deepcopy(profile[key]))
    for key in _STATIC_LISTS:
        if key in profile:
            out.setdefault(key, copy.deepcopy(profile[key]))
    for key in _STATIC_MAPPINGS:
        if key in profile:
            out[key] = _merge_mapping(profile[key], out.get(key))
    camp = faction_camp(fid)
    if camp:
        out.setdefault("jianghu_camp", camp)
    return out


def _mapping_deviation(current: Any, baseline: Any) -> dict[str, Any]:
    if not isinstance(current, Mapping):
        return {}
    base = baseline if isinstance(baseline, Mapping) else {}
    return {str(k): copy.deepcopy(v) for k, v in current.items() if k not in base or base.get(k) != v}


def compact_faction_state(faction: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize one faction owner to minimum sufficient current state."""
    out = copy.deepcopy(dict(faction))
    fid = out.get("faction_id")
    if not isinstance(fid, str) or not fid:
        raise ValueError("jianghu faction_id missing")
    profile = faction_profile(fid)

    camp = faction_camp(fid)
    if camp and out.get("jianghu_camp") == camp:
        out.pop("jianghu_camp", None)
    if profile is not None:
        for key in _STATIC_SCALARS + _STATIC_LISTS:
            if key in out and key in profile and out[key] == profile[key]:
                out.pop(key, None)
        for key in _STATIC_MAPPINGS:
            if key not in out:
                continue
            deviation = _mapping_deviation(out.get(key), profile.get(key, {}))
            if deviation:
                out[key] = deviation
            else:
                out.pop(key, None)
    return out


def read_faction(repository: Any, faction_ref: str) -> tuple[str, dict[str, Any]]:
    path = faction_path(faction_ref)
    raw = repository.read_json(path)
    if not isinstance(raw, Mapping):
        raise ValueError("jianghu faction owner invalid")
    if raw.get("faction_id") != faction_ref:
        raise ValueError("jianghu faction path/identity mismatch")
    return path, hydrate_faction_state(raw)


__all__ = [
    "compact_faction_state",
    "faction_path",
    "faction_profile",
    "faction_type",
    "resolved_faction_type",
    "hydrate_faction_state",
    "inventory_path",
    "read_faction",
    "roster_path",
]
