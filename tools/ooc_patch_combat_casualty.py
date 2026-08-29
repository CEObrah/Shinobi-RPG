from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"patch anchor missing: {label}")
    return text.replace(old, new, 1)


def between(text: str, start: str, end: str, replacement: str) -> str:
    i = text.index(start)
    j = text.index(end, i)
    return text[:i] + replacement + text[j:]


exact_path = Path("runtime/shinobi_runtime/martial_world/exact_combat.py")
exact = exact_path.read_text()
if "def _observed_status_snapshot(" not in exact:
    helper = '''\n\ndef _observed_status_snapshot(person: Mapping[str, Any], state: Mapping[str, Any]) -> list[str]:
    """Return one observer-safe snapshot of a combat body's visible condition."""
    statuses = {str(x) for x in state.get("status_families", []) if isinstance(x, str)}
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    health_status = str(health.get("status") or "")
    if health_status == "dead":
        statuses.add("dead")
    elif health_status == "incapacitated":
        statuses.add("incapacitated")
    if int(health.get("consciousness", 100)) <= 0 and health_status != "dead":
        statuses.add("unconscious")
    if health_status == "injured" or _wounds(person):
        statuses.add("wounded")
    return sorted(statuses)
'''
    exact = replace_once(exact, "\n\ndef initialize_combat", helper + "\n\ndef initialize_combat", "snapshot helper")
    exact = replace_once(
        exact,
        'enemy_refs=list(b if ref in a else a); observed=enemy_refs if awareness_mode=="mutual" else []',
        'enemy_refs=list(b if ref in a else a); arrived_enemy_refs=[enemy for enemy in enemy_refs if max(0,int(reinforcements.get(enemy,0)))==0]; observed=arrived_enemy_refs if awareness_mode=="mutual" else []',
        "initial arrived observation",
    )
    exact = replace_once(
        exact,
        'if not surprised and awareness_mode!="mutual": observed=enemy_refs',
        'if not surprised and awareness_mode!="mutual": observed=arrived_enemy_refs',
        "ambush arrived observation",
    )
    exact = replace_once(
        exact,
        'nearest=min((planar_distance_mm(positions[ref],positions[enemy]) for enemy in enemy_refs),default=0)',
        'nearest=min((planar_distance_mm(positions[ref],positions[enemy]) for enemy in arrived_enemy_refs),default=0)',
        "initial weapon range excludes future reinforcements",
    )
    exact = replace_once(
        exact,
        '"observed_refs":([] if reinforce>0 else observed),"awareness_confidence_milli"',
        '"observed_refs":([] if reinforce>0 else observed),"observed_status_families":({} if reinforce>0 else {enemy:_observed_status_snapshot(people[enemy],{}) for enemy in observed}),"awareness_confidence_milli"',
        "initial observed status snapshots",
    )

    observe = '''def _observe_visible_enemies(combat: dict[str, Any], *, actor_ref: str, enemy_refs: Sequence[str], people: Mapping[str, Mapping[str, Any]], at_ms: int) -> list[str]:
    state=combat["combatants"][actor_ref]; known=set(str(x) for x in state.get("observed_refs",[]) if isinstance(x,str)); actor_cap=capability_from_person(people[actor_ref])
    snapshots_raw=state.get("observed_status_families",{}); snapshots=dict(snapshots_raw) if isinstance(snapshots_raw,Mapping) else {}
    for enemy_ref in enemy_refs:
        if enemy_ref not in people or enemy_ref not in combat.get("positions",{}): continue
        if not line_of_sight_clear(combat["positions"],actor_ref=actor_ref,target_ref=enemy_ref,obstacles=combat.get("obstacles",[])): continue
        enemy_state=combat["combatants"].get(enemy_ref,{})
        enemy_cap=capability_from_person(people[enemy_ref]); distance_m=planar_distance_mm(combat["positions"][actor_ref],combat["positions"][enemy_ref])/1000.0
        stealth_contribution=enemy_cap.stealth*3 if _active(people[enemy_ref],enemy_state) else 0
        concealment=max(0,int(enemy_state.get("concealment_milli",0))+stealth_contribution)
        visibility=max(200,min(1200,int((combat.get("environment") or {}).get("visibility_milli",1000)))) if isinstance(combat.get("environment"),Mapping) else 1000
        detection=(actor_cap.perception*5+actor_cap.reaction*2-int(distance_m*5))*visibility//1000
        if detection>=concealment:
            known.add(enemy_ref)
            snapshots[enemy_ref]=_observed_status_snapshot(people[enemy_ref],enemy_state)
    state["observed_refs"]=sorted(known)
    state["observed_status_families"]={ref:list(snapshots[ref]) for ref in sorted(snapshots) if ref in known and isinstance(snapshots.get(ref),list)}
    if known: state["awareness_confidence_milli"]=1000; state["surprise_milli"]=max(0,int(state.get("surprise_milli",0))-max(200,at_ms//5))
    return sorted(known & set(enemy_refs))
'''
    exact = between(exact, "def _observe_visible_enemies(", "\n\ndef _refresh_team_plan", observe)

    refresh = '''def _refresh_team_plan(combat: dict[str, Any], *, side: str, people: Mapping[str, Mapping[str, Any]], doctrine: Mapping[str, Any] | None) -> dict[str, Any]:
    members=[ref for ref in combat["sides"][side] if _active(people[ref],combat["combatants"][ref])]; other="side_b" if side=="side_a" else "side_a"; at_ms=int(combat.get("elapsed_ms",0))
    enemy_members=[ref for ref in combat["sides"][other] if ref in people and ref in combat.get("combatants",{})]
    enemies=[ref for ref in enemy_members if _active(people[ref],combat["combatants"][ref])]
    enemy_set=set(enemies); observable=[]
    for ref in enemy_members:
        state=combat["combatants"][ref]; statuses={str(x) for x in state.get("status_families",[]) if isinstance(x,str)}
        if {"escaped","ring_out"}&statuses: continue
        if "reinforcing" in statuses and at_ms<max(0,int(state.get("reinforcement_at_ms",0))): continue
        if ref in combat.get("positions",{}): observable.append(ref)
    known:set[str]=set()
    for member in members:
        seen=_observe_visible_enemies(combat,actor_ref=member,enemy_refs=observable,people=people,at_ms=at_ms)
        known.update(ref for ref in seen if ref in enemy_set)
    previous=combat.get("team_plans",{}).get(side); reasons=replan_reasons(previous,active_member_refs=members,known_enemy_refs=sorted(known),positions=combat["positions"],objective_kind=str(combat.get("objective",{}).get("kind","eliminate")))
    if reasons:
        plan=plan_team_exchange(side_ref=side,member_refs=members,known_enemy_refs=sorted(known),records=people,positions=combat["positions"],obstacles=combat.get("obstacles",[]),objective_kind=str(combat.get("objective",{}).get("kind","eliminate")),doctrine=doctrine,at_ms=at_ms); plan["replan_reasons"]=list(reasons); combat.setdefault("team_plans",{})[side]=plan
    return combat.get("team_plans",{}).get(side,{})
'''
    exact = between(exact, "def _refresh_team_plan(", "\n\ndef _ready_team_assignment", refresh)

    old_arrival = '''        if "reinforcing" in statuses and now_ms>=max(0,int(state.get("reinforcement_at_ms",0))):
            state["status_families"]=[x for x in statuses if x!="reinforcing"]
            enemy_side="side_b" if _side_of(out,str(ref))=="side_a" else "side_a"
            state["observed_refs"]=[str(x) for x in out.get("sides",{}).get(enemy_side,[])]
            state["awareness_confidence_milli"]=1000
'''
    new_arrival = '''        if "reinforcing" in statuses and now_ms>=max(0,int(state.get("reinforcement_at_ms",0))):
            state["status_families"]=[x for x in statuses if x!="reinforcing"]
            enemy_side="side_b" if _side_of(out,str(ref))=="side_a" else "side_a"
            arrived_enemies=[]
            for enemy_ref in out.get("sides",{}).get(enemy_side,[]):
                enemy_state=out.get("combatants",{}).get(enemy_ref,{})
                enemy_statuses={str(x) for x in enemy_state.get("status_families",[]) if isinstance(x,str)} if isinstance(enemy_state,Mapping) else set()
                if {"escaped","ring_out"}&enemy_statuses: continue
                if "reinforcing" in enemy_statuses and now_ms<max(0,int(enemy_state.get("reinforcement_at_ms",0))): continue
                arrived_enemies.append(str(enemy_ref))
            state["observed_refs"]=arrived_enemies
            state["observed_status_families"]={enemy_ref:_observed_status_snapshot(persons[enemy_ref],out["combatants"].get(enemy_ref,{})) for enemy_ref in arrived_enemies if enemy_ref in persons}
            state["awareness_confidence_milli"]=1000
'''
    exact = replace_once(exact, old_arrival, new_arrival, "reinforcement observation")

    wound_line = '            statuses=set(defender_state.get("status_families",[])); status="dead" if defender["health"].get("status")=="dead" else "incapacitated"; statuses.add(status); defender_state["status_families"]=sorted(statuses); defender_state.setdefault("incapacitated_at_ms",at_ms)'
    wound_new = wound_line + '\n        snapshots=actor_state.get("observed_status_families",{}); snapshots=dict(snapshots) if isinstance(snapshots,Mapping) else {}; snapshots[actual_ref]=_observed_status_snapshot(defender,defender_state); actor_state["observed_status_families"]=snapshots'
    exact = replace_once(exact, wound_line, wound_new, "post-contact casualty snapshot")
    exact = replace_once(
        exact,
        'body_refs=[ref for refs in combat["sides"].values() for ref in refs]',
        'body_refs=_present_body_refs(combat)',
        "combat geometry excludes future reinforcements",
    )
    exact_path.write_text(exact)


travel_path = Path("runtime/shinobi_runtime/api/travel_operations.py")
travel = travel_path.read_text()
if '"player_hostile_status_observation"' not in travel:
    projection = '''def combat_observation_scene_projection(
    *,
    read_json: Callable[[str], Any],
    player_id: str,
    ally_limit: int = 16,
) -> dict[str, Any] | None:
    """Project active friendlies, casualty bodies, and observer-safe hostile status."""
    active = active_combat_for_person(read_json, player_id)
    if active is None:
        return None
    combat_ref, combat = active
    if not isinstance(combat, Mapping):
        return None
    sides = combat.get("sides", {})
    combatants = combat.get("combatants", {})
    if not isinstance(sides, Mapping) or not isinstance(combatants, Mapping):
        return None

    player_side_ref: str | None = None
    player_side_members: list[str] = []
    enemy_refs: list[str] = []
    for side_ref, raw_members in sides.items():
        members = _unique_person_refs(raw_members)
        if player_id in members:
            player_side_ref = str(side_ref)
            player_side_members = members
            break
    if player_side_ref is None:
        return None
    for side_ref, raw_members in sides.items():
        if str(side_ref) == player_side_ref:
            continue
        for ref in _unique_person_refs(raw_members):
            if ref not in enemy_refs:
                enemy_refs.append(ref)
    enemy_set = set(enemy_refs)

    def status_families(ref: str) -> set[str]:
        state = combatants.get(ref, {})
        if not isinstance(state, Mapping):
            return set()
        return {str(value) for value in state.get("status_families", []) if isinstance(value, str)}

    def body_present(ref: str) -> bool:
        return combat_person_arrived(combat, ref) and not ({"escaped", "ring_out"} & status_families(ref))

    def can_act(ref: str) -> bool:
        return body_present(ref) and not ({"dead", "incapacitated", "unconscious"} & status_families(ref))

    friendly_bodies = [ref for ref in player_side_members if body_present(ref)]
    friendly_active = [ref for ref in friendly_bodies if can_act(ref)]
    friendly_dead = [ref for ref in friendly_bodies if "dead" in status_families(ref)]
    friendly_incapacitated = [
        ref for ref in friendly_bodies
        if "dead" not in status_families(ref)
        and bool({"incapacitated", "unconscious"} & status_families(ref))
    ]
    if player_id not in friendly_bodies:
        friendly_bodies.insert(0, player_id)
    if player_id not in friendly_active and not ({"dead", "incapacitated", "unconscious"} & status_families(player_id)):
        friendly_active.insert(0, player_id)

    def observer_summary(observer_ref: str) -> dict[str, Any]:
        state = combatants.get(observer_ref, {})
        observed = _unique_person_refs(state.get("observed_refs")) if isinstance(state, Mapping) else []
        confirmed_count = sum(1 for ref in observed if ref in enemy_set)
        return {
            "observer_person_id": observer_ref,
            "confirmed_observed_hostile_count": confirmed_count,
        }

    def hostile_status_summary(observer_ref: str) -> dict[str, Any]:
        state = combatants.get(observer_ref, {})
        observed = _unique_person_refs(state.get("observed_refs")) if isinstance(state, Mapping) else []
        snapshots = state.get("observed_status_families", {}) if isinstance(state, Mapping) else {}
        snapshots = snapshots if isinstance(snapshots, Mapping) else {}
        active_unwounded = active_wounded = incapacitated = dead = unknown = 0
        for ref in observed:
            if ref not in enemy_set:
                continue
            raw = snapshots.get(ref)
            if not isinstance(raw, list):
                unknown += 1
                continue
            statuses = {str(value) for value in raw if isinstance(value, str)}
            if "dead" in statuses:
                dead += 1
            elif {"incapacitated", "unconscious"} & statuses:
                incapacitated += 1
            elif "wounded" in statuses:
                active_wounded += 1
            else:
                active_unwounded += 1
        return {
            "observer_person_id": observer_ref,
            "last_observed_active_unwounded_count": active_unwounded,
            "last_observed_active_wounded_count": active_wounded,
            "last_observed_incapacitated_count": incapacitated,
            "last_observed_dead_count": dead,
            "observed_status_unknown_count": unknown,
            "status_semantics": "last_direct_observation_not_omniscient_current_state",
        }

    player_observation = observer_summary(player_id)
    limit = max(0, min(24, int(ally_limit)))
    ally_observers: list[dict[str, Any]] = []
    if limit:
        for ref in friendly_active:
            if ref == player_id:
                continue
            ally_observers.append(observer_summary(ref))
            if len(ally_observers) >= limit:
                break

    return {
        "combat_ref": combat_ref,
        "friendly_participant_person_ids": friendly_active,
        "friendly_participant_count": len(friendly_active),
        "friendly_body_person_ids": friendly_bodies,
        "friendly_dead_person_ids": friendly_dead,
        "friendly_incapacitated_person_ids": friendly_incapacitated,
        "friendly_presence_semantics": "arrived_exact_combat_participants_only",
        "friendly_activity_semantics": "combat_present_person_ids_are_able_to_act_bodies_only",
        "player_observation": player_observation,
        "player_hostile_status_observation": hostile_status_summary(player_id),
        "ally_observer_summaries": ally_observers,
        "knowledge_semantics": "observer_specific_not_automatically_shared",
        "count_semantics": "confirmed_observed_hostiles_ever_detected_not_current_active_or_total_force",
    }
'''
    travel = between(travel, "def combat_observation_scene_projection(", "\n\ndef movement_scene_projection", projection)

    play_block = '''            if combat_observation is not None:
                scene["combat_observation_context"] = combat_observation
                combat_present = _unique_person_refs(
                    combat_observation.get("friendly_participant_person_ids")
                )
                combat_bodies = _unique_person_refs(
                    combat_observation.get("friendly_body_person_ids")
                )
                combat_dead = _unique_person_refs(
                    combat_observation.get("friendly_dead_person_ids")
                )
                combat_incapacitated = _unique_person_refs(
                    combat_observation.get("friendly_incapacitated_person_ids")
                )
                present: list[str] = []
                existing_present = scene.get("present_person_ids", [])
                for ref in ([*existing_present] if isinstance(existing_present, list) else []) + combat_present:
                    if isinstance(ref, str) and ref and ref not in present:
                        present.append(ref)
                scene["present_person_ids"] = present
                scene["combat_present_person_ids"] = combat_present
                scene["combat_body_person_ids"] = combat_bodies
                scene["combat_dead_person_ids"] = combat_dead
                scene["combat_incapacitated_person_ids"] = combat_incapacitated
                for ref in combat_bodies:
                    if ref not in suggested:
                        suggested.append(ref)
                person_reads["combat_participant_use"] = (
                    "combat_present_person_ids are arrived friendly fighters still able to act. "
                    "combat_body_person_ids are the wider physically present friendly bodies; dead or incapacitated IDs are separated explicitly and must not speak, protect, or act."
                )
                person_reads["combat_observer_use"] = (
                    "confirmed_observed_hostile_count is a cumulative detected count for this combat, not the number still fighting. "
                    "Use player_hostile_status_observation for Wei's last directly observed hostile condition counts; those snapshots are not omniscient current state. "
                    "Ally observations remain that ally's knowledge until communicated in-scene."
                )
'''
    travel = between(travel, "            if combat_observation is not None:", "\n            if movement is not None:", play_block)
    travel_path.write_text(travel)


test_path = Path("tests/current/test_combat_casualty_projection.py")
if not test_path.exists():
    test_path.write_text('''from __future__ import annotations\n\nimport json\n\nfrom shinobi_runtime.api.travel_operations import combat_observation_scene_projection\nfrom shinobi_runtime.martial_world.exact_combat import initialize_combat\n\n\ndef _reader(combat):\n    payload = {\n        "state/martial-world/combats.json": {\n            "schema": "jianghu-combat-state-1.0",\n            "combats": {"combat:test": combat},\n        }\n    }\n\n    def read(path):\n        if path not in payload:\n            raise FileNotFoundError(path)\n        return payload[path]\n\n    return read\n\n\ndef test_mutual_awareness_does_not_observe_future_enemy_reinforcements():\n    people = {\n        "pc": {"person_id": "pc", "health": {"status": "ready", "injuries": [], "consciousness": 100}},\n        "enemy.arrived": {"person_id": "enemy.arrived", "health": {"status": "ready", "injuries": [], "consciousness": 100}},\n        "enemy.future": {"person_id": "enemy.future", "health": {"status": "ready", "injuries": [], "consciousness": 100}},\n    }\n    combat = initialize_combat(\n        combat_ref="combat:test",\n        side_a_refs=["pc"],\n        side_b_refs=["enemy.arrived", "enemy.future"],\n        people=people,\n        zone_ref="test",\n        started_at="0061-10-19T21:15:00",\n        objective={"kind": "eliminate", "target_refs": ["enemy.arrived"]},\n        awareness_mode="mutual",\n        reinforcement_delays_ms={"enemy.future": 5000},\n    )\n    pc_state = combat["combatants"]["pc"]\n    assert pc_state["observed_refs"] == ["enemy.arrived"]\n    assert set(pc_state["observed_status_families"]) == {"enemy.arrived"}\n\n\ndef test_projection_separates_active_allies_from_casualty_bodies_and_aggregates_enemy_casualties():\n    combat = {\n        "combat_id": "combat:test",\n        "status": "active",\n        "elapsed_ms": 5000,\n        "sides": {\n            "side_a": ["pc", "ally.active", "ally.down", "ally.dead"],\n            "side_b": ["enemy.1", "enemy.2", "enemy.3", "enemy.4", "enemy.5"],\n        },\n        "combatants": {\n            "pc": {\n                "status_families": [],\n                "observed_refs": ["enemy.1", "enemy.2", "enemy.3", "enemy.4", "enemy.5"],\n                "observed_status_families": {\n                    "enemy.1": [],\n                    "enemy.2": ["wounded"],\n                    "enemy.3": ["incapacitated"],\n                    "enemy.4": ["dead"],\n                },\n            },\n            "ally.active": {"status_families": [], "observed_refs": []},\n            "ally.down": {"status_families": ["incapacitated"], "observed_refs": []},\n            "ally.dead": {"status_families": ["dead"], "observed_refs": []},\n            "enemy.1": {"status_families": []},\n            "enemy.2": {"status_families": ["wounded"]},\n            "enemy.3": {"status_families": ["incapacitated"]},\n            "enemy.4": {"status_families": ["dead"]},\n            "enemy.5": {"status_families": []},\n        },\n    }\n    result = combat_observation_scene_projection(read_json=_reader(combat), player_id="pc")\n    assert result is not None\n    assert result["friendly_participant_person_ids"] == ["pc", "ally.active"]\n    assert result["friendly_body_person_ids"] == ["pc", "ally.active", "ally.down", "ally.dead"]\n    assert result["friendly_incapacitated_person_ids"] == ["ally.down"]\n    assert result["friendly_dead_person_ids"] == ["ally.dead"]\n    assert result["player_observation"]["confirmed_observed_hostile_count"] == 5\n    assert result["player_hostile_status_observation"] == {\n        "observer_person_id": "pc",\n        "last_observed_active_unwounded_count": 1,\n        "last_observed_active_wounded_count": 1,\n        "last_observed_incapacitated_count": 1,\n        "last_observed_dead_count": 1,\n        "observed_status_unknown_count": 1,\n        "status_semantics": "last_direct_observation_not_omniscient_current_state",\n    }\n    encoded = json.dumps(result, sort_keys=True)\n    assert "enemy.1" not in encoded\n    assert "enemy.5" not in encoded\n\n\ndef test_confirmed_observed_count_is_explicitly_not_current_active_strength():\n    combat = {\n        "combat_id": "combat:test",\n        "status": "active",\n        "elapsed_ms": 1,\n        "sides": {"side_a": ["pc"], "side_b": ["enemy.1"]},\n        "combatants": {\n            "pc": {\n                "status_families": [],\n                "observed_refs": ["enemy.1"],\n                "observed_status_families": {"enemy.1": ["dead"]},\n            },\n            "enemy.1": {"status_families": ["dead"]},\n        },\n    }\n    result = combat_observation_scene_projection(read_json=_reader(combat), player_id="pc")\n    assert result is not None\n    assert result["player_observation"]["confirmed_observed_hostile_count"] == 1\n    assert result["player_hostile_status_observation"]["last_observed_dead_count"] == 1\n    assert result["count_semantics"] == "confirmed_observed_hostiles_ever_detected_not_current_active_or_total_force"\n''')
