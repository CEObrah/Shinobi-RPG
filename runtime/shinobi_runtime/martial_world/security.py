"""Deterministic physical security for Jianghu compounds.

Security is built from actual walls plus actual available people.  Watch posts
are positions, never free guards.  Read-time shifts are derived from stable
identity/time inputs so the campaign does not store hourly schedules.
"""
from __future__ import annotations
import hashlib
import math
from datetime import datetime
from typing import Any, Mapping, Sequence

from .infrastructure import facility_physical_effects
from .manpower import combat_eligible
from .health import functional_capacity_factors


def _stable(ref: str) -> int:
    return int(hashlib.sha256(ref.encode('utf-8')).hexdigest()[:16],16)


def watch_requirement(buildings: Mapping[str,Any], *, infrastructure:Mapping[str,Any]|None=None, hour: int, threat_milli: int=500) -> int:
    positions=max(0,int(facility_physical_effects(buildings,infrastructure,'walls_gate').get('watch_positions',0)))
    if positions<=0:return 0
    night=hour<6 or hour>=20
    base=320 if night else 220
    posture=min(1000,base+max(0,int(threat_milli))//3)
    return min(positions,max(1,(positions*posture+999)//1000))


def _eligible_guard(person: Mapping[str,Any], unavailable_refs:set[str], *, year:int) -> bool:
    return combat_eligible(
        person, year=year, unavailable_refs=unavailable_refs, minimum_age=14, minimum_combat_skill=20,
    )


def select_watch_guards(
    people: Sequence[Mapping[str,Any]], *, faction_ref:str, at:datetime,
    buildings:Mapping[str,Any], infrastructure:Mapping[str,Any]|None=None, unavailable_refs:set[str]|frozenset[str]=frozenset(),
    threat_milli:int=500,
) -> list[dict[str,Any]]:
    """Select existing guards deterministically for one four-hour shift."""
    unavailable=set(map(str,unavailable_refs))
    required=watch_requirement(buildings,infrastructure=infrastructure,hour=at.hour,threat_milli=threat_milli)
    shift=at.hour//4
    rows=[]
    for person in people:
        if not isinstance(person,Mapping) or not _eligible_guard(person,unavailable,year=at.year):continue
        ref=str(person['person_id'])
        attrs=person.get('attributes',{}) if isinstance(person.get('attributes'),Mapping) else {}
        martial=person.get('martial_skills',{}) if isinstance(person.get('martial_skills'),Mapping) else {}
        perception=max(0,int(attrs.get('perception',0)))
        scout=max(0,int(martial.get('stealth_scouting',0)))
        guard_skill=(perception*3+scout)//4
        rotation=_stable(f'{faction_ref}|{at.date().isoformat()}|{shift}|{ref}')%1_000_000
        # Competence matters, but stable rotation prevents the same elite from
        # becoming an invisible permanent sentry every day.
        rows.append((-guard_skill,rotation,ref,dict(person)))
    rows.sort(key=lambda r:(r[0],r[1],r[2]))
    return [r[3] for r in rows[:required]]


def institutional_guard_duty_milli(buildings:Mapping[str,Any], *, infrastructure:Mapping[str,Any]|None=None, eligible_guard_count:int, threat_milli:int=500) -> int:
    """Average weekly training-time share consumed by real watch duty.

    This is a compact cohort duty share. Immediate scenes still select exact
    guards with :func:`select_watch_guards`. No hourly history is persisted.
    """
    guards=max(0,int(eligible_guard_count))
    if guards<=0:return 0
    # Average the day and night complement across six four-hour shifts.
    required=sum(watch_requirement(buildings,infrastructure=infrastructure,hour=h,threat_milli=threat_milli) for h in (2,6,10,14,18,22))
    # required is guard-shifts/day. Each shift is four hours. Spread over the
    # real eligible pool; cap at 60% to avoid a security posture consuming an
    # impossible amount of every person's life without explicit mobilization.
    hours_per_guard_week=required*4*7/max(1,guards)
    return min(600,max(0,int(round(hours_per_guard_week/112*1000))))


def infiltration_resolution(*, intruder:Mapping[str,Any], guards:Sequence[Mapping[str,Any]], buildings:Mapping[str,Any], infrastructure:Mapping[str,Any]|None=None,
                            lighting_milli:int=700, weather_visibility_milli:int=1000,
                            concealment_milli:int=0) -> dict[str,Any]:
    attrs=intruder.get('attributes',{}) if isinstance(intruder.get('attributes'),Mapping) else {}
    skills=intruder.get('martial_skills',{}) if isinstance(intruder.get('martial_skills'),Mapping) else {}
    stealth=max(0,int(skills.get('stealth_scouting',0))); dex=max(0,int(attrs.get('dexterity',0))); strength=max(0,int(attrs.get('strength',0))); perception=max(0,int(attrs.get('perception',0)))
    health=intruder.get('health',{}) if isinstance(intruder.get('health'),Mapping) else {}
    wounds=health.get('injuries',[]) if isinstance(health.get('injuries'),list) else []
    body=functional_capacity_factors([row for row in wounds if isinstance(row,Mapping)])
    climb_function=max(0,min(1000,int(body.get('climbing_milli',1000))))
    level=max(0,int(buildings.get('walls_gate',0))); wall=facility_physical_effects(buildings,infrastructure,'walls_gate')
    climb=max(0,int(wall.get('climb_difficulty_milli',0))); height=max(1,int(wall.get('wall_height_m',1)))
    movement=(stealth*45+dex*25+strength*15+perception*15)//100
    movement=movement*climb_function//1000
    climb_penalty=max(0,(climb-700)//8)+height*3
    intrusion_score=max(0,movement-climb_penalty)
    observer_scores=[]
    for guard in guards:
        ga=guard.get('attributes',{}) if isinstance(guard.get('attributes'),Mapping) else {}
        gm=guard.get('martial_skills',{}) if isinstance(guard.get('martial_skills'),Mapping) else {}
        observer_scores.append((max(0,int(ga.get('perception',0)))*70+max(0,int(gm.get('stealth_scouting',0)))*30)//100)
    best_observer=max(observer_scores,default=0)
    visibility=max(100,min(1400,int(lighting_milli)*max(100,int(weather_visibility_milli))//1000))
    detection_score=best_observer*visibility//1000 + max(0,1000-int(concealment_milli))//25 + level*5
    success=intrusion_score>detection_score
    margin=intrusion_score-detection_score
    climb_seconds=max(20,int(height*1200/max(20,30+dex+strength//2))*1000//max(100,climb_function))
    return {'success':success,'intrusion_score':intrusion_score,'detection_score':detection_score,'margin':margin,'climb_seconds':climb_seconds,'guard_refs':[str(g.get('person_id')) for g in guards if isinstance(g.get('person_id'),str)]}


def forced_entry_resolution(*, intruder:Mapping[str,Any], buildings:Mapping[str,Any], infrastructure:Mapping[str,Any]|None=None, weapon_impact:int=0) -> dict[str,Any]:
    attrs=intruder.get('attributes',{}) if isinstance(intruder.get('attributes'),Mapping) else {}
    strength=max(0,int(attrs.get('strength',0)))
    health=intruder.get('health',{}) if isinstance(intruder.get('health'),Mapping) else {}
    wounds=health.get('injuries',[]) if isinstance(health.get('injuries'),list) else []
    labor=max(0,min(1000,int(functional_capacity_factors([row for row in wounds if isinstance(row,Mapping)]).get('labor_milli',1000))))
    barrier=max(1,int(facility_physical_effects(buildings,infrastructure,'walls_gate').get('barrier_integrity',1)))
    work_rate=max(1,(strength*2+max(0,int(weapon_impact)))*max(100,labor)//1000)
    breach_seconds=max(30,math.ceil(barrier*20/work_rate))
    return {'barrier_integrity':barrier,'work_rate':work_rate,'breach_seconds':breach_seconds}


def alarm_response_seconds(buildings:Mapping[str,Any], guards:Sequence[Mapping[str,Any]], infrastructure:Mapping[str,Any]|None=None) -> int:
    perimeter=max(50,int(facility_physical_effects(buildings,infrastructure,'walls_gate').get('defended_perimeter_m',50)))
    speeds=[]
    for guard in guards:
        attrs=guard.get('attributes',{}) if isinstance(guard.get('attributes'),Mapping) else {}
        health=guard.get('health',{}) if isinstance(guard.get('health'),Mapping) else {}
        wounds=health.get('injuries',[]) if isinstance(health.get('injuries'),list) else []
        running=max(0,min(1000,int(functional_capacity_factors([row for row in wounds if isinstance(row,Mapping)]).get('running_milli',1000))))
        speeds.append(max(1,int(attrs.get('speed',0))*max(50,running)//1000))
    speed=max(speeds,default=40)
    return max(15,int(perimeter*5/max(20,speed)))

__all__=['alarm_response_seconds','forced_entry_resolution','infiltration_resolution','institutional_guard_duty_milli','select_watch_guards','watch_requirement']
