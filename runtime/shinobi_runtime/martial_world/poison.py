"""Finite deterministic fictional poison burden mechanics for Jianghu.

The registered poisons are intentionally abstract game substances.  Combat
exposure uses two deterministic stages: a small onset tranche becomes active
quickly, then the remaining burden activates at the registered peak time.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

_ROOT=Path(__file__).resolve().parents[3]
_MW=_ROOT/'game/data/martial-world'


@lru_cache(maxsize=1)
def _data()->dict[str,Any]:
    return json.loads((_MW/'poisons.json').read_text(encoding='utf-8'))


def _row(poison_ref:str)->Mapping[str,Any]:
    row=_data().get('poisons',{}).get(poison_ref)
    if not isinstance(row,Mapping):
        raise KeyError(poison_ref)
    return row


def poison_item_ref(poison_ref:str)->str:
    _row(poison_ref)
    return f'poison_{poison_ref}'


def resistance_score(*,endurance:int,qi:int,qi_control:int)->int:
    return max(0,int(endurance))*4+max(0,int(qi))*3+max(0,int(qi_control))*5


def exposure_pressure(*,poison_ref:str,doses:int)->int:
    row=_row(poison_ref)
    if doses<=0: raise ValueError('dose invalid')
    return max(1,int(row.get('potency',0))*10+max(1,int(doses))*180)


def poison_onset_seconds(poison_ref:str)->int:
    row=_row(poison_ref)
    if 'onset_seconds' in row:
        return max(0,int(row.get('onset_seconds',0)))
    return max(0,int(row.get('onset_minutes',0)))*60


def poison_peak_seconds(poison_ref:str)->int:
    row=_row(poison_ref)
    onset=poison_onset_seconds(poison_ref)
    return max(onset,int(row.get('peak_seconds',onset)))


def poison_onset_fraction_milli(poison_ref:str)->int:
    row=_row(poison_ref)
    return max(0,min(1000,int(row.get('onset_fraction_milli',300))))


def poison_onset_minutes(poison_ref:str)->int:
    """Compatibility projection for older callers.

    New combat code uses exact seconds.  This returns a conservative whole
    minute ceiling so legacy scheduler/test code never activates earlier than
    the registered onset merely because it still speaks minutes.
    """
    seconds=poison_onset_seconds(poison_ref)
    return (seconds+59)//60 if seconds else 0


def apply_poison(*,poison_ref:str,current_burden:int,doses:int,endurance:int,qi:int,qi_control:int)->dict[str,Any]:
    row=_row(poison_ref)
    if doses<=0: raise ValueError('dose invalid')
    raw=max(0,int(row.get('burden_per_dose',0)))*int(doses)
    resist=resistance_score(endurance=endurance,qi=qi,qi_control=qi_control)
    pressure=exposure_pressure(poison_ref=poison_ref,doses=doses)
    excess=max(0,pressure-resist)
    added=0 if excess<=0 else max(1,raw*excess//pressure)
    before=max(0,int(current_burden))
    return {
        'poison_ref':poison_ref,'burden_before':before,'burden_after':before+added,'burden_added':added,
        'exposure_rejected':added==0,'resistance_score':resist,'exposure_pressure':pressure,
        'onset_seconds':poison_onset_seconds(poison_ref),'peak_seconds':poison_peak_seconds(poison_ref),
        'onset_fraction_milli':poison_onset_fraction_milli(poison_ref),
    }


def poison_clearance_per_hour(poison_ref:str,*,medicine_multiplier_milli:int=1000)->int:
    _row(poison_ref)
    base=max(0,int(_data().get('natural_clearance_per_hour',0)))
    return max(0,base*max(0,int(medicine_multiplier_milli))//1000)


def poison_effects_for_burdens(burdens:Mapping[str,Any]|None)->dict[str,int]:
    out:dict[str,int]={}
    rows=_data().get('poisons',{})
    for ref,raw_burden in (burdens or {}).items():
        row=rows.get(str(ref))
        if not isinstance(row,Mapping): continue
        burden=max(0,int(raw_burden))
        if burden <= 0: continue
        for key,val in (row.get('effects_per_100_burden',{}) or {}).items():
            if isinstance(val,int) and not isinstance(val,bool):
                effect_key=str(key)
                if effect_key.endswith('_multiplier_milli'):
                    # Multipliers are authored around the neutral 1000 baseline.
                    # Scale only their delta from neutral; scaling the whole value
                    # from zero would make a partial harmful burden beneficial
                    # (for example, 30 anticoagulant burden turned 1500 into 450).
                    delta=(int(val)-1000)*burden//100
                    out[effect_key]=max(0,out.get(effect_key,1000)+delta)
                else:
                    out[effect_key]=out.get(effect_key,0)+int(val)*burden//100
    return out


def current_poison_effects(burdens:Mapping[str,Any]|None)->dict[str,int]:
    return poison_effects_for_burdens(burdens)


def _pending_poison_ref(key:str,row:Mapping[str,Any])->str:
    explicit=row.get('poison_ref')
    if isinstance(explicit,str) and explicit:
        return explicit
    # Backward compatibility for the legacy one-row-per-poison shape.
    return str(key).split('#',1)[0]


def _normalize_pending_row(raw:Mapping[str,Any],*,storage_key:str|None=None)->dict[str,Any]|None:
    burden=max(0,int(raw.get('burden',0)))
    when=raw.get('activates_at',raw.get('due_at'))
    if burden<=0 or not isinstance(when,str):
        return None
    poison_ref=_pending_poison_ref(str(storage_key or ''),raw)
    if not poison_ref:
        return None
    # Validate the registered substance at normalization time so corrupt dynamic
    # storage keys cannot create unresolvable physiology work later.
    _row(poison_ref)
    out={
        'poison_ref':poison_ref,
        'burden':burden,
        'activates_at':datetime.fromisoformat(when.removeprefix('SE-')).isoformat(),
    }
    peaks=raw.get('peaks_at')
    if isinstance(peaks,str):
        out['peaks_at']=datetime.fromisoformat(peaks.removeprefix('SE-')).isoformat()
    stage=raw.get('stage')
    if stage in {'onset','peak'}:
        out['stage']=str(stage)
    return out


def _normalized_pending(pending:Mapping[str,Any]|None)->dict[str,dict[str,Any]]:
    out:dict[str,dict[str,Any]]={}
    for key,value in (pending or {}).items():
        if not isinstance(value,Mapping):
            continue
        try:
            normalized=_normalize_pending_row(value,storage_key=str(key))
        except (KeyError,ValueError):
            continue
        if normalized is not None:
            out[str(key)]=normalized
    return out


def _new_pending_key(out:Mapping[str,Any],poison_ref:str)->str:
    if poison_ref not in out:
        return poison_ref
    ordinal=2
    while f'{poison_ref}#{ordinal}' in out:
        ordinal+=1
    return f'{poison_ref}#{ordinal}'


def _pending_burden_for(out:Mapping[str,Any],poison_ref:str)->int:
    total=0
    for key,row in out.items():
        if not isinstance(row,Mapping):
            continue
        if _pending_poison_ref(str(key),row)==poison_ref:
            total+=max(0,int(row.get('burden',0)))
    return total


def pending_poison_burden(pending:Mapping[str,Any]|None,poison_ref:str)->int:
    _row(poison_ref)
    return _pending_burden_for(_normalized_pending(pending),poison_ref)


def combined_poison_burdens(active:Mapping[str,Any]|None,pending:Mapping[str,Any]|None)->dict[str,int]:
    """Return conserved active + pending burden by registered poison."""
    totals={str(k):max(0,int(v)) for k,v in (active or {}).items() if max(0,int(v))>0}
    for key,row in _normalized_pending(pending).items():
        poison_ref=_pending_poison_ref(str(key),row)
        totals[poison_ref]=totals.get(poison_ref,0)+max(0,int(row.get('burden',0)))
    return {ref:burden for ref,burden in totals.items() if burden>0}


def clear_poison_burden(
    *,active:Mapping[str,Any]|None,pending:Mapping[str,Any]|None,poison_ref:str,amount:int,
)->dict[str,Any]:
    """Clear conserved poison burden without collapsing independent onset clocks.

    Treatment relieves already-active burden first because that is the burden
    currently producing physiology effects. Any remaining clearance then removes
    the earliest still-pending tranches while preserving every surviving row's
    registered activation/peak metadata.
    """
    _row(poison_ref)
    active_after={str(k):max(0,int(v)) for k,v in (active or {}).items() if max(0,int(v))>0}
    pending_after=_normalized_pending(pending)
    requested=max(0,int(amount)); remaining=requested
    active_before=max(0,int(active_after.get(poison_ref,0)))
    take=min(active_before,remaining)
    if take:
        left=active_before-take
        if left: active_after[poison_ref]=left
        else: active_after.pop(poison_ref,None)
        remaining-=take
    if remaining:
        matching=[
            key for key,row in pending_after.items()
            if _pending_poison_ref(str(key),row)==poison_ref
        ]
        matching.sort(key=lambda key:(str(pending_after[key].get('activates_at','')),str(key)))
        for key in matching:
            if remaining<=0: break
            row=pending_after.get(key)
            if not isinstance(row,dict): continue
            burden=max(0,int(row.get('burden',0)))
            take=min(burden,remaining)
            left=burden-take
            remaining-=take
            if left: row['burden']=left
            else: pending_after.pop(key,None)
    cleared=requested-remaining
    return {
        'poison_ref':poison_ref,'requested_clearance':requested,'burden_cleared':cleared,
        'active_after':active_after,'pending_after':pending_after,
        'active_burden_after':max(0,int(active_after.get(poison_ref,0))),
        'pending_burden_after':_pending_burden_for(pending_after,poison_ref),
    }


def add_pending_poison_exposure(
    pending:Mapping[str,Any]|None,*,poison_ref:str,burden_added:int,activates_at:str,
    peaks_at:str|None=None,stage:str|None=None,
)->dict[str,Any]:
    """Add one delayed poison tranche without losing overlapping exposure clocks.

    Pending poison state is not history: activated rows disappear.  Multiple
    still-pending exposures of the same poison remain separate only when their
    activation/peak clocks differ.  Identical stage clocks compact by summing
    burden, which keeps rapid same-second volleys bounded without pulling a
    later dose forward or postponing an earlier one.
    """
    _row(poison_ref)
    due=datetime.fromisoformat(str(activates_at).removeprefix('SE-'))
    peak=datetime.fromisoformat(str(peaks_at).removeprefix('SE-')) if isinstance(peaks_at,str) else None
    if peak is not None and peak<due:
        peak=due
    normalized_stage=str(stage) if stage in {'onset','peak'} else None
    out=_normalized_pending(pending)
    added=max(0,int(burden_added))
    if added<=0:
        return out
    due_iso=due.isoformat(); peak_iso=peak.isoformat() if peak is not None else None
    # Merge only truly identical pending clocks. This is compaction, not a
    # chronology approximation.
    for key,row in out.items():
        if _pending_poison_ref(key,row)!=poison_ref:
            continue
        if str(row.get('activates_at'))!=due_iso:
            continue
        if (str(row.get('peaks_at')) if isinstance(row.get('peaks_at'),str) else None)!=peak_iso:
            continue
        if (str(row.get('stage')) if row.get('stage') in {'onset','peak'} else None)!=normalized_stage:
            continue
        row['burden']=max(0,int(row.get('burden',0)))+added
        return out
    incoming={'poison_ref':poison_ref,'burden':added,'activates_at':due_iso}
    if peak_iso is not None:
        incoming['peaks_at']=peak_iso
    if normalized_stage is not None:
        incoming['stage']=normalized_stage
    out[_new_pending_key(out,poison_ref)]=incoming
    return out


def queue_progressive_poison_exposure(
    *,pending_burdens:Mapping[str,Any]|None,poison_ref:str,burden_added:int,exposed_at:str,
)->dict[str,Any]:
    exposed=datetime.fromisoformat(str(exposed_at).removeprefix('SE-'))
    onset_at=exposed+timedelta(seconds=poison_onset_seconds(poison_ref))
    peak_at=exposed+timedelta(seconds=poison_peak_seconds(poison_ref))
    out=add_pending_poison_exposure(
        pending_burdens,poison_ref=poison_ref,burden_added=burden_added,
        activates_at=onset_at.isoformat(),peaks_at=peak_at.isoformat(),stage='onset',
    )
    return {
        'pending_after':out,
        'due_at':onset_at.isoformat(),
        'peaks_at':peak_at.isoformat(),
        'pending_burden_after':_pending_burden_for(out,poison_ref),
    }


def queue_poison_onset(*,pending_burdens:Mapping[str,Any]|None,poison_ref:str,burden_added:int,exposed_at:str,onset_minutes:int)->dict[str,Any]:
    """Legacy single-stage queue retained for compatibility."""
    exposed=datetime.fromisoformat(str(exposed_at).removeprefix('SE-'))
    due=(exposed+timedelta(minutes=max(0,int(onset_minutes)))).isoformat()
    out=add_pending_poison_exposure(pending_burdens,poison_ref=poison_ref,burden_added=burden_added,activates_at=due)
    return {'pending_after':out,'due_at':due,'pending_burden_after':_pending_burden_for(out,poison_ref)}


def activate_due_poison_exposures(*,active:Mapping[str,Any]|None,pending:Mapping[str,Any]|None,at:str)->dict[str,Any]:
    now=datetime.fromisoformat(str(at).removeprefix('SE-'))
    active_after={str(k):max(0,int(v)) for k,v in (active or {}).items() if max(0,int(v))>0}
    pending_after:dict[str,Any]={}; activated:dict[str,int]={}
    for storage_key,raw_row in (pending or {}).items():
        if not isinstance(raw_row,Mapping):
            continue
        try:
            row=_normalize_pending_row(raw_row,storage_key=str(storage_key))
        except (KeyError,ValueError):
            continue
        if row is None:
            continue
        poison_ref=str(row['poison_ref'])
        burden=max(0,int(row['burden'])); due=datetime.fromisoformat(str(row['activates_at']))
        if due>now:
            pending_after[str(storage_key)]=row
            continue
        stage=str(row.get('stage') or '')
        peak_raw=row.get('peaks_at')
        peak=datetime.fromisoformat(str(peak_raw)) if isinstance(peak_raw,str) else None
        if stage=='onset' and peak is not None and peak>now:
            fraction=poison_onset_fraction_milli(poison_ref)
            tranche=max(1,burden*fraction//1000) if fraction>0 else 0
            tranche=min(burden,tranche)
            if tranche>0:
                active_after[poison_ref]=active_after.get(poison_ref,0)+tranche
                activated[poison_ref]=activated.get(poison_ref,0)+tranche
            remainder=burden-tranche
            if remainder>0:
                pending_after[str(storage_key)]={
                    'poison_ref':poison_ref,'burden':remainder,'activates_at':peak.isoformat(),
                    'peaks_at':peak.isoformat(),'stage':'peak',
                }
            continue
        active_after[poison_ref]=active_after.get(poison_ref,0)+burden
        activated[poison_ref]=activated.get(poison_ref,0)+burden
    return {'active_after':active_after,'pending_after':pending_after,'activated':activated}


def activate_poison_onset(*,active_burdens:Mapping[str,Any]|None,pending_burdens:Mapping[str,Any]|None,poison_ref:str,at:str)->dict[str,Any]:
    result=activate_due_poison_exposures(active=active_burdens,pending=pending_burdens,at=at)
    activated=max(0,int(result.get('activated',{}).get(poison_ref,0)))
    return {'activated':activated,'active_after':result['active_after'],'pending_after':result['pending_after']}

def settle_poison(*,poison_ref:str,burden:int,elapsed_hours:int)->dict[str,Any]:
    row=_row(poison_ref)
    after=max(0,int(burden)-poison_clearance_per_hour(poison_ref)*max(0,int(elapsed_hours)))
    effects={str(k):int(v)*after//100 for k,v in (row.get('effects_per_100_burden',{}) or {}).items() if isinstance(v,int) and not isinstance(v,bool)}
    return {'burden_after':after,'effects':effects}


def active_qi_purge(*,poison_ref:str,burden:int,current_qi:int|None=None,current_qi_milli:int|None=None,qi:int,qi_control:int,elapsed_minutes:int)->dict[str,Any]:
    _row(poison_ref)
    before=max(0,int(burden)); capacity=max(0,int(qi))*1000
    current_milli=max(0,int(current_qi_milli)) if current_qi_milli is not None else max(0,int(current_qi if current_qi is not None else qi))*1000
    current_milli=min(capacity,current_milli); minutes=max(0,int(elapsed_minutes))
    if before<=0 or current_milli<=0 or minutes<=0:
        return {'poison_ref':poison_ref,'burden_before':before,'burden_after':before,'burden_cleared':0,
                'current_qi_milli_before':current_milli,'current_qi_milli_after':current_milli,
                'current_qi_before':current_milli//1000,'current_qi_after':current_milli//1000,
                'qi_spent_milli':0,'qi_spent':0,'elapsed_minutes':minutes}
    cfg=_data().get('active_qi_purge',{}); base_cost=max(1,int(cfg.get('qi_cost_per_burden',4)))
    efficiency=max(350,min(1800,500+max(0,int(qi_control))*6))
    cost=max(500,base_cost*1000*1000//efficiency)
    throughput=max(1,max(0,int(qi_control))//6+max(0,int(qi))//30)
    cleared=min(before,max(0,throughput*minutes//60),max(0,current_milli//cost))
    spent=min(current_milli,cleared*cost); after=max(0,current_milli-spent)
    return {'poison_ref':poison_ref,'burden_before':before,'burden_after':before-cleared,'burden_cleared':cleared,
            'current_qi_milli_before':current_milli,'current_qi_milli_after':after,
            'current_qi_before':current_milli//1000,'current_qi_after':after//1000,
            'qi_spent_milli':spent,'qi_spent':(spent+999)//1000 if spent else 0,
            'control_efficiency_milli':efficiency,'elapsed_minutes':minutes}


__all__=[
    'activate_due_poison_exposures','activate_poison_onset','active_qi_purge','add_pending_poison_exposure','clear_poison_burden','combined_poison_burdens',
    'apply_poison','current_poison_effects','exposure_pressure','poison_clearance_per_hour','poison_effects_for_burdens',
    'poison_item_ref','pending_poison_burden','poison_onset_fraction_milli','poison_onset_minutes','poison_onset_seconds','poison_peak_seconds',
    'queue_poison_onset','queue_progressive_poison_exposure','resistance_score','settle_poison',
]
