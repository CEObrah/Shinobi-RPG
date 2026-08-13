from __future__ import annotations
import hashlib
from typing import Any, Dict, Mapping, Optional
from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.world_front_rules import front_phase


def _add(values:list[str], value:object)->None:
    if isinstance(value,str) and value and value not in values: values.append(value)


def _seen(event:Mapping[str,Any], player_ref:str)->bool:
    vis=event.get("visibility") if isinstance(event.get("visibility"),Mapping) else {}
    return any(isinstance(vis.get(key),list) and player_ref in vis[key] for key in ("audience_refs","witness_refs"))


def apply_evidence(*,registry:Dict[str,Any],rules:Mapping[str,Any],action:Mapping[str,Any],event:Mapping[str,Any],player_ref:str)->Optional[Mapping[str,Any]]:
    kind,event_id=action.get("kind"),event.get("id")
    if not isinstance(kind,str) or not isinstance(event_id,str) or action.get("skipped") is not None:return None
    material=rules.get("material_action_kinds")
    if not isinstance(material,list) or kind not in material:return None
    fronts,pressures=rules.get("fronts"),registry.get("pressures");hosts=event.get("host_refs")
    if not isinstance(fronts,Mapping) or not isinstance(pressures,dict):raise CommandRejectedError("world_front_policy_invalid")
    if not isinstance(hosts,list):return None
    wanted=action.get("world_front_ref")
    if wanted is not None and (not isinstance(wanted,str) or wanted not in fronts):return None
    matches=[]
    for front_id,config in sorted(fronts.items()):
        if wanted is not None and front_id!=wanted:continue
        pressure=pressures.get(front_id);roles=config.get("faction_roles") if isinstance(config,Mapping) else None
        if not isinstance(pressure,dict) or not isinstance(roles,Mapping) or front_phase(pressure,rules)=="resolved":continue
        faction=next((ref for ref in hosts if isinstance(ref,str) and roles.get(ref) in ("source","opposition")),None)
        if faction is not None:matches.append((front_id,pressure,faction,str(roles[faction])))
    if len(matches)!=1:return None
    front_id,pressure,faction,role=matches[0]
    evidence,actors,opposition,chronology=(pressure.get(key) for key in ("evidence_refs","actors","opposition","chronology"))
    if not all(isinstance(value,list) for value in (evidence,actors,opposition,chronology)):raise CommandRejectedError("canon_pressure_registry_invalid")
    if event_id in evidence:return None
    before_status,before_phase=pressure.get("status"),front_phase(pressure,rules);_add(evidence,event_id)
    event_actors=event.get("actor_refs") if isinstance(event.get("actor_refs"),list) else []
    if role=="source":
        for ref in event_actors:_add(actors,ref)
    else:
        _add(opposition,faction)
        for ref in event_actors:_add(opposition,ref)
    pressure["current_step"]=f"{kind}:{event_id}"
    timing=event.get("timing") if isinstance(event.get("timing"),Mapping) else {};at=timing.get("occurred_at") or timing.get("scheduled_for")
    if not isinstance(at,str):raise CommandRejectedError("world_front_event_time_invalid")
    digest=hashlib.sha256(f"{front_id}\x00{event_id}".encode()).hexdigest()[:20]
    chronology.append({"entry_id":f"front_history.{front_id.removeprefix('pressure_')}.{digest}","at":at,"kind":"committed_domain_evidence","status_before":before_status,"status_after":pressure.get("status"),"source_refs":[event_id,faction]})
    return {"front_id":front_id,"phase_before":before_phase,"phase_after":front_phase(pressure,rules),"event_ref":event_id,"action_kind":kind,"player_visible":_seen(event,player_ref)}


__all__=["apply_evidence"]
