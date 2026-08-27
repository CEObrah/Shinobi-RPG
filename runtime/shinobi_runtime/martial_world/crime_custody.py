"""Evidence-based crime attention and conserved custody records."""
from __future__ import annotations
import copy, json
from pathlib import Path
from typing import Any, Mapping
from datetime import datetime, timedelta
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'
def _cfg(): return json.loads((_MW/'crime-custody.json').read_text())
def crime_attention(*,offense:str,confidence:int,publicly_delivered:bool,prior_offenses:int=0)->dict[str,int]:
    row=_cfg()['offenses'].get(offense)
    if row is None: raise KeyError(offense)
    if not publicly_delivered: return {'attention':0,'bounty_cash':0}
    conf=max(0,min(100,int(confidence))); att=int(row['attention'])*conf//100 + max(0,int(prior_offenses))*5
    return {'attention':att,'bounty_cash':int(row['bounty_base_cash'])*(100+min(200,att))//100}
def create_custody_record(*,person_ref:str,captor_ref:str,at:str,location_ref:str,basis:str,holder_faction_ref:str|None=None)->dict[str,Any]:
    if person_ref==captor_ref: raise ValueError('self custody invalid')
    out={'custody_id':f'custody:{person_ref}:{at}','person_ref':person_ref,'captor_ref':captor_ref,'status':'restrained','location_ref':location_ref,'basis':basis,'started_at':at}
    if holder_faction_ref:
        out['holder_faction_ref']=str(holder_faction_ref)
    return out



def government_detention_days(offense:str)->int:
    row=_cfg()['offenses'].get(str(offense))
    if not isinstance(row,Mapping): raise KeyError(offense)
    return max(1,int(row.get('detention_days',7)))

def create_government_custody_record(*,person_ref:str,jurisdiction_ref:str,at:str,detention_site_ref:str,basis:str,offense:str,guard_strength:int,sentence_days:int|None=None)->dict[str,Any]:
    start=datetime.fromisoformat(str(at)); days=max(1,int(sentence_days)) if sentence_days is not None else government_detention_days(offense)
    out=create_custody_record(person_ref=person_ref,captor_ref=f'government:{jurisdiction_ref}',at=at,location_ref=detention_site_ref,basis=basis)
    out.update({'holder_kind':'government','jurisdiction_ref':str(jurisdiction_ref),'detention_site_ref':str(detention_site_ref),'sentence_offense':str(offense),'sentence_days':days,'sentence_release_at':(start+timedelta(days=days)).isoformat(),'guard_strength':max(1,int(guard_strength))})
    return out

def government_rescue_infiltration(*,actor:Mapping[str,Any],guard_strength:int,hour:int)->dict[str,Any]:
    attrs=actor.get('attributes',{}) if isinstance(actor.get('attributes'),Mapping) else {}
    skills=actor.get('martial_skills',{}) if isinstance(actor.get('martial_skills'),Mapping) else {}
    professional=actor.get('professional_skills',{}) if isinstance(actor.get('professional_skills'),Mapping) else {}
    stealth=max(0,int(skills.get('stealth_scouting',professional.get('stealth_scouting',0))))
    dex=max(0,int(attrs.get('dexterity',0))); per=max(0,int(attrs.get('perception',0))); intel=max(0,int(attrs.get('intelligence',0)))
    actor_score=stealth*4+dex+per+intel
    night_bonus=120 if int(hour)%24 < 5 or int(hour)%24 >= 22 else 0
    guard=max(1,int(guard_strength)); threshold=guard*22+180
    return {'success': bool(actor_score+night_bonus>=threshold),'actor_score':actor_score+night_bonus,'guard_threshold':threshold}

def mark_custody_informed(record:Mapping[str,Any],*,faction_ref:str)->dict[str,Any]:
    """Persist only consequential institutional knowledge of a live captivity."""
    if not faction_ref:
        return copy.deepcopy(dict(record))
    out=copy.deepcopy(dict(record))
    known=[str(x) for x in out.get('informed_faction_refs',[]) if isinstance(x,str) and x]
    if faction_ref not in known:
        known.append(str(faction_ref))
    out['informed_faction_refs']=sorted(set(known))
    return out
def custody_transition(record:Mapping[str,Any],*,action:str,at:str,actor_ref:str,new_location_ref:str|None=None)->dict[str,Any]:
    if action not in _cfg()['custody_actions']: raise KeyError(action)
    out=copy.deepcopy(dict(record))
    if out.get('status') in {'released','escaped','rescued','executed'}: raise ValueError('custody already terminal')
    captor=str(out.get('captor_ref') or '')
    prisoner=str(out.get('person_ref') or '')
    if action=='escape_attempt':
        if actor_ref!=prisoner: raise ValueError('only prisoner may attempt escape')
    elif action=='rescue':
        if not actor_ref or actor_ref==prisoner: raise ValueError('rescue requires another actor')
    elif actor_ref!=captor:
        raise ValueError('custody transition requires current custodian')
    status={'restrain':'restrained','release':'released','escape_attempt':'escaped','rescue':'rescued'}[action]
    out['status']=status
    out['resolved_at']=at
    if action=='rescue': out['rescued_by_ref']=actor_ref
    return out
