"""Shared authority and availability rules for consequential Jianghu actions."""
from __future__ import annotations
import copy
from datetime import datetime
from typing import Any, Mapping, Sequence

_TERMINAL_COMMITMENT=frozenset({'completed','released','cancelled','failed'})
_COMMAND_OFFICES=frozenset({'leader','deputy_leader','field_commander','deputy_field_commander','route_captain'})

def office_roots(person:Mapping[str,Any])->frozenset[str]:
    rows=person.get('standing_offices',[])
    if not isinstance(rows,list): return frozenset()
    return frozenset(str(row).split(':',1)[0] for row in rows if isinstance(row,str))

def can_command_faction_members(person:Mapping[str,Any])->bool:
    return bool(office_roots(person)&_COMMAND_OFFICES)

def active_commitment_for_person(registry:Mapping[str,Any],person_ref:str,*,excluding:str|None=None)->Mapping[str,Any]|None:
    commitments=registry.get('commitments',{})
    if not isinstance(commitments,Mapping): return None
    index=registry.get('person_index',{})
    refs=[]
    if isinstance(index,Mapping) and isinstance(index.get(person_ref),str): refs.append(str(index[person_ref]))
    if not refs:
        refs.extend(str(cid) for cid,row in commitments.items() if isinstance(row,Mapping) and person_ref in row.get('person_refs',[]))
    for cid in refs:
        if cid==excluding: continue
        row=commitments.get(cid)
        if isinstance(row,Mapping) and str(row.get('status','active')) not in _TERMINAL_COMMITMENT: return row
    return None

def reserve_people(registry:Mapping[str,Any],*,commitment_ref:str,kind:str,owner_ref:str,person_refs:Sequence[str],started_at:str,location_ref:str|None=None,ends_at:str|None=None)->dict[str,Any]:
    out=copy.deepcopy(dict(registry)); commitments=out.setdefault('commitments',{}); index=out.setdefault('person_index',{})
    if not isinstance(commitments,dict) or not isinstance(index,dict): raise ValueError('commitment registry invalid')
    if commitment_ref in commitments: raise ValueError('commitment already exists')
    refs=tuple(dict.fromkeys(str(ref) for ref in person_refs))
    if not refs: raise ValueError('commitment requires people')
    for ref in refs:
        if active_commitment_for_person(out,ref) is not None: raise ValueError(f'person already committed:{ref}')
    resources=[{'kind':'person','ref':ref,'owner_ref':owner_ref} for ref in refs]
    row={'commitment_ref':commitment_ref,'kind':str(kind),'activity_ref':commitment_ref,'activity_kind':str(kind),'actor_ref':str(owner_ref),'owner_ref':str(owner_ref),'person_refs':list(refs),'resources':resources,'started_at':str(started_at),'status':'active'}
    if location_ref: row['location_ref']=str(location_ref)
    if ends_at: datetime.fromisoformat(str(ends_at)); row['ends_at']=str(ends_at)
    commitments[commitment_ref]=row
    for ref in refs: index[ref]=commitment_ref
    return out

def release_commitment(registry:Mapping[str,Any],*,commitment_ref:str,status:str,ended_at:str)->dict[str,Any]:
    if status not in _TERMINAL_COMMITMENT: raise ValueError('commitment terminal status invalid')
    out=copy.deepcopy(dict(registry)); commitments=out.get('commitments',{}); index=out.get('person_index',{})
    row=commitments.get(commitment_ref) if isinstance(commitments,dict) else None
    if not isinstance(row,dict): raise ValueError('commitment unresolved')
    for ref in row.get('person_refs',[]):
        if isinstance(index,dict) and index.get(ref)==commitment_ref: index.pop(ref,None)
    commitments.pop(commitment_ref,None)
    return out

def available_people(registry:Mapping[str,Any],people:Sequence[Mapping[str,Any]])->list[Mapping[str,Any]]:
    return [p for p in people if isinstance(p,Mapping) and isinstance(p.get('person_id'),str) and active_commitment_for_person(registry,str(p['person_id'])) is None]

def _active_deployment_for(deployments:Mapping[str,Any],person_ref:str)->tuple[str,Mapping[str,Any]]|None:
    rows=deployments.get('deployments',{})
    if not isinstance(rows,Mapping): return None
    for dep_ref,row in rows.items():
        if not isinstance(row,Mapping) or row.get('status')!='active': continue
        structure=row.get('structure',{}); members=structure.get('member_refs',[]) if isinstance(structure,Mapping) else []
        if person_ref in members: return str(dep_ref),row
    return None

def validate_combat_sides(*,actor_ref:str,side_a_refs:Sequence[str],side_b_refs:Sequence[str],people:Mapping[str,Mapping[str,Any]],present_refs:Sequence[str],deployments:Mapping[str,Any],objective:Mapping[str,Any])->dict[str,str]:
    a=tuple(dict.fromkeys(str(x) for x in side_a_refs)); b=tuple(dict.fromkeys(str(x) for x in side_b_refs))
    if not a or not b or set(a)&set(b) or actor_ref not in set(a)|set(b): raise ValueError('combat sides invalid')
    all_refs=set(a)|set(b)
    if all_refs-set(map(str,present_refs)): raise ValueError('combat participant not physically present')
    if all_refs-set(people): raise ValueError('combat participant unresolved')
    actor_side=a if actor_ref in a else b; enemy_side=b if actor_ref in a else a; actor=people[actor_ref]
    actor_dep=_active_deployment_for(deployments,actor_ref); reasons={actor_ref:'acting_person'}
    for ref in actor_side:
        if ref==actor_ref: continue
        dep=_active_deployment_for(deployments,ref)
        if actor_dep and dep and dep[0]==actor_dep[0]: reasons[ref]=f'deployment:{actor_dep[0]}'; continue
        if people[ref].get('faction_ref')==actor.get('faction_ref') and can_command_faction_members(actor): reasons[ref]='lawful_faction_command'; continue
        raise ValueError(f'combat ally lacks consent or command basis:{ref}')
    targets=[str(x) for x in objective.get('target_refs',[]) if isinstance(x,str) and x in enemy_side]
    anchor=targets[0] if targets else enemy_side[0]; reasons[anchor]='directly_attacked_or_hostile_contact'; anchor_dep=_active_deployment_for(deployments,anchor)
    for ref in enemy_side:
        if ref==anchor: continue
        dep=_active_deployment_for(deployments,ref)
        if anchor_dep and dep and dep[0]==anchor_dep[0]: reasons[ref]=f'deployment:{anchor_dep[0]}'; continue
        raise ValueError(f'combat opponent was assigned without team authority:{ref}')
    return reasons

def factual_restraint_basis(*,target:Mapping[str,Any],target_ref:str,actor_ref:str,combats:Mapping[str,Any],existing_custody:Sequence[Mapping[str,Any]])->str|None:
    health=target.get('health',{}) if isinstance(target.get('health'),Mapping) else {}
    if health.get('status')=='dead': return 'deceased'
    if health.get('status')=='incapacitated' or int(health.get('consciousness',100))<=0: return 'incapacitated'
    rows=combats.get('combats',{}) if isinstance(combats,Mapping) else {}
    if isinstance(rows,Mapping):
        for combat in rows.values():
            if not isinstance(combat,Mapping) or target_ref not in combat.get('combatants',{}): continue
            statuses=set(combat['combatants'][target_ref].get('status_families',[]))
            if 'surrendered' in statuses: return 'voluntary_surrender'
            if 'restrained' in statuses: return 'successful_physical_restraint'
            if {'unconscious','incapacitated'}&statuses: return 'incapacitated_after_combat'
    for row in existing_custody:
        if isinstance(row,Mapping) and row.get('person_ref')==target_ref and row.get('status') not in {'released','escaped','rescued','executed'} and row.get('captor_ref')==actor_ref: return 'existing_lawful_custody'
    return None
