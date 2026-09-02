from __future__ import annotations

import json

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.transition_envelope_safety import (
    install_production_transition_envelope_safety,
)
from shinobi_runtime.api.transition_operations import current_transition_projection
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.tx.receipts import IdempotencyReceipt


def _rich_event(sequence: int) -> dict:
    enemy_ref = f"enemy.{sequence % 4}"
    return {
        "sequence": sequence,
        "actor_ref": "pc_test" if sequence % 2 == 0 else enemy_ref,
        "intended_ref": enemy_ref,
        "actual_ref": enemy_ref,
        "action_kind": "thrust",
        "weapon_ref": "weapon_jian",
        "hit_zone": "chest",
        "target_structure_ref": "rib_cage",
        "result": "contact",
        "contact_at_ms": 1000 + sequence * 10,
        "approach": {
            "reason": "closing_distance",
            "moved": True,
            "distance_mm": 900,
            "remaining_mm": 0,
            "required_mm": 1150,
        },
        "defense": {
            "response": "late_guard",
            "detected": True,
            "reason": "reaction_window",
            "reaction_delay_ms": 120,
            "recovery_ms": 240,
        },
        "contact": {
            "channel": "blade",
            "zone": "chest",
            "structure_ref": "rib_cage",
            "contact_kind": "penetrating",
            "penetration": 31,
            "impact": 44,
        },
        "damage": {
            "wound": {
                "zone": "chest",
                "structure_ref": "rib_cage",
                "side": "left",
                "severity": 52,
                "bleeding_ml_per_min": 18,
                "fracture": 9,
                "tendon_damage": 11,
                "nerve_damage": 13,
                "organ_trauma": 27,
                "function_loss_pct": 34,
                "pain": 61,
            }
        },
        "resource_commit": {
            "ok": True,
            "projectile_ref": "weapon_needle",
            "poison_ref": "cardiotoxic",
            "poison_dose_consumed": sequence % 3 == 0,
        },
        "qi": {"current_qi_milli_spent": 7},
        "fatigue": {"added_milli": 5},
        "poison": {
            "poison_ref": "cardiotoxic",
            "burden_added": 3,
            "current_burden": 10 + sequence,
            "burden_after": 13 + sequence,
        },
        "physiology": {"status": "wounded"},
    }


def _receipt(event_count: int = 80) -> IdempotencyReceipt:
    command = CommandEnvelope(
        campaign_id="campaign.test",
        request_id="test.transition.rich-envelope",
        actor_id="pc_test",
        command_type="jianghu_combat_resolution",
        expected_revision=7,
        submitted_at="2026-09-02T00:00:00Z",
        payload={"action": "exchange", "combat_ref": "combat:test"},
        mode="gameplay",
    )
    return IdempotencyReceipt.for_command(
        command,
        transaction_id="tx:rich-envelope",
        committed_revision=8,
        committed_at="2026-09-02T00:00:01Z",
        result={
            "command_type": "jianghu_combat_resolution",
            "combat_ref": "combat:test",
            "combat_status": "active",
            "exchanges_resolved": 10,
            "scope_stop_reason": "scope_complete",
            "continuation_required": False,
            "events": [_rich_event(index) for index in range(event_count)],
        },
    )


def test_production_transition_safety_preserves_exact_page_and_bounds_optional_spine():
    install_production_transition_envelope_safety()
    projected = current_transition_projection(
        receipt=_receipt(),
        campaign_id="campaign.test",
        revision=8,
        object_ref="transition:current",
        event_offset=0,
        combat_opposing_person_refs=frozenset({f"enemy.{index}" for index in range(4)}),
    )["object"]

    assert projected["event_count"] == 80
    assert len(projected["events"]) == 16
    assert [row["sequence"] for row in projected["events"]] == list(range(16))
    assert projected["next_object_ref"] == "transition:current:16"

    narrative = projected["combat_narrative"]
    assert narrative["material_event_count"] == 80
    assert narrative["material_beats_truncated"] is True
    assert narrative["omitted_material_beat_count"] > 0
    assert len(narrative["material_beats"]) < 80

    encoded = json.dumps(projected, sort_keys=True)
    for index in range(4):
        assert f"enemy.{index}" not in encoded
    assert "opposing_combatant" in encoded

    # The same authoritative public envelope validator must accept the final
    # projection. The repair is local trimming, never a larger global limit.
    validate_bounded_json(projected, label="game object projection", allow_float=True)
