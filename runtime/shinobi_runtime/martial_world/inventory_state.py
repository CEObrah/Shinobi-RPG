"""Sparse canonical storage for conserved Jianghu faction inventory."""
from __future__ import annotations

import copy
from typing import Any, Mapping

_BUCKETS = ("equipment", "raw_materials", "herbs", "medicines", "transport_assets")


def _positive_map(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, int] = {}
    for key, raw in value.items():
        if isinstance(raw, bool):
            continue
        try:
            amount = int(raw)
        except (TypeError, ValueError):
            continue
        if amount < 0:
            raise ValueError("jianghu inventory quantity cannot be negative")
        if amount:
            out[str(key)] = amount
    return out


def hydrate_inventory_state(inventory: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(inventory))
    for key in _BUCKETS:
        out.setdefault(key, {})
    return out


def compact_inventory_state(inventory: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(inventory))
    out.pop("last_settled_at", None)
    for key in _BUCKETS:
        values = _positive_map(out.get(key))
        if values:
            out[key] = values
        else:
            out.pop(key, None)
    if "food_ration_days" in out:
        food = int(out["food_ration_days"])
        if food < 0:
            raise ValueError("jianghu food_ration_days cannot be negative")
        out["food_ration_days"] = food
    return out


__all__ = ["compact_inventory_state", "hydrate_inventory_state"]
