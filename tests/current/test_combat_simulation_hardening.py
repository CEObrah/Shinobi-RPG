from __future__ import annotations

from typing import Any, Mapping

from shinobi_runtime.api.combat_hardening import (
    apply_transition_handoff,
    fatigue_aware_withdrawal,
    install_combat_contract_hints,
    legacy_safe_functional_penalties,
    preserve_player_support_task_provenance,
    stagnation_checkpoint_span,
    transition_handoff_from_result,
)
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.martial_world.health import functional_penalties


def test_combat_contract_advertises_exact_ally_order_shape():
    install_combat_contract_hints()
    descriptor = COMMAND_SPECS["jianghu_combat_resolution"].public_descriptor()
    ally_orders = descriptor["variants"]["exchange"]["payload"]["ally_orders"]

    assert ally_orders == [
        {
            "actor_ref": "<player-commanded same-side retinue person id>",
            "task": "<reach|protect|extract|treat>",
            "target_ref": "<same-side combatant person id>",
        }
    ]


def test_legacy_unsided_knee_trauma_recovers_aggregate_function_loss():
    wound = {
        "zone": "knee",
        "structure_ref": None,
        "side": None,
        "cut": 14,
        "pierce": 200,
        "blunt": 158,
        "penetration": 198,
        "severity": 200,
        "bleeding_ml_per_min": 52,
        "fracture": 0,
        "tendon_damage": 132,
        "nerve_damage": 127,
        "organ_trauma": 0,
        "functional_effects": {},
        "function_loss_pct": 0,
        "pain": 200,
        "treated": False,
    }

    penalties = legacy_safe_functional_penalties(functional_penalties, [wound])

    assert max(penalties["leg"], penalties["footwork"]) > 0
    assert penalties["leg_left"] == 0
    assert penalties["leg_right"] == 0
    assert penalties["footwork_left"] == 0
    assert penalties["footwork_right"] == 0


def test_player_treatment_support_fallback_keeps_exact_issuer_provenance():
    def base(**_kwargs: Any) -> Mapping[str, Any]:
        return {
            "combat_after": {
                "combatants": {
                    "medic": {
                        "support_task": {
                            "task": "treat",
                            "target_ref": "casualty",
                            "status": "active",
                            "issued_by_ref": "player",
                            "issued_at_ms": 1000,
                        }
                    }
                }
            }
        }

    result = preserve_player_support_task_provenance(
        base,
        player_ref="wei",
        player_ally_orders=[
            {"actor_ref": "medic", "task": "treat", "target_ref": "casualty"},
        ],
    )

    assert result["combat_after"]["combatants"]["medic"]["support_task"]["issued_by_ref"] == "wei"


def test_support_provenance_does_not_rewrite_unrelated_or_nonplaceholder_tasks():
    original = {
        "combat_after": {
            "combatants": {
                "medic": {
                    "support_task": {
                        "task": "treat",
                        "target_ref": "someone_else",
                        "status": "active",
                        "issued_by_ref": "captain",
                        "issued_at_ms": 1000,
                    }
                }
            }
        }
    }

    def base(**_kwargs: Any) -> Mapping[str, Any]:
        return original

    result = preserve_player_support_task_provenance(
        base,
        player_ref="wei",
        player_ally_orders=[
            {"actor_ref": "medic", "task": "treat", "target_ref": "casualty"},
        ],
    )

    assert result is original
    assert result["combat_after"]["combatants"]["medic"]["support_task"]["issued_by_ref"] == "captain"


def _standing_result(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "scope_stop_reason": "execution_frontier",
        "continuation_required": True,
        "combat_after": {"status": "active"},
        "events": events,
        "narrative_projection": {
            "scope_stop_reason": "execution_frontier",
            "narration_rules": [],
        },
    }


def test_empty_standing_frontier_becomes_stagnation_checkpoint():
    def base(**_kwargs: Any) -> Mapping[str, Any]:
        return _standing_result([
            {"result": "defended_or_missed"},
            {"result": "action_interrupted_by_defense_before_commitment"},
            {"result": "action_rejected"},
        ])

    result = stagnation_checkpoint_span(
        base,
        until_resolution=True,
        exchange_count=None,
        duration_seconds=None,
    )

    assert result["scope_stop_reason"] == "stagnation_checkpoint"
    assert result["continuation_required"] is False
    assert result["narrative_projection"]["scope_stop_reason"] == "stagnation_checkpoint"


def test_material_wound_keeps_normal_standing_continuation():
    def base(**_kwargs: Any) -> Mapping[str, Any]:
        return _standing_result([
            {
                "result": "contact",
                "damage": {
                    "wound": {
                        "severity": 25,
                        "bleeding_ml_per_min": 0,
                        "function_loss_pct": 0,
                        "fracture": 0,
                        "tendon_damage": 0,
                        "nerve_damage": 0,
                        "organ_trauma": 0,
                    }
                },
            }
        ])

    result = stagnation_checkpoint_span(
        base,
        until_resolution=True,
        exchange_count=None,
        duration_seconds=None,
    )

    assert result["scope_stop_reason"] == "execution_frontier"
    assert result["continuation_required"] is True


def test_protected_current_transition_survives_fresh_context_projection():
    receipt_result = {
        "command_type": "jianghu_combat_resolution",
        "scope_stop_reason": "protected_player_decision",
        "continuation_required": False,
    }

    handoff = transition_handoff_from_result(receipt_result, committed_revision=64)
    context = apply_transition_handoff(
        {"campaign": {"revision": 64}},
        receipt_result,
        committed_revision=64,
    )

    assert handoff is not None
    assert handoff["protected_player_decision"] is True
    assert context["current_transition_handoff"] == handoff
    assert context["unresolved_decision"] == {
        "kind": "combat_transition_decision",
        "source": "current_committed_transition",
        "scope_stop_reason": "protected_player_decision",
        "committed_revision": 64,
    }


def test_stagnation_transition_is_also_a_player_decision_handoff():
    result = {
        "command_type": "jianghu_combat_resolution",
        "scope_stop_reason": "stagnation_checkpoint",
        "continuation_required": False,
    }
    handoff = transition_handoff_from_result(result, committed_revision=65)

    assert handoff is not None
    assert handoff["protected_player_decision"] is True


def _fatigue_combat() -> dict[str, Any]:
    return {
        "sides": {"side_a": ["ally"], "side_b": ["enemy"]},
        "combatants": {
            "ally": {"status_families": []},
            "enemy": {"status_families": []},
        },
        "positions": {
            "ally": {"x_mm": 0, "y_mm": 0, "radius_mm": 300, "zone_ref": "road"},
            "enemy": {"x_mm": 3000, "y_mm": 0, "radius_mm": 300, "zone_ref": "road"},
        },
        "obstacles": [],
    }


def test_critical_fatigue_can_trigger_physical_npc_withdrawal():
    def base(**_kwargs: Any):
        return None

    people = {
        "ally": {
            "fatigue_milli": 9000,
            "health": {"status": "ready", "consciousness": 100, "shock": 0, "blood_lost_ml": 0},
        },
        "enemy": {
            "fatigue_milli": 0,
            "health": {"status": "ready", "consciousness": 100, "shock": 0, "blood_lost_ml": 0},
        },
    }

    decision = fatigue_aware_withdrawal(
        base,
        combat=_fatigue_combat(),
        actor_ref="ally",
        people=people,
        faction_doctrine={"casualty_preservation": 20, "withdrawal_discipline": 80},
    )

    assert decision is not None
    assert decision["reason"] == "critical_condition"
    assert decision["condition"]["fatigue_milli"] == 9000


def test_moderate_fatigue_does_not_force_withdrawal():
    def base(**_kwargs: Any):
        return None

    people = {
        "ally": {
            "fatigue_milli": 4000,
            "health": {"status": "ready", "consciousness": 100, "shock": 0, "blood_lost_ml": 0},
        },
        "enemy": {
            "fatigue_milli": 0,
            "health": {"status": "ready", "consciousness": 100, "shock": 0, "blood_lost_ml": 0},
        },
    }

    decision = fatigue_aware_withdrawal(
        base,
        combat=_fatigue_combat(),
        actor_ref="ally",
        people=people,
        faction_doctrine={"casualty_preservation": 100, "withdrawal_discipline": 0},
    )

    assert decision is None
