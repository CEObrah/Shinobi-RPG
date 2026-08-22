"""Universal deterministic Jianghu personal-combat kernel primitives."""
from __future__ import annotations
import copy, json, math
from pathlib import Path
from typing import Any, Mapping
from shinobi_runtime.combat.geometry import targets_intersecting_geometry
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'
def _data(): return json.loads((_MW/'combat.json').read_text(encoding='utf-8'))
def _clamp(lo:int,hi:int,v:int)->int:return max(lo,min(hi,v))


def physical_action_targets(*,positions:Mapping[str,Mapping[str,Any]],actor_ref:str,candidate_refs:list[str]|tuple[str,...],geometry:Mapping[str,Any]|None=None,target_limit:int=1,maximum_range_m:float|int|None=None,aim_ref:str|None=None,obstacles:list[Mapping[str,Any]]|tuple[Mapping[str,Any],...]=(),channel:str="melee",trajectory:Mapping[str,Any]|None=None)->tuple[str,...]:
    """Resolve physical victims from local geometry, never from side membership."""
    return targets_intersecting_geometry(
        positions, actor_ref=actor_ref, candidate_refs=candidate_refs, geometry=geometry,
        aim_ref=aim_ref, obstacles=obstacles, channel=channel,
        target_limit=target_limit, maximum_range_m=maximum_range_m, trajectory=trajectory,
    )

def control_efficiency_milli(qi_control:int)->int:
    c=max(0,qi_control); return c*1000//(c+100) if c else 0
def safe_qi_flow_milli_per_second(qi:int,qi_control:int)->int:
    # milli-Qi flow keeps small values useful without floating state.
    q=max(0,qi); eff=control_efficiency_milli(qi_control)
    return int(math.isqrt(q*1000))* (1000+eff)//1000
def redistribution_latency_ms(qi_control:int)->int:
    return max(50,60000//(100+max(0,qi_control)))

def allocate_qi(*,qi:int,qi_control:int,current_qi_milli:int,allocations_milli:Mapping[str,int],duration_ms:int)->dict[str,Any]:
    if duration_ms<0: raise ValueError('duration invalid')
    allowed=set(_data()['qi']['allocations']); unknown=set(allocations_milli)-allowed
    if unknown: raise ValueError('unknown qi allocation')
    requested=sum(max(0,int(v)) for v in allocations_milli.values())
    safe=safe_qi_flow_milli_per_second(qi,qi_control)
    available_flow=safe*duration_ms//1000
    spent=min(max(0,current_qi_milli),requested*duration_ms//1000)
    over=max(0,requested-safe)
    strain=over*duration_ms//1000*int(_data()['qi']['overdraw_strain_per_flow_milli'])//1000
    return {'safe_flow_milli_per_second':safe,'requested_flow_milli_per_second':requested,'flow_overdraw_milli_per_second':over,'current_qi_milli_spent':spent,'current_qi_milli_after':max(0,current_qi_milli-spent),'strain_milli_added':strain,'allocations_milli':{k:max(0,int(v)) for k,v in allocations_milli.items()},'redistribution_latency_ms':redistribution_latency_ms(qi_control)}

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
    out=copy.deepcopy(dict(state)); load=max(0,int(out.get('load_milli',0))); ratio=min(2600,max(1,threat_speed)*1000//max(1,reaction_score)); commitment=_clamp(180,900,180+ratio//4+max(0,min(1000,body_commitment_milli))//3)
    out['load_milli']=min(1000,load+commitment); out['last_at_ms']=at_ms; recent=dict(out.get('recent_attackers',{})); recent[attacker_ref]=at_ms; out['recent_attackers']=recent
    return {'commitment_milli':commitment,'state_after':out}
