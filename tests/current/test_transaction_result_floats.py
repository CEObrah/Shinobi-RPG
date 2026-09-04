import json

import pytest

from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.tx.canonical import canonical_json_bytes
from shinobi_runtime.tx.receipts import IdempotencyReceipt


def _command(payload=None):
    return CommandEnvelope(
        campaign_id="test-campaign",
        request_id="test.transaction-result-floats",
        actor_id="pc.test",
        command_type="jianghu_combat_resolution",
        expected_revision=7,
        submitted_at="2026-08-28T00:00:00Z",
        payload=payload or {
            "action": "exchange",
            "combat_ref": "combat.test",
            "until_resolution": True,
        },
        mode="gameplay",
    )


def test_receipt_preserves_finite_exact_combat_result_floats():
    command = _command()
    result = {
        "command_type": "jianghu_combat_resolution",
        "combat_ref": "combat.test",
        "events": [
            {
                "result": "contact",
                "trace": {
                    "distance_m": 1.25,
                    "geometry": {"shape": "arc", "width_m": 0.42},
                },
                "projectile_speed_mps": 31.5,
            }
        ],
        "combat_status": "resolved",
    }

    receipt = IdempotencyReceipt.for_command(
        command,
        transaction_id="tx.test.transaction-result-floats",
        committed_revision=8,
        committed_at="2026-08-28T00:00:01Z",
        result=result,
    )
    decoded = json.loads(canonical_json_bytes(receipt.to_record()).decode("utf-8"))

    event = decoded["result"]["events"][0]
    assert event["trace"]["distance_m"] == 1.25
    assert event["trace"]["geometry"]["width_m"] == 0.42
    assert event["projectile_speed_mps"] == 31.5


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_durable_result_metadata_rejects_nonfinite_floats(bad):
    command = _command()

    with pytest.raises(TypeError, match="non-finite floating-point value"):
        IdempotencyReceipt.for_command(
            command,
            transaction_id="tx.test.nonfinite",
            committed_revision=8,
            committed_at="2026-08-28T00:00:01Z",
            result={"events": [{"distance_m": bad}]},
        )


def test_command_envelope_remains_float_free():
    with pytest.raises(TypeError, match="floating-point values are forbidden"):
        _command(
            {
                "action": "exchange",
                "combat_ref": "combat.test",
                "distance_m": 1.25,
            }
        )
