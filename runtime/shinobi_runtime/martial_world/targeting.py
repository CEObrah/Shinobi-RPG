"""Deterministic anatomical target preference resolution.

Targeting doctrine is static game data referenced by ID. Persistent person state
stores only that ID plus current mutable combat facts.
"""
from __future__ import annotations
from typing import Any, Mapping, Sequence
from .health import structure_family_members
from .doctrines import doctrine_registry, resolve_individual_doctrine


def _structure_damage(wounds:Sequence[Mapping[str,Any]],structure_ref:str)->int:
    return max([0]+[max(0,int(w.get('structure_damage',0))) for w in wounds if w.get('structure_ref')==structure_ref])


def _targeting_data()->Mapping[str,Any]:
    return doctrine_registry()


def resolve_combat_doctrine(person:Mapping[str,Any])->Mapping[str,Any]|None:
    return resolve_individual_doctrine(person.get('combat_doctrine_ref'))


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


def _priority_target(rows:Any,*,target:Mapping[str,Any]) -> str|None:
    if not isinstance(rows,list): return None
    for row in rows:
        selector = row if isinstance(row,str) else (row.get('structure_selector') or row.get('structure_ref') if isinstance(row,Mapping) else None)
        if not isinstance(selector,str) or not selector: continue
        try:return resolve_structure_selector(selector,target=target)
        except KeyError:continue
    return None


def doctrine_target(person:Mapping[str,Any],*,intent:str,target:Mapping[str,Any]) -> str|None:
    doctrine=resolve_combat_doctrine(person)
    if not isinstance(doctrine,Mapping): return None
    targeting=doctrine.get('targeting',{}) if isinstance(doctrine.get('targeting'),Mapping) else {}
    key='lethal_priority' if intent=='lethal' else 'disable_priority'
    return _priority_target(targeting.get(key,[]),target=target)


def intent_target(person:Mapping[str,Any],*,intent:str,target:Mapping[str,Any]) -> str|None:
    """Resolve a person's requested combat intent to a concrete anatomical aim.

    A personal doctrine remains authoritative when present. People without a
    registered precision doctrine still understand ordinary restraint: generic
    disable intent aims at function-denial limb structures instead of silently
    falling back to the chest. Contact geometry, precision, weapons and trauma
    remain authoritative, so a failed or catastrophic nonlethal attempt can
    still injure or kill. Generic lethal intent intentionally has no precision
    fallback; untrained lethal actors keep broad physical targeting rather than
    receiving free vital-structure expertise.
    """
    if intent not in {'disable','lethal'}:
        raise ValueError('targeting intent invalid')
    chosen=doctrine_target(person,intent=intent,target=target)
    if chosen: return chosen
    generic=_targeting_data().get('generic_intent_priorities',{})
    rows=generic.get(intent,[]) if isinstance(generic,Mapping) else []
    if intent=='disable' and isinstance(rows,Mapping):
        skills=person.get('martial_skills',{}) if isinstance(person.get('martial_skills'),Mapping) else {}
        disciplines=('sword','spear','unarmed','bow','hidden_weapons')
        discipline=max(disciplines,key=lambda ref:(max(0,int(skills.get(ref,0))),-disciplines.index(ref)))
        if max(0,int(skills.get(discipline,0)))<=0:
            discipline='default'
        selectors=rows.get(discipline,rows.get('default',[]))
        if isinstance(selectors,list):
            rows=[{'structure_selector':str(selector)} for selector in selectors if isinstance(selector,str) and selector]
    return _priority_target(rows,target=target)

__all__=['doctrine_target','intent_target','resolve_combat_doctrine','resolve_structure_selector']
