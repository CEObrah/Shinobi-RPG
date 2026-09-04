"""Deterministic route traffic, patrol, outlaw pressure and local movement."""
from __future__ import annotations
import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'

@lru_cache(maxsize=1)
def _route_activity_data() -> Mapping[str, Any]:
    return json.loads((_MW/'route-activity.json').read_text())

def route_traffic_milli(road_quality: str) -> int:
    cfg=_route_activity_data(); rows=cfg.get('traffic_milli_by_road_quality',{})
    if not isinstance(rows,Mapping): rows={}
    return max(0,min(1000,int(rows.get(str(road_quality),cfg.get('default_traffic_milli',350)))))

# One shared definition of every nonterminal route status that still owns
# people/assets and therefore requires scheduler + availability service.
ROUTE_SERVICE_STATUSES = frozenset({
    "active", "traveling", "outbound", "returning", "lodging_rest",
    "field_rest", "contact_pending", "pursuing", "party_extinguished",
    "awaiting_return_logistics",
})
def route_exposure(*,traffic_milli:int,patrol_presence:int,outlaw_fighters:int,weather_visibility_milli:int,night:bool)->dict[str,int]:
    cfg=_route_activity_data()
    outlaw=max(0,outlaw_fighters)*int(cfg['outlaw_pressure_milli_per_fighter']); patrol=max(0,patrol_presence)*int(cfg['patrol_effect_milli_per_presence'])
    conceal=(1000-max(0,min(1000,weather_visibility_milli)))//3 + (180 if night else 0)
    threat=max(0,min(2000,outlaw+conceal-patrol)); witness=max(0,min(1000,int(traffic_milli)-conceal//2+patrol*2))
    return {'threat_milli':threat,'witness_milli':witness,'patrol_suppression_milli':patrol}
def local_travel_minutes(*,distance_km_tenths:int,site_kind:str='city',crowd_milli:int=1000)->int:
    cfg=json.loads((_MW/'local-geography.json').read_text()); base=float(cfg['walking_speed_kph']); factor=1000
    if site_kind=='compound': factor=int(cfg['compound_speed_milli'])
    elif site_kind=='mountain': factor=int(cfg['mountain_site_speed_milli'])
    else: factor=min(int(cfg['crowded_city_speed_milli']),max(200,int(crowd_milli)))
    kph=base*factor/1000.0; km=max(0,distance_km_tenths)/10.0
    return max(1,int(round(km/max(0.1,kph)*60)))


def _exact_refs(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(x) for x in value if isinstance(x, str) and x))


def route_potential_controller_refs(movement: Mapping[str, Any]) -> list[str]:
    """Exact travelers who could control the party if physically capable.

    Protected, captive, and rescued people are physically carried participants,
    never controllers merely because they share the same movement owner.
    """
    participants = _exact_refs(movement.get("participant_refs"))
    carried = set(_exact_refs(movement.get("protected_person_refs")))
    carried.update(_exact_refs(movement.get("captive_refs")))
    carried.update(_exact_refs(movement.get("rescued_refs")))
    return [ref for ref in participants if ref not in carried]


def route_controlling_refs(movement: Mapping[str, Any]) -> list[str]:
    """Project the exact people currently assigned to control one route party.

    The complete participant list is physical presence. Protected/captive/rescued
    people are carried. Explicit controller lists used during contact staging narrow the controller
    subset; otherwise every non-carried participant is a potential controller.
    """
    potential = route_potential_controller_refs(movement)
    participants = set(_exact_refs(movement.get("participant_refs")))
    if isinstance(movement.get("raider_refs"), list):
        return [ref for ref in _exact_refs(movement.get("raider_refs")) if ref in participants and ref in potential]
    if isinstance(movement.get("escort_refs"), list):
        return [ref for ref in _exact_refs(movement.get("escort_refs")) if ref in participants and ref in potential]
    return potential


def compact_route_movement_roles(movement: Mapping[str, Any]) -> dict[str, Any]:
    """Remove route-role aliases that are derivable from current movement facts."""
    out = copy.deepcopy(dict(movement))
    participants = _exact_refs(out.get("participant_refs"))
    out["participant_refs"] = participants
    controllers = route_controlling_refs(out)
    if str(out.get("movement_kind") or "") == "raid_return":
        # Captive/rescued refs already identify the non-controlling travelers.
        # Storing the same raider roster again as escort/raider lists is bloat.
        out.pop("escort_refs", None)
        out.pop("raider_refs", None)
    else:
        out.pop("raider_refs", None)
        potential = route_potential_controller_refs(out)
        if controllers == potential:
            out.pop("escort_refs", None)
        else:
            out["escort_refs"] = controllers
    return out
