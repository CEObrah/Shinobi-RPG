"""Conserved ammunition, integrity wear, loss and repair calculations."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Mapping
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'
def _cfg(): return json.loads((_MW/'equipment-lifecycle.json').read_text())
def _workshop(): return json.loads((_MW/'workshop.json').read_text())
def expend_ammunition(stock:Mapping[str,int],*,ammo_ref:str,shots:int)->dict[str,int]:
    if shots<0:
        raise ValueError('shots invalid')
    out={str(k):int(v) for k,v in stock.items()}
    if out.get(ammo_ref,0)<shots:
        raise ValueError('insufficient ammunition')
    out[ammo_ref]-=shots
    return out
def apply_wear(*,integrity_milli:int,event_kind:str,count:int=1)->int:
    wear=_cfg()['wear_milli'].get(event_kind)
    if wear is None: raise KeyError(event_kind)
    return max(0,int(integrity_milli)-int(wear)*max(0,int(count)))
def repair_quote(*,integrity_milli:int,target_integrity_milli:int,crafting_skill:int)->dict[str,int]:
    c=_cfg()['repair']
    if crafting_skill<int(c['minimum_crafting_skill']): raise ValueError('crafting skill too low')
    missing=max(0,min(1000,int(target_integrity_milli))-max(0,int(integrity_milli)))
    hours=(missing+int(c['general_integrity_milli_per_crafting_hour'])-1)//int(c['general_integrity_milli_per_crafting_hour'])
    return {'integrity_restored_milli':missing,'crafting_hours':hours}

def repair_material_requirements(*,item_ref:str,integrity_restored_milli:int,quantity:int=1)->dict[str,int]:
    if integrity_restored_milli<0 or quantity<=0: raise ValueError('repair material input invalid')
    recipe=next((row for row in _workshop().get('recipes',{}).values() if isinstance(row,Mapping) and row.get('output')==item_ref),None)
    if not isinstance(recipe,Mapping): raise KeyError(item_ref)
    # Repair replaces only the damaged fraction. At most one quarter of the full
    # production inputs is consumed at complete integrity loss; labor performs the
    # remaining restoration. Every positive physical input rounds up deterministically.
    out={}
    for ref,raw in recipe.get('inputs',{}).items():
        numerator=max(0,int(raw))*max(0,int(integrity_restored_milli))*int(quantity)
        amount=(numerator+3999)//4000
        if amount: out[str(ref)]=amount
    return out
