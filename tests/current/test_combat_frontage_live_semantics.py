from __future__ import annotations

from shinobi_runtime.combat import frontage_targeting as frontage


def _person() -> dict:
    return {
        "martial_skills": {
            "sword": 20,
            "spear": 70,
            "bow": 0,
            "hidden_weapons": 0,
            "unarmed": 20,
        },
        "attributes": {
            "speed": 60,
            "dexterity": 60,
            "perception": 60,
            "endurance": 60,
        },
    }


def _pos(x: int, y: int) -> dict:
    return {"x_mm": x, "y_mm": y, "zone_ref": "field", "body_radius_mm": 300}


def test_frontage_wait_can_reactivate_when_a_sector_opens() -> None:
    records = {ref: _person() for ref in ["a0", "a1", "p"]}
    blocked_positions = {
        "a0": _pos(0, 100),
        "a1": _pos(0, 300),
        "p": _pos(2500, 0),
    }
    plan = {
        "objective_kind": "preserve_route_mission",
        "primary_threat_ref": "p",
        "assignments": {
            "a0": {"role": "pressure", "target_ref": "p", "preferred_action": "attack"},
            "a1": {"role": "pressure", "target_ref": "p", "preferred_action": "attack"},
        },
    }

    blocked = frontage.apply_frontage_to_plan(
        plan,
        known_enemy_refs=["p"],
        records=records,
        positions=blocked_positions,
    )
    assert sum(row["preferred_action"] == "hold" for row in blocked["assignments"].values()) == 1

    reopened_positions = {
        "a0": _pos(0, 100),
        "a1": _pos(2500, 0),
        "p": _pos(0, 0),
    }
    reopened = frontage.apply_frontage_to_plan(
        blocked,
        known_enemy_refs=["p"],
        records=records,
        positions=reopened_positions,
    )
    assert all(row["preferred_action"] == "attack" for row in reopened["assignments"].values())


def test_generic_holds_exclude_player_retinue_sector_holds() -> None:
    combat = {
        "team_plans": {
            "side_a": {
                "assignments": {
                    "retinue_guard": {"role": "intercept", "preferred_action": "hold"},
                    "ordinary_ally": {"role": "reserve", "preferred_action": "hold"},
                }
            },
            "side_b": {
                "assignments": {
                    "enemy_anchor": {"role": "anchor", "preferred_action": "hold"},
                    "enemy_pressure": {"role": "pressure", "preferred_action": "attack"},
                }
            },
        }
    }

    held = frontage._generic_hold_refs(
        combat,
        player_retinue_context={"member_refs": ["retinue_guard"], "temporary_member_refs": []},
    )

    assert "retinue_guard" not in held
    assert held == frozenset({"ordinary_ally", "enemy_anchor"})


def test_enemy_frontage_is_recomputed_from_current_positions() -> None:
    people = {ref: _person() for ref in ["wei", "enemy0", "enemy1"]}
    combat = {
        "sides": {"side_a": ["wei"], "side_b": ["enemy0", "enemy1"]},
        "positions": {
            "wei": _pos(2500, 0),
            "enemy0": _pos(0, 100),
            "enemy1": _pos(0, 300),
        },
        "team_plans": {
            "side_b": {
                "primary_threat_ref": "wei",
                "known_enemy_refs": ["wei"],
                "assignments": {
                    "enemy0": {"role": "pressure", "target_ref": "wei", "preferred_action": "attack"},
                    "enemy1": {"role": "pressure", "target_ref": "wei", "preferred_action": "attack"},
                },
            }
        },
    }

    adjusted = frontage.reapply_enemy_frontage(combat, people=people, player_ref="wei")
    rows = adjusted["team_plans"]["side_b"]["assignments"]

    assert sum(row["preferred_action"] == "attack" for row in rows.values()) == 1
    assert sum(row["preferred_action"] == "hold" for row in rows.values()) == 1
