from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Bound each melee declaration to a short physical closing slice. Longer pursuit
# remains possible across exchanges, but one attack can no longer reserve tens
# of seconds of future combat time merely because the target is far away.
combat_path = ROOT / "game/data/martial-world/combat.json"
combat = json.loads(combat_path.read_text(encoding="utf-8"))
combat["maximum_melee_approach_ms"] = 2000
combat_path.write_text(json.dumps(combat, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# Public command contract: a compound combat declaration may contain a real
# rally attempt in addition to the player's personal attack.
replace_once(
    "runtime/shinobi_runtime/commands/specs.py",
    '"exchange_count","duration_seconds","until_resolution","improvised_prop_fact_ref"),(),"Start or resolve anatomy-first exact Jianghu combat',
    '"exchange_count","duration_seconds","until_resolution","improvised_prop_fact_ref","rally_allies"),(),"Start or resolve anatomy-first exact Jianghu combat',
)
replace_once(
    "runtime/shinobi_runtime/commands/specs.py",
    '("poison_ref","qi_allocation_milli","exchange_count","duration_seconds","until_resolution","improvised_prop_fact_ref")),\n    "disengage"',
    '("poison_ref","qi_allocation_milli","exchange_count","duration_seconds","until_resolution","improvised_prop_fact_ref","rally_allies")),\n    "disengage"',
)
replace_once(
    "runtime/shinobi_runtime/commands/specs.py",
    'Optional exchange_count, duration_seconds, or until_resolution extends the same high-level attack intent across a bounded combat span; doctrine is re-evaluated each exchange.',
    'Optional exchange_count, duration_seconds, or until_resolution extends the same high-level attack intent across a bounded combat span; doctrine is re-evaluated each exchange. Optional rally_allies records a real contested leadership attempt to arrest noncritical allied withdrawal; it never guarantees obedience or overrides critical physical condition.',
)

# Route the rally flag through the semantic command wrapper into exact combat.
replace_once(
    "runtime/shinobi_runtime/commands/jianghu_extended.py",
    'exchange_count: int | None, duration_seconds: int | None, until_resolution: bool,\n    player_improvised_weapon_state: Mapping[str, Any] | None = None,',
    'exchange_count: int | None, duration_seconds: int | None, until_resolution: bool,\n    rally_allies: bool = False,\n    player_improvised_weapon_state: Mapping[str, Any] | None = None,',
)
replace_once(
    "runtime/shinobi_runtime/commands/jianghu_extended.py",
    'player_auto_qi=bool(qi_auto),player_auto_poison=bool(poison_auto),\n            martial_familiarity=social_cursor,player_retinue_context=player_retinue_context,',
    'player_auto_qi=bool(qi_auto),player_auto_poison=bool(poison_auto),\n            player_rally_allies=bool(rally_allies),\n            martial_familiarity=social_cursor,player_retinue_context=player_retinue_context,',
)
replace_once(
    "runtime/shinobi_runtime/commands/jianghu_extended.py",
    "            if sum((exchange_count is not None,duration_seconds is not None,until_resolution))>1:\n                raise CommandRejectedError('jianghu_combat_scope_conflict')\n\n            try:\n",
    "            rally_allies=False\n            if 'rally_allies' in command.payload:\n                raw_rally=command.payload.get('rally_allies')\n                if not isinstance(raw_rally,bool):\n                    raise CommandRejectedError('jianghu_combat_rally_allies_invalid')\n                rally_allies=bool(raw_rally)\n            if sum((exchange_count is not None,duration_seconds is not None,until_resolution))>1:\n                raise CommandRejectedError('jianghu_combat_scope_conflict')\n\n            try:\n",
)
replace_once(
    "runtime/shinobi_runtime/commands/jianghu_extended.py",
    'exchange_count=exchange_count,duration_seconds=duration_seconds,until_resolution=until_resolution,\n                    player_improvised_weapon_state=improvised_prop_state,',
    'exchange_count=exchange_count,duration_seconds=duration_seconds,until_resolution=until_resolution,\n                    rally_allies=rally_allies,player_improvised_weapon_state=improvised_prop_state,',
)

# Exact combat: bounded melee approach scheduling.
replace_once(
    "runtime/shinobi_runtime/martial_world/exact_combat.py",
    '''        required = max(0, distance - physical_reach_mm(profile))
        approach_distance_mm = required
        base_speed=max(1,_movement_speed_for_state(actor_ref,people[actor_ref],equipment_ledger,actor_state,cap))
        base_approach_ms=required*1000//base_speed
        qi_preview=_qi_preview(
            person=people[actor_ref],combatant_state=actor_state,
            duration_ms=max(1,decision_ms+ready_delay_ms+base_approach_ms+int(profile.startup_ms)),
        )
        movement_cap=_qi_enhanced_capability(cap,qi_preview)
        approach_ms = required * 1000 // max(1, _movement_speed_for_state(actor_ref, people[actor_ref], equipment_ledger, actor_state, movement_cap))
        params = dict(profile.effect_parameters)
        params["approach_distance_mm"] = approach_distance_mm
        params["approach_time_ms"] = approach_ms
''',
    '''        required = max(0, distance - physical_reach_mm(profile))
        maximum_approach_ms=max(250,int(_combat_rules().get("maximum_melee_approach_ms",2000)))
        base_speed=max(1,_movement_speed_for_state(actor_ref,people[actor_ref],equipment_ledger,actor_state,cap))
        base_approach_ms=min(maximum_approach_ms,required*1000//base_speed)
        qi_preview=_qi_preview(
            person=people[actor_ref],combatant_state=actor_state,
            duration_ms=max(1,decision_ms+ready_delay_ms+base_approach_ms+int(profile.startup_ms)),
        )
        movement_cap=_qi_enhanced_capability(cap,qi_preview)
        movement_speed=max(1,_movement_speed_for_state(actor_ref,people[actor_ref],equipment_ledger,actor_state,movement_cap))
        approach_distance_mm=min(required,movement_speed*maximum_approach_ms//1000)
        approach_ms=approach_distance_mm*1000//movement_speed if approach_distance_mm>0 else 0
        params = dict(profile.effect_parameters)
        params["approach_distance_mm"] = approach_distance_mm
        params["approach_time_ms"] = approach_ms
        params["approach_budget_limited"] = bool(required>approach_distance_mm)
''',
)
replace_once(
    "runtime/shinobi_runtime/martial_world/exact_combat.py",
    '''            approach_reason=str(approach.get("reason") or "")
            result_kind=(
                "target_outpaced_committed_approach"
                if approach_reason in {"partial_committed_approach","target_moved_beyond_committed_approach"}
                else "melee_approach_blocked"
            )
''',
    '''            approach_reason=str(approach.get("reason") or "")
            budget_limited=bool(action.profile.effect_parameters.get("approach_budget_limited",False))
            result_kind=(
                "melee_approach_in_progress"
                if budget_limited and approach_reason=="partial_committed_approach"
                else "target_outpaced_committed_approach"
                if approach_reason in {"partial_committed_approach","target_moved_beyond_committed_approach"}
                else "melee_approach_blocked"
            )
''',
)

# Exact combat: one deterministic contested rally attempt. Command is a real
# professional skill; ally resolve and current withdrawal pressure matter. A
# critical physical condition is never overridden by rhetoric.
rally_helper = r'''

def _rally_withdrawal_attempt(
    *, leader_ref: str, ally_ref: str, people: Mapping[str, Mapping[str, Any]],
    withdrawal: Mapping[str, Any],
) -> dict[str, Any]:
    """Contest one noncritical allied withdrawal with existing human capability.

    This is deliberately not a morale subsystem. It derives one immediate
    leadership contest from the leader's real Command/Willpower/Intelligence,
    the ally's own resolve, and the already-authoritative withdrawal pressure.
    Critical medical/functional withdrawal remains non-negotiable.
    """
    leader=people.get(leader_ref,{}) if isinstance(people.get(leader_ref,{}),Mapping) else {}
    ally=people.get(ally_ref,{}) if isinstance(people.get(ally_ref,{}),Mapping) else {}
    leader_prof=leader.get("professional_skills",{}) if isinstance(leader.get("professional_skills"),Mapping) else {}
    ally_prof=ally.get("professional_skills",{}) if isinstance(ally.get("professional_skills"),Mapping) else {}
    leader_attrs=_attrs(leader); ally_attrs=_attrs(ally)
    leadership=(
        max(0,int(leader_prof.get("command",0)))*4
        + max(0,int(leader_attrs.get("willpower",0)))*2
        + max(0,int(leader_attrs.get("intelligence",0)))
        + max(0,int(ally_attrs.get("willpower",0)))
        + max(0,int(ally_prof.get("command",0)))
    )
    condition=withdrawal.get("condition",{}) if isinstance(withdrawal.get("condition"),Mapping) else {}
    reason=str(withdrawal.get("reason") or "")
    consciousness=max(0,min(100,int(condition.get("consciousness",100))))
    shock=max(0,int(condition.get("shock",0)))
    blood_lost=max(0,int(condition.get("blood_lost_ml",0)))
    functional_floor=max(0,min(1000,int(condition.get("functional_floor_milli",1000))))
    collapse_over=max(
        0,
        int(withdrawal.get("loss_percent",0))-int(withdrawal.get("collapse_threshold_percent",0)),
    )
    pressure=(
        260
        + collapse_over*5
        + shock*3
        + max(0,100-consciousness)*4
        + blood_lost//5
        + max(0,1000-functional_floor)//3
        + max(0,int(withdrawal.get("casualty_preservation",0))-50)
        + max(0,int(withdrawal.get("withdrawal_discipline",0))-50)//2
    )
    if reason=="casualty_preservation":
        pressure+=80
    if reason=="critical_condition":
        return {
            "attempted":True,"success":False,"reason":"critical_condition_not_overridable",
            "leadership_score":leadership,"withdrawal_pressure_score":max(pressure,1000),
        }
    success=leadership>=pressure
    return {
        "attempted":True,"success":success,
        "reason":"rally_strength_met_withdrawal_pressure" if success else "withdrawal_pressure_held",
        "leadership_score":leadership,"withdrawal_pressure_score":pressure,
    }
'''
replace_once(
    "runtime/shinobi_runtime/martial_world/exact_combat.py",
    "\n\ndef _disengage_step(*, combat: dict[str, Any], actor_ref: str, people: Mapping[str, Mapping[str, Any]] | None, equipment_ledger: Mapping[str, Any] | None, duration_ms: int, start_ms: int) -> dict[str, Any]:",
    rally_helper + "\n\ndef _disengage_step(*, combat: dict[str, Any], actor_ref: str, people: Mapping[str, Mapping[str, Any]] | None, equipment_ledger: Mapping[str, Any] | None, duration_ms: int, start_ms: int) -> dict[str, Any]:",
)
replace_once(
    "runtime/shinobi_runtime/martial_world/exact_combat.py",
    'player_improvised_weapon_state: Mapping[str, Any] | None = None, equipment_ledger_hydrated: bool = False, compact_equipment_result: bool = True, mutate_equipment_ledger: bool = False, mutate_state: bool = False) -> dict[str, Any]:',
    'player_improvised_weapon_state: Mapping[str, Any] | None = None, player_rally_allies: bool = False, equipment_ledger_hydrated: bool = False, compact_equipment_result: bool = True, mutate_equipment_ledger: bool = False, mutate_state: bool = False) -> dict[str, Any]:',
)
replace_once(
    "runtime/shinobi_runtime/martial_world/exact_combat.py",
    '''    for actor_ref in active_at_declaration:
        if actor_ref==player_ref: continue
        withdrawal=_npc_withdrawal_decision(combat=out,actor_ref=actor_ref,people=persons,faction_doctrine=doctrines.get(str(persons[actor_ref].get("faction_ref") or ""),{}))
        if withdrawal is None: continue
        withdrawing.append(actor_ref)
        out["positions"][actor_ref]["stance"]="disengaging"
        declaration_events.append({"actor_ref":actor_ref,"result":"withdrawal_declared","decision_origin":"actor_ai","declared_at_ms":declared_exchange_ms,"withdrawal":withdrawal})
''',
    '''    player_side=_side_of(out,player_ref)
    for actor_ref in active_at_declaration:
        if actor_ref==player_ref: continue
        withdrawal=_npc_withdrawal_decision(combat=out,actor_ref=actor_ref,people=persons,faction_doctrine=doctrines.get(str(persons[actor_ref].get("faction_ref") or ""),{}))
        if withdrawal is None: continue
        if bool(player_rally_allies) and _side_of(out,actor_ref)==player_side:
            rally=_rally_withdrawal_attempt(leader_ref=player_ref,ally_ref=actor_ref,people=persons,withdrawal=withdrawal)
            rally_success=bool(rally.get("success"))
            declaration_events.append({
                "actor_ref":actor_ref,"leader_ref":player_ref,
                "result":"rally_held_position" if rally_success else "rally_failed",
                "decision_origin":"player_command","declared_at_ms":declared_exchange_ms,
                "rally":rally,"withdrawal":withdrawal,
            })
            if rally_success:
                out["positions"][actor_ref]["stance"]="ready"
                out["positions"][actor_ref]["vx_mmps"]=0
                out["positions"][actor_ref]["vy_mmps"]=0
                continue
        withdrawing.append(actor_ref)
        out["positions"][actor_ref]["stance"]="disengaging"
        declaration_events.append({"actor_ref":actor_ref,"result":"withdrawal_declared","decision_origin":"actor_ai","declared_at_ms":declared_exchange_ms,"withdrawal":withdrawal})
''',
)

# Source Skill: preserve all independent components of compound declarations and
# keep the anti-jargon prose contract explicit.
replace_once(
    "plugins/shinobi-rpg/skill/shinobi-game-master/references/combat.md",
    "The registered lethal-until-resolution combat span supplies a temporary assertive, committed, persistent, mobile engagement override while retaining Wei's standing resource discipline and targeting policy; it must never overwrite his saved doctrine.\n\nA long standing combat span",
    "The registered lethal-until-resolution combat span supplies a temporary assertive, committed, persistent, mobile engagement override while retaining Wei's standing resource discipline and targeting policy; it must never overwrite his saved doctrine.\n\nPreserve independent components of a compound combat declaration. If Wei says **rally them and attack**, **cover the withdrawal while killing pursuers**, or otherwise combines leadership/team intent with personal violence, do not silently execute only the attack. Route each hard consequence through its registered authority in the same declared intent where supported; if one required consequence is unsupported, fail closed on that unsupported component rather than pretending it was never said. A rally is an attempt against real allied withdrawal pressure, not automatic obedience.\n\nA long standing combat span",
)

# Focused regressions.
test_path = ROOT / "tests/current/test_combat_rally_and_approach_budget.py"
test_path.write_text(r'''from __future__ import annotations

import copy
from pathlib import Path

from shinobi_runtime.commands.specs import COMMAND_SPECS
import shinobi_runtime.martial_world.exact_combat as exact

ROOT = Path(__file__).resolve().parents[2]


def _person(ref: str, faction: str, *, command: int = 20, will: int = 60, intelligence: int = 60):
    return {
        "person_id": ref,
        "faction_ref": faction,
        "body_mass_kg": 70,
        "attributes": {
            "strength": 60, "speed": 60, "dexterity": 60, "endurance": 60,
            "perception": 60, "intelligence": intelligence, "willpower": will,
        },
        "martial_skills": {"unarmed": 60, "sword": 0, "spear": 0, "bow": 0, "hidden_weapons": 0},
        "professional_skills": {"command": command},
        "qi": 0,
        "qi_control": 0,
        "fatigue_milli": 0,
        "health": {"status": "ready", "injuries": [], "blood_lost_ml": 0, "shock": 0, "consciousness": 100},
        "poison_burdens": {},
        "pending_poison_burdens": {},
    }


def _ledger():
    return {"schema": "jianghu-equipment-ledger-1.0", "policy_assignments": {}, "person_loadouts": {}}


def _withdrawal(reason: str = "side_collapse"):
    return {
        "reason": reason,
        "casualty_preservation": 80,
        "withdrawal_discipline": 80,
        "loss_percent": 36,
        "collapse_threshold_percent": 25,
        "condition": {"consciousness": 100, "shock": 0, "blood_lost_ml": 0, "functional_floor_milli": 1000},
    }


def test_combat_exchange_contract_exposes_contested_rally_component():
    exchange = COMMAND_SPECS["jianghu_combat_resolution"].variants["exchange"]
    assert exchange is not None
    assert "rally_allies" in exchange.optional_fields


def test_rally_uses_real_leadership_and_never_overrides_critical_condition():
    people = {
        "leader": _person("leader", "a", command=55, will=92, intelligence=100),
        "ally": _person("ally", "a", command=20, will=70, intelligence=60),
    }
    ordinary = exact._rally_withdrawal_attempt(
        leader_ref="leader", ally_ref="ally", people=people, withdrawal=_withdrawal("side_collapse")
    )
    assert ordinary["success"] is True
    assert ordinary["leadership_score"] >= ordinary["withdrawal_pressure_score"]

    critical = exact._rally_withdrawal_attempt(
        leader_ref="leader", ally_ref="ally", people=people, withdrawal=_withdrawal("critical_condition")
    )
    assert critical["success"] is False
    assert critical["reason"] == "critical_condition_not_overridable"


def test_rally_flag_arrests_noncritical_allied_withdrawal_but_default_does_not(monkeypatch):
    leader = _person("leader", "a", command=55, will=92, intelligence=100)
    ally = _person("ally", "a", command=20, will=70, intelligence=60)
    enemy = _person("enemy", "b")
    people = {row["person_id"]: row for row in (leader, ally, enemy)}
    combat = exact.initialize_combat(
        combat_ref="rally-test", side_a_refs=["leader", "ally"], side_b_refs=["enemy"],
        people=people, zone_ref="test", started_at="SE-0061-01-01T00:00:00",
        objective={"kind": "eliminate", "target_refs": ["enemy"]}, equipment_ledger=_ledger(),
    )

    original = exact._npc_withdrawal_decision
    def forced(*, combat, actor_ref, people, faction_doctrine):
        if actor_ref == "ally":
            return _withdrawal("side_collapse")
        return None
    monkeypatch.setattr(exact, "_npc_withdrawal_decision", forced)

    rallied = exact.resolve_exchange(
        combat=copy.deepcopy(combat), people=copy.deepcopy(people), equipment_ledger=_ledger(), doctrines={},
        player_ref="leader", player_action_kind="unarmed_strike", player_target_ref="enemy",
        player_weapon_ref="body_unarmed", player_hit_zone="chest", player_targeting_intent="lethal",
        player_rally_allies=True,
    )
    assert any(e.get("actor_ref") == "ally" and e.get("result") == "rally_held_position" for e in rallied["events"])
    assert not any(e.get("actor_ref") == "ally" and e.get("result") == "withdrawal_declared" for e in rallied["events"])

    unrallied = exact.resolve_exchange(
        combat=copy.deepcopy(combat), people=copy.deepcopy(people), equipment_ledger=_ledger(), doctrines={},
        player_ref="leader", player_action_kind="unarmed_strike", player_target_ref="enemy",
        player_weapon_ref="body_unarmed", player_hit_zone="chest", player_targeting_intent="lethal",
        player_rally_allies=False,
    )
    assert any(e.get("actor_ref") == "ally" and e.get("result") == "withdrawal_declared" for e in unrallied["events"])
    monkeypatch.setattr(exact, "_npc_withdrawal_decision", original)


def test_distant_melee_uses_short_approach_slice_and_never_releases_remote_strike():
    attacker = _person("attacker", "a")
    target = _person("target", "b")
    people = {"attacker": attacker, "target": target}
    combat = exact.initialize_combat(
        combat_ref="far-melee", side_a_refs=["attacker"], side_b_refs=["target"], people=people,
        zone_ref="test", started_at="SE-0061-01-01T00:00:00",
        objective={"kind": "eliminate", "target_refs": ["target"]}, equipment_ledger=_ledger(),
    )
    combat["positions"]["attacker"].update(x_mm=0, y_mm=0)
    combat["positions"]["target"].update(x_mm=70_000, y_mm=0)

    action = exact._schedule_action(
        combat=combat, actor_ref="attacker", target_ref="target", action_kind="unarmed_strike",
        weapon_ref="body_unarmed", poison_ref=None, hit_zone="chest", target_structure_ref=None,
        decision_origin="test", people=people, equipment_ledger=_ledger(),
    )
    params = action.profile.effect_parameters
    assert int(params["approach_time_ms"]) <= 2000
    assert params["approach_budget_limited"] is True
    assert int(params["approach_distance_mm"]) < 70_000 - 650

    result = exact.resolve_exchange(
        combat=combat, people=people, equipment_ledger=_ledger(), doctrines={},
        player_ref="attacker", player_action_kind="unarmed_strike", player_target_ref="target",
        player_weapon_ref="body_unarmed", player_hit_zone="chest", player_targeting_intent="lethal",
    )
    event = next(e for e in result["events"] if e.get("actor_ref") == "attacker")
    assert event["result"] == "melee_approach_in_progress"
    assert result["combat_after"]["positions"]["attacker"]["x_mm"] > 0
    assert result["combat_after"]["elapsed_ms"] <= 5000
    assert result["people_after"]["target"]["health"]["injuries"] == []


def test_near_melee_is_not_misclassified_as_approach_only():
    attacker = _person("attacker", "a")
    target = _person("target", "b")
    people = {"attacker": attacker, "target": target}
    combat = exact.initialize_combat(
        combat_ref="near-melee", side_a_refs=["attacker"], side_b_refs=["target"], people=people,
        zone_ref="test", started_at="SE-0061-01-01T00:00:00",
        objective={"kind": "eliminate", "target_refs": ["target"]}, equipment_ledger=_ledger(),
    )
    combat["positions"]["attacker"].update(x_mm=0, y_mm=0)
    combat["positions"]["target"].update(x_mm=600, y_mm=0)
    result = exact.resolve_exchange(
        combat=combat, people=people, equipment_ledger=_ledger(), doctrines={},
        player_ref="attacker", player_action_kind="unarmed_strike", player_target_ref="target",
        player_weapon_ref="body_unarmed", player_hit_zone="chest", player_targeting_intent="lethal",
    )
    event = next(e for e in result["events"] if e.get("actor_ref") == "attacker")
    assert event["result"] != "melee_approach_in_progress"


def test_skill_preserves_rally_plus_attack_and_bans_solver_jargon_in_ic_prose():
    text = (ROOT / "plugins/shinobi-rpg/skill/shinobi-game-master/references/combat.md").read_text(encoding="utf-8")
    assert "Preserve independent components of a compound combat declaration" in text
    assert "rally them and attack" in text
    assert "Player-facing combat prose must not name resolver primitives" in text
    assert "strong novel or film" in text
''', encoding="utf-8")
