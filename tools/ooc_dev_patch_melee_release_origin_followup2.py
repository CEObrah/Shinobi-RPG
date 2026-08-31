from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "tests/current/test_combat_contact_pursuit_repair.py"
text = path.read_text(encoding="utf-8")
old = r'''def test_melee_strike_lane_launches_from_post_approach_position():
    attacker, defender = _people_pair()
    people = {attacker["person_id"]: attacker, defender["person_id"]: defender}
    ledger = _jian_ledger(attacker["person_id"])
    combat = exact.initialize_combat(
        combat_ref="post-approach-release", side_a_refs=[attacker["person_id"]], side_b_refs=[defender["person_id"]],
        people=people, zone_ref="test", started_at="SE-0061-01-01T00:00:00",
        objective={"kind": "eliminate", "target_refs": [defender["person_id"]]}, equipment_ledger=ledger,
    )
    combat["positions"][attacker["person_id"]].update(x_mm=0, y_mm=0, elevation_mm=0)
    combat["positions"][defender["person_id"]].update(x_mm=1750, y_mm=0, elevation_mm=0)
    action = exact._schedule_action(
        combat=combat, actor_ref=attacker["person_id"], target_ref=defender["person_id"],
        action_kind="thrust", weapon_ref="weapon_jian", poison_ref=None, hit_zone="chest",
        target_structure_ref=None, decision_origin="test", people=people, equipment_ledger=ledger,
    )
    event = exact._resolve_scheduled_action(
        combat=combat, action=action, people=people, equipment_ledger=ledger,
    )
    assert combat["positions"][attacker["person_id"]]["x_mm"] == 600
    trajectory = event["trace"]["trajectory"]
    assert trajectory["launch_x_mm"] == 600
    assert trajectory["launch_y_mm"] == 0
    assert trajectory["aim_x_mm"] == 1750
    assert event["trace"]["contacts"]
    assert event["trace"]["contacts"][0]["participant_ref"] == defender["person_id"]
'''
new = r'''def test_melee_strike_lane_launches_from_post_approach_position(monkeypatch):
    attacker, defender = _people_pair()
    people = {attacker["person_id"]: attacker, defender["person_id"]: defender}
    ledger = _jian_ledger(attacker["person_id"])
    combat = exact.initialize_combat(
        combat_ref="post-approach-release", side_a_refs=[attacker["person_id"]], side_b_refs=[defender["person_id"]],
        people=people, zone_ref="test", started_at="SE-0061-01-01T00:00:00",
        objective={"kind": "eliminate", "target_refs": [defender["person_id"]]}, equipment_ledger=ledger,
    )
    combat["positions"][attacker["person_id"]].update(x_mm=0, y_mm=0, elevation_mm=0)
    combat["positions"][defender["person_id"]].update(x_mm=1750, y_mm=0, elevation_mm=0)
    action = exact._schedule_action(
        combat=combat, actor_ref=attacker["person_id"], target_ref=defender["person_id"],
        action_kind="thrust", weapon_ref="weapon_jian", poison_ref=None, hit_zone="chest",
        target_structure_ref=None, decision_origin="test", people=people, equipment_ledger=ledger,
    )
    original_trace = exact.trace_attack_geometry
    captured = []
    def spy_trace(*args, **kwargs):
        captured.append(dict(kwargs))
        return original_trace(*args, **kwargs)
    monkeypatch.setattr(exact, "trace_attack_geometry", spy_trace)
    event = exact._resolve_scheduled_action(
        combat=combat, action=action, people=people, equipment_ledger=ledger,
    )
    assert combat["positions"][attacker["person_id"]]["x_mm"] == 600
    assert captured
    trajectory = captured[0]["trajectory"]
    assert trajectory["launch_x_mm"] == 600
    assert trajectory["launch_y_mm"] == 0
    assert trajectory["aim_x_mm"] == 1750
    assert event["actual_ref"] == defender["person_id"]
'''
if text.count(old) != 1:
    raise SystemExit("post-approach scheduled-action regression match missing or ambiguous")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("post-approach regression now spies exact preliminary melee geometry")
