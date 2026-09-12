from __future__ import annotations

import json
from pathlib import Path

import pytest

from shinobi_runtime.api.black_lance_combat_reset import build_black_lance_combat_reset
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.martial_world.equipment_state import effective_person_loadout
from shinobi_runtime.store.repository import RepositoryStore
from tools.verify_release_state import verify_release_state

ROOT = Path(__file__).resolve().parents[2]
COMBAT_REF = "combat:contact:escort_muster:0e52cfa45f5bbea72ba0:0061-09-27:black_lance_company"


def test_current_r108_is_fullfresh_black_lance_reset_snapshot() -> None:
    meta = json.loads((ROOT / "state/meta.json").read_text(encoding="utf-8"))
    combat = json.loads((ROOT / "state/martial-world/combats.json").read_text(encoding="utf-8"))["combats"][COMBAT_REF]
    ledger = json.loads((ROOT / "state/martial-world/equipment-ledger.json").read_text(encoding="utf-8"))
    loadout = effective_person_loadout(ledger, str(meta["player_id"]))

    assert meta["revision"] == 108
    assert meta["time"] == "SE-0061-09-27T21:21:45"
    assert combat["status"] == "active"
    assert combat["elapsed_ms"] == 0
    assert len(combat["combatants"]) == 30
    assert sorted(len(side) for side in combat["sides"].values()) == [12, 18]
    assert combat.get("team_plans", {}) == {}
    assert combat["combatants"][meta["player_id"]].get("ready_weapon_ref") == "weapon_jian"
    assert int(loadout.get("items", {}).get("weapon_needle", 0)) == 19
    assert int(loadout.get("condition_milli", {}).get("weapon_jian", 0)) == 1000
    assert verify_release_state(ROOT) == []


def test_one_shot_r107_reset_anchor_cannot_reapply_to_r108() -> None:
    with pytest.raises(OperationError, match="black_lance_reset_release_contract_mismatch"):
        build_black_lance_combat_reset(
            repository=RepositoryStore(ROOT), expected_revision=107
        )
