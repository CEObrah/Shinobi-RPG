"""Deterministic anatomical target preference resolution.

Targeting doctrine is static game data referenced by ID. Persistent person state
stores only that ID plus current mutable combat facts.
"""
from __future__ import annotations
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence
from .health import structure_family_members


def _structure_damage(wounds:Sequence[Mapping[str,Any]],structure_ref:str)->int:
    return max([0]+[max(0,int(w.get('structure_damage',0))) for w in wounds if w.get('structure_ref')==structure_ref])


@lru_cache(maxsize=1)
def _doctrine_registry()->Mapping[str,Any]:
    root=Path(__file__).resolve().parents[3]
    data=json.loads((root/'game/data/martial-world/combat-doctrines.json').read_text())
    rows=data.get('doctrines',{}) if isinstance(data,Mapping) else {}
    if not isinstance(rows,Mapping):
        raise ValueError('jianghu combat doctrine registry invalid')
    return rows


def resolve_combat_doctrine(person:Mapping[str,Any])->Mapping[str,Any]|None:
    ref=person.get('combat_doctrine_ref')
    if not isinstance(ref,str) or not ref:
        return None
    row=_doctrine_registry().get(ref)
    if row is None:
        raise KeyError(ref)
    if not isinstance(row,Mapping):
        raise ValueError('jianghu combat doctrine invalid')
    return row


def resolve_structure_selector(selector:str,*,target:Mapping[str,Any]) -> str:
    """Resolve a structure id or structure family to one concrete structure.

    Families prefer a still-functional member. Ties are stable by id. This is
    intentionally not an exposure simulator; geometry still determines whether
    the aimed line reaches the body, and the player can name a concrete side.
    """
    if not selector: raise KeyError(selector)
    try:
        members=structure_family_members(selector)
    except KeyError:
        return selector
    wounds=target.get('health',{}).get('injuries',[]) if isinstance(target.get('health'),Mapping) else []
    if not isinstance(wounds,list): wounds=[]
    return min(members,key=lambda ref:(_structure_damage(wounds,ref),ref))


def doctrine_target(person:Mapping[str,Any],*,intent:str,target:Mapping[str,Any]) -> str|None:
    doctrine=resolve_combat_doctrine(person)
    if not isinstance(doctrine,Mapping): return None
    key='lethal_priority' if intent=='lethal' else 'disable_priority'
    rows=doctrine.get(key,[])
    if not isinstance(rows,list): return None
    for row in rows:
        if not isinstance(row,Mapping): continue
        selector=row.get('structure_selector') or row.get('structure_ref')
        if not isinstance(selector,str) or not selector: continue
        try:return resolve_structure_selector(selector,target=target)
        except KeyError:continue
    return None

__all__=['doctrine_target','resolve_combat_doctrine','resolve_structure_selector']
