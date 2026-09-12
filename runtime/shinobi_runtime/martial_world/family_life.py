"""Deterministic courtship/marriage/birth eligibility and child identity construction."""
from __future__ import annotations
import hashlib, json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from .life_course import inherited_appearance, inherited_aptitudes
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'
def _cfg(): return json.loads((_MW/'family-life.json').read_text())
def courtship_eligible(*,age_a:int,age_b:int,affection_ab:int,affection_ba:int,trust_ab:int,trust_ba:int)->bool:
    c=_cfg()['courtship']; return min(age_a,age_b)>=int(c['minimum_age']) and min(affection_ab,affection_ba)>=int(c['minimum_mutual_affection']) and min(trust_ab,trust_ba)>=int(c['minimum_mutual_trust'])
def marriage_eligible(*,age_a:int,age_b:int,mutual_consent:bool,relationship_stage:str)->bool:
    c=_cfg()['marriage']; return min(age_a,age_b)>=int(c['minimum_age']) and bool(mutual_consent) and relationship_stage in {'courtship','betrothed','arranged_with_consent'}
def due_birth_at(*,conception_at:str)->str:
    dt=datetime.fromisoformat(conception_at); return (dt+timedelta(days=int(_cfg()['birth']['gestation_days']))).isoformat()
def child_identity(*,child_id:str,parent_a:Mapping[str,Any],parent_b:Mapping[str,Any],birth_at:str,sex:str)->dict[str,Any]:
    if sex not in {'male','female'}: raise ValueError('sex')
    return {'person_id':child_id,'birth_at':birth_at,'sex':sex,'appearance':inherited_appearance(int(parent_a['appearance']),int(parent_b['appearance']),child_id=child_id),'aptitudes':inherited_aptitudes(parent_a['aptitudes'],parent_b['aptitudes'],child_id=child_id),'parent_refs':[parent_a['person_id'],parent_b['person_id']]}
