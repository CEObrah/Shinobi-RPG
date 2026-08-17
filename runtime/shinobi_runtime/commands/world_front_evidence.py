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


def _public(event:Mapping[str,Any])->bool:
    vis=event.get("visibility") if isinstance(event.get("visibility"),Mapping) else {}
    return vis.get("classification") == "public"


def _resource_like(ref: object) -> bool:
    if not isinstance(ref, str) or not ref:
        return False
    prefixes=(
        "stock.","treasury.","account:","finance:","funding_gap:","mission.","research.","case.","security.",
        "shipment.","trade.contract.","jurisdiction.","settlement.","project.","asset.","item_","market_",
    )
    return ref.startswith(prefixes)


def _record_evidence(
    *,
    pressure: Dict[str, Any],
    rules: Mapping[str, Any],
    front_id: str,
    event: Mapping[str, Any],
    role: str,
    source_anchor: str,
    action_kind: str,
    player_ref: str,
    player_visibility: str,
) -> Optional[Mapping[str, Any]]:
    event_id=event.get("id")
    if not isinstance(event_id,str): return None
    evidence=pressure.get("evidence_refs"); actors=pressure.get("actors"); opposition=pressure.get("opposition")
    chronology=pressure.get("chronology"); source_refs=pressure.get("source_refs"); resources=pressure.get("resources")
    if not all(isinstance(value,list) for value in (evidence,actors,opposition,chronology,source_refs,resources)):
        raise CommandRejectedError("canon_pressure_registry_invalid")
    if event_id in evidence:return None
    before_status,before_phase=pressure.get("status"),front_phase(pressure,rules)
    _add(evidence,event_id)
    event_actors=event.get("actor_refs") if isinstance(event.get("actor_refs"),list) else []
    if role=="source":
        _add(source_refs,source_anchor)
        for ref in event_actors:_add(actors,ref)
    else:
        _add(opposition,source_anchor)
        for ref in event_actors:_add(opposition,ref)
    provenance=event.get("provenance") if isinstance(event.get("provenance"),Mapping) else {}
    for ref in provenance.get("source_refs",[]) if isinstance(provenance.get("source_refs"),list) else []:
        _add(source_refs,ref)
    for ref in event.get("material_consequence_refs",[]) if isinstance(event.get("material_consequence_refs"),list) else []:
        if _resource_like(ref):_add(resources,ref)
    pressure["current_step"]=f"{action_kind}:{event_id}"
    timing=event.get("timing") if isinstance(event.get("timing"),Mapping) else {};at=timing.get("occurred_at") or timing.get("scheduled_for")
    if not isinstance(at,str):raise CommandRejectedError("world_front_event_time_invalid")
    digest=hashlib.sha256(f"{front_id}\x00{event_id}".encode()).hexdigest()[:20]
    chronology.append({
        "entry_id":f"front_history.{front_id.removeprefix('pressure_')}.{digest}","at":at,
        "kind":"committed_domain_evidence","status_before":before_status,"status_after":pressure.get("status"),
        "source_refs":[event_id,source_anchor],
    })
    chronology.sort(key=lambda row:(str(row.get("at") or ""),str(row.get("entry_id") or "")))
    visible=_seen(event,player_ref) or (player_visibility=="public_or_knowledge" and _public(event))
    return {"front_id":front_id,"phase_before":before_phase,"phase_after":front_phase(pressure,rules),"event_ref":event_id,"action_kind":action_kind,"player_visible":visible}


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
        if faction is not None:matches.append((front_id,pressure,faction,str(roles[faction]),config))
    if len(matches)!=1:return None
    front_id,pressure,faction,role,config=matches[0]
    return _record_evidence(
        pressure=pressure,rules=rules,front_id=front_id,event=event,role=role,source_anchor=faction,
        action_kind=kind,player_ref=player_ref,player_visibility=str(config.get("player_visibility") or "knowledge_only"),
    )


def _event_source_matches(source: Mapping[str, Any], event: Mapping[str, Any]) -> bool:
    kind=event.get("kind")
    kinds=source.get("event_kinds")
    if not isinstance(kind,str) or not isinstance(kinds,list) or kind not in kinds:return False
    hosts=event.get("host_refs") if isinstance(event.get("host_refs"),list) else []
    actors=event.get("actor_refs") if isinstance(event.get("actor_refs"),list) else []
    material=event.get("material_consequence_refs") if isinstance(event.get("material_consequence_refs"),list) else []
    wanted_hosts=source.get("host_refs") if isinstance(source.get("host_refs"),list) else []
    wanted_actors=source.get("actor_refs") if isinstance(source.get("actor_refs"),list) else []
    prefixes=source.get("material_ref_prefixes") if isinstance(source.get("material_ref_prefixes"),list) else []
    if wanted_hosts and not any(ref in hosts for ref in wanted_hosts):return False
    if wanted_actors and not any(ref in actors for ref in wanted_actors):return False
    if prefixes and not any(isinstance(ref,str) and any(ref.startswith(prefix) for prefix in prefixes) for ref in material):return False
    return True


def apply_event_evidence(*,registry:Dict[str,Any],rules:Mapping[str,Any],event:Mapping[str,Any],player_ref:str)->list[Mapping[str,Any]]:
    fronts,pressures=rules.get("fronts"),registry.get("pressures")
    if not isinstance(fronts,Mapping) or not isinstance(pressures,dict):raise CommandRejectedError("world_front_policy_invalid")
    updates=[]
    for front_id,config in sorted(fronts.items()):
        pressure=pressures.get(front_id)
        sources=config.get("event_sources") if isinstance(config,Mapping) else None
        if not isinstance(pressure,dict) or not isinstance(sources,list) or front_phase(pressure,rules)=="resolved":continue
        matching=[row for row in sources if isinstance(row,Mapping) and _event_source_matches(row,event)]
        if not matching:continue
        # Multiple source patterns for the same front/event collapse to one causal evidence row.
        source=matching[0]; role=str(source.get("role") or "source")
        anchors=source.get("host_refs") if isinstance(source.get("host_refs"),list) else []
        anchor=next((ref for ref in anchors if ref in (event.get("host_refs") or [])),None)
        if not isinstance(anchor,str):
            wanted_actors=source.get("actor_refs") if isinstance(source.get("actor_refs"),list) else []
            anchor=next((ref for ref in wanted_actors if ref in (event.get("actor_refs") or [])),None)
        if not isinstance(anchor,str):anchor=str(pressure.get("host_ref") or front_id)
        update=_record_evidence(
            pressure=pressure,rules=rules,front_id=front_id,event=event,role=role,source_anchor=anchor,
            action_kind=str(event.get("kind") or "world_event"),player_ref=player_ref,
            player_visibility=str(config.get("player_visibility") or "knowledge_only"),
        )
        if update is not None:updates.append(update)
    return updates


__all__=["apply_evidence","apply_event_evidence"]
