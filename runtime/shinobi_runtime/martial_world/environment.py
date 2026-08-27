"""Deterministic route/site environment projection into exact combat.

Geography remains compact. Route edges may contain bounded ``terrain_segments``
(0..1000 along the authored edge), while exact combat receives only the local
terrain, weather, ground and physical obstacles relevant to that encounter.
No tree, reed patch or rock becomes persistent world state merely to decorate a
fight.
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

_TERRAIN: dict[str, dict[str, int]] = {
    "plain":       {"movement_milli":1000,"mounted_milli":1050,"visibility_milli":1100,"concealment_milli":100,"frontage_m":80},
    "river_plain": {"movement_milli":950,"mounted_milli":950,"visibility_milli":1050,"concealment_milli":180,"frontage_m":55},
    "hills":       {"movement_milli":900,"mounted_milli":820,"visibility_milli":900,"concealment_milli":280,"frontage_m":28},
    "mountain":    {"movement_milli":720,"mounted_milli":600,"visibility_milli":820,"concealment_milli":320,"frontage_m":10},
    "highland":    {"movement_milli":800,"mounted_milli":720,"visibility_milli":1000,"concealment_milli":180,"frontage_m":22},
    "forest":      {"movement_milli":760,"mounted_milli":620,"visibility_milli":600,"concealment_milli":650,"frontage_m":14},
    "marsh":       {"movement_milli":620,"mounted_milli":430,"visibility_milli":720,"concealment_milli":520,"frontage_m":9},
    "desert":      {"movement_milli":850,"mounted_milli":900,"visibility_milli":1150,"concealment_milli":120,"frontage_m":65},
    "urban":       {"movement_milli":860,"mounted_milli":600,"visibility_milli":700,"concealment_milli":420,"frontage_m":8},
}




def place_terrain(place: Mapping[str, Any] | None) -> str:
    """Project one strategic place onto the compact battle-terrain vocabulary."""
    if not isinstance(place, Mapping):
        return "plain"
    kind = str(place.get("kind") or "")
    if kind in {"city", "major_city", "imperial_capital"}:
        return "urban"
    climate = str(place.get("climate_profile") or "")
    if climate in {"temperate_mountain", "humid_mountain"}:
        return "mountain"
    if climate in {"yunnan_highland", "tibetan_highland"}:
        return "highland"
    if climate in {"middle_yangtze", "lower_yangtze"}:
        return "river_plain"
    if climate == "northwest_dry":
        return "desert"
    return "plain"


def site_combat_terrain(site: Mapping[str, Any] | None, place: Mapping[str, Any] | None) -> str:
    """Project a registered site and its parent place into exact battle terrain."""
    site_type = str(site.get("site_type") or "") if isinstance(site, Mapping) else ""
    if site_type == "tournament_ground":
        natural = dict(place or {}) if isinstance(place, Mapping) else {}
        if str(natural.get("kind") or "") in {"city", "major_city", "imperial_capital"}:
            natural["kind"] = "rural_holding"
        return place_terrain(natural)
    if isinstance(site, Mapping) and site_type in {
        "market", "guild_hall", "residence", "government_office", "inn",
        "workshop", "warehouse", "clinic", "temple", "compound", "courtyard",
    }:
        return "urban"
    return place_terrain(place)

def terrain_profile(terrain: str) -> dict[str, int]:
    return dict(_TERRAIN.get(str(terrain), _TERRAIN["plain"]))


def edge_terrain_segments(edge: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw=edge.get("terrain_segments")
    rows=[]
    if isinstance(raw,Sequence) and not isinstance(raw,(str,bytes,bytearray)):
        for row in raw:
            if not isinstance(row,Mapping): continue
            start=max(0,min(1000,int(row.get("start_milli",0))))
            end=max(0,min(1000,int(row.get("end_milli",1000))))
            if end<=start: continue
            terrain=str(row.get("terrain") or edge.get("terrain") or "plain")
            rows.append({"start_milli":start,"end_milli":end,"terrain":terrain})
    if not rows:
        rows=[{"start_milli":0,"end_milli":1000,"terrain":str(edge.get("terrain") or "plain")}]
    rows.sort(key=lambda r:(r["start_milli"],r["end_milli"],r["terrain"]))
    return rows


def route_terrain_at(edge: Mapping[str, Any], progress_milli: int) -> str:
    p=max(0,min(1000,int(progress_milli)))
    rows=edge_terrain_segments(edge)
    for row in rows:
        if row["start_milli"] <= p < row["end_milli"] or (p==1000 and row["end_milli"]==1000):
            return str(row["terrain"])
    return str(rows[-1]["terrain"])


def edge_weighted_terrain_time_milli(edge: Mapping[str, Any], terrain_time_milli: Mapping[str, Any]) -> int:
    total=0; covered=0
    for row in edge_terrain_segments(edge):
        span=max(0,int(row["end_milli"])-int(row["start_milli"]))
        total += span * max(1,int(terrain_time_milli.get(str(row["terrain"]),1000)))
        covered += span
    return max(1,total//max(1,covered))


def movement_edge_progress_milli(movement: Mapping[str, Any], edge: Mapping[str, Any]) -> int:
    """Return authored-edge progress even when the journey travels in reverse."""
    required=max(1,int(movement.get("required_seconds",1)))
    elapsed=max(0,min(required,int(movement.get("elapsed_seconds",0))))
    frac=elapsed*1000//required
    start=int(movement.get("edge_start_milli",0))
    end=int(movement.get("edge_end_milli",1000))
    if "edge_start_milli" not in movement or "edge_end_milli" not in movement:
        origin=str(movement.get("segment_origin_place_ref") or movement.get("origin_place_ref") or "")
        if origin and origin==str(edge.get("to") or ""):
            start,end=1000,0
    return max(0,min(1000,start + (end-start)*frac//1000))


def _stable(seed: str) -> int:
    return int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16],16)


def _rect(ref: str, x: int, y: int, w: int, h: int, *, zone_ref: str, cover: int=0, los: bool=True, projectile: bool=True, movement: bool=True, height: int=2500) -> dict[str, Any]:
    return {
        "obstacle_ref":ref,"shape":"rect","zone_ref":zone_ref,
        "min_x_mm":x-w//2,"max_x_mm":x+w//2,"min_y_mm":y-h//2,"max_y_mm":y+h//2,
        "height_mm":height,"blocks_los":bool(los),"blocks_projectiles":bool(projectile),
        "blocks_movement":bool(movement),"blocks_melee":bool(movement),"cover_milli":max(0,min(1000,cover)),
    }


def _circle(ref: str, x: int, y: int, r: int, *, zone_ref: str, cover: int=0, los: bool=True, movement: bool=True, height: int=4000) -> dict[str, Any]:
    return {
        "obstacle_ref":ref,"shape":"circle","zone_ref":zone_ref,"center_x_mm":x,"center_y_mm":y,"radius_mm":r,
        "height_mm":height,"blocks_los":bool(los),"blocks_projectiles":bool(los),"blocks_movement":bool(movement),
        "blocks_melee":bool(movement),"cover_milli":max(0,min(1000,cover)),
    }


def terrain_obstacles(*, terrain: str, zone_ref: str, seed_ref: str, frontage_m: int | None=None) -> list[dict[str, Any]]:
    """Project a bounded deterministic local patch, never persistent scenery."""
    t=str(terrain); seed=_stable(f"{seed_ref}|{t}|{zone_ref}")
    rows:list[dict[str,Any]]=[]
    # Keep the central contact lane usable. Obstacles live mostly to the flanks.
    if t=="forest":
        for i in range(8):
            sign=-1 if i%2==0 else 1
            x=((seed>>(i*5))%18000)-9000
            y=sign*(4500+((seed>>(i*7+3))%9000))
            rows.append(_circle(f"terrain:{seed_ref}:tree:{i}",int(x),int(y),450+int((seed>>(i*3))%350),zone_ref=zone_ref,cover=650,los=True,movement=True,height=7000))
    elif t=="marsh":
        for i in range(4):
            x=((seed>>(i*8))%16000)-8000; y=(-1 if i%2==0 else 1)*(4500+((seed>>(i*6+2))%7000))
            rows.append(_rect(f"terrain:{seed_ref}:deep_mud:{i}",int(x),int(y),3500,2500,zone_ref=zone_ref,cover=120,los=False,projectile=False,movement=True,height=100))
    elif t in {"mountain","hills","highland"}:
        count=5 if t=="mountain" else 3
        for i in range(count):
            x=((seed>>(i*7))%18000)-9000; y=(-1 if i%2==0 else 1)*(5000+((seed>>(i*5+4))%8000))
            rows.append(_rect(f"terrain:{seed_ref}:rock:{i}",int(x),int(y),2500+int(seed%1800),1800+int((seed>>9)%1400),zone_ref=zone_ref,cover=700,los=True,movement=True,height=3500))
    elif t=="river_plain":
        # A ditch/bank constrains lateral movement without pretending every river plain is a crossing.
        rows.append(_rect(f"terrain:{seed_ref}:bank",0,12000,24000,1800,zone_ref=zone_ref,cover=300,los=False,projectile=False,movement=True,height=500))
    elif t=="urban":
        rows.extend([
            _rect(f"terrain:{seed_ref}:wall:left",0,-6500,28000,3500,zone_ref=zone_ref,cover=900,los=True,movement=True,height=5000),
            _rect(f"terrain:{seed_ref}:wall:right",0,6500,28000,3500,zone_ref=zone_ref,cover=900,los=True,movement=True,height=5000),
        ])
    return rows


def combat_environment(*, terrain: str, zone_ref: str, seed_ref: str, weather: Mapping[str, Any] | None=None, frontage_m: int | None=None, extra_obstacles: Sequence[Mapping[str,Any]]=()) -> dict[str, Any]:
    profile=terrain_profile(terrain)
    condition=str((weather or {}).get("condition") or "clear")
    ground=str((weather or {}).get("ground") or "dry")
    vis_weather={"clear":1000,"fog":600,"rain":820,"storm":650,"snow":850,"snowstorm":550}.get(condition,1000)
    ground_move={"dry":1000,"wet":950,"muddy":800,"snow":850,"ice":700}.get(ground,1000)
    movement=max(300,profile["movement_milli"]*ground_move//1000)
    mounted=max(250,profile["mounted_milli"]*ground_move//1000)
    visibility=max(200,min(1200,profile["visibility_milli"]*vis_weather//1000))
    obstacles=terrain_obstacles(terrain=terrain,zone_ref=zone_ref,seed_ref=seed_ref,frontage_m=frontage_m)
    obstacles.extend(dict(row) for row in extra_obstacles if isinstance(row,Mapping))
    return {
        "terrain":str(terrain),"movement_milli":movement,"mounted_milli":mounted,
        "visibility_milli":visibility,"concealment_milli":profile["concealment_milli"],
        "frontage_m":int(frontage_m or profile["frontage_m"]),"weather_condition":condition,"ground":ground,
        "obstacles":obstacles,
    }

__all__=["combat_environment","edge_terrain_segments","edge_weighted_terrain_time_milli","movement_edge_progress_milli","place_terrain","route_terrain_at","site_combat_terrain","terrain_obstacles","terrain_profile"]
