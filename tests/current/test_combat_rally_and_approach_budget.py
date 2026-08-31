from __future__ import annotations

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
