"""Production hardening for exact-combat contracts, physiology, and liveness.

This module is deliberately policy-layer code. It does not rewrite campaign state,
change saved doctrine, or force combat outcomes. It repairs read/contract surfaces
and places bounded safety frontiers around deterministic exact combat so a valid
simulation cannot silently turn into an endless standing-intent loop.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Mapping, Sequence

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.transition_operations import TransitionAwareCampaignOperations
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec, CommandVariant
from shinobi_runtime.tx.canonical import thaw_json


_DECISION_STOP_REASONS = frozenset({"protected_player_decision", "stagnation_checkpoint"})
_SEVERE_FATIGUE_MILLI = 6_000
_CRITICAL_FATIGUE_MILLI = 9_000
_MATERIAL_WOUND_SEVERITY = 20


def install_combat_contract_hints() -> None:
    """Expose the exact nested shape for compound ally combat orders.

    The validator already requires ``actor_ref``, ``task`` and ``target_ref``.
    Advertising only ``<value>`` forced the LLM caller to guess that closed
    object shape, causing valid player intent to fail before simulation.
    """

    spec = COMMAND_SPECS.get("jianghu_combat_resolution")
    if not isinstance(spec, CommandSpec) or not isinstance(spec.variants, Mapping):
        raise RuntimeError("jianghu combat command spec unavailable")
    exchange = spec.variants.get("exchange")
    if not isinstance(exchange, CommandVariant):
        raise RuntimeError("jianghu combat exchange variant unavailable")
    hints = dict(exchange.payload_hints or {})
    hints.update({
        "exchange_count": "<positive integer>",
        "duration_seconds": "<positive integer>",
        "until_resolution": "<true|false>",
        "rally_allies": "<true|false>",
        "ally_orders": [
            {
                "actor_ref": "<player-commanded same-side retinue person id>",
                "task": "<reach|protect|extract|treat>",
                "target_ref": "<same-side combatant person id>",
            }
        ],
    })
    variants = dict(spec.variants)
    variants["exchange"] = CommandVariant(
        exchange.required_fields,
        exchange.optional_fields,
        hints,
    )
    COMMAND_SPECS["jianghu_combat_resolution"] = CommandSpec(
        spec.required_fields,
        spec.optional_fields,
        spec.summary,
        spec.payload_hints,
        spec.availability,
        variants,
    )


def _coarse_trauma_function_loss(wound: Mapping[str, Any]) -> int:
    """Re-derive the coarse functional burden encoded by current wound trauma.

    Older committed injuries can predate the current ``function_loss_pct``
    derivation and therefore legitimately contain severe physical trauma plus a
    stale zero. This uses the same coarse formula as ``wound_from_contact`` and
    never invents a left/right side or a named structure.
    """

    cut = max(0, int(wound.get("cut", 0) or 0))
    pierce = max(0, int(wound.get("pierce", 0) or 0))
    blunt = max(0, int(wound.get("blunt", 0) or 0))
    penetration = max(0, int(wound.get("penetration", 0) or 0))
    fracture = max(0, int(wound.get("fracture", 0) or 0))
    tendon = max(0, int(wound.get("tendon_damage", 0) or 0))
    nerve = max(0, int(wound.get("nerve_damage", 0) or 0))
    tissue = cut + pierce + blunt // 2 + penetration
    return min(100, max(fracture, tendon, nerve) // 2 + min(50, tissue // 5))


def legacy_safe_functional_penalties(
    base: Callable[[Sequence[Mapping[str, Any]]], Mapping[str, int]],
    wounds: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    """Apply a read-time fallback for severe legacy wounds with stale zero loss."""

    normalized: list[Mapping[str, Any]] = []
    for raw in wounds:
        if not isinstance(raw, Mapping):
            continue
        row = copy.deepcopy(dict(raw))
        stored = max(0, min(100, int(row.get("function_loss_pct", 0) or 0)))
        row["function_loss_pct"] = max(stored, _coarse_trauma_function_loss(row))
        normalized.append(row)
    return {str(key): int(value) for key, value in base(normalized).items()}


def _event_is_material_progress(event: Mapping[str, Any]) -> bool:
    result = str(event.get("result") or "")
    if result in {
        "dead", "incapacitated", "escaped", "withdrew_from_combat",
        "withdrawal_in_progress", "support_treatment_completed",
        # Keep legacy names readable for any older transition evidence.
        "support_extract_moved", "support_reached_target",
    }:
        return True
    if str(event.get("action_kind") or "") == "ally_support":
        movement = event.get("movement")
        if isinstance(movement, Mapping):
            return True
        if result in {
            "support_reached", "support_approach", "support_protecting",
            "support_extraction_secured", "support_treatment_approach",
            "support_treatment_started", "support_treatment_in_progress",
        }:
            return True
    physiology = event.get("physiology") if isinstance(event.get("physiology"), Mapping) else {}
    if str(physiology.get("status") or "") in {"dead", "incapacitated", "unconscious"}:
        return True
    damage = event.get("damage") if isinstance(event.get("damage"), Mapping) else {}
    wound = damage.get("wound") if isinstance(damage.get("wound"), Mapping) else {}
    if wound:
        severity = max(0, int(wound.get("severity", 0) or 0))
        bleeding = max(0, int(wound.get("bleeding_ml_per_min", 0) or 0))
        function_loss = max(0, int(wound.get("function_loss_pct", 0) or 0))
        structural = max(
            max(0, int(wound.get("fracture", 0) or 0)),
            max(0, int(wound.get("tendon_damage", 0) or 0)),
            max(0, int(wound.get("nerve_damage", 0) or 0)),
            max(0, int(wound.get("organ_trauma", 0) or 0)),
        )
        if severity >= _MATERIAL_WOUND_SEVERITY or bleeding > 0 or function_loss > 0 or structural > 0:
            return True
    return False


def stagnation_checkpoint_span(
    base_resolver: Callable[..., Mapping[str, Any]], **kwargs: Any
) -> Mapping[str, Any]:
    """Turn a no-progress standing frontier into a genuine tactical handoff.

    Existing combat-span safety already bounds one ``until_resolution``
    transaction. Previously every empty execution frontier advertised automatic
    continuation, so the GM could legally chain many minutes of mutually denied
    attacks. We preserve the deterministic result but stop auto-continuation when
    the entire bounded span contains no material physical progress.
    """

    result = base_resolver(**kwargs)
    if not bool(kwargs.get("until_resolution")):
        return result
    if kwargs.get("exchange_count") is not None or kwargs.get("duration_seconds") is not None:
        return result
    if str(result.get("scope_stop_reason") or "") != "execution_frontier":
        return result
    combat_after = result.get("combat_after") if isinstance(result.get("combat_after"), Mapping) else {}
    if str(combat_after.get("status") or "") != "active":
        return result
    events = result.get("events", []) if isinstance(result.get("events"), list) else []
    if any(isinstance(event, Mapping) and _event_is_material_progress(event) for event in events):
        return result

    out = copy.deepcopy(dict(result))
    out["scope_stop_reason"] = "stagnation_checkpoint"
    out["continuation_required"] = False
    projection = out.get("narrative_projection")
    if isinstance(projection, dict):
        projection["scope_stop_reason"] = "stagnation_checkpoint"
        rules = projection.setdefault("narration_rules", [])
        if isinstance(rules, list):
            rule = (
                "No material physical progress occurred across the bounded standing-combat span; "
                "return control for a tactical decision instead of silently chaining another span."
            )
            if rule not in rules:
                rules.append(rule)
    return out


def preserve_player_support_task_provenance(
    base_resolver: Callable[..., Mapping[str, Any]], **kwargs: Any
) -> Mapping[str, Any]:
    """Keep player-authored persistent support tasks tied to the exact issuer.

    The core treatment step has a defensive fallback that can create a task when
    one is unexpectedly absent. Older code stamped that fallback with the literal
    string ``player``. Persistent treatment recovery compares ``issued_by_ref``
    to the exact player person ID, so that placeholder can orphan an otherwise
    valid medic objective at the next exchange. Normalize only support tasks that
    correspond to a concrete treatment order in this exact resolver call.
    """

    result = base_resolver(**kwargs)
    player_ref = str(kwargs.get("player_ref") or "")
    raw_orders = kwargs.get("player_ally_orders")
    if not player_ref or not isinstance(raw_orders, Sequence) or isinstance(raw_orders, (str, bytes, bytearray)):
        return result

    ordered: dict[str, str] = {}
    for raw in raw_orders:
        if not isinstance(raw, Mapping) or str(raw.get("task") or "") != "treat":
            continue
        actor_ref = str(raw.get("actor_ref") or "")
        target_ref = str(raw.get("target_ref") or "")
        if actor_ref and target_ref:
            ordered[actor_ref] = target_ref
    if not ordered:
        return result

    combat_after = result.get("combat_after") if isinstance(result, Mapping) else None
    states = combat_after.get("combatants") if isinstance(combat_after, Mapping) else None
    if not isinstance(states, Mapping):
        return result

    needs_fix = False
    for actor_ref, target_ref in ordered.items():
        state = states.get(actor_ref)
        support = state.get("support_task") if isinstance(state, Mapping) else None
        if not isinstance(support, Mapping):
            continue
        if str(support.get("task") or "") != "treat" or str(support.get("target_ref") or "") != target_ref:
            continue
        if str(support.get("issued_by_ref") or "") in {"", "player"}:
            needs_fix = True
            break
    if not needs_fix:
        return result

    out = copy.deepcopy(dict(result))
    out_combat = out.get("combat_after")
    out_states = out_combat.get("combatants") if isinstance(out_combat, Mapping) else None
    if not isinstance(out_states, dict):
        return result
    for actor_ref, target_ref in ordered.items():
        state = out_states.get(actor_ref)
        support = state.get("support_task") if isinstance(state, dict) else None
        if not isinstance(support, dict):
            continue
        if str(support.get("task") or "") != "treat" or str(support.get("target_ref") or "") != target_ref:
            continue
        if str(support.get("issued_by_ref") or "") in {"", "player"}:
            support["issued_by_ref"] = player_ref
    return out


def fatigue_aware_withdrawal(
    base: Callable[..., Mapping[str, Any] | None],
    *,
    combat: Mapping[str, Any],
    actor_ref: str,
    people: Mapping[str, Mapping[str, Any]],
    faction_doctrine: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Let extreme accumulated exertion produce lawful autonomous withdrawal.

    Exact combat already makes fatigue expensive and bottoms performance at a
    severe floor. Without a withdrawal bridge, however, NPCs can remain at that
    floor indefinitely. This adds no morale meter and no forced defeat: an
    exhausted NPC still needs an open physical retreat corridor, and severe but
    noncritical exhaustion remains rallyable.
    """

    existing = base(
        combat=combat,
        actor_ref=actor_ref,
        people=people,
        faction_doctrine=faction_doctrine,
    )
    if existing is not None:
        return existing

    from shinobi_runtime.martial_world import exact_combat as exact

    states = combat.get("combatants", {}) if isinstance(combat.get("combatants"), Mapping) else {}
    state = states.get(actor_ref)
    person = people.get(actor_ref)
    if not isinstance(state, Mapping) or not isinstance(person, Mapping) or not exact._active(person, state):
        return None
    fatigue = max(0, int(person.get("fatigue_milli", 0) or 0))
    doctrine = faction_doctrine if isinstance(faction_doctrine, Mapping) else {}
    preservation = max(0, min(100, int(doctrine.get("casualty_preservation", 55) or 55)))
    discipline = max(0, min(100, int(doctrine.get("withdrawal_discipline", 50) or 50)))
    critical = fatigue >= _CRITICAL_FATIGUE_MILLI
    severe = fatigue >= _SEVERE_FATIGUE_MILLI and preservation >= 60
    if not (critical or severe):
        return None

    body_refs = exact._present_body_refs(combat)
    corridors = list(exact.open_retreat_corridors(
        combat.get("positions", {}),
        actor_ref=actor_ref,
        body_refs=body_refs,
        obstacles=combat.get("obstacles", []),
    ))
    if not corridors:
        return None

    side = exact._side_of(combat, actor_ref)
    arrived: list[str] = []
    for ref in combat.get("sides", {}).get(side, []) if isinstance(combat.get("sides"), Mapping) else []:
        ref_state = states.get(ref)
        if not isinstance(ref, str) or not isinstance(ref_state, Mapping):
            continue
        statuses = {str(value) for value in ref_state.get("status_families", []) if isinstance(value, str)}
        if "reinforcing" not in statuses:
            arrived.append(ref)
    active_arrived = [
        ref for ref in arrived
        if ref in people and isinstance(states.get(ref), Mapping) and exact._active(people[ref], states[ref])
    ]
    return {
        "reason": "critical_condition" if critical else "exhaustion",
        "casualty_preservation": preservation,
        "withdrawal_discipline": discipline,
        "arrived_side_count": len(arrived),
        "active_arrived_count": len(active_arrived),
        "combat_loss_count": 0,
        "withdrawn_count": sum(
            1 for ref in arrived
            if "escaped" in {
                str(value) for value in states.get(ref, {}).get("status_families", [])
                if isinstance(value, str)
            }
        ),
        "loss_percent": 0,
        "collapse_threshold_percent": 100,
        "condition": {
            "consciousness": max(0, int((person.get("health") or {}).get("consciousness", 100)))
            if isinstance(person.get("health"), Mapping) else 100,
            "shock": max(0, int((person.get("health") or {}).get("shock", 0)))
            if isinstance(person.get("health"), Mapping) else 0,
            "blood_lost_ml": max(0, int((person.get("health") or {}).get("blood_lost_ml", 0)))
            if isinstance(person.get("health"), Mapping) else 0,
            "functional_floor_milli": 1000,
            "fatigue_milli": fatigue,
        },
    }


def transition_handoff_from_result(
    result: Mapping[str, Any] | None, *, committed_revision: int
) -> dict[str, Any] | None:
    """Project only safe current-revision decision metadata from a receipt result."""

    if not isinstance(result, Mapping):
        return None
    if str(result.get("command_type") or "") != "jianghu_combat_resolution":
        return None
    reason = str(result.get("scope_stop_reason") or "")
    if not reason:
        return None
    return {
        "source": "current_committed_transition",
        "committed_revision": max(0, int(committed_revision)),
        "scope_stop_reason": reason,
        "continuation_required": bool(result.get("continuation_required", False)),
        "protected_player_decision": reason in _DECISION_STOP_REASONS,
    }


def apply_transition_handoff(
    context: Mapping[str, Any], result: Mapping[str, Any] | None, *, committed_revision: int
) -> dict[str, Any]:
    """Add an ephemeral decision handoff without creating mutable campaign truth."""

    out = copy.deepcopy(dict(context))
    handoff = transition_handoff_from_result(result, committed_revision=committed_revision)
    if handoff is None:
        return out
    out["current_transition_handoff"] = handoff
    if handoff["protected_player_decision"] and not isinstance(out.get("unresolved_decision"), Mapping):
        out["unresolved_decision"] = {
            "kind": "combat_transition_decision",
            "source": "current_committed_transition",
            "scope_stop_reason": handoff["scope_stop_reason"],
            "committed_revision": handoff["committed_revision"],
        }
    return out


class CombatHardenedCampaignOperations(TransitionAwareCampaignOperations):
    """Production operations with current-revision combat decision continuity."""

    def play_context(self) -> Mapping[str, Any]:
        base = super().play_context()
        campaign = base.get("campaign") if isinstance(base.get("campaign"), Mapping) else {}
        campaign_id = str(campaign.get("campaign_id") or "")
        revision = int(campaign.get("revision", -1))
        if not campaign_id or revision < 0:
            return base
        receipt = self.coordinator.receipts.get_campaign_revision(campaign_id, revision)
        raw_result = thaw_json(receipt.result) if receipt is not None else None
        projected = apply_transition_handoff(
            base,
            raw_result if isinstance(raw_result, Mapping) else None,
            committed_revision=revision,
        )
        validate_bounded_json(projected, label="play context", allow_float=True)
        return projected


def install_combat_simulation_hardening() -> None:
    """Install production-only deterministic hardening once per process."""

    from shinobi_runtime.commands import jianghu_extended as extended
    from shinobi_runtime.commands.combat_span_safety import install_production_combat_span_safety
    from shinobi_runtime.martial_world import exact_combat as exact
    from shinobi_runtime.martial_world import health

    install_combat_contract_hints()
    install_production_combat_span_safety()

    if not bool(getattr(health, "_legacy_function_fallback_installed", False)):
        base_penalties = health.functional_penalties

        def functional_penalties(wounds: Sequence[Mapping[str, Any]]) -> dict[str, int]:
            return legacy_safe_functional_penalties(base_penalties, wounds)

        health.functional_penalties = functional_penalties
        exact.functional_penalties = functional_penalties
        health._legacy_function_fallback_installed = True

    if not bool(getattr(exact, "_support_task_provenance_hardening_installed", False)):
        base_exact_exchange = exact.resolve_exchange
        base_extended_exchange = extended.resolve_exchange

        def exact_exchange(**kwargs: Any) -> Mapping[str, Any]:
            return preserve_player_support_task_provenance(base_exact_exchange, **kwargs)

        exact.resolve_exchange = exact_exchange
        if base_extended_exchange is base_exact_exchange:
            extended.resolve_exchange = exact_exchange
        else:
            def extended_exchange(**kwargs: Any) -> Mapping[str, Any]:
                return preserve_player_support_task_provenance(base_extended_exchange, **kwargs)

            extended.resolve_exchange = extended_exchange
        exact._support_task_provenance_hardening_installed = True

    if not bool(getattr(exact, "_fatigue_withdrawal_hardening_installed", False)):
        base_withdrawal = exact._npc_withdrawal_decision

        def withdrawal_decision(**kwargs: Any) -> Mapping[str, Any] | None:
            return fatigue_aware_withdrawal(base_withdrawal, **kwargs)

        exact._npc_withdrawal_decision = withdrawal_decision
        exact._fatigue_withdrawal_hardening_installed = True

    if not bool(getattr(extended, "_stagnation_checkpoint_installed", False)):
        base_span = extended._resolve_player_combat_span

        def span_resolver(**kwargs: Any) -> Mapping[str, Any]:
            return stagnation_checkpoint_span(base_span, **kwargs)

        extended._resolve_player_combat_span = span_resolver
        extended._stagnation_checkpoint_installed = True


__all__ = [
    "CombatHardenedCampaignOperations",
    "apply_transition_handoff",
    "fatigue_aware_withdrawal",
    "install_combat_contract_hints",
    "install_combat_simulation_hardening",
    "legacy_safe_functional_penalties",
    "preserve_player_support_task_provenance",
    "stagnation_checkpoint_span",
    "transition_handoff_from_result",
]
