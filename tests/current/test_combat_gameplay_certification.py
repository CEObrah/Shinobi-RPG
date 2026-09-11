from __future__ import annotations

import copy
import json
from pathlib import Path

from shinobi_runtime.combat.models import ActionProfile, CombatIntent, InformationState, Participant, PersonnelState, PositionState
from shinobi_runtime.combat.physical_defense import select_physical_defense
from shinobi_runtime.combat.team_tactics import plan_team_exchange
from shinobi_runtime.martial_world.combat_exertion import fatigue_performance_milli
from shinobi_runtime.martial_world.exact_combat import (
    _usable_hand_count,
    capability_from_person,
    initialize_combat,
    resolve_exchange,
)
from shinobi_runtime.martial_world.health import functional_penalties, wound_from_contact
from shinobi_runtime.martial_world.live_state import player_view_from_person

ROOT = Path(__file__).resolve().parents[2]
BASE = json.loads((ROOT / "state/martial-world/people/house_tang.json").read_text())["people"][0]


def _fighter(ref: str, *, stat: int = 70, discipline: str = "sword", skill: int = 70, fatigue: int = 0):
    row = copy.deepcopy(BASE)
    row["person_id"] = ref
    row["name"] = ref
    row["faction_ref"] = f"faction.{ref}"
    row["fatigue_milli"] = fatigue
    row["health"] = {"status": "ready", "injuries": [], "blood_lost_ml": 0, "shock": 0, "consciousness": 100}
    row["poison_burdens"] = {}
    row["pending_poison_burdens"] = {}
    row["qi"] = 0
    row["current_qi_milli"] = 0
    row["qi_control"] = stat
    row["attributes"] = {key: stat for key in ("strength", "speed", "dexterity", "endurance", "perception", "intelligence", "willpower")}
    row["martial_skills"] = {key: 0 for key in ("sword", "spear", "bow", "hidden_weapons", "unarmed", "stealth_scouting", "command")}
    row["martial_skills"][discipline] = skill
    row.pop("combat_doctrine_ref", None)
    return row


def _ledger(*rows: tuple[str, str]):
    return {
        "schema": "jianghu-equipment-ledger-1.0",
        "policy_assignments": {},
        "person_loadouts": {ref: {"items": {weapon: 1}} for ref, weapon in rows},
    }


def test_relevant_martial_skill_monotonically_changes_real_capability() -> None:
    for discipline in ("sword", "spear"):
        offense = []
        control = []
        for skill in (40, 60, 80, 100, 120):
            row = _fighter(f"{discipline}.{skill}", discipline=discipline, skill=skill)
            cap = capability_from_person(row, action_skill=discipline)
            offense.append(cap.offense)
            control.append(cap.control)
        assert offense == sorted(offense) and len(set(offense)) == len(offense)
        assert control == sorted(control) and len(set(control)) == len(control)
        assert offense[-1] - offense[0] >= 30
        assert control[-1] - control[0] >= 20


def _participant(ref: str, cap, pos: PositionState) -> Participant:
    guard = ActionProfile(
        method_ref="weapon_jian",
        effect_kind="physical",
        delivery="direct",
        startup_ms=0,
        external_contact=True,
        speed_score=cap.reaction,
        effect_parameters={"physical_reach_m": 1.15},
    )
    return Participant(
        participant_ref=ref,
        authoritative_owner_ref=ref,
        side_ref=ref,
        sequence=0,
        representation="exact",
        capability=cap,
        personnel=PersonnelState(total=1, active=1),
        position=pos,
        information=InformationState(observed_refs=("other",)),
        intent=CombatIntent(action="attack"),
        initiative=100,
        readiness=100,
        morale=100,
        cohesion=100,
        action_profile=guard,
        physical_defense_preferences=("parry", "deflect", "block", "brace"),
        balance_milli=1000,
        weapon_position="guard",
    )


def _incoming() -> ActionProfile:
    return ActionProfile(
        method_ref="thrust",
        effect_kind="physical",
        delivery="direct",
        startup_ms=330,
        external_contact=True,
        speed_score=100,
        effect_parameters={"physical_reach_m": 1.15, "geometry": {"shape": "direct", "width_m": 0.35, "length_m": 1.15}},
    )


def _defense_for(stat: int):
    defender_row = _fighter(f"def.{stat}", stat=stat, skill=stat)
    attacker_row = _fighter("atk", stat=70, skill=70)
    defender_cap = capability_from_person(defender_row, action_skill="sword")
    attacker_cap = capability_from_person(attacker_row, action_skill="sword")
    ap = PositionState(zone_ref="z", x_mm=0, y_mm=0, facing_mdeg=0)
    dp = PositionState(zone_ref="z", x_mm=1000, y_mm=0, facing_mdeg=180000)
    attacker = _participant("atk", attacker_cap, ap)
    defender = _participant(f"def.{stat}", defender_cap, dp)
    return select_physical_defense(
        attacker=attacker,
        defender=defender,
        attacker_position=ap,
        defender_position=dp,
        attacker_capability=attacker_cap,
        defender_capability=defender_cap,
        profile=_incoming(),
        line_of_sight=True,
        participant_positions={"atk": ap.to_record(), f"def.{stat}": dp.to_record()},
        body_refs=("atk", f"def.{stat}"),
        obstacles=(),
        at_ms=330,
    )


def test_melee_reaction_calibration_works_at_actual_game_stat_bands() -> None:
    ordinary = _defense_for(45)
    trained = _defense_for(70)
    elite = _defense_for(90)
    master = _defense_for(120)
    assert ordinary.response == "none"
    assert trained.response != "none"
    assert elite.response != "none"
    assert master.response != "none"
    assert ordinary.reaction_delay_ms > trained.reaction_delay_ms > elite.reaction_delay_ms > master.reaction_delay_ms
    assert trained.defense_factor_milli < elite.defense_factor_milli <= master.defense_factor_milli


def test_single_combatant_team_plan_cannot_assign_a_passive_anchor_for_elimination() -> None:
    a = _fighter("solo", stat=70, skill=70)
    b = _fighter("enemy", stat=70, skill=70)
    plan = plan_team_exchange(
        side_ref="side_a",
        member_refs=("solo",),
        known_enemy_refs=("enemy",),
        records={"solo": a, "enemy": b},
        positions={
            "solo": {"zone_ref": "z", "x_mm": 0, "y_mm": 0, "facing_mdeg": 0},
            "enemy": {"zone_ref": "z", "x_mm": 1500, "y_mm": 0, "facing_mdeg": 180000},
        },
        objective_kind="eliminate",
        at_ms=0,
    )
    assignment = plan["assignments"]["solo"]
    assert assignment["preferred_action"] == "attack"
    assert assignment["role"] not in {"anchor", "screen", "protect", "reserve"}


def test_equal_jian_duel_gives_both_sides_real_offensive_cadence() -> None:
    a = _fighter("a", stat=70, skill=70)
    b = _fighter("b", stat=70, skill=70)
    people = {"a": a, "b": b}
    gear = _ledger(("a", "weapon_jian"), ("b", "weapon_jian"))
    combat = initialize_combat(
        combat_ref="cert.equal.jian",
        side_a_refs=("a",),
        side_b_refs=("b",),
        people=people,
        zone_ref="z",
        started_at="x",
        objective={"kind": "eliminate", "target_refs": ["b"]},
        initial_range_band=0,
        equipment_ledger=gear,
        initial_ready_weapons={"a": "weapon_jian", "b": "weapon_jian"},
    )
    attempts = {"a": 0, "b": 0}
    contacts = {"a": 0, "b": 0}
    defended = {"a": 0, "b": 0}
    for _ in range(3):
        if combat.get("status") != "active":
            break
        result = resolve_exchange(
            combat=combat,
            people=people,
            equipment_ledger=gear,
            doctrines={},
            player_ref="a",
            player_action_kind="thrust",
            player_target_ref="b",
            player_weapon_ref="weapon_jian",
            player_hit_zone="chest",
            player_targeting_intent="disable",
            compact_equipment_result=False,
        )
        for event in result["events"]:
            ref = event.get("actor_ref")
            if ref in attempts and event.get("action_kind") in {"thrust", "cut"}:
                attempts[ref] += 1
            if ref in contacts and event.get("result") == "contact":
                contacts[ref] += 1
            target = event.get("intended_ref")
            defense = event.get("defense") if isinstance(event.get("defense"), dict) else {}
            if target in defended and defense.get("response") not in {None, "none"}:
                defended[target] += 1
        combat = result["combat_after"]
        people = result["people_after"]
        gear = result["equipment_ledger_after"]
    assert attempts["a"] >= 1
    assert attempts["b"] >= 1
    assert max(attempts.values()) <= max(1, min(attempts.values())) * 2
    assert abs(contacts["a"] - contacts["b"]) <= 1
    # The cadence invariant is that neither side is starved of real offense and
    # active defense occurs in the exchange. Exact initiative can make one side
    # spend more of this short window attacking while the other parries; forcing
    # a successful defense by *both* sides in exactly three player horizons would
    # reintroduce a round-count assumption.
    assert sum(defended.values()) >= 1


def test_catastrophic_coarse_wrist_trauma_cannot_leave_two_hands_mechanically_healthy() -> None:
    row = _fighter("wounded", discipline="spear", skill=90)
    wound = wound_from_contact(zone="wrist", cut=220, pierce=120, blunt=40, penetration=100, created_at="x")
    assert wound["side"] is None
    row["health"]["injuries"] = [wound]
    penalties = functional_penalties([wound])
    assert penalties["grip"] >= 90
    assert _usable_hand_count(row) == 1
    wounded = capability_from_person(row, action_skill="spear")
    healthy = capability_from_person(_fighter("healthy", discipline="spear", skill=90), action_skill="spear")
    assert wounded.offense < healthy.offense
    assert wounded.control < healthy.control


def test_fatigue_is_monotonic_and_visible_in_player_projection() -> None:
    fresh = _fighter("fresh", fatigue=0)
    tired = _fighter("tired", fatigue=1500)
    exhausted = _fighter("exhausted", fatigue=3000)
    caps = [capability_from_person(row, action_skill="sword") for row in (fresh, tired, exhausted)]
    assert caps[0].offense > caps[1].offense > caps[2].offense
    assert caps[0].mobility > caps[1].mobility > caps[2].mobility
    assert fatigue_performance_milli(0) > fatigue_performance_milli(1500) > fatigue_performance_milli(3000)
    view = player_view_from_person(tired)
    assert view["fatigue_milli"] == 1500
    assert view["fatigue_performance_milli"] == fatigue_performance_milli(1500)
    assert "combat_movement_milli" in view["functional_capacity"]


def test_weapon_reach_creates_opening_advantage_without_close_range_immunity() -> None:
    from shinobi_runtime.martial_world.equipment import weapon_contact_profile

    catalog = json.loads((ROOT / "game/data/martial-world/equipment.json").read_text())["weapon_catalog"]
    jian = catalog["weapon_jian"]
    spear = catalog["weapon_spear"]

    # At two metres the spear owns the threat envelope while a jian cannot
    # contact at all. Reach therefore matters before entry.
    jian_far = weapon_contact_profile(jian, skill=80, strength=80, dexterity=80, range_m=2.0)
    spear_far = weapon_contact_profile(spear, skill=80, strength=80, dexterity=80, range_m=2.0)
    assert jian_far["in_reach"] is False
    assert jian_far["range_factor_milli"] == 0
    assert spear_far["in_reach"] is True
    assert spear_far["range_factor_milli"] == 1000

    # Once the swordsman gets inside the point, both weapons remain physical,
    # but the long spear is materially outside its ideal range rather than
    # retaining a permanent full-strength force field.
    jian_inside = weapon_contact_profile(jian, skill=80, strength=80, dexterity=80, range_m=0.7)
    spear_inside = weapon_contact_profile(spear, skill=80, strength=80, dexterity=80, range_m=0.7)
    assert jian_inside["in_reach"] is True
    assert spear_inside["in_reach"] is True
    assert jian_inside["range_factor_milli"] == 1000
    assert spear_inside["range_factor_milli"] < jian_inside["range_factor_milli"]


def test_catastrophic_coarse_wrist_trauma_blocks_two_handed_spear_in_real_exchange() -> None:
    a = _fighter("a", stat=80, discipline="spear", skill=90)
    b = _fighter("b", stat=80, discipline="sword", skill=80)
    a["health"]["injuries"] = [
        wound_from_contact(zone="wrist", cut=220, pierce=120, blunt=40, penetration=100, created_at="x")
    ]
    people = {"a": a, "b": b}
    gear = _ledger(("a", "weapon_spear"), ("b", "weapon_jian"))
    combat = initialize_combat(
        combat_ref="cert.coarse.wrist.spear",
        side_a_refs=("a",),
        side_b_refs=("b",),
        people=people,
        zone_ref="z",
        started_at="x",
        objective={"kind": "eliminate", "target_refs": ["b"]},
        initial_range_band=0,
        equipment_ledger=gear,
        initial_ready_weapons={"a": "weapon_spear", "b": "weapon_jian"},
    )
    result = resolve_exchange(
        combat=combat,
        people=people,
        equipment_ledger=gear,
        doctrines={},
        player_ref="a",
        player_action_kind="thrust",
        player_target_ref="b",
        player_weapon_ref="weapon_spear",
        player_hit_zone="chest",
        player_targeting_intent="disable",
        compact_equipment_result=False,
    )
    actor_events = [row for row in result["events"] if row.get("actor_ref") == "a"]
    assert any(row.get("result") == "weapon_hand_control_unavailable" for row in actor_events)
    assert not any(row.get("result") == "contact" for row in actor_events)



def test_weapon_condition_materially_degrades_real_combat_weapon_profile() -> None:
    from shinobi_runtime.martial_world.exact_combat import _weapon_for_holder

    row = _fighter("condition", stat=80, discipline="sword", skill=80)
    pristine = _ledger(("condition", "weapon_jian"))
    damaged = _ledger(("condition", "weapon_jian"))
    pristine["person_loadouts"]["condition"]["condition_milli"] = {"weapon_jian": 1000}
    damaged["person_loadouts"]["condition"]["condition_milli"] = {"weapon_jian": 250}
    full = _weapon_for_holder(pristine, "condition", "weapon_jian")
    worn = _weapon_for_holder(damaged, "condition", "weapon_jian")
    assert full is not None and worn is not None
    for key in ("cut", "pierce", "penetration", "precision", "control", "guard"):
        assert int(worn[key]) < int(full[key]), key
    assert float(worn["reach_m"]) == float(full["reach_m"])


def test_current_player_equipment_projection_matches_real_logical_loadout() -> None:
    from shinobi_runtime.api.operations import _player_equipment_projection
    from shinobi_runtime.martial_world.equipment_state import effective_person_loadout

    meta = json.loads((ROOT / "state/meta.json").read_text())
    ledger = json.loads((ROOT / "state/martial-world/equipment-ledger.json").read_text())
    player_id = meta["player_id"]
    expected = effective_person_loadout(ledger, player_id)
    projected = _player_equipment_projection(ledger, player_id)
    assert projected["items"] == expected.get("items", {})
    assert projected["condition_milli"] == expected.get("condition_milli", {})
    assert set(projected["condition_milli"]).issubset(projected["items"])


def test_core_combat_attributes_have_monotonic_effects_at_realistic_bands() -> None:
    values = (40, 60, 80, 100, 120)

    def sweep(attribute: str, metric: str) -> list[int]:
        out = []
        for value in values:
            row = _fighter(f"{attribute}.{value}", stat=80, discipline="sword", skill=80)
            row["attributes"][attribute] = value
            out.append(int(getattr(capability_from_person(row, action_skill="sword"), metric)))
        return out

    expectations = {
        ("strength", "offense"),
        ("speed", "mobility"),
        ("speed", "reaction"),
        ("dexterity", "control"),
        ("dexterity", "defense"),
        ("perception", "reaction"),
        ("perception", "offense"),
        ("endurance", "defense"),
        ("intelligence", "control"),
    }
    for attribute, metric in expectations:
        series = sweep(attribute, metric)
        assert series == sorted(series), (attribute, metric, series)
        assert series[-1] > series[0], (attribute, metric, series)



def test_real_black_lance_12v18_replay_is_contested_not_a_medic_or_short_reach_exploit() -> None:
    fixture = json.loads((ROOT / "tests/fixtures/black_lance_fresh_group_certification.json").read_text())
    combat = copy.deepcopy(fixture["combat"])
    people = copy.deepcopy(fixture["people"])
    gear = copy.deepcopy(fixture["equipment_ledger"])
    player_ref = fixture["player_ref"]
    player_target_ref = fixture["player_target_ref"]
    retinue_context = copy.deepcopy(fixture["retinue_context"])
    a_refs = set(combat["sides"]["side_a"])
    b_refs = set(combat["sides"]["side_b"])
    medic_ref = next(ref for ref, role in retinue_context["member_roles"].items() if role == "field_medic")

    contacts = {"a": 0, "b": 0}
    wei_contacts = 0
    medic_offense = 0
    medical_hold_events = 0
    same_side_contacts = 0
    first_plan_actions = None
    spear_families: list[str] = []

    # Resolver-call count is not a physical-time unit. The event-driven combat
    # scheduler deliberately returns at player decision horizons, so certify the
    # group fight over a bounded 20-second combat span instead of assuming eight
    # calls always cover the same duration.
    for _ in range(80):
        if combat.get("status") != "active" or int(combat.get("elapsed_ms", 0)) >= 20_000:
            break
        if people[player_target_ref].get("health", {}).get("status") in {"dead", "incapacitated"}:
            player_target_ref = next(
                ref for ref in combat["sides"]["side_b"]
                if people[ref].get("health", {}).get("status") not in {"dead", "incapacitated"}
            )
        result = resolve_exchange(
            combat=combat,
            people=people,
            equipment_ledger=gear,
            doctrines=copy.deepcopy(fixture.get("doctrines", {})),
            player_ref=player_ref,
            player_action_kind="thrust",
            player_target_ref=player_target_ref,
            player_weapon_ref="weapon_jian",
            player_hit_zone="auto",
            player_targeting_intent="disable",
            player_retinue_context=retinue_context,
            compact_equipment_result=False,
        )
        for event in result["events"]:
            actor = event.get("actor_ref")
            actual = event.get("actual_ref")
            if event.get("result") == "holding_medical_support_position":
                medical_hold_events += 1
            if actor == medic_ref and event.get("action_kind") in {
                "thrust", "cut", "hidden_weapon_throw", "unarmed_strike"
            }:
                medic_offense += 1
            if event.get("result") == "contact":
                if actor in a_refs:
                    contacts["a"] += 1
                    if actor == player_ref:
                        wei_contacts += 1
                elif actor in b_refs:
                    contacts["b"] += 1
            if event.get("result") == "contact" and actor in a_refs | b_refs and actual in a_refs | b_refs:
                if (actor in a_refs) == (actual in a_refs):
                    same_side_contacts += 1
            if actor in b_refs:
                structure = event.get("target_structure_ref")
                if isinstance(structure, str):
                    family = next((name for name in ("wrist", "elbow", "knee", "ankle") if name in structure), None)
                    if family:
                        spear_families.append(family)
        if first_plan_actions is None:
            assignments = result["combat_after"].get("team_plans", {}).get("side_a", {}).get("assignments", {})
            first_plan_actions = [
                str(row.get("preferred_action") or "")
                for row in assignments.values() if isinstance(row, dict)
            ]
        combat = result["combat_after"]
        people = result["people_after"]
        gear = result["equipment_ledger_after"]

    assert len(retinue_context.get("temporary_member_refs", [])) == 8
    assert medical_hold_events == 0
    assert medic_offense >= 1
    assert same_side_contacts == 0
    assert first_plan_actions is not None
    assert first_plan_actions.count("attack") >= 7
    assert "medical_support_hold" not in first_plan_actions
    assert wei_contacts >= 2
    assert contacts["a"] >= 10 and contacts["b"] >= 10, contacts
    assert max(contacts.values()) <= min(contacts.values()) * 3, contacts
    assert len(set(spear_families)) >= 3, spear_families
    most_common = max(spear_families.count(name) for name in set(spear_families))
    assert most_common * 4 < len(spear_families) * 3, spear_families



def test_black_lance_live_like_jian_and_needles_does_not_recreate_stale_hit_cascade() -> None:
    fixture = json.loads((ROOT / "tests/fixtures/black_lance_fresh_group_certification.json").read_text())
    combat = copy.deepcopy(fixture["combat"])
    people = copy.deepcopy(fixture["people"])
    gear = copy.deepcopy(fixture["equipment_ledger"])
    doctrines = copy.deepcopy(fixture.get("doctrines", {}))
    player_ref = fixture["player_ref"]
    retinue_context = copy.deepcopy(fixture["retinue_context"])
    gear["person_loadouts"][player_ref]["items"]["weapon_jian"] = 1
    gear["person_loadouts"][player_ref]["items"]["weapon_needle"] = 40
    combat["combatants"][player_ref]["ready_weapon_ref"] = "weapon_jian"
    combat["combatants"][player_ref]["ready_hands_required"] = 1

    hostile_refs = set(combat["sides"]["side_b"])
    hostile_resolved = 0
    hostile_contacts = 0
    player_throws = 0
    player_throw_disruptions = 0
    hostile_contact_times: list[int] = []

    for _ in range(160):
        if combat.get("status") != "active" or int(combat.get("elapsed_ms", 0)) >= 47_000:
            break
        if people[player_ref].get("health", {}).get("status") in {"dead", "incapacitated"}:
            break
        targets = [
            ref for ref in combat["combatants"][player_ref].get("current_contact_refs", [])
            if ref in hostile_refs
            and people[ref].get("health", {}).get("status") not in {"dead", "incapacitated"}
            and combat["combatants"].get(ref, {}).get("escaped_at_ms") is None
        ]
        if not targets:
            targets = [
                ref for ref in combat["sides"]["side_b"]
                if people[ref].get("health", {}).get("status") not in {"dead", "incapacitated"}
                and combat["combatants"].get(ref, {}).get("escaped_at_ms") is None
            ]
        assert targets
        result = resolve_exchange(
            combat=combat,
            people=people,
            equipment_ledger=gear,
            doctrines=doctrines,
            player_ref=player_ref,
            player_action_kind="hidden_weapon_throw",
            player_target_ref=targets[0],
            player_weapon_ref="weapon_needle",
            player_hit_zone="eyes",
            player_targeting_intent="lethal",
            player_retinue_context=retinue_context,
            compact_equipment_result=False,
        )
        for event in result["events"]:
            if event.get("actor_ref") == player_ref and event.get("action_kind") == "hidden_weapon_throw":
                player_throws += 1
                if event.get("result") in {
                    "action_interrupted_by_defense_before_commitment",
                    "action_disrupted_by_defense_after_commitment",
                }:
                    player_throw_disruptions += 1
            if (
                event.get("actor_ref") in hostile_refs
                and event.get("intended_ref") == player_ref
                and event.get("action_kind") in {"thrust", "cut", "unarmed_strike", "staff_sweep"}
                and event.get("result") not in {"melee_approach_in_progress", "melee_approach_blocked"}
            ):
                hostile_resolved += 1
                if event.get("result") == "contact":
                    hostile_contacts += 1
                    hostile_contact_times.append(int(event.get("contact_at_ms", 0)))
        combat = result["combat_after"]
        people = result["people_after"]
        gear = result["equipment_ledger_after"]

    # This is a shape-of-simulation certification, not a demanded story result.
    # It specifically catches the old synchronized-hit cascade, stale completed
    # defense penalties, and stale interruption evidence that made later throws
    # impossible regardless of what happened on the current horizon.
    assert int(combat.get("elapsed_ms", 0)) >= 40_000
    assert player_throws >= 12
    assert player_throw_disruptions < player_throws * 2 // 3
    assert hostile_resolved >= 12
    assert hostile_contacts * 5 <= hostile_resolved * 2
    assert people[player_ref].get("health", {}).get("status") not in {"dead", "incapacitated"}
    assert all(b - a >= 200 for a, b in zip(hostile_contact_times, hostile_contact_times[1:]))
