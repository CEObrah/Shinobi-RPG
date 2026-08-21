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

def release_resources(state:Mapping[str,Any],*,activity_ref:str)->dict[str,Any]:
    out=copy.deepcopy(dict(state)); rows=out.setdefault('commitments',{}); index=out.setdefault('person_index',{}); cid=f'commitment:{activity_ref}'
    row=rows.pop(cid,None)
    if isinstance(row,Mapping) and isinstance(index,dict):
        for p in row.get('person_refs',[]):
            if index.get(p)==cid: index.pop(p,None)
    return out
