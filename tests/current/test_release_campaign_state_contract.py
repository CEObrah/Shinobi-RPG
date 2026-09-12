from __future__ import annotations

import json
from pathlib import Path

from tools.verify_release_state import CONTRACT_REL, state_tree_sha256, verify_release_state

ROOT = Path(__file__).resolve().parents[2]


def test_release_contract_pins_repaired_r108_black_lance_snapshot() -> None:
    contract = json.loads((ROOT / CONTRACT_REL).read_text(encoding="utf-8"))
    assert contract["release_baseline_id"] == "black-lance-ai-judgment-r108-20260912"
    assert contract["revision"] == 108
    assert contract["world_time"] == "SE-0061-09-27T21:21:45"
    assert contract["active_combat_elapsed_ms"] == 0
    assert sorted(contract["active_combat_side_sizes"]) == [12, 18]
    assert contract["active_combatant_count"] == 30
    # Pre-fight parley/interaction continuity is intentionally preserved by the
    # combat-only reset and is part of the repaired canonical snapshot.
    assert contract["interaction_attempt_count"] == 1
    assert contract["scene_history_count"] == 2
    assert contract["player_needles"] == 19
    assert contract["player_jian_condition_milli"] == 1000
    assert contract["state_tree_sha256"] == state_tree_sha256(ROOT)


def test_repaired_release_snapshot_passes_fullfresh_verifier() -> None:
    assert verify_release_state(ROOT) == []
