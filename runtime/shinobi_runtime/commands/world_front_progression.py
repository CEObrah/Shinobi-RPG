from __future__ import annotations
import hashlib
from functools import wraps
from typing import Any, Dict, Mapping
from shinobi_runtime.autonomy import AutonomousDecision
from shinobi_runtime.commands.core import _BuiltPlan
from shinobi_runtime.commands.living_world_mission import LivingWorldMissionMixin
from shinobi_runtime.commands.world_front_plan import progress_plan
from shinobi_runtime.commands.world_front_rules import front_phase,policy,pressure_registry

_INSTALLED=False
_PROJECTION_INSTALLED=False


def _install_strategic_bias()->None:
    original=LivingWorldMissionMixin._apply_autonomous_decision
    if getattr(original,"_world_front_strategic_bias",False):return
    @wraps(original)
    def wrapped(self:Any,*,decision:Any,at:Any,command:Any,scheduler:Any,world_events:Dict[str,Any],record_writes:Dict[str,Dict[str,Any]],faction_record:Dict[str,Any])->Mapping[str,Any]:
        candidate,selected=decision,None
        payload=decision.payload if hasattr(decision,"payload") and isinstance(decision.payload,Mapping) else {};faction=payload.get("faction_id")
        if decision.kind=="routine_summary" and isinstance(faction,str):
            rules,registry=policy(self.repository),pressure_registry(self.repository);fronts,pressures=rules.get("fronts"),registry.get("pressures")
            try:profile,_=self._autonomy_policy_book().faction_context(faction)
            except (TypeError,ValueError):profile={}
            cycle=profile.get("action_cycle") if isinstance(profile,Mapping) else None;profile_allowed={v for v in cycle if isinstance(v,str)} if isinstance(cycle,list) else set()
            if isinstance(fronts,Mapping) and isinstance(pressures,Mapping):
                for front_id,config in sorted(fronts.items()):
                    roles=config.get("faction_roles") if isinstance(config,Mapping) else None;pressure=pressures.get(front_id)
                    if not isinstance(roles,Mapping) or faction not in roles or not isinstance(pressure,Mapping) or front_phase(pressure,rules) in ("latent","resolved"):continue
                    strategic=config.get("strategic_action_cycle");allowed=[v for v in strategic if v in ("mission_generate","information_report") and v in profile_allowed] if isinstance(strategic,list) else []
                    if not allowed:continue
                    seed=f"{front_id}\x00{faction}\x00{at}";kind=allowed[int(hashlib.sha256(seed.encode()).hexdigest()[:8],16)%len(allowed)];new_payload=dict(payload);new_payload["world_front_ref"]=front_id
                    candidate=AutonomousDecision(kind=kind,actor_ref=decision.actor_ref,reason=f"causal world-front pressure: {front_id}",payload=new_payload,material=True);selected=front_id;break
        result=original(self,decision=candidate,at=at,command=command,scheduler=scheduler,world_events=world_events,record_writes=record_writes,faction_record=faction_record)
        if selected is not None and isinstance(result,Mapping):result=dict(result);result["world_front_ref"]=selected
        return result
    wrapped._world_front_strategic_bias=True
    LivingWorldMissionMixin._apply_autonomous_decision=wrapped


def _install_time_postprocessor()->None:
    from shinobi_runtime.commands import campaign_runtime_planner as module
    original=module.CampaignCommandPlanner._advance_time
    if getattr(original,"_world_front_progression",False):return
    @wraps(original)
    def wrapped(self:Any,command:Any,meta:Mapping[str,Any],current_time:Any)->_BuiltPlan:
        try:previous=self.repository.read_json(self.scene_path)
        except (FileNotFoundError,ValueError):previous={}
        plan=progress_plan(self,original(self,command,meta,current_time),command)
        return module._refresh_time_advanced_plan(plan,self.scene_path,previous_scene=previous) if isinstance(previous,Mapping) else plan
    wrapped._world_front_progression=True
    module.CampaignCommandPlanner._advance_time=wrapped


def install_world_front_projection()->None:
    global _PROJECTION_INSTALLED
    if _PROJECTION_INSTALLED:return
    from shinobi_runtime.commands import campaign_runtime_planner as module
    original=module._fresh_player_facing_time_handoff
    if getattr(original,"_world_front_projection",False):_PROJECTION_INSTALLED=True;return
    @wraps(original)
    def wrapped(result:Mapping[str,Any])->tuple[list[str],list[str],list[str]]:
        pressures,reports,approaching=original(result);updates=result.get("world_front_updates")
        if isinstance(updates,list):
            for update in updates:
                if not isinstance(update,Mapping):continue
                phase=update.get("phase_after")
                if phase in ("developing","operational","crisis") and "A known world pressure has materially changed." not in pressures:pressures.append("A known world pressure has materially changed.")
                if phase=="crisis" and "A known strategic pressure has reached crisis-level consequences." not in approaching:approaching.append("A known strategic pressure has reached crisis-level consequences.")
        return pressures[:12],reports[:6],approaching[:8]
    wrapped._world_front_projection=True
    module._fresh_player_facing_time_handoff=wrapped;_PROJECTION_INSTALLED=True


def install_world_front_progression()->None:
    global _INSTALLED
    if _INSTALLED:return
    _install_strategic_bias();install_world_front_projection();_install_time_postprocessor();_INSTALLED=True


__all__=["install_world_front_progression","install_world_front_projection","front_phase"]
