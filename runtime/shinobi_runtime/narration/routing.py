"""Pure deterministic routing for cold scene narration modules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Tuple


_NON_SEMANTIC = re.compile(r"[^a-z0-9]+")


def _semantic_key(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    key = _NON_SEMANTIC.sub("_", value.strip().lower()).strip("_")
    if not key:
        raise ValueError(f"{label} has no semantic characters")
    return key


def _string_map(router: Mapping[str, Any], field: str) -> Mapping[str, str]:
    raw = router.get(field)
    if not isinstance(raw, Mapping) or not raw:
        raise ValueError(f"narration router {field} must be a non-empty object")
    normalized = {}
    for raw_key, module_id in raw.items():
        key = _semantic_key(raw_key, f"{field} key")
        if key in normalized:
            raise ValueError(f"narration router {field} has a normalized-key collision")
        if not isinstance(module_id, str) or not module_id:
            raise ValueError(f"narration router {field} has an invalid module ID")
        normalized[key] = module_id
    return normalized


@dataclass(frozen=True)
class NarrationSelection:
    primary_id: str
    primary_path: str
    secondary_id: Optional[str]
    secondary_path: Optional[str]
    scene_type_matched: bool
    matched_pressures: Tuple[str, ...]


def select_narration_modules(
    router: Mapping[str, Any],
    *,
    scene_type: str,
    pressures: Iterable[str] = (),
) -> NarrationSelection:
    """Select one primary and at most one independently causal secondary.

    Matching is exact after identifier normalization. Unknown pressure prose is
    ignored; substrings such as ``command`` never activate large-war narration.
    Ambiguous pressure overrides fail closed instead of depending on input order.
    """

    if (
        not isinstance(router, Mapping)
        or router.get("schema") != "narration-router"
        or router.get("authority") is not False
    ):
        raise ValueError("invalid narration router")
    modules = _string_map(router, "modules")
    if len(set(modules.values())) != len(modules):
        raise ValueError("narration module paths must be unique")
    scene_map = _string_map(router, "scene_type_primary")
    pressure_map = _string_map(router, "pressure_primary_overrides")

    default_primary = router.get("default_primary")
    if not isinstance(default_primary, str) or default_primary not in modules:
        raise ValueError("narration default primary does not resolve")
    raw_gated = router.get("pressure_gated_modules")
    if not isinstance(raw_gated, list) or any(
        not isinstance(module_id, str) or module_id not in modules
        for module_id in raw_gated
    ):
        raise ValueError("narration pressure-gated modules do not resolve")
    gated = frozenset(raw_gated)
    if len(gated) != len(raw_gated):
        raise ValueError("narration pressure-gated modules contain duplicates")
    if default_primary in gated or gated.intersection(scene_map.values()):
        raise ValueError("pressure-gated module is reachable without a pressure")
    if not gated.issubset(set(pressure_map.values())):
        raise ValueError("pressure-gated module has no pressure route")

    referenced = set(scene_map.values()) | set(pressure_map.values())
    unknown_modules = referenced - set(modules)
    if unknown_modules:
        raise ValueError(
            "narration route references unknown modules: "
            + ", ".join(sorted(unknown_modules))
        )

    scene_key = _semantic_key(scene_type, "scene type")
    scene_type_matched = scene_key in scene_map
    base_primary = scene_map.get(scene_key, default_primary)

    if isinstance(pressures, (str, bytes)):
        raise TypeError("pressures must be an iterable of semantic pressure IDs")
    normalized_pressures = tuple(
        sorted({_semantic_key(value, "pressure") for value in pressures})
    )
    matched_pressures = tuple(
        pressure for pressure in normalized_pressures if pressure in pressure_map
    )
    overrides = {pressure_map[pressure] for pressure in matched_pressures}
    if len(overrides) > 1:
        raise ValueError("causal pressures select conflicting narration modules")

    if overrides:
        primary_id = next(iter(overrides))
        secondary_id = base_primary if base_primary != primary_id else None
    else:
        primary_id = base_primary
        secondary_id = None
    return NarrationSelection(
        primary_id=primary_id,
        primary_path=modules[primary_id],
        secondary_id=secondary_id,
        secondary_path=None if secondary_id is None else modules[secondary_id],
        scene_type_matched=scene_type_matched,
        matched_pressures=matched_pressures,
    )
