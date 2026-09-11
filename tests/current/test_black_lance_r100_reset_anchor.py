from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from shinobi_runtime.api.black_lance_combat_reset import (
    BLACK_LANCE_COMBAT_RESET_ANCHOR,
    build_black_lance_combat_reset,
)
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.store.repository import RepositoryStore
from tools.verify_release_state import verify_release_state

ROOT = Path(__file__).resolve().parents[2]
COMBAT_REF = "combat:contact:escort_muster:0e52cfa45f5bbea72ba0:0061-09-27:black_lance_company"


def _apply_plan(tmp_path: Path):
    shutil.copytree(ROOT / "state", tmp_path / "state")
    (tmp_path / "runtime/contracts").mkdir(parents=True)
    shutil.copy2(
        ROOT / "runtime/contracts/release-campaign-state.json",
        tmp_path / "runtime/contracts/release-campaign-state.json",
    )
    store = RepositoryStore(tmp_path)
    plan = build_black_lance_combat_reset(repository=store, expected_revision=100)
    route_before = store.read_bytes("state/martial-world/route-operations.json")
    for path, raw in plan.writes.items():
        store.replace_image(path, raw)
    return store, plan, route_before


def test_closed_r100_anchor_builds_forward_fullfresh_combat_reset(tmp_path: Path) -> None:
    store, plan, route_before = _apply_plan(tmp_path)

    assert plan.result["historical_anchor"] == BLACK_LANCE_COMBAT_RESET_ANCHOR
    assert plan.result["committed_revision"] == 101
    assert plan.result["preserved_route_and_mission_state"] is True
    assert "state/martial-world/route-operations.json" not in plan.writes
    assert store.read_bytes("state/martial-world/route-operations.json") == route_before

    meta = store.read_json("state/meta.json")
    scheduler = store.read_json("state/martial-world/scheduler.json")
    combat = store.read_json("state/martial-world/combats.json")["combats"][COMBAT_REF]
    assert meta["revision"] == 101
    assert meta["time"] == "SE-0061-09-27T21:21:45"
    assert scheduler["settled_through"] == "0061-09-27T21:21:45"
    assert combat["status"] == "active"
    assert combat["elapsed_ms"] == 0
    assert len(combat["combatants"]) == 30
    assert sorted(len(side) for side in combat["sides"].values()) == [12, 18]
    assert combat.get("team_plans", {}) == {}

    errors = verify_release_state(tmp_path)
    # The repair intentionally creates a new forward revision/state hash. Every
    # subsystem-specific full-fresh acceptance check must already pass before a
    # follow-up release contract can certify the new canonical state.
    assert len(errors) == 2
    assert errors[0].startswith("campaign baseline mismatch revision:")
    assert errors[1].startswith("campaign state tree hash mismatch:")


def test_closed_r100_anchor_refuses_state_drift(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "state", tmp_path / "state")
    (tmp_path / "runtime/contracts").mkdir(parents=True)
    shutil.copy2(
        ROOT / "runtime/contracts/release-campaign-state.json",
        tmp_path / "runtime/contracts/release-campaign-state.json",
    )
    meta_path = tmp_path / "state/meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["time"] = "SE-0061-09-27T21:22:35"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(OperationError, match="black_lance_reset_state_mismatch"):
        build_black_lance_combat_reset(
            repository=RepositoryStore(tmp_path), expected_revision=100
        )
