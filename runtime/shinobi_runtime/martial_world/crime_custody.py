"""Evidence-based crime attention and conserved custody records."""
from __future__ import annotations
import copy, json
from pathlib import Path
from typing import Any, Mapping
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'
def _cfg(): return json.loads((_MW/'crime-custody.json').read_text())
def crime_attention(*,offense:str,confidence:int,publicly_delivered:bool,prior_offenses:int=0)->dict[str,int]:
    row=_cfg()['offenses'].get(offense)
    if row is None: raise KeyError(offense)
    if not publicly_delivered: return {'attention':0,'bounty_cash':0}
    conf=max(0,min(100,int(confidence))); att=int(row['attention'])*conf//100 + max(0,int(prior_offenses))*5
    return {'attention':att,'bounty_cash':int(row['bounty_base_cash'])*(100+min(200,att))//100}
def create_custody_record(*,person_ref:str,captor_ref:str,at:str,location_ref:str,basis:str)->dict[str,Any]:
    if person_ref==captor_ref: raise ValueError('self custody invalid')
    return {'custody_id':f'custody:{person_ref}:{at}','person_ref':person_ref,'captor_ref':captor_ref,'status':'restrained','location_ref':location_ref,'basis':basis,'started_at':at}
def custody_transition(record:Mapping[str,Any],*,action:str,at:str,actor_ref:str,new_location_ref:str|None=None)->dict[str,Any]:
    if action not in _cfg()['custody_actions']: raise KeyError(action)
    out=copy.deepcopy(dict(record))
    if out.get('status') in {'released','escaped','executed'}: raise ValueError('custody already terminal')
    captor=str(out.get('captor_ref') or '')
    prisoner=str(out.get('person_ref') or '')
    if action=='escape_attempt':
        if actor_ref!=prisoner: raise ValueError('only prisoner may attempt escape')
    elif actor_ref!=captor:
        raise ValueError('custody transition requires current custodian')
    status={'restrain':'restrained','release':'released','escape_attempt':'escaped'}[action]
    out['status']=status
    return out
