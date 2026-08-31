from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected source block missing in {path}: {old[:120]!r}")
    if text.count(old) != 1:
        raise RuntimeError(f"expected exactly one source block in {path}, found {text.count(old)}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_between(path: Path, start: str, end: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    a = text.find(start)
    if a < 0:
        raise RuntimeError(f"start marker missing in {path}: {start!r}")
    b = text.find(end, a)
    if b < 0:
        raise RuntimeError(f"end marker missing in {path}: {end!r}")
    path.write_text(text[:a] + replacement + text[b:], encoding="utf-8")


# 1. Exact-combat automatic weapon selection must never choose a projectile that
# cannot physically reach the target at declaration time. Explicitly released
# projectiles can still miss later because bodies move; this closes only the
# guaranteed-impossible AI/doctrine selection path.
exact = ROOT / "runtime/shinobi_runtime/martial_world/exact_combat.py"
replace_once(
    exact,
    '    def effective_skill(ref: str) -> int:\n        discipline=str(weapons[ref].get("discipline") or "")\n        return int(_skills(person).get(discipline,0))*_weapon_condition_milli(equipment_ledger,person_ref,ref)//1000\n    if target_distance_mm>bow_threshold and bows and int(items.get("item_arrow",0))>0:\n',
    '    def effective_skill(ref: str) -> int:\n        discipline=str(weapons[ref].get("discipline") or "")\n        return int(_skills(person).get(discipline,0))*_weapon_condition_milli(equipment_ledger,person_ref,ref)//1000\n    def projectile_reaches(ref: str) -> bool:\n        row=conditioned(ref)\n        try:\n            maximum_range_mm=max(0,int(round(float(row.get("maximum_range_m",0))*1000)))\n        except (TypeError,ValueError):\n            return False\n        return maximum_range_mm>0 and target_distance_mm<=maximum_range_mm\n    bows=[ref for ref in bows if projectile_reaches(ref)]\n    if target_distance_mm>bow_threshold and bows and int(items.get("item_arrow",0))>0:\n',
)
replace_once(
    exact,
    '    thrown=[str(ref) for ref,q in items.items() if int(q)>0 and isinstance(weapons.get(ref),Mapping) and weapons[ref].get("discipline")=="hidden_weapons" and _weapon_condition_milli(equipment_ledger,person_ref,str(ref))>0]\n',
    '    thrown=[str(ref) for ref,q in items.items() if int(q)>0 and isinstance(weapons.get(ref),Mapping) and weapons[ref].get("discipline")=="hidden_weapons" and _weapon_condition_milli(equipment_ledger,person_ref,str(ref))>0 and projectile_reaches(str(ref))]\n',
)
replace_once(
    exact,
    '    actor_ref=action.actor_ref; target_ref=action.target_ref; event_base={"actor_ref":actor_ref,"intended_ref":target_ref,"action_kind":action.action_kind,"weapon_ref":action.weapon_ref,"poison_ref":action.poison_ref,"decision_origin":action.decision_origin,"declared_at_ms":action.declared_at_ms,"start_at_ms":action.start_at_ms,"ready_delay_ms":action.ready_delay_ms,"previous_ready_weapon_ref":action.previous_ready_weapon_ref,"commit_at_ms":action.commit_at_ms,"release_at_ms":action.release_at_ms,"contact_at_ms":action.contact_at_ms,"recovery_end_ms":action.recovery_end_ms}\n',
    '    actor_ref=action.actor_ref; target_ref=action.target_ref; event_base={"actor_ref":actor_ref,"intended_ref":target_ref,"action_kind":action.action_kind,"weapon_ref":action.weapon_ref,"poison_ref":action.poison_ref,"hit_zone":action.hit_zone,"target_structure_ref":action.target_structure_ref,"decision_origin":action.decision_origin,"declared_at_ms":action.declared_at_ms,"start_at_ms":action.start_at_ms,"ready_delay_ms":action.ready_delay_ms,"previous_ready_weapon_ref":action.previous_ready_weapon_ref,"commit_at_ms":action.commit_at_ms,"release_at_ms":action.release_at_ms,"contact_at_ms":action.contact_at_ms,"recovery_end_ms":action.recovery_end_ms}\n',
)

# 2. Current-transition recovery keeps raw receipt evidence, but also derives one
# bounded combat-narrative spine from the whole committed span. This makes the
# material chronology salient without deleting or replacing exact evidence.
transition = ROOT / "runtime/shinobi_runtime/api/transition_operations.py"
transition_text = transition.read_text(encoding="utf-8")
marker = '\ndef current_transition_projection(\n'
if marker not in transition_text:
    raise RuntimeError("current transition projection marker missing")
helper = r'''

def _compact_mapping(value: object, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: value.get(key)
        for key in keys
        if value.get(key) not in (None, "", [], {})
    }


def _combat_narrative_beat(event: Mapping[str, Any]) -> dict[str, Any] | None:
    result = str(event.get("result") or "")
    resource = event.get("resource_commit") if isinstance(event.get("resource_commit"), Mapping) else {}
    qi = event.get("qi") if isinstance(event.get("qi"), Mapping) else {}
    damage = event.get("damage") if isinstance(event.get("damage"), Mapping) else {}
    wound = damage.get("wound") if isinstance(damage.get("wound"), Mapping) else {}
    physiology = event.get("physiology") if isinstance(event.get("physiology"), Mapping) else {}
    actual_ref = event.get("actual_ref")
    intended_ref = event.get("intended_ref")
    material = bool(
        result in {
            "contact", "mount_contact", "mount_disabled", "escaped", "dead", "incapacitated",
            "action_interrupted_before_commitment", "action_disrupted_after_commitment_before_release",
            "action_interrupted_by_defense_before_commitment", "action_disrupted_by_defense_after_commitment",
        }
        or wound
        or bool(resource.get("poison_dose_consumed"))
        or (isinstance(actual_ref, str) and isinstance(intended_ref, str) and actual_ref != intended_ref)
        or str(physiology.get("status") or "") in {"dead", "incapacitated", "unconscious"}
    )
    if not material:
        return None
    beat: dict[str, Any] = {}
    for key in (
        "actor_ref", "intended_ref", "actual_ref", "action_kind", "weapon_ref", "poison_ref",
        "hit_zone", "target_structure_ref", "result",
    ):
        value = event.get(key)
        if value not in (None, "", [], {}):
            beat[key] = value
    for time_key in ("contact_at_ms", "release_at_ms", "commit_at_ms", "start_at_ms"):
        value = event.get(time_key)
        if isinstance(value, int) and not isinstance(value, bool):
            beat["at_ms"] = value
            break
    approach = _compact_mapping(event.get("approach"), ("reason", "moved", "distance_mm", "remaining_mm", "required_mm"))
    if approach:
        beat["approach"] = approach
    defense = _compact_mapping(event.get("defense"), ("response", "detected", "reason", "reaction_delay_ms", "recovery_ms"))
    if defense:
        beat["defense"] = defense
    if wound:
        beat["wound"] = _compact_mapping(
            wound,
            (
                "zone", "structure_ref", "side", "severity", "bleeding_ml_per_min", "fracture",
                "tendon_damage", "nerve_damage", "organ_trauma", "function_loss_pct", "pain",
            ),
        )
    contact = _compact_mapping(event.get("contact"), ("channel", "zone", "structure_ref", "contact_kind", "penetration", "impact"))
    if contact:
        beat["contact"] = contact
    if resource:
        compact_resource = _compact_mapping(resource, ("ok", "projectile_ref", "poison_ref", "poison_dose_consumed"))
        if compact_resource:
            beat["resource_commit"] = compact_resource
    qi_spent = qi.get("current_qi_milli_spent")
    if isinstance(qi_spent, int) and not isinstance(qi_spent, bool) and qi_spent > 0:
        beat["qi_milli_spent"] = qi_spent
    fatigue = event.get("fatigue") if isinstance(event.get("fatigue"), Mapping) else {}
    fatigue_added = fatigue.get("added_milli")
    if isinstance(fatigue_added, int) and not isinstance(fatigue_added, bool) and fatigue_added > 0:
        beat["fatigue_milli_added"] = fatigue_added
    poison = _compact_mapping(event.get("poison"), ("poison_ref", "burden_added", "current_burden", "burden_after"))
    if poison:
        beat["poison_effect"] = poison
    return beat


def _combat_narrative_summary(raw_events: list[Any], opposing_refs: frozenset[str]) -> dict[str, Any]:
    material: list[dict[str, Any]] = []
    routine_counts: dict[str, int] = {}
    resource_summary = {
        "projectiles_committed": 0,
        "poison_doses_consumed": 0,
        "qi_milli_spent": 0,
        "fatigue_milli_added": 0,
    }
    for raw in raw_events:
        if not isinstance(raw, Mapping):
            continue
        action_kind = str(raw.get("action_kind") or "unknown")
        result = str(raw.get("result") or "unknown")
        routine_key = f"{action_kind}:{result}"
        routine_counts[routine_key] = routine_counts.get(routine_key, 0) + 1
        resource = raw.get("resource_commit") if isinstance(raw.get("resource_commit"), Mapping) else {}
        if resource.get("ok") is True and isinstance(resource.get("projectile_ref"), str):
            resource_summary["projectiles_committed"] += 1
        if resource.get("poison_dose_consumed") is True:
            resource_summary["poison_doses_consumed"] += 1
        qi = raw.get("qi") if isinstance(raw.get("qi"), Mapping) else {}
        spent = qi.get("current_qi_milli_spent")
        if isinstance(spent, int) and not isinstance(spent, bool) and spent > 0:
            resource_summary["qi_milli_spent"] += spent
        fatigue = raw.get("fatigue") if isinstance(raw.get("fatigue"), Mapping) else {}
        added = fatigue.get("added_milli")
        if isinstance(added, int) and not isinstance(added, bool) and added > 0:
            resource_summary["fatigue_milli_added"] += added
        beat = _combat_narrative_beat(raw)
        if beat is not None:
            material.append(beat)

    material_limit = 96
    safe_beats = _sanitize_opposing_refs(material[:material_limit], opposing_refs)
    if not isinstance(safe_beats, list):
        safe_beats = []
    counts = [
        {"event_kind": key, "count": count}
        for key, count in sorted(routine_counts.items())
    ]
    return {
        "source": "complete_current_transition_receipt",
        "event_count": len(raw_events),
        "material_event_count": len(material),
        "material_beats": safe_beats,
        "material_beats_truncated": len(material) > material_limit,
        "omitted_material_beat_count": max(0, len(material) - material_limit),
        "event_kind_counts": counts,
        "resource_summary": resource_summary,
        "narration_rule": (
            "Use material_beats as the primary chronological scene spine. Routine counts summarize repeated no-change work. "
            "Raw event pages remain exact evidence for audit, negative claims, or detail that the compact spine does not establish."
        ),
    }
'''
transition.write_text(transition_text.replace(marker, helper + marker, 1), encoding="utf-8")

# Add the summary only on the first page; subsequent raw pages stay bounded and
# avoid repeating the same summary dozens of times.
replace_once(
    transition,
    '    command_redacted = original_command is not None and command_record != original_command\n    payload = {\n',
    '    command_redacted = original_command is not None and command_record != original_command\n    combat_narrative = _combat_narrative_summary(raw_events, opposing_refs) if is_combat and event_offset == 0 else None\n    payload = {\n',
)
replace_once(
    transition,
    '        "events_withheld": False,\n        "next_object_ref": next_ref,\n',
    '        "events_withheld": False,\n        "combat_narrative": combat_narrative,\n        "next_object_ref": next_ref,\n',
)

# 3. Large-combat GM context remains omniscient but stops serializing a complete
# character sheet and full assignment map for every body. Every participant stays
# indexed with identity, side, current position/status and tactical assignment;
# a deterministic focal window gets full sheets. Small combats retain full detail.
travel = ROOT / "runtime/shinobi_runtime/api/travel_operations.py"
new_projection = r'''def gm_private_combat_director_projection(
    *,
    read_json: Callable[[str], Any],
    sheet_resolver: Callable[[str], Mapping[str, Any]],
    player_id: str,
    participant_limit: int = 128,
    focus_limit: int = 8,
) -> dict[str, Any] | None:
    """Return exact-combat director truth without turning a large fight into a world dump.

    The resolver continues to use full exact person/combat state for every body.
    This is narration transport only: every admitted participant remains indexed,
    while full character sheets are concentrated on the current focal actors.
    """
    active = active_combat_for_person(read_json, player_id)
    if active is None:
        return None
    combat_ref, combat = active
    if not isinstance(combat, Mapping):
        return None
    sides = combat.get("sides", {}) if isinstance(combat.get("sides"), Mapping) else {}
    combatants = combat.get("combatants", {}) if isinstance(combat.get("combatants"), Mapping) else {}
    positions = combat.get("positions", {}) if isinstance(combat.get("positions"), Mapping) else {}
    team_plans = combat.get("team_plans", {}) if isinstance(combat.get("team_plans"), Mapping) else {}
    player_side_ref = next((str(side) for side, refs in sides.items() if isinstance(refs, list) and player_id in refs), None)
    player_state = combatants.get(player_id, {}) if isinstance(combatants.get(player_id), Mapping) else {}
    player_observed = {str(x) for x in player_state.get("observed_refs", []) if isinstance(x, str)}

    refs: list[tuple[str, str]] = []
    for side_ref, raw_refs in sides.items():
        if not isinstance(raw_refs, list):
            continue
        for ref in raw_refs:
            if isinstance(ref, str) and ref and all(existing_ref != ref for _side, existing_ref in refs):
                refs.append((str(side_ref), ref))
    limit = max(1, min(128, int(participant_limit)))
    admitted = refs[:limit]
    try:
        equipment_ledger = read_json("state/martial-world/equipment-ledger.json")
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        equipment_ledger = {}

    assignments: dict[str, dict[str, Any]] = {}
    plan_summaries: dict[str, dict[str, Any]] = {}
    primary_threats: set[str] = set()
    targeting_player: set[str] = set()
    for side_ref, raw_plan in team_plans.items():
        if not isinstance(raw_plan, Mapping):
            continue
        primary = raw_plan.get("primary_threat_ref")
        if isinstance(primary, str) and primary:
            primary_threats.add(primary)
        raw_assignments = raw_plan.get("assignments", {}) if isinstance(raw_plan.get("assignments"), Mapping) else {}
        role_counts: dict[str, int] = {}
        for actor_ref, raw_assignment in raw_assignments.items():
            if not isinstance(actor_ref, str) or not isinstance(raw_assignment, Mapping):
                continue
            assignment = {
                key: raw_assignment.get(key)
                for key in ("role", "target_ref", "preferred_action", "requires_line_of_sight")
                if raw_assignment.get(key) not in (None, "", [], {})
            }
            assignments[actor_ref] = assignment
            role = str(raw_assignment.get("role") or "unassigned")
            role_counts[role] = role_counts.get(role, 0) + 1
            if raw_assignment.get("target_ref") == player_id:
                targeting_player.add(actor_ref)
        plan_summaries[str(side_ref)] = {
            key: raw_plan.get(key)
            for key in (
                "plan_id", "objective_kind", "primary_threat_ref", "tactical_problem",
                "desired_states", "coordination_latency_ms", "replan_reasons",
            )
            if raw_plan.get(key) not in (None, "", [], {})
        }
        plan_summaries[str(side_ref)]["assignment_role_counts"] = role_counts

    records: dict[str, dict[str, Any]] = {}
    for side_ref, ref in admitted:
        state = combatants.get(ref, {}) if isinstance(combatants.get(ref), Mapping) else {}
        position = positions.get(ref, {}) if isinstance(positions.get(ref), Mapping) else {}
        try:
            person = sheet_resolver(ref)
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            person = {}
        if not isinstance(person, Mapping):
            person = {}
        try:
            loadout = effective_person_loadout(equipment_ledger, ref) if isinstance(equipment_ledger, Mapping) else {}
        except (KeyError, TypeError, ValueError):
            loadout = {}
        records[ref] = {
            "side_ref": side_ref,
            "state": state,
            "position": position,
            "person": person,
            "loadout": loadout,
        }

    player_position = positions.get(player_id, {}) if isinstance(positions.get(player_id), Mapping) else {}
    px = int(player_position.get("x_mm", 0)); py = int(player_position.get("y_mm", 0))
    def distance_key(ref: str) -> tuple[int, str]:
        row = records.get(ref, {}).get("position", {})
        if not isinstance(row, Mapping):
            return (10**30, ref)
        dx = int(row.get("x_mm", 0)) - px; dy = int(row.get("y_mm", 0)) - py
        return (dx * dx + dy * dy, ref)

    focus_max = max(1, min(16, int(focus_limit)))
    focus: list[str] = []
    def add_focus(ref: str) -> None:
        if ref in records and ref not in focus and len(focus) < focus_max:
            focus.append(ref)
    add_focus(player_id)
    same_side = sorted(
        (ref for side, ref in admitted if side == player_side_ref and ref != player_id),
        key=distance_key,
    )
    for ref in same_side[:2]:
        add_focus(ref)
    hostile_priority = sorted(
        (ref for side, ref in admitted if side != player_side_ref and (ref in targeting_player or ref in primary_threats)),
        key=lambda ref: (0 if ref in targeting_player else 1, *distance_key(ref)),
    )
    for ref in hostile_priority:
        add_focus(ref)
    nearest = sorted((ref for _side, ref in admitted if ref != player_id), key=distance_key)
    for ref in nearest:
        add_focus(ref)
    detailed_refs = [ref for _side, ref in admitted] if len(admitted) <= 12 else focus

    participant_index: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []
    for side_ref, ref in admitted:
        record = records[ref]
        state = record["state"] if isinstance(record["state"], Mapping) else {}
        position = record["position"] if isinstance(record["position"], Mapping) else {}
        person = record["person"] if isinstance(record["person"], Mapping) else {}
        loadout = record["loadout"] if isinstance(record["loadout"], Mapping) else {}
        injury = _injury_director_summary(person)
        compact_health = {
            key: injury.get(key)
            for key in ("status", "shock", "injury_count")
            if injury.get(key) not in (None, "", [], {})
        }
        compact_state = {
            key: state.get(key)
            for key in ("status_families", "ready_weapon_ref", "weapon_position")
            if state.get(key) not in (None, "", [], {})
        }
        compact_position = {
            key: position.get(key)
            for key in ("x_mm", "y_mm", "elevation_mm", "stance")
            if position.get(key) is not None
        }
        index_row: dict[str, Any] = {
            "person_ref": ref,
            "name": person.get("name"),
            "side_ref": side_ref,
            "relation_to_player": "same_side" if side_ref == player_side_ref else "opposing_side",
            "arrived": bool(combat_person_arrived(combat, ref)),
            "player_has_observed_person": bool(ref == player_id or side_ref == player_side_ref or ref in player_observed),
            "health": compact_health,
            "position": compact_position,
            "combat_state": compact_state,
            "team_assignment": assignments.get(ref),
        }
        participant_index.append({key: value for key, value in index_row.items() if value not in (None, {}, [])})
        if ref not in detailed_refs:
            continue
        detail: dict[str, Any] = {
            "person_ref": ref,
            "name": person.get("name"),
            "faction_ref": person.get("faction_ref"),
            "membership_grade": person.get("membership_grade"),
            "side_ref": side_ref,
            "relation_to_player": "same_side" if side_ref == player_side_ref else "opposing_side",
            "arrived": bool(combat_person_arrived(combat, ref)),
            "player_has_observed_person": bool(ref == player_id or side_ref == player_side_ref or ref in player_observed),
            "fatigue_milli": person.get("fatigue_milli"),
            "attributes": dict(person.get("attributes", {})) if isinstance(person.get("attributes"), Mapping) else {},
            "martial_skills": dict(person.get("martial_skills", {})) if isinstance(person.get("martial_skills"), Mapping) else {},
            "qi": person.get("qi"),
            "qi_control": person.get("qi_control"),
            "current_qi_milli": person.get("current_qi_milli"),
            "health": injury,
            "equipment": {
                "items": dict(loadout.get("items", {})) if isinstance(loadout.get("items"), Mapping) else {},
                "condition_milli": dict(loadout.get("condition_milli", {})) if isinstance(loadout.get("condition_milli"), Mapping) else {},
            },
            "position": {
                key: position.get(key)
                for key in ("x_mm", "y_mm", "elevation_mm", "facing_mdeg", "vx_mmps", "vy_mmps", "stance", "cover_milli")
                if position.get(key) is not None
            },
            "combat_state": {
                key: state.get(key)
                for key in (
                    "status_families", "balance_milli", "limb_commitment_milli", "recovery_until_ms",
                    "weapon_position", "ready_weapon_ref", "surprise_milli", "awareness_confidence_milli",
                    "concealment_milli", "qi_allocation_milli",
                )
                if state.get(key) not in (None, [], {})
            },
            "team_assignment": assignments.get(ref),
        }
        cognition = _gm_private_person_cognition(person, {})
        if cognition:
            detail["gm_private_cognition"] = cognition
        detail_rows.append({key: value for key, value in detail.items() if value not in (None, {}, [])})

    obstacle_rows = combat.get("obstacles", []) if isinstance(combat.get("obstacles"), list) else []
    environment = dict(combat.get("environment", {})) if isinstance(combat.get("environment"), Mapping) else {}
    environment.pop("obstacles", None)
    encounter_causality: dict[str, Any] = {}
    try:
        route_ops = read_json("state/martial-world/route-operations.json")
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        route_ops = {}
    contacts = route_ops.get("contacts", {}) if isinstance(route_ops, Mapping) else {}
    if isinstance(contacts, Mapping):
        active_contact = next(
            (
                row for row in contacts.values()
                if isinstance(row, Mapping) and row.get("status") == "active" and row.get("combat_ref") == combat_ref
            ),
            None,
        )
        if isinstance(active_contact, Mapping):
            causal, causal_source = resolved_contact_causality(active_contact, route_ops, read_json=read_json)
            attacker_refs = [str(ref) for ref in causal.get("attacker_refs", []) if isinstance(ref, str)] if isinstance(causal.get("attacker_refs"), list) else []
            encounter_causality = {
                key: causal.get(key)
                for key in (
                    "movement_ref", "route_ref", "attacker_faction_ref", "attacker_intent", "motive_kind",
                    "gm_private_decision_context",
                )
                if causal.get(key) not in (None, "", [], {})
            }
            if attacker_refs:
                encounter_causality["attacker_count"] = len(attacker_refs)
                if len(admitted) <= 12:
                    encounter_causality["attacker_refs"] = attacker_refs
            if encounter_causality:
                encounter_causality["source"] = causal_source

    observed_hostile = sorted(ref for ref in player_observed if any(side != player_side_ref and r == ref for side, r in refs))
    observation_boundary: dict[str, Any] = {
        "observed_hostile_person_count": len(observed_hostile),
        "rule": "Use the private packet to direct coherent action, but narrate hidden identities, positions, motives, injuries, plans, or capabilities only after Wei can perceive/infer them or another lawful source communicates them.",
    }
    if len(admitted) <= 12:
        observation_boundary["observed_hostile_person_refs"] = observed_hostile

    large = len(admitted) > 12
    packet: dict[str, Any] = {
        "privacy": "gm_private_scene_bounded_omniscient_truth_not_player_knowledge",
        "combat_ref": combat_ref,
        "world_truth_scope": "exact_active_combat_only",
        "elapsed_ms": combat.get("elapsed_ms"),
        "zone_ref": combat.get("zone_ref"),
        "objective": dict(combat.get("objective", {})) if isinstance(combat.get("objective"), Mapping) else combat.get("objective"),
        "environment": environment,
        "team_plans": dict(team_plans) if not large else plan_summaries,
        "team_plan_projection_mode": "full_small_combat" if not large else "compact_large_combat",
        "participants": detail_rows if not large else participant_index,
        "focus_participants": detail_rows,
        "focus_participant_refs": detailed_refs,
        "participant_projection_mode": "full_small_combat" if not large else "compact_large_combat_with_focal_full_sheets",
        "participant_count": len(refs),
        "participant_index_count": len(admitted),
        "participants_truncated": len(refs) > limit,
        "omitted_participant_count": max(0, len(refs) - limit),
        "obstacles": [dict(row) for row in obstacle_rows[:32] if isinstance(row, Mapping)],
        "obstacle_count": len(obstacle_rows),
        "encounter_causality": encounter_causality,
        "player_observation_boundary": observation_boundary,
        "director_rule": (
            "The exact resolver still uses full sheets and exact combat state for every participant. This packet is narration transport only. "
            "In large fights, participants is a complete compact tactical index for the bounded combat roster and focus_participants contains richer sheets for the immediate focal actors. "
            "Use current-transition combat_narrative material beats as the primary causal chronology when available. Hidden truth remains director context, not Wei knowledge."
        ),
    }
    return {key: value for key, value in packet.items() if value not in (None, [], {})}


'''
replace_between(
    travel,
    'def gm_private_combat_director_projection(\n',
    'def movement_scene_projection(\n',
    new_projection,
)

# 4. Teach the repository Skill to prefer the compact causal spine while keeping
# raw event pagination for exact audit and negative claims.
skill_combat = ROOT / "plugins/shinobi-rpg/skill/shinobi-game-master/references/combat.md"
skill_text = skill_combat.read_text(encoding="utf-8")
needle = (
    "A combat command receipt that contains ordered `events` is transition evidence. Preserve that event sequence while refreshing play context. "
    "The refreshed context establishes current truth; the receipt establishes how the committed transition happened. Do not throw away the events and reconstruct the fight afterward from final health totals.\n"
)
addition = needle + (
    "When the current-transition projection provides `combat_narrative`, use its ordered `material_beats` as the primary scene spine and its routine/event/resource summaries for compression. "
    "Those fields are a deterministic projection of the same committed receipt, not a second combat authority. Demand-load raw event pages when a material detail is absent, when exact ordering beyond the compact spine matters, or before making a negative claim such as no wound, no kill, no Qi use, or no poison/ammunition expenditure.\n"
)
if needle not in skill_text:
    raise RuntimeError("combat skill narration anchor missing")
skill_combat.write_text(skill_text.replace(needle, addition, 1), encoding="utf-8")

# 5. Focused regressions. These are generic and contain no Black Lance IDs.
test = ROOT / "tests/runtime/test_systemic_combat_range_narration.py"
test.write_text(r'''from __future__ import annotations

import json
from types import SimpleNamespace

from shinobi_runtime.api import transition_operations
from shinobi_runtime.api import travel_operations
from shinobi_runtime.martial_world import exact_combat


def _person(ref: str, *, side: str = "a") -> dict:
    return {
        "person_id": ref,
        "name": ref,
        "faction_ref": f"faction_{side}",
        "membership_grade": "full",
        "fatigue_milli": 0,
        "attributes": {
            "strength": 60, "speed": 60, "dexterity": 60, "endurance": 60,
            "perception": 60, "intelligence": 60, "willpower": 60,
        },
        "martial_skills": {
            "sword": 0, "spear": 0, "bow": 0, "hidden_weapons": 80,
            "unarmed": 40, "stealth_scouting": 20, "command": 20,
        },
        "qi": 20,
        "qi_control": 20,
        "current_qi_milli": 20000,
        "health": {"status": "ready", "shock": 0, "injuries": []},
    }


def test_auto_hidden_weapon_selection_respects_physical_maximum_range():
    actor = _person("actor")
    ledger = {
        "schema": "jianghu-equipment-ledger-1.0",
        "person_loadouts": {"actor": {"items": {"weapon_needle": 3}}},
    }
    close_kind, close_weapon = exact_combat._default_weapon_for(
        "actor", actor, ledger, target_distance_mm=5000
    )
    assert (close_kind, close_weapon) == ("hidden_weapon_throw", "weapon_needle")

    far_kind, far_weapon = exact_combat._default_weapon_for(
        "actor", actor, ledger, target_distance_mm=30000
    )
    assert far_kind == "unarmed_strike"
    assert far_weapon == "body_unarmed"


def test_event_record_preserves_intended_anatomical_target_even_on_early_failure():
    action = SimpleNamespace(
        actor_ref="actor", target_ref="missing", action_kind="cut", weapon_ref="weapon_jian",
        poison_ref=None, hit_zone="right_arm", target_structure_ref="right_wrist",
        decision_origin="standing_doctrine", declared_at_ms=0, start_at_ms=1, ready_delay_ms=0,
        previous_ready_weapon_ref="weapon_jian", commit_at_ms=2, release_at_ms=3,
        contact_at_ms=4, recovery_end_ms=5,
    )
    event = exact_combat._resolve_scheduled_action(
        combat={}, action=action, people={"actor": _person("actor")}, equipment_ledger={}
    )
    assert event["result"] == "invalid_target"
    assert event["hit_zone"] == "right_arm"
    assert event["target_structure_ref"] == "right_wrist"


def test_combat_narrative_summary_uses_actual_resource_and_qi_keys():
    events = [
        {
            "actor_ref": "player", "intended_ref": "enemy", "actual_ref": "enemy",
            "action_kind": "hidden_weapon_throw", "weapon_ref": "weapon_needle",
            "poison_ref": "cardiotoxic", "hit_zone": "right_arm",
            "target_structure_ref": "right_wrist", "result": "miss_no_spatial_intersection",
            "contact_at_ms": 100,
            "resource_commit": {
                "ok": True, "projectile_ref": "weapon_needle", "poison_ref": "cardiotoxic",
                "poison_dose_consumed": True,
            },
            "qi": {"current_qi_milli_spent": 125},
            "fatigue": {"added_milli": 7},
        },
        {
            "actor_ref": "player", "intended_ref": "enemy", "actual_ref": "enemy",
            "action_kind": "cut", "weapon_ref": "weapon_jian", "result": "contact",
            "contact_at_ms": 200, "hit_zone": "right_arm", "target_structure_ref": "right_wrist",
            "damage": {"wound": {"zone": "arm", "structure_ref": "forearm", "severity": 40}},
        },
    ]
    summary = transition_operations._combat_narrative_summary(events, frozenset({"enemy"}))
    assert summary["resource_summary"]["projectiles_committed"] == 1
    assert summary["resource_summary"]["poison_doses_consumed"] == 1
    assert summary["resource_summary"]["qi_milli_spent"] == 125
    assert summary["resource_summary"]["fatigue_milli_added"] == 7
    assert len(summary["material_beats"]) == 2
    assert summary["material_beats"][0]["resource_commit"]["projectile_ref"] == "weapon_needle"
    assert summary["material_beats"][1]["target_structure_ref"] == "right_wrist"
    assert summary["material_beats"][1]["wound"]["structure_ref"] == "forearm"
    assert summary["material_beats"][1]["intended_ref"] == "opposing_combatant"


def test_large_combat_keeps_complete_compact_index_and_bounded_full_sheets(monkeypatch):
    player = "p0"
    friends = [player, *[f"a{i}" for i in range(1, 12)]]
    enemies = [f"b{i}" for i in range(49)]
    refs = friends + enemies
    combatants = {
        ref: {
            "observed_refs": enemies if ref == player else [],
            "status_families": [], "ready_weapon_ref": "weapon_jian", "weapon_position": "guard",
        }
        for ref in refs
    }
    positions = {
        ref: {"x_mm": i * 350, "y_mm": (i % 7) * 500, "elevation_mm": 0, "stance": "ready"}
        for i, ref in enumerate(refs)
    }
    combat = {
        "combat_id": "combat:test", "elapsed_ms": 5000, "zone_ref": "route.test",
        "sides": {"side_a": friends, "side_b": enemies},
        "combatants": combatants, "positions": positions,
        "team_plans": {
            "side_b": {
                "plan_id": "plan:b", "primary_threat_ref": player, "tactical_problem": "multiple_threats",
                "desired_states": ["maintain_mutual_support"],
                "assignments": {
                    ref: {"role": "pressure", "target_ref": player, "preferred_action": "attack"}
                    for ref in enemies
                },
            }
        },
        "environment": {"terrain": "hills", "obstacles": [{"obstacle_ref": "dup"}]},
        "obstacles": [],
    }
    people = {ref: _person(ref, side="a" if ref in friends else "b") for ref in refs}
    monkeypatch.setattr(travel_operations, "active_combat_for_person", lambda read_json, player_id: ("combat:test", combat))
    monkeypatch.setattr(travel_operations, "combat_person_arrived", lambda combat, ref: True)

    def read_json(path: str):
        if path == "state/martial-world/equipment-ledger.json":
            return {"schema": "jianghu-equipment-ledger-1.0"}
        if path == "state/martial-world/route-operations.json":
            return {"contacts": {}}
        raise FileNotFoundError(path)

    packet = travel_operations.gm_private_combat_director_projection(
        read_json=read_json, sheet_resolver=lambda ref: people[ref], player_id=player
    )
    assert packet is not None
    assert packet["participant_count"] == 61
    assert packet["participant_index_count"] == 61
    assert len(packet["participants"]) == 61
    assert len(packet["focus_participants"]) <= 8
    assert packet["participant_projection_mode"] == "compact_large_combat_with_focal_full_sheets"
    assert all("attributes" not in row for row in packet["participants"])
    assert all("attributes" in row for row in packet["focus_participants"])
    assert "assignments" not in packet["team_plans"]["side_b"]
    assert "obstacles" not in packet["environment"]
    assert len(json.dumps(packet, sort_keys=True, separators=(",", ":"))) < 48000


def test_small_combat_retains_full_director_sheets(monkeypatch):
    player = "p0"; enemy = "b0"
    combat = {
        "combat_id": "combat:small", "sides": {"side_a": [player], "side_b": [enemy]},
        "combatants": {player: {"observed_refs": [enemy]}, enemy: {"observed_refs": [player]}},
        "positions": {
            player: {"x_mm": 0, "y_mm": 0, "elevation_mm": 0, "stance": "ready"},
            enemy: {"x_mm": 1000, "y_mm": 0, "elevation_mm": 0, "stance": "ready"},
        },
        "team_plans": {}, "obstacles": [],
    }
    people = {player: _person(player), enemy: _person(enemy, side="b")}
    monkeypatch.setattr(travel_operations, "active_combat_for_person", lambda read_json, player_id: ("combat:small", combat))
    monkeypatch.setattr(travel_operations, "combat_person_arrived", lambda combat, ref: True)
    def read_json(path: str):
        if path == "state/martial-world/equipment-ledger.json": return {"schema": "jianghu-equipment-ledger-1.0"}
        if path == "state/martial-world/route-operations.json": return {"contacts": {}}
        raise FileNotFoundError(path)
    packet = travel_operations.gm_private_combat_director_projection(
        read_json=read_json, sheet_resolver=lambda ref: people[ref], player_id=player
    )
    assert packet is not None
    assert packet["participant_projection_mode"] == "full_small_combat"
    assert len(packet["participants"]) == 2
    assert all("attributes" in row and "martial_skills" in row for row in packet["participants"])
''', encoding="utf-8")

print("systemic combat range/narration patch applied")
