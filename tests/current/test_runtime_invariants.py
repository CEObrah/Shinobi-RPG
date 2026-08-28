import copy
import json
from pathlib import Path

import pytest

from shinobi_runtime.martial_world.exact_combat import initialize_combat, resolve_exchange
from shinobi_runtime.martial_world.training import (
    institutional_instruction_assignment,
    institutional_teaching_duty_milli,
    instructor_capacity,
)

ROOT = Path(__file__).resolve().parents[2]


def load(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def _training_person(pid: str, *, sword: int, instruction: int) -> dict:
    return {
        "person_id": pid,
        "membership_grade": "full",
        "health": {"status": "healthy", "consciousness": 100},
        "martial_skills": {"sword": sword},
        "professional_skills": {"instruction": instruction},
    }


def test_institutional_instruction_is_capacity_bounded_and_teaching_consumes_time():
    teacher = _training_person("teacher", sword=220, instruction=100)
    students = [_training_person(f"student.{index:02d}", sword=20, instruction=0) for index in range(30)]
    people = [teacher, *students]
    capacity = instructor_capacity(instruction_skill=100, facility_level=1, group_drill=True)
    assigned = []
    for student in students:
        report = institutional_instruction_assignment(
            people,
            student_ref=student["person_id"],
            domain="sword",
            facility_level=1,
            epoch_key="0061-spring",
        )
        if report["instructor_ref"] == "teacher":
            assigned.append(student["person_id"])
    assert len(assigned) == capacity
    assert len(assigned) < len(students)
    assert assigned == [
        student["person_id"]
        for student in students
        if institutional_instruction_assignment(
            people,
            student_ref=student["person_id"],
            domain="sword",
            facility_level=1,
            epoch_key="0061-spring",
        )["instructor_ref"] == "teacher"
    ]
    duty = institutional_teaching_duty_milli(
        "teacher",
        people,
        domains=[("sword", 1)],
        epoch_key="0061-spring",
    )
    assert duty == 300


def test_removed_shield_discipline_cannot_survive_in_contract_template_keys():
    roster_template = load("runtime/contracts/templates/jianghu-person-lite-roster-1.0.template.json")
    faction_template = load("runtime/contracts/templates/jianghu-faction-state-1.0.template.json")
    roster_paths = set(roster_template.get("type_contracts", {}))
    faction_paths = set(faction_template.get("type_contracts", {}))
    assert not any(path.endswith("/shield") for path in roster_paths | faction_paths)
    assert "/people/*/martial_skills/hidden_weapons" in roster_paths
    assert "/training/hidden_weapons" in faction_paths


def test_combat_weapon_readiness_is_explicit_and_switching_consumes_time(monkeypatch):
    roster = load("state/martial-world/people/house_tang.json")["people"]
    attacker = copy.deepcopy(next(person for person in roster if person["person_id"] == "pc_wei_tang"))
    defender = copy.deepcopy(next(person for person in roster if person["person_id"] == "char.kai"))
    people = {attacker["person_id"]: attacker, defender["person_id"]: defender}
    ledger = load("state/martial-world/equipment-ledger.json")

    import shinobi_runtime.martial_world.exact_combat as exact

    monkeypatch.setattr(exact, "trace_attack_geometry", lambda *args, **kwargs: {"contacts": [], "blocked_by": None})
    combat = initialize_combat(
        combat_ref="readiness",
        side_a_refs=[attacker["person_id"]],
        side_b_refs=[defender["person_id"]],
        people=people,
        zone_ref="site.house_tang",
        started_at="0061-01-01T00:00:00",
        objective={"kind": "eliminate", "target_refs": [defender["person_id"]]},
    )
    assert combat["combatants"][attacker["person_id"]]["ready_weapon_ref"] is None

    first = resolve_exchange(
        combat=combat,
        people=people,
        equipment_ledger=ledger,
        doctrines={},
        player_ref=attacker["person_id"],
        player_action_kind="thrust",
        player_target_ref=defender["person_id"],
        player_weapon_ref="weapon_jian",
        player_hit_zone="chest",
        player_targeting_intent="disable",
    )
    first_event = next(event for event in first["events"] if event["actor_ref"] == attacker["person_id"])
    assert first_event["ready_delay_ms"] > 0
    assert first["combat_after"]["combatants"][attacker["person_id"]]["ready_weapon_ref"] == "weapon_jian"

    second = resolve_exchange(
        combat=first["combat_after"],
        people=first["people_after"],
        equipment_ledger=first["equipment_ledger_after"],
        doctrines={},
        player_ref=attacker["person_id"],
        player_action_kind="thrust",
        player_target_ref=defender["person_id"],
        player_weapon_ref="weapon_jian",
        player_hit_zone="chest",
        player_targeting_intent="disable",
    )
    second_event = next(event for event in second["events"] if event["actor_ref"] == attacker["person_id"])
    assert second_event["ready_delay_ms"] == 0


def test_mutual_combat_can_begin_ready_but_surprised_side_cannot():
    roster = load("state/martial-world/people/house_tang.json")["people"]
    attacker = copy.deepcopy(next(person for person in roster if person["person_id"] == "pc_wei_tang"))
    defender = copy.deepcopy(next(person for person in roster if person["person_id"] == "char.kai"))
    people = {attacker["person_id"]: attacker, defender["person_id"]: defender}
    ledger = load("state/martial-world/equipment-ledger.json")

    mutual = initialize_combat(
        combat_ref="mutual-ready",
        side_a_refs=[attacker["person_id"]],
        side_b_refs=[defender["person_id"]],
        people=people,
        zone_ref="site.house_tang",
        started_at="0061-01-01T00:00:00",
        objective={"kind": "eliminate", "target_refs": [defender["person_id"]]},
        equipment_ledger=ledger,
    )
    assert mutual["combatants"][attacker["person_id"]]["ready_weapon_ref"] is not None
    assert mutual["combatants"][defender["person_id"]]["ready_weapon_ref"] is not None

    ambush = initialize_combat(
        combat_ref="ambush-ready",
        side_a_refs=[attacker["person_id"]],
        side_b_refs=[defender["person_id"]],
        people=people,
        zone_ref="site.house_tang",
        started_at="0061-01-01T00:00:00",
        objective={"kind": "eliminate", "target_refs": [defender["person_id"]]},
        awareness_mode="side_a_ambush",
        awareness_evidence={"derived": True},
        equipment_ledger=ledger,
    )
    assert ambush["combatants"][attacker["person_id"]]["ready_weapon_ref"] is not None
    assert ambush["combatants"][defender["person_id"]]["ready_weapon_ref"] is None


def test_unowned_weapon_is_rejected_and_thrown_weapon_leaves_the_hand(monkeypatch):
    roster = load("state/martial-world/people/house_tang.json")["people"]
    attacker = copy.deepcopy(next(person for person in roster if person["person_id"] == "pc_wei_tang"))
    defender = copy.deepcopy(next(person for person in roster if person["person_id"] == "char.kai"))
    people = {attacker["person_id"]: attacker, defender["person_id"]: defender}

    import shinobi_runtime.martial_world.exact_combat as exact

    monkeypatch.setattr(exact, "trace_attack_geometry", lambda *args, **kwargs: {"contacts": [], "blocked_by": None})
    sparse_ledger = {
        "schema": "jianghu-equipment-ledger-1.0",
        "policy_assignments": {},
        "person_loadouts": {
            attacker["person_id"]: {
                "items": {"weapon_throwing_knife": 1},
                "condition_milli": {"weapon_throwing_knife": 1000},
            }
        },
    }
    combat = initialize_combat(
        combat_ref="availability",
        side_a_refs=[attacker["person_id"]],
        side_b_refs=[defender["person_id"]],
        people=people,
        zone_ref="site.house_tang",
        started_at="0061-01-01T00:00:00",
        objective={"kind": "eliminate", "target_refs": [defender["person_id"]]},
    )
    rejected = resolve_exchange(
        combat=combat,
        people=people,
        equipment_ledger=sparse_ledger,
        doctrines={},
        player_ref=attacker["person_id"],
        player_action_kind="thrust",
        player_target_ref=defender["person_id"],
        player_weapon_ref="weapon_jian",
        player_hit_zone="chest",
        player_targeting_intent="disable",
    )
    rejected_event = next(event for event in rejected["events"] if event["actor_ref"] == attacker["person_id"])
    assert rejected_event["result"] == "weapon_not_owned"

    thrown = resolve_exchange(
        combat=combat,
        people=people,
        equipment_ledger=sparse_ledger,
        doctrines={},
        player_ref=attacker["person_id"],
        player_action_kind="hidden_weapon_throw",
        player_target_ref=defender["person_id"],
        player_weapon_ref="weapon_throwing_knife",
        player_hit_zone="chest",
        player_targeting_intent="disable",
    )
    thrown_event = next(event for event in thrown["events"] if event["actor_ref"] == attacker["person_id"])
    assert thrown_event["result"] == "miss_no_spatial_intersection"
    assert thrown["combat_after"]["combatants"][attacker["person_id"]]["ready_weapon_ref"] is None
