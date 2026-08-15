from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dynamic_reputation_owners_are_mutable_state_templates_with_blank_contracts() -> None:
    shard = json.loads((ROOT / "runtime/contracts/template-index-shards/r.json").read_text())
    blank_index = json.loads((ROOT / "runtime/contracts/blank-owner-index.json").read_text())
    for schema_id in ("reputation-audience-profile", "reputation-event"):
        entry = shard["templates"][schema_id]
        template = json.loads((ROOT / entry["path"]).read_text())
        assert entry["scope"] == "mutable_state"
        assert template["scope"] == "mutable_state"
        blank_path = blank_index["owners"][schema_id]
        assert (ROOT / blank_path).is_file()
