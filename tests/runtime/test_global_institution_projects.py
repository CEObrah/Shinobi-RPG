from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.campaign_intake_onboarding import CampaignCommandPlanner
from shinobi_runtime.store import RegisteredSchemaValidator, RegisteredTemplateValidator, RepositoryStore
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionPlanner

ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_ID = "shinobi-wei-main"
SUBMITTED = "2026-08-14T00:00:00Z"


def _copy_campaign(tmp_path: Path) -> Path:
    campaign = tmp_path / "campaign"
    shutil.copytree(ROOT, campaign, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc"))
    return campaign


def _command(root: Path, kind: str, payload: dict, suffix: str, actor: str) -> CommandEnvelope:
    meta = RepositoryStore(root).read_json("state/meta.json")
    return CommandEnvelope(
        campaign_id=CAMPAIGN_ID,
        request_id=f"global-institution-{suffix}-{meta['revision']}",
        actor_id=actor,
        command_type=kind,
        expected_revision=meta["revision"],
        submitted_at=SUBMITTED,
        payload=payload,
        mode="autonomous",
    )


def _plan(root: Path, envelope: CommandEnvelope):
    repo = RepositoryStore(root)
    plan = CampaignCommandPlanner(repo).plan(envelope)
    manifest = TransactionPlanner(repo).plan(
        envelope, transaction_id=plan.transaction_id, created_at=plan.created_at, writes=plan.writes
    )
    overlay = StagedOverlay(repo, manifest)
    plan.validator(overlay, manifest)
    RegisteredSchemaValidator(repo).validate_overlay(overlay, manifest.paths)
    RegisteredTemplateValidator(repo).validate_overlay(overlay, manifest.paths)
    return plan, overlay


def _commit(root: Path, plan) -> None:
    for relative, payload in plan.writes.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def test_non_house_leader_can_start_real_resource_backed_laboratory_project(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    before_stock = RepositoryStore(root).read_json("state/stock/oto-network.json")
    before_inventory = RepositoryStore(root).read_json("state/inventory/registry.json")
    before_oto = before_inventory["holders"]["treasury.oto"]["currency.ryo"]
    before_contractors = before_inventory["holders"]["economy.contractors"]["currency.ryo"]

    plan, overlay = _plan(root, _command(root, "institution_project_resolution", {
        "action": "start",
        "institution_ref": "faction.oto_research_cells",
        "project_type": "laboratory_expansion",
        "place_ref": "place.oto.hidden_laboratory",
        "stock_ref": "stock.force_oto_network",
        "summary": "Expand the existing Oto laboratory with conserved staff-facing capacity.",
        "visibility": "secret",
    }, "oto-lab", "canon_orochimaru"))

    after_stock = overlay.read_json("state/stock/oto-network.json")
    assert after_stock["engineering_toolkits"] == before_stock["engineering_toolkits"] - 8
    assert after_stock["medical_kits"] == before_stock["medical_kits"] - 8
    inventory = overlay.read_json("state/inventory/registry.json")
    assert inventory["holders"]["treasury.oto"]["currency.ryo"] == before_oto - 14_000_000
    assert inventory["holders"]["economy.contractors"]["currency.ryo"] == before_contractors + 14_000_000
    project = next(row for row in overlay.read_json("state/reg/missions-contracts-projects.json")["projects"] if row.get("id") == plan.result["project_ref"])
    assert project["institution_ref"] == "faction.oto_research_cells"
    assert project["module_kind"] == "laboratory"
    assert project["status"] == "active"


def test_non_house_manufacturing_uses_organization_control_not_house_policy(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    routes_path = root / "state/world/routes-and-settlements.json"
    routes = json.loads(routes_path.read_text())
    place = next(row for row in routes["payload"]["places"] if row.get("id") == "place.suna")
    place["mechanical_modules"] = {
        "production": {"puppet_lines": 1, "quality_milli": 1000}
    }
    routes_path.write_text(json.dumps(routes, indent=2) + "\n")

    plan, overlay = _plan(root, _command(root, "institution_manufacturing_resolution", {
        "action": "schedule",
        "institution_ref": "faction_suna",
        "summary": "Operate one conserved Suna puppet equipment line.",
        "visibility": "restricted",
        "recipe_ref": "manufacturing.suna.puppet_field_kit",
        "place_ref": "place.suna",
        "stock_ref": "stock.force_suna_shinobi",
    }, "suna-puppet", "canon_rasa"))
    assert plan.result["institution_ref"] == "faction_suna"
    schedule = next(row for row in overlay.read_json("state/reg/missions-contracts-projects.json")["projects"] if row.get("id") == plan.result["schedule_ref"])
    assert schedule["project_type"] == "manufacturing.suna.puppet_field_kit"
    assert schedule["stock_ref"] == "stock.force_suna_shinobi"
    assert schedule["module_kind"] == "production"


def test_facility_cardinality_and_line_capacity_have_no_universal_ceiling() -> None:
    mechanics = json.loads((ROOT / "game/data/mechanics/institution-projects.json").read_text())
    forbidden = {
        "capacity_slots", "beds", "surgical_capacity", "staff_capacity", "custody_capacity",
        "residential_capacity", "armor_lines", "weapon_lines", "puppet_lines", "medical_lines", "tool_lines",
    }
    for project_type, rule in mechanics["project_types"].items():
        assert forbidden.isdisjoint(set(rule.get("caps", {}))), project_type
    source = (ROOT / "runtime/shinobi_runtime/commands/domains/economy.py").read_text()
    assert "cap = caps.get(field_name)" in source
    assert "module[field_name] = min(cap, next_value) if isinstance(cap, int) else next_value" in source


def test_puppet_maintenance_scales_with_specialists_and_reusable_kits() -> None:
    systems = json.loads((ROOT / "game/data/mechanics/special-systems.json").read_text())
    maintenance = systems["puppets"]["maintenance"]
    assert maintenance["assets_per_specialist_per_review"] == 2
    assert "max_assets_per_review" not in maintenance
    source = (ROOT / "runtime/shinobi_runtime/commands/living_world_operations.py").read_text()
    assert "staffed_workstations = min(len(eligible), available_kits)" in source
    assert "throughput = staffed_workstations * per_specialist" in source


def test_manufacturing_can_require_exact_approved_research_candidate(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    routes_path = root / "state/world/routes-and-settlements.json"
    routes = json.loads(routes_path.read_text())
    place = next(row for row in routes["payload"]["places"] if row.get("id") == "place.suna")
    place["mechanical_modules"] = {"production": {"puppet_lines": 1, "quality_milli": 1000}}
    routes_path.write_text(json.dumps(routes, indent=2) + "\n")

    research_path = root / "state/reg/research.json"
    research = json.loads(research_path.read_text())
    project_ref = "research.test.suna_approved_puppet_kit"
    research["projects"][project_ref] = {
        "id": project_ref, "institution_ref": "faction_suna", "lead_ref": "canon_rasa", "place_ref": "place.suna",
        "project_kind": "engineering", "subject_ref": None, "custody_ref": None, "stock_ref": "stock.force_suna_shinobi",
        "material_costs": {}, "status": "succeeded", "hypothesis": "Validated field-kit production process.",
        "opened_at": "SE-0061-01-01T00:00:00", "last_advanced_at": "SE-0061-06-01T00:00:00", "next_due_at": None,
        "progress_milli": 1000, "risk_milli": 100, "result_claim_refs": ["claim.test.suna_puppet_kit"],
        "evidence_refs": [], "classification": "restricted", "candidate_kind": "manufacturing_recipe",
        "candidate_ref": "manufacturing.suna.puppet_field_kit", "prototype_status": "approved", "prototype_next_test_at": None,
        "prototype_test_refs": ["event.test.prototype.1", "event.test.prototype.2"], "successful_test_count": 2,
        "failed_test_count": 0, "approved_at": "SE-0061-06-01T00:00:00",
    }
    research_path.write_text(json.dumps(research, indent=2) + "\n")

    plan, overlay = _plan(root, _command(root, "institution_manufacturing_resolution", {
        "action": "schedule", "institution_ref": "faction_suna",
        "summary": "Manufacture the validated puppet field kit design.", "visibility": "restricted",
        "recipe_ref": "manufacturing.suna.puppet_field_kit", "place_ref": "place.suna",
        "stock_ref": "stock.force_suna_shinobi", "research_project_ref": project_ref,
    }, "suna-research-gated", "canon_rasa"))
    schedule = next(row for row in overlay.read_json("state/reg/missions-contracts-projects.json")["projects"] if row.get("id") == plan.result["schedule_ref"])
    assert schedule["research_project_ref"] == project_ref

    research["projects"][project_ref]["prototype_status"] = "validated"
    research_path.write_text(json.dumps(research, indent=2) + "\n")
    with pytest.raises(CommandRejectedError, match="institution_manufacturing_research_not_approved"):
        _plan(root, _command(root, "institution_manufacturing_resolution", {
            "action": "schedule", "institution_ref": "faction_suna",
            "summary": "Attempt production before prototype approval.", "visibility": "restricted",
            "recipe_ref": "manufacturing.suna.puppet_field_kit", "place_ref": "place.suna",
            "stock_ref": "stock.force_suna_shinobi", "research_project_ref": project_ref,
        }, "suna-research-unapproved", "canon_rasa"))
