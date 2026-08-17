"""Deterministic current environment and registered Shinobi mechanics."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from shinobi_runtime.sim.events import CampaignTime

CLIMATE_PATH = "game/data/content/environment-climates.json"
WORLD_PATH = "state/world/routes-and-settlements.json"
META_PATH = "state/meta.json"
BLOCK_HOURS = 6
SEASON = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring", 6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}
LIGHT = {"winter": (7, 17), "spring": (6, 19), "summer": (5, 20), "autumn": (6, 18)}
TEMP = ("freezing", "cold", "cool", "mild", "warm", "hot")
SEASONAL_ECONOMY = {"winter": 900, "spring": 1020, "summer": 1060, "autumn": 1080}


def _read(reader: Any, path: str) -> Mapping[str, Any]:
    value = reader.read_json(path)
    if not isinstance(value, Mapping):
        raise ValueError(f"environment source {path} must be an object")
    return value


def _roll(seed: str, *parts: object) -> int:
    text = "\x00".join((seed, *(str(part) for part in parts)))
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big") % 10_000


def _pct(profile: Mapping[str, Any], field: str, season: str, default: int) -> int:
    table = profile.get(field)
    value = table.get(season) if isinstance(table, Mapping) else default
    if isinstance(value, bool) or not isinstance(value, int):
        value = default
    return max(0, min(100, value)) * 100


def _catalog(reader: Any) -> Mapping[str, Any]:
    data = _read(reader, CLIMATE_PATH)
    if data.get("schema") != "shinobi-environment-climate-catalog":
        raise ValueError("invalid Shinobi environment climate catalog")
    if not isinstance(data.get("profiles"), Mapping) or not isinstance(data.get("country_profiles"), Mapping):
        raise ValueError("invalid Shinobi environment climate catalog")
    return data


def _place_country(reader: Any, location_ref: str) -> str:
    world = _read(reader, WORLD_PATH)
    payload = world.get("payload")
    places = payload.get("places") if isinstance(payload, Mapping) else None
    if isinstance(places, list):
        for row in places:
            if isinstance(row, Mapping) and row.get("id") == location_ref:
                country = row.get("country_id")
                if isinstance(country, str) and country:
                    return country
    return "default"


def _climate(reader: Any, location_ref: str) -> tuple[str, str, Mapping[str, Any]]:
    catalog = _catalog(reader)
    country = _place_country(reader, location_ref)
    mapping = catalog["country_profiles"]
    profile_ref = mapping.get(country, mapping.get("default", "temperate"))
    profiles = catalog["profiles"]
    profile = profiles.get(profile_ref)
    if not isinstance(profile_ref, str) or not isinstance(profile, Mapping):
        raise ValueError("environment climate profile missing")
    return country, profile_ref, profile


def _seed(reader: Any) -> tuple[str, str]:
    meta = _read(reader, META_PATH)
    campaign_id = str(meta.get("campaign_id", ""))
    if not campaign_id:
        raise ValueError("campaign has no campaign_id")
    seed = meta.get("world_seed")
    if not isinstance(seed, str) or not seed:
        seed = "derived:" + hashlib.sha256(campaign_id.encode()).hexdigest()
    return campaign_id, seed


def _light(at: CampaignTime, season: str) -> str:
    sunrise, sunset = LIGHT[season]
    if at.hour < sunrise - 1 or at.hour >= sunset + 1:
        return "night"
    return "twilight" if at.hour < sunrise or at.hour >= sunset else "day"


def _core(at: CampaignTime, seed: str, climate_ref: str, profile: Mapping[str, Any]) -> dict[str, Any]:
    season = SEASON[at.month]
    key = (at.year, at.month, at.day, at.hour // BLOCK_HOURS)
    table = profile.get("temperature_index_by_season", {})
    base = table.get(season, 0) if isinstance(table, Mapping) else 0
    if isinstance(base, bool) or not isinstance(base, int):
        base = 0
    index = max(-2, min(3, base + (_roll(seed, climate_ref, *key, "temperature") % 3) - 1))
    temperature = TEMP[index + 2]
    storm = _roll(seed, climate_ref, *key, "storm") < _pct(profile, "storm_chance_pct", season, 4)
    precip = _roll(seed, climate_ref, *key, "precipitation") < _pct(profile, "precipitation_chance_pct", season, 25)
    fog = _roll(seed, climate_ref, *key, "fog") < _pct(profile, "fog_chance_pct", season, 8)
    cloudy = _roll(seed, climate_ref, *key, "cloud") < _pct(profile, "cloud_chance_pct", season, 45)
    frozen = temperature in {"freezing", "cold"}
    if storm:
        condition, precipitation = (("snowstorm", "heavy_snow") if frozen else ("storm", "heavy_rain"))
    elif precip:
        condition, precipitation = (("snow", "snow") if frozen else ("rain", "rain"))
    elif fog:
        condition, precipitation = "fog", "none"
    else:
        condition, precipitation = ("overcast" if cloudy else "clear"), "none"
    bias = profile.get("wind_bias", 0)
    if isinstance(bias, bool) or not isinstance(bias, int):
        bias = 0
    wind_roll = _roll(seed, climate_ref, *key, "wind") + max(-20, min(30, bias)) * 100
    wind = "strong" if storm else "calm" if wind_roll < 2000 else "light" if wind_roll < 6500 else "moderate" if wind_roll < 9000 else "strong"
    return {"season": season, "condition": condition, "precipitation": precipitation, "wind": wind, "temperature_band": temperature}


def _effects(core: Mapping[str, Any], light: str, prior: str) -> tuple[str, str, dict[str, int]]:
    condition = str(core["condition"])
    temperature = str(core["temperature_band"])
    wind = str(core["wind"])
    if condition in {"snow", "snowstorm"} or prior in {"snow", "snowstorm"}:
        ground = "snow"
    elif temperature == "freezing" and prior in {"rain", "storm"}:
        ground = "ice"
    elif condition in {"rain", "storm"} and prior in {"rain", "storm"}:
        ground = "muddy"
    elif condition in {"rain", "storm"}:
        ground = "wet"
    elif prior in {"rain", "storm"}:
        ground = "damp"
    else:
        ground = "dry"
    visibility = {"clear": 1000, "overcast": 960, "fog": 600, "rain": 840, "storm": 620, "snow": 800, "snowstorm": 540}[condition]
    visibility = max(420, min(1000, visibility * {"day": 1000, "twilight": 860, "night": 680}[light] // 1000))
    travel_weather = {"clear": 1000, "overcast": 1000, "fog": 1050, "rain": 1080, "storm": 1180, "snow": 1120, "snowstorm": 1260}[condition]
    ground_drag = {"dry": 1000, "damp": 1010, "wet": 1040, "muddy": 1110, "snow": 1100, "ice": 1150}[ground]
    travel = min(1400, travel_weather * ground_drag // 1000)
    mobility = {"dry": 1000, "damp": 995, "wet": 980, "muddy": 930, "snow": 940, "ice": 900}[ground]
    if condition in {"storm", "snowstorm"}:
        mobility = mobility * 950 // 1000
    hazard = (90 if ground == "ice" else 0) + (140 if condition == "snowstorm" else 110 if condition == "storm" else 0)
    if wind == "strong" and condition in {"rain", "snow", "fog"}:
        hazard += 20
    fire = 500 if condition in {"storm", "snowstorm"} else 650 if condition in {"rain", "snow"} else 1250 if wind == "strong" else 1100 if wind == "moderate" else 1000

    # Physical sensing/elemental affordances are deterministic environment
    # channels, not free clues or technique outcomes. Exact systems decide
    # whether a channel matters for a particular action. Chakra sensing stays
    # weather-neutral unless a separate exact mechanic explicitly changes it.
    sound_masking = {"clear": 0, "overcast": 20, "fog": 40, "rain": 240, "storm": 520, "snow": 100, "snowstorm": 380}[condition]
    sound_masking += {"calm": 0, "light": 20, "moderate": 80, "strong": 160}[wind]
    sound_masking = min(700, sound_masking)
    track_evidence = {"dry": 700, "damp": 820, "wet": 900, "muddy": 1000, "snow": 1000, "ice": 560}[ground]
    if condition == "storm":
        track_evidence = max(450, track_evidence - 220)
    elif condition == "snowstorm":
        track_evidence = max(650, track_evidence - 120)
    scent_tracking = {"clear": 1000, "overcast": 980, "fog": 950, "rain": 760, "storm": 560, "snow": 880, "snowstorm": 680}[condition]
    scent_tracking = scent_tracking * {"calm": 1000, "light": 980, "moderate": 900, "strong": 800}[wind] // 1000
    water_availability = {"dry": 0, "damp": 220, "wet": 560, "muddy": 720, "snow": 520, "ice": 420}[ground]
    water_availability = max(water_availability, {"clear": 0, "overcast": 0, "fog": 120, "rain": 820, "storm": 1000, "snow": 620, "snowstorm": 760}[condition])
    conductivity = {"dry": 1000, "damp": 1060, "wet": 1160, "muddy": 1210, "snow": 920, "ice": 1100}[ground]
    smoke_gas_drift = {"calm": 700, "light": 900, "moderate": 1120, "strong": 1400}[wind]

    return ground, ("good" if visibility >= 900 else "reduced" if visibility >= 725 else "poor" if visibility >= 550 else "severe"), {
        "travel_time_milli": travel,
        "mobility_milli": max(820, mobility),
        "visibility_milli": visibility,
        "hazard_milli": min(300, hazard),
        "fire_spread_milli": fire,
        "seasonal_economy_milli": SEASONAL_ECONOMY[str(core["season"])],
        "sound_masking_milli": sound_masking,
        "track_evidence_milli": track_evidence,
        "scent_tracking_milli": max(400, scent_tracking),
        "surface_water_milli": min(1000, water_availability),
        "conductivity_milli": conductivity,
        "smoke_gas_drift_milli": smoke_gas_drift,
        "chakra_sensing_milli": 1000,
    }


def environment_snapshot(reader: Any, *, world_time: str, location_ref: str) -> dict[str, Any]:
    at = CampaignTime.parse(world_time)
    campaign_id, seed = _seed(reader)
    country, climate_ref, profile = _climate(reader, location_ref)
    core = _core(at, seed, climate_ref, profile)
    prior = _core(at.add_seconds(-BLOCK_HOURS * 3600), seed, climate_ref, profile)
    light = _light(at, str(core["season"]))
    ground, visibility, effects = _effects(core, light, str(prior["condition"]))
    block_hour = (at.hour // BLOCK_HOURS) * BLOCK_HOURS
    block_start = CampaignTime(at.year, at.month, at.day, block_hour, 0, 0)
    weather_next = block_start.add_seconds(BLOCK_HOURS * 3600)
    sunrise, sunset = LIGHT[str(core["season"])]
    candidates = [CampaignTime(at.year, at.month, at.day, hour, 0, 0) for hour in sorted({max(0, sunrise - 1), sunrise, sunset, min(23, sunset + 1)})]
    light_next = next((value for value in candidates if value > at), None)
    if light_next is None:
        tomorrow = CampaignTime(at.year, at.month, at.day, 0, 0, 0).add_seconds(24 * 3600)
        tomorrow_sunrise = LIGHT[SEASON[tomorrow.month]][0]
        light_next = CampaignTime(tomorrow.year, tomorrow.month, tomorrow.day, max(0, tomorrow_sunrise - 1), 0, 0)
    ref = hashlib.sha256(f"{campaign_id}\x00{seed}\x00{climate_ref}\x00{block_start}".encode()).hexdigest()[:16]
    return {
        "source": "derived_environment_authority",
        "authority_contract": "runtime/contracts/environment.json",
        "as_of": str(at),
        "location_ref": location_ref,
        "country_ref": country,
        "climate_ref": climate_ref,
        "season": core["season"],
        "light": light,
        "condition": core["condition"],
        "precipitation": core["precipitation"],
        "wind": core["wind"],
        "temperature_band": core["temperature_band"],
        "visibility": visibility,
        "ground": ground,
        "weather_block_ref": f"env.{ref}",
        "next_transition_after": str(min(weather_next, light_next)),
        "mechanical_effects": effects,
        "scope": "Derived from campaign time + world seed + static country climate. Mutable hazards, schedules, patrols and technique effects remain owned elsewhere.",
    }


def route_travel_factor_milli(reader: Any, *, world_time: str, origin_ref: str, destination_ref: str, base_hours: int) -> int:
    start = environment_snapshot(reader, world_time=world_time, location_ref=origin_ref)
    midpoint = CampaignTime.parse(world_time).add_seconds(max(0, int(base_hours)) * 1800)
    end = environment_snapshot(reader, world_time=str(midpoint), location_ref=destination_ref)
    return max(900, min(1400, (int(start["mechanical_effects"]["travel_time_milli"]) + int(end["mechanical_effects"]["travel_time_milli"]) + 1) // 2))


def apply_environment_to_terrain(terrain: Any, environment: Mapping[str, Any]) -> Any:
    """Apply only registered environment channels to an existing TerrainState."""
    from shinobi_runtime.combat.models import SideTerrain, TerrainState

    effects = environment.get("mechanical_effects")
    if not isinstance(effects, Mapping):
        raise ValueError("environment mechanical effects missing")
    mobility = int(effects["mobility_milli"])
    visibility = int(effects["visibility_milli"])
    hazard = int(effects["hazard_milli"])
    modifiers = tuple(
        SideTerrain(
            side_ref=row.side_ref,
            cover_milli=row.cover_milli,
            mobility_milli=max(0, min(2000, row.mobility_milli * mobility // 1000)),
            visibility_milli=max(0, min(2000, row.visibility_milli * visibility // 1000)),
            hazard_milli=max(0, min(1000, row.hazard_milli + hazard)),
        )
        for row in terrain.side_modifiers
    )
    return TerrainState(
        terrain_ref=f"{terrain.terrain_ref}:{environment['weather_block_ref']}",
        side_modifiers=modifiers,
    )


__all__ = ["CLIMATE_PATH", "apply_environment_to_terrain", "environment_snapshot", "route_travel_factor_milli"]
