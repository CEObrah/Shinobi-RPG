"""Bounded autonomous use of the authoritative exact personal combat resolver.

The simulation is used only when every participant is an autonomous NPC. Player
combat remains command-driven. No combat history is persisted by this helper;
callers keep current injuries/equipment and a compact result only.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from .exact_combat import default_action_for, initialize_combat, resolve_exchange


def simulate_exact_combat(
    *, combat_ref: str, side_a_refs: Sequence[str], side_b_refs: Sequence[str],
    people: Mapping[str, Mapping[str, Any]], equipment_ledger: Mapping[str, Any],
    doctrines: Mapping[str, Mapping[str, Any]], zone_ref: str, started_at: str,
    objective: Mapping[str, Any], targeting_intent: str = "lethal", max_exchanges: int = 160,
) -> dict[str, Any]:
    persons = {str(ref): copy.deepcopy(dict(p)) for ref, p in people.items()}
    ledger = copy.deepcopy(dict(equipment_ledger))
    combat = initialize_combat(
        combat_ref=combat_ref,
        side_a_refs=list(side_a_refs), side_b_refs=list(side_b_refs), people=persons,
        zone_ref=zone_ref, started_at=started_at, objective=objective,
        awareness_mode="mutual", initial_range_band=1, equipment_ledger=ledger,
    )
    exchanges = 0
    last_events: list[Mapping[str, Any]] = []
    while combat.get("status") == "active" and exchanges < max(1, int(max_exchanges)):
        active_a = [r for r in side_a_refs if r in persons and persons[r].get("health", {}).get("status") != "dead" and "incapacitated" not in combat.get("combatants", {}).get(r, {}).get("status_families", [])]
        active_b = [r for r in side_b_refs if r in persons and persons[r].get("health", {}).get("status") != "dead" and "incapacitated" not in combat.get("combatants", {}).get(r, {}).get("status_families", [])]
        if not active_a or not active_b:
            break
        driver = sorted(active_a)[0]
        target = min(
            active_b,
            key=lambda ref: (
                (int(combat["positions"][driver]["x_mm"]) - int(combat["positions"][ref]["x_mm"])) ** 2
                + (int(combat["positions"][driver]["y_mm"]) - int(combat["positions"][ref]["y_mm"])) ** 2,
                ref,
            ),
        )
        action_kind, weapon_ref = default_action_for(
            combat=combat, people=persons, equipment_ledger=ledger,
            actor_ref=driver, target_ref=target,
        )
        resolved = resolve_exchange(
            combat=combat, people=persons, equipment_ledger=ledger, doctrines=doctrines,
            player_ref=driver, player_action_kind=action_kind, player_target_ref=target,
            player_weapon_ref=weapon_ref, player_hit_zone="auto", player_target_structure_ref="auto",
            player_targeting_intent=targeting_intent, npc_targeting_intent=targeting_intent,
        )
        combat = resolved["combat_after"]
        persons = resolved["people_after"]
        ledger = resolved["equipment_ledger_after"]
        last_events = [e for e in resolved.get("events", []) if isinstance(e, Mapping)]
        exchanges += 1
    return {
        "resolved": combat.get("status") == "resolved",
        "winner_side": combat.get("winner_side"),
        "combat_elapsed_ms": max(0, int(combat.get("elapsed_ms", 0))),
        "exchanges": exchanges,
        "people_after": persons,
        "equipment_ledger_after": ledger,
        "last_events": last_events,
    }


__all__ = ["simulate_exact_combat"]
