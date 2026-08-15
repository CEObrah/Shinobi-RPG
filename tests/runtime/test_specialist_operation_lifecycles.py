from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil

from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.information.store import InformationStore
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store import RegisteredSchemaValidator, RegisteredTemplateValidator, RepositoryStore

ROOT = Path(__file__).resolve().parents[2]

ACTORS = {
    "faction.akatsuki": "canon_nagato",
    "faction.gato_company": "canon_gato",
    "faction.zabuza_mercenary_cell": "canon_zabuza",
    "faction.konoha_anbu": "canon_hiruzen",
    "faction.kiri_hunter_office": "support.kiri.hunter_captain",
    "faction.oto_research_cells": "canon_orochimaru",
    "faction.suna_puppet_corps": "canon_rasa",
    "faction.fire_daimyo_liaison": "support.daimyo.noboru_shimizu",
    "faction.mizuki_cell": "canon_mizuki",
}


def _copy_campaign(tmp_path: Path) -> Path:
    campaign = tmp_path / "campaign"
    shutil.copytree(ROOT, campaign, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc"))
    return campaign


def _op(planner: CampaignCommandPlanner, faction_ref: str, template_id: str, at: CampaignTime) -> dict:
    program = planner._institutional_program(faction_ref)
    assert program is not None
    template = next(row for row in program["operation_templates"] if row["id"] == template_id)
    return planner._operation_spec_from_template(
        faction_id=faction_ref,
        actor=ACTORS[faction_ref],
        at=at,
        template=template,
    )


def _known_claim(
    repo: RepositoryStore,
    writes: dict[str, dict],
    *,
    holder_ref: str,
    subject_ref: str,
    claim_ref: str,
    at: CampaignTime,
) -> None:
    information = InformationStore(repo, writes)
    information.add_claim({
        "claim_id": claim_ref,
        "subject_ref": subject_ref,
        "source_ref": holder_ref,
        "collected_at": str(at),
        "epistemic_kind": "observation",
        "confidence_milli": 700,
        "evidence_refs": [],
    })
    information.grant(holder_ref, claim_ref)


def _validate(repo: RepositoryStore, writes: dict[str, dict]) -> None:
    templates = RegisteredTemplateValidator(repo)
    schemas = RegisteredSchemaValidator(repo)
    for path, row in writes.items():
        if not path.endswith(".json") or not isinstance(row, dict) or not isinstance(row.get("schema"), str):
            continue
        template = templates.templates.get(row["schema"])
        if template is not None:
            templates._validate_document(row, template, label=path)
        validator = schemas.validators.get(row["schema"])
        if validator is not None:
            validator.validate(row)


def test_akatsuki_observation_and_tasking_require_sourced_target_knowledge(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    repo = RepositoryStore(root)
    planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])

    observe = _op(planner, "faction.akatsuki", "akatsuki.target_collection", at)
    observe["subject_ref"] = "canon_zabuza"
    observe["participant_refs"] = ["canon_itachi"]
    _rule, blocked = planner._autonomous_effect_preflight(observe, record_writes={})
    assert blocked is not None and "knows a sourced claim" in blocked

    writes: dict[str, dict] = {}
    _known_claim(repo, writes, holder_ref="canon_itachi", subject_ref="canon_zabuza", claim_ref="claim.test.itachi.zabuza", at=at)
    _rule, blocked = planner._autonomous_effect_preflight(observe, record_writes=writes)
    assert blocked is None

    task = _op(planner, "faction.akatsuki", "akatsuki.cell_tasking", at)
    task["subject_ref"] = "canon_zabuza"
    task["team_refs"] = ["team.akatsuki.itachi_kisame"]
    task["participant_refs"] = ["canon_itachi", "canon_kisame"]
    _rule, blocked = planner._autonomous_effect_preflight(task, record_writes=writes)
    assert blocked is None
    result = planner._apply_autonomous_operation_effect(
        operation=task,
        faction_id="faction.akatsuki",
        actor="canon_nagato",
        at=at,
        evidence_event_ref=repo.read_json("state/reg/world-events.json")["events"][-1]["id"],
        record_writes=writes,
    )
    assert result["status"] == "deferred"
    mission_ref = next(ref for ref in result["refs"] if ref.startswith("mission."))
    mission = writes[f"state/mission/{mission_ref}.json"]
    assert mission["operation_ref"] == task["operation_id"]
    assert set(mission["participant_refs"]) == {"canon_itachi", "canon_kisame"}
    assert task["subject_ref"] not in repo.read_json("state/reg/custody.json").get("records", {})
    _validate(repo, writes)


def test_gato_zabuza_service_contract_escrow_acceptance_task_and_exact_once_payout(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    repo = RepositoryStore(root)
    planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    writes: dict[str, dict] = {}

    inventory_before = repo.read_json("state/inventory/registry.json")["holders"]
    buyer_before = inventory_before["economy.land_waves.private"]["currency.ryo"]
    seller_before = inventory_before["treasury.zabuza_mercenary_cell"].get("currency.ryo", 0)

    offer = _op(planner, "faction.gato_company", "gato.mercenary_contract", at)
    result = planner._apply_autonomous_operation_effect(
        operation=offer, faction_id="faction.gato_company", actor="canon_gato", at=at,
        evidence_event_ref=repo.read_json("state/reg/world-events.json")["events"][-1]["id"], record_writes=writes,
    )
    assert result["status"] == "applied"
    contract_ref = next(ref for ref in result["refs"] if ref.startswith("contract.service."))
    contract = next(row for row in writes["state/reg/missions-contracts-projects.json"]["contracts"] if row["id"] == contract_ref)
    amount = contract["total_ryo"]
    assert amount == 1_300_000
    holders = writes["state/inventory/registry.json"]["holders"]
    assert holders["economy.land_waves.private"]["currency.ryo"] == buyer_before - amount
    assert holders[contract["payment_holder_ref"]]["currency.ryo"] == amount
    assert contract["status"] == "offered"

    accept = _op(planner, "faction.zabuza_mercenary_cell", "zabuza.contract_search", at)
    result = planner._apply_autonomous_operation_effect(
        operation=accept, faction_id="faction.zabuza_mercenary_cell", actor="canon_zabuza", at=at,
        evidence_event_ref=repo.read_json("state/reg/world-events.json")["events"][-1]["id"], record_writes=writes,
    )
    assert result["status"] == "applied"
    assert contract["status"] == "accepted"
    assert holders[contract["payment_holder_ref"]]["currency.ryo"] == amount

    job = _op(planner, "faction.zabuza_mercenary_cell", "zabuza.contract_job", at)
    job["team_refs"] = ["team.zabuza_haku"]
    result = planner._apply_autonomous_operation_effect(
        operation=job, faction_id="faction.zabuza_mercenary_cell", actor="canon_zabuza", at=at,
        evidence_event_ref=repo.read_json("state/reg/world-events.json")["events"][-1]["id"], record_writes=writes,
    )
    assert result["status"] == "deferred"
    assert contract["status"] == "in_progress"
    assert holders[contract["payment_holder_ref"]]["currency.ryo"] == amount

    settled = planner._settle_service_contract_for_operation(job, succeeded=True, at=at, record_writes=writes)
    assert contract_ref in settled
    assert contract["status"] == "completed"
    assert holders[contract["payment_holder_ref"]].get("currency.ryo", 0) == 0
    assert holders["treasury.zabuza_mercenary_cell"]["currency.ryo"] == seller_before + amount
    planner._settle_service_contract_for_operation(job, succeeded=True, at=at, record_writes=writes)
    assert holders["treasury.zabuza_mercenary_cell"]["currency.ryo"] == seller_before + amount
    _validate(repo, writes)


def test_anbu_capture_creates_custody_only_after_success_and_is_idempotent(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    repo = RepositoryStore(root)
    planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    writes: dict[str, dict] = {}
    op = _op(planner, "faction.konoha_anbu", "anbu.classified_capture", at)
    op["subject_ref"] = "canon_zabuza"
    op["team_refs"] = ["team.konoha.anbu.ro"]
    op["participant_refs"] = ["canon_yugao", "canon_aoba", "canon_hayate"]
    _known_claim(repo, writes, holder_ref="canon_yugao", subject_ref="canon_zabuza", claim_ref="claim.test.anbu.zabuza", at=at)
    _rule, blocked = planner._autonomous_effect_preflight(op, record_writes=writes)
    assert blocked is None

    effect = planner._apply_autonomous_operation_effect(
        operation=op, faction_id="faction.konoha_anbu", actor="canon_hiruzen", at=at,
        evidence_event_ref=repo.read_json("state/reg/world-events.json")["events"][-1]["id"], record_writes=writes,
    )
    assert effect["status"] == "deferred"
    mission_ref = next(ref for ref in effect["refs"] if ref.startswith("mission."))
    base_records = repo.read_json("state/reg/custody.json")["records"]
    assert all(row.get("subject_ref") != "canon_zabuza" for row in base_records.values())
    assert "state/reg/custody.json" not in writes

    refs = planner._capture_mission_subject(
        operation=op, mission_id=mission_ref, faction_id="faction.konoha_anbu", at=at, record_writes=writes,
    )
    custody_ref = next(ref for ref in refs if ref.startswith("custody."))
    record = writes["state/reg/custody.json"]["records"][custody_ref]
    assert record["subject_ref"] == "canon_zabuza"
    assert record["status"] == "captured_pending_placement"
    count_before = len(writes["state/reg/custody.json"]["records"])
    refs2 = planner._capture_mission_subject(
        operation=op, mission_id=mission_ref, faction_id="faction.konoha_anbu", at=at, record_writes=writes,
    )
    assert custody_ref in refs2
    assert len(writes["state/reg/custody.json"]["records"]) == count_before
    _validate(repo, writes)


def test_kiri_corpse_recovery_blocks_while_target_alive_and_oto_blocks_without_team(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    repo = RepositoryStore(root)
    planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])

    kiri_writes: dict[str, dict] = {}
    kiri = _op(planner, "faction.kiri_hunter_office", "kiri.corpse_secret_recovery", at)
    kiri["subject_ref"] = "canon_zabuza"
    kiri["team_refs"] = ["team.kiri.hunter.office.auto.a29a55f55d97"]
    kiri["participant_refs"] = ["canon_ao", "canon_chojuro"]
    _known_claim(repo, kiri_writes, holder_ref="canon_ao", subject_ref="canon_zabuza", claim_ref="claim.test.kiri.zabuza", at=at)
    _rule, blocked = planner._autonomous_effect_preflight(kiri, record_writes=kiri_writes)
    assert blocked == "recovery task requires the subject to be established dead"

    oto_writes: dict[str, dict] = {}
    oto = _op(planner, "faction.oto_research_cells", "oto.subject_acquisition", at)
    oto["subject_ref"] = "canon_sasuke"
    oto["participant_refs"] = ["canon_orochimaru"]
    _known_claim(repo, oto_writes, holder_ref="canon_orochimaru", subject_ref="canon_sasuke", claim_ref="claim.test.oto.sasuke", at=at)
    _rule, blocked = planner._autonomous_effect_preflight(oto, record_writes=oto_writes)
    assert blocked == "mission tasking requires an available eligible team"


def test_daimyo_border_coordination_records_incident_without_binding_agreement(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    repo = RepositoryStore(root)
    planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    op = _op(planner, "faction.fire_daimyo_liaison", "daimyo.border_coordination", at)
    writes: dict[str, dict] = {}
    result = planner._apply_autonomous_operation_effect(
        operation=op, faction_id="faction.fire_daimyo_liaison", actor=ACTORS["faction.fire_daimyo_liaison"], at=at,
        evidence_event_ref=repo.read_json("state/reg/world-events.json")["events"][-1]["id"], record_writes=writes,
    )
    assert result["status"] == "applied"
    diplomacy = writes["state/reg/diplomacy.json"]
    assert diplomacy["incidents"]
    assert diplomacy["agreements"] == repo.read_json("state/reg/diplomacy.json")["agreements"]
    _validate(repo, writes)


def test_successful_kiri_corpse_recovery_materializes_one_conserved_remains_asset(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    repo = RepositoryStore(root)
    planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    writes: dict[str, dict] = {}

    zabuza_path = "state/char/zabuza.json"
    zabuza = copy.deepcopy(repo.read_json(zabuza_path))
    zabuza["life_status"] = "dead"
    zabuza["condition"]["readiness"] = "dead"
    writes[zabuza_path] = zabuza

    op = _op(planner, "faction.kiri_hunter_office", "kiri.corpse_secret_recovery", at)
    op["subject_ref"] = "canon_zabuza"
    op["team_refs"] = ["team.kiri.hunter.office.auto.a29a55f55d97"]
    op["participant_refs"] = ["canon_ao", "canon_chojuro"]
    _known_claim(repo, writes, holder_ref="canon_ao", subject_ref="canon_zabuza", claim_ref="claim.test.kiri.dead_zabuza", at=at)
    _rule, blocked = planner._autonomous_effect_preflight(op, record_writes=writes)
    assert blocked is None
    effect = planner._apply_autonomous_operation_effect(
        operation=op, faction_id="faction.kiri_hunter_office", actor="support.kiri.hunter_captain", at=at,
        evidence_event_ref=repo.read_json("state/reg/world-events.json")["events"][-1]["id"], record_writes=writes,
    )
    assert effect["status"] == "deferred"
    mission_ref = next(ref for ref in effect["refs"] if ref.startswith("mission."))
    assert repo.read_json("state/reg/biological-remains.json")["records"] == {}
    assert "state/reg/biological-remains.json" not in writes

    refs = planner._recover_mission_remains(
        operation=op, mission_id=mission_ref, faction_id="faction.kiri_hunter_office", at=at,
        evidence_ref=repo.read_json("state/reg/world-events.json")["events"][-1]["id"], record_writes=writes,
    )
    remains_ref = next(ref for ref in refs if ref.startswith("remains."))
    record = writes["state/reg/biological-remains.json"]["records"][remains_ref]
    assert record["subject_ref"] == "canon_zabuza"
    assert record["status"] == "recovered"
    assert record["source_mission_ref"] == mission_ref
    assert record["evidence_refs"]
    count = len(writes["state/reg/biological-remains.json"]["records"])
    refs2 = planner._recover_mission_remains(
        operation=op, mission_id=mission_ref, faction_id="faction.kiri_hunter_office", at=at,
        evidence_ref=repo.read_json("state/reg/world-events.json")["events"][-1]["id"], record_writes=writes,
    )
    assert refs2 == [remains_ref]
    assert len(writes["state/reg/biological-remains.json"]["records"]) == count
    _validate(repo, writes)
