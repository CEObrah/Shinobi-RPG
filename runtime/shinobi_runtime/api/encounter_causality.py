"""Private encounter-causality projection and narrow legacy recovery.

This module never mutates campaign state. It reconstructs only causal decisions
that are provable from legacy authoritative owners under the historical decision
branch, then returns them as explicitly GM-private director context.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from shinobi_runtime.martial_world.regional_economy import current_cargo_market_value_cash


def legacy_contact_causality(
    contact: Mapping[str, Any],
    route_operations: Mapping[str, Any],
    *,
    read_json: Callable[[str], Any] | None,
) -> dict[str, Any] | None:
    """Recover only the provable zero-cargo/zero-principal outlaw branch."""
    if read_json is None:
        return None
    movement_ref = str(contact.get("movement_ref") or "")
    attacker_faction_ref = str(contact.get("attacker_faction_ref") or "")
    if not movement_ref or not attacker_faction_ref:
        return None
    movements = route_operations.get("movements", {}) if isinstance(route_operations, Mapping) else {}
    movement = movements.get(movement_ref) if isinstance(movements, Mapping) else None
    if not isinstance(movement, Mapping):
        return None
    if str(movement.get("combat_ref") or "") not in {"", str(contact.get("combat_ref") or "")} and contact.get("combat_ref"):
        return None
    if str(movement.get("contact_intent") or "hostile_interception") != "hostile_interception":
        return None
    protected_refs = [
        str(x) for x in movement.get("protected_person_refs", []) if isinstance(x, str) and x
    ] if isinstance(movement.get("protected_person_refs"), list) else []
    if protected_refs:
        return None
    try:
        cargo_value = current_cargo_market_value_cash(movement, read_json=read_json)
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        return None
    if cargo_value != 0:
        return None
    try:
        identities = read_json("game/data/martial-world/faction-identities.json")
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        return None
    identity_rows = identities.get("identities", {}) if isinstance(identities, Mapping) else {}
    identity = identity_rows.get(attacker_faction_ref) if isinstance(identity_rows, Mapping) else None
    if not isinstance(identity, Mapping) or str(identity.get("faction_type") or "") != "outlaw_faction":
        return None
    return {
        "attacker_intent": "hostile_interception",
        "motive_kind": "opportunistic_predation",
        "gm_private_decision_context": {
            "legacy_reconstruction": True,
            "reconstruction_rule": "historical_zero_cargo_zero_principal_outlaw_interception_branch",
            "cargo_value_cash": 0,
            "protected_principal_count": 0,
            "attacker_faction_type": "outlaw_faction",
        },
    }


def resolved_contact_causality(
    contact: Mapping[str, Any],
    route_operations: Mapping[str, Any],
    *,
    read_json: Callable[[str], Any] | None,
) -> tuple[dict[str, Any], str]:
    """Return current or provably reconstructed private contact causality."""
    causal = dict(contact)
    source = "private_runtime_causality"
    existing_intent = str(causal.get("attacker_intent") or "")
    if not causal.get("motive_kind") and existing_intent in {"", "hostile_interception"}:
        reconstructed = legacy_contact_causality(contact, route_operations, read_json=read_json)
        if reconstructed is not None:
            causal.update(reconstructed)
            source = "private_runtime_causality_legacy_reconstructed"
    return causal, source


__all__ = ["legacy_contact_causality", "resolved_contact_causality"]
