"""Universal deterministic Jianghu personal-combat kernel primitives."""
from __future__ import annotations
import copy, json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping
from shinobi_runtime.combat.geometry import targets_intersecting_geometry
from .qi import control_efficiency_milli, redistribution_latency_ms, safe_flow_milli_per_second as safe_qi_flow_milli_per_second
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'
@lru_cache(maxsize=1)
def _data(): return json.loads((_MW/'combat.json').read_text(encoding='utf-8'))
def _clamp(lo:int,hi:int,v:int)->int:return max(lo,min(hi,v))


def physical_action_targets(*,positions:Mapping[str,Mapping[str,Any]],actor_ref:str,candidate_refs:list[str]|tuple[str,...],geometry:Mapping[str,Any]|None=None,target_limit:int=1,maximum_range_m:float|int|None=None,aim_ref:str|None=None,obstacles:list[Mapping[str,Any]]|tuple[Mapping[str,Any],...]=(),channel:str="melee",trajectory:Mapping[str,Any]|None=None)->tuple[str,...]:
    """Resolve physical victims from local geometry, never from side membership."""
    return targets_intersecting_geometry(
        positions, actor_ref=actor_ref, candidate_refs=candidate_refs, geometry=geometry,
        aim_ref=aim_ref, obstacles=obstacles, channel=channel,
        target_limit=target_limit, maximum_range_m=maximum_range_m, trajectory=trajectory,
    )

def allocate_qi(*,qi:int,qi_control:int,current_qi_milli:int,allocations_milli:Mapping[str,int],duration_ms:int,carry_milli_ms:int=0)->dict[str,Any]:
    """Allocate real Qi flow for a finite interval without quantizing low flow away.

    ``allocations_milli`` is requested channel flow in milli-Qi per second. The
    closed combat command caps total requested flow at 1000. ``carry_milli_ms``
    preserves sub-milli expenditure between exact-combat clock intervals: 1000
    milli-ms equals one milli-Qi. Effects use the returned ``allocations_milli``,
    which is delivered flow after resource limits, never merely requested flow.
    """
    if duration_ms<0: raise ValueError('duration invalid')
    allowed=set(_data()['qi']['allocations']); unknown=set(allocations_milli)-allowed
    if unknown: raise ValueError('unknown qi allocation')
    requested_allocations={str(k):max(0,int(v)) for k,v in allocations_milli.items() if max(0,int(v))>0}
    requested=sum(requested_allocations.values())
    if requested>1000: raise ValueError('qi allocation exceeds whole-body flow cap')
    safe=safe_qi_flow_milli_per_second(qi,qi_control)
    capacity=max(0,int(qi))*1000
    current=max(0,min(capacity,int(current_qi_milli)))
    carry=max(0,min(999,int(carry_milli_ms)))

    # Work in milli-Qi * milliseconds so a 199 milli-Qi/s flow remains a real
    # 199 flow even across a 1 ms contact window. Whole milli-Qi is deducted
    # only after enough sub-milli work accumulates.
    interval_units=requested*duration_ms
    available_units=max(0,current*1000-carry)
    delivered_units=min(interval_units,available_units)
    if requested<=0 or duration_ms<=0 or interval_units<=0:
        delivered_total=0
    elif delivered_units>=interval_units:
        delivered_total=requested
    else:
        delivered_total=requested*delivered_units//interval_units
    delivered={}
    if requested>0 and delivered_total>0:
        remaining=delivered_total
        rows=list(requested_allocations.items())
        for index,(key,value) in enumerate(rows):
            amount=remaining if index==len(rows)-1 else value*delivered_total//requested
            amount=max(0,min(value,amount)); delivered[key]=amount; remaining-=amount

    accumulated_units=carry+delivered_units
    spent=min(current,accumulated_units//1000)
    carry_after=accumulated_units-spent*1000
    if spent>=current and current>0:
        carry_after=0
    over=max(0,delivered_total-safe)
    strain=over*duration_ms//1000*int(_data()['qi']['overdraw_strain_per_flow_milli'])//1000
    return {
        'safe_flow_milli_per_second':safe,
        'requested_flow_milli_per_second':requested,
        'delivered_flow_milli_per_second':delivered_total,
        'flow_overdraw_milli_per_second':over,
        'current_qi_milli_spent':spent,
        'current_qi_milli_after':current-spent,
        'strain_milli_added':strain,
        'requested_allocations_milli':requested_allocations,
        'allocations_milli':delivered,
        'resource_limited':delivered_units<interval_units,
        'qi_flow_carry_milli_ms_before':carry,
        'qi_flow_carry_milli_ms_after':carry_after,
        'redistribution_latency_ms':redistribution_latency_ms(qi_control),
    }

def active_defense_available(state:Mapping[str,Any],*,attacker_ref:str,at_ms:int,reaction_score:int,angle_deg:int,balance_milli:int=1000,limb_commitment_milli:int=0)->dict[str,Any]:
    cfg=_data()['active_defense']; out=copy.deepcopy(dict(state)); load=max(0,int(out.get('load_milli',0))); last=int(out.get('last_at_ms',-10**9)); recent=dict(out.get('recent_attackers',{}))
    recovery=max(180,120000//max(40,reaction_score+40))
    elapsed=max(0,at_ms-last); load=max(0,load-elapsed*1000//recovery)
    cutoff=at_ms-recovery; recent={k:int(v) for k,v in recent.items() if int(v)>=cutoff}
    distinct=max(0,len(set(recent)|{attacker_ref})-1); conflict=min(int(cfg['max_distinct_attacker_penalty_milli']),distinct*int(cfg['distinct_attacker_penalty_milli']))
    angle=abs(((int(angle_deg)+180)%360)-180)
    angle_pen=int(cfg['rear_angle_penalty_milli']) if angle>=120 else (int(cfg['side_angle_penalty_milli']) if angle>=60 else 0)
    balance_pen=max(0,1000-max(0,min(1000,balance_milli)))*int(cfg['low_balance_penalty_max_milli'])//1000
    limb_pen=max(0,min(1000,limb_commitment_milli))*int(cfg['limb_commitment_penalty_max_milli'])//1000
    available=max(int(cfg['minimum_available_milli']),1000-load-conflict-angle_pen-balance_pen-limb_pen)
    out.update(load_milli=load,last_at_ms=at_ms,recent_attackers=recent)
    return {'available_milli':available,'recovery_ms':recovery,'distinct_attackers':distinct+1,'state_after_decay':out,'penalties':{'conflict':conflict,'angle':angle_pen,'balance':balance_pen,'limb_commitment':limb_pen}}

def commit_active_defense(state:Mapping[str,Any],*,attacker_ref:str,at_ms:int,threat_speed:int,reaction_score:int,body_commitment_milli:int)->dict[str,Any]:
    cfg=_data()['active_defense']; out=copy.deepcopy(dict(state)); load=max(0,int(out.get('load_milli',0))); ratio=min(2600,max(1,threat_speed)*1000//max(1,reaction_score))
    base=max(0,int(cfg.get('base_commitment_milli',180))); maximum=max(base,int(cfg.get('maximum_commitment_milli',900)))
    commitment=_clamp(base,maximum,base+ratio//4+max(0,min(1000,body_commitment_milli))//3)
    out['load_milli']=min(1000,load+commitment); out['last_at_ms']=at_ms; recent=dict(out.get('recent_attackers',{})); recent[attacker_ref]=at_ms; out['recent_attackers']=recent
    return {'commitment_milli':commitment,'state_after':out}
