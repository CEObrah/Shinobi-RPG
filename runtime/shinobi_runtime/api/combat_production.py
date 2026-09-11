"""Canonical production handshake for exact combat.

Combat behavior is statically owned by the canonical command, exact-combat,
physical-defense, health, equipment, and team-tactics modules. Historical
incident modules are no longer installed at startup and no function identity is
mutated by import order. The startup handshake only validates that the closed
combat command surface still exposes the required canonical controls.
"""
from __future__ import annotations

from collections.abc import Mapping


PRODUCTION_COMBAT_OWNERS = (
    "commands.jianghu_extended",
    "martial_world.exact_combat",
    "combat.physical_defense",
    "combat.team_tactics",
    "martial_world.health",
    "martial_world.equipment",
)


def install_production_combat_runtime() -> tuple[str, ...]:
    """Validate and report static production combat owners without mutation."""
    from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec, CommandVariant

    spec = COMMAND_SPECS.get("jianghu_combat_resolution")
    if not isinstance(spec, CommandSpec) or not isinstance(spec.variants, Mapping):
        raise RuntimeError("jianghu combat command spec unavailable")
    exchange = spec.variants.get("exchange")
    if not isinstance(exchange, CommandVariant):
        raise RuntimeError("jianghu combat exchange variant unavailable")
    hints = dict(exchange.payload_hints or {})
    required = {
        "exchange_count", "duration_seconds", "until_resolution",
        "rally_allies", "ally_orders", "movement_intent",
    }
    if not required.issubset(hints):
        raise RuntimeError("jianghu combat command hints incomplete")
    return PRODUCTION_COMBAT_OWNERS


__all__ = ["PRODUCTION_COMBAT_OWNERS", "install_production_combat_runtime"]
