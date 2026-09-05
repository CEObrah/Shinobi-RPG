"""Production integrity fixes for exact-combat pressure and offense continuity.

A defensive response and an offensive interruption are related but not identical.
The physical-defense layer already distinguishes a simple brace from responses that
meaningfully contest the attacker's or defender's weapon line. Treating every detected
response as a full cancellation of the defender's pending offense creates an artificial
turn-lock under repeated incoming attacks, especially in spear-heavy group combat.

This module keeps the existing causal timing clamp and all active-defense costs. It only
prevents a low-disruption stationary brace from erasing a pending attack. Evades,
repositions, parries, deflections, blocks and counter-intercepts retain the existing
interruption path because each can plausibly consume the body, weapon or movement needed
for the queued offense.
"""
from __future__ import annotations

from typing import Any, Callable


_NON_INTERRUPTING_RESPONSES = frozenset({"brace"})


def interruption_aware_defense_record(
    base_recorder: Callable[..., None],
    *,
    combat: dict[str, Any],
    defender_ref: str,
    attacker_ref: str,
    response: str,
    response_start_ms: int,
    response_contact_ms: int,
) -> None:
    """Record only defensive responses that can consume the pending offense."""
    if str(response) in _NON_INTERRUPTING_RESPONSES:
        return
    base_recorder(
        combat,
        defender_ref=defender_ref,
        attacker_ref=attacker_ref,
        response=response,
        response_start_ms=int(response_start_ms),
        response_contact_ms=int(response_contact_ms),
    )


def install_combat_pressure_integrity() -> None:
    """Install after the production defense-timing wrapper, once per process."""
    from shinobi_runtime.martial_world import exact_combat as exact

    if bool(getattr(exact, "_combat_pressure_integrity_installed", False)):
        return
    base_recorder = exact._record_defensive_interruption

    def defensive_interruption_recorder(combat: dict[str, Any], **kwargs: Any) -> None:
        interruption_aware_defense_record(base_recorder, combat=combat, **kwargs)

    exact._record_defensive_interruption = defensive_interruption_recorder
    exact._combat_pressure_integrity_installed = True


__all__ = ["install_combat_pressure_integrity", "interruption_aware_defense_record"]
