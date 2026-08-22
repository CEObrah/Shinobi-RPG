"""Deterministic public-site attendance without persistent daily schedules.

Attendance is a read-time projection over existing persistent people. It never
teleports a person between settlements and never creates a new identity or a
relationship merely because two people share a venue.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Mapping, Sequence

_PUBLIC_SITE_BASE = {
    "inn": 380,
    "tea_house": 330,
    "wine_shop": 260,
    "market": 320,
    "caravan_yard": 220,
    "guild_hall": 180,
    "temple": 130,
    "clinic": 90,
    "government_office": 70,
    "magistrate_office": 70,
    "tournament_ground": 120,
    "gambling_house": 100,
    "stable": 120,
}


def _stable_milli(*parts: object) -> int:
    raw="|".join(str(x) for x in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:4],"big") % 1000


def _site_parent(site: Mapping[str,Any]) -> str:
    return str(site.get("parent_place_ref") or "")


def person_settlement(person: Mapping[str,Any], *, faction_headquarters: str, sites: Mapping[str,Any]) -> str:
    loc=str(person.get("location_ref") or "")
    if loc:
        site=sites.get(loc)
        if isinstance(site,Mapping):
            return _site_parent(site)
        if not loc.startswith("site."):
            return loc
    return str(person.get("home_place_ref") or faction_headquarters or "")


def attendance_threshold_milli(person: Mapping[str,Any], *, site_type: str, hour: int) -> int:
    base=max(0,int(_PUBLIC_SITE_BASE.get(site_type,0)))
    if base<=0:return 0
    allowed=person.get("public_site_types")
    if isinstance(allowed,list) and allowed and site_type not in {str(x) for x in allowed}: return 0
    offices={str(x).split(":",1)[0] for x in person.get("standing_offices",[]) if isinstance(x,str)}
    martial=person.get("martial_skills",{}) if isinstance(person.get("martial_skills"),Mapping) else {}
    prof=person.get("professional_skills",{}) if isinstance(person.get("professional_skills"),Mapping) else {}
    if site_type in {"inn","wine_shop","gambling_house"}:
        time_factor=1250 if hour>=17 or hour<2 else 650
    elif site_type in {"market","government_office","magistrate_office","guild_hall","clinic"}:
        time_factor=1150 if 7<=hour<18 else 250
    elif site_type=="tea_house":
        time_factor=1150 if 8<=hour<21 else 350
    else:
        time_factor=1000 if 6<=hour<22 else 450
    role=1000
    if offices & {"emperor"}: role=350 if site_type in {"government_office","temple"} else 0
    elif offices & {"prince","princess","empress"}: role=600 if site_type in {"government_office","temple","tournament_ground"} else 280
    elif offices & {"grand_minister","imperial_minister","imperial_marshal","magistrate"}: role=1450 if site_type in {"government_office","magistrate_office"} else 550
    elif offices & {"merchant_head","noble_head","noble_family"}: role=1250 if site_type in {"market","guild_hall","tea_house"} else 800
    elif site_type in {"government_office","magistrate_office"} and offices & {"leader","deputy_leader","chief_steward","treasurer"}: role=1450
    elif site_type=="clinic" and int(prof.get("medicine",0))>=50: role=1450
    elif site_type in {"market","guild_hall","caravan_yard"} and int(prof.get("commerce",0))>=40: role=1350
    elif site_type=="tournament_ground" and max((int(v) for k,v in martial.items() if k in {"sword","spear","bow","hidden_weapons","unarmed"}),default=0)>=65: role=1350
    elif site_type in {"inn","tea_house","wine_shop"} and offices & {"leader","deputy_leader"}: role=850
    return min(900,base*time_factor*role//1_000_000)


def person_attends_site(
    person: Mapping[str,Any], *, site_ref: str, site: Mapping[str,Any], faction_headquarters: str,
    sites: Mapping[str,Any], at: datetime, unavailable_refs: set[str]|frozenset[str]=frozenset(),
) -> bool:
    pid=str(person.get("person_id") or "")
    if not pid or pid in unavailable_refs:return False
    health=person.get("health",{}) if isinstance(person.get("health"),Mapping) else {}
    if health.get("status") in {"dead","incapacitated"}:return False
    parent=_site_parent(site)
    if not parent or person_settlement(person,faction_headquarters=faction_headquarters,sites=sites)!=parent:return False
    site_type=str(site.get("site_type") or "")
    threshold=attendance_threshold_milli(person,site_type=site_type,hour=at.hour)
    if threshold<=0:return False
    block=at.hour//6
    return _stable_milli(pid,site_ref,at.date().isoformat(),block) < threshold


def derived_site_attendance(
    *, site_ref: str, site: Mapping[str,Any], faction_people: Sequence[tuple[str,Sequence[Mapping[str,Any]]]],
    faction_headquarters: Mapping[str,str], sites: Mapping[str,Any], at: datetime,
    unavailable_refs: set[str]|frozenset[str]=frozenset(), exclude_refs: set[str]|frozenset[str]=frozenset(), limit: int|None=None,
    civic_people: Sequence[Mapping[str,Any]] = (),
) -> list[str]:
    """Return everyone who deterministically attends this site.

    ``limit`` is optional transport trimming only. Attendance itself has no
    fictional population cap.
    """
    rows=[]
    for faction_ref,people in faction_people:
        hq=str(faction_headquarters.get(faction_ref) or "")
        for person in people:
            if not isinstance(person,Mapping):continue
            pid=str(person.get("person_id") or "")
            if not pid or pid in exclude_refs:continue
            if person_attends_site(person,site_ref=site_ref,site=site,faction_headquarters=hq,sites=sites,at=at,unavailable_refs=unavailable_refs):
                grade=str(person.get("membership_grade") or "")
                standing={"elder":5,"elite":4,"senior":3,"full":2,"junior":1}.get(grade,0)
                rows.append((-standing,pid))
    for person in civic_people:
        if not isinstance(person,Mapping): continue
        pid=str(person.get("person_id") or "")
        if not pid or pid in exclude_refs: continue
        if person_attends_site(person,site_ref=site_ref,site=site,faction_headquarters=str(person.get("home_place_ref") or ""),sites=sites,at=at,unavailable_refs=unavailable_refs):
            rank={"imperial":8,"high_official":7,"noble":6,"regional_official":5,"local_elite":4}.get(str(person.get("social_rank") or ""),3)
            rows.append((-rank,pid))
    rows.sort()
    result=[pid for _standing,pid in rows]
    return result if limit is None else result[:max(0,int(limit))]


__all__=["attendance_threshold_milli","derived_site_attendance","person_attends_site","person_settlement"]
