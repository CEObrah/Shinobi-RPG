"""Directed current faction relationships and diplomacy scoring."""
from __future__ import annotations
import copy, json
from pathlib import Path
from typing import Any, Mapping
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'
def _data(): return json.loads((_MW/'faction-relations.json').read_text())
def apply_relation_event(edge:Mapping[str,Any]|None,*,from_faction:str,to_faction:str,event_kind:str)->dict[str,Any]:
    d=_data(); delta=d['event_deltas'].get(event_kind)
    if delta is None: raise KeyError(event_kind)
    out=copy.deepcopy(dict(edge or {'from_faction':from_faction,'to_faction':to_faction}))
    if out['from_faction']!=from_faction or out['to_faction']!=to_faction: raise ValueError('relation direction mismatch')
    for k,(lo,hi) in d['axes'].items():
        value=max(lo,min(hi,int(out.get(k,0))+int(delta.get(k,0))))
        if value: out[k]=value
        else: out.pop(k,None)
    return out
def diplomacy_score(edge:Mapping[str,Any],*,proposal_value_cash:int,proposal_cost_cash:int,strategic_fit:int,risk:int)->int:
    trust=int(edge.get('trust',0)); respect=int(edge.get('respect',0)); hostility=int(edge.get('hostility',0)); obligation=int(edge.get('obligation',0))
    value=max(-100, min(100, (int(proposal_value_cash)-int(proposal_cost_cash))//1000))
    return trust*4 + respect*2 - hostility*5 + obligation*2 + value*3 + int(strategic_fit)*3 - int(risk)*2
def evaluate_proposal(edge:Mapping[str,Any],**kwargs)->dict[str,Any]:
    score=diplomacy_score(edge,**kwargs); return {'score':score,'accept':score>=100,'counteroffer':-50<=score<100,'reject':score<-50}
def proposal_kind_supported(kind:str)->bool:
    return str(kind) in {str(x) for x in _data().get('proposal_kinds',[]) if isinstance(x,str)}
