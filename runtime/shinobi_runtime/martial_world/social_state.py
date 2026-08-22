"""Canonical current Jianghu person-social state."""
from __future__ import annotations

import copy
from typing import Any, Mapping

_SCORE_KEYS = ("trust", "affection", "respect", "familiarity")


def compact_social_state(state: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"schema": str(state.get("schema", "jianghu-social-state-1.0"))}
    relationships = state.get("relationships", {})
    if relationships not in (None, {}) and not isinstance(relationships, Mapping):
        raise ValueError("jianghu social relationships invalid")
    compact_relationships: dict[str, dict[str, int]] = {}
    for edge_ref, raw in (relationships.items() if isinstance(relationships, Mapping) else []):
        if not isinstance(raw, Mapping):
            raise ValueError("jianghu social relationship invalid")
        row = {key: int(raw.get(key, 0)) for key in _SCORE_KEYS}
        compact_relationships[str(edge_ref)] = row
    if compact_relationships:
        out["relationships"] = compact_relationships
    courtships = state.get("courtships", {})
    if courtships not in (None, {}) and not isinstance(courtships, Mapping):
        raise ValueError("jianghu courtships invalid")
    if isinstance(courtships, Mapping) and courtships:
        out["courtships"] = copy.deepcopy(dict(courtships))
    return out


__all__ = ["compact_social_state"]
