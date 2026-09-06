from __future__ import annotations

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
from shinobi_runtime.combat.physical_defense import select_physical_defense


def _cap(*, reaction: int, perception: int, defense: int = 100, control: int = 100, mobility: int = 100) -> CapabilityProfile:
    return CapabilityProfile(
        offense=100,
        defense=defense,
        control=control,
        mobility=mobility,
        perception=perception,
        stealth=0,
        capture=50,
        escape=100,
        reaction=reaction,
    )


def _participant(
    ref: str,
    *,
    side: str,
    position: PositionState,
    capability: CapabilityProfile,
    observed_refs: tuple[str, ...],
    defense_load: int = 0,
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
        information=InformationState(observed_refs=observed_refs),
        intent=CombatIntent(action="attack"),
        initiative=100,
        readiness=100,
        morale=100,
        cohesion=100,
        action_profile=guard,
        reactive_defenses=(ReactiveDefense(defense_ref="weapon_spear", defense_kind="weapon_guard"),),
        active_defense_load_milli=defense_load,
        balance_milli=1000,
        limb_commitment_milli=0,
        recovery_remaining_ms=0,
        weapon_position="guard",
        physical_defense_preferences=("parry", "deflect", "block", "brace"),
    )


def _incoming_profile() -> ActionProfile:
    return ActionProfile(
        method_ref="thrust",
        effect_kind="physical",
        delivery="direct",
        startup_ms=330,
        external_contact=True,
        speed_score=100,
        effect_parameters={
            "physical_reach_m": 1.15,
            "geometry": {"shape": "direct", "width_m": 0.35, "length_m": 1.15},
        },
    )


def test_detected_attack_does_not_grant_active_defense_after_warning_window_expires() -> None:
    attacker_pos = PositionState(zone_ref="test", x_mm=0, y_mm=0, facing_mdeg=0)
    defender_pos = PositionState(zone_ref="test", x_mm=1000, y_mm=0, facing_mdeg=180000)
    attacker_cap = _cap(reaction=100, perception=100)
    defender_cap = _cap(reaction=40, perception=80, defense=120, control=120)
    attacker = _participant(
        "attacker", side="a", position=attacker_pos, capability=attacker_cap, observed_refs=("defender",)
    )
    defender = _participant(
        "defender", side="b", position=defender_pos, capability=defender_cap, observed_refs=("attacker",)
    )

    decision = select_physical_defense(
        attacker=attacker,
        defender=defender,
        attacker_position=attacker_pos,
        defender_position=defender_pos,
        attacker_capability=attacker_cap,
        defender_capability=defender_cap,
        profile=_incoming_profile(),
        line_of_sight=True,
        participant_positions={"attacker": attacker_pos.to_record(), "defender": defender_pos.to_record()},
        body_refs=("attacker", "defender"),
        obstacles=(),
        at_ms=330,
    )

    assert decision.detected is True
    assert decision.reaction_delay_ms >= 330
    assert decision.response == "none"
    assert decision.force_transmission_milli == 1000
    assert decision.reason == "reaction_window_elapsed_before_response"


def test_fast_prepared_defender_still_gets_a_lawful_active_response() -> None:
    attacker_pos = PositionState(zone_ref="test", x_mm=0, y_mm=0, facing_mdeg=0)
    defender_pos = PositionState(zone_ref="test", x_mm=1000, y_mm=0, facing_mdeg=180000)
    attacker_cap = _cap(reaction=100, perception=100)
    defender_cap = _cap(reaction=320, perception=320, defense=320, control=320)
    attacker = _participant(
        "attacker", side="a", position=attacker_pos, capability=attacker_cap, observed_refs=("defender",)
    )
    defender = _participant(
        "defender", side="b", position=defender_pos, capability=defender_cap, observed_refs=("attacker",)
    )

    decision = select_physical_defense(
        attacker=attacker,
        defender=defender,
        attacker_position=attacker_pos,
        defender_position=defender_pos,
        attacker_capability=attacker_cap,
        defender_capability=defender_cap,
        profile=_incoming_profile(),
        line_of_sight=True,
        participant_positions={"attacker": attacker_pos.to_record(), "defender": defender_pos.to_record()},
        body_refs=("attacker", "defender"),
        obstacles=(),
        at_ms=330,
    )

    assert decision.detected is True
    assert decision.reaction_delay_ms < 330
    assert decision.response in {"evade", "reposition", "parry", "deflect", "block", "brace", "counter_intercept"}
    assert decision.reason == "lawful_physical_response_selected"


def test_active_defense_pressure_can_exhaust_an_otherwise_lawful_reaction_window() -> None:
    attacker_pos = PositionState(zone_ref="test", x_mm=0, y_mm=0, facing_mdeg=0)
    defender_pos = PositionState(zone_ref="test", x_mm=1000, y_mm=0, facing_mdeg=180000)
    attacker_cap = _cap(reaction=100, perception=100)
    defender_cap = _cap(reaction=180, perception=180, defense=220, control=220)
    attacker = _participant(
        "attacker", side="a", position=attacker_pos, capability=attacker_cap, observed_refs=("defender",)
    )
    defender = _participant(
        "defender",
        side="b",
        position=defender_pos,
        capability=defender_cap,
        observed_refs=("attacker",),
        defense_load=700,
    )

    decision = select_physical_defense(
        attacker=attacker,
        defender=defender,
        attacker_position=attacker_pos,
        defender_position=defender_pos,
        attacker_capability=attacker_cap,
        defender_capability=defender_cap,
        profile=_incoming_profile(),
        line_of_sight=True,
        participant_positions={"attacker": attacker_pos.to_record(), "defender": defender_pos.to_record()},
        body_refs=("attacker", "defender"),
        obstacles=(),
        at_ms=330,
    )

    assert decision.detected is True
    assert decision.reaction_availability_milli == 300
    assert decision.reaction_delay_ms >= 330
    assert decision.response == "none"
    assert decision.reason == "reaction_window_elapsed_before_response"
