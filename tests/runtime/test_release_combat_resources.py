from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.store import RegisteredSchemaValidator, RegisteredTemplateValidator, RepositoryStore
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionPlanner

ROOT = Path(__file__).resolve().parents[2]
SUBMITTED = "2026-08-14T03:00:00Z"
ACTOR = "pc_wei_tang"


def _command(repo: RepositoryStore, payload: dict, suffix: str) -> CommandEnvelope:
    meta = repo.read_json("state/meta.json")
    return CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id=f"release-combat-resource-{suffix}",
        actor_id=ACTOR,
        command_type="combat_resolution",
        expected_revision=meta["revision"],
        submitted_at=SUBMITTED,
        payload=payload,
        mode="gameplay",
    )


def _opponent(repo: RepositoryStore) -> str:
    location = repo.read_json("state/scene.json")["location_id"]
    for path in sorted((repo.root / "state/char").glob("*.json")):
        record = json.loads(path.read_text())
        ref = record.get("owner_id") or record.get("id")
        if ref != ACTOR and record.get("current_location_id") == location and record.get("life_status") in ("active", "alive"):
            return str(ref)
    raise AssertionError("no co-located exact opponent in release snapshot")


def _payload(repo: RepositoryStore, *, technique_ref: str | None = None, weapon_ref: str | None = None, range_band: int = 1) -> dict:
    opponent = _opponent(repo)
    actor = {
        "actor_ref": ACTOR,
        "side_ref": "side:wei",
        "action": "attack",
        "target_refs": [opponent],
        "objective_ref": "objective:pressure",
        "lethal": False,
    }
    if technique_ref is not None:
        actor["technique_ref"] = technique_ref
    if weapon_ref is not None:
        actor["weapon_ref"] = weapon_ref
    return {
        "combat_id": f"combat.release.resource.{technique_ref or weapon_ref or 'baseline'}.{range_band}",
        "scale": "duel",
        "range_band": range_band,
        "participants": [
            actor,
            {
                "actor_ref": opponent,
                "side_ref": "side:opponent",
                "action": "hold",
                "target_refs": [],
                "objective_ref": None,
                "lethal": False,
            },
        ],
        "objectives": [
            {
                "objective_ref": "objective:pressure",
                "side_ref": "side:wei",
                "kind": "delay",
                "target_refs": [opponent],
                "zone_ref": None,
                "deadline_tick": 1,
            }
        ],
    }


def _validated(repo: RepositoryStore, envelope: CommandEnvelope):
    plan = RepositoryCommandPlanner(repo).plan(envelope)
    manifest = TransactionPlanner(repo).plan(
        envelope,
        transaction_id=plan.transaction_id,
        created_at=plan.created_at,
        writes=plan.writes,
    )
    overlay = StagedOverlay(repo, manifest)
    plan.validator(overlay, manifest)
    RegisteredSchemaValidator(repo).validate_overlay(overlay, manifest.paths)
    RegisteredTemplateValidator(repo).validate_overlay(overlay, manifest.paths)
    return plan, overlay


def _copy_repo(tmp_path: Path) -> RepositoryStore:
    work = tmp_path / "repo"
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    return RepositoryStore(work)


def test_selected_jutsu_spends_chakra_and_mastery_changes_projection(tmp_path: Path) -> None:
    repo = _copy_repo(tmp_path)
    before = repo.read_json("state/player.json")["resources"]["chakra"]["current"]
    plan, overlay = _validated(repo, _command(repo, _payload(repo, technique_ref="pressure_nails", range_band=1), "jutsu"))
    used = plan.result["methods_used"][ACTOR]
    assert used["technique_ref"] == "pressure_nails"
    assert used["chakra_cost"] > 0
    assert overlay.read_json("state/player.json")["resources"]["chakra"]["current"] == before - used["chakra_cost"]

    planner = RepositoryCommandPlanner(repo)
    actor = repo.read_json("state/player.json")
    technique = planner._combat_technique_record("pressure_nails")
    base = planner._combat_capability(actor)
    low, low_method = planner._project_exact_method_capability(actor, base=base, technique=technique, mastery=20)
    high, high_method = planner._project_exact_method_capability(actor, base=base, technique=technique, mastery=120)
    assert low_method == high_method == "ninjutsu"
    assert high.offense > low.offense
    assert high.control > low.control


@pytest.mark.parametrize("weapon_ref", ["weapon_kunai", "weapon_shuriken"])
def test_thrown_tools_are_conserved_and_zero_stock_rejects(tmp_path: Path, weapon_ref: str) -> None:
    repo = _copy_repo(tmp_path)
    inventory_path = repo.root / "state/inventory/registry.json"
    inventory = json.loads(inventory_path.read_text())
    inventory["holders"][ACTOR][weapon_ref] = 2
    inventory_path.write_text(json.dumps(inventory, indent=2) + "\n")
    repo = RepositoryStore(repo.root)

    plan, overlay = _validated(repo, _command(repo, _payload(repo, weapon_ref=weapon_ref, range_band=1), weapon_ref))
    assert plan.result["methods_used"][ACTOR]["weapon_ref"] == weapon_ref
    assert overlay.read_json("state/inventory/registry.json")["holders"][ACTOR][weapon_ref] == 1

    empty = json.loads(inventory_path.read_text())
    empty["holders"][ACTOR][weapon_ref] = 0
    inventory_path.write_text(json.dumps(empty, indent=2) + "\n")
    empty_repo = RepositoryStore(repo.root)
    with pytest.raises(CommandRejectedError, match="combat_weapon_not_held|combat_ammunition_insufficient"):
        RepositoryCommandPlanner(empty_repo).plan(_command(empty_repo, _payload(empty_repo, weapon_ref=weapon_ref, range_band=1), f"{weapon_ref}-empty"))


def test_jutsu_range_is_mechanical_not_narrative(tmp_path: Path) -> None:
    repo = _copy_repo(tmp_path)
    with pytest.raises(CommandRejectedError, match="combat_technique_out_of_range"):
        RepositoryCommandPlanner(repo).plan(
            _command(repo, _payload(repo, technique_ref="pressure_nails", range_band=3), "range-reject")
        )


def test_concrete_technique_equipment_requirement_is_custody_bound(tmp_path: Path) -> None:
    repo = _copy_repo(tmp_path)
    planner = RepositoryCommandPlanner(repo)
    technique = planner._combat_technique_record("shock_staff")
    riku = repo.read_json("state/char/riku-hyuga.json")
    holder = repo.read_json("state/inventory/registry.json")["holders"].get("char.riku_hyuga", {})
    assert holder.get("weapon_reinforced_bo", 0) > 0
    assert planner._technique_equipment_binding(
        technique=technique,
        holder=holder,
        requested_weapon_ref=None,
        record=riku,
    ) == "weapon_reinforced_bo"
    with pytest.raises(CommandRejectedError, match="combat_technique_required_equipment_missing"):
        planner._technique_equipment_binding(
            technique=technique,
            holder={},
            requested_weapon_ref=None,
            record=riku,
        )
