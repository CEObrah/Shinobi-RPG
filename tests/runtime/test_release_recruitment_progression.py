from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.combat.capabilities import consolidate_training, record_field_experience, weighted_state
from shinobi_runtime.store import RegisteredSchemaValidator, RegisteredTemplateValidator, RepositoryStore
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionPlanner

ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_ID = "shinobi-wei-main"
AUTHORITY = "canon_hiruzen"
SUBMITTED = "2026-08-11T00:00:00Z"


def _command(root: Path, *, request_id: str, count: int = 8) -> CommandEnvelope:
    meta = RepositoryStore(root).read_json("state/meta.json")
    return CommandEnvelope(
        campaign_id=CAMPAIGN_ID,
        request_id=request_id,
        actor_id=AUTHORITY,
        command_type="recruitment_resolution",
        expected_revision=meta["revision"],
        submitted_at=SUBMITTED,
        payload={
            "source_pool_id": "pool.konoha.civilian_general",
            "destination_pool_id": "pool.konoha.shinobi_service",
            "requested_count": count,
            "policy_ref": "recruitment.general",
            "authority_ref": AUTHORITY,
            "background_ref": "background.hunting_forestry",
        },
        mode="autonomous",
    )


def _validated(root: Path, envelope: CommandEnvelope):
    repo = RepositoryStore(root)
    planner = RepositoryCommandPlanner(repo)
    plan = planner.plan(envelope)
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


def test_background_recruitment_is_deterministic_and_preserves_distribution_metadata() -> None:
    envelope = _command(ROOT, request_id="release-hunter-determinism")
    plan_a, overlay_a = _validated(ROOT, envelope)
    plan_b, overlay_b = _validated(ROOT, envelope)
    assert plan_a.result == plan_b.result
    assert plan_a.writes == plan_b.writes

    receipt = overlay_a.read_json("state/population/registry.json")["transfers"][-1]
    assert receipt["method"] == "policy_background_conserved_selection"
    assert receipt["accepted"] == 8
    assert receipt["accepted_profile"]["dimension_counts"]["background"] == {"hunting_forestry": 8}
    distributions = receipt["accepted_profile"]["numeric_distributions"]
    awareness = distributions["combat.fundamentals.awareness"]
    bow = distributions["combat.methods.bow"]
    assert awareness["count"] == 8 and awareness["sd"] == 15
    assert bow["count"] == 8 and bow["sd"] == 15
    policy = json.loads((ROOT / "game/rules/recruitment/policies.json").read_text())
    model = policy["background_profiles"]["background.hunting_forestry"]["distribution"]
    assert ["awareness", "tracking", "survival"] in model["covariance_groups"]
    assert "cannot be rerolled" in model["rule"]


def test_background_recruitment_partially_fulfills_instead_of_inventing_hunters(tmp_path: Path) -> None:
    campaign = tmp_path / "campaign"
    shutil.copytree(ROOT, campaign, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc"))
    registry_path = campaign / "state/population/registry.json"
    registry = json.loads(registry_path.read_text())
    source = registry["pools"]["pool.konoha.civilian_general"]
    background = source["profile"]["dimension_counts"]["background"]
    moved = background["hunting_forestry"] - 2
    background["hunting_forestry"] = 2
    background["general_service"] += moved
    registry_path.write_text(json.dumps(registry, indent=2) + "\n")

    plan, overlay = _validated(campaign, _command(campaign, request_id="release-hunter-scarcity", count=9))
    assert plan.result["accepted"] == 2
    assert plan.result["rejected"] == 7
    after = overlay.read_json("state/population/registry.json")
    assert after["pools"]["pool.konoha.civilian_general"]["profile"]["dimension_counts"]["background"]["hunting_forestry"] == 0
    assert after["transfers"][-1]["accepted_profile"]["dimension_counts"]["background"] == {"hunting_forestry": 2}


def test_hunter_intake_changes_actual_force_reserve_without_creating_people() -> None:
    before_force = RepositoryStore(ROOT).read_json("state/force/konoha-shinobi.json")
    before_population = RepositoryStore(ROOT).read_json("state/population/registry.json")
    plan, overlay = _validated(ROOT, _command(ROOT, request_id="release-hunter-force", count=20))
    after_force = overlay.read_json("state/force/konoha-shinobi.json")
    after_population = overlay.read_json("state/population/registry.json")

    assert after_force["total"] == before_force["total"] + 20
    assert after_population["pools"]["pool.konoha.shinobi_service"]["count"] == before_population["pools"]["pool.konoha.shinobi_service"]["count"] + 20
    assert after_population["pools"]["pool.konoha.civilian_general"]["count"] == before_population["pools"]["pool.konoha.civilian_general"]["count"] - 20
    assert sum(pool["count"] for pool in after_population["pools"].values()) == sum(pool["count"] for pool in before_population["pools"].values())

    before_training = before_force["reserve_capability"]["training_or_instruction"]
    after_training = after_force["reserve_capability"]["training_or_instruction"]
    assert after_training["count"] == before_training["count"] + 20
    # Hunters enter with a stronger bow/awareness distribution than the force training baseline.
    assert after_training["methods"]["bow"] >= before_training["methods"]["bow"]
    assert after_training["fundamentals"]["awareness"] >= before_training["fundamentals"]["awareness"]
    assert 1 <= after_training["spread"] <= 50
    assert plan.result["accepted"] == 20


def _capability(method: int, *, experience: int = 0, spread: int = 10) -> dict:
    return {
        "source_capability_ref": "test",
        "fundamentals": {
            "combat": 70,
            "awareness": 70,
            "endurance": 70,
            "chakra_control": 60,
            "chakra_output": 60,
            "movement": 70,
            "tactics": 65,
            "team_coordination": 65,
        },
        "methods": {
            "sword": method,
            "unarmed": 60,
            "thrown_tools": 55,
            "bow": 45,
            "polearm": 45,
            "heavy_weapon": 45,
            "ninjutsu": 50,
            "genjutsu": 45,
            "traps": 45,
            "sensory": 50,
            "medical": 35,
            "sealing": 35,
        },
        "equipment_methods": ["sword"],
        "equipment_readiness_milli": 1000,
        "intake_fundamental_bias": {k: 0 for k in ("combat", "awareness", "endurance", "chakra_control", "chakra_output", "movement", "tactics", "team_coordination")},
        "intake_method_bias": {k: 0 for k in ("sword", "unarmed", "thrown_tools", "bow", "polearm", "heavy_weapon", "ninjutsu", "genjutsu", "traps", "sensory", "medical", "sealing")},
        "spread": spread,
        "experience": experience,
        "development_evidence": {"combat_exchanges": 0, "mission_events": 0, "training_hours": 0, "last_event_ref": None},
    }


def test_field_experience_creates_evidence_not_instant_sword_points_then_training_consolidates() -> None:
    initial = _capability(80, experience=20)
    field = record_field_experience(initial, event_ref="event.test.battle", exchanges=12)
    assert field["methods"]["sword"] == initial["methods"]["sword"]
    assert field["development_evidence"]["combat_exchanges"] == 12
    assert field["experience"] > initial["experience"]

    consolidated = consolidate_training(field, hours=16, focus_methods=("sword",))
    assert consolidated["methods"]["sword"] > field["methods"]["sword"]
    assert consolidated["development_evidence"]["combat_exchanges"] < field["development_evidence"]["combat_exchanges"]


def test_replacements_dilute_veteran_capability_and_preserve_population_spread() -> None:
    veterans = _capability(120, experience=110, spread=8)
    recruits = _capability(55, experience=5, spread=18)
    mixed = weighted_state(((veterans, 60), (recruits, 40)))
    assert recruits["methods"]["sword"] < mixed["methods"]["sword"] < veterans["methods"]["sword"]
    assert recruits["experience"] < mixed["experience"] < veterans["experience"]
    # The mixed unit remains heterogeneous rather than being flattened to a zero-variance mean.
    assert mixed["spread"] >= min(veterans["spread"], recruits["spread"])
    assert mixed["spread"] > veterans["spread"]


def test_governed_population_recruitment_requires_jurisdiction_rights(tmp_path: Path) -> None:
    campaign = tmp_path / "governed-campaign"
    shutil.copytree(ROOT, campaign, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc"))
    governance_path = campaign / "state/reg/governance.json"
    governance = json.loads(governance_path.read_text())
    now = RepositoryStore(campaign).read_json("state/meta.json")["time"]
    governance["jurisdictions"]["jurisdiction.test.konoha_recruitment"] = {
        "id": "jurisdiction.test.konoha_recruitment",
        "place_ref": "place.konoha",
        "sovereign_ref": "faction_konoha",
        "administration_ref": "faction_konoha",
        "status": "hidden_village",
        "established_at": now,
        "updated_at": now,
        "population_pool_ref": "pool.konoha.civilian_general",
        "treasury_holder_ref": "treasury.konoha",
        "recruitment_rights": False,
        "tax_milli": 0,
        "integration_milli": 1000,
        "resistance_milli": 0,
        "garrison_force_ref": "force.konoha.shinobi",
        "recognition_agreement_refs": [],
        "parent_jurisdiction_ref": None,
        "basis_refs": ["fixture.test.governance_recruitment_rights"],
        "visibility": "public",
    }
    governance_path.write_text(json.dumps(governance, indent=2) + "\n")

    import pytest
    from shinobi_runtime.api.contracts import CommandRejectedError

    with pytest.raises(CommandRejectedError, match="governance_recruitment_rights_denied"):
        _validated(campaign, _command(campaign, request_id="release-governance-recruitment-denied", count=1))

    governance["jurisdictions"]["jurisdiction.test.konoha_recruitment"]["recruitment_rights"] = True
    governance_path.write_text(json.dumps(governance, indent=2) + "\n")
    plan, overlay = _validated(campaign, _command(campaign, request_id="release-governance-recruitment-allowed", count=1))
    assert plan.result["accepted"] == 1
    assert overlay.read_json("state/population/registry.json")["transfers"][-1]["governance_jurisdiction_ref"] == "jurisdiction.test.konoha_recruitment"
