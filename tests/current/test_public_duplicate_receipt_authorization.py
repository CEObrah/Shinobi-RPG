from types import SimpleNamespace

import pytest

from shinobi_runtime.api.operations import CampaignOperations, OperationError
from shinobi_runtime.commands import CommandEnvelope


def _command(*, actor="pc.intruder", mode="gameplay"):
    return CommandEnvelope(
        campaign_id="campaign.test",
        request_id="request.test",
        actor_id=actor,
        command_type="advance_time",
        expected_revision=1,
        submitted_at="0061-09-27T21:15:54Z",
        payload={},
        mode=mode,
    )


def test_duplicate_lookup_authorizes_before_touching_receipt_store():
    operations = object.__new__(CampaignOperations)
    operations.allowed_actor_ids = frozenset({"pc.wei"})
    operations.coordinator = SimpleNamespace(
        lookup_receipt=lambda _command: (_ for _ in ()).throw(AssertionError("receipt lookup must not run"))
    )
    with pytest.raises(OperationError) as exc:
        operations.lookup_command_receipt(_command())
    assert exc.value.status_code == 403
    assert exc.value.code == "actor_not_allowed"


def test_duplicate_lookup_rejects_internal_mode_before_receipt_store():
    operations = object.__new__(CampaignOperations)
    operations.allowed_actor_ids = frozenset({"pc.wei"})
    operations.coordinator = SimpleNamespace(
        lookup_receipt=lambda _command: (_ for _ in ()).throw(AssertionError("receipt lookup must not run"))
    )
    with pytest.raises(OperationError) as exc:
        operations.lookup_command_receipt(_command(actor="pc.wei", mode="autonomous"))
    assert exc.value.status_code == 403
    assert exc.value.code == "public_gameplay_mode_required"
