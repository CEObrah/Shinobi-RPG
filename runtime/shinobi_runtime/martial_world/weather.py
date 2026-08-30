"""Deterministic China-grounded weather for the Jianghu strategic map."""
from __future__ import annotations

import hashlib
import json
import math
from functools import lru_cache
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[3]
_MW = _ROOT / "game" / "data" / "martial-world"


@lru_cache(maxsize=8)
def _load(name: str) -> Mapping[str, Any]:
    return json.loads((_MW / name).read_text(encoding="utf-8"))


def _roll(seed: str, *parts: object) -> int:
    text = "\x00".join((seed, *(str(p) for p in parts)))
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big") % 1_000_000


def _season(month: int) -> str:
    return "winter" if month in (12, 1, 2) else "spring" if month in (3, 4, 5) else "summer" if month in (6, 7, 8) else "autumn"


def weather_snapshot(*, world_seed: str, at: datetime, place_id: str) -> dict[str, Any]:
    geo = _load("geography.json")
    climate = _load("climate.json")
    place = geo.get("places", {}).get(place_id)
    if not isinstance(place, Mapping):
        raise KeyError(place_id)
    profile_id = place.get("climate_profile")
    profile = climate.get("profiles", {}).get(profile_id)
    if not isinstance(profile, Mapping):
        raise KeyError(profile_id)
    means = profile["monthly_mean_temp_c_tenths"]
    base = int(means[at.month - 1])
    ref_elev = int(profile.get("reference_elevation_m", 0))
    elev = int(place.get("elevation_m", ref_elev))
    lapse = int(round((elev - ref_elev) * -0.065))  # tenths C per 100 m = -0.65 C
    amplitude = int(profile.get("diurnal_amplitude_c_tenths", 70))
    # Warmest around 15:00, coolest around 03:00.
    diurnal = int(round(amplitude * math.cos((at.hour - 15) * math.pi / 12.0) / 2.0))
    block = at.hour // int(climate.get("weather_block_hours", 6))
    anomaly = (_roll(world_seed, place_id, at.year, at.month, at.day, block, "temp") % 61) - 30
    temp = base + lapse + diurnal + anomaly

    season = _season(at.month)
    precip_pct = int(profile.get("precipitation_chance_pct", {}).get(season, 25))
    storm_pct = int(profile.get("storm_chance_pct", {}).get(season, 5))
    fog_pct = int(profile.get("fog_chance_pct", {}).get(season, 8))
    precip_roll = _roll(world_seed, place_id, at.year, at.month, at.day, block, "precip") % 100
    storm_roll = _roll(world_seed, place_id, at.year, at.month, at.day, block, "storm") % 100
    fog_roll = _roll(world_seed, place_id, at.year, at.month, at.day, block, "fog") % 100
    freezing = temp <= 0
    if storm_roll < storm_pct:
        condition = "snowstorm" if freezing else "storm"
        precip_milli = 1000
    elif precip_roll < precip_pct:
        condition = "snow" if freezing else "rain"
        precip_milli = 650
    elif fog_roll < fog_pct:
        condition = "fog"
        precip_milli = 0
    else:
        condition = "clear"
        precip_milli = 0

    base_wind = int(profile.get("base_wind_mps_tenths", 25))
    wind = max(0, base_wind + ((_roll(world_seed, place_id, at.year, at.month, at.day, block, "wind") % 61) - 30))
    wind_dir = _roll(world_seed, place_id, at.year, at.month, at.day, block, "wind_dir") % 360
    humidity_base = int(profile.get("humidity_milli", 600))
    humidity = max(150, min(1000, humidity_base + (180 if condition in {"rain", "snow", "storm", "snowstorm", "fog"} else 0)))
    if condition in {"storm", "snowstorm"}:
        visibility = 500
    elif condition == "fog":
        visibility = 550
    elif condition in {"rain", "snow"}:
        visibility = 800
    else:
        visibility = 1000
    sound_masking = min(750, precip_milli // 2 + wind * 5)
    if condition in {"snow", "snowstorm"}:
        ground = "snow"
    elif condition == "storm":
        ground = "muddy"
    elif condition == "rain":
        ground = "wet"
    else:
        ground = "dry"
    return {
        "place_id": place_id,
        "at": at.isoformat(),
        "season": season,
        "temperature_c_tenths": temp,
        "temperature_c": round(temp / 10.0, 1),
        "condition": condition,
        "precipitation_milli": precip_milli,
        "humidity_milli": humidity,
        "wind_mps_tenths": wind,
        "wind_direction_degrees": wind_dir,
        "visibility_milli": visibility,
        "sound_masking_milli": sound_masking,
        "ground": ground,
    }
