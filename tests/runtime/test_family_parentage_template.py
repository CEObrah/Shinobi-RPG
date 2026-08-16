from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_family_parentage_is_registered_mutable_state() -> None:
    template = json.loads(
        (ROOT / "runtime/contracts/templates/family-parentage.template.json").read_text()
    )
    shard = json.loads(
        (ROOT / "runtime/contracts/template-index-shards/f.json").read_text()
    )
    entry = shard["templates"]["family-parentage"]

    assert template["target_schema"] == "family-parentage"
    assert template["scope"] == "mutable_state"
    assert template["current_directories"] == ["state/family/parentage"]
    assert entry["scope"] == "mutable_state"
    assert entry["path"] == "runtime/contracts/templates/family-parentage.template.json"
