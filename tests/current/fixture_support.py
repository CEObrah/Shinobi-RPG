"""Test-only helpers for synthetic physical-presence fixtures.

The live campaign save is intentionally allowed to progress. Tests that stage a
person at a synthetic site must therefore remove any unrelated live route owner
from the copied fixture instead of relying on ``state/scene.json`` to override
mechanical presence.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

_ROUTE_PATH = "state/martial-world/route-operations.json"
_CARRIED_KEYS = ("participant_refs", "protected_person_refs", "captive_refs", "rescued_refs")


def route_state_without_people(state: Mapping[str, Any], *person_refs: str) -> dict[str, Any]:
    """Return a copy with movements physically owning any supplied person removed."""
    out = copy.deepcopy(dict(state))
    blocked = {str(ref) for ref in person_refs if isinstance(ref, str) and ref}
    movements = out.get("movements", {})
    if not isinstance(movements, Mapping) or not blocked:
        return out
    kept: dict[str, Any] = {}
    for movement_ref, raw in movements.items():
        if not isinstance(raw, Mapping):
            kept[str(movement_ref)] = copy.deepcopy(raw)
            continue
        owned: set[str] = set()
        for key in _CARRIED_KEYS:
            values = raw.get(key)
            if isinstance(values, list):
                owned.update(str(value) for value in values if isinstance(value, str))
        if blocked & owned:
            continue
        kept[str(movement_ref)] = copy.deepcopy(dict(raw))
    out["movements"] = kept
    return out


def remove_people_from_route_fixture(root: Path, *person_refs: str) -> dict[str, Any]:
    """Remove unrelated live route ownership from a disposable copied fixture."""
    path = Path(root) / _ROUTE_PATH
    state = json.loads(path.read_text(encoding="utf-8"))
    after = route_state_without_people(state, *person_refs)
    path.write_text(json.dumps(after, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return after


def put_movement_at_lodging_fixture(root: Path, movement_ref: str, site_ref: str) -> dict[str, Any]:
    """Stage an existing route party at a real lodging site without deleting its route owner."""
    path = Path(root) / _ROUTE_PATH
    state = json.loads(path.read_text(encoding="utf-8"))
    movements = state.get("movements", {})
    if not isinstance(movements, dict) or movement_ref not in movements:
        raise KeyError(movement_ref)
    movement = movements[movement_ref]
    if not isinstance(movement, dict):
        raise TypeError(movement_ref)
    movement["status"] = "lodging_rest"
    movement["rest_place_ref"] = str(site_ref)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state
