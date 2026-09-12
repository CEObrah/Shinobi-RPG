from datetime import datetime

from shinobi_runtime.commands.jianghu_development import JianghuDevelopmentCommandsMixin
from shinobi_runtime.commands.jianghu_institutional import JianghuInstitutionalCommandsMixin
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.martial_world.field_development import (
    apply_combat_events,
    apply_field_activity,
    apply_single_combat_action,
)
from shinobi_runtime.martial_world import time_progression
from shinobi_runtime.martial_world.time_progression import augment_frontier_with_progression


def _person(pid: str, *, sword: int = 50, scouting: int = 30, command: int = 20):
    return {
        "person_id": pid,
        "birth_year": 30,
        "membership_grade": "full",
        "attributes": {
            "strength": 60,
            "speed": 60,
            "dexterity": 60,
            "endurance": 60,
            "perception": 60,
            "intelligence": 60,
            "willpower": 60,
        },
        "martial_skills": {
            "sword": sword,
            "spear": 0,
            "bow": 0,
            "hidden_weapons": 0,
            "unarmed": 30,
            "stealth_scouting": scouting,
            "command": command,
        },
        "professional_skills": {
            "medicine": 10,
            "administration": 10,
            "commerce": 10,
            "crafting": 10,
            "instruction": 10,
        },
        "aptitudes": {
            "physical": 120,
            "martial": 120,
            "qi": 120,
            "cognitive": 120,
            "leadership": 120,
        },
        "qi": 30,
        "qi_control": 30,
        "current_qi": 30,
        "health": {
            "status": "ready",
            "injuries": [],
            "toxicity_milli": 0,
            "blood_lost_ml": 0,
            "shock": 0,
            "consciousness": 100,
        },
        "fatigue_milli": 0,
    }


def test_planner_mro_keeps_development_overlay_active():
    # New orthogonal mixins may be inserted ahead of development. The invariant
    # is that the development mixin remains active in the planner MRO rather than
    # an absolute slot number that makes unrelated scene features look broken.
    assert JianghuInstitutionalCommandsMixin in RepositoryCommandPlanner.__mro__
    assert JianghuDevelopmentCommandsMixin in RepositoryCommandPlanner.__mro__
    assert RepositoryCommandPlanner.__mro__.index(JianghuDevelopmentCommandsMixin) < RepositoryCommandPlanner.__mro__.index(object)


def test_long_field_activity_builds_real_bounded_development():
    before = _person("traveler")
    after, summary = apply_field_activity(
        before,
        duration_hours_milli=240_000,
        activity_kind="escort_travel",
        leader=False,
        pressure_milli=800,
    )
    assert summary["duration_hours_milli"] == 240_000
    assert after["training_state"]["evidence_milli"]["stealth_scouting"] > 0
    assert after["training_state"]["evidence_milli"]["attribute:endurance"] > 0
    assert after["training_state"]["evidence_milli"]["attribute:perception"] > 0
    assert after["attributes"]["endurance"] >= before["attributes"]["endurance"]
    assert after["martial_skills"]["stealth_scouting"] >= before["martial_skills"]["stealth_scouting"]


def test_leading_field_party_adds_command_development_without_extra_time():
    follower, _ = apply_field_activity(
        _person("follower"), duration_hours_milli=120_000,
        activity_kind="escort_travel", leader=False, pressure_milli=800,
    )
    leader, _ = apply_field_activity(
        _person("leader"), duration_hours_milli=120_000,
        activity_kind="escort_travel", leader=True, pressure_milli=800,
    )
    follower_evidence = follower.get("training_state", {}).get("evidence_milli", {})
    leader_evidence = leader.get("training_state", {}).get("evidence_milli", {})
    assert follower_evidence.get("command", 0) == 0
    assert leader_evidence.get("command", 0) > 0


def test_combat_against_far_weaker_opponent_cannot_be_farmed():
    elite = _person("elite", sword=180)
    weak = _person("weak", sword=5, scouting=0, command=0)
    weak["attributes"] = {key: 10 for key in weak["attributes"]}
    weak["martial_skills"]["unarmed"] = 0
    weak["qi_control"] = 0
    weak["qi"] = 0
    weak["current_qi"] = 0
    people_after = {"elite": elite, "weak": weak}
    events = [{
        "actor_ref": "elite",
        "intended_ref": "weak",
        "actual_ref": "weak",
        "action_kind": "sword_thrust",
        "weapon_ref": "jian.test",
        "declared_at_ms": 100,
        "result": "contact",
    }]
    developed, summary = apply_combat_events(
        people_after, people_before=people_after, events=events,
    )
    assert summary["actions_counted"] == 0
    assert developed["elite"].get("training_state", {}).get("evidence_milli", {}).get("sword", 0) == 0
    assert developed["elite"]["martial_skills"]["sword"] == 180


def test_credible_combat_builds_only_the_skill_actually_used():
    actor = _person("actor", sword=70)
    after, gain = apply_single_combat_action(actor, domain="sword", pressure_milli=1100)
    assert gain["evidence_added_milli"] > 0
    evidence = after["training_state"]["evidence_milli"]
    assert evidence.get("sword", 0) > 0
    assert evidence.get("unarmed", 0) == 0
    assert after["martial_skills"]["sword"] >= actor["martial_skills"]["sword"]


def test_invalid_or_precommit_combat_action_adds_no_development():
    actor = _person("actor", sword=70)
    opponent = _person("opponent", sword=80)
    people = {"actor": actor, "opponent": opponent}
    events = [{
        "actor_ref": "actor",
        "intended_ref": "opponent",
        "actual_ref": "opponent",
        "action_kind": "sword_thrust",
        "weapon_ref": "jian.test",
        "declared_at_ms": 100,
        "result": "action_interrupted_before_commitment",
    }]
    developed, summary = apply_combat_events(people, people_before=people, events=events)
    assert summary["actions_counted"] == 0
    assert developed["actor"].get("training_state", {}).get("evidence_milli", {}) == {}


def test_new_public_contract_offer_interrupts_event_seeking_wait(monkeypatch):
    contracts_path = "state/martial-world/contracts/index.json"
    route_path = "state/martial-world/route-operations.json"
    meta_path = "state/meta.json"
    before = {
        contracts_path: {"schema": "jianghu-contract-index-1.0", "active": {}, "archive": {}},
        route_path: {"schema": "jianghu-route-operations-1.0", "movements": {}},
        meta_path: {"schema": "meta", "player_id": "pc.test"},
    }

    def read_json(path):
        if path not in before:
            raise FileNotFoundError(path)
        return before[path]

    monkeypatch.setattr(
        time_progression,
        "roster_person",
        lambda _view, _ref: (
            "state/martial-world/people/house_tang.json",
            {},
            0,
            {"person_id": "pc.test", "faction_ref": "house_tang"},
        ),
    )
    offered = {
        "contract_ref": "contract.test.public",
        "contract_type": "escort",
        "status": "offered",
        "issuer_ref": "merchant.test",
        "beneficiary_ref": None,
        "reward_cash": 900,
        "expires_at": "0061-10-01T00:00:00",
        "objective": {"kind": "escort", "minimum_escort_count": 2},
    }
    frontier = {
        "writes": {
            contracts_path: {
                "schema": "jianghu-contract-index-1.0",
                "active": {"contract.test.public": offered},
                "archive": {},
            }
        },
        "handoffs": [],
        "reviews": [],
    }
    out = augment_frontier_with_progression(
        read_json=read_json,
        frontier=frontier,
        at=datetime.fromisoformat("0061-09-01T00:00:00"),
    )
    handoff = next(row for row in out["handoffs"] if row.get("contract_ref") == "contract.test.public")
    assert handoff["kind"] == "funded_contract_offer"
    assert handoff["handoff"]["class"] == "soft_player_facing"
    assert handoff["handoff"]["interrupts_event_seeking"] is True
    assert handoff["handoff"]["requires_player_decision"] is False


def test_handoff_salience_registry_is_canonical_data_not_python_shadow_table():
    import json
    from pathlib import Path
    from shinobi_runtime.martial_world.handoffs import classify_handoff

    root = Path(__file__).resolve().parents[2]
    policy = json.loads((root / "game/data/martial-world/handoff.json").read_text(encoding="utf-8"))
    source = (root / "runtime/shinobi_runtime/martial_world/handoffs.py").read_text(encoding="utf-8")

    assert "event_kinds" in policy
    assert "hostile_contact" in policy["event_kinds"]["hard_decision"]
    assert "funded_contract_offer" in policy["event_kinds"]["soft_player_facing"]
    assert "HARD={" not in source and "SOFT={" not in source
    assert 'handoff.json' in source
    assert classify_handoff({"kind": "hostile_contact"})["class"] == "hard_decision"
    assert classify_handoff({"kind": "funded_contract_offer"})["class"] == "soft_player_facing"
    assert classify_handoff({"kind": "ordinary_internal_review"})["class"] == "internal"
    assert classify_handoff({"kind": "unregistered_notice", "delivered_to_player": True})["class"] == "soft_player_facing"
