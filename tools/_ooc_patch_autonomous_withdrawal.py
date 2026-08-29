from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'runtime/shinobi_runtime/martial_world/exact_combat.py'
text=SOURCE.read_text(encoding='utf-8')

helpers=r'''
def _npc_withdrawal_decision(*, combat: Mapping[str, Any], actor_ref: str, people: Mapping[str, Mapping[str, Any]], faction_doctrine: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return a deterministic autonomous withdrawal declaration, if warranted."""
    states=combat.get("combatants",{}) if isinstance(combat.get("combatants"),Mapping) else {}
    state=states.get(actor_ref); person=people.get(actor_ref)
    if not isinstance(state,Mapping) or not isinstance(person,Mapping) or not _active(person,state): return None
    try: side=_side_of(combat,actor_ref)
    except KeyError: return None
    doctrine=faction_doctrine if isinstance(faction_doctrine,Mapping) else {}
    preservation=max(0,min(100,int(doctrine.get("casualty_preservation",55))))
    discipline=max(0,min(100,int(doctrine.get("withdrawal_discipline",50))))
    health=person.get("health",{}) if isinstance(person.get("health"),Mapping) else {}
    capacity=functional_capacity_factors(_wounds(person))
    function_floor=min(max(0,int(capacity.get(key,1000))) for key in ("combat_movement_milli","manual_milli","vision_milli","respiratory_milli"))
    consciousness=max(0,int(health.get("consciousness",100))); shock=max(0,int(health.get("shock",0))); blood_lost=max(0,int(health.get("blood_lost_ml",0)))
    critical=consciousness<=55 or shock>=60 or blood_lost>=700 or function_floor<500
    impaired=consciousness<80 or shock>=35 or blood_lost>=350 or function_floor<750
    arrived=[]
    for ref in combat.get("sides",{}).get(side,[]):
        ref_state=states.get(ref)
        if not isinstance(ref_state,Mapping): continue
        statuses={str(x) for x in ref_state.get("status_families",[]) if isinstance(x,str)}
        if "reinforcing" not in statuses: arrived.append(str(ref))
    active_arrived=[ref for ref in arrived if ref in people and _active(people[ref],states[ref])]
    losses=max(0,len(arrived)-len(active_arrived)); loss_percent=losses*100//max(1,len(arrived))
    collapse_threshold=max(20,min(75,90-preservation//2-discipline//3))
    side_collapse=len(arrived)>=2 and loss_percent>=collapse_threshold
    preservation_trigger=preservation>=70 and impaired
    formal=combat_force_context(combat) in {"formal_spar","tournament_nonlethal"}
    if not (critical if formal else (critical or side_collapse or preservation_trigger)): return None
    body=[str(ref) for refs in combat.get("sides",{}).values() for ref in refs if isinstance(ref,str) and ref in combat.get("positions",{})]
    if not list(open_retreat_corridors(combat.get("positions",{}),actor_ref=actor_ref,body_refs=body,obstacles=combat.get("obstacles",[]))): return None
    reason="critical_condition" if critical else "side_collapse" if side_collapse else "casualty_preservation"
    return {"reason":reason,"casualty_preservation":preservation,"withdrawal_discipline":discipline,"arrived_side_count":len(arrived),"active_arrived_count":len(active_arrived),"loss_percent":loss_percent,"collapse_threshold_percent":collapse_threshold,"condition":{"consciousness":consciousness,"shock":shock,"blood_lost_ml":blood_lost,"functional_floor_milli":function_floor}}


def _disengage_step(*, combat: dict[str, Any], actor_ref: str, people: Mapping[str, Mapping[str, Any]] | None, equipment_ledger: Mapping[str, Any] | None, duration_ms: int, start_ms: int) -> dict[str, Any]:
    """Move one fighter through a disengagement slice without advancing the clock."""
    if actor_ref not in combat.get("combatants",{}) or actor_ref not in combat.get("positions",{}): raise ValueError("combat actor unresolved")
    state=combat["combatants"][actor_ref]
    if not status_action_allowed(state.get("status_families",[]),"disengage"): return {"moved":False,"escaped":False,"reason":"status_blocks_disengagement"}
    body=[str(ref) for refs in combat.get("sides",{}).values() for ref in refs if isinstance(ref,str) and ref in combat.get("positions",{})]
    corridors=list(open_retreat_corridors(combat["positions"],actor_ref=actor_ref,body_refs=body,obstacles=combat.get("obstacles",[])))
    if not corridors: return {"moved":False,"escaped":False,"reason":"no_open_retreat_corridor"}
    chosen=sorted(corridors,key=lambda row:int(row.get("angle_mdeg",0)))[0]; row=combat["positions"][actor_ref]
    start_x,start_y=int(row["x_mm"]),int(row["y_mm"]); end_x,end_y=int(chosen["end_x_mm"]),int(chosen["end_y_mm"]); duration=max(1,int(duration_ms))
    if isinstance(people,Mapping) and actor_ref in people:
        cap=capability_from_person(people[actor_ref]); speed=movement_speed_mmps(cap)
        if isinstance(equipment_ledger,Mapping): speed=max(speed,_movement_speed_for_state(actor_ref,people[actor_ref],equipment_ledger,state,cap))
        maximum=max(0,speed)*duration//1000; dx,dy=end_x-start_x,end_y-start_y; distance=max(1,math.isqrt(dx*dx+dy*dy))
        if distance>maximum: end_x=start_x+dx*maximum//distance; end_y=start_y+dy*maximum//distance
    if not path_clear(combat["positions"],actor_ref=actor_ref,end_x_mm=end_x,end_y_mm=end_y,body_refs=body,obstacles=combat.get("obstacles",[])): return {"moved":False,"escaped":False,"reason":"retreat_path_became_blocked","corridor":chosen}
    row["x_mm"]=end_x; row["y_mm"]=end_y; row["facing_mdeg"]=int(chosen["angle_mdeg"])%360000; row["stance"]="disengaging"
    state["recovery_until_ms"]=max(int(state.get("recovery_until_ms",0)),int(start_ms)+duration+250)
    side=_side_of(combat,actor_ref); enemy_side="side_b" if side=="side_a" else "side_a"; enemies=[]
    for ref in combat.get("sides",{}).get(enemy_side,[]):
        if ref not in combat.get("positions",{}): continue
        enemy_state=combat.get("combatants",{}).get(ref,{})
        statuses={str(x) for x in enemy_state.get("status_families",[]) if isinstance(x,str)} if isinstance(enemy_state,Mapping) else set()
        if "ring_out" in statuses: continue
        if isinstance(people,Mapping) and ref in people and isinstance(enemy_state,Mapping):
            if not _active(people[ref],enemy_state): continue
        elif statuses & {"dead","unconscious","incapacitated","escaped","reinforcing"}: continue
        enemies.append(ref)
    nearest=min([planar_distance_mm(row,combat["positions"][ref]) for ref in enemies],default=999_999); escaped=nearest>=6000
    if escaped:
        statuses={str(x) for x in state.get("status_families",[]) if isinstance(x,str)}; statuses.add("escaped"); state["status_families"]=sorted(statuses)
    return {"moved":True,"escaped":escaped,"reason":"cleared_opponent_reach" if escaped else "retreat_in_progress","corridor":chosen,"movement":{"start_x_mm":start_x,"start_y_mm":start_y,"end_x_mm":end_x,"end_y_mm":end_y,"duration_ms":duration,"nearest_enemy_mm":nearest}}
'''

marker='def resolve_exchange(*, combat:'
assert text.count(marker)==1, text.count(marker)
text=text.replace(marker,helpers+'\n\n'+marker,1)

old='''    active_at_declaration=[ref for refs in out["sides"].values() for ref in refs if _active(persons[ref],out["combatants"][ref])]; scheduled=[]; declaration_events=[]
    for actor_ref in active_at_declaration:
        side=_side_of(out,actor_ref); enemy_side="side_b" if side=="side_a" else "side_a"; enemies=[ref for ref in out["sides"][enemy_side] if _active(persons[ref],out["combatants"][ref])]; known=_observe_visible_enemies(out,actor_ref=actor_ref,enemy_refs=enemies,people=persons,at_ms=int(out.get("elapsed_ms",0)))
        if not known: declaration_events.append({"actor_ref":actor_ref,"result":"no_lawfully_known_target","decision_origin":"awareness"}); continue
        if actor_ref==player_ref:
'''
new='''    active_at_declaration=[ref for refs in out["sides"].values() for ref in refs if _active(persons[ref],out["combatants"][ref])]; scheduled=[]; declaration_events=[]; withdrawing=[]
    for actor_ref in active_at_declaration:
        side=_side_of(out,actor_ref); enemy_side="side_b" if side=="side_a" else "side_a"; enemies=[ref for ref in out["sides"][enemy_side] if _active(persons[ref],out["combatants"][ref])]
        if actor_ref!=player_ref:
            withdrawal=_npc_withdrawal_decision(combat=out,actor_ref=actor_ref,people=persons,faction_doctrine=doctrines.get(str(persons[actor_ref].get("faction_ref") or ""),{}))
            if withdrawal is not None:
                withdrawing.append(actor_ref); declaration_events.append({"actor_ref":actor_ref,"result":"withdrawal_declared","decision_origin":"actor_ai","declared_at_ms":int(out.get("elapsed_ms",0)),"withdrawal":withdrawal}); continue
        known=_observe_visible_enemies(out,actor_ref=actor_ref,enemy_refs=enemies,people=persons,at_ms=int(out.get("elapsed_ms",0)))
        if not known: declaration_events.append({"actor_ref":actor_ref,"result":"no_lawfully_known_target","decision_origin":"awareness"}); continue
        if actor_ref==player_ref:
'''
assert text.count(old)==1, text.count(old)
text=text.replace(old,new,1)

old='''    _settle_combat_physiology_until(out,persons,target_ms=max(int(out.get("elapsed_ms",0)),exchange_end),equipment_ledger=ledger)
    out.pop("_pending_actions", None)
    out.pop("_defense_interruptions", None)
    out.pop("_exchange_declared_at_ms", None)
    # Exchange events are returned to the caller for narration/effects but are
'''
new='''    _settle_combat_physiology_until(out,persons,target_ms=max(int(out.get("elapsed_ms",0)),exchange_end),equipment_ledger=ledger)
    out.pop("_pending_actions", None)
    out.pop("_defense_interruptions", None)
    out.pop("_exchange_declared_at_ms", None)
    surviving_withdrawers=[ref for ref in withdrawing if ref in persons and ref in out.get("combatants",{}) and _active(persons[ref],out["combatants"][ref])]
    if surviving_withdrawers:
        withdrawal_start=int(out.get("elapsed_ms",0)); withdrawal_end=withdrawal_start+1000
        for ref in withdrawing:
            if ref not in surviving_withdrawers:
                events.append({"actor_ref":ref,"result":"withdrawal_interrupted","decision_origin":"actor_ai","started_at_ms":withdrawal_start,"ended_at_ms":withdrawal_start}); continue
            step=_disengage_step(combat=out,actor_ref=ref,people=persons,equipment_ledger=ledger,duration_ms=1000,start_ms=withdrawal_start)
            events.append({"actor_ref":ref,"result":("withdrew_from_combat" if step.get("escaped") else "withdrawal_in_progress" if step.get("moved") else "withdrawal_blocked"),"decision_origin":"actor_ai","started_at_ms":withdrawal_start,"ended_at_ms":withdrawal_end,"withdrawal":step})
        _settle_combat_physiology_until(out,persons,target_ms=withdrawal_end,equipment_ledger=ledger)
    elif withdrawing:
        withdrawal_start=int(out.get("elapsed_ms",0))
        for ref in withdrawing: events.append({"actor_ref":ref,"result":"withdrawal_interrupted","decision_origin":"actor_ai","started_at_ms":withdrawal_start,"ended_at_ms":withdrawal_start})
    # Exchange events are returned to the caller for narration/effects but are
'''
assert text.count(old)==1, text.count(old)
text=text.replace(old,new,1)

start=text.index('\ndef attempt_disengage('); end=text.index('\n\n\n__all__',start)
attempt=r'''
def attempt_disengage(*, combat: Mapping[str, Any], actor_ref: str, people: Mapping[str, Mapping[str, Any]] | None = None, equipment_ledger: Mapping[str, Any] | None = None) -> dict[str, Any]:
    out=copy.deepcopy(dict(combat)); start_ms=int(out.get("elapsed_ms",0))
    step=_disengage_step(combat=out,actor_ref=actor_ref,people=people,equipment_ledger=equipment_ledger,duration_ms=1000,start_ms=start_ms)
    if not step.get("moved"):
        result={"combat_after":out,"escaped":False,"reason":str(step.get("reason") or "disengagement_failed")}
        if "corridor" in step: result["corridor"]=step["corridor"]
        return result
    out["elapsed_ms"]=start_ms+1000
    return {"combat_after":out,"escaped":bool(step.get("escaped")),"reason":str(step.get("reason") or "retreat_in_progress"),"corridor":step.get("corridor"),"movement":step.get("movement")}
'''
text=text[:start]+attempt+text[end:]
SOURCE.write_text(text,encoding='utf-8')
print('patched exact combat withdrawal')
