"""Deterministic anatomical target preference resolution.

Targeting doctrine is static game data referenced by ID. Persistent person state
stores only that ID plus current mutable combat facts.
"""
from __future__ import annotations
from typing import Any, Mapping, Sequence
import hashlib
from .health import structure_family_members
from .doctrines import doctrine_registry, resolve_individual_doctrine


def _structure_damage(wounds:Sequence[Mapping[str,Any]],structure_ref:str)->int:
    return max([0]+[max(0,int(w.get('structure_damage',0))) for w in wounds if w.get('structure_ref')==structure_ref])


def _targeting_data()->Mapping[str,Any]:
    return doctrine_registry()


def resolve_combat_doctrine(person:Mapping[str,Any])->Mapping[str,Any]|None:
    return resolve_individual_doctrine(person.get('combat_doctrine_ref'))


def _stable_index(seed: str, count: int) -> int:
    if count <= 1:
        return 0
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % count


def resolve_structure_selector(
    selector:str,*,target:Mapping[str,Any],preference_seed:str|None=None
) -> str:
    """Resolve a structure id or structure family to one concrete structure.

    Families prefer the least-damaged member.  When several members are equally
    viable, autonomous callers may supply a deterministic preference seed so a
    whole formation does not independently choose the same left/right structure
    merely because lexical ID order is globally stable.  Player-authored calls
    that omit the seed retain the historical stable-by-id behavior.
    """
    if not selector: raise KeyError(selector)
    try:
        members=structure_family_members(selector)
    except KeyError:
        return selector
    wounds=target.get('health',{}).get('injuries',[]) if isinstance(target.get('health'),Mapping) else []
    if not isinstance(wounds,list): wounds=[]
    ranked=sorted(((_structure_damage(wounds,ref),ref) for ref in members),key=lambda row:(row[0],row[1]))
    best_damage=ranked[0][0]
    candidates=[ref for damage,ref in ranked if damage==best_damage]
    if preference_seed:
        return candidates[_stable_index(f"{preference_seed}|{selector}|side",len(candidates))]
    return candidates[0]


def _weighted_priority_offset(seed: str, count: int) -> int:
    """Choose among an ordered generic priority list without erasing its bias.

    The first discipline-specific family remains the modal choice, but stable
    actor/target identity spreads a formation across secondary function-denial
    targets.  This is deterministic replay-safe variation, not RNG.
    """
    if count <= 1:
        return 0
    if count == 2:
        weights = (70, 30)
    elif count == 3:
        weights = (55, 30, 15)
    else:
        weights = (50, 25, 15, 10, *([5] * max(0, count - 4)))
        weights = weights[:count]
    bucket = _stable_index(f"{seed}|weighted-family", sum(weights))
    running = 0
    for index, weight in enumerate(weights):
        running += weight
        if bucket < running:
            return index
    return 0


def _priority_target(
    rows:Any,*,target:Mapping[str,Any],preference_seed:str|None=None,rotate_rows:bool=False
) -> str|None:
    if not isinstance(rows,list): return None
    ordered=list(rows)
    if rotate_rows and preference_seed and len(ordered)>1:
        offset=_weighted_priority_offset(preference_seed,len(ordered))
        ordered=ordered[offset:]+ordered[:offset]
    for row in ordered:
        selector = row if isinstance(row,str) else (row.get('structure_selector') or row.get('structure_ref') if isinstance(row,Mapping) else None)
        if not isinstance(selector,str) or not selector: continue
        try:return resolve_structure_selector(selector,target=target,preference_seed=preference_seed)
        except KeyError:continue
    return None


def doctrine_target(person:Mapping[str,Any],*,intent:str,target:Mapping[str,Any]) -> str|None:
    doctrine=resolve_combat_doctrine(person)
    if not isinstance(doctrine,Mapping): return None
    targeting=doctrine.get('targeting',{}) if isinstance(doctrine.get('targeting'),Mapping) else {}
    key='lethal_priority' if intent=='lethal' else 'disable_priority'
    seed=f"{person.get('person_id','')}|{target.get('person_id','')}|doctrine|{intent}"
    return _priority_target(targeting.get(key,[]),target=target,preference_seed=seed)


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
    seed=f"{person.get('person_id','')}|{target.get('person_id','')}|generic|{discipline if intent=='disable' else intent}"
    return _priority_target(rows,target=target,preference_seed=seed,rotate_rows=(intent=='disable'))

__all__=['doctrine_target','intent_target','resolve_combat_doctrine','resolve_structure_selector']
