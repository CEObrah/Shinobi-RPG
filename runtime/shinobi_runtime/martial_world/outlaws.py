"""Deterministic outlaw route pressure and attack decision policy."""
from __future__ import annotations
from typing import Any, Mapping, Sequence

def route_threat_score(outlaw_factions:Sequence[Mapping[str,Any]],*,route_id:str,combat_ready_by_faction:Mapping[str,int]|None=None)->int:
    score=0
    for f in outlaw_factions:
        if f.get('type')!='outlaw_faction' or route_id not in f.get('operating_routes',[]): continue
        fid=str(f.get('faction_id') or ''); martial=max(0,int((combat_ready_by_faction or {}).get(fid,0))); aggression=int(f.get('autonomy_policy',{}).get('external_aggression',50));
        skill=int(f.get('training',{}).get('stealth_scouting',50)); pressure=int(f.get('outlaw_policy',{}).get('loot_need_threshold',55))
        enterprises=f.get('enterprises',{}) if isinstance(f.get('enterprises'),Mapping) else {}
        scale=f.get('enterprise_scale',{}) if isinstance(f.get('enterprise_scale'),Mapping) else {}
        criminal_level=max(0,int(enterprises.get('criminal_enterprise',0)))
        criminal_row=scale.get('criminal_enterprise',{}) if isinstance(scale.get('criminal_enterprise'),Mapping) else {}
        cells=max(0,int(criminal_row.get('registered_cells_or_ventures',0)))
        organization_milli=1000 + min(500, criminal_level*60 + cells*15)
        base=martial*(aggression+skill+pressure)//300
        score += base*organization_milli//1000
    return min(300,score)

def attack_decision(*,own_available_martial:int,own_combat_index:int,known_escort_count:int,known_escort_combat_index:int,cargo_value_cash:int,food_reserve_days:int,treasury_cash:int,minimum_attack_advantage_milli:int,risk_tolerance:int)->dict[str,Any]:
    if own_available_martial<=0: return {'attack':False,'reason':'no_available_force'}
    own=max(1,own_available_martial)*max(1,own_combat_index)
    enemy=max(1,known_escort_count)*max(1,known_escort_combat_index)
    advantage=own*1000//enemy
    need=max(0,60-food_reserve_days)*20 + max(0,20000-treasury_cash)//1000
    value=min(200, cargo_value_cash//10000)
    motive=need+value+risk_tolerance
    threshold=max(500,minimum_attack_advantage_milli - motive*2)
    return {'attack':advantage>=threshold,'advantage_milli':advantage,'required_advantage_milli':threshold,'motive_score':motive}
