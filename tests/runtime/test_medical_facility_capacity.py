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
SUBMITTED = "2026-08-14T00:00:00Z"


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "campaign"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc"))
    for rel in ("state/player.json", "state/char/linh.json"):
        path = root / rel
        row = json.loads(path.read_text())
        row["current_location_id"] = "place.konoha.hospital"
        path.write_text(json.dumps(row, indent=2) + "\n")
    return root


def _env(root: Path, *, action: str = "surgery", facility_ref="place.konoha.hospital", suffix="case") -> CommandEnvelope:
    meta = RepositoryStore(root).read_json("state/meta.json")
    return CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id=f"medical-capacity-{suffix}-{meta['revision']}",
        actor_id="char.linh",
        command_type="medical_treatment_resolution",
        expected_revision=meta["revision"],
        submitted_at=SUBMITTED,
        payload={
            **{
                "action": action,
                "patient_ref": "pc_wei_tang",
                "practitioner_ref": "char.linh",
                "target_time": "SE-0061-06-11T09:01:00",
                "active_hours": "0.016666",
                "summary": "Resolve one deterministic capacity-sensitive treatment.",
                "visibility": "restricted",
            },
            **({"facility_ref": facility_ref} if facility_ref is not None else {}),
        },
        mode="autonomous",
    )


def _plan(root: Path, env: CommandEnvelope):
    repo = RepositoryStore(root)
    plan = RepositoryCommandPlanner(repo).plan(env)
    manifest = TransactionPlanner(repo).plan(env, transaction_id=plan.transaction_id, created_at=plan.created_at, writes=plan.writes)
    overlay = StagedOverlay(repo, manifest)
    plan.validator(overlay, manifest)
    RegisteredSchemaValidator(repo).validate_overlay(overlay, manifest.paths)
    RegisteredTemplateValidator(repo).validate_overlay(overlay, manifest.paths)
    return plan, overlay


def _medical_module(record: dict) -> dict:
    place = next(row for row in record["payload"]["places"] if row["id"] == "place.konoha.hospital")
    return place["mechanical_modules"]["medical"]


def test_hospital_surgery_consumes_real_pharmacy_stock_and_capacity(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    before_stock = RepositoryStore(root).read_json("state/stock/konoha-shinobi.json")["medical_kits"]
    plan, overlay = _plan(root, _env(root))
    module = _medical_module(overlay.read_json("state/world/routes-and-settlements.json"))
    assert module["bed_load_units"] == 2
    assert module["surgical_load_units"] == 1
    assert module["staff_load_units"] == 5
    assert module["last_capacity_update_at"] == "SE-0061-06-11T09:01:00"
    assert overlay.read_json("state/stock/konoha-shinobi.json")["medical_kits"] == before_stock - 2
    assert plan.result["facility_capacity"]["medical_kit_units_consumed"] == 2


def test_hospital_staff_fatigue_is_a_real_capacity_gate(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    world_path = root / "state/world/routes-and-settlements.json"
    world = json.loads(world_path.read_text())
    module = _medical_module(world)
    module["staff_load_units"] = module["staff_capacity_units"] - 1
    module["last_capacity_update_at"] = "SE-0061-06-11T09:00:00"
    world_path.write_text(json.dumps(world, indent=2) + "\n")
    with pytest.raises(CommandRejectedError, match="medical_facility_staff_capacity_exhausted"):
        RepositoryCommandPlanner(RepositoryStore(root)).plan(_env(root, suffix="fatigue"))


def test_hospital_capacity_recovers_with_elapsed_campaign_time(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    world_path = root / "state/world/routes-and-settlements.json"
    world = json.loads(world_path.read_text())
    module = _medical_module(world)
    module["staff_load_units"] = module["staff_capacity_units"]
    module["last_capacity_update_at"] = "SE-0061-06-11T08:00:00"
    world_path.write_text(json.dumps(world, indent=2) + "\n")
    _plan(root, _env(root, suffix="recover"))


def test_field_treatment_does_not_silently_use_hospital_quality_or_pharmacy(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    before_stock = RepositoryStore(root).read_json("state/stock/konoha-shinobi.json")["medical_kits"]
    plan, overlay = _plan(root, _env(root, action="treat", facility_ref=None, suffix="field"))
    assert plan.result["facility_capacity"] is None
    assert plan.result["effective_quality"] == RepositoryCommandPlanner(RepositoryStore(root))._medical_operator_quality(
        RepositoryStore(root).read_json("state/char/linh.json")
    )
    assert overlay.read_json("state/stock/konoha-shinobi.json")["medical_kits"] == before_stock
