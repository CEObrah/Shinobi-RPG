from __future__ import annotations

import copy

from shinobi_runtime.combat.physical_defense import close_attacker_into_reach
from shinobi_runtime.martial_world import exact_combat as exact


def _person(person_id: str, *, faction_ref: str, speed: int, dexterity: int, sword: int = 0, spear: int = 0) -> dict:
    return {
        "person_id": person_id,
        "faction_ref": faction_ref,
        "body_mass_kg": 64,
        "attributes": {
            "strength": 75,
            "speed": speed,
            "dexterity": dexterity,
            "endurance": 80,
            "perception": 85,
            "intelligence": 60,
            "willpower": 80,
        },
        "martial_skills": {
            "sword": sword,
            "spear": spear,
            "bow": 0,
            "hidden_weapons": 0,
            "unarmed": 20,
            "stealth_scouting": 0,
            "command": 0,
        },
        "qi": 0,
        "qi_control": 0,
        "current_qi_milli": 0,
        "fatigue_milli": 0,
        "health": {
            "status": "ready",
            "injuries": [],
            "blood_lost_ml": 0,
            "shock": 0,
            "consciousness": 100,
        },
        "poison_burdens": {},
        "pending_poison_burdens": {},
    }


def _fixture() -> tuple[dict, dict, dict]:
    people = {
        "wei": _person("wei", faction_ref="house_tang", speed=90, dexterity=94, sword=115),
        "spear": _person("spear", faction_ref="enemy", speed=60, dexterity=55, spear=50),
    }
    ledger = {
        "schema": "jianghu-equipment-ledger-1.0",
        "policy_assignments": {},
        "person_loadouts": {
            "wei": {"items": {"weapon_jian": 1}, "condition_milli": {"weapon_jian": 1000}},
            "spear": {"items": {"weapon_spear": 1}, "condition_milli": {"weapon_spear": 1000}},
        },
    }
    combat = exact.initialize_combat(
        combat_ref="combat.test.continuous-pursuit",
        side_a_refs=["wei"],
        side_b_refs=["spear"],
        people=people,
        zone_ref="site.test",
        started_at="0061-01-01T00:00:00",
        objective={"kind": "duel"},
        awareness_mode="mutual",
        initial_range_band=1,
        equipment_ledger=ledger,
        initial_ready_weapons={"wei": "weapon_jian", "spear": "weapon_spear"},
    )
    combat["positions"]["wei"].update(x_mm=0, y_mm=0, elevation_mm=0, facing_mdeg=0)
    combat["positions"]["spear"].update(x_mm=1000, y_mm=0, elevation_mm=0, facing_mdeg=180000)
    return combat, people, ledger


def _schedule(combat: dict, people: dict, ledger: dict, *, movement_intent: str | None = None):
    return exact._schedule_action(
        combat=combat,
        actor_ref="wei",
        target_ref="spear",
        action_kind="thrust",
        weapon_ref="weapon_jian",
        poison_ref=None,
        hit_zone="chest",
        target_structure_ref=None,
        decision_origin="player",
        people=people,
        equipment_ledger=ledger,
        movement_intent=movement_intent,
    )


def test_melee_startup_preserves_finite_follow_budget_when_target_begins_inside_reach() -> None:
    combat, people, ledger = _fixture()
    action = _schedule(combat, people, ledger)
    params = action.profile.effect_parameters

    # This is the historical spear-lock trigger: the target is initially inside
    # sword reach, so no declaration-time approach is needed.
    assert params["approach_distance_mm"] == 0
    # The action must nevertheless preserve finite lawful locomotion through its
    # startup. A target moving after declaration cannot turn that zero snapshot
    # into a zero chase allowance.
    assert params["pursuit_follow_distance_mm"] > 0
    assert params["melee_movement_budget_mm"] >= params["pursuit_follow_distance_mm"]

    moved_target = copy.deepcopy(combat["positions"]["spear"])
    moved_target["x_mm"] = 2000
    positions = copy.deepcopy(combat["positions"])
    positions["spear"] = moved_target
    moved, trace = close_attacker_into_reach(
        attacker_ref="wei",
        defender_ref="spear",
        positions=positions,
        attacker_position=exact._pos(positions["wei"]),
        defender_position=exact._pos(positions["spear"]),
        attacker_capability=exact._combat_capability("wei", people["wei"], ledger, action_skill="sword"),
        profile=action.profile,
        body_refs=["wei", "spear"],
        obstacles=[],
    )
    assert moved.x_mm > positions["wei"]["x_mm"]
    assert trace["moved"] is True
    assert trace["distance_mm"] > 0
    assert trace.get("reason") != "target_moved_beyond_committed_approach"


def test_pending_melee_pursuit_materializes_on_same_clock_as_withdrawal() -> None:
    combat, people, ledger = _fixture()
    action = _schedule(combat, people, ledger)
    pending = exact._pending_action_record(action)
    # Make the regression explicit: the pursuit is still pre-commit over this
    # slice. Old escape logic only protected already-committed melee and allowed
    # the slower target to create uncontested separation here.
    pending["commit_at_ms"] = 1400
    pending["release_at_ms"] = 1800
    pending["start_at_ms"] = 0
    pending["melee_movement_budget_mm"] = max(4000, pending["melee_movement_budget_mm"])
    pending["approach_speed_mmps"] = max(4000, pending["approach_speed_mmps"])
    combat["_pending_actions"] = {"wei": pending}

    # Materialize the slower spearman's withdrawal first, exactly as the combat
    # clock does, then settle the already-authorized pursuing motion on that same
    # slice. The pursuer should move even though commit_at_ms is after the slice.
    combat["positions"]["spear"]["x_mm"] = 3800
    traces = exact._settle_pending_melee_pursuit_during_withdrawal(
        combat=combat,
        withdrawer_ref="spear",
        people=people,
        equipment_ledger=ledger,
        start_ms=0,
        end_ms=1000,
    )
    assert traces and traces[0]["moved"] is True
    assert combat["positions"]["wei"]["x_mm"] > 0
    assert exact.planar_distance_mm(combat["positions"]["wei"], combat["positions"]["spear"]) <= pending["physical_reach_mm"]


def test_close_and_chase_intents_allocate_real_time_and_distance_even_from_inside_reach() -> None:
    combat, people, ledger = _fixture()
    ordinary = _schedule(combat, people, ledger)
    close = _schedule(combat, people, ledger, movement_intent="close")
    chase = _schedule(combat, people, ledger, movement_intent="chase")

    assert ordinary.profile.effect_parameters["approach_distance_mm"] == 0
    assert close.profile.effect_parameters["approach_time_ms"] > 0
    assert close.profile.effect_parameters["approach_distance_mm"] > 0
    assert chase.profile.effect_parameters["approach_time_ms"] >= close.profile.effect_parameters["approach_time_ms"]
    assert chase.profile.effect_parameters["approach_distance_mm"] >= close.profile.effect_parameters["approach_distance_mm"]
    # These are real movement windows, so the attack cannot contact earlier than
    # an otherwise identical ordinary strike simply because the player chose to
    # chase or close.
    assert close.contact_at_ms > ordinary.contact_at_ms
    assert chase.contact_at_ms >= close.contact_at_ms


def test_move_only_close_maneuver_changes_position_without_authoring_attack() -> None:
    combat, people, ledger = _fixture()
    combat["positions"]["spear"]["x_mm"] = 4500
    before = copy.deepcopy(combat["positions"]["wei"])
    result = exact.resolve_exchange(
        combat=combat,
        people=people,
        equipment_ledger=ledger,
        doctrines={"house_tang": {}, "enemy": {}},
        player_ref="wei",
        player_action_kind="maneuver",
        player_target_ref="spear",
        player_weapon_ref="weapon_jian",
        player_movement_intent="close",
        player_auto_qi=False,
        player_auto_poison=False,
    )
    player_events = [row for row in result["events"] if row.get("actor_ref") == "wei"]
    assert player_events
    maneuver = next(row for row in player_events if row.get("action_kind") == "maneuver")
    assert maneuver["result"] in {"maneuver_completed", "action_interrupted_by_defense_before_commitment", "action_disrupted_by_defense_after_commitment"}
    # The maneuver itself never becomes weapon contact. Enemy activity can still
    # interrupt it because everybody remains on the same exact-combat clock.
    assert not any(
        row.get("actor_ref") == "wei" and row.get("result") in {"contact", "mount_contact", "mount_disabled"}
        for row in result["events"]
    )
    after = result["combat_after"]["positions"]["wei"]
    if maneuver["result"] == "maneuver_completed":
        assert (after["x_mm"], after["y_mm"]) != (before["x_mm"], before["y_mm"])


def test_high_stat_swordsman_is_not_frozen_outside_weaker_spear_point_during_close() -> None:
    """Regression for the live spear-lock complaint.

    The weaker spearman may still intercept and wound the closing swordsman. The
    invariant is narrower: reach cannot become a static exclusion radius that
    prevents a materially faster fighter's already-authorized locomotion from
    existing on the shared clock.
    """
    combat, people, ledger = _fixture()
    combat["positions"]["spear"].update(x_mm=5000, y_mm=0, facing_mdeg=180000)
    before_distance = exact.planar_distance_mm(combat["positions"]["wei"], combat["positions"]["spear"])

    result = exact.resolve_exchange(
        combat=combat,
        people=people,
        equipment_ledger=ledger,
        doctrines={"house_tang": {}, "enemy": {}},
        player_ref="wei",
        player_action_kind="maneuver",
        player_target_ref="spear",
        player_weapon_ref="weapon_jian",
        player_movement_intent="close",
        player_auto_qi=False,
        player_auto_poison=False,
    )

    after = result["combat_after"]
    after_distance = exact.planar_distance_mm(after["positions"]["wei"], after["positions"]["spear"])
    player_events = [row for row in result["events"] if row.get("actor_ref") == "wei"]
    spear_resolutions = [
        row for row in result["events"]
        if row.get("actor_ref") == "spear"
        and row.get("action_kind") == "thrust"
        and row.get("result") not in {"melee_approach_in_progress", "melee_approach_blocked"}
    ]

    assert after_distance < before_distance
    assert after["positions"]["wei"]["x_mm"] > combat["positions"]["wei"]["x_mm"]
    # The point remains dangerous and receives a real resolution. A successful
    # elite parry is not evidence that spear reach became an automatic-entry
    # bonus, so this invariant must not require a wound as proof of threat.
    assert spear_resolutions
    assert player_events
    assert any(
        row.get("action_kind") == "maneuver"
        and row.get("result") in {
            "maneuver_completed",
            "action_interrupted_by_defense_before_commitment",
            "action_disrupted_by_defense_after_commitment",
        }
        for row in player_events
    )
