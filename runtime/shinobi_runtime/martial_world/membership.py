"""Membership-grade, office and succession calculations without stat-granting promotions."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'
def _structure(): return json.loads((_MW/'faction-structure.json').read_text())
def grade_eligibility(person:Mapping[str,Any],*,target_grade:str,service_days:int,primary_discipline:str,discipline_clean:bool=True,elder_open_seat:bool=True)->dict[str,Any]:
    row=next((r for r in _structure()['membership_grades'] if r['id']==target_grade),None)
    if row is None: raise KeyError(target_grade)
    reasons=[]; ms=int(person.get('martial_skills',{}).get(primary_discipline,0)); qi=int(person.get('qi') or 0); qc=int(person.get('qi_control') or 0)
    if service_days<int(row.get('minimum_service_days',0)): reasons.append('service')
    if ms<int(row.get('minimum_primary_discipline',0)): reasons.append('primary_discipline')
    if qi<int(row.get('minimum_qi',0)): reasons.append('qi')
    if qc<int(row.get('minimum_qi_control',0)): reasons.append('qi_control')
    if not discipline_clean: reasons.append('discipline_status')
    if row.get('seat_limited') and not elder_open_seat: reasons.append('seat_capacity')
    return {'eligible':not reasons,'reasons':reasons,'target_grade':target_grade,'stat_changes':{}}
def office_candidate_score(person:Mapping[str,Any],*,relevant_skill:int,service_days:int,trust:int)->int:
    weights=json.loads((_MW/'membership-politics.json').read_text())['office_candidate_weights']
    leadership=int(person.get('aptitudes',{}).get('leadership',100)); admin=int(person.get('professional_skills',{}).get('administration',0)); instr=int(person.get('professional_skills',{}).get('instruction',0))
    service=min(200,max(0,int(service_days))//30)
    return (
        int(relevant_skill)*int(weights.get('relevant_skill_milli',400))
        + leadership*int(weights.get('leadership_milli',200))
        + admin*int(weights.get('administration_milli',150))
        + instr*int(weights.get('instruction_milli',100))
        + service*int(weights.get('service_milli',100))
        + int(trust)*int(weights.get('trust_milli',50))
    )//100
def select_office_candidate(candidates:Sequence[Mapping[str,Any]],*,relevant_skill_key:str,service_days:Mapping[str,int],trust:Mapping[str,int])->str|None:
    rows=[]
    for p in candidates:
        pid=str(p['person_id']); relevant=max(int(p.get('martial_skills',{}).get(relevant_skill_key,0)),int(p.get('professional_skills',{}).get(relevant_skill_key,0)))
        rows.append((office_candidate_score(p,relevant_skill=relevant,service_days=int(service_days.get(pid,0)),trust=int(trust.get(pid,0))),pid))
    return sorted(rows,key=lambda x:(-x[0],x[1]))[0][1] if rows else None
