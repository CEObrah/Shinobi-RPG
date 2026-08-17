from __future__ import annotations

import json
from types import SimpleNamespace

from shinobi_runtime.commands.campaign_runtime_planner import _refresh_time_advanced_plan
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.tx.invalidations import load_transaction_invalidations, receipt_is_invalidated
from shinobi_runtime.tx.receipts import IdempotencyReceipt


def _validator(*_args):
    return None


def test_time_advance_scrubs_transient_scene_handoff() -> None:
    scene = {"narrative": {"current_scene_type": "old", "current_tension": "old", "active_questions": ["old"], "approaching_consequences": ["old"], "known_clues": ["keep"]}}
    plan = _BuiltPlan("advance_time_ready", ("state/scene.json",), {"state/scene.json": _json_bytes(scene)}, {}, _validator)
    narrative = json.loads(_refresh_time_advanced_plan(plan, "state/scene.json", previous_scene=scene).writes["state/scene.json"])["narrative"]
    assert narrative == {"known_clues": ["keep"]}


def test_transaction_invalidation_is_exact() -> None:
    digest = "ecca343a8f38bd33d1881d4fa972d3536deb79ca239e3f6f4dc95a1e6595d258"
    record = {"schema": "shinobi.transaction-invalidations", "version": 1, "records": [{"campaign_id": "shinobi-wei-main", "transaction_id": "tx.gameplay." + digest, "request_id": "request.synthetic.invalidated", "request_digest": digest, "invalidated_revision": 12, "restored_revision": 11, "reason": "synthetic transaction invalidation"}]}
    repo = SimpleNamespace(read_json=lambda _path: record)
    invalidations = load_transaction_invalidations(repo)
    receipt = IdempotencyReceipt(request_id="request.synthetic.invalidated", request_digest=digest, transaction_id="tx.gameplay." + digest, campaign_id="shinobi-wei-main", committed_revision=12, committed_at="2026-08-10T19:28:00Z", result={})
    assert receipt_is_invalidated(receipt, invalidations, current_revision=11)
    assert not receipt_is_invalidated(receipt, invalidations, current_revision=10)
