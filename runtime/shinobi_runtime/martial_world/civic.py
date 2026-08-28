"""Exact compact civic-person authority outside martial-faction population.

Named court, government, noble and regional-elite identities live here so the
martial faction population remains conserved. Government actions themselves
remain owned by the existing government authority.
"""
from __future__ import annotations
import copy
from typing import Any, Mapping

_CIVIC_PATH = "state/martial-world/civic-people.json"


def _healthy() -> dict[str, Any]:
    return {"status":"ready","injuries":[],"toxicity_milli":0,"blood_lost_ml":0,"shock":0,"consciousness":100}


def hydrate_civic_person(person: Mapping[str, Any]) -> dict[str, Any]:
    out=copy.deepcopy(dict(person))
    out["martial_skills"]={str(k):max(0,int(v)) for k,v in (out.get("martial_skills",{}) or {}).items()}
    out["professional_skills"]={str(k):max(0,int(v)) for k,v in (out.get("professional_skills",{}) or {}).items()}
    out["qi"]=max(0,int(out.get("qi",0))); out["qi_control"]=max(0,int(out.get("qi_control",0)))
    cap_milli=out["qi"]*1000; milli=out.get("current_qi_milli")
    if milli is None: milli=max(0,int(out.get("current_qi",out["qi"])))*1000
    milli=max(0,min(cap_milli,int(milli))); out["current_qi_milli"]=milli; out["current_qi"]=milli//1000
    health=_healthy(); raw=out.get("health")
    if isinstance(raw,Mapping): health.update(copy.deepcopy(dict(raw)))
    out["health"]=health; out["fatigue_milli"]=max(0,int(out.get("fatigue_milli",0)))
    out.setdefault("standing_offices",[])
    out.setdefault("location_ref",out.get("home_place_ref"))
    return out


def compact_civic_person(person: Mapping[str, Any]) -> dict[str, Any]:
    out=copy.deepcopy(dict(person)); qi=max(0,int(out.get("qi",0))); cap_milli=qi*1000
    milli=out.get("current_qi_milli")
    if milli is None: milli=max(0,int(out.get("current_qi",qi)))*1000
    milli=max(0,min(cap_milli,int(milli))); coarse=milli//1000
    if milli==cap_milli: out.pop("current_qi",None); out.pop("current_qi_milli",None)
    else:
        out["current_qi"]=coarse
        if milli==coarse*1000: out.pop("current_qi_milli",None)
        else: out["current_qi_milli"]=milli
    burdens=out.get("poison_burdens")
    if isinstance(burdens,Mapping):
        burdens={str(k):max(0,int(v)) for k,v in burdens.items() if max(0,int(v))>0}
        if burdens: out["poison_burdens"]=burdens
        else: out.pop("poison_burdens",None)
    else: out.pop("poison_burdens",None)
    pending=out.get("pending_poison_burdens")
    if isinstance(pending,Mapping):
        rows={}
        for k,v in pending.items():
            if not isinstance(v,Mapping):
                continue
            burden=max(0,int(v.get("burden",0))); activates_at=v.get("activates_at")
            if burden<=0 or not isinstance(activates_at,str) or not activates_at:
                continue
            row={"burden":burden,"activates_at":str(activates_at)}
            poison_ref=v.get("poison_ref")
            if isinstance(poison_ref,str) and poison_ref: row["poison_ref"]=poison_ref
            peaks_at=v.get("peaks_at")
            if isinstance(peaks_at,str) and peaks_at: row["peaks_at"]=peaks_at
            stage=v.get("stage")
            if stage in {"onset","peak"}: row["stage"]=str(stage)
            rows[str(k)]=row
        if rows: out["pending_poison_burdens"]=rows
        else: out.pop("pending_poison_burdens",None)
    else: out.pop("pending_poison_burdens",None)
    if int(out.get("fatigue_milli",0))==0: out.pop("fatigue_milli",None)
    h=out.get("health")
    if isinstance(h,Mapping):
        h=copy.deepcopy(dict(h))
        defaults=_healthy()
        for k,v in defaults.items():
            if h.get(k)==v: h.pop(k,None)
        if h: out["health"]=h
        else: out.pop("health",None)
    if out.get("location_ref")==out.get("home_place_ref"): out.pop("location_ref",None)
    for section in ("martial_skills","professional_skills"):
        raw=out.get(section,{}) if isinstance(out.get(section),Mapping) else {}
        out[section]={str(k):int(v) for k,v in raw.items() if int(v)>0}
    return out


def civic_person(repository: Any, person_ref: str) -> tuple[str, dict[str, Any], int, dict[str, Any]]:
    roster=copy.deepcopy(repository.read_json(_CIVIC_PATH))
    rows=roster.get("people",[]) if isinstance(roster,Mapping) else []
    if not isinstance(rows,list): raise ValueError("jianghu civic people invalid")
    for i,row in enumerate(rows):
        if isinstance(row,Mapping) and row.get("person_id")==person_ref:
            return _CIVIC_PATH,roster,i,hydrate_civic_person(row)
    raise KeyError(person_ref)


def set_civic_person(roster: Mapping[str, Any], ordinal: int, person: Mapping[str, Any]) -> dict[str, Any]:
    out=copy.deepcopy(dict(roster)); rows=out.get("people")
    if out.get("schema")!="jianghu-civic-people-state-1.0" or not isinstance(rows,list) or ordinal<0 or ordinal>=len(rows):
        raise ValueError("jianghu civic roster invalid")
    rows[ordinal]=compact_civic_person(person); return out


def civic_people(repository: Any) -> list[dict[str, Any]]:
    data=repository.read_json(_CIVIC_PATH); rows=data.get("people",[]) if isinstance(data,Mapping) else []
    return [hydrate_civic_person(row) for row in rows if isinstance(row,Mapping)] if isinstance(rows,list) else []


__all__=["civic_person","civic_people","compact_civic_person","hydrate_civic_person","set_civic_person"]
