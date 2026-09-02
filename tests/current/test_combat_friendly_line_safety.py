import copy
import json
from pathlib import Path

import pytest

import shinobi_runtime.martial_world.exact_combat as exact

ROOT = Path(__file__).resolve().parents[2]


def _clean(row):
    person = copy.deepcopy(row)
    person["fatigue_milli"] = 0
    person["health"] = {"status": "ready", "injuries": [], "blood_lost_ml": 0, "shock": 0, "consciousness": 100}
    person["poison_burdens"] = {}
    person["pending_poison_burdens"] = {}
    return person


def _fixture():
    roster = json.loads((ROOT / "state/martial-world/people/house_tang.json").read_text())["people"]
    actor, friend, target = map(_clean, (roster[0], roster[1], roster[3]))
    people = {row["person_id"]: row for row in (actor, friend, target)}
    ledger = json.loads((ROOT / "state/martial-world/equipment-ledger.json").read_text())
    ledger.setdefault("person_loadouts", {})[actor["person_id"]] = {"items": {"weapon_jian": 1}}
    combat = exact.initialize_combat(
        combat_ref="friendly-line-safety",
        side_a_refs=[actor["person_id"], friend["person_id"]],
        side_b_refs=[target["person_id"]],
        people=people,
        zone_ref="test",
        started_at="x",
        objective={"kind": "eliminate", "target_refs": [target["person_id"]]},
        equipment_ledger=ledger,
    )
    combat["positions"][actor["person_id"]].update(x_mm=0, y_mm=0)
    combat["positions"][friend["person_id"]].update(x_mm=550, y_mm=0)
    combat["positions"][target["person_id"]].update(x_mm=1050, y_mm=0)
    return actor, friend, target, people, ledger, combat


def test_autonomous_schedule_rejects_preexisting_friendly_first_contact():
    actor, friend, target, people, ledger, combat = _fixture()
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


def test_player_schedule_is_not_rewritten_by_autonomous_safety():
    actor, friend, target, people, ledger, combat = _fixture()
    action = exact._schedule_action(
        combat=combat,
        actor_ref=actor["person_id"],
        target_ref=target["person_id"],
        action_kind="thrust",
        weapon_ref="weapon_jian",
        poison_ref=None,
        hit_zone="chest",
        target_structure_ref=None,
        decision_origin="player",
        people=people,
        equipment_ledger=ledger,
    )
    assert action.target_ref == target["person_id"]
    trace = exact.trace_attack_geometry(
        combat["positions"],
        actor_ref=actor["person_id"],
        aim_ref=target["person_id"],
        body_refs=exact._present_body_refs(combat),
        geometry=action.profile.effect_parameters["geometry"],
        obstacles=[],
        target_limit=1,
        maximum_range_m=action.profile.effect_parameters["physical_reach_m"],
        channel="melee",
        trajectory={
            "launch_x_mm": 0,
            "launch_y_mm": 0,
            "launch_elevation_mm": 0,
            "aim_x_mm": 1050,
            "aim_y_mm": 0,
            "aim_elevation_mm": 0,
        },
    )
    assert trace["contacts"][0]["participant_ref"] == friend["person_id"]
