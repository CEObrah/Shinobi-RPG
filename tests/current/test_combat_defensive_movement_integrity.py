from __future__ import annotations

from math import hypot

from shinobi_runtime.combat.models import CapabilityProfile, PositionState
from shinobi_runtime.combat.physical_defense import _movement_response, movement_speed_mmps


def _capability(*, mobility: int = 100) -> CapabilityProfile:
    return CapabilityProfile(
        offense=100,
        defense=100,
        control=100,
        mobility=mobility,
        perception=100,
        stealth=100,
        capture=100,
        escape=100,
        reaction=100,
    )


def test_tiny_reaction_window_cannot_mint_minimum_dodge_distance() -> None:
    defender = PositionState(zone_ref="test", x_mm=0, y_mm=0, facing_mdeg=0)
    attacker = PositionState(zone_ref="test", x_mm=5000, y_mm=0, facing_mdeg=180_000)
    capability = _capability(mobility=100)

    moved = _movement_response(
        response="evade",
        attacker_ref="attacker",
        defender_ref="defender",
        participant_positions={
            "attacker": attacker.to_record(),
            "defender": defender.to_record(),
        },
        defender_position=defender,
        incoming_bearing_mdeg=0,
        defender_capability=capability,
        reaction_delay_ms=100,
        warning_ms=101,
        body_refs=(),
        obstacles=(),
    )

    assert moved is not None
    distance = hypot(moved.x_mm - defender.x_mm, moved.y_mm - defender.y_mm)
    physical_budget_mm = movement_speed_mmps(capability) * 1 // 1000
    assert distance <= physical_budget_mm
    assert distance < 350


def test_defensive_displacement_scales_with_available_time() -> None:
    defender = PositionState(zone_ref="test", x_mm=0, y_mm=0, facing_mdeg=0)
    attacker = PositionState(zone_ref="test", x_mm=5000, y_mm=0, facing_mdeg=180_000)
    capability = _capability(mobility=100)

    short = _movement_response(
        response="evade",
        attacker_ref="attacker",
        defender_ref="defender",
        participant_positions={"attacker": attacker.to_record(), "defender": defender.to_record()},
        defender_position=defender,
        incoming_bearing_mdeg=0,
        defender_capability=capability,
        reaction_delay_ms=100,
        warning_ms=110,
        body_refs=(),
        obstacles=(),
    )
    long = _movement_response(
        response="evade",
        attacker_ref="attacker",
        defender_ref="defender",
        participant_positions={"attacker": attacker.to_record(), "defender": defender.to_record()},
        defender_position=defender,
        incoming_bearing_mdeg=0,
        defender_capability=capability,
        reaction_delay_ms=100,
        warning_ms=200,
        body_refs=(),
        obstacles=(),
    )

    assert short is not None and long is not None
    short_distance = hypot(short.x_mm - defender.x_mm, short.y_mm - defender.y_mm)
    long_distance = hypot(long.x_mm - defender.x_mm, long.y_mm - defender.y_mm)
    assert 0 < short_distance < long_distance
