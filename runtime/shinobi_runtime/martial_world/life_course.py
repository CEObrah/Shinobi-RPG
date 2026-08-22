"""Deterministic aging and inheritance for the Jianghu world."""
from __future__ import annotations
import hashlib, json, math
from pathlib import Path
from typing import Mapping
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'
def _data(): return json.loads((_MW/'life-course.json').read_text(encoding='utf-8'))
def _offset(key:str,limit:int)->int:
    v=int.from_bytes(hashlib.sha256(key.encode()).digest()[:8],'big'); return (v%(2*limit+1))-limit

def effective_cultivation(qi:int,qi_control:int)->float:
    q=max(0,qi); c=max(0,qi_control)
    return 0.0 if q+c==0 else 2.0*q*c/(q+c)
def biological_aging_rate(qi:int,qi_control:int)->float:
    return 1.0/math.sqrt(1.0+effective_cultivation(qi,qi_control)/100.0)
def natural_lifespan_years(*,person_id:str,qi:int,qi_control:int,health_milli:int=1000)->int:
    d=_data(); base=int(d['base_natural_lifespan_years'])+_offset(person_id+':life',int(d['identity_variation_years']))
    rate=biological_aging_rate(qi,qi_control); health=max(300,min(1200,health_milli))/1000.0
    return max(25,int(round(base*health/max(0.2,rate))))
def inherited_aptitudes(parent_a:Mapping[str,int],parent_b:Mapping[str,int],*,child_id:str)->dict[str,int]:
    d=_data()['aptitude_inheritance']; out={}
    for key in sorted(set(parent_a)|set(parent_b)):
        mean=(int(parent_a.get(key,100))+int(parent_b.get(key,100)))//2
        val=(mean*int(d['parent_mean_weight_milli'])+int(d['population_mean'])*int(d['population_regression_weight_milli']))//1000
        val+=_offset(child_id+':apt:'+key,int(d['identity_variation_points']))
        out[key]=max(int(d['minimum']),min(int(d['maximum']),val))
    return out
def inherited_appearance(parent_a:int,parent_b:int,*,child_id:str)->int:
    d=_data()['appearance_inheritance']; mean=(int(parent_a)+int(parent_b))//2
    val=(mean*int(d['parent_mean_weight_milli'])+int(d['population_mean'])*int(d['population_regression_weight_milli']))//1000
    val+=_offset(child_id+':appearance',int(d['identity_variation_points']))
    return max(int(d['minimum']),min(int(d['maximum']),val))
