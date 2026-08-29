from __future__ import annotations

import json

import pytest

from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.api.transition_operations import (
    _combat_opposing_person_refs,
    current_transition_projection,
)
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.tx.receipts import IdempotencyReceipt


def _combat_command() -> CommandEnvelope:
    return CommandEnvelope(
        campaign_id="campaign.test",
        request_id="test.transition.hidden-combat",
        actor_id="pc_test",
        command_type="jianghu_combat_resolution",
        expected_revision=7,
        submitted_at="2026-08-28T00:00:00Z",
        payload={
            "action": "exchange",
            "combat_ref": "combat:test",
            "target_ref": "enemy.1",
        },
        mode="gameplay",
    )


def _combat_receipt(*, event_count: int = 20) -> IdempotencyReceipt:
    command = _combat_command()
    events = []
    for index in range(event_count):
        if index % 2:
            events.append(
                {
                    "sequence": index,
                    "actor_ref": "enemy.2",
                    "intended_ref": "pc_test",
                    "actual_ref": "pc_test",
                    "result": "miss",
                    "nested": {"observer_ref": "enemy.1"},
                }
            )
        else:
            events.append(
                {
                    "sequence": index,
                    "actor_ref": "pc_test",
                    "intended_ref": "enemy.1",
                    "actual_ref": "enemy.1",
                    "result": "contact",
                    "nested": {"witness_ref": "ally.1"},
                }
            )
    return IdempotencyReceipt.for_command(
        command,
        transaction_id="tx:hidden-combat",
        committed_revision=8,
        committed_at="2026-08-28T00:00:01Z",
        result={
            "command_type": "jianghu_combat_resolution",
            "combat_ref": "combat:test",
            "events": events,
            "combat_status": "active",
            "exchanges_resolved": 4,
            "scope_stop_reason": "scope_complete",
            "continuation_required": False,
            "future_metadata": {
                "target_ref": "enemy.2",
                "friendly_ref": "ally.1",
            },
        },
    )


def _read_combat(path: str):
    if path != "state/martial-world/combats.json":
        raise FileNotFoundError(path)
    return {
        "schema": "jianghu-combat-state-1.0",
        "combats": {
            "combat:test": {
                "status": "active",
                "sides": {
                    "side_a": ["pc_test", "ally.1"],
                    "side_b": ["enemy.1", "enemy.2"],
                },
            }
        },
    }


def test_combat_opposing_refs_are_derived_for_redaction_without_exposing_them():
    refs = _combat_opposing_person_refs(
        read_json=_read_combat,
        receipt=_combat_receipt(event_count=1),
        player_id="pc_test",
    )

    assert refs == frozenset({"enemy.1", "enemy.2"})


def test_combat_transition_redacts_hidden_opponents_in_command_metadata_and_events():
    receipt = _combat_receipt()
    refs = frozenset({"enemy.1", "enemy.2"})

    first = current_transition_projection(
        receipt=receipt,
        campaign_id="campaign.test",
        revision=8,
        object_ref="transition:current",
        event_offset=0,
        combat_opposing_person_refs=refs,
    )["object"]
    second = current_transition_projection(
        receipt=receipt,
        campaign_id="campaign.test",
        revision=8,
        object_ref="transition:current:16",
        event_offset=16,
        combat_opposing_person_refs=refs,
    )["object"]

    encoded = json.dumps([first, second], sort_keys=True)
    assert "enemy.1" not in encoded
    assert "enemy.2" not in encoded
    assert "opposing_combatant" in encoded
    assert "pc_test" in encoded
    assert "ally.1" in encoded
    assert first["command"]["payload"]["target_ref"] == "opposing_combatant"
    assert first["command_recoverable"] is False
    assert first["command_redacted"] is True
    assert first["result_metadata"]["future_metadata"]["target_ref"] == "opposing_combatant"
    assert first["result_metadata"]["future_metadata"]["friendly_ref"] == "ally.1"
    assert [row["sequence"] for row in first["events"]] == list(range(16))
    assert [row["sequence"] for row in second["events"]] == list(range(16, 20))
    assert first["next_object_ref"] == "transition:current:16"
    assert second["next_object_ref"] is None
    assert first["events_withheld"] is False
    assert first["event_identity_semantics"] == "opposing_exact_person_refs_redacted"


def test_combat_transition_withholds_detail_when_redaction_identity_cannot_be_resolved():
    projected = current_transition_projection(
        receipt=_combat_receipt(event_count=3),
        campaign_id="campaign.test",
        revision=8,
        object_ref="transition:current",
        event_offset=0,
        combat_opposing_person_refs=None,
    )["object"]

    encoded = json.dumps(projected, sort_keys=True)
    assert "enemy.1" not in encoded
    assert "enemy.2" not in encoded
    assert projected["event_count"] == 3
    assert projected["events"] == []
    assert projected["events_withheld"] is True
    assert projected["command"] is None
    assert projected["command_recoverable"] is False
    assert projected["command_withheld"] is True
    assert projected["result_metadata"] == {
        "command_type": "jianghu_combat_resolution",
        "combat_ref": "combat:test",
        "combat_status": "active",
        "exchanges_resolved": 4,
        "scope_stop_reason": "scope_complete",
        "continuation_required": False,
    }
    assert projected["next_object_ref"] is None
    assert projected["event_identity_semantics"] == "combat_identity_redaction_unavailable"


def test_withheld_combat_transition_rejects_nonzero_event_cursor():
    with pytest.raises(OperationError) as exc:
        current_transition_projection(
            receipt=_combat_receipt(event_count=3),
            campaign_id="campaign.test",
            revision=8,
            object_ref="transition:current:1",
            event_offset=1,
            combat_opposing_person_refs=None,
        )
    assert exc.value.code == "current_transition_event_cursor_invalid"


def test_mapping_key_collision_from_hidden_ids_fails_closed_without_leak():
    command = _combat_command()
    receipt = IdempotencyReceipt.for_command(
        command,
        transaction_id="tx:key-collision",
        committed_revision=8,
        committed_at="2026-08-28T00:00:01Z",
        result={
            "command_type": "jianghu_combat_resolution",
            "combat_ref": "combat:test",
            "events": [
                {
                    "sequence": 0,
                    "actor_ref": "pc_test",
                    "result": "contact",
                    "by_person": {
                        "enemy.1": {"value": 1},
                        "enemy.2": {"value": 2},
                    },
                }
            ],
        },
    )

    projected = current_transition_projection(
        receipt=receipt,
        campaign_id="campaign.test",
        revision=8,
        object_ref="transition:current",
        event_offset=0,
        combat_opposing_person_refs=frozenset({"enemy.1", "enemy.2"}),
    )["object"]

    encoded = json.dumps(projected, sort_keys=True)
    assert "enemy.1" not in encoded
    assert "enemy.2" not in encoded
    assert projected["events"] == []
    assert projected["events_withheld"] is True
    assert projected["event_identity_semantics"] == "combat_identity_redaction_failed_closed"


def test_noncombat_transition_keeps_existing_projection_semantics():
    command = CommandEnvelope(
        campaign_id="campaign.test",
        request_id="test.transition.noncombat",
        actor_id="pc_test",
        command_type="advance_time",
        expected_revision=7,
        submitted_at="2026-08-28T00:00:00Z",
        payload={"seconds": 60},
        mode="gameplay",
    )
    receipt = IdempotencyReceipt.for_command(
        command,
        transaction_id="tx:noncombat",
        committed_revision=8,
        committed_at="2026-08-28T00:00:01Z",
        result={
            "command_type": "advance_time",
            "events": [{"kind": "report", "subject_ref": "enemy.1"}],
        },
    )

    projected = current_transition_projection(
        receipt=receipt,
        campaign_id="campaign.test",
        revision=8,
        object_ref="transition:current",
        event_offset=0,
    )["object"]

    assert projected["command"] == command.to_record()
    assert projected["command_recoverable"] is True
    assert projected["events"] == [{"kind": "report", "subject_ref": "enemy.1"}]
    assert projected["event_identity_semantics"] == "not_applicable"
