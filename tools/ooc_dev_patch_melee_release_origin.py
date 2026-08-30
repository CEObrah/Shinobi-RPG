from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "runtime/shinobi_runtime/martial_world/exact_combat.py"
text = PATH.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one source match, found {count}: {old[:140]!r}")
    text = text.replace(old, new, 1)


# A melee action's authored declaration trajectory is useful to decide the
# committed approach, but once that approach physically moves the attacker the
# actual strike must launch from the post-approach body position. Freezing the
# release lane to the pre-charge coordinates makes an attacker stand at exact
# weapon reach while the blade trace is still several metres behind them.
replace_once(
    '''    if profile.delivery not in {"projectile","ranged","thrown"}:\n        params=dict(profile.effect_parameters)\n        params["committed_melee_trajectory"]=copy.deepcopy(dict(action.trajectory))\n        params["intended_target_ref"]=target_ref\n        profile=ActionProfile(**{**profile.__dict__,"effect_parameters":params})\n        moved,approach=close_attacker_into_reach(\n''',
    '''    if profile.delivery not in {"projectile","ranged","thrown"}:\n        params=dict(profile.effect_parameters)\n        params["intended_target_ref"]=target_ref\n        profile=ActionProfile(**{**profile.__dict__,"effect_parameters":params})\n        moved,approach=close_attacker_into_reach(\n''',
)

replace_once(
    '''        melee_distance_mm=planar_distance_mm(positions[actor_ref],positions[target_ref])\n        melee_reach_mm=physical_reach_mm(profile)\n        if melee_reach_mm>0 and melee_distance_mm>melee_reach_mm:\n''',
    '''        melee_distance_mm=planar_distance_mm(positions[actor_ref],positions[target_ref])\n        melee_reach_mm=physical_reach_mm(profile)\n        if melee_reach_mm>0 and melee_distance_mm>melee_reach_mm:\n''',
)

# Insert the committed release trajectory only after a successful close. It is
# the strike lane at release: current attacker position -> current target
# position before the defender's reaction. Low tracking may preserve that lane;
# high tracking may redirect during the remaining startup in contact_after_defense.
marker = '''            return {\n                **event_base,"result":result_kind,"approach":approach,\n                "distance_mm":melee_distance_mm,"reach_mm":melee_reach_mm,\n                "fatigue":chase_exertion,"qi":qi_preview,\n            }\n    distance_m=planar_distance_mm(positions[actor_ref],positions[target_ref])/1000.0; visibility=1000-cover_milli_between(positions,actor_ref=actor_ref,target_ref=target_ref,obstacles=combat.get("obstacles",[])); bow_profile=None; trajectory=copy.deepcopy(dict(action.trajectory))\n'''
replacement = '''            return {\n                **event_base,"result":result_kind,"approach":approach,\n                "distance_mm":melee_distance_mm,"reach_mm":melee_reach_mm,\n                "fatigue":chase_exertion,"qi":qi_preview,\n            }\n        actor_release=positions[actor_ref]\n        target_release=positions[target_ref]\n        params=dict(profile.effect_parameters)\n        params["committed_melee_trajectory"]={\n            "launch_x_mm":int(actor_release["x_mm"]),\n            "launch_y_mm":int(actor_release["y_mm"]),\n            "launch_elevation_mm":int(actor_release.get("elevation_mm",0)),\n            "aim_x_mm":int(target_release["x_mm"]),\n            "aim_y_mm":int(target_release["y_mm"]),\n            "aim_elevation_mm":int(target_release.get("elevation_mm",0)),\n        }\n        params["intended_target_ref"]=target_ref\n        profile=ActionProfile(**{**profile.__dict__,"effect_parameters":params})\n    distance_m=planar_distance_mm(positions[actor_ref],positions[target_ref])/1000.0; visibility=1000-cover_milli_between(positions,actor_ref=actor_ref,target_ref=target_ref,obstacles=combat.get("obstacles",[])); bow_profile=None; trajectory=copy.deepcopy(dict(action.trajectory))\n'''
replace_once(marker, replacement)

replace_once(
    '''    geometry=profile.effect_parameters.get("geometry"); channel="projectile" if profile.delivery in {"projectile","ranged","thrown"} else "melee"; trace=trace_attack_geometry(positions,actor_ref=actor_ref,aim_ref=target_ref,body_refs=body_refs,geometry=geometry,obstacles=combat.get("obstacles",[]),target_limit=1,maximum_range_m=(profile.effect_parameters.get("maximum_range_m") if channel=="projectile" else profile.effect_parameters.get("physical_reach_m")),channel=channel,trajectory=trajectory if channel=="projectile" else action.trajectory); contacts=trace.get("contacts",[]) if isinstance(trace,Mapping) else []; actual_ref=contacts[0].get("participant_ref") if contacts and isinstance(contacts[0],Mapping) else None\n''',
    '''    geometry=profile.effect_parameters.get("geometry"); channel="projectile" if profile.delivery in {"projectile","ranged","thrown"} else "melee"; melee_trajectory=profile.effect_parameters.get("committed_melee_trajectory") if isinstance(profile.effect_parameters.get("committed_melee_trajectory"),Mapping) else None; trace=trace_attack_geometry(positions,actor_ref=actor_ref,aim_ref=target_ref,body_refs=body_refs,geometry=geometry,obstacles=combat.get("obstacles",[]),target_limit=1,maximum_range_m=(profile.effect_parameters.get("maximum_range_m") if channel=="projectile" else profile.effect_parameters.get("physical_reach_m")),channel=channel,trajectory=trajectory if channel=="projectile" else melee_trajectory); contacts=trace.get("contacts",[]) if isinstance(trace,Mapping) else []; actual_ref=contacts[0].get("participant_ref") if contacts and isinstance(contacts[0],Mapping) else None\n''',
)

# Team-plan provenance is not player-retinue provenance. Only actors actually in
# the player's retinue overlay may emit player_retinue_ai. A normal opposing or
# allied team assignment remains team_ai, including hold assignments.
replace_once(
    '''                    declaration_events.append({"actor_ref":actor_ref,"result":"holding_guard_position","decision_origin":"player_retinue_ai" if assignment else "team_ai","target_ref":target})\n''',
    '''                    retinue_ai_refs={str(x) for x in (player_retinue_context or {}).get("member_refs",[]) if isinstance(x,str)}\n                    if isinstance(player_retinue_context,Mapping):\n                        retinue_ai_refs.update(str(x) for x in player_retinue_context.get("temporary_member_refs",[]) if isinstance(x,str) and x in retinue_coordinated_refs)\n                    hold_origin="player_retinue_ai" if actor_ref in retinue_ai_refs else "team_ai" if assignment else "actor_ai"\n                    declaration_events.append({"actor_ref":actor_ref,"result":"holding_guard_position","decision_origin":hold_origin,"target_ref":target})\n''',
)

replace_once(
    '''            provenance="player_retinue_ai" if preferred_action=="hold" or (isinstance(assignment,Mapping) and actor_ref in retinue_ai_refs) else "team_ai" if assignment else "actor_ai"\n''',
    '''            provenance="player_retinue_ai" if actor_ref in retinue_ai_refs else "team_ai" if assignment else "actor_ai"\n''',
)

PATH.write_text(text, encoding="utf-8")

TEST = ROOT / "tests/current/test_combat_contact_pursuit_repair.py"
test = TEST.read_text(encoding="utf-8")
addition = r'''


def test_melee_strike_lane_launches_from_post_approach_position(monkeypatch):
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

    original_observe = exact._observe_visible_enemies
    monkeypatch.setattr(
        exact, "_observe_visible_enemies",
        lambda combat, actor_ref, enemy_refs, people, at_ms: [] if actor_ref == defender["person_id"] else original_observe(
            combat, actor_ref=actor_ref, enemy_refs=enemy_refs, people=people, at_ms=at_ms
        ),
    )
    result = exact.resolve_exchange(
        combat=combat, people=people, equipment_ledger=ledger, doctrines={},
        player_ref=attacker["person_id"], player_action_kind="thrust", player_target_ref=defender["person_id"],
        player_weapon_ref="weapon_jian", player_hit_zone="chest", player_targeting_intent="lethal",
    )
    event = next(row for row in result["events"] if row.get("actor_ref") == attacker["person_id"])
    trajectory = event["trace"]["trajectory"]
    assert trajectory["launch_x_mm"] == 600
    assert trajectory["launch_y_mm"] == 0
    assert trajectory["aim_x_mm"] == 1750
    assert event["trace"]["contacts"]
    assert event["trace"]["contacts"][0]["participant_ref"] == defender["person_id"]


def test_enemy_team_hold_assignment_is_not_mislabeled_as_player_retinue_ai(monkeypatch):
    player, enemy = _people_pair()
    people = {player["person_id"]: player, enemy["person_id"]: enemy}
    ledger = _jian_ledger(player["person_id"])
    combat = exact.initialize_combat(
        combat_ref="team-provenance", side_a_refs=[player["person_id"]], side_b_refs=[enemy["person_id"]],
        people=people, zone_ref="test", started_at="SE-0061-01-01T00:00:00",
        objective={"kind": "eliminate", "target_refs": [enemy["person_id"]]}, equipment_ledger=ledger,
    )
    monkeypatch.setattr(
        exact, "_ready_team_assignment",
        lambda plan, actor_ref, at_ms: {"target_ref": player["person_id"], "preferred_action": "hold", "role": "reserve"}
        if actor_ref == enemy["person_id"] else {},
    )
    monkeypatch.setattr(exact, "_hold_position_weapon_for", lambda *args, **kwargs: None)
    result = exact.resolve_exchange(
        combat=combat, people=people, equipment_ledger=ledger, doctrines={},
        player_ref=player["person_id"], player_action_kind="thrust", player_target_ref=enemy["person_id"],
        player_weapon_ref="weapon_jian", player_hit_zone="chest", player_targeting_intent="lethal",
    )
    hold = next(row for row in result["events"] if row.get("actor_ref") == enemy["person_id"] and row.get("result") == "holding_guard_position")
    assert hold["decision_origin"] == "team_ai"
'''
if "test_melee_strike_lane_launches_from_post_approach_position" in test:
    raise SystemExit("new contact regression already present")
TEST.write_text(test.rstrip() + addition + "\n", encoding="utf-8")
print("melee release-origin and provenance repair staged")
