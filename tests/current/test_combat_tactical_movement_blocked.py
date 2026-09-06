from __future__ import annotations

import pytest

from shinobi_runtime.api.combat_tactical_movement_integrity import (
    _MOVEMENT_CONTEXT,
    install_combat_tactical_movement_integrity,
)
from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.combat.models import ActionProfile, CapabilityProfile, PositionState


def _position(x: int, y: int) -> PositionState:
    return PositionState(zone_ref="test", x_mm=x, y_mm=y, facing_mdeg=0)


def _profile() -> ActionProfile:
    return ActionProfile(
        method_ref="test_jian",
        effect_kind="physical",
        delivery="direct",
        startup_ms=180,
        external_contact=True,
        speed_score=100,
        damage_channels=("cut",),
        effect_parameters={
            "physical_reach_m": 1.15,
            "approach_distance_mm": 2200,
            "approach_time_ms": 600,
            "tactical_movement_intent": "lateral",
        },
    )


def _capability() -> CapabilityProfile:
    return CapabilityProfile(
        offense=100,
        defense=100,
        control=100,
        mobility=100,
        perception=100,
        stealth=0,
        capture=50,
        escape=100,
        reaction=100,
    )


def test_blocked_lateral_intent_fails_closed_without_frontal_fallback(monkeypatch) -> None:
    from shinobi_runtime.api import combat_tactical_movement_integrity as movement
    from shinobi_runtime.martial_world import exact_combat as exact

    install_combat_tactical_movement_integrity()
    attacker = _position(0, 0)
    defender = _position(900, 0)
    positions = {
        "attacker": attacker.to_record(),
        "defender": defender.to_record(),
    }

    # This deliberately blocks every lateral candidate while the attacker is
    # already inside weapon reach.  The dangerous legacy fallback would be to
    # ignore the failed reposition and let the same frontal strike continue.
    monkeypatch.setattr(movement, "path_clear", lambda *args, **kwargs: False)
    token = _MOVEMENT_CONTEXT.set(
        {"actor_ref": "attacker", "movement_intent": "lateral"}
    )
    try:
        with pytest.raises(
            CommandRejectedError,
            match="jianghu_combat_tactical_movement_path_blocked",
        ):
            exact.close_attacker_into_reach(
                attacker_ref="attacker",
                defender_ref="defender",
                positions=positions,
                attacker_position=attacker,
                defender_position=defender,
                attacker_capability=_capability(),
                profile=_profile(),
                body_refs=("attacker", "defender"),
                obstacles=(),
            )
    finally:
        _MOVEMENT_CONTEXT.reset(token)

    assert positions["attacker"]["x_mm"] == 0
    assert positions["attacker"]["y_mm"] == 0
