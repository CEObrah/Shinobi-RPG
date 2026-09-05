from __future__ import annotations

from dataclasses import dataclass

from shinobi_runtime.api.combat_readiness_integrity import recovered_defense_participant


@dataclass(frozen=True)
class DefenderView:
    recovery_remaining_ms: int
    weapon_position: str
    limb_commitment_milli: int


def test_expired_attack_recovery_returns_guard_and_releases_limb_commitment() -> None:
    defender = DefenderView(
        recovery_remaining_ms=0,
        weapon_position="extended_attack",
        limb_commitment_milli=620,
    )

    recovered = recovered_defense_participant(defender)

    assert recovered.weapon_position == "guard"
    assert recovered.limb_commitment_milli == 0
    assert defender.weapon_position == "extended_attack"
    assert defender.limb_commitment_milli == 620


def test_expired_parry_block_and_brace_positions_recover_to_guard() -> None:
    for position in ("extended_parry", "displaced_guard", "committed_guard", "guarded_brace"):
        recovered = recovered_defense_participant(
            DefenderView(
                recovery_remaining_ms=0,
                weapon_position=position,
                limb_commitment_milli=480,
            )
        )
        assert recovered.weapon_position == "guard"
        assert recovered.limb_commitment_milli == 0


def test_active_recovery_window_preserves_current_commitment() -> None:
    defender = DefenderView(
        recovery_remaining_ms=120,
        weapon_position="extended_parry",
        limb_commitment_milli=510,
    )

    assert recovered_defense_participant(defender) is defender


def test_current_pending_attack_commitment_is_not_erased() -> None:
    defender = DefenderView(
        recovery_remaining_ms=0,
        weapon_position="committed_attack",
        limb_commitment_milli=700,
    )

    assert recovered_defense_participant(defender) is defender


def test_released_projectile_state_does_not_recreate_guard_or_weapon() -> None:
    defender = DefenderView(
        recovery_remaining_ms=0,
        weapon_position="released_projectile",
        limb_commitment_milli=0,
    )

    assert recovered_defense_participant(defender) is defender
