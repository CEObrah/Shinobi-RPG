"""Anatomy-first health and injury settlement with no HP authority.

Physical contact resolves against coarse anatomical zones.  Contact may additionally name a
specific structure (eye, wrist, knee, organ, etc.).  Structure damage is stored
once on the wound and all combat penalties are derived from those wounds; the
save does not carry separate hand-maintained "blind" or "+/- defense" flags.
"""
from __future__ import annotations
import copy, json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

_ROOT=Path(__file__).resolve().parents[3]
_MW=_ROOT/'game/data/martial-world'

@lru_cache(maxsize=1)
def _data():
    return json.loads((_MW/'anatomy.json').read_text(encoding='utf-8'))
def blood_volume_ml(body_mass_kg:float)->int: return max(1,int(round(max(1.0,body_mass_kg)*int(_data()['blood_ml_per_kg']))))

def structure_definition(structure_ref:str|None)->Mapping[str,Any]|None:
    if not structure_ref:return None
    row=_data().get('structures',{}).get(structure_ref)
    return row if isinstance(row,Mapping) else None

def target_zone(*,zone:str|None=None,structure_ref:str|None=None)->str:
    if structure_ref:
        row=structure_definition(structure_ref)
        if row is None:raise KeyError(structure_ref)
        return str(row['zone'])
    if zone and zone in _data()['zones']:return str(zone)
    raise KeyError(zone)

def structure_family_members(family_ref:str)->tuple[str,...]:
    rows=_data().get('structure_families',{}).get(family_ref,())
    if not isinstance(rows,list):raise KeyError(family_ref)
    return tuple(str(x) for x in rows)

def _structure_damage(structure:Mapping[str,Any],*,cut:int,pierce:int,blunt:int,penetration:int)->int:
    biases=structure.get('channel_bias_milli',{}) if isinstance(structure.get('channel_bias_milli'),Mapping) else {}
    weighted=(
        cut*int(biases.get('cut',1000))+
        pierce*int(biases.get('pierce',1000))+
        blunt*int(biases.get('blunt',1000))+
        penetration*int(biases.get('penetration',1000))
    )//4000
    return min(200,max(0,weighted*int(structure.get('vulnerability_milli',1000))//1000))

def _functional_effects(structure:Mapping[str,Any],structure_damage:int)->dict[str,int]:
    threshold=max(1,int(structure.get('destruction_threshold',160)))
    localized=min(100,structure_damage*100//threshold)
    out={}
    for key,weight in (structure.get('functions',{}) or {}).items():
        out[str(key)]=min(100,localized*max(0,int(weight))//100)
    return out


def _outcome_effects(outcome_ref:str|None)->dict[str,int]:
    if not outcome_ref:
        return {}
    row=_data().get('permanent_outcomes',{}).get(str(outcome_ref),{})
    effects=row.get('effects',{}) if isinstance(row,Mapping) else {}
    return {str(k):max(0,min(100,int(v))) for k,v in effects.items()} if isinstance(effects,Mapping) else {}

def _permanent_outcome(*,structure_ref:str|None,structure:Mapping[str,Any]|None,structure_damage:int,cut:int)->tuple[str|None,dict[str,int]]:
    if structure is None:
        return None,{}
    severance_ref=structure.get('severance_outcome')
    severance_threshold=max(1,int(structure.get('severance_cut_threshold',10_000)))
    if isinstance(severance_ref,str) and cut>=severance_threshold:
        return severance_ref,_outcome_effects(severance_ref)
    threshold=max(1,int(structure.get('destruction_threshold',160)))
    if structure_damage>=threshold:
        outcome=structure.get('destruction_outcome')
        if isinstance(outcome,str) and outcome:
            effects=_outcome_effects(outcome)
            if effects:
                return outcome,effects
        # Destroyed named musculoskeletal/sensory structures retain their full
        # authored functional loss even when no special amputation label applies.
        effects={str(k):max(0,min(100,int(v))) for k,v in (structure.get('functions',{}) or {}).items()}
        if effects:
            return f'destroyed:{structure_ref}',effects
    return None,{}

def wound_requires_persistence(wound:Mapping[str,Any])->bool:
    return bool(wound.get('permanent')) or bool(wound.get('permanent_outcome')) or bool(wound.get('permanent_effects'))

def _healed_permanent_residual(wound:Mapping[str,Any])->dict[str,Any]:
    out=copy.deepcopy(dict(wound))
    permanent=out.get('permanent_effects',{}) if isinstance(out.get('permanent_effects'),Mapping) else {}
    out['functional_effects']={str(k):max(0,min(100,int(v))) for k,v in permanent.items()}
    out['function_loss_pct']=max([0]+list(out['functional_effects'].values()))
    for key in ('cut','pierce','blunt','penetration','severity','bleeding_ml_per_min','fracture','tendon_damage','nerve_damage','organ_trauma','pain'):
        out[key]=0

    for _key in ('hemostasis_progress_milli','hemostasis_progress_units','hemostasis_initial_bleeding_ml_per_min','hemostasis_blood_remainder'):
        out.pop(_key,None)
    out['treated']=True; out['stabilized']=True; out['healed']=True; out['healing_progress_milli']=100000
    return out

def _healed_nonpermanent_residual(wound:Mapping[str,Any])->dict[str,Any]:
    out=copy.deepcopy(dict(wound))
    for key in ('cut','pierce','blunt','penetration','severity','bleeding_ml_per_min','fracture','tendon_damage','nerve_damage','organ_trauma','structure_damage','pain','function_loss_pct'):
        out[key]=0
    out['functional_effects']={}
    out['permanent']=False; out['permanent_outcome']=None; out['permanent_effects']={}

    for _key in ('hemostasis_progress_milli','hemostasis_progress_units','hemostasis_initial_bleeding_ml_per_min','hemostasis_blood_remainder'):
        out.pop(_key,None)
    out['treated']=True; out['stabilized']=True; out['healed']=True; out['healing_progress_milli']=100000
    return out


def wound_from_contact(*,zone:str|None=None,structure_ref:str|None=None,cut:int,pierce:int,blunt:int,penetration:int,created_at:str)->dict[str,Any]:
    resolved_zone=target_zone(zone=zone,structure_ref=structure_ref)
    z=_data()['zones'][resolved_zone]
    cut=max(0,cut); pierce=max(0,pierce); blunt=max(0,blunt); penetration=max(0,penetration)
    tissue=cut+pierce+blunt//2+penetration
    vascular=tissue*int(z['vascularity'])//100
    fracture=max(0,blunt+penetration//2-int(z['bone']))
    tendon=max(0,(cut+pierce)*int(z['tendon'])//180)
    nerve=max(0,(cut+pierce+penetration)*int(z['nerve'])//220)
    organ=max(0,(pierce+penetration+blunt//2)*int(z['organ'])//160)
    bleeding=max(0,(vascular*(cut+pierce+penetration))//max(25,300))
    pain=min(200,(cut+pierce+blunt+penetration)//2+fracture//2)
    function_loss=min(100,max(fracture,tendon,nerve)//2+min(50,tissue//5))
    severity=min(200,max(cut,pierce,blunt,penetration,tissue//2))
    structure=structure_definition(structure_ref)
    structure_damage=0; functional_effects={}; permanent_effects={}; permanent_outcome=None; side=None
    if structure is not None:
        structure_damage=_structure_damage(structure,cut=cut,pierce=pierce,blunt=blunt,penetration=penetration)
        functional_effects=_functional_effects(structure,structure_damage)
        permanent_outcome,permanent_effects=_permanent_outcome(structure_ref=structure_ref,structure=structure,structure_damage=structure_damage,cut=cut)
        for key,val in permanent_effects.items(): functional_effects[key]=max(int(functional_effects.get(key,0)),int(val))
        side=str(structure.get('side','center'))
        if functional_effects:function_loss=max(function_loss,max(functional_effects.values()))
    return {
        'zone':resolved_zone,'structure_ref':structure_ref,'side':side,'created_at':created_at,
        'cut':cut,'pierce':pierce,'blunt':blunt,'penetration':penetration,'severity':severity,
        'bleeding_ml_per_min':bleeding,'fracture':min(200,fracture),'tendon_damage':min(200,tendon),
        'nerve_damage':min(200,nerve),'organ_trauma':min(200,organ),'structure_damage':structure_damage,
        'functional_effects':functional_effects,'permanent':bool(permanent_effects),'permanent_outcome':permanent_outcome,
        'permanent_effects':permanent_effects,'pain':pain,'function_loss_pct':function_loss,
        'treated':False,'healing_progress_milli':0,
    }


def _wound_locus(wound:Mapping[str,Any])->tuple[str,str,str]:
    """Return the bounded current-state bucket for one injury.

    A named anatomical structure is its own exact locus.  Contacts that do not
    resolve a named structure are summarized by coarse zone/side.  This keeps
    the save as *current anatomy* rather than an append-only combat transcript
    while retaining exact persistent damage to eyes, joints, tendons, organs,
    limbs and other authored structures.
    """
    structure_ref=wound.get('structure_ref')
    if isinstance(structure_ref,str) and structure_ref:
        return ('structure',structure_ref,'')
    return ('zone',str(wound.get('zone') or 'unknown'),str(wound.get('side') or 'center'))


def _merge_current_wound(existing:Mapping[str,Any],incoming:Mapping[str,Any])->dict[str,Any]:
    """Merge repeated contact at one locus into one current clinical injury.

    Damage is cumulative at the locus, so repeated lesser hits can still break,
    destroy or sever anatomy.  Permanent effects are floors and can never be
    erased by later recovery or by merging a new acute contact.
    """
    if _wound_locus(existing)!=_wound_locus(incoming):
        raise ValueError('cannot merge wounds at different loci')
    out=copy.deepcopy(dict(existing)); new=copy.deepcopy(dict(incoming))
    out['zone']=str(new.get('zone') or out.get('zone') or 'unknown')
    if new.get('structure_ref') is not None: out['structure_ref']=new.get('structure_ref')
    if new.get('side') is not None: out['side']=new.get('side')

    # These are current burden scalars, not receipts.  Summation within a locus
    # preserves cumulative trauma while bounded caps retain their authored
    # 0..200 interpretation. Bleeding is a physical flow and remains additive.
    for key in ('cut','pierce','blunt','penetration','fracture','tendon_damage','nerve_damage','organ_trauma','structure_damage','pain'):
        out[key]=min(200,max(0,int(out.get(key,0)))+max(0,int(new.get(key,0))))
    out['severity']=min(200,max(
        max(0,int(out.get('severity',0))),max(0,int(new.get('severity',0))),
        max(int(out.get(k,0)) for k in ('cut','pierce','blunt','penetration','fracture','tendon_damage','nerve_damage','organ_trauma','structure_damage'))
    ))
    out['bleeding_ml_per_min']=max(0,int(out.get('bleeding_ml_per_min',0)))+max(0,int(new.get('bleeding_ml_per_min',0)))
    # Fresh contact changes the physical bleeding source, so any transient
    # clotting accumulator for the prior flow is no longer authoritative.
    for _key in ('hemostasis_progress_milli','hemostasis_progress_units','hemostasis_initial_bleeding_ml_per_min','hemostasis_blood_remainder'):
        out.pop(_key,None)

    structure_ref=out.get('structure_ref') if isinstance(out.get('structure_ref'),str) else None
    structure=structure_definition(structure_ref)
    prior_perm=out.get('permanent_effects',{}) if isinstance(out.get('permanent_effects'),Mapping) else {}
    new_perm=new.get('permanent_effects',{}) if isinstance(new.get('permanent_effects'),Mapping) else {}
    permanent={str(k):max(0,min(100,max(int(prior_perm.get(k,0)),int(new_perm.get(k,0))))) for k in set(prior_perm)|set(new_perm)}
    accumulated_outcome,accumulated_effects=_permanent_outcome(
        structure_ref=structure_ref,structure=structure,structure_damage=int(out.get('structure_damage',0)),cut=int(out.get('cut',0)),
    )
    for key,val in accumulated_effects.items(): permanent[str(key)]=max(int(permanent.get(str(key),0)),int(val))

    if structure is not None:
        functional=_functional_effects(structure,int(out.get('structure_damage',0)))
    else:
        old_fx=out.get('functional_effects',{}) if isinstance(out.get('functional_effects'),Mapping) else {}
        new_fx=new.get('functional_effects',{}) if isinstance(new.get('functional_effects'),Mapping) else {}
        functional={str(k):min(100,max(0,int(old_fx.get(k,0)))+max(0,int(new_fx.get(k,0)))) for k in set(old_fx)|set(new_fx)}
    for key,val in permanent.items(): functional[str(key)]=max(int(functional.get(str(key),0)),int(val))
    out['functional_effects']=functional
    out['function_loss_pct']=max([0]+[max(0,min(100,int(v))) for v in functional.values()])
    out['permanent_effects']=permanent
    out['permanent']=bool(permanent)

    # If cumulative damage produces a new irreversible outcome, it supersedes a
    # weaker prior label. Otherwise retain the existing exact permanent outcome.
    if accumulated_outcome:
        out['permanent_outcome']=accumulated_outcome
    elif new.get('permanent_outcome'):
        out['permanent_outcome']=new.get('permanent_outcome')
    else:
        out['permanent_outcome']=out.get('permanent_outcome')

    # A fresh contact reopens the clinical episode.  Treatment/stabilization can
    # be re-applied afterward; old healing progress cannot instantly heal it.
    out['created_at']=new.get('created_at',out.get('created_at'))
    out['treated']=bool(out.get('treated')) and bool(new.get('treated'))
    out['stabilized']=bool(out.get('stabilized')) and bool(new.get('stabilized'))
    out['healed']=False
    out['healing_progress_milli']=0
    return out


def record_current_wound(wounds:Sequence[Mapping[str,Any]],wound:Mapping[str,Any])->list[dict[str,Any]]:
    """Record one contact without turning the save into a wound event ledger."""
    incoming=copy.deepcopy(dict(wound)); locus=_wound_locus(incoming); out=[]; merged=False
    for raw in wounds:
        if not isinstance(raw,Mapping):
            continue
        row=copy.deepcopy(dict(raw))
        if not merged and _wound_locus(row)==locus:
            out.append(_merge_current_wound(row,incoming)); merged=True
        else:
            out.append(row)
    if not merged: out.append(incoming)
    return out


def compact_current_wounds(wounds:Sequence[Mapping[str,Any]])->list[dict[str,Any]]:
    """Canonicalize current injuries to at most one row per anatomical locus."""
    out=[]
    for raw in wounds:
        if isinstance(raw,Mapping): out=record_current_wound(out,raw)
    return out

def _max_effects(wounds:Sequence[Mapping[str,Any]])->dict[str,int]:
    out={}
    for w in wounds:
        effects=w.get('functional_effects',{}) if isinstance(w.get('functional_effects'),Mapping) else {}
        for key,val in effects.items():out[str(key)]=max(out.get(str(key),0),max(0,min(100,int(val))))
    return out

def vision_state(wounds:Sequence[Mapping[str,Any]])->dict[str,int|str]:
    effects=_max_effects(wounds); rules=_data().get('vision_rules',{})
    left_loss=max(0,min(100,int(effects.get('vision_left',0))))
    right_loss=max(0,min(100,int(effects.get('vision_right',0))))
    left=100-left_loss; right=100-right_loss
    if left<=0 and right<=0:
        state='blind'; visual_loss=int(rules.get('both_eyes_destroyed_visual_perception_loss_pct',100)); depth_loss=100
    elif left<=0 or right<=0:
        state='monocular'; visual_loss=max(int(rules.get('one_eye_destroyed_visual_perception_loss_pct',30)),max(left_loss,right_loss)//3); depth_loss=max(int(rules.get('one_eye_destroyed_depth_perception_loss_pct',45)),abs(left_loss-right_loss)*int(rules.get('one_eye_destroyed_depth_perception_loss_pct',45))//100)
    else:
        state='binocular'; visual_loss=(left_loss+right_loss)//4; depth_loss=abs(left_loss-right_loss)//2
    return {
        'state':state,'left_eye_vision_pct':left,'right_eye_vision_pct':right,
        'visual_perception_loss_pct':min(100,visual_loss),'depth_perception_loss_pct':min(100,depth_loss),
        'blind_side_detection_penalty':int(rules.get('blind_side_detection_penalty',35)),
    }

def combat_status_families(wounds:Sequence[Mapping[str,Any]])->tuple[str,...]:
    v=vision_state(wounds); out=[]
    if v['state']=='blind':out.append('blind_both_eyes')
    elif v['state']=='monocular':
        out.append('monocular_vision')
        if int(v['left_eye_vision_pct'])<=0:out.append('blind_left_eye')
        if int(v['right_eye_vision_pct'])<=0:out.append('blind_right_eye')
    effects=_max_effects(wounds)
    rules=_data().get('permanent_outcomes',{})
    for wound in wounds:
        outcome=wound.get('permanent_outcome') if isinstance(wound,Mapping) else None
        row=rules.get(str(outcome),{}) if isinstance(rules,Mapping) and outcome else {}
        for status in row.get('status_families',[]) if isinstance(row,Mapping) else []:
            if isinstance(status,str): out.append(status)
    for side in ('left','right'):
        if int(effects.get(f'grip_{side}',0))>=90:out.append(f'{side}_hand_disabled')
        if max(int(effects.get(f'leg_{side}',0)),int(effects.get(f'footwork_{side}',0)))>=90:out.append(f'{side}_leg_disabled')
    return tuple(dict.fromkeys(out))

def functional_penalties(wounds:Sequence[Mapping[str,Any]])->dict[str,int]:
    out={'arm':0,'grip':0,'leg':0,'footwork':0,'senses':0,'vision':0,'depth_perception':0,'weapon_control':0,'core':0,'breathing':0,'consciousness':0,
         'arm_left':0,'arm_right':0,'grip_left':0,'grip_right':0,'weapon_control_left':0,'weapon_control_right':0,'leg_left':0,'leg_right':0,'footwork_left':0,'footwork_right':0}
    zones=_data()['zones']
    for w in wounds:
        z=zones.get(w.get('zone'),{}); fn=z.get('function'); loss=max(0,min(100,int(w.get('function_loss_pct',0))))
        if fn=='arm':out['arm']=max(out['arm'],loss)
        elif fn=='grip':out['grip']=max(out['grip'],loss)
        elif fn=='leg':out['leg']=max(out['leg'],loss);out['footwork']=max(out['footwork'],loss)
        elif fn=='footwork':out['footwork']=max(out['footwork'],loss)
        elif fn in {'senses','vision'}:out['senses']=max(out['senses'],loss)
        elif fn in {'core','breathing_and_core','core_and_spine','lower_body'}:out['core']=max(out['core'],loss)
        elif fn in {'consciousness','breathing_and_head_control'}:out['consciousness']=max(out['consciousness'],loss)
    effects=_max_effects(wounds)
    for key in ('arm_left','arm_right','grip_left','grip_right','weapon_control_left','weapon_control_right','leg_left','leg_right','footwork_left','footwork_right','breathing','consciousness','core'):
        out[key]=max(out[key],int(effects.get(key,0)))
    out['arm']=max(out['arm'],out['arm_left'],out['arm_right'])
    out['grip']=max(out['grip'],(out['grip_left']+out['grip_right'])//2,min(out['grip_left'],out['grip_right'])+max(out['grip_left'],out['grip_right'])//2)
    out['weapon_control']=min(100,(out['weapon_control_left']+out['weapon_control_right'])//2+min(out['weapon_control_left'],out['weapon_control_right'])//2)
    out['leg']=max(out['leg'],out['leg_left'],out['leg_right'])
    out['footwork']=max(out['footwork'],out['footwork_left'],out['footwork_right'])
    v=vision_state(wounds);out['vision']=int(v['visual_perception_loss_pct']);out['depth_perception']=int(v['depth_perception_loss_pct']);out['senses']=max(out['senses'],out['vision'])
    return out



def _paired_function_loss(left_loss: int, right_loss: int, *, unilateral_weight: int) -> int:
    """Combine left/right impairment without letting one healthy side erase the other.

    ``unilateral_weight`` is how strongly the worse side constrains a function
    when the opposite side is intact.  Bilateral loss naturally converges on the
    actual shared loss.  This is deliberately asymmetric because walking,
    running, climbing and combat footwork depend on two coordinated sides to
    different degrees.
    """
    left=max(0,min(100,int(left_loss))); right=max(0,min(100,int(right_loss)))
    worse=max(left,right); better=min(left,right); weight=max(50,min(100,int(unilateral_weight)))
    return max(0,min(100,(worse*weight+better*(100-weight)+50)//100))


def functional_capacity_factors(wounds:Sequence[Mapping[str,Any]])->dict[str,int]:
    """Derive current bodily capacities in permille from exact wounds.

    These are *functions*, not replacement attributes and not learned-skill
    penalties.  A master swordsman who loses a leg keeps the same sword skill,
    but standing, running, climbing, field travel and combat movement are no
    longer treated as though the healthy opposite leg can compensate almost
    completely.

    Lower-body functions intentionally use different bilateral dependencies:
    a severed Achilles, destroyed knee and missing leg therefore produce
    distinct deterministic consequences even when the other leg is healthy.
    """
    p=functional_penalties(wounds)
    ll=max(0,min(100,int(p.get('leg_left',0)))); rl=max(0,min(100,int(p.get('leg_right',0))))
    lf=max(0,min(100,int(p.get('footwork_left',0)))); rf=max(0,min(100,int(p.get('footwork_right',0))))
    lh=max(0,min(100,max(int(p.get('arm_left',0)),int(p.get('grip_left',0)),int(p.get('weapon_control_left',0)))))
    rh=max(0,min(100,max(int(p.get('arm_right',0)),int(p.get('grip_right',0)),int(p.get('weapon_control_right',0)))))

    # Side-specific lower-body impairment for each physical task.  Achilles
    # damage weighs push-off/footwork more heavily; knee/leg destruction weighs
    # support more heavily.  The paired combiner then limits compensation from
    # an intact opposite leg according to the actual task.
    walk_left=(ll*60+lf*40)//100; walk_right=(rl*60+rf*40)//100
    run_left=(ll*45+lf*55)//100; run_right=(rl*45+rf*55)//100
    stand_left=(ll*80+lf*20)//100; stand_right=(rl*80+rf*20)//100
    climb_left=(ll*60+lf*40)//100; climb_right=(rl*60+rf*40)//100
    ride_left=(ll*70+lf*30)//100; ride_right=(rl*70+rf*30)//100
    combat_left=(ll*50+lf*50)//100; combat_right=(rl*50+rf*50)//100

    walking=max(0,1000-_paired_function_loss(walk_left,walk_right,unilateral_weight=82)*10)
    running=max(0,1000-_paired_function_loss(run_left,run_right,unilateral_weight=94)*10)
    standing=max(0,1000-_paired_function_loss(stand_left,stand_right,unilateral_weight=76)*10)
    climbing_lower=max(0,1000-_paired_function_loss(climb_left,climb_right,unilateral_weight=88)*10)
    mounted_stability_lower=max(0,1000-_paired_function_loss(ride_left,ride_right,unilateral_weight=78)*10)
    combat_lower=max(0,1000-_paired_function_loss(combat_left,combat_right,unilateral_weight=92)*10)

    # Upper-body and sensory capacities remain independently derived.  A single
    # usable hand can compensate for many ordinary tasks but not for two-handed
    # precision, climbing or weapon control as if nothing happened.
    hand_loss=_paired_function_loss(lh,rh,unilateral_weight=72)
    manual=max(0,1000-hand_loss*10)
    vision_loss=max(0,min(100,int(p.get('vision',0))))
    visual=max(0,1000-vision_loss*10)
    breathing_loss=max(0,min(100,max(int(p.get('breathing',0)),int(p.get('core',0))//2)))
    respiratory=max(0,1000-breathing_loss*10)

    climbing=max(0,min(climbing_lower,(manual*60+standing*40)//100))
    mounted_stability=max(0,min(mounted_stability_lower,(manual*55+visual*20+standing*25)//100))
    combat_movement=max(0,min(combat_lower,(standing*70+respiratory*30)//100))
    physical_labor=max(0,(walking*20+standing*30+climbing*15+manual*20+respiratory*15)//100)
    general_work=max(0,(physical_labor*35+manual*35+visual*15+respiratory*15)//100)
    fine_manual=max(0,min(manual,(visual*3+manual)//4))
    field=max(0,min(walking,combat_movement,respiratory))

    return {
        'locomotion_milli':walking,'walking_milli':walking,'running_milli':running,
        'standing_milli':standing,'climbing_milli':climbing,'mounted_stability_milli':mounted_stability,
        'combat_movement_milli':combat_movement,'labor_milli':physical_labor,
        'manual_milli':manual,'vision_milli':visual,'respiratory_milli':respiratory,
        'general_work_milli':general_work,'field_mobility_milli':field,
        'fine_manual_milli':fine_manual,
    }

def _hemostasis_rate_milli_per_hour(wound:Mapping[str,Any])->int:
    # Classification is anchored to the bleeding source at the start of the
    # current clotting episode. It must not jump categories merely because the
    # same bleed has already tapered during earlier scheduler chunks.
    bleed=max(0,int(wound.get('hemostasis_initial_bleeding_ml_per_min',wound.get('bleeding_ml_per_min',0))))
    if bleed<=0:return 0
    cfg=_data().get('physiology',{}); rates=cfg.get('natural_hemostasis_progress_per_hour_milli',{}) if isinstance(cfg,Mapping) else {}
    if bool(wound.get('treated')) or bool(wound.get('stabilized')):
        key='treated'
    elif bleed>=max(1,int(cfg.get('catastrophic_bleeding_ml_per_min_min',60))) or int(wound.get('organ_trauma',0))>=180:
        key='catastrophic'
    elif bleed<=max(1,int(cfg.get('minor_bleeding_ml_per_min_max',5))):key='minor'
    elif bleed<=max(1,int(cfg.get('moderate_bleeding_ml_per_min_max',20))):key='moderate'
    else:key='severe'
    return max(0,int(rates.get(key,0))) if isinstance(rates,Mapping) else 0


_HEMOSTASIS_FULL_UNITS=3_600_000  # 1000 milli-progress * 3600 seconds/hour
_HEMOSTASIS_BLOOD_DENOMINATOR=120*1000*_HEMOSTASIS_FULL_UNITS

def advance_natural_hemostasis(wounds:Sequence[Mapping[str,Any]],*,elapsed_seconds:int,bleeding_multiplier_milli:int=1000)->dict[str,Any]:
    """Advance bleeding/clotting with chunk-invariant compact current state.

    Progress is stored in exact ``rate * seconds`` units rather than rounded
    milli-progress. Blood loss uses a carried rational remainder, so repeated
    five-minute causal wakes equal one settlement over the same interval. The
    transient fields exist only while a wound is actively bleeding and are
    removed as soon as it clots.
    """
    if elapsed_seconds<0:raise ValueError('elapsed invalid')
    out=[];blood_added=0; multiplier=max(0,int(bleeding_multiplier_milli))
    for raw in wounds:
        if not isinstance(raw,Mapping):continue
        wound=copy.deepcopy(dict(raw)); current=max(0,int(wound.get('bleeding_ml_per_min',0)))
        if current<=0:
            for key in ('hemostasis_progress_milli','hemostasis_progress_units','hemostasis_initial_bleeding_ml_per_min','hemostasis_blood_remainder'):
                wound.pop(key,None)
            out.append(wound);continue
        if elapsed_seconds==0:
            out.append(wound);continue

        initial=max(current,max(1,int(wound.get('hemostasis_initial_bleeding_ml_per_min',current))))
        prior_units=max(0,min(_HEMOSTASIS_FULL_UNITS-1,int(wound.get('hemostasis_progress_units',0))))
        rate=max(0,_hemostasis_rate_milli_per_hour({**wound,'hemostasis_initial_bleeding_ml_per_min':initial}))
        active_seconds=max(0,int(elapsed_seconds))
        if rate>0:
            remaining_units=_HEMOSTASIS_FULL_UNITS-prior_units
            seconds_to_clot=(remaining_units+rate-1)//rate
            active_seconds=min(active_seconds,seconds_to_clot)
            after_units=min(_HEMOSTASIS_FULL_UNITS,prior_units+rate*active_seconds)
        else:
            after_units=prior_units

        # Exact trapezoid integral of the linearly tapering source. Keeping one
        # sub-millilitre numerator makes the sum independent of settlement chunks.
        remainder=max(0,int(wound.get('hemostasis_blood_remainder',0)))
        numerator=remainder + initial*multiplier*active_seconds*(
            2*_HEMOSTASIS_FULL_UNITS-prior_units-after_units
        )
        added,remainder=divmod(numerator,_HEMOSTASIS_BLOOD_DENOMINATOR)
        blood_added+=max(0,int(added))

        if after_units>=_HEMOSTASIS_FULL_UNITS:
            wound['bleeding_ml_per_min']=0
            for key in ('hemostasis_progress_milli','hemostasis_progress_units','hemostasis_initial_bleeding_ml_per_min','hemostasis_blood_remainder'):
                wound.pop(key,None)
        else:
            remaining=_HEMOSTASIS_FULL_UNITS-after_units
            wound['bleeding_ml_per_min']=max(1,(initial*remaining+_HEMOSTASIS_FULL_UNITS-1)//_HEMOSTASIS_FULL_UNITS)
            wound['hemostasis_initial_bleeding_ml_per_min']=initial
            if after_units>0:wound['hemostasis_progress_units']=after_units
            else:wound.pop('hemostasis_progress_units',None)
            if remainder:wound['hemostasis_blood_remainder']=remainder
            else:wound.pop('hemostasis_blood_remainder',None)
            wound.pop('hemostasis_progress_milli',None)
        out.append(wound)
    return {'wounds_after':out,'blood_added_ml':max(0,blood_added)}


def blood_regeneration_ml(*,body_mass_kg:float,elapsed_hours:int,medicine_modifiers:Mapping[str,int]|None=None)->int:
    if elapsed_hours<=0:return 0
    cfg=_data().get('physiology',{}); base=max(0,int(cfg.get('blood_regeneration_ml_per_hour_at_70kg',8))) if isinstance(cfg,Mapping) else 8
    scaled=base*max(10,int(round(float(body_mass_kg))))//70
    mods=medicine_modifiers or {}; rate=max(1000,int(mods.get('blood_regeneration_rate_milli',1000)))
    return max(0,scaled*int(elapsed_hours)*rate//1000)

def acute_physiology_wake_minutes()->int:
    cfg=_data().get('physiology',{})
    return max(1,int(cfg.get('acute_max_wake_minutes',5))) if isinstance(cfg,Mapping) else 5

def dying_deadline_minutes()->int:
    cfg=_data().get('physiology',{})
    return max(1,int(cfg.get('dying_deadline_minutes',5))) if isinstance(cfg,Mapping) else 5

def settle_physiology(*,body_mass_kg:float,wounds:Sequence[Mapping[str,Any]],blood_lost_ml:int,elapsed_seconds:int,endurance:int,willpower:int,medicine_modifiers:Mapping[str,int]|None=None,poison_effects:Mapping[str,int]|None=None,bleeding_rate_multiplier_milli:int|None=None,endurance_penalty:int=0,extra_shock:int=0,consciousness_pressure:int=0)->dict[str,Any]:
    if elapsed_seconds<0:raise ValueError('elapsed invalid')
    tox=dict(poison_effects or {})
    if bleeding_rate_multiplier_milli is not None: tox['bleeding_rate_multiplier_milli']=max(0,int(bleeding_rate_multiplier_milli))
    bleed_multiplier=max(0,int(tox.get('bleeding_rate_multiplier_milli',1000)))
    hemostasis=advance_natural_hemostasis(wounds,elapsed_seconds=elapsed_seconds,bleeding_multiplier_milli=bleed_multiplier); wounds_after=hemostasis['wounds_after']
    volume=blood_volume_ml(body_mass_kg); blood=min(volume,max(0,int(blood_lost_ml)+int(hemostasis['blood_added_ml'])))
    loss_frac=min(1000,blood*1000//volume); mods=medicine_modifiers or {}; pain_reduction=max(0,int(mods.get('pain_severity_reduction',0)))
    pain=sum(max(0,int(w.get('pain',0))) for w in wounds_after); pain=max(0,pain-pain_reduction*max(1,len(wounds_after)))
    organ=sum(max(0,int(w.get('organ_trauma',0))) for w in wounds_after)
    respiratory_pressure=max(0,int(tox.get('respiratory_pressure',0)))
    effective_endurance=max(0,int(endurance)-max(0,int(tox.get('endurance_penalty',endurance_penalty)))-respiratory_pressure//2)
    shock=max(0,pain//3+organ//2+loss_frac//4+max(0,int(tox.get('shock_pressure',0)))+respiratory_pressure+max(0,int(extra_shock))-effective_endurance//4)
    consciousness=max(0,200+max(0,willpower)//2+effective_endurance//3-shock-max(0,int(tox.get('consciousness_pressure',0)))-max(0,int(consciousness_pressure)))
    lethal=lethal_state(wounds=wounds_after,blood_loss_fraction_milli=loss_frac,shock=shock,consciousness=consciousness)
    total_bleed=sum(max(0,int(w.get('bleeding_ml_per_min',0))) for w in wounds_after)
    return {'blood_volume_ml':volume,'blood_lost_ml':blood,'blood_loss_fraction_milli':loss_frac,'bleeding_ml_per_min':total_bleed,'blood_added_ml':int(hemostasis['blood_added_ml']),'pain_total':pain,'shock':shock,'consciousness':consciousness,'lethal_state':lethal,'vision':vision_state(wounds_after),'functional_penalties':functional_penalties(wounds_after),'wounds_after':wounds_after}

def lethal_state(*,wounds:Sequence[Mapping[str,Any]],blood_loss_fraction_milli:int,shock:int,consciousness:int)->str:
    l=_data()['lethality']
    for w in wounds:
        zone=w.get('zone');organ=int(w.get('organ_trauma',0));severity=int(w.get('severity',0));structure=w.get('structure_ref');sd=int(w.get('structure_damage',0))
        if structure=='brain' and sd>=int(_data()['structures']['brain']['destruction_threshold']):return 'dead'
        # Destruction is not a recoverable wound state. Lesser catastrophic
        # thoracic trauma may be medically stabilized, but a destroyed heart
        # cannot become healthy merely because a later monthly recovery tick
        # shrinks the stored trauma numbers.
        if structure=='heart' and sd>=int(_data()['structures']['heart']['destruction_threshold']):return 'dead'
        # Exact neck anatomy outranks the coarse zone label. Major-vessel or
        # cervical cord destruction is immediately catastrophic; an exact airway
        # destruction is a dying state. A generic neck/throat hit remains governed
        # by coarse trauma and may be survivable.
        if structure in {'carotid_artery','jugular_vein'} and sd>=int(l['catastrophic_major_neck_vessel_damage']):return 'dead'
        if structure in {'spinal_cord'} and sd>=int(l['catastrophic_spinal_cord_damage']):return 'dead'
        if structure=='trachea' and sd>=int(l['catastrophic_airway_damage']) and not bool(w.get('stabilized')):return 'dying'
        # ``throat`` is the legacy anterior-neck/airway composite retained for
        # old targeting commands. It is deliberately *not* a synonym for a
        # carotid or jugular hit. Once that exact composite is destroyed,
        # however, leaving the actor merely ``alive`` would recreate the old
        # playtest failure, so treat it as an unstabilized airway emergency.
        if structure=='throat' and sd>=int(_data()['structures']['throat']['destruction_threshold']) and not bool(w.get('stabilized')):return 'dying'
        if structure=='cervical_spine' and sd>=int(_data()['structures']['cervical_spine']['destruction_threshold']) and int(w.get('nerve_damage',0))>=80:return 'dying'
        if zone=='head' and max(organ,severity)>=int(l['catastrophic_brain_trauma']):return 'dead'
        if zone=='neck' and structure not in {'carotid_artery','jugular_vein','trachea','throat','spinal_cord','cervical_spine'} and max(organ,severity)>=int(l['catastrophic_neck_trauma']):return 'dead'
        if zone=='chest' and organ>=int(l['catastrophic_chest_organ_trauma']) and not bool(w.get('stabilized')):return 'dying'
    if blood_loss_fraction_milli>=int(l['fatal_blood_loss_fraction_milli']):return 'dying'
    if blood_loss_fraction_milli>=int(l['critical_blood_loss_fraction_milli']) or shock>=int(l['unconsciousness_shock']):return 'critical'
    if consciousness<=0:return 'unconscious'
    return 'alive'

def recovery_advance(wound:Mapping[str,Any],*,elapsed_hours:int,medicine_modifiers:Mapping[str,int]|None=None)->dict[str,Any]:
    if elapsed_hours<0:raise ValueError('elapsed invalid')
    out=copy.deepcopy(dict(wound))
    if bool(out.get('healed')):
        return _healed_permanent_residual(out) if wound_requires_persistence(out) else _healed_nonpermanent_residual(out)
    mods=medicine_modifiers or {};kind='external_wound_healing_rate_milli'
    if int(out.get('fracture',0))>0:kind='fracture_healing_rate_milli'
    elif int(out.get('organ_trauma',0))>0:kind='soft_tissue_and_organ_healing_rate_milli'
    prior=max(0,min(100000,int(out.get('healing_progress_milli',0))))
    rate=max(700,int(mods.get(kind,1000)));gain=elapsed_hours*rate//24;current=min(100000,prior+gain);out['healing_progress_milli']=current
    if current>=100000:
        return _healed_permanent_residual(out) if wound_requires_persistence(out) else _healed_nonpermanent_residual(out)

    # Acute damage recovers continuously instead of staying at full severity
    # until disappearing on the final tick. Permanent functional loss is the
    # floor and is never regenerated. Scaling by remaining/prior remaining makes
    # repeated recovery calls linear rather than accidentally exponential.
    old_remaining=max(1,100000-prior); new_remaining=max(0,100000-current)
    permanent=out.get('permanent_effects',{}) if isinstance(out.get('permanent_effects'),Mapping) else {}
    for key in ('cut','pierce','blunt','penetration','severity','bleeding_ml_per_min','fracture','tendon_damage','nerve_damage','organ_trauma','structure_damage','pain'):
        out[key]=max(0,int(out.get(key,0))*new_remaining//old_remaining)
    effects=out.get('functional_effects',{}) if isinstance(out.get('functional_effects'),Mapping) else {}
    recovered={}
    for key,val in effects.items():
        floor=max(0,min(100,int(permanent.get(key,0))))
        old=max(floor,max(0,min(100,int(val))))
        recovered[str(key)]=floor+(old-floor)*new_remaining//old_remaining
    out['functional_effects']=recovered
    out['function_loss_pct']=max([0]+list(recovered.values()))
    for _key in ('hemostasis_progress_milli','hemostasis_progress_units','hemostasis_initial_bleeding_ml_per_min','hemostasis_blood_remainder'):
        out.pop(_key,None)
    return out


__all__=['acute_physiology_wake_minutes','dying_deadline_minutes','advance_natural_hemostasis','blood_regeneration_ml','blood_volume_ml','blood_recovery_advance','structure_definition','target_zone','structure_family_members','wound_from_contact','record_current_wound','compact_current_wounds','vision_state','combat_status_families','functional_penalties','functional_capacity_factors','settle_physiology','lethal_state','recovery_advance','wound_requires_persistence']
