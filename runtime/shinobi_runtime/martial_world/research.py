"""Bounded deterministic research over records a faction actually possesses."""
from __future__ import annotations
from typing import Any, Mapping
from .infrastructure import building_quality_milli, library_capacity


def research_score(*, intelligence:int, relevant_skill:int, library_level:int, minutes:int) -> int:
    time_factor=min(1400,500+max(1,int(minutes))*900//120)
    quality=max(500,building_quality_milli('library_records',library_level))
    base=max(0,int(intelligence))*55//100+max(0,int(relevant_skill))*45//100
    return max(0,base*quality//1000*time_factor//1000)


def research_record(*, record_ref:str, catalog:Mapping[str,Any], held_refs:set[str], intelligence:int,
                    relevant_skill:int, library_level:int, infrastructure:Mapping[str,Any]|None=None, minutes:int=60) -> dict[str,Any]:
    if record_ref not in held_refs:raise ValueError('record not held')
    if library_capacity({'library_records':library_level},infrastructure)<=0:raise ValueError('library unavailable')
    row=catalog.get('records',{}).get(record_ref) if isinstance(catalog.get('records'),Mapping) else None
    if not isinstance(row,Mapping):raise KeyError(record_ref)
    score=research_score(intelligence=intelligence,relevant_skill=relevant_skill,library_level=library_level,minutes=minutes)
    facts=[str(x) for x in row.get('facts',[]) if isinstance(x,str)] if isinstance(row.get('facts'),list) else []
    if score<30:revealed=facts[:1]
    elif score<70:revealed=facts[:2]
    else:revealed=facts[:3]
    return {'record_ref':record_ref,'title':str(row.get('title') or record_ref),'topic':str(row.get('topic') or ''),'score':score,'facts':revealed,'complete_for_record':len(revealed)>=len(facts)}

__all__=['research_record','research_score']
