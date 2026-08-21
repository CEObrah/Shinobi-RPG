"""Deterministic route traffic, patrol, outlaw pressure and local movement."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'
def route_exposure(*,traffic_milli:int,patrol_presence:int,outlaw_fighters:int,weather_visibility_milli:int,night:bool)->dict[str,int]:
    cfg=json.loads((_MW/'route-activity.json').read_text())
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
