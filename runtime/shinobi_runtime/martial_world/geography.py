"""Canonical composed Jianghu geography reads.

``geography.json`` remains the large authored base map. Small reviewed route
additions live in ``geography-extensions.json`` so correcting one corridor does
not require rewriting the generated-looking site catalog. Route planning and
route-frontier settlement consume the same extension source.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping

_ROOT = Path(__file__).resolve().parents[3]
_MW = _ROOT / "game" / "data" / "martial-world"
_BASE_PATH = "game/data/martial-world/geography.json"
_EXTENSIONS_PATH = "game/data/martial-world/geography-extensions.json"


def compose_geography(base: Mapping[str, Any], extensions: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return one validated geography view with non-overriding route additions."""
    if not isinstance(base, Mapping):
        raise ValueError("jianghu geography invalid")
    out = copy.deepcopy(dict(base))
    routes = out.get("routes", [])
    if not isinstance(routes, list):
        raise ValueError("jianghu geography routes invalid")
    route_ids = {
        str(row.get("id"))
        for row in routes
        if isinstance(row, Mapping) and isinstance(row.get("id"), str) and row.get("id")
    }
    if len(route_ids) != sum(
        1 for row in routes
        if isinstance(row, Mapping) and isinstance(row.get("id"), str) and row.get("id")
    ):
        raise ValueError("jianghu geography duplicate route id")

    if extensions is None:
        return out
    if not isinstance(extensions, Mapping):
        raise ValueError("jianghu geography extensions invalid")
    extra_routes = extensions.get("routes", [])
    if not isinstance(extra_routes, list):
        raise ValueError("jianghu geography extension routes invalid")
    places = out.get("places", {})
    if not isinstance(places, Mapping):
        raise ValueError("jianghu geography places invalid")
    for raw in extra_routes:
        if not isinstance(raw, Mapping):
            raise ValueError("jianghu geography extension route invalid")
        row = copy.deepcopy(dict(raw))
        route_id = str(row.get("id") or "")
        origin = str(row.get("from") or "")
        destination = str(row.get("to") or "")
        if not route_id or route_id in route_ids:
            raise ValueError("jianghu geography extension route id invalid")
        if origin not in places or destination not in places or origin == destination:
            raise ValueError("jianghu geography extension route endpoints invalid")
        distance = row.get("distance_km")
        if not isinstance(distance, (int, float)) or isinstance(distance, bool) or float(distance) <= 0:
            raise ValueError("jianghu geography extension route distance invalid")
        modes = row.get("allowed_modes")
        if not isinstance(modes, list) or not modes or any(not isinstance(mode, str) or not mode for mode in modes):
            raise ValueError("jianghu geography extension route modes invalid")
        if not isinstance(row.get("road_quality"), str) or not row.get("road_quality"):
            raise ValueError("jianghu geography extension route road quality invalid")
        route_ids.add(route_id)
        routes.append(row)
    out["routes"] = routes
    return out


@lru_cache(maxsize=1)
def load_static_extensions() -> Mapping[str, Any] | None:
    path = _MW / "geography-extensions.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


@lru_cache(maxsize=1)
def load_static_geography() -> Mapping[str, Any]:
    base = json.loads((_MW / "geography.json").read_text(encoding="utf-8"))
    return compose_geography(base, load_static_extensions())


def applicable_route_extensions(base: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return extensions not already present and valid for this geography view.

    Frontier unit tests sometimes pass deliberately tiny geography fixtures. An
    unrelated production corridor must not invalidate those fixtures. Production
    callers may also pass an already-composed geography view, so existing route
    IDs are filtered out to keep extension application idempotent.
    """
    extensions = load_static_extensions()
    if not isinstance(extensions, Mapping):
        return None
    places = base.get("places", {}) if isinstance(base, Mapping) else {}
    base_routes = base.get("routes", []) if isinstance(base, Mapping) else []
    if not isinstance(places, Mapping) or not isinstance(base_routes, list):
        return None
    existing_ids = {
        str(row.get("id"))
        for row in base_routes
        if isinstance(row, Mapping) and isinstance(row.get("id"), str) and row.get("id")
    }
    rows = extensions.get("routes", [])
    if not isinstance(rows, list):
        raise ValueError("jianghu geography extension routes invalid")
    applicable = [
        copy.deepcopy(dict(row))
        for row in rows
        if isinstance(row, Mapping)
        and isinstance(row.get("id"), str)
        and str(row.get("id")) not in existing_ids
        and str(row.get("from") or "") in places
        and str(row.get("to") or "") in places
    ]
    return {"routes": applicable} if applicable else None


def read_geography(read_json: Callable[[str], Any]) -> dict[str, Any]:
    base = read_json(_BASE_PATH)
    try:
        extensions = read_json(_EXTENSIONS_PATH)
    except FileNotFoundError:
        extensions = None
    return compose_geography(base, extensions)


__all__ = [
    "applicable_route_extensions",
    "compose_geography",
    "load_static_extensions",
    "load_static_geography",
    "read_geography",
]
