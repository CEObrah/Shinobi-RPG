"""Deterministic Jianghu medicine production/use state.

Medicine operates through physiology and recovery only. It never restores Qi,
grants Qi training credit, raises Maximum Qi, or substitutes for cultivation.
"""
from __future__ import annotations
import copy, json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'
def _data(): return json.loads((_MW/'medicine.json').read_text(encoding='utf-8'))
def _at(value:str|datetime)->datetime:
    if isinstance(value,datetime): return value
    if not isinstance(value,str): raise ValueError('timestamp invalid')
    return datetime.fromisoformat(value)
def blank_medicine_state(at:str|datetime)->dict[str,Any]:
    now=_at(at); return {'last_settled_at':now.isoformat(),'category_saturation_milli':{},'toxicity_milli':0,'active_effects':[]}
def settle_medicine_state(state:Mapping[str,Any]|None,*,at:str|datetime)->dict[str,Any]:
    now=_at(at); current=copy.deepcopy(dict(state or blank_medicine_state(now))); last=_at(current.get('last_settled_at',now.isoformat()))
    if now<last: raise ValueError('medicine settlement before last settlement')
    minutes=int((now-last).total_seconds()//60); cfg=_data()['saturation']
    sat_decay=int(cfg.get('decay_per_hour_milli',0))*minutes//60; tox_decay=int(cfg.get('toxicity_decay_per_hour_milli',0))*minutes//60
    sats=current.setdefault('category_saturation_milli',{})
    for key in list(sats):
        sats[key]=max(0,int(sats[key])-sat_decay)
        if sats[key]==0: sats.pop(key,None)
    current['toxicity_milli']=max(0,int(current.get('toxicity_milli',0))-tox_decay)
    current['active_effects']=[dict(e) for e in current.get('active_effects',[]) if isinstance(e,Mapping) and isinstance(e.get('expires_at'),str) and _at(e['expires_at'])>now]
    current['last_settled_at']=now.isoformat(); return current
def _scaled_effect(recipe:Mapping[str,Any],saturation_milli:int)->tuple[int,dict[str,Any]]:
    multiplier=max(0,100000-int(saturation_milli))//100
    out={}
    for key,value in recipe.get('effect',{}).items():
        out[key]=value if key=='duration_hours' else (int(value)*multiplier//1000 if isinstance(value,int) and not isinstance(value,bool) else value)
    return multiplier,out
def administer_dose(recipe_ref:str,*,at:str|datetime,inventory:Mapping[str,int],person_state:Mapping[str,Any]|None)->dict[str,Any]:
    data=_data(); recipe=data['recipes'].get(recipe_ref)
    if not isinstance(recipe,Mapping): raise KeyError(recipe_ref)
    effect_keys=set(recipe.get('effect',{}))
    forbidden={'current_qi_restore','qi_training_equivalent_hours','max_qi_gain','qi_gain','fatigue_recovery_points'}
    if effect_keys & forbidden: raise ValueError('power-consumable medicine forbidden')
    stock={str(k):int(v) for k,v in inventory.items()}
    if stock.get(recipe_ref,0)<=0: raise ValueError('medicine dose unavailable')
    stock[recipe_ref]-=1
    state=settle_medicine_state(person_state,at=at); category=str(recipe['category']); before=int(state['category_saturation_milli'].get(category,0)); multiplier,effect=_scaled_effect(recipe,before)
    gain=int(data['saturation'].get('dose_saturation_gain',40))*1000; raw=before+gain; excess=max(0,raw-100000)
    state['category_saturation_milli'][category]=min(100000,raw); state['toxicity_milli']=int(state.get('toxicity_milli',0))+excess
    duration=effect.get('duration_hours'); timed={k:v for k,v in effect.items() if k!='duration_hours'}
    if timed:
        if not isinstance(duration,int) or duration<=0: raise ValueError('medicine effect requires duration')
        now=_at(at); state['active_effects'].append({'recipe_ref':recipe_ref,'category':category,'started_at':now.isoformat(),'expires_at':(now+timedelta(hours=duration)).isoformat(),'modifiers':timed})
    tox=int(state['toxicity_milli']); cfg=data['saturation']; severe=int(cfg.get('severe_toxicity_threshold',150))*1000; unsafe=int(cfg.get('maximum_safe_toxicity',100))*1000
    status='severe' if tox>=severe else ('unsafe' if tox>=unsafe else ('elevated' if tox>0 else 'none'))
    return {'recipe_ref':recipe_ref,'inventory_after':stock,'medicine_state_after':state,'effect_multiplier_milli':multiplier,'applied_effect':effect,'toxicity_status':status}
def toxicity_consequences(state:Mapping[str,Any]|None,*,at:str|datetime)->dict[str,int]:
    settled=settle_medicine_state(state,at=at); tox=max(0,int(settled.get('toxicity_milli',0))); cfg=_data()['saturation'].get('toxicity_consequence_formula',{}); units=tox//1000
    fatigue=units*int(cfg.get('fatigue_burden_per_1000_toxicity_milli',0)); coordination=min(int(cfg.get('maximum_coordination_penalty_milli',1000)),units*int(cfg.get('coordination_penalty_milli_per_1000_toxicity_milli',0)))
    shock=max(0,(tox-int(cfg.get('shock_begins_at_toxicity_milli',10**18)))//1000)*int(cfg.get('shock_per_1000_excess_toxicity_milli',0)); organ=max(0,(tox-int(cfg.get('organ_stress_begins_at_toxicity_milli',10**18)))//1000)*int(cfg.get('organ_stress_per_1000_excess_toxicity_milli',0))
    return {'toxicity_milli':tox,'fatigue_burden_points':fatigue,'coordination_penalty_milli':coordination,'shock_contribution':shock,'organ_stress':organ}
def toxicity_consequences_current(state:Mapping[str,Any]|None)->dict[str,int]:
    """Read toxicity consequences from already-settled current medicine state."""
    current=dict(state or {})
    tox=max(0,int(current.get('toxicity_milli',0)))
    cfg=_data()['saturation'].get('toxicity_consequence_formula',{})
    units=tox//1000
    fatigue=units*int(cfg.get('fatigue_burden_per_1000_toxicity_milli',0))
    coordination=min(int(cfg.get('maximum_coordination_penalty_milli',1000)),units*int(cfg.get('coordination_penalty_milli_per_1000_toxicity_milli',0)))
    shock=max(0,(tox-int(cfg.get('shock_begins_at_toxicity_milli',10**18)))//1000)*int(cfg.get('shock_per_1000_excess_toxicity_milli',0))
    organ=max(0,(tox-int(cfg.get('organ_stress_begins_at_toxicity_milli',10**18)))//1000)*int(cfg.get('organ_stress_per_1000_excess_toxicity_milli',0))
    return {'toxicity_milli':tox,'fatigue_burden_points':fatigue,'coordination_penalty_milli':coordination,'shock_contribution':shock,'organ_stress':organ}

def active_recovery_modifiers(state:Mapping[str,Any]|None,*,at:str|datetime)->dict[str,int]:
    settled=settle_medicine_state(state,at=at); combined={}
    for effect in settled['active_effects']:
        for key,value in effect.get('modifiers',{}).items():
            if isinstance(value,int) and not isinstance(value,bool): combined[key]=max(combined.get(key,0),value)
    return combined


def _weighted_score(parts: tuple[tuple[int, int], ...]) -> int:
    return max(0, sum(max(0, int(value)) * int(weight) for value, weight in parts) // 100)


def diagnosis_score(
    *, medicine: int, intelligence: int, perception: int,
    symptoms_milli: int = 1000, examination_minutes: int = 10,
    tool_available: bool = False, environment_milli: int = 1000,
    patient_access_milli: int = 1000,
) -> dict[str, int]:
    """Deterministic diagnostic specificity from actual observer capability."""
    base = _weighted_score(((medicine, 60), (intelligence, 25), (perception, 15)))
    time_milli = min(1250, 700 + max(0, int(examination_minutes)) * 25)
    tool_milli = 1100 if tool_available else 925
    modifier = max(200, int(symptoms_milli)) * time_milli // 1000
    modifier = modifier * tool_milli // 1000
    modifier = modifier * max(250, int(environment_milli)) // 1000
    modifier = modifier * max(250, int(patient_access_milli)) // 1000
    score = base * modifier // 1000
    specificity = 4 if score >= 105 else 3 if score >= 80 else 2 if score >= 55 else 1 if score >= 30 else 0
    return {"base_score": base, "diagnosis_score": score, "specificity_level": specificity}


def wound_treatment_score(
    *, medicine: int, dexterity: int, intelligence: int, perception: int,
    physician_kit: bool, medical_supply: bool, environment_milli: int = 1000,
    treatment_minutes: int = 30, patient_condition_milli: int = 1000,
) -> dict[str, int | bool]:
    """Deterministic stabilization skill. Tools enable, never add flat Medicine."""
    base = _weighted_score(((medicine, 55), (dexterity, 20), (intelligence, 15), (perception, 10)))
    time_milli = min(1250, 650 + max(0, int(treatment_minutes)) * 15)
    supply_milli = 1000 if medical_supply else 650
    modifier = time_milli * max(250, int(environment_milli)) // 1000
    modifier = modifier * max(250, int(patient_condition_milli)) // 1000
    modifier = modifier * supply_milli // 1000
    score = base * modifier // 1000
    return {
        "base_score": base,
        "treatment_score": score,
        "advanced_procedure_enabled": bool(physician_kit),
        "medical_supply_available": bool(medical_supply),
    }


def antidote_affinity_milli(medicine_ref: str, poison_ref: str) -> int:
    recipe = _data().get("recipes", {}).get(medicine_ref)
    if not isinstance(recipe, Mapping):
        raise KeyError(medicine_ref)
    affinity = recipe.get("toxin_affinity", {})
    if not isinstance(affinity, Mapping):
        return 0
    return max(0, int(affinity.get(poison_ref, 0)))


def poison_treatment_score(
    *, medicine: int, intelligence: int, perception: int, poison_ref: str,
    medicine_ref: str, burden: int, patient_endurance: int, patient_qi: int,
    patient_qi_control: int, facility_level: int, treatment_minutes: int = 20,
) -> dict[str, int]:
    """Deterministic supportive toxin treatment; one dose remains one dose."""
    base = _weighted_score(((medicine, 65), (intelligence, 25), (perception, 10)))
    affinity = antidote_affinity_milli(medicine_ref, poison_ref)
    facility_milli = 850 + max(0, min(5, int(facility_level))) * 50
    time_milli = min(1250, 700 + max(0, int(treatment_minutes)) * 20)
    patient_resistance = max(0, int(patient_endurance)) * 4 + max(0, int(patient_qi)) * 3 + max(0, int(patient_qi_control)) * 5
    patient_milli = min(1350, 850 + patient_resistance // 8)
    burden_milli = max(450, 1150 - max(0, int(burden)) * 3)
    score = base * max(0, affinity) // 1000
    score = score * facility_milli // 1000
    score = score * time_milli // 1000
    score = score * patient_milli // 1000
    score = score * burden_milli // 1000
    return {
        "base_score": base, "poison_treatment_score": score,
        "antidote_affinity_milli": affinity, "patient_resistance_score": patient_resistance,
    }


def treat_poison_burden(
    *, burden: int, medicine: int, intelligence: int, perception: int,
    poison_ref: str, medicine_ref: str, patient_endurance: int, patient_qi: int,
    patient_qi_control: int, facility_level: int, treatment_minutes: int = 20,
) -> dict[str, int]:
    before = max(0, int(burden))
    score = poison_treatment_score(
        medicine=medicine, intelligence=intelligence, perception=perception,
        poison_ref=poison_ref, medicine_ref=medicine_ref, burden=before,
        patient_endurance=patient_endurance, patient_qi=patient_qi,
        patient_qi_control=patient_qi_control, facility_level=facility_level,
        treatment_minutes=treatment_minutes,
    )
    # Strong treatment can clear substantial burden, but never more than exists.
    cleared = min(before, max(0, int(score["poison_treatment_score"]) // 4))
    return {**score, "burden_before": before, "burden_cleared": cleared, "burden_after": before - cleared}


def stabilize_wounds(
    health: Mapping[str, Any] | None, *, treatment_score_value: int,
    advanced_procedure_enabled: bool, medical_supply_available: bool,
) -> dict[str, Any]:
    """Stabilize current injuries without regenerating destroyed anatomy."""
    out = copy.deepcopy(dict(health or {}))
    injuries = out.get("injuries", [])
    if not isinstance(injuries, list):
        return out
    score = max(0, int(treatment_score_value))
    for raw in injuries:
        if not isinstance(raw, dict):
            continue
        bleeding = max(0, int(raw.get("bleeding_ml_per_min", 0)))
        if bleeding > 0 and medical_supply_available:
            reduction_milli = min(900, 250 + score * 5)
            raw["bleeding_ml_per_min"] = max(0, bleeding * (1000 - reduction_milli) // 1000)
        if advanced_procedure_enabled and score >= 70 and (
            int(raw.get("fracture", 0)) > 0 or int(raw.get("tendon_damage", 0)) > 0
        ):
            raw["stabilized"] = True
        # Severe internal trauma needs a materially stronger intervention than
        # ordinary wound dressing.  When that intervention succeeds the wound
        # remains severe and incapacitating, but it no longer counts as
        # untreated catastrophic chest trauma for lethal-state settlement.
        if advanced_procedure_enabled and medical_supply_available and score >= 85 and int(raw.get("organ_trauma", 0)) > 0:
            raw["stabilized"] = True
        raw["treated"] = True
    out["injuries"] = injuries
    return out


__all__ = [
    "active_recovery_modifiers", "administer_dose", "antidote_affinity_milli",
    "blank_medicine_state", "diagnosis_score", "medicine_category", "poison_treatment_score",
    "settle_medicine_state", "stabilize_wounds", "toxicity_consequences", "toxicity_consequences_current",
    "treat_poison_burden", "wound_treatment_score",
]


def medicine_category(recipe_ref: str) -> str:
    recipe = _data().get("recipes", {}).get(recipe_ref)
    if not isinstance(recipe, Mapping):
        raise KeyError(recipe_ref)
    return str(recipe.get("category", ""))
