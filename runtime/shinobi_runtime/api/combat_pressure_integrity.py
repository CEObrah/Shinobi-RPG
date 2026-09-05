"""Production integrity fixes for exact-combat pressure and offense continuity.

A defensive response and an offensive interruption are related but not identical.
The physical-defense layer already distinguishes a simple brace from responses that
meaningfully contest the attacker's or defender's weapon line. Treating every detected
response as a full cancellation of offense that is already underway creates an
artificial turn-lock under repeated incoming attacks, especially in spear-heavy group
combat.

This module keeps the existing causal timing clamp and all active-defense costs. A
low-disruption stationary brace preserves a pending attack only when that attack has
physically started before the brace response begins. A brace that starts first may still
pre-empt a future offensive startup. Evades, repositions, parries, deflections, blocks
and counter-intercepts retain the existing interruption path because each can plausibly
consume the body, weapon or movement needed for the queued offense.

The exact resolver's compact pending-action record historically began at commitment,
not startup. Brace continuity needs the earlier physical startup timestamp, so this
integrity layer also carries ``start_at_ms`` from the scheduled action into that bounded
transient record. The record remains exchange-local and is never campaign state.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping


_NON_INTERRUPTING_STARTED_RESPONSES = frozenset({"brace"})


def pending_action_record_with_start(
    base_recorder: Callable[[Any], Mapping[str, Any]], action: Any
) -> dict[str, Any]:
    """Extend the exact transient pending record with physical startup timing."""
    row = dict(base_recorder(action))
    row["start_at_ms"] = int(getattr(action, "start_at_ms"))
    return row


def _started_pending_offense(
    combat: Mapping[str, Any], *, defender_ref: str, response_start_ms: int
) -> bool:
    pending = combat.get("_pending_actions", {}) if isinstance(combat.get("_pending_actions"), Mapping) else {}
    row = pending.get(defender_ref) if isinstance(pending, Mapping) else None
    if not isinstance(row, Mapping):
        return False
    start_at = int(row.get("start_at_ms", 10**18))
    release_at = int(row.get("release_at_ms", -1))
    started = start_at <= int(response_start_ms)
    not_finished = release_at < 0 or int(response_start_ms) < release_at
    return bool(started and not_finished)


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
    """Record defenses unless a low-disruption brace meets started offense."""
    if (
        str(response) in _NON_INTERRUPTING_STARTED_RESPONSES
        and _started_pending_offense(
            combat,
            defender_ref=defender_ref,
            response_start_ms=int(response_start_ms),
        )
    ):
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
    base_pending_recorder = exact._pending_action_record
    base_defense_recorder = exact._record_defensive_interruption

    def pending_action_recorder(action: Any) -> dict[str, Any]:
        return pending_action_record_with_start(base_pending_recorder, action)

    def defensive_interruption_recorder(combat: dict[str, Any], **kwargs: Any) -> None:
        interruption_aware_defense_record(base_defense_recorder, combat=combat, **kwargs)

    exact._pending_action_record = pending_action_recorder
    exact._record_defensive_interruption = defensive_interruption_recorder
    exact._combat_pressure_integrity_installed = True


__all__ = [
    "install_combat_pressure_integrity",
    "interruption_aware_defense_record",
    "pending_action_record_with_start",
]
