"""Universal conserved availability reservations for Jianghu activities."""
from __future__ import annotations
import copy
from typing import Any, Mapping, Sequence

def _active_resources(state:Mapping[str,Any])->set[tuple[str,str]]:
    used=set()
    for row in state.get('commitments',{}).values():
        if not isinstance(row,Mapping) or row.get('status','active')!='active': continue
        for r in row.get('resources',[]):
            if isinstance(r,Mapping) and isinstance(r.get('kind'),str) and isinstance(r.get('ref'),str): used.add((r['kind'],r['ref']))
    return used

def reserve_resources(state:Mapping[str,Any],*,resources:Sequence[tuple[str,str,str]],actor_ref:str,owner_ref:str,activity_ref:str,activity_kind:str,started_at:str,location_ref:str|None=None)->dict[str,Any]:
    out=copy.deepcopy(dict(state)); rows=out.setdefault('commitments',{}); index=out.setdefault('person_index',{})
    if not isinstance(rows,dict): raise ValueError('commitments invalid')
    used=_active_resources(out); material=[]
    for kind,ref,res_owner in resources:
        key=(str(kind),str(ref))
        if key in used: raise ValueError(f'resource already committed:{key[0]}:{key[1]}')
        used.add(key); material.append({'kind':key[0],'ref':key[1],'owner_ref':str(res_owner)})
    cid=f'commitment:{activity_ref}'
    if cid in rows: raise ValueError('activity already committed')
    people=[r['ref'] for r in material if r['kind']=='person']
    row={'commitment_ref':cid,'activity_ref':str(activity_ref),'activity_kind':str(activity_kind),'kind':str(activity_kind),'actor_ref':str(actor_ref),'owner_ref':str(owner_ref),'resources':material,'person_refs':people,'started_at':str(started_at),'status':'active'}
    if location_ref: row['location_ref']=str(location_ref)
    rows[cid]=row
    if isinstance(index,dict):
        for p in people: index[p]=cid
    return out

def extend_commitment_resources(state:Mapping[str,Any],*,activity_ref:str,resources:Sequence[tuple[str,str,str]])->dict[str,Any]:
    """Add newly mobilized exact resources to one existing active activity."""
    out=copy.deepcopy(dict(state)); rows=out.setdefault('commitments',{}); index=out.setdefault('person_index',{})
    if not isinstance(rows,dict) or not isinstance(index,dict): raise ValueError('commitments invalid')
    cid=f'commitment:{activity_ref}'; row=rows.get(cid)
    if not isinstance(row,dict) or row.get('status','active')!='active': raise ValueError('active commitment not found')
    used=_active_resources(out)
    existing={(str(r.get('kind')),str(r.get('ref'))) for r in row.get('resources',[]) if isinstance(r,Mapping) and isinstance(r.get('kind'),str) and isinstance(r.get('ref'),str)}
    material=row.setdefault('resources',[]); people=row.setdefault('person_refs',[])
    if not isinstance(material,list) or not isinstance(people,list): raise ValueError('commitment resources invalid')
    for kind,ref,res_owner in resources:
        key=(str(kind),str(ref))
        if key in existing: continue
        if key in used: raise ValueError(f'resource already committed:{key[0]}:{key[1]}')
        material.append({'kind':key[0],'ref':key[1],'owner_ref':str(res_owner)}); existing.add(key); used.add(key)
        if key[0]=='person':
            if key[1] not in people: people.append(key[1])
            index[key[1]]=cid
    return out

def remove_people_from_commitments(state:Mapping[str,Any],*,person_refs:Sequence[str])->dict[str,Any]:
    """Remove unavailable/dead people from all current reservations without deleting other resources."""
    out=copy.deepcopy(dict(state)); rows=out.setdefault('commitments',{}); index=out.setdefault('person_index',{})
    if not isinstance(rows,dict) or not isinstance(index,dict): raise ValueError('commitments invalid')
    removed={str(x) for x in person_refs if isinstance(x,str) and x}
    if not removed:return out
    for cid,raw in list(rows.items()):
        if not isinstance(raw,Mapping):continue
        people=[str(x) for x in raw.get('person_refs',[]) if isinstance(x,str)] if isinstance(raw.get('person_refs'),list) else []
        if not any(ref in removed for ref in people):continue
        row=copy.deepcopy(dict(raw)); resources=row.get('resources',[]) if isinstance(row.get('resources'),list) else []
        row['resources']=[r for r in resources if not (isinstance(r,Mapping) and r.get('kind')=='person' and str(r.get('ref')) in removed)]
        row['person_refs']=[ref for ref in people if ref not in removed]
        for ref in people:
            if ref in removed and index.get(ref)==cid:index.pop(ref,None)
        has_nonperson=any(isinstance(r,Mapping) and r.get('kind')!='person' for r in row['resources'])
        if not row['person_refs'] and not has_nonperson:rows.pop(cid,None)
        else:rows[cid]=row
    return out

def release_resources(state:Mapping[str,Any],*,activity_ref:str)->dict[str,Any]:
    out=copy.deepcopy(dict(state)); rows=out.setdefault('commitments',{}); index=out.setdefault('person_index',{}); cid=f'commitment:{activity_ref}'
    row=rows.pop(cid,None)
    if isinstance(row,Mapping) and isinstance(index,dict):
        for p in row.get('person_refs',[]):
            if index.get(p)==cid: index.pop(p,None)
    return out

__all__=['extend_commitment_resources','release_resources','remove_people_from_commitments','reserve_resources']
