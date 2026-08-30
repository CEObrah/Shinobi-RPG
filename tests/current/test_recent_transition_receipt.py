from __future__ import annotations

import pytest

from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.api.transition_operations import current_transition_projection
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.tx.receipts import IdempotencyReceipt, ReceiptStore


def _command(request_id: str = "test.transition.command") -> CommandEnvelope:
    return CommandEnvelope(
        campaign_id="campaign.test",
        request_id=request_id,
        actor_id="pc_test",
        command_type="jianghu_combat_resolution",
        expected_revision=7,
        submitted_at="2026-08-28T00:00:00Z",
        payload={
            "action": "exchange",
            "combat_ref": "combat:test",
            "until_resolution": True,
        },
        mode="gameplay",
    )


def _receipt(
    request_id: str = "test.transition.command",
    *,
    revision: int = 8,
    event_count: int = 0,
    include_command: bool = True,
) -> IdempotencyReceipt:
    command = _command(request_id)
    result = {
        "command_type": command.command_type,
        "combat_ref": "combat:test",
        "events": [
            {"sequence": index, "actor_ref": "pc_test", "result": "miss"}
            for index in range(event_count)
        ],
        "exchanges_resolved": max(1, event_count),
        "scope_stop_reason": "execution_frontier",
        "continuation_required": True,
    }
    if include_command:
        return IdempotencyReceipt.for_command(
            command,
            transaction_id=f"tx:{request_id}",
            committed_revision=revision,
            committed_at="2026-08-28T00:00:01Z",
            result=result,
        )
    return IdempotencyReceipt(
        request_id=command.request_id,
        request_digest=command.digest,
        transaction_id=f"tx:{request_id}",
        campaign_id=command.campaign_id,
        committed_revision=revision,
        committed_at="2026-08-28T00:00:01Z",
        result=result,
    )


def _project(receipt: IdempotencyReceipt, *, object_ref: str, event_offset: int):
    return current_transition_projection(
        receipt=receipt,
        campaign_id="campaign.test",
        revision=8,
        object_ref=object_ref,
        event_offset=event_offset,
        # These fixtures contain no opposing exact refs. Production derives this
        # set from exact combat sides before calling the projection.
        combat_opposing_person_refs=frozenset(),
    )


def test_new_receipt_round_trip_preserves_exact_committed_command():
    receipt = _receipt()
    restored = IdempotencyReceipt.from_record(receipt.to_record())

    assert restored.command is not None
    assert dict(restored.command) == _command().to_record()
    assert restored.request_digest == _command().digest


def test_legacy_receipt_without_command_remains_readable():
    receipt = _receipt(include_command=False)
    restored = IdempotencyReceipt.from_record(receipt.to_record())

    assert restored.command is None
    assert restored.committed_revision == 8


def test_receipt_store_can_find_unique_exact_campaign_revision(tmp_path):
    store = ReceiptStore(tmp_path / "receipts")
    prior = _receipt("test.transition.prior", revision=7)
    current = _receipt("test.transition.current", revision=8)
    store.put(prior)
    store.put(current)

    assert store.get_campaign_revision("campaign.test", 8) == current
    assert store.get_campaign_revision("campaign.test", 6) is None
    assert store.get_campaign_revision("other.campaign", 8) is None


def test_duplicate_receipts_for_one_campaign_revision_fail_closed(tmp_path):
    store = ReceiptStore(tmp_path / "receipts")
    store.put(_receipt("test.transition.a", revision=8))
    store.put(_receipt("test.transition.b", revision=8))

    with pytest.raises(ValueError, match="multiple receipts claim one campaign revision"):
        store.get_campaign_revision("campaign.test", 8)


def test_current_transition_projection_pages_events_without_losing_order():
    receipt = _receipt(event_count=40)

    first = _project(receipt, object_ref="transition:current", event_offset=0)["object"]
    second = _project(receipt, object_ref="transition:current:16", event_offset=16)["object"]
    third = _project(receipt, object_ref="transition:current:32", event_offset=32)["object"]

    assert first["command_recoverable"] is True
    assert first["command"] == _command().to_record()
    assert first["command_redacted"] is False
    assert first["events_withheld"] is False
    assert "events" not in first["result_metadata"]
    assert [row["sequence"] for row in first["events"]] == list(range(16))
    assert first["next_object_ref"] == "transition:current:16"
    assert [row["sequence"] for row in second["events"]] == list(range(16, 32))
    assert second["next_object_ref"] == "transition:current:32"
    assert [row["sequence"] for row in third["events"]] == list(range(32, 40))
    assert third["next_object_ref"] is None


def test_current_transition_projection_exposes_legacy_result_without_fabricating_command():
    projected = _project(
        _receipt(include_command=False, event_count=1),
        object_ref="transition:current",
        event_offset=0,
    )["object"]

    assert projected["command"] is None
    assert projected["command_recoverable"] is False
    assert projected["command_redacted"] is False
    assert projected["events"][0]["sequence"] == 0


def test_current_transition_projection_rejects_invalid_event_cursor():
    with pytest.raises(OperationError) as exc:
        _project(
            _receipt(event_count=2),
            object_ref="transition:current:3",
            event_offset=3,
        )
    assert exc.value.code == "current_transition_event_cursor_invalid"
