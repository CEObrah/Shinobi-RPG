"""Sparse exact-person physiology settlement and one-wake ownership."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from .health import (
    acute_physiology_wake_minutes, blood_regeneration_ml, compact_current_wounds,
    dying_deadline_minutes, recovery_advance, settle_physiology, wound_requires_persistence,
)
from .medicine import active_recovery_modifiers, settle_medicine_state, toxicity_consequences_current
from .poison import activate_due_poison_exposures, current_poison_effects, poison_clearance_per_hour


def _dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime): return value
    if not isinstance(value, str): raise ValueError("physiology timestamp invalid")
    return datetime.fromisoformat(value.removeprefix("SE-"))


def _active_wounds(person: Mapping[str, Any]) -> list[dict[str, Any]]:
    health=person.get("health",{}) if isinstance(person.get("health"),Mapping) else {}
    rows=health.get("injuries",[]) if isinstance(health.get("injuries"),list) else []
    return [copy.deepcopy(dict(row)) for row in rows if isinstance(row,Mapping)]


def physiology_needed(person: Mapping[str, Any]) -> bool:
    health=person.get("health",{}) if isinstance(person.get("health"),Mapping) else {}
    if health.get("status")=="dead": return False
    if health.get("injuries") or max(0,int(health.get("blood_lost_ml",0)))>0 or isinstance(health.get("dying_since"),str): return True
    if any(max(0,int(v))>0 for v in (person.get("poison_burdens",{}) or {}).values()): return True
    pending=person.get("pending_poison_burdens",{})
    if isinstance(pending,Mapping) and any(isinstance(v,Mapping) and max(0,int(v.get("burden",0)))>0 for v in pending.values()): return True
    medicine=person.get("medicine_state")
    return isinstance(medicine,Mapping) and (max(0,int(medicine.get("toxicity_milli",0)))>0 or bool(medicine.get("active_effects")) or bool(medicine.get("category_saturation_milli")))


def _medicine_boundary(person: Mapping[str, Any], now: datetime) -> datetime | None:
    medicine=person.get("medicine_state")
    if not isinstance(medicine,Mapping): return None
    candidates=[]
    for row in medicine.get("active_effects",[]) if isinstance(medicine.get("active_effects"),list) else []:
        if isinstance(row,Mapping) and isinstance(row.get("expires_at"),str):
            try: when=_dt(str(row["expires_at"]))
            except ValueError: continue
            if when>now: candidates.append(when)
    if max(0,int(medicine.get("toxicity_milli",0)))>0 or medicine.get("category_saturation_milli"):
        candidates.append(now+timedelta(hours=1))
    return min(candidates) if candidates else None


def next_physiology_event(person_ref: str, person: Mapping[str, Any], *, now: str | datetime,
    recovery_carry_minutes: int=0, poison_clearance_carry_minutes: int=0) -> dict[str, Any] | None:
    if not physiology_needed(person): return None
    at=_dt(now); health=person.get("health",{}) if isinstance(person.get("health"),Mapping) else {}; candidates=[]
    dying=health.get("dying_since")
    if isinstance(dying,str): candidates.append(_dt(dying)+timedelta(minutes=dying_deadline_minutes()))
    wounds=_active_wounds(person)
    if any(max(0,int(w.get("bleeding_ml_per_min",0)))>0 for w in wounds):
        candidates.append(at+timedelta(minutes=acute_physiology_wake_minutes()))
    if wounds or max(0,int(health.get("blood_lost_ml",0)))>0: candidates.append(at+timedelta(hours=24))
    pending=person.get("pending_poison_burdens",{})
    if isinstance(pending,Mapping):
        for row in pending.values():
            if isinstance(row,Mapping) and isinstance(row.get("activates_at"),str): candidates.append(max(at,_dt(row["activates_at"])))
    active=person.get("poison_burdens",{})
    if isinstance(active,Mapping) and any(max(0,int(v))>0 for v in active.values()):
        candidates.append(at+timedelta(minutes=max(1,60-max(0,int(poison_clearance_carry_minutes))%60)))
    boundary=_medicine_boundary(person,at)
    if boundary is not None: candidates.append(boundary)
    if not candidates: return None
    due=max(at,min(candidates))
    return {"event_id":f"person_physiology_due:{person_ref}","kind":"person_physiology_due","due_at":due.isoformat(),"owner_ref":person_ref,
            "last_settled_at":at.isoformat(),"recovery_carry_minutes":max(0,int(recovery_carry_minutes))%60,
            "poison_clearance_carry_minutes":max(0,int(poison_clearance_carry_minutes))%60,"requires_player_decision":False}


def _merged_effects(burdens: Mapping[str, Any], medicine: Mapping[str, Any] | None) -> dict[str,int]:
    effects={str(k):int(v) for k,v in current_poison_effects(burdens).items()}
    tox=toxicity_consequences_current(medicine)
    if int(tox.get("shock_contribution",0))>0: effects["shock_pressure"]=effects.get("shock_pressure",0)+int(tox["shock_contribution"])
    return effects


def settle_person_physiology_event(person: Mapping[str, Any], event: Mapping[str, Any], *, at: str | datetime) -> dict[str, Any]:
    now=_dt(at); last_raw=event.get("last_settled_at")
    if not isinstance(last_raw,str): raise ValueError("physiology event missing last_settled_at")
    last=_dt(last_raw)
    if now<last: raise ValueError("physiology settlement before last settlement")
    seconds=int((now-last).total_seconds()); minutes=seconds//60
    out=copy.deepcopy(dict(person)); health=copy.deepcopy(dict(out.get("health",{}))) if isinstance(out.get("health"),Mapping) else {}
    if health.get("status")=="dead": return {"person_after":out,"next_event":None,"newly_dead":False,"activated_poisons":[]}
    medicine0=out.get("medicine_state") if isinstance(out.get("medicine_state"),Mapping) else None
    medicine_start=settle_medicine_state(medicine0,at=last) if medicine0 is not None else None
    modifiers=active_recovery_modifiers(medicine_start,at=last) if medicine_start is not None else {}
    active0={str(k):max(0,int(v)) for k,v in (out.get("poison_burdens",{}) or {}).items() if max(0,int(v))>0}
    wounds0=_active_wounds(out); blood0=max(0,int(health.get("blood_lost_ml",0))); attrs=out.get("attributes",{}) if isinstance(out.get("attributes"),Mapping) else {}
    common=dict(body_mass_kg=float(out.get("body_mass_kg",70)),wounds=wounds0,blood_lost_ml=blood0,endurance=max(0,int(attrs.get("endurance",0))),willpower=max(0,int(attrs.get("willpower",0))),medicine_modifiers=modifiers,poison_effects=_merged_effects(active0,medicine_start))
    before=settle_physiology(elapsed_seconds=0,**common); settled=settle_physiology(elapsed_seconds=seconds,**common)
    crossing=last if before.get("lethal_state") in {"dying","dead"} else (now if settled.get("lethal_state") in {"dying","dead"} else None)
    wounds=[dict(w) for w in settled.get("wounds_after",wounds0) if isinstance(w,Mapping)]; blood=max(0,int(settled.get("blood_lost_ml",blood0)))
    poison_total=(max(0,int(event.get("poison_clearance_carry_minutes",0)))+minutes) if active0 else 0; poison_hours,poison_carry=divmod(poison_total,60)
    clear_milli=max(0,int(modifiers.get("toxin_clearance_rate_milli",1000))); active={}
    for ref,burden in active0.items():
        left=max(0,burden-poison_clearance_per_hour(ref,medicine_multiplier_milli=clear_milli)*poison_hours)
        if left: active[ref]=left
    recovery_total=(max(0,int(event.get("recovery_carry_minutes",0)))+minutes) if (wounds0 or blood0>0) else 0; recovery_hours,recovery_carry=divmod(recovery_total,60)
    if recovery_hours and wounds:
        wounds=[dict(w) for w in compact_current_wounds([recovery_advance(w,elapsed_hours=recovery_hours,medicine_modifiers=modifiers) for w in wounds]) if int(w.get("healing_progress_milli",0))<100000 or wound_requires_persistence(w)]
    if recovery_hours and blood>0: blood=max(0,blood-blood_regeneration_ml(body_mass_kg=float(out.get("body_mass_kg",70)),elapsed_hours=recovery_hours,medicine_modifiers=modifiers))
    activation=activate_due_poison_exposures(active=active,pending=out.get("pending_poison_burdens",{}) if isinstance(out.get("pending_poison_burdens"),Mapping) else {},at=now.isoformat())
    active=dict(activation["active_after"]); pending=dict(activation["pending_after"])
    if active: out["poison_burdens"]=active
    else: out.pop("poison_burdens",None)
    if pending: out["pending_poison_burdens"]=pending
    else: out.pop("pending_poison_burdens",None)
    if medicine0 is not None: out["medicine_state"]=settle_medicine_state(medicine_start,at=now); endmods=active_recovery_modifiers(out["medicine_state"],at=now)
    else: endmods={}
    final=settle_physiology(body_mass_kg=float(out.get("body_mass_kg",70)),wounds=wounds,blood_lost_ml=blood,elapsed_seconds=0,endurance=max(0,int(attrs.get("endurance",0))),willpower=max(0,int(attrs.get("willpower",0))),medicine_modifiers=endmods,poison_effects=_merged_effects(active,out.get("medicine_state") if isinstance(out.get("medicine_state"),Mapping) else None))
    health["injuries"]=[dict(w) for w in final.get("wounds_after",wounds) if isinstance(w,Mapping)]; health["blood_lost_ml"]=max(0,int(final.get("blood_lost_ml",blood))); health["shock"]=max(0,int(final.get("shock",0))); health["consciousness"]=max(0,min(100,int(final.get("consciousness",100))))
    prior_dead=str((person.get("health",{}) if isinstance(person.get("health"),Mapping) else {}).get("status",""))=="dead"; lethal=str(final.get("lethal_state","alive")); dying_raw=health.get("dying_since"); dying=_dt(dying_raw) if isinstance(dying_raw,str) else crossing
    if lethal=="dead": health["status"]="dead"; health["consciousness"]=0; health.pop("dying_since",None)
    elif lethal=="dying" or dying is not None:
        dying=dying or now
        if now>=dying+timedelta(minutes=dying_deadline_minutes()): health["status"]="dead"; health["consciousness"]=0; health.pop("dying_since",None)
        else: health["status"]="incapacitated"; health["dying_since"]=dying.isoformat()
    elif lethal in {"critical","unconscious"}: health["status"]="incapacitated"; health.pop("dying_since",None)
    elif health["injuries"]: health["status"]="injured"; health.pop("dying_since",None)
    else:
        health["status"]="ready"; health.pop("dying_since",None)
        if health["blood_lost_ml"]==0: health["shock"]=0; health["consciousness"]=100
    out["health"]=health
    next_event=next_physiology_event(str(out.get("person_id") or event.get("owner_ref") or ""),out,now=now,recovery_carry_minutes=recovery_carry,poison_clearance_carry_minutes=poison_carry)
    return {"person_after":out,"next_event":next_event,"newly_dead":not prior_dead and health.get("status")=="dead","activated_poisons":sorted(str(k) for k in activation.get("activated",{})),"recovery_hours":recovery_hours,"poison_clearance_hours":poison_hours}


def detach_person_physiology_wake(schedule: Mapping[str,Any],*,person_ref:str,person:Mapping[str,Any],at:str|datetime)->dict[str,Any]:
    state=copy.deepcopy(dict(schedule)); rows=state.setdefault("one_off",{}); event=rows.pop(f"person_physiology_due:{person_ref}",None); current=copy.deepcopy(dict(person)); rc=pc=0
    if isinstance(event,Mapping):
        result=settle_person_physiology_event(current,event,at=at); current=dict(result["person_after"]); replacement=result.get("next_event")
        if isinstance(replacement,Mapping): rc=max(0,int(replacement.get("recovery_carry_minutes",0)))%60; pc=max(0,int(replacement.get("poison_clearance_carry_minutes",0)))%60
    return {"person_after":current,"schedule_after":state,"recovery_carry_minutes":rc,"poison_clearance_carry_minutes":pc}


def attach_person_physiology_wake(schedule:Mapping[str,Any],*,person_ref:str,person:Mapping[str,Any],now:str|datetime,recovery_carry_minutes:int=0,poison_clearance_carry_minutes:int=0)->dict[str,Any]:
    state=copy.deepcopy(dict(schedule)); rows=state.setdefault("one_off",{}); event_id=f"person_physiology_due:{person_ref}"; rows.pop(event_id,None); wake=next_physiology_event(person_ref,person,now=now,recovery_carry_minutes=recovery_carry_minutes,poison_clearance_carry_minutes=poison_clearance_carry_minutes)
    if wake is not None: rows[event_id]=wake
    return state


def settle_due_person_physiology(events:Sequence[Mapping[str,Any]],*,at:str|datetime,load_person:Callable[[str],tuple[Any,...]],save_person:Callable[[str,Mapping[str,Any]],None])->dict[str,Any]:
    pending=[]; settled=[]; dead=[]
    for event in events:
        if not isinstance(event,Mapping) or event.get("kind")!="person_physiology_due": continue
        ref=str(event.get("owner_ref") or "")
        try: loaded=load_person(ref)
        except (KeyError,ValueError,FileNotFoundError): continue
        person=loaded[-1] if isinstance(loaded,tuple) else loaded
        if not isinstance(person,Mapping): continue
        result=settle_person_physiology_event(person,event,at=at); save_person(ref,result["person_after"]); settled.append(ref)
        if result.get("next_event"): pending.append(result["next_event"])
        if result.get("newly_dead"): dead.append(ref)
    return {"pending_events":pending,"settled_refs":sorted(set(settled)),"dead_refs":sorted(set(dead))}


def settle_review_faction_physiology(schedule:Mapping[str,Any],*,faction_refs:Sequence[str],at:str|datetime,load_roster:Callable[[str],tuple[Any,...]],save_person:Callable[[str,Mapping[str,Any]],None],already_settled_refs:Sequence[str]=())->dict[str,Any]:
    """Settle active body clocks before a monthly faction review can treat them."""
    now=_dt(at); one_off=schedule.get("one_off",{}) if isinstance(schedule.get("one_off"),Mapping) else {}; excluded={str(x) for x in already_settled_refs}; replaced=[]; settled=[]; dead=[]; carries={}
    for faction_ref in sorted({str(x) for x in faction_refs if isinstance(x,str) and x}):
        try: loaded=load_roster(faction_ref)
        except (KeyError,ValueError,FileNotFoundError): continue
        roster=loaded[-1]; people=roster.get("people",[]) if isinstance(roster,Mapping) else []
        for raw in people if isinstance(people,list) else []:
            if not isinstance(raw,Mapping): continue
            ref=str(raw.get("person_id") or ""); event_id=f"person_physiology_due:{ref}"; event=one_off.get(event_id)
            if not ref or ref in excluded or not isinstance(event,Mapping) or event.get("kind")!="person_physiology_due": continue
            last=event.get("last_settled_at")
            if not isinstance(last,str) or _dt(last)>=now: continue
            result=settle_person_physiology_event(raw,event,at=now); save_person(ref,result["person_after"]); replacement=result.get("next_event")
            if isinstance(replacement,Mapping): carries[ref]=(int(replacement.get("recovery_carry_minutes",0))%60,int(replacement.get("poison_clearance_carry_minutes",0))%60)
            replaced.append(event_id); settled.append(ref)
            if result.get("newly_dead"): dead.append(ref)
    return {"replaced_event_ids":sorted(set(replaced)),"settled_refs":sorted(set(settled)),"dead_refs":sorted(set(dead)),"carry_by_person":carries}


def new_physiology_wakes_from_touched_people(writes:Mapping[str,Any],*,now:str|datetime,existing_event_ids:Sequence[str],person_collection_paths:Sequence[str]=(),replace_event_ids:Sequence[str]=(),replacement_carries:Mapping[str,Sequence[int]]|None=None)->list[dict[str,Any]]:
    replacing=set(map(str,replace_event_ids)); existing={str(x) for x in existing_event_ids if str(x) not in replacing}; allowed=set(person_collection_paths); carries=replacement_carries or {}; result=[]
    for path,value in writes.items():
        if allowed and path not in allowed: continue
        rows=value.get("people",[]) if isinstance(value,Mapping) else []
        if not isinstance(rows,list): continue
        for person in rows:
            if not isinstance(person,Mapping): continue
            ref=str(person.get("person_id") or ""); eid=f"person_physiology_due:{ref}"
            if not ref or eid in existing: continue
            carry=carries.get(ref,()); rc=int(carry[0])%60 if isinstance(carry,Sequence) and not isinstance(carry,(str,bytes)) and len(carry)>0 else 0; pc=int(carry[1])%60 if isinstance(carry,Sequence) and not isinstance(carry,(str,bytes)) and len(carry)>1 else 0
            wake=next_physiology_event(ref,person,now=now,recovery_carry_minutes=rc,poison_clearance_carry_minutes=pc)
            if wake: result.append(wake); existing.add(eid)
    return result


__all__=["attach_person_physiology_wake","detach_person_physiology_wake","new_physiology_wakes_from_touched_people","next_physiology_event","physiology_needed","settle_due_person_physiology","settle_person_physiology_event","settle_review_faction_physiology"]
