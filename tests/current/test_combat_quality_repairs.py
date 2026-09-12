from __future__ import annotations

from shinobi_runtime.api import transition_operations
from shinobi_runtime.api.gm_scene_context import build_gm_scene_context
from shinobi_runtime.combat.models import (
    ActionProfile,
    CapabilityProfile,
    CombatIntent,
    InformationState,
    Participant,
    PersonnelState,
    PositionState,
    ReactiveDefense,
)
from shinobi_runtime.combat.physical_defense import _movement_response, contact_after_defense, select_physical_defense
from shinobi_runtime.commands.jianghu_extended import _player_poison_instruction
from shinobi_runtime.martial_world import exact_combat as exact
from shinobi_runtime.martial_world.health import (
    functional_capacity_factors,
    functional_penalties,
    record_current_wound,
    wound_from_contact,
)


def _cap(value: int) -> CapabilityProfile:
    return CapabilityProfile(
        offense=value,
        defense=value,
        control=value,
        mobility=value,
        perception=value,
        stealth=0,
        capture=value,
        escape=value,
        reaction=value,
    )


def _participant(
    ref: str,
    *,
    side: str,
    position: PositionState,
    capability: CapabilityProfile,
    observed: tuple[str, ...],
    defense_load: int = 0,
    recovery_ms: int = 0,
    weapon_position: str = "guard",
) -> Participant:
    guard = ActionProfile(
        method_ref="guard",
        effect_kind="physical",
        delivery="direct",
        startup_ms=0,
        external_contact=True,
        speed_score=capability.reaction,
        effect_parameters={"physical_reach_m": 2.5},
    )
    return Participant(
        participant_ref=ref,
        authoritative_owner_ref=ref,
        side_ref=side,
        sequence=0,
        representation="exact",
        capability=capability,
        personnel=PersonnelState(total=1, active=1),
        position=position,
        information=InformationState(observed_refs=observed),
        intent=CombatIntent(action="attack"),
        initiative=100,
        readiness=100,
        morale=100,
        cohesion=100,
        action_profile=guard,
        reactive_defenses=(ReactiveDefense(defense_ref="weapon_jian", defense_kind="weapon_guard"),),
        active_defense_load_milli=defense_load,
        balance_milli=1000,
        limb_commitment_milli=500 if recovery_ms else 0,
        recovery_remaining_ms=recovery_ms,
        weapon_position=weapon_position,
        physical_defense_preferences=("parry", "deflect", "block", "brace"),
    )


def _incoming_thrust() -> ActionProfile:
    return ActionProfile(
        method_ref="thrust",
        effect_kind="physical",
        delivery="direct",
        startup_ms=330,
        external_contact=True,
        speed_score=80,
        effect_parameters={
            "commitment_milli": 470,
            "physical_reach_m": 2.5,
            "geometry": {"shape": "direct", "width_m": 0.06, "length_m": 2.5},
        },
    )




def test_defensive_sidestep_keeps_guard_oriented_on_incoming_attacker() -> None:
    defender = PositionState(
        zone_ref="z", x_mm=1000, y_mm=0, facing_mdeg=180000,
        vx_mmps=0, vy_mmps=0, stance="ready",
    )
    moved = _movement_response(
        response="reposition",
        attacker_ref="attacker",
        defender_ref="defender",
        participant_positions={
            "attacker": {"zone_ref": "z", "x_mm": 0, "y_mm": 0, "facing_mdeg": 0},
            "defender": defender.to_record(),
        },
        defender_position=defender,
        incoming_bearing_mdeg=180000,
        defender_capability=_cap(100),
        reaction_delay_ms=180,
        warning_ms=330,
        body_refs=("attacker", "defender"),
        obstacles=(),
    )
    assert moved is not None
    assert (moved.x_mm, moved.y_mm) != (defender.x_mm, defender.y_mm)
    # Footwork may travel laterally/diagonally, but a defensive step against an
    # observed attacker must not rotate the fighter's guard toward travel and
    # expose a false blind side on the next beat.
    assert moved.facing_mdeg == 180000

def test_side_unknown_coarse_injury_reduces_capacity_without_inventing_a_side() -> None:
    wound = {
        "zone": "knee",
        "structure_ref": None,
        "side": None,
        "cut": 30,
        "pierce": 150,
        "blunt": 120,
        "penetration": 140,
        "severity": 160,
        "bleeding_ml_per_min": 45,
        "fracture": 0,
        "tendon_damage": 120,
        "nerve_damage": 110,
        "organ_trauma": 0,
        "structure_damage": 0,
        "functional_effects": {},
        "pain": 160,
        "function_loss_pct": 90,
    }
    penalties = functional_penalties([wound])
    capacity = functional_capacity_factors([wound])

    assert penalties["leg"] > 0 and penalties["footwork"] > 0
    assert penalties["leg_left"] == penalties["leg_right"] == 0
    assert capacity["walking_milli"] < 1000
    assert capacity["combat_movement_milli"] < 1000
    assert capacity["field_mobility_milli"] < 1000


def test_repeated_coarse_zone_contacts_worsen_without_linear_same_structure_stacking() -> None:
    wounds = []
    first = wound_from_contact(zone="knee", cut=4, pierce=48, blunt=38, penetration=42, created_at="t0")
    for index in range(6):
        wounds = record_current_wound(
            wounds,
            wound_from_contact(zone="knee", cut=4, pierce=48, blunt=38, penetration=42, created_at=f"t{index}"),
        )
    merged = wounds[0]

    assert len(wounds) == 1
    assert int(merged["severity"]) > int(first["severity"])
    assert int(merged["severity"]) < 200
    assert int(merged["bleeding_ml_per_min"]) < int(first["bleeding_ml_per_min"]) * 6
    assert int(merged["pierce"]) < min(200, int(first["pierce"]) * 6)


def test_exact_structure_contacts_still_accumulate_to_irreversible_damage() -> None:
    first = wound_from_contact(structure_ref="left_eye", cut=0, pierce=48, blunt=0, penetration=48, created_at="a")
    second = wound_from_contact(structure_ref="left_eye", cut=0, pierce=48, blunt=0, penetration=48, created_at="b")
    merged = record_current_wound([first], second)[0]
    assert merged["permanent"] is True
    assert merged["permanent_outcome"] == "left_eye_destroyed"


def test_authored_thrust_profile_uses_narrow_direct_weapon_path() -> None:
    actor = {
        "attributes": {"strength": 80, "speed": 90, "dexterity": 90, "endurance": 80, "perception": 90, "intelligence": 80, "willpower": 80},
        "martial_skills": {"spear": 80},
    }
    profile, _weapon = exact._action_profile(
        "thrust",
        actor,
        "weapon_spear",
        {"x_mm": 2000, "y_mm": 0, "elevation_mm": 0},
        {"x_mm": 0, "y_mm": 0, "elevation_mm": 0},
    )
    assert profile.effect_parameters["geometry"]["shape"] == "direct"
    assert profile.effect_parameters["geometry"]["width_m"] == 0.06


def test_melee_tracking_blends_committed_aim_continuously_without_threshold_homing() -> None:
    profile = ActionProfile(
        method_ref="thrust",
        effect_kind="physical",
        delivery="direct",
        startup_ms=330,
        external_contact=True,
        speed_score=80,
        effect_parameters={
            "physical_reach_m": 2.5,
            "intended_target_ref": "defender",
            "geometry": {"shape": "direct", "width_m": 0.04, "length_m": 2.5},
            "committed_melee_trajectory": {
                "launch_x_mm": 0,
                "launch_y_mm": 0,
                "launch_elevation_mm": 0,
                "aim_x_mm": 1000,
                "aim_y_mm": 0,
                "aim_elevation_mm": 0,
            },
        },
    )
    positions = {
        "attacker": {"zone_ref": "test", "x_mm": 0, "y_mm": 0, "elevation_mm": 0, "body_radius_mm": 300},
        "defender": {"zone_ref": "test", "x_mm": 1000, "y_mm": 1000, "elevation_mm": 0, "body_radius_mm": 300},
    }
    original = PositionState(zone_ref="test", x_mm=1000, y_mm=0)

    low = contact_after_defense(
        attacker_ref="attacker", defender_ref="defender", positions=positions, profile=profile,
        obstacles=(), trajectory=None, tracking_milli=649, original_defender_position=original,
        body_refs=("defender",),
    )
    high = contact_after_defense(
        attacker_ref="attacker", defender_ref="defender", positions=positions, profile=profile,
        obstacles=(), trajectory=None, tracking_milli=650, original_defender_position=original,
        body_refs=("defender",),
    )
    low_y = int(low["trace"]["trajectory"]["aim_y_mm"])
    high_y = int(high["trace"]["trajectory"]["aim_y_mm"])

    assert 0 < low_y < 1000
    assert 0 < high_y < 1000
    assert abs(high_y - low_y) <= 2
    assert low["tracking_aim_shift_mm"] == low_y
    assert high["tracking_aim_shift_mm"] == high_y


def test_live_committed_guard_can_cover_near_simultaneous_followup() -> None:
    attacker_pos = PositionState(zone_ref="test", x_mm=0, y_mm=0, facing_mdeg=0)
    defender_pos = PositionState(zone_ref="test", x_mm=1000, y_mm=0, facing_mdeg=180_000)
    attacker_cap = _cap(100)
    defender_cap = _cap(180)
    attacker = _participant("attacker", side="a", position=attacker_pos, capability=attacker_cap, observed=("defender",))
    defender = _participant(
        "defender", side="b", position=defender_pos, capability=defender_cap, observed=("attacker",),
        defense_load=700, recovery_ms=500, weapon_position="committed_guard",
    )

    decision = select_physical_defense(
        attacker=attacker, defender=defender,
        attacker_position=attacker_pos, defender_position=defender_pos,
        attacker_capability=attacker_cap, defender_capability=defender_cap,
        profile=_incoming_thrust(), line_of_sight=True,
        participant_positions={"attacker": attacker_pos.to_record(), "defender": defender_pos.to_record()},
        body_refs=("attacker", "defender"), obstacles=(), at_ms=34,
    )

    assert decision.detected is True
    assert decision.response != "none"
    assert decision.reason == "existing_guard_covers_followup"


def test_sound_weapon_defense_can_stop_body_contact_but_weak_late_guard_cannot() -> None:
    profile = _incoming_thrust()
    base = dict(
        detected=True,
        detection_margin=100,
        response="block",
        before_position=PositionState(zone_ref="test", x_mm=0, y_mm=0),
        after_position=PositionState(zone_ref="test", x_mm=0, y_mm=0),
        displacement_mm=0,
        reaction_delay_ms=258,
        recovery_ms=518,
        reaction_availability_milli=402,
        balance_after_milli=900,
        limb_commitment_after_milli=500,
        weapon_position_after="committed_guard",
        attack_angle_mdeg=0,
        tracking_milli=316,
        displacement_resistance_milli=720,
        interrupts_attacker=False,
        contact_surface="weapon_or_body_guard",
        reason="lawful_physical_response_selected",
    )
    from shinobi_runtime.combat.physical_defense import PhysicalDefenseDecision

    sound = PhysicalDefenseDecision(defense_factor_milli=305, force_transmission_milli=808, control_disruption=35, **base)
    weak = PhysicalDefenseDecision(defense_factor_milli=120, force_transmission_milli=950, control_disruption=10, **base)

    assert exact._weapon_defense_stops_body_contact(sound, profile) is True
    assert exact._weapon_defense_stops_body_contact(weak, profile) is False


def test_player_poison_requires_explicit_auto_or_exact_ref() -> None:
    assert _player_poison_instruction({}) == (None, False)
    assert _player_poison_instruction({"poison_ref": "none"}) == (None, False)
    assert _player_poison_instruction({"poison_ref": "auto"}) == (None, True)
    assert _player_poison_instruction({"poison_ref": "cardiotoxic"}) == ("cardiotoxic", False)


def test_transition_narrative_sampling_preserves_beginning_middle_and_end() -> None:
    events = [
        {
            "actor_ref": "wei",
            "intended_ref": "enemy",
            "actual_ref": "enemy",
            "action_kind": "thrust",
            "result": "contact",
            "contact_at_ms": index,
            "damage": {"wound": {"zone": "forearm", "severity": 20 + index % 5}},
        }
        for index in range(120)
    ]
    summary = transition_operations._combat_narrative_summary(events, frozenset({"enemy"}))
    beats = summary["material_beats"]

    assert summary["material_event_count"] == 120
    assert len(beats) == 96
    assert beats[0]["at_ms"] == 0
    assert beats[-1]["at_ms"] == 119
    assert any(45 <= int(row["at_ms"]) <= 75 for row in beats)

    transition_operations._trim_optional_combat_narrative(summary)
    trimmed = summary["material_beats"]
    assert trimmed[0]["at_ms"] == 0
    assert trimmed[-1]["at_ms"] == 119
    assert any(45 <= int(row["at_ms"]) <= 75 for row in trimmed)


def test_gm_scene_context_separates_physical_visibility_from_hidden_combat_identity() -> None:
    context = {
        "campaign": {"player_id": "wei", "world_time": "SE-test"},
        "player": {"person_id": "wei", "faction_ref": "house_tang", "current_location_id": "road"},
        "scene": {
            "location_id": "road",
            "present_person_ids": ["wei", "enemy"],
            "visible_person_ids": ["wei", "enemy"],
            "combat_parley": {"identity_policy": "opposing_person_ids_remain_hidden"},
            "gm_private_director_context": {
                "present_people": [
                    {
                        "person_ref": "enemy",
                        "character_truth": {
                            "person_id": "enemy", "name": "Hidden Name",
                            "faction_ref": "black_lance_company", "membership_grade": "senior",
                        },
                    }
                ]
            },
        },
    }
    built = build_gm_scene_context(context)
    row = next(item for item in built["present_people"] if item["person_ref"] == "enemy")

    assert row["visible_to_wei"] is True
    assert row["physically_visible_to_wei"] is True
    assert row["identity_known_to_wei"] is False
    assert "name" not in row
    assert row["gm_private_direction"]["character_truth"]["name"] == "Hidden Name"
    assert "sensory presence, not recognition" in built["writer_contract"]["person_visibility_semantics"]


def _basic_person(ref: str, *, discipline: str = "sword", skill: int = 80) -> dict:
    return {
        "person_id": ref,
        "faction_ref": "house_tang",
        "attributes": {
            "strength": 80, "speed": 80, "dexterity": 80, "endurance": 80,
            "perception": 80, "intelligence": 80, "willpower": 80,
        },
        "martial_skills": {
            "sword": skill if discipline == "sword" else 0,
            "spear": skill if discipline == "spear" else 0,
            "bow": skill if discipline == "bow" else 0,
            "hidden_weapons": skill if discipline == "hidden_weapons" else 0,
            "unarmed": skill if discipline == "unarmed" else 0,
        },
        "qi_control": 60,
        "health": {"status": "ready", "consciousness": 100, "injuries": []},
        "fatigue_milli": 0,
    }


def test_mixed_known_and_unknown_upper_injuries_keep_coarse_manual_impairment() -> None:
    generic_wrist = {
        "zone": "wrist", "structure_ref": None, "side": None,
        "severity": 190, "tendon_damage": 190, "nerve_damage": 190,
        "function_loss_pct": 100, "pain": 180, "bleeding_ml_per_min": 10,
        "cut": 20, "pierce": 120, "blunt": 80, "penetration": 100,
        "fracture": 0, "organ_trauma": 0, "structure_damage": 0, "functional_effects": {},
    }
    minor_left = {
        "zone": "elbow", "structure_ref": None, "side": "left",
        "severity": 20, "tendon_damage": 10, "nerve_damage": 5,
        "function_loss_pct": 10, "pain": 20, "bleeding_ml_per_min": 0,
        "cut": 0, "pierce": 10, "blunt": 10, "penetration": 0,
        "fracture": 0, "organ_trauma": 0, "structure_damage": 0, "functional_effects": {},
    }
    generic_only = functional_capacity_factors([generic_wrist])
    mixed = functional_capacity_factors([generic_wrist, minor_left])
    assert generic_only["manual_milli"] < 300
    assert mixed["manual_milli"] <= generic_only["manual_milli"] + 100


def test_projectile_profiles_consume_authored_dimensions() -> None:
    actor = _basic_person("thrower", discipline="hidden_weapons", skill=100)
    needle, _ = exact._action_profile(
        "hidden_weapon_throw", actor, "weapon_needle",
        {"x_mm": 3000, "y_mm": 0, "elevation_mm": 0},
        {"x_mm": 0, "y_mm": 0, "elevation_mm": 0},
    )
    assert needle.effect_parameters["projectile_width_mm"] == 2
    assert needle.effect_parameters["projectile_length_mm"] == 65

    archer = _basic_person("archer", discipline="bow", skill=100)
    bow, _ = exact._action_profile(
        "bow_shot", archer, "weapon_bow",
        {"x_mm": 10000, "y_mm": 0, "elevation_mm": 0},
        {"x_mm": 0, "y_mm": 0, "elevation_mm": 0},
    )
    assert bow.effect_parameters["projectile_width_mm"] == 9
    assert bow.effect_parameters["projectile_length_mm"] == 750


def test_one_handed_hidden_weapon_uses_draw_time_without_stowing_primary() -> None:
    actor = _basic_person("wei", discipline="hidden_weapons", skill=100)
    delay = exact._weapon_ready_delay_ms(actor, "weapon_jian", "weapon_needle", {})
    assert 0 < delay < 1000
    assert exact._hidden_offhand_draw_allowed(actor, "weapon_jian", "weapon_needle") is True


def test_melee_endcap_uses_body_volume_not_center_distance_cutoff() -> None:
    profile = ActionProfile(
        method_ref="thrust", effect_kind="physical", delivery="direct", startup_ms=330,
        external_contact=True, speed_score=80,
        effect_parameters={
            "physical_reach_m": 2.5, "intended_target_ref": "defender",
            "geometry": {"shape": "direct", "width_m": 0.04, "length_m": 2.5},
            "committed_melee_trajectory": {
                "launch_x_mm": 0, "launch_y_mm": 0, "launch_elevation_mm": 0,
                "aim_x_mm": 2500, "aim_y_mm": 0, "aim_elevation_mm": 0,
            },
        },
    )
    positions = {
        "attacker": {"zone_ref": "test", "x_mm": 0, "y_mm": 0, "elevation_mm": 0, "body_radius_mm": 300},
        "defender": {"zone_ref": "test", "x_mm": 2600, "y_mm": 0, "elevation_mm": 0, "body_radius_mm": 300},
    }
    result = contact_after_defense(
        attacker_ref="attacker", defender_ref="defender", positions=positions, profile=profile,
        obstacles=(), trajectory=None, tracking_milli=0,
        original_defender_position=PositionState(zone_ref="test", x_mm=2600, y_mm=0),
        body_refs=("defender",),
    )
    assert result["distance_mm"] > result["reach_mm"]
    assert result["contact"] is True
    surface_range = exact._contact_surface_range_m(
        result, contacted_ref="defender", fallback_center_range_m=2.6
    )
    assert surface_range <= 2.5
    attacker = _basic_person("spear", discipline="spear", skill=90)
    defender = _basic_person("target", discipline="sword", skill=60)
    damage = exact._contact_damage(
        actor=attacker, defender=defender, weapon=exact._weapon("weapon_spear"),
        weapon_ref="weapon_spear", action_kind="thrust", range_m=surface_range,
        defense_force_milli=1000, hit_zone="chest", target_structure_ref=None,
        created_at="test", precision_margin=0,
    )
    assert damage["outcome"] == "contact"
    assert damage["wound"] is not None


def test_dead_combatant_cannot_select_active_physical_defense() -> None:
    attacker_pos = PositionState(zone_ref="test", x_mm=0, y_mm=0)
    defender_pos = PositionState(zone_ref="test", x_mm=1000, y_mm=0)
    attacker_cap = _cap(100)
    defender_cap = _cap(180)
    attacker = _participant("attacker", side="a", position=attacker_pos, capability=attacker_cap, observed=("defender",))
    defender = _participant("defender", side="b", position=defender_pos, capability=defender_cap, observed=("attacker",))
    defender = Participant(**{**defender.__dict__, "status_families": ("dead",)})
    decision = select_physical_defense(
        attacker=attacker, defender=defender,
        attacker_position=attacker_pos, defender_position=defender_pos,
        attacker_capability=attacker_cap, defender_capability=defender_cap,
        profile=_incoming_thrust(), line_of_sight=True,
        participant_positions={"attacker": attacker_pos.to_record(), "defender": defender_pos.to_record()},
        body_refs=("attacker", "defender"), obstacles=(), at_ms=100,
    )
    assert decision.response == "none"
    assert decision.detected is False


def test_completed_legacy_velocity_does_not_become_permanent_momentum() -> None:
    row = {"x_mm": 0, "y_mm": 0, "vx_mmps": 3000, "vy_mmps": -1200, "stance": "ready"}
    exact._expire_stale_motion(row, at_ms=5000)
    assert row["vx_mmps"] == row["vy_mmps"] == 0

    live = {"x_mm": 0, "y_mm": 0, "vx_mmps": 3000, "vy_mmps": 0, "stance": "ready", "motion_expires_at_ms": 5100}
    exact._expire_stale_motion(live, at_ms=5000)
    assert live["vx_mmps"] == 3000


def test_world_combat_index_is_not_sword_biased() -> None:
    from shinobi_runtime.martial_world.escort_living_world import person_combat_index
    sword = _basic_person("sword", discipline="sword", skill=100)
    spear = _basic_person("spear", discipline="spear", skill=100)
    unarmed = _basic_person("unarmed", discipline="unarmed", skill=100)
    assert person_combat_index(sword) == person_combat_index(spear) == person_combat_index(unarmed)


def test_hidden_identity_fails_closed_when_private_person_packet_is_truncated() -> None:
    context = {
        "campaign": {"player_id": "wei", "world_time": "SE-test"},
        "player": {"person_id": "wei", "faction_ref": "house_tang", "current_location_id": "road"},
        "scene": {
            "location_id": "road",
            "present_person_ids": ["wei", "enemy"],
            "visible_person_ids": ["wei", "enemy"],
            "combat_present_person_ids": ["wei"],
            "combat_parley": {"identity_policy": "opposing_person_ids_remain_hidden"},
            "gm_private_director_context": {"present_people": []},
        },
    }
    built = build_gm_scene_context(context)
    enemy = next(item for item in built["present_people"] if item["person_ref"] == "enemy")
    assert enemy["physically_visible_to_wei"] is True
    assert enemy["identity_known_to_wei"] is False
    assert "name" not in enemy


def test_adaptive_projection_merge_keeps_beginning_middle_and_end() -> None:
    from shinobi_runtime.commands.combat_adaptation import _merge_projections
    projections = [
        {"beats": [{"at_ms": i, "kind": "action", "result": "defended_or_missed"}], "narration_rules": []}
        for i in range(40)
    ]
    merged = _merge_projections(projections, exchanges=40, stop_reason="scope_complete")
    times = [int(row["at_ms"]) for row in merged["beats"]]
    assert times[0] == 0
    assert times[-1] == 39
    assert any(15 <= t <= 25 for t in times)
    assert merged["beats_truncated"] is True


def test_transition_reentry_marks_defenses_withdrawal_and_maneuvers_material() -> None:
    for result in ("defended_or_missed", "projectile_intercepted_clean", "counter_intercepted", "withdrawal_declared", "maneuver_completed"):
        beat = transition_operations._combat_narrative_beat({
            "actor_ref": "wei", "intended_ref": "enemy", "result": result,
            "action_kind": "maneuver" if result == "maneuver_completed" else "thrust",
            "contact_at_ms": 100,
        })
        assert beat is not None, result


def test_player_visible_identity_card_excludes_nonplayer_internal_stats() -> None:
    from shinobi_runtime.api.operations import _player_visible_identity_card
    sheet = {
        "person_id": "enemy", "name": "Known Enemy", "sex": "male", "birth_year": 40,
        "faction_ref": "black_lance_company", "membership_grade": "senior", "standing_offices": ["captain"],
        "attributes": {"strength": 99}, "martial_skills": {"spear": 99}, "qi": 120, "qi_control": 90,
        "personal_cash": 9999, "health": {"injuries": [{"zone": "heart"}]},
    }
    card = _player_visible_identity_card(
        sheet, person_id="enemy", knows_identity=True, knows_faction=True, knows_office=True, visible=True,
        exact_presence={"location_ref": "road", "presence_kind": "combat", "owner_ref": "fight", "available_for_site_activity": False},
        appearance={"build": "lean", "visible_health_marks": ["limping"]},
    )
    assert card["name"] == "Known Enemy"
    assert card["faction_ref"] == "black_lance_company"
    for secret in ("attributes", "martial_skills", "qi", "qi_control", "personal_cash", "health"):
        assert secret not in card


def test_projectile_schedule_uses_first_body_surface_not_intended_target_center() -> None:
    people = {
        "wei": _basic_person("wei", discipline="hidden_weapons", skill=100),
        "ally": _basic_person("ally", discipline="sword", skill=80),
        "enemy": {**_basic_person("enemy", discipline="sword", skill=80), "faction_ref": "enemy"},
    }
    ledger = {
        "schema": "jianghu-equipment-ledger-1.0", "policy_assignments": {},
        "person_loadouts": {
            "wei": {"items": {"weapon_needle": 3}}, "ally": {"items": {}}, "enemy": {"items": {}},
        },
    }
    combat = exact.initialize_combat(
        combat_ref="flight-first-body", side_a_refs=["wei", "ally"], side_b_refs=["enemy"],
        people=people, zone_ref="road", started_at="x", objective={"kind": "eliminate", "target_refs": ["enemy"]},
        equipment_ledger=ledger,
    )
    combat["positions"]["wei"].update(x_mm=0, y_mm=0)
    combat["positions"]["ally"].update(x_mm=2000, y_mm=0)
    combat["positions"]["enemy"].update(x_mm=5000, y_mm=0)
    action = exact._schedule_action(
        combat=combat, actor_ref="wei", target_ref="enemy", action_kind="hidden_weapon_throw",
        weapon_ref="weapon_needle", poison_ref=None, hit_zone="chest", target_structure_ref=None,
        decision_origin="player", people=people, equipment_ledger=ledger,
    )
    # 2 m center distance with a 300 mm body radius means the first surface is
    # about 1.7 m away, far sooner than the intended target 5 m downrange.
    assert action.contact_at_ms - action.release_at_ms < 150


def test_spear_guard_participant_uses_spear_control_not_default_sword() -> None:
    person = _basic_person("guard", discipline="spear", skill=100)
    ledger = {
        "schema": "jianghu-equipment-ledger-1.0", "policy_assignments": {},
        "person_loadouts": {"guard": {"items": {"weapon_spear": 1}}},
    }
    state = {"ready_weapon_ref": "weapon_spear", "defense_state": {"load_milli": 0}, "status_families": []}
    profile = exact._guard_profile("guard", person, ledger, state)
    participant = exact._participant(
        "guard", person, side_ref="a", position={"zone_ref": "road", "x_mm": 0, "y_mm": 0},
        known_refs=(), combatant_state=state, action_profile=profile, equipment_ledger=ledger,
    )
    spear_control = exact.capability_from_person(person, action_skill="spear").control
    sword_control = exact.capability_from_person(person, action_skill="sword").control
    assert participant.capability.control == spear_control
    assert spear_control > sword_control


def test_generic_spear_thrust_uses_spear_discipline_for_action_timing() -> None:
    person = _basic_person("spearman", discipline="spear", skill=100)
    profile, weapon = exact._action_profile(
        "thrust", person, "weapon_spear",
        {"x_mm": 1800, "y_mm": 0, "elevation_mm": 0},
        {"x_mm": 0, "y_mm": 0, "elevation_mm": 0},
    )
    assert weapon["discipline"] == "spear"
    assert profile.effect_parameters["action_discipline"] == "spear"
    assert exact._action_rule("thrust", weapon)["skill"] == "spear"


def test_projectile_impact_speed_decays_with_range() -> None:
    from shinobi_runtime.martial_world.equipment import projectile_impact_speed_mps
    needle = exact._weapon("weapon_needle")
    near = projectile_impact_speed_mps(
        needle, launch_speed_mps=22.0, distance_m=1.0, maximum_range_m=12.0,
    )
    far = projectile_impact_speed_mps(
        needle, launch_speed_mps=22.0, distance_m=12.0, maximum_range_m=12.0,
    )
    assert 0 < far < near < 22.0
    assert round(far / 22.0, 2) == 0.52


def test_fallen_body_keeps_ground_footprint_for_movement() -> None:
    from shinobi_runtime.combat.geometry import path_clear
    combat = {
        "positions": {
            "fallen": {"zone_ref": "road", "x_mm": 1000, "y_mm": 350, "body_radius_mm": 300, "stance": "ready"},
        }
    }
    exact._apply_fallen_body_pose(combat, "fallen")
    fallen = combat["positions"]["fallen"]
    assert fallen["body_radius_mm"] <= 140
    assert fallen["movement_body_radius_mm"] >= 300
    positions = {
        "walker": {"zone_ref": "road", "x_mm": 0, "y_mm": 0, "body_radius_mm": 300},
        "fallen": fallen,
    }
    assert path_clear(positions, actor_ref="walker", end_x_mm=2000, end_y_mm=0, body_refs=("walker", "fallen")) is False


def test_hidden_identity_preserves_explicit_recognition_without_truncation_leak() -> None:
    context = {
        "campaign": {"player_id": "wei", "world_time": "SE-test"},
        "player": {"person_id": "wei", "faction_ref": "house_tang", "current_location_id": "road"},
        "scene": {
            "location_id": "road",
            "present_person_ids": ["wei", "enemy"],
            "visible_person_ids": ["wei", "enemy"],
            "identity_known_person_ids": ["wei", "enemy"],
            "combat_present_person_ids": ["wei"],
            "combat_parley": {"identity_policy": "opposing_person_ids_remain_hidden"},
            "gm_private_director_context": {
                "present_people": [{"person_ref": "enemy", "character_truth": {"name": "Recognized Foe", "faction_ref": "enemy"}}]
            },
        },
    }
    built = build_gm_scene_context(context)
    enemy = next(item for item in built["present_people"] if item["person_ref"] == "enemy")
    assert enemy["identity_known_to_wei"] is True
    assert enemy["name"] == "Recognized Foe"


def test_projectile_schedule_commits_accuracy_before_flight_timing() -> None:
    thrower = _basic_person("thrower", discipline="hidden_weapons", skill=0)
    thrower["attributes"] = {key: 10 for key in thrower["attributes"]}
    target = {**_basic_person("target", discipline="sword", skill=100), "faction_ref": "enemy"}
    target["attributes"]["speed"] = 120
    people = {"thrower": thrower, "target": target}
    ledger = {
        "schema": "jianghu-equipment-ledger-1.0", "policy_assignments": {},
        "person_loadouts": {"thrower": {"items": {"weapon_needle": 2}}, "target": {"items": {}}},
    }
    combat = exact.initialize_combat(
        combat_ref="committed-projectile-line", side_a_refs=["thrower"], side_b_refs=["target"],
        people=people, zone_ref="road", started_at="x", objective={"kind": "eliminate", "target_refs": ["target"]},
        equipment_ledger=ledger,
    )
    combat["positions"]["thrower"].update(x_mm=0, y_mm=0)
    combat["positions"]["target"].update(x_mm=5000, y_mm=0, vx_mmps=4000, motion_expires_at_ms=10000)
    action = exact._schedule_action(
        combat=combat, actor_ref="thrower", target_ref="target", action_kind="hidden_weapon_throw",
        weapon_ref="weapon_needle", poison_ref=None, hit_zone="eyes", target_structure_ref=None,
        decision_origin="player", people=people, equipment_ledger=ledger,
    )
    params = action.profile.effect_parameters
    assert isinstance(params.get("committed_precision_margin"), int)
    assert int(params.get("projectile_aim_error_max_mm", 0)) > 0
    # The stored trajectory is the one the resolver will later trace.  Timing is
    # therefore tied to the same physical launch line rather than an ideal aim.
    assert action.trajectory["launch_x_mm"] == 0
    assert action.contact_at_ms >= action.release_at_ms


def test_critical_ally_narrative_uses_actual_incapacitation_clock() -> None:
    before = {
        "pc": {"health": {"status": "ready", "consciousness": 100, "shock": 0}},
        "ally": {"health": {"status": "ready", "consciousness": 100, "shock": 0}},
        "enemy": {"health": {"status": "ready", "consciousness": 100, "shock": 0}},
    }
    after = {
        "pc": before["pc"],
        "ally": {"health": {"status": "incapacitated", "consciousness": 0, "shock": 180}},
        "enemy": before["enemy"],
    }
    combat_before = {
        "elapsed_ms": 0, "sides": {"side_a": ["pc", "ally"], "side_b": ["enemy"]},
        "combatants": {"pc": {"observed_refs": ["enemy"]}, "ally": {}, "enemy": {}},
    }
    combat_after = {
        **combat_before, "elapsed_ms": 2000,
        "combatants": {"pc": {"observed_refs": ["enemy"]}, "ally": {"incapacitated_at_ms": 450}, "enemy": {}},
    }
    projection = exact._combat_narrative_projection(
        combat_before=combat_before, combat_after=combat_after,
        people_before=before, people_after=after, events=[], player_ref="pc",
        combat_information={"visible_hostiles_current": 1, "observed_combat_capable_remaining": 1},
    )
    beat = next(row for row in projection["beats"] if row.get("kind") == "critical_ally_casualty")
    assert beat["at_ms"] == 450


def test_offhand_hidden_throw_keeps_primary_guard_independent_of_block() -> None:
    thrower = _basic_person("thrower", discipline="hidden_weapons", skill=90)
    target = _basic_person("target", discipline="spear", skill=70)
    people = {"thrower": thrower, "target": target}
    ledger = {
        "schema": "jianghu-equipment-ledger-1.0",
        "policy_assignments": {},
        "person_loadouts": {
            "thrower": {"items": {"weapon_jian": 1, "weapon_needle": 3}},
            "target": {"items": {"weapon_spear": 1}},
        },
    }
    combat = exact.initialize_combat(
        combat_ref="test.offhand.guard", side_a_refs=("thrower",), side_b_refs=("target",),
        people=people, zone_ref="z", started_at="x",
        objective={"kind": "eliminate", "target_refs": ["target"]},
        initial_range_band=1, equipment_ledger=ledger,
        initial_ready_weapons={"thrower": "weapon_jian", "target": "weapon_spear"},
    )
    action = exact._schedule_action(
        combat=combat, actor_ref="thrower", target_ref="target",
        action_kind="hidden_weapon_throw", weapon_ref="weapon_needle", poison_ref=None,
        hit_zone="chest", target_structure_ref=None, decision_origin="player",
        people=people, equipment_ledger=ledger,
    )
    assert action.profile.effect_parameters["offhand_hidden"] is True
    pending = exact._pending_action_record(action)
    assert pending["offhand_hidden"] is True
    combat["_pending_actions"] = {"thrower": pending}
    exact._record_defensive_interruption(
        combat, defender_ref="thrower", attacker_ref="target", response="block",
        response_start_ms=action.start_at_ms + 10, response_contact_ms=action.start_at_ms + 50,
    )
    assert "thrower" not in combat.get("_defense_interruptions", {})
    exact._record_defensive_interruption(
        combat, defender_ref="thrower", attacker_ref="target", response="evade",
        response_start_ms=action.start_at_ms + 10, response_contact_ms=action.start_at_ms + 50,
    )
    assert combat["_defense_interruptions"]["thrower"]["response"] == "evade"


def test_offhand_hidden_throw_uses_separate_recovery_clock_from_primary_guard() -> None:
    thrower = _basic_person("thrower", discipline="hidden_weapons", skill=90)
    target = _basic_person("target", discipline="spear", skill=70)
    people = {"thrower": thrower, "target": target}
    ledger = {
        "schema": "jianghu-equipment-ledger-1.0",
        "policy_assignments": {},
        "person_loadouts": {
            "thrower": {"items": {"weapon_jian": 1, "weapon_needle": 3}},
            "target": {"items": {"weapon_spear": 1}},
        },
    }
    combat = exact.initialize_combat(
        combat_ref="test.offhand.recovery", side_a_refs=("thrower",), side_b_refs=("target",),
        people=people, zone_ref="z", started_at="x",
        objective={"kind": "eliminate", "target_refs": ["target"]},
        initial_range_band=1, equipment_ledger=ledger,
        initial_ready_weapons={"thrower": "weapon_jian", "target": "weapon_spear"},
    )
    state = combat["combatants"]["thrower"]
    state["recovery_until_ms"] = 5000
    state["offhand_recovery_until_ms"] = 0
    action = exact._schedule_action(
        combat=combat, actor_ref="thrower", target_ref="target",
        action_kind="hidden_weapon_throw", weapon_ref="weapon_needle", poison_ref=None,
        hit_zone="chest", target_structure_ref=None, decision_origin="player",
        people=people, equipment_ledger=ledger,
    )
    assert action.profile.effect_parameters["offhand_hidden"] is True
    assert action.start_at_ms < 5000

    global_before = state["recovery_until_ms"]
    exact._resolve_scheduled_action(combat=combat, action=action, people=people, equipment_ledger=ledger)
    assert state["recovery_until_ms"] == global_before
    assert state["offhand_recovery_until_ms"] >= action.recovery_end_ms


def test_completed_defense_posture_is_cleared_before_next_pressure() -> None:
    state = {
        "defense_state": {"load_milli": 0, "last_at_ms": 100, "recent_attackers": {}},
        "recovery_until_ms": 600,
        "weapon_position": "extended_parry",
        "limb_commitment_milli": 620,
        "balance_milli": 820,
    }
    exact._settle_completed_posture_recovery(state, at_ms=1000)
    assert state["weapon_position"] == "guard"
    assert state["limb_commitment_milli"] == 0
    assert state["balance_milli"] == 1000
    pressure = exact._decay_defense_state(
        state, attacker_ref="attacker", at_ms=1000, reaction_score=100, angle_deg=0,
    )
    assert pressure["penalties"]["balance"] == 0
    assert pressure["penalties"]["limb_commitment"] == 0


def test_defense_interruption_cannot_retroactively_cancel_later_declaration() -> None:
    thrower = _basic_person("thrower", discipline="hidden_weapons", skill=90)
    target = _basic_person("target", discipline="spear", skill=70)
    people = {"thrower": thrower, "target": target}
    ledger = {
        "schema": "jianghu-equipment-ledger-1.0", "policy_assignments": {},
        "person_loadouts": {
            "thrower": {"items": {"weapon_jian": 1, "weapon_needle": 3}},
            "target": {"items": {"weapon_spear": 1}},
        },
    }
    combat = exact.initialize_combat(
        combat_ref="test.stale.interruption", side_a_refs=("thrower",), side_b_refs=("target",),
        people=people, zone_ref="z", started_at="x",
        objective={"kind": "eliminate", "target_refs": ["target"]},
        initial_range_band=1, equipment_ledger=ledger,
        initial_ready_weapons={"thrower": "weapon_jian", "target": "weapon_spear"},
    )
    combat["elapsed_ms"] = 2000
    action = exact._schedule_action(
        combat=combat, actor_ref="thrower", target_ref="target",
        action_kind="hidden_weapon_throw", weapon_ref="weapon_needle", poison_ref=None,
        hit_zone="chest", target_structure_ref=None, decision_origin="player",
        people=people, equipment_ledger=ledger,
    )
    combat["_defense_interruptions"] = {
        "thrower": {"response": "reposition", "attacker_ref": "target", "started_at_ms": 1500, "contact_at_ms": 1600}
    }
    assert exact._defensive_action_interruption(combat, action) is None
