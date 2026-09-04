"""Capability-gap driven party/retinue composition without creating people."""
from __future__ import annotations
from typing import Any, Mapping, Sequence

DIMS=('leadership','reconnaissance','melee','ranged','medicine','logistics','negotiation')

def capability_vector(person:Mapping[str,Any])->dict[str,int]:
    sk=person.get('martial_skills',{}); prof=person.get('professional_skills',{}); attr=person.get('attributes',{})
    return {
      'leadership':int(sk.get('command',0)),
      'reconnaissance':(int(sk.get('stealth_scouting',0))*2+int(attr.get('perception',0)))//3,
      'melee':max(int(sk.get('sword',0)),int(sk.get('spear',0)),int(sk.get('unarmed',0))),
      'ranged':int(sk.get('bow',0)),
      'medicine':int(prof.get('medicine',0)),
      'logistics':(int(prof.get('administration',0))+int(prof.get('commerce',0)))//2,
      'negotiation':(int(prof.get('commerce',0))+int(attr.get('intelligence',0))+int(attr.get('willpower',0)))//3,
    }

def select_party(candidates:Sequence[Mapping[str,Any]],*,existing:Sequence[Mapping[str,Any]]=(),slots:int,required:Mapping[str,int]|None=None)->list[str]:
    if slots<0: raise ValueError('slots invalid')
    required={d:int((required or {}).get(d,100)) for d in DIMS}
    current={d:0 for d in DIMS}
    for p in existing:
        v=capability_vector(p)
        for d in DIMS: current[d]=max(current[d],v[d])
    remaining=[p for p in candidates if isinstance(p.get('person_id'),str)]
    selected=[]
    for _ in range(min(slots,len(remaining))):
        best=None
        for p in remaining:
            vec=capability_vector(p); gain=0
            for d in DIMS:
                gap=max(0,required[d]-current[d]); gain += min(gap,max(0,vec[d]-current[d]))
            key=(gain,sum(vec.values()),p['person_id'])
            if best is None or key>(best[0],best[1],best[2]): best=(gain,sum(vec.values()),p['person_id'],p,vec)
        assert best is not None
        p=best[3]; vec=best[4]; selected.append(p['person_id']); remaining.remove(p)
        for d in DIMS: current[d]=max(current[d],vec[d])
    return selected
