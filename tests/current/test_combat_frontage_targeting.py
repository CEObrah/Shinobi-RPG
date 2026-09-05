from __future__ import annotations

from shinobi_runtime.combat.frontage_targeting import apply_frontage_to_plan


def _person(*, sword: int = 60, spear: int = 60, bow: int = 0, hidden: int = 0) -> dict:
    return {
        "martial_skills": {
            "sword": sword,
            "spear": spear,
            "bow": bow,
            "hidden_weapons": hidden,
            "unarmed": 30,
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


def _plan(assignments: dict[str, dict], primary: str = "p") -> dict:
    return {
        "plan_id": "plan:test",
        "primary_threat_ref": primary,
        "assignments": assignments,
    }


def test_same_front_sector_cannot_stack_melee_assignments_on_one_target() -> None:
    attackers = ["a0", "a1", "a2", "a3"]
    enemies = ["p", "q", "r"]
    records = {ref: _person() for ref in [*attackers, *enemies]}
    positions = {
        "a0": _pos(0, 100),
        "a1": _pos(0, 300),
        "a2": _pos(0, 500),
        "a3": _pos(0, 700),
        "p": _pos(2500, 0),
        "q": _pos(2500, 1800),
        "r": _pos(2500, -1800),
    }
    plan = _plan({
        ref: {"role": "pressure", "target_ref": "p", "preferred_action": "attack"}
        for ref in attackers
    })

    adjusted = apply_frontage_to_plan(
        plan,
        known_enemy_refs=enemies,
        records=records,
        positions=positions,
    )
    rows = adjusted["assignments"]
    active = [row for row in rows.values() if row["preferred_action"] != "hold"]
    primary_active = [row for row in active if row["target_ref"] == "p"]

    assert len(primary_active) == 1
    assert len({row["target_ref"] for row in active}) > 1
    assert any(row["preferred_action"] == "hold" for row in rows.values())


def test_distinct_surrounding_sectors_can_all_pressure_one_target() -> None:
    attackers = ["east", "north", "west", "south"]
    records = {ref: _person() for ref in [*attackers, "p"]}
    positions = {
        "p": _pos(0, 0),
        "east": _pos(2500, 0),
        "north": _pos(0, 2500),
        "west": _pos(-2500, 0),
        "south": _pos(0, -2500),
    }
    plan = _plan({
        ref: {"role": "pressure", "target_ref": "p", "preferred_action": "attack"}
        for ref in attackers
    })

    adjusted = apply_frontage_to_plan(
        plan,
        known_enemy_refs=["p"],
        records=records,
        positions=positions,
    )

    assert all(row["preferred_action"] == "attack" for row in adjusted["assignments"].values())
    assert {row["target_ref"] for row in adjusted["assignments"].values()} == {"p"}


def test_ranged_specialists_do_not_consume_melee_frontage() -> None:
    records = {
        "archer0": _person(sword=10, spear=0, bow=90),
        "archer1": _person(sword=10, spear=0, bow=90),
        "p": _person(),
    }
    positions = {
        "archer0": _pos(0, 100),
        "archer1": _pos(0, 200),
        "p": _pos(5000, 0),
    }
    plan = _plan({
        "archer0": {"role": "ranged_denial", "target_ref": "p", "preferred_action": "attack"},
        "archer1": {"role": "ranged_denial", "target_ref": "p", "preferred_action": "attack"},
    })

    adjusted = apply_frontage_to_plan(
        plan,
        known_enemy_refs=["p"],
        records=records,
        positions=positions,
    )

    assert [row["preferred_action"] for row in adjusted["assignments"].values()] == ["attack", "attack"]


def test_frontage_policy_does_not_leak_unregistered_metadata_into_durable_plan() -> None:
    records = {ref: _person() for ref in ["a0", "a1", "p", "q"]}
    positions = {
        "a0": _pos(0, 100),
        "a1": _pos(0, 300),
        "p": _pos(2500, 0),
        "q": _pos(2500, 1800),
    }
    plan = _plan({
        "a0": {"role": "pressure", "target_ref": "p", "preferred_action": "attack"},
        "a1": {"role": "pressure", "target_ref": "p", "preferred_action": "attack"},
    })

    adjusted = apply_frontage_to_plan(
        plan,
        known_enemy_refs=["p", "q"],
        records=records,
        positions=positions,
    )

    assert set(adjusted) == set(plan)
    allowed_assignment_keys = {"role", "target_ref", "preferred_action"}
    assert all(set(row) <= allowed_assignment_keys for row in adjusted["assignments"].values())


def test_frontage_adjustment_is_deterministic() -> None:
    records = {ref: _person() for ref in ["a0", "a1", "p", "q"]}
    positions = {
        "a0": _pos(0, 100),
        "a1": _pos(0, 300),
        "p": _pos(2500, 0),
        "q": _pos(2500, 1800),
    }
    plan = _plan({
        "a0": {"role": "pressure", "target_ref": "p", "preferred_action": "attack"},
        "a1": {"role": "pressure", "target_ref": "p", "preferred_action": "attack"},
    })
    kwargs = {"known_enemy_refs": ["p", "q"], "records": records, "positions": positions}

    assert apply_frontage_to_plan(plan, **kwargs) == apply_frontage_to_plan(plan, **kwargs)


def test_martial_world_bootstrap_installs_frontage_planner() -> None:
    import shinobi_runtime.martial_world  # noqa: F401
    from shinobi_runtime.martial_world import exact_combat

    assert getattr(exact_combat.plan_team_exchange, "_frontage_targeting", False) is True
