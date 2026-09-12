from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import shinobi_runtime.martial_world.exact_combat as exact
from shinobi_runtime.martial_world.health import lethal_state

ROOT = Path(__file__).resolve().parents[2]


def _state(wound):
    return lethal_state(
        wounds=[wound],
        blood_loss_fraction_milli=0,
        shock=0,
        consciousness=100,
    )


def test_neck_anatomy_matrix_distinguishes_generic_contact_from_destroyed_exact_structures():
    for severity in (0, 20, 40):
        for organ_trauma in (0, 10, 20):
            assert _state(
                {
                    "zone": "neck",
                    "severity": severity,
                    "organ_trauma": organ_trauma,
                    "structure_ref": None,
                    "structure_damage": 0,
                }
            ) == "alive"

    for structure in ("carotid_artery", "jugular_vein"):
        assert _state(
            {
                "zone": "neck",
                "severity": 60,
                "organ_trauma": 50,
                "structure_ref": structure,
                "structure_damage": 120,
            }
        ) == "dead"

    for structure in ("trachea", "throat"):
        assert _state(
            {
                "zone": "neck",
                "severity": 70,
                "organ_trauma": 55,
                "structure_ref": structure,
                "structure_damage": 130,
                "stabilized": False,
            }
        ) == "dying"

    assert _state(
        {
            "zone": "neck",
            "severity": 70,
            "organ_trauma": 55,
            "structure_ref": "spinal_cord",
            "structure_damage": 130,
        }
    ) == "dead"

    assert _state(
        {
            "zone": "neck",
            "severity": 70,
            "organ_trauma": 55,
            "structure_ref": "cervical_spine",
            "structure_damage": 170,
            "nerve_damage": 90,
        }
    ) == "dying"


def _clean(row):
    person = copy.deepcopy(row)
    person["fatigue_milli"] = 0
    person["health"] = {
        "status": "ready",
        "injuries": [],
        "blood_lost_ml": 0,
        "shock": 0,
        "consciousness": 100,
    }
    person["poison_burdens"] = {}
    person["pending_poison_burdens"] = {}
    return person


def _fixture(friend_x: int):
    roster = json.loads(
        (ROOT / "state/martial-world/people/house_tang.json").read_text(encoding="utf-8")
    )["people"]
    actor, friend, target = map(_clean, (roster[0], roster[1], roster[3]))
    people = {row["person_id"]: row for row in (actor, friend, target)}
    ledger = json.loads(
        (ROOT / "state/martial-world/equipment-ledger.json").read_text(encoding="utf-8")
    )
    ledger.setdefault("person_loadouts", {})[actor["person_id"]] = {
        "items": {"weapon_jian": 1}
    }
    combat = exact.initialize_combat(
        combat_ref=f"friendly-lane-{friend_x}",
        side_a_refs=[actor["person_id"], friend["person_id"]],
        side_b_refs=[target["person_id"]],
        people=people,
        zone_ref="test",
        started_at="x",
        objective={"kind": "eliminate", "target_refs": [target["person_id"]]},
        equipment_ledger=ledger,
    )
    combat["positions"][actor["person_id"]].update(x_mm=0, y_mm=0)
    combat["positions"][friend["person_id"]].update(x_mm=friend_x, y_mm=0)
    combat["positions"][target["person_id"]].update(x_mm=1050, y_mm=0)
    return actor, target, people, ledger, combat


@pytest.mark.parametrize("friend_x", [200, 350, 500, 650, 800])
def test_autonomous_melee_never_schedules_through_a_same_side_body(friend_x: int):
    actor, target, people, ledger, combat = _fixture(friend_x)
    with pytest.raises(ValueError, match="friendly_attack_lane_blocked"):
        exact._schedule_action(
            combat=combat,
            actor_ref=actor["person_id"],
            target_ref=target["person_id"],
            action_kind="thrust",
            weapon_ref="weapon_jian",
            poison_ref=None,
            hit_zone="chest",
            target_structure_ref=None,
            decision_origin="team_ai",
            people=people,
            equipment_ledger=ledger,
        )



def test_autonomous_projectile_is_withheld_when_ally_crosses_lane_after_declaration():
    actor, target, people, ledger, combat = _fixture(2500)
    friend_ref = next(
        ref for ref in combat["sides"]["side_a"] if ref != actor["person_id"]
    )
    ledger.setdefault("person_loadouts", {})[actor["person_id"]] = {
        "items": {"weapon_needle": 12}
    }
    combat["positions"][friend_ref].update(x_mm=2500, y_mm=2000)
    combat["positions"][target["person_id"]].update(x_mm=5000, y_mm=0)
    action = exact._schedule_action(
        combat=combat,
        actor_ref=actor["person_id"],
        target_ref=target["person_id"],
        action_kind="hidden_weapon_throw",
        weapon_ref="weapon_needle",
        poison_ref=None,
        hit_zone="chest",
        target_structure_ref=None,
        decision_origin="team_ai",
        people=people,
        equipment_ledger=ledger,
    )
    combat["positions"][friend_ref].update(x_mm=2500, y_mm=0)
    event = exact._resolve_scheduled_action(
        combat=combat,
        action=action,
        people=people,
        equipment_ledger=ledger,
    )
    assert event["result"] == "autonomous_attack_withheld_friendly_lane"
    assert event["friendly_blocker_ref"] == friend_ref
    assert event["safety_phase"] == "pre_resolution_dynamic_lane_recheck"
