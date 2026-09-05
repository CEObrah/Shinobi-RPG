"""Production integrity for recovered exact-combat body and guard commitments.

Exact combat persists active-defense load, balance, weapon position and limb
commitment across bounded exchanges. That persistence is intentional while a
fighter is still inside the recovery window. Once the recovery clock has
expired, however, a completed attack/parry/block/brace must not leave the body
permanently represented as extended or committed.

This layer normalizes only stale post-action posture when the existing recovery
clock says the commitment has completed. It adds no new campaign state and does
not weaken active-defense load, distinct-attacker pressure, current pending
attacks, wounds, balance or movement constraints.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable


_RECOVERABLE_POST_ACTION_POSITIONS = frozenset(
    {
        "extended_attack",
        "extended_parry",
        "displaced_guard",
        "committed_guard",
        "guarded_brace",
    }
)


def recovered_defense_participant(defender: Any) -> Any:
    """Return the defender view after an already-finished recovery window.

    ``committed_attack`` is deliberately excluded. Exact combat uses that value
    for an attack that is currently in startup and therefore still owns the
    fighter's limb/weapon commitment even when an older recovery clock is zero.
    Projectile-release states are also excluded because recovering posture does
    not recreate a released weapon.
    """
    remaining = max(0, int(getattr(defender, "recovery_remaining_ms", 0)))
    if remaining > 0:
        return defender

    weapon_position = str(getattr(defender, "weapon_position", "guard"))
    limb_commitment = max(0, int(getattr(defender, "limb_commitment_milli", 0)))
    recovered_position = "guard" if weapon_position in _RECOVERABLE_POST_ACTION_POSITIONS else weapon_position
    recovered_commitment = 0 if weapon_position in _RECOVERABLE_POST_ACTION_POSITIONS else limb_commitment

    if recovered_position == weapon_position and recovered_commitment == limb_commitment:
        return defender
    return replace(
        defender,
        weapon_position=recovered_position,
        limb_commitment_milli=recovered_commitment,
    )


def install_combat_readiness_integrity() -> None:
    """Install a recovered-readiness view at the exact defense decision seam."""
    from shinobi_runtime.martial_world import exact_combat as exact

    if bool(getattr(exact, "_combat_readiness_integrity_installed", False)):
        return
    base_selector: Callable[..., Any] = exact.select_physical_defense

    def readiness_aware_selector(*args: Any, **kwargs: Any) -> Any:
        if "defender" in kwargs:
            kwargs["defender"] = recovered_defense_participant(kwargs["defender"])
            return base_selector(*args, **kwargs)
        # Exact combat currently calls the selector with keywords. Keep a
        # fail-safe positional path so a future internal refactor does not turn
        # the integrity layer into an argument-shape trap.
        if len(args) >= 2:
            mutable = list(args)
            mutable[1] = recovered_defense_participant(mutable[1])
            return base_selector(*mutable, **kwargs)
        return base_selector(*args, **kwargs)

    exact.select_physical_defense = readiness_aware_selector
    exact._combat_readiness_integrity_installed = True


__all__ = ["install_combat_readiness_integrity", "recovered_defense_participant"]
