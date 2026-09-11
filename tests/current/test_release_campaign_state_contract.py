from __future__ import annotations

import json
import shutil
from pathlib import Path

from tools.verify_release_state import CONTRACT_REL, state_tree_sha256, verify_release_state

ROOT = Path(__file__).resolve().parents[2]


def test_release_contract_pins_fullfresh_revision_97_black_lance_snapshot() -> None:
    contract = json.loads((ROOT / CONTRACT_REL).read_text(encoding="utf-8"))
    assert contract["release_baseline_id"] == "black-lance-fullfresh-r97-20260908"
    assert contract["revision"] == 97
    assert contract["active_combat_elapsed_ms"] == 0
    assert sorted(contract["active_combat_side_sizes"]) == [12, 18]
    assert contract["active_combatant_count"] == 30
    assert contract["interaction_attempt_count"] == 0
    assert contract["scene_history_count"] == 0
    assert contract["player_needles"] == 19
    assert contract["player_jian_condition_milli"] == 1000
    assert contract["state_tree_sha256"] == state_tree_sha256(ROOT)
    assert verify_release_state(ROOT) == []


def test_release_contract_rejects_revision_106_style_stale_snapshot(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "state", tmp_path / "state")
    contract_target = tmp_path / CONTRACT_REL
    contract_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / CONTRACT_REL, contract_target)
    meta_path = tmp_path / "state/meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["revision"] = 106
    meta_path.write_text(json.dumps(meta, separators=(",", ":")) + "\n", encoding="utf-8")

    errors = verify_release_state(tmp_path)
    assert any("revision" in error for error in errors)
    assert any("state tree hash mismatch" in error for error in errors)
