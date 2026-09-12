"""Deterministic local-site travel inside one strategic place."""
from __future__ import annotations

import json, math
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

_MW = Path(__file__).resolve().parents[3] / "game" / "data" / "martial-world"


@lru_cache(maxsize=1)
def _local_geography() -> Mapping[str, Any]:
    return json.loads((_MW / "local-geography.json").read_text(encoding="utf-8"))


def _sites() -> Mapping[str, Any]:
    return json.loads((_MW / "local-sites.json").read_text(encoding="utf-8"))


def base_walking_speed_kph() -> float:
    return max(0.1, float(_local_geography()["walking_speed_kph"]))


def local_travel_quote(*, start_site_ref: str, end_site_ref: str, walking_speed_kph: float | None = None) -> dict[str, Any]:
    data = _sites()
    sites = data.get("sites", {})
    a = sites.get(start_site_ref) if isinstance(sites, Mapping) else None
    b = sites.get(end_site_ref) if isinstance(sites, Mapping) else None
    if not isinstance(a, Mapping) or not isinstance(b, Mapping):
        raise ValueError("unknown local site")
    if a.get("parent_place_ref") != b.get("parent_place_ref"):
        raise ValueError("local sites are not in the same strategic place")
    dx = float(b.get("x_m", 0)) - float(a.get("x_m", 0))
    dy = float(b.get("y_m", 0)) - float(a.get("y_m", 0))
    straight = math.hypot(dx, dy)
    # Streets/pathing are longer than a straight ray; the constant factor is a
    # deterministic local-network approximation, not random travel narration.
    distance_m = max(1, int(round(straight * 1.18)))
    speed = base_walking_speed_kph() if walking_speed_kph is None else float(walking_speed_kph)
    speed_mpm = max(1.0, speed * 1000.0 / 60.0)
    minutes = max(1, int(math.ceil(distance_m / speed_mpm)))
    return {
        "parent_place_ref": a.get("parent_place_ref"),
        "distance_m": distance_m,
        "walking_minutes": minutes,
    }


__all__ = ["base_walking_speed_kph", "local_travel_quote"]
