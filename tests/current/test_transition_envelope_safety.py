from __future__ import annotations

import json

from shinobi_runtime.api.models import validate_bounded_json
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
        # Real exact-combat receipt rows may carry substantial geometry/timing
        # structure. This deliberately makes a fixed 16-row page exceed the
        # public 2048-node game-object envelope while each individual row stays
        # comfortably valid and useful.
        "geometry_trace": {
            f"segment_{index}": {
                "x_mm": sequence * 100 + index,
                "y_mm": sequence * 200 + index,
                "distance_mm": 500 + index,
                "time_ms": 1000 + sequence * 10 + index,
            }
            for index in range(24)
        },
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


def _project(offset: int) -> dict:
    object_ref = "transition:current" if offset == 0 else f"transition:current:{offset}"
    return current_transition_projection(
        receipt=_receipt(),
        campaign_id="campaign.test",
        revision=8,
        object_ref=object_ref,
        event_offset=offset,
        combat_opposing_person_refs=frozenset({f"enemy.{index}" for index in range(4)}),
    )["object"]


def test_rich_noninitial_transition_page_adapts_to_public_envelope():
    projected = _project(16)

    assert projected["event_offset"] == 16
    assert 1 <= len(projected["events"]) < 16
    assert [row["sequence"] for row in projected["events"]] == list(
        range(16, 16 + len(projected["events"]))
    )
    assert projected["next_object_ref"] == (
        f"transition:current:{16 + len(projected['events'])}"
    )
    assert projected["combat_narrative"] is None

    encoded = json.dumps(projected, sort_keys=True)
    for index in range(4):
        assert f"enemy.{index}" not in encoded
    assert "opposing_combatant" in encoded
    validate_bounded_json(projected, label="game object projection", allow_float=True)


def test_adaptive_transition_pagination_preserves_complete_exact_order():
    receipt = _receipt()
    opposing = frozenset({f"enemy.{index}" for index in range(4)})
    offset = 0
    seen: list[int] = []
    page_count = 0

    while True:
        object_ref = "transition:current" if offset == 0 else f"transition:current:{offset}"
        projected = current_transition_projection(
            receipt=receipt,
            campaign_id="campaign.test",
            revision=8,
            object_ref=object_ref,
            event_offset=offset,
            combat_opposing_person_refs=opposing,
        )["object"]
        validate_bounded_json(projected, label="game object projection", allow_float=True)

        rows = projected["events"]
        assert rows or offset == projected["event_count"]
        seen.extend(row["sequence"] for row in rows)
        page_count += 1

        next_ref = projected["next_object_ref"]
        if next_ref is None:
            break
        offset = int(next_ref.rsplit(":", 1)[1])
        assert offset == len(seen)

    assert page_count > 5
    assert seen == list(range(80))


def test_first_rich_page_bounds_optional_narrative_without_startup_patch():
    projected = _project(0)

    assert projected["event_count"] == 80
    assert 1 <= len(projected["events"]) <= 16
    assert [row["sequence"] for row in projected["events"]] == list(
        range(len(projected["events"]))
    )

    narrative = projected["combat_narrative"]
    assert narrative["material_event_count"] == 80
    assert narrative["material_beats_truncated"] is True
    assert narrative["omitted_material_beat_count"] > 0

    validate_bounded_json(projected, label="game object projection", allow_float=True)
