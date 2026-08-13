from __future__ import annotations
from shinobi_runtime.commands.living_world_support import *

class LivingWorldConsequencesMixin:
    def _after_autonomous_mission_result(self, *, result: Dict[str, Any], faction_id: str, decision: Any, at: CampaignTime, command: CommandEnvelope, scheduler: CausalSchedulerRegistry, world_events: Dict[str, Any], record_writes: Dict[str, Dict[str, Any]], faction_record: Dict[str, Any]) -> Mapping[str, Any]:
        mission_id = result.get("mission_id"); claim_id = result.get("claim_id"); outcome = result.get("outcome")
        if not isinstance(mission_id, str):
            return result
        memory = self._faction_memory(faction_id, at=at, record_writes=record_writes)
        team_ref = memory["active_mission_team_refs"].pop(mission_id, None)
        mission_path = mission_owner_path(mission_id)
        try:
            mission_record = record_writes.get(mission_path) or self.repository.read_json(mission_path)
            owner = MissionOwner.from_record(mission_record)
        except (FileNotFoundError, TypeError, ValueError):
            owner = None
        participants = list(owner.mission.participant_refs) if owner is not None else []
        succeeded = outcome in ("succeeded","completed","settled_success") or "success" in str(outcome)
        if isinstance(team_ref, str):
            history = self._team_history(team_ref, at=at, record_writes=record_writes)
            history["missions_total"] = int(history.get("missions_total", 0)) + 1
            key = "missions_succeeded" if succeeded else "missions_failed"; history[key] = int(history.get(key, 0)) + 1
            history["last_mission_ref"] = mission_id; history["last_result_at"] = str(at)
            event_ref = result.get("event_id")
            if isinstance(event_ref, str):
                notable = history.setdefault("notable_event_refs", [])
                if event_ref not in notable:
                    notable.append(event_ref); del notable[:-_MAX_TEAM_HISTORY_EVENTS]
            perf = memory["team_performance"].setdefault(team_ref, {"missions_total":0,"missions_succeeded":0,"missions_failed":0,"last_mission_ref":None,"last_result_at":None})
            perf["missions_total"] = int(perf.get("missions_total", 0)) + 1
            pkey = "missions_succeeded" if succeeded else "missions_failed"; perf[pkey] = int(perf.get(pkey, 0)) + 1
            perf["last_mission_ref"] = mission_id; perf["last_result_at"] = str(at)
        consequence_summary = self._apply_routine_mission_consequences(mission_id=mission_id, participant_refs=participants, difficulty=int(result.get("difficulty",60)) if isinstance(result.get("difficulty"),int) else 60, mission_score=int(result.get("capability_score",0)) if isinstance(result.get("capability_score"),int) else 0, succeeded=succeeded, at=at, command=command, scheduler=scheduler, record_writes=record_writes, team_ref=team_ref if isinstance(team_ref,str) else None)
        logistics = self._consume_mission_supplies(participant_refs=participants, difficulty=int(result.get("difficulty",60)) if isinstance(result.get("difficulty"),int) else 60, succeeded=succeeded, mission_id=mission_id, record_writes=record_writes)
        report_event = self._append_internal_event(world_events, command=command, identity=f"{mission_id}:{at}:report", kind="mission_result_report_received", at=at, host_refs=tuple(ref for ref in (faction_id,team_ref,mission_id) if isinstance(ref,str)), actor_refs=tuple(participants[:16]), affected_owner_refs=(self._faction_memory_path(faction_id),), material_consequence_refs=tuple(ref for ref in (mission_id,claim_id) if isinstance(ref,str)), classification=str(decision.payload.get("classification") or "restricted"), audience_refs=(faction_id,), knowledge_refs=(claim_id,) if isinstance(claim_id,str) else (), source_refs=tuple(ref for ref in (result.get("event_id"),claim_id) if isinstance(ref,str)))
        reports = memory["recent_report_refs"]
        if report_event not in reports:
            reports.append(report_event); del reports[:-_MAX_REPORT_HISTORY]
        if isinstance(team_ref, str):
            signal = "reputation.signal.mission_success" if succeeded else "reputation.signal.mission_failure"
            for subject_ref in dict.fromkeys([team_ref,*participants[:8]]):
                self._apply_autonomous_reputation_signal(subject_ref=subject_ref, audience_id=faction_id, source_event_ref=report_event, source_event_kind="mission_result_report_received", signal_ref=signal, classification=str(decision.payload.get("classification") or "restricted"), at=at, record_writes=record_writes)
            self._apply_team_relationship_event(participants, event_ref=report_event, interaction_kind="professional_mission_success" if succeeded else "professional_shared_setback", summary=f"Shared autonomous mission result: {mission_id}", at=at, record_writes=record_writes, player_id=command.actor_id)
        return {**result,"team_ref":team_ref,"report_event_id":report_event,"routine_consequences":consequence_summary,"logistics":logistics}

    def _routine_consequence_tier(self, *, mission_id: str, person_ref: str, difficulty: int, mission_score: int, succeeded: bool, high_salience: bool) -> str:
        difficulty = max(20,min(95,difficulty)); margin = mission_score-difficulty
        risk = max(5,min(100,difficulty+(16 if not succeeded else -18)-max(-20,min(20,margin//2))))
        roll = _stable_roll(mission_id,person_ref,"consequence")
        if not succeeded and risk >= 88 and roll < 4 and not high_salience: return "killed"
        if not succeeded and risk >= 72 and roll < 14: return "incapacitated"
        if risk >= 55 and roll < min(45,risk//2): return "wounded"
        return "fatigued" if risk >= 30 else "clean"

    @staticmethod
    def _routine_high_salience_person(record: Mapping[str, Any], person_ref: str, player_id: str) -> bool:
        if person_ref == player_id or record.get("resolution_tier") == "featured": return True
        roles = record.get("roles"); role_set = {str(value).lower() for value in roles} if isinstance(roles,list) else set()
        return bool(role_set & {"kage","jinchuriki","commander","clan_head","daimyo","heir","player_character","household_head"})

    def _apply_routine_mission_consequences(self, *, mission_id: str, participant_refs: Sequence[str], difficulty: int, mission_score: int, succeeded: bool, at: CampaignTime, command: CommandEnvelope, scheduler: CausalSchedulerRegistry, record_writes: Dict[str, Dict[str, Any]], team_ref: Optional[str]) -> Mapping[str, Any]:
        summary: Dict[str,str] = {}; force_writes: Dict[str,Dict[str,Any]] = {}; team_writes: Dict[str,Dict[str,Any]] = {}; formation_writes: Dict[str,Dict[str,Any]] = {}; population: Optional[Dict[str,Any]] = None; casualty_count = 0
        for person_ref in participant_refs[:16]:
            try: path,_digest,view = self._resolve_covered_owner_view(person_ref,cache=_OwnerResolutionCache())
            except CommandRejectedError: continue
            if not isinstance(view,Mapping) or view.get("schema") not in ("shinobi_character","person"): continue
            person = record_writes.get(path)
            if person is None: person=copy.deepcopy(dict(view)); record_writes[path]=person
            high = self._routine_high_salience_person(person,person_ref,command.actor_id)
            tier = self._routine_consequence_tier(mission_id=mission_id,person_ref=person_ref,difficulty=difficulty,mission_score=mission_score,succeeded=succeeded,high_salience=high)
            if tier == "clean": summary[person_ref]=tier; continue
            resources = person.get("resources") if isinstance(person.get("resources"),Mapping) else {}; fatigue=resources.get("fatigue") if isinstance(resources.get("fatigue"),Mapping) else None; chakra=resources.get("chakra") if isinstance(resources.get("chakra"),Mapping) else None; after_resources=[]
            if isinstance(fatigue,Mapping) and isinstance(fatigue.get("current"),int) and isinstance(fatigue.get("capacity"),int):
                cost=max(1,min(int(fatigue["capacity"]*(difficulty+20)/500),fatigue["capacity"]//2 or 1)); after_resources.append(SimpleNamespace(resource_ref="fatigue",current=min(fatigue["capacity"],fatigue["current"]+cost)))
            if isinstance(chakra,Mapping) and isinstance(chakra.get("current"),int) and isinstance(chakra.get("capacity"),int):
                cost=max(1,int(chakra["capacity"]*min(35,difficulty//3)/100)); after_resources.append(SimpleNamespace(resource_ref="chakra",current=max(0,chakra["current"]-cost)))
            after_personnel=PersonnelState(total=1,active=1 if tier=="fatigued" else 0,wounded=1 if tier=="wounded" else 0,incapacitated=1 if tier=="incapacitated" else 0,killed=1 if tier=="killed" else 0)
            try: apply_personnel_effect(person,effect=SimpleNamespace(after_resources=tuple(after_resources),after_personnel=after_personnel),event_marker=f"{mission_id}@{at}")
            except ValueError as exc: raise CommandRejectedError("autonomous_mission_health_invalid") from exc
            if tier in ("wounded","incapacitated"):
                casualty_count += 1
                if population is None:
                    try: population=copy.deepcopy(self.repository.read_json(POPULATION_REGISTRY_PATH))
                    except (FileNotFoundError,ValueError) as exc: raise CommandRejectedError("population_registry_invalid") from exc
                reconciliation=self._reconcile_rostered_person_injury(population,person_ref=person_ref,force_writes=force_writes,team_writes=team_writes,formation_writes=formation_writes)
                if isinstance(reconciliation,Mapping):
                    life_course=person.setdefault("life_course_state",{}); deployment=life_course.setdefault("deployment",{}) if isinstance(life_course,dict) else None
                    if isinstance(deployment,dict):
                        deployment["status"]="recovering"; deployment["return_availability_class"]=reconciliation.get("return_availability_class")
                        if reconciliation.get("return_formation_ref"): deployment["return_formation_ref"]=reconciliation.get("return_formation_ref")
                host_id="host.recovery."+person_ref
                if host_id not in scheduler.hosts:
                    due=at.add_seconds(86400); host_metadata={"person_ref":person_ref}
                    if isinstance(reconciliation,Mapping): host_metadata.update(reconciliation)
                    scheduler.add_host(SchedulerHost(state=HostState(host_id=host_id,kind="person_recovery",resolved_through=at,safe_through=due.add_seconds(-1),handler_ref="causal.scheduler",rng_namespace="recovery:"+person_ref,next_due=due),authority_kind="person_recovery",owner_ref=path,metadata=host_metadata))
                    scheduler.upsert_event(recurring_event(kind="person.recovery.periodic_review",identity=person_ref,host_id=host_id,due_at=due,recurrence={"kind":"fixed_interval","interval_seconds":86400,"accrual_mode":"boundary_only"},payload={"actor_ref":person_ref,"owner_ref":path},priority=25,visibility="restricted",requires_player=False))
                life=person.get("life_course_state")
                if isinstance(life,dict):
                    injuries=life.setdefault("injury_events",[])
                    if isinstance(injuries,list): injuries.append({"at":str(at),"source":mission_id,"severity":tier}); del injuries[:-32]
            elif tier == "killed":
                casualty_count += 1
                if population is None:
                    try: population=copy.deepcopy(self.repository.read_json(POPULATION_REGISTRY_PATH))
                    except (FileNotFoundError,ValueError) as exc: raise CommandRejectedError("population_registry_invalid") from exc
                self._reconcile_rostered_person_death(population,person_ref=person_ref,at=at,command=command,force_writes=force_writes,team_writes=team_writes,formation_writes=formation_writes)
                life=person.get("life_course_state")
                if isinstance(life,dict):
                    status=life.setdefault("status_history",[])
                    if isinstance(status,list): status.append(f"{at}: killed during autonomous mission {mission_id}"); del status[:-32]
            summary[person_ref]=tier
        if population is not None: record_writes[POPULATION_REGISTRY_PATH]=population
        record_writes.update(force_writes); record_writes.update(team_writes); record_writes.update(formation_writes)
        if team_ref and casualty_count:
            history=self._team_history(team_ref,at=at,record_writes=record_writes); history["casualty_events"]=int(history.get("casualty_events",0))+1
        return {"members":summary,"casualty_count":casualty_count}

    def _team_supply_readiness(self, participant_refs: Sequence[str], *, record_writes: Mapping[str, Mapping[str, Any]]) -> int:
        try:
            inventory = record_writes.get(INVENTORY_REGISTRY_PATH) or self.repository.read_json(INVENTORY_REGISTRY_PATH)
        except (FileNotFoundError, ValueError):
            return 0
        holders = inventory.get("holders") if isinstance(inventory,Mapping) else None
        if not isinstance(holders,Mapping): return 0
        ready=0
        for ref in participant_refs:
            items=holders.get(ref)
            if isinstance(items,Mapping) and any(isinstance(items.get(item),int) and items.get(item,0)>0 for item in _CONSUMABLE_PRIORITY): ready += 1
        return min(8,ready)

    def _consume_mission_supplies(self, *, participant_refs: Sequence[str], difficulty: int, succeeded: bool, mission_id: str, record_writes: Dict[str, Dict[str, Any]]) -> Mapping[str, Any]:
        try: inventory=record_writes.get(INVENTORY_REGISTRY_PATH) or copy.deepcopy(self.repository.read_json(INVENTORY_REGISTRY_PATH))
        except (FileNotFoundError,ValueError): return {"consumed":{}}
        holders=inventory.get("holders") if isinstance(inventory,dict) else None
        if not isinstance(holders,dict): raise CommandRejectedError("inventory_registry_invalid")
        consumed: Dict[str,Dict[str,int]]={}; budget=1+int(difficulty>=60)+int(not succeeded)
        for person_ref in participant_refs[:16]:
            items=holders.get(person_ref)
            if not isinstance(items,dict): continue
            remaining=budget; used={}; offset=_stable_roll(mission_id,person_ref,"supply",modulo=len(_CONSUMABLE_PRIORITY)); order=_CONSUMABLE_PRIORITY[offset:]+_CONSUMABLE_PRIORITY[:offset]
            for item_ref in order:
                if remaining<=0: break
                current=items.get(item_ref)
                if isinstance(current,int) and not isinstance(current,bool) and current>0:
                    amount=min(current,remaining); items[item_ref]=current-amount; remaining-=amount; used[item_ref]=amount
            if used: consumed[person_ref]=used
        if consumed: record_writes[INVENTORY_REGISTRY_PATH]=inventory
        return {"consumed":consumed}
