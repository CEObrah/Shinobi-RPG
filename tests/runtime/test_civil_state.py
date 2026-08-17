import json
from pathlib import Path
import shutil

import pytest

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.sim.scheduler_store import SchedulerStore
from shinobi_runtime.sim.events import CampaignTime
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
        request_id=f"civil-{suffix}-{meta['revision']}",
        actor_id=actor,
        command_type=kind,
        expected_revision=meta["revision"],
        submitted_at=SUBMITTED,
        payload=payload,
        mode="gameplay" if actor == "pc_wei_tang" else "autonomous",
    )


def _plan(root: Path, envelope: CommandEnvelope):
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


def _commit(root: Path, plan) -> None:
    for relative, payload in plan.writes.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def test_diplomacy_requires_each_party_to_persist_consent(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    proposal, overlay = _plan(
        root,
        _command(
            root,
            "diplomacy_resolution",
            {
                "action": "propose",
                "agreement_ref": "agreement.test.tang_hyuga_trade",
                "representing_ref": "house.tang",
                "agreement_type": "trade",
                "party_refs": ["house.tang", "faction.hyuga_clan"],
                "terms": ["Mutual lawful trade access under each party's existing authority."],
                "visibility": "restricted",
            },
            "diplomacy-propose",
            "pc_wei_tang",
        ),
    )
    row = overlay.read_json("state/reg/diplomacy.json")["agreements"]["agreement.test.tang_hyuga_trade"]
    assert row["status"] == "proposed"
    assert row["consent_refs"] == ["house.tang"]
    _commit(root, proposal)

    acceptance, accepted_overlay = _plan(
        root,
        _command(
            root,
            "diplomacy_resolution",
            {
                "action": "accept",
                "agreement_ref": "agreement.test.tang_hyuga_trade",
                "representing_ref": "faction.hyuga_clan",
            },
            "diplomacy-accept",
            "canon_hiashi",
        ),
    )
    accepted = accepted_overlay.read_json("state/reg/diplomacy.json")["agreements"]["agreement.test.tang_hyuga_trade"]
    assert accepted["status"] == "active"
    assert set(accepted["consent_refs"]) == {"house.tang", "faction.hyuga_clan"}
    assert accepted["effective_at"] is not None


def test_bounty_posting_moves_money_into_exact_escrow_without_minting(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    event_ref = RepositoryStore(root).read_json("state/reg/world-events.json")["events"][-1]["id"]

    commands = [
        ("open", {
            "action": "open", "case_ref": "case.test.zabuza", "issuer_ref": "house.tang",
            "subject_ref": "canon_zabuza", "case_kind": "wanted_person",
            "offense_refs": ["offense.test.banditry"], "summary": "Test evidence-backed wanted case.", "visibility": "restricted",
        }),
        ("evidence", {"action": "add_evidence", "case_ref": "case.test.zabuza", "evidence_ref": event_ref}),
        ("warrant", {"action": "issue_warrant", "case_ref": "case.test.zabuza", "authority_ref": "house.tang"}),
    ]
    for suffix, payload in commands:
        plan, _ = _plan(root, _command(root, "legal_case_resolution", payload, f"legal-{suffix}", "pc_wei_tang"))
        _commit(root, plan)

    before = RepositoryStore(root).read_json("state/inventory/registry.json")
    before_total = sum(int(items.get("currency.ryo", 0)) for items in before["holders"].values() if isinstance(items, dict))
    before_house = before["holders"]["house.tang"]["currency.ryo"]

    amount = 125000
    post, overlay = _plan(
        root,
        _command(
            root,
            "legal_case_resolution",
            {"action": "post_bounty", "case_ref": "case.test.zabuza", "payer_ref": "house.tang", "amount_ryo": amount},
            "legal-post",
            "pc_wei_tang",
        ),
    )
    inventory = overlay.read_json("state/inventory/registry.json")
    after_total = sum(int(items.get("currency.ryo", 0)) for items in inventory["holders"].values() if isinstance(items, dict))
    assert after_total == before_total
    assert inventory["holders"]["house.tang"]["currency.ryo"] == before_house - amount
    assert inventory["holders"]["escrow.legal.test.zabuza"]["currency.ryo"] == amount
    case = overlay.read_json("state/reg/legal-cases.json")["cases"]["case.test.zabuza"]
    assert case["bounty"]["status"] == "posted"
    assert case["warrant"]["status"] == "active"


def test_founding_settlement_conserves_existing_population_and_creates_governed_place(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    population_path = root / "state/population/registry.json"
    population = json.loads(population_path.read_text())
    source = population["pools"]["pool.iwa.civilian_general"]
    source["owner_ref"] = "house.tang"
    population_path.write_text(json.dumps(population, indent=2) + "\n")

    before_count = source["count"]
    before_total = sum(pool["count"] for pool in population["pools"].values())
    count = 120
    plan, overlay = _plan(
        root,
        _command(
            root,
            "governance_resolution",
            {
                "action": "found_settlement",
                "jurisdiction_ref": "jurisdiction.test.sword_outpost",
                "place_ref": "place.test.sword_outpost",
                "base_place_ref": "place.sword_manor",
                "settlement_name": "Sword Outpost Test",
                "sovereign_ref": "house.tang",
                "administration_ref": "house.tang",
                "source_population_pool_id": "pool.iwa.civilian_general",
                "resident_pool_id": "pool.test.sword_outpost.residents",
                "initial_population": count,
                "treasury_holder_ref": "treasury.test.sword_outpost",
                "summary": "Found a governed outpost by moving existing consenting residents.",
                "visibility": "restricted",
            },
            "governance-found",
            "pc_wei_tang",
        ),
    )

    after = overlay.read_json("state/population/registry.json")
    assert after["pools"]["pool.iwa.civilian_general"]["count"] == before_count - count
    assert after["pools"]["pool.test.sword_outpost.residents"]["count"] == count
    assert sum(pool["count"] for pool in after["pools"].values()) == before_total
    for distribution in after["pools"]["pool.iwa.civilian_general"]["profile"]["numeric_distributions"].values():
        assert distribution["count"] == before_count - count
    for distribution in after["pools"]["pool.test.sword_outpost.residents"]["profile"]["numeric_distributions"].values():
        assert distribution["count"] == count

    jurisdiction = overlay.read_json("state/reg/governance.json")["jurisdictions"]["jurisdiction.test.sword_outpost"]
    assert jurisdiction["status"] == "outpost"
    assert jurisdiction["recruitment_rights"] is False
    assert jurisdiction["population_pool_ref"] == "pool.test.sword_outpost.residents"
    assert "treasury.test.sword_outpost" in overlay.read_json("state/inventory/registry.json")["holders"]
    places = overlay.read_json("state/world/routes-and-settlements.json")["payload"]["places"]
    assert any(place["id"] == "place.test.sword_outpost" and place["authority_ref"] == "house.tang" for place in places)
    assert "hidden-village status requires a lawful governance upgrade" in overlay.read_json("state/world/routes-and-settlements.json")["payload"]["settlement_generation_rule"]


def test_founding_settlement_can_draw_real_foreign_civilians_only_through_active_migration_treaty(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    agreement_ref = "agreement.test.tang_iwa_migration"
    proposal, _ = _plan(
        root,
        _command(
            root,
            "diplomacy_resolution",
            {
                "action": "propose",
                "agreement_ref": agreement_ref,
                "representing_ref": "house.tang",
                "agreement_type": "migration",
                "party_refs": ["house.tang", "faction_iwa"],
                "terms": ["Permit a bounded voluntary civilian migration cohort to establish a House Tang outpost."],
                "visibility": "restricted",
            },
            "migration-propose",
            "pc_wei_tang",
        ),
    )
    _commit(root, proposal)
    acceptance, _ = _plan(
        root,
        _command(
            root,
            "diplomacy_resolution",
            {"action": "accept", "agreement_ref": agreement_ref, "representing_ref": "faction_iwa"},
            "migration-accept",
            "canon_onoki",
        ),
    )
    _commit(root, acceptance)

    repo = RepositoryStore(root)
    source_before = repo.read_json("state/population/registry.json")["pools"]["pool.iwa.civilian_general"]["count"]
    count = 75
    plan, overlay = _plan(
        root,
        _command(
            root,
            "governance_resolution",
            {
                "action": "found_settlement",
                "jurisdiction_ref": "jurisdiction.test.migration_outpost",
                "place_ref": "place.test.migration_outpost",
                "base_place_ref": "place.sword_manor",
                "settlement_name": "Migration Outpost Test",
                "sovereign_ref": "house.tang",
                "administration_ref": "house.tang",
                "source_population_pool_id": "pool.iwa.civilian_general",
                "resident_pool_id": "pool.test.migration_outpost.residents",
                "initial_population": count,
                "treasury_holder_ref": "treasury.test.migration_outpost",
                "migration_agreement_ref": agreement_ref,
                "summary": "Found an outpost using only civilians covered by a mutually accepted migration agreement.",
                "visibility": "restricted",
            },
            "migration-found",
            "pc_wei_tang",
        ),
    )
    population = overlay.read_json("state/population/registry.json")["pools"]
    assert population["pool.iwa.civilian_general"]["count"] == source_before - count
    assert population["pool.iwa.civilian_general"]["owner_ref"] == "faction_iwa"
    assert population["pool.test.migration_outpost.residents"]["count"] == count
    assert population["pool.test.migration_outpost.residents"]["owner_ref"] == "house.tang"
    jurisdiction = overlay.read_json("state/reg/governance.json")["jurisdictions"]["jurisdiction.test.migration_outpost"]
    assert agreement_ref in jurisdiction["basis_refs"]
    assert jurisdiction["recruitment_rights"] is False


def test_bounty_settlement_requires_verified_return_evidence_and_pays_once(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    repo = RepositoryStore(root)
    evidence_ref = repo.read_json("state/reg/world-events.json")["events"][-1]["id"]
    hunter_ref = "team.kiri.hunter.office.auto.a29a55f55d97"
    case_ref = "case.test.bounty_lifecycle"
    amount = 90000

    setup = [
        ("open", {
            "action": "open", "case_ref": case_ref, "issuer_ref": "house.tang",
            "subject_ref": "canon_zabuza", "case_kind": "wanted_person",
            "offense_refs": ["offense.test.banditry"], "summary": "Evidence-backed wanted test.",
            "visibility": "restricted",
        }),
        ("evidence", {"action": "add_evidence", "case_ref": case_ref, "evidence_ref": evidence_ref}),
        ("warrant", {"action": "issue_warrant", "case_ref": case_ref, "authority_ref": "house.tang"}),
        ("post", {"action": "post_bounty", "case_ref": case_ref, "payer_ref": "house.tang", "amount_ryo": amount}),
        ("assign", {"action": "assign_hunter", "case_ref": case_ref, "hunter_ref": hunter_ref}),
    ]
    for suffix, payload in setup:
        plan, _ = _plan(root, _command(root, "legal_case_resolution", payload, f"bounty-life-{suffix}", "pc_wei_tang"))
        _commit(root, plan)

    before_inventory = RepositoryStore(root).read_json("state/inventory/registry.json")
    before_total = sum(int(items.get("currency.ryo", 0)) for items in before_inventory["holders"].values() if isinstance(items, dict))
    before_hunter = before_inventory["holders"].get(hunter_ref, {}).get("currency.ryo", 0)
    assert before_inventory["holders"]["escrow.legal.test.bounty_lifecycle"]["currency.ryo"] == amount

    with pytest.raises(CommandRejectedError, match="legal_bounty_claim_unverified"):
        _plan(
            root,
            _command(
                root, "legal_case_resolution",
                {"action": "settle_bounty", "case_ref": case_ref, "hunter_ref": hunter_ref},
                "bounty-life-premature", "pc_wei_tang",
            ),
        )

    verify, _ = _plan(
        root,
        _command(
            root, "legal_case_resolution",
            {"action": "verify_bounty", "case_ref": case_ref, "evidence_ref": evidence_ref},
            "bounty-life-verify", "pc_wei_tang",
        ),
    )
    _commit(root, verify)

    settle, overlay = _plan(
        root,
        _command(
            root, "legal_case_resolution",
            {"action": "settle_bounty", "case_ref": case_ref, "hunter_ref": hunter_ref},
            "bounty-life-settle", "pc_wei_tang",
        ),
    )
    inventory = overlay.read_json("state/inventory/registry.json")
    after_total = sum(int(items.get("currency.ryo", 0)) for items in inventory["holders"].values() if isinstance(items, dict))
    assert after_total == before_total
    assert inventory["holders"][hunter_ref]["currency.ryo"] == before_hunter + amount
    assert inventory["holders"]["escrow.legal.test.bounty_lifecycle"].get("currency.ryo", 0) == 0
    case = overlay.read_json("state/reg/legal-cases.json")["cases"][case_ref]
    assert case["bounty"]["status"] == "paid"
    assert case["status"] == "resolved"
    assert case["disposition"] == "bounty_paid_after_verified_evidence"
    _commit(root, settle)

    with pytest.raises(CommandRejectedError, match="legal_case_closed"):
        _plan(
            root,
            _command(
                root, "legal_case_resolution",
                {"action": "settle_bounty", "case_ref": case_ref, "hunter_ref": hunter_ref},
                "bounty-life-settle-again", "pc_wei_tang",
            ),
        )


def test_governed_outpost_can_progress_to_recognized_hidden_village_without_creating_population_or_remote_garrison(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    population_path = root / "state/population/registry.json"
    population = json.loads(population_path.read_text())
    source = population["pools"]["pool.iwa.civilian_general"]
    source["owner_ref"] = "house.tang"
    population_path.write_text(json.dumps(population, indent=2) + "\n")

    source_before = source["count"]
    total_before = sum(pool["count"] for pool in population["pools"].values())
    residents = 1200
    jurisdiction_ref = "jurisdiction.test.sword_hidden_village"
    place_ref = "place.test.sword_hidden_village"
    resident_pool_ref = "pool.test.sword_hidden_village.residents"

    found, found_overlay = _plan(
        root,
        _command(
            root,
            "governance_resolution",
            {
                "action": "found_settlement",
                "jurisdiction_ref": jurisdiction_ref,
                "place_ref": place_ref,
                "base_place_ref": "place.sword_manor",
                "settlement_name": "Sword Village Test",
                "sovereign_ref": "house.tang",
                "administration_ref": "house.tang",
                "source_population_pool_id": "pool.iwa.civilian_general",
                "resident_pool_id": resident_pool_ref,
                "initial_population": residents,
                "treasury_holder_ref": "treasury.test.sword_hidden_village",
                "summary": "Found a conserved House Tang outpost for progression testing.",
                "visibility": "restricted",
            },
            "governance-ladder-found",
            "pc_wei_tang",
        ),
    )
    found_event = found.result["semantic_event_id"]
    assert found_overlay.read_json("state/reg/governance.json")["jurisdictions"][jurisdiction_ref]["status"] == "outpost"
    _commit(root, found)

    policy, _ = _plan(
        root,
        _command(
            root,
            "governance_resolution",
            {"action": "set_policy", "jurisdiction_ref": jurisdiction_ref, "recruitment_rights": True, "tax_milli": 125},
            "governance-ladder-policy",
            "pc_wei_tang",
        ),
    )
    policy_event = policy.result["semantic_event_id"]
    _commit(root, policy)

    integration, _ = _plan(
        root,
        _command(
            root,
            "governance_resolution",
            {"action": "integrate", "jurisdiction_ref": jurisdiction_ref, "evidence_ref": found_event, "delta_milli": 200},
            "governance-ladder-integrate-founded",
            "pc_wei_tang",
        ),
    )
    _commit(root, integration)

    with pytest.raises(CommandRejectedError, match="governance_evidence_already_applied"):
        _plan(
            root,
            _command(
                root,
                "governance_resolution",
                {"action": "integrate", "jurisdiction_ref": jurisdiction_ref, "evidence_ref": found_event, "delta_milli": 200},
                "governance-ladder-integrate-duplicate",
                "pc_wei_tang",
            ),
        )

    settlement, _ = _plan(
        root,
        _command(
            root,
            "governance_resolution",
            {"action": "upgrade", "jurisdiction_ref": jurisdiction_ref, "target_status": "settlement"},
            "governance-ladder-settlement",
            "pc_wei_tang",
        ),
    )
    _commit(root, settlement)

    # This fixture represents the *separate* force/formation systems lawfully
    # establishing a House garrison at the new jurisdiction before governance
    # records it. Governance must not teleport or create that military force.
    force_path = root / "state/force/konoha-shinobi.json"
    force = json.loads(force_path.read_text())
    force["owner_ref"] = "house.tang"
    force["mobilization_anchor_ref"] = place_ref
    force_path.write_text(json.dumps(force, indent=2) + "\n")

    garrison, _ = _plan(
        root,
        _command(
            root,
            "governance_resolution",
            {"action": "set_garrison", "jurisdiction_ref": jurisdiction_ref, "garrison_force_ref": "force.konoha.shinobi"},
            "governance-ladder-garrison",
            "pc_wei_tang",
        ),
    )
    garrison_event = garrison.result["semantic_event_id"]
    _commit(root, garrison)

    village, _ = _plan(
        root,
        _command(
            root,
            "governance_resolution",
            {"action": "upgrade", "jurisdiction_ref": jurisdiction_ref, "target_status": "village"},
            "governance-ladder-village",
            "pc_wei_tang",
        ),
    )
    _commit(root, village)

    integration_two, _ = _plan(
        root,
        _command(
            root,
            "governance_resolution",
            {"action": "integrate", "jurisdiction_ref": jurisdiction_ref, "evidence_ref": garrison_event, "delta_milli": 200},
            "governance-ladder-integrate-garrison",
            "pc_wei_tang",
        ),
    )
    _commit(root, integration_two)

    integration_three, _ = _plan(
        root,
        _command(
            root,
            "governance_resolution",
            {"action": "integrate", "jurisdiction_ref": jurisdiction_ref, "evidence_ref": policy_event, "delta_milli": 50},
            "governance-ladder-integrate-policy",
            "pc_wei_tang",
        ),
    )
    _commit(root, integration_three)

    recognition_ref = "agreement.test.konoha_recognizes_sword_village"
    recognition_proposal, _ = _plan(
        root,
        _command(
            root,
            "diplomacy_resolution",
            {
                "action": "propose",
                "agreement_ref": recognition_ref,
                "representing_ref": "house.tang",
                "agreement_type": "recognition",
                "party_refs": ["house.tang", "faction_konoha"],
                "terms": ["Konoha recognizes the tested Sword jurisdiction as a separately governed polity."],
                "visibility": "public",
            },
            "governance-ladder-recognition-propose",
            "pc_wei_tang",
        ),
    )
    _commit(root, recognition_proposal)
    recognition_accept, _ = _plan(
        root,
        _command(
            root,
            "diplomacy_resolution",
            {"action": "accept", "agreement_ref": recognition_ref, "representing_ref": "faction_konoha"},
            "governance-ladder-recognition-accept",
            "canon_hiruzen",
        ),
    )
    _commit(root, recognition_accept)

    hidden, hidden_overlay = _plan(
        root,
        _command(
            root,
            "governance_resolution",
            {
                "action": "upgrade",
                "jurisdiction_ref": jurisdiction_ref,
                "target_status": "hidden_village",
                "recognition_agreement_ref": recognition_ref,
            },
            "governance-ladder-hidden-village",
            "pc_wei_tang",
        ),
    )
    jurisdiction = hidden_overlay.read_json("state/reg/governance.json")["jurisdictions"][jurisdiction_ref]
    assert jurisdiction["status"] == "hidden_village"
    assert jurisdiction["integration_milli"] == 650
    assert jurisdiction["recruitment_rights"] is True
    assert jurisdiction["tax_milli"] == 125
    assert jurisdiction["garrison_force_ref"] == "force.konoha.shinobi"
    assert jurisdiction["recognition_agreement_refs"] == [recognition_ref]

    population_after = hidden_overlay.read_json("state/population/registry.json")["pools"]
    assert population_after["pool.iwa.civilian_general"]["count"] == source_before - residents
    assert population_after[resident_pool_ref]["count"] == residents
    assert sum(pool["count"] for pool in population_after.values()) == total_before


def test_typed_tribute_settlement_moves_conserved_money_between_parties(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    agreement_ref = "agreement.test.tang_konoha_tribute"
    amount = 1500
    before = RepositoryStore(root).read_json("state/inventory/registry.json")
    before_total = sum(int(items.get("currency.ryo", 0)) for items in before["holders"].values() if isinstance(items, dict))
    before_payer = before["holders"]["house.tang"]["currency.ryo"]
    before_payee = before["holders"]["treasury.konoha"]["currency.ryo"]

    proposal, _ = _plan(root, _command(root, "diplomacy_resolution", {
        "action": "propose",
        "agreement_ref": agreement_ref,
        "representing_ref": "house.tang",
        "agreement_type": "tribute",
        "party_refs": ["house.tang", "faction_konoha"],
        "terms": ["House Tang will transfer the agreed conserved tribute amount when settlement is invoked."],
        "visibility": "restricted",
        "provisions": {"payer_ref": "house.tang", "payee_ref": "faction_konoha", "amount_ryo": amount},
    }, "tribute-propose", "pc_wei_tang"))
    _commit(root, proposal)
    acceptance, _ = _plan(root, _command(root, "diplomacy_resolution", {
        "action": "accept", "agreement_ref": agreement_ref, "representing_ref": "faction_konoha",
    }, "tribute-accept", "canon_hiruzen"))
    _commit(root, acceptance)

    settlement, overlay = _plan(root, _command(root, "diplomacy_resolution", {
        "action": "settle_tribute", "agreement_ref": agreement_ref, "representing_ref": "house.tang",
    }, "tribute-settle", "pc_wei_tang"))
    after = overlay.read_json("state/inventory/registry.json")
    after_total = sum(int(items.get("currency.ryo", 0)) for items in after["holders"].values() if isinstance(items, dict))
    assert after_total == before_total
    assert after["holders"]["house.tang"]["currency.ryo"] == before_payer - amount
    assert after["holders"]["treasury.konoha"]["currency.ryo"] == before_payee + amount
    row = overlay.read_json("state/reg/diplomacy.json")["agreements"][agreement_ref]
    assert row["settlement_count"] == 1
    assert row["last_settled_at"] is not None
    assert settlement.result["settlement_refs"]


def test_military_access_agreement_authorizes_foreign_formation_entry(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    now = RepositoryStore(root).read_json("state/meta.json")["time"]
    governance_path = root / "state/reg/governance.json"
    governance = json.loads(governance_path.read_text())
    governance["jurisdictions"]["jurisdiction.test.konoha_access"] = {
        "id": "jurisdiction.test.konoha_access",
        "place_ref": "place.konoha",
        "sovereign_ref": "faction_konoha",
        "administration_ref": "faction_konoha",
        "status": "hidden_village",
        "established_at": now,
        "updated_at": now,
        "population_pool_ref": "pool.konoha.civilian_general",
        "treasury_holder_ref": "treasury.konoha",
        "recruitment_rights": True,
        "tax_milli": 0,
        "integration_milli": 1000,
        "resistance_milli": 0,
        "garrison_force_ref": "force.konoha.shinobi",
        "recognition_agreement_refs": [],
        "parent_jurisdiction_ref": None,
        "basis_refs": ["fixture.test.military_access"],
        "visibility": "public",
    }
    governance_path.write_text(json.dumps(governance, indent=2) + "\n")

    planner = RepositoryCommandPlanner(RepositoryStore(root))
    with pytest.raises(CommandRejectedError, match="formation_movement_diplomatic_access_required"):
        planner._require_formation_diplomatic_access(
            force_ref="force.iwa.shinobi", force_owner_ref="faction_iwa",
            destination_ref="place.konoha", route_ref="route_fire_capital_konoha",
        )

    diplomacy_path = root / "state/reg/diplomacy.json"
    diplomacy = json.loads(diplomacy_path.read_text())
    diplomacy["agreements"]["agreement.test.iwa_konoha_access"] = {
        "id": "agreement.test.iwa_konoha_access",
        "agreement_type": "military_access",
        "party_refs": ["faction_konoha", "faction_iwa"],
        "status": "active",
        "proposed_by": "faction_konoha",
        "consent_refs": ["faction_konoha", "faction_iwa"],
        "rejection_refs": [],
        "terms": ["Iwa formations may enter Konoha through the designated route."],
        "opened_at": now,
        "effective_at": now,
        "ended_at": None,
        "evidence_refs": [],
        "visibility": "restricted",
        "provisions": {
            "grantor_ref": "faction_konoha",
            "grantee_ref": "faction_iwa",
            "place_refs": ["place.konoha"],
            "route_refs": ["route_fire_capital_konoha"],
        },
        "settlement_count": 0,
        "last_settled_at": None,
    }
    diplomacy_path.write_text(json.dumps(diplomacy, indent=2) + "\n")
    basis = RepositoryCommandPlanner(RepositoryStore(root))._require_formation_diplomatic_access(
        force_ref="force.iwa.shinobi", force_owner_ref="faction_iwa",
        destination_ref="place.konoha", route_ref="route_fire_capital_konoha",
    )
    assert basis == "agreement:agreement.test.iwa_konoha_access"
    strategic_source = (ROOT / "runtime/shinobi_runtime/commands/domains/strategic.py").read_text()
    assert "intrusion=(diplomatic_access_basis is None)" in strategic_source


def test_governed_destination_requires_exact_active_military_access() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "campaign"
        shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc"))
        governance_path = root / "state/reg/governance.json"
        governance = json.loads(governance_path.read_text())
        now = RepositoryStore(root).read_json("state/meta.json")["time"]
        governance["jurisdictions"]["jurisdiction.test.konoha_access"] = {
            "id": "jurisdiction.test.konoha_access", "place_ref": "place.konoha",
            "sovereign_ref": "faction_konoha", "administration_ref": "faction_konoha",
            "status": "hidden_village", "established_at": now, "updated_at": now,
            "population_pool_ref": "pool.konoha.civilian_general", "treasury_holder_ref": "treasury.konoha",
            "recruitment_rights": True, "tax_milli": 0, "integration_milli": 1000, "resistance_milli": 0,
            "garrison_force_ref": "force.konoha.shinobi", "recognition_agreement_refs": [],
            "parent_jurisdiction_ref": None, "basis_refs": ["fixture.test.military_access"], "visibility": "public",
        }
        governance_path.write_text(json.dumps(governance, indent=2) + "\n")
        planner = RepositoryCommandPlanner(RepositoryStore(root))
        with pytest.raises(CommandRejectedError, match="formation_movement_diplomatic_access_required"):
            planner._require_formation_diplomatic_access(
                force_ref="force.iwa.shinobi", force_owner_ref="faction_iwa",
                destination_ref="place.konoha", route_ref="route_konoha_fire_west",
            )
        diplomacy_path = root / "state/reg/diplomacy.json"
        diplomacy = json.loads(diplomacy_path.read_text())
        diplomacy["agreements"]["agreement.test.iwa_konoha_access"] = {
            "id": "agreement.test.iwa_konoha_access", "agreement_type": "military_access",
            "party_refs": ["faction_iwa", "faction_konoha"], "status": "active",
            "proposed_by": "faction_konoha", "consent_refs": ["faction_iwa", "faction_konoha"],
            "rejection_refs": [], "terms": ["Iwa formations may use the western road to enter Konoha."],
            "opened_at": now, "effective_at": now, "ended_at": None, "evidence_refs": [], "visibility": "restricted",
            "provisions": {"grantor_ref": "faction_konoha", "grantee_ref": "faction_iwa", "place_refs": ["place.konoha"], "route_refs": ["route_konoha_fire_west"]},
            "settlement_count": 0, "last_settled_at": None,
        }
        diplomacy_path.write_text(json.dumps(diplomacy, indent=2) + "\n")
        planner = RepositoryCommandPlanner(RepositoryStore(root))
        assert planner._require_formation_diplomatic_access(
            force_ref="force.iwa.shinobi", force_owner_ref="faction_iwa",
            destination_ref="place.konoha", route_ref="route_konoha_fire_west",
        ) == "agreement:agreement.test.iwa_konoha_access"


def test_nonaggression_treaty_blocks_state_level_conflict_start(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    diplomacy_path = root / "state/reg/diplomacy.json"
    diplomacy = json.loads(diplomacy_path.read_text())
    diplomacy["agreements"]["agreement.test.tang_hyuga_nonaggression"] = {
        "status": "active",
        "agreement_type": "nonaggression",
        "party_refs": ["house.tang", "faction.hyuga_clan"],
        "provisions": {},
        "visibility": "public",
    }
    diplomacy_path.write_text(json.dumps(diplomacy, indent=2) + "\n")

    with pytest.raises(CommandRejectedError, match="conflict_blocked_by_nonaggression_agreement"):
        _plan(root, _command(root, "conflict_resolution", {
            "action": "start",
            "conflict_ref": "conflict.test.tang_hyuga",
            "name": "Treaty violation attempt",
            "side_refs": ["house.tang", "faction.hyuga_clan"],
            "objectives": {"house.tang": ["Pressure the clan."], "faction.hyuga_clan": ["Defend itself."]},
        }, "nonaggression-war", "pc_wei_tang"))


def test_guarantee_attack_creates_timed_defense_obligation_commitment(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    diplomacy_path = root / "state/reg/diplomacy.json"
    diplomacy = json.loads(diplomacy_path.read_text())
    diplomacy["agreements"]["agreement.test.konoha_guarantees_hyuga"] = {
        "status": "active",
        "agreement_type": "guarantee",
        "party_refs": ["faction_konoha", "faction.hyuga_clan"],
        "provisions": {"guarantor_ref": "faction_konoha", "protected_ref": "faction.hyuga_clan"},
        "visibility": "public",
    }
    diplomacy_path.write_text(json.dumps(diplomacy, indent=2) + "\n")

    plan, overlay = _plan(root, _command(root, "conflict_resolution", {
        "action": "start",
        "conflict_ref": "conflict.test.tang_attacks_hyuga",
        "name": "Tang-Hyuga conflict fixture",
        "side_refs": ["house.tang", "faction.hyuga_clan"],
        "objectives": {"house.tang": ["Compel concessions."], "faction.hyuga_clan": ["Defend itself."]},
    }, "guarantee-war", "pc_wei_tang"))

    refs = plan.result["defense_commitment_refs"]
    assert len(refs) == 1
    commitment = next(
        row for row in overlay.read_json("state/reg/commitments.json")["records"]
        if row.get("id") == refs[0]
    )
    assert commitment["kind"] == "obligation"
    assert commitment["subject_ref"] == "faction_konoha"
    assert commitment["target_ref"] == "faction.hyuga_clan"
    assert commitment["authority_basis"].startswith("agreement:agreement.test.konoha_guarantees_hyuga:guarantee")
    assert commitment["due_at"] is not None
    scheduler = SchedulerStore(overlay).load(full=True)
    assert "host." + refs[0] in scheduler.hosts

    _commit(root, plan)
    response, response_overlay = _plan(root, _command(root, "commitment_transition", {
        "commitment_id": refs[0],
        "target_status": "completed",
        "summary": "Konoha leadership records its treaty response decision.",
    }, "guarantee-response", "canon_hiruzen"))
    persisted = next(
        row for row in response_overlay.read_json("state/reg/commitments.json")["records"]
        if row.get("id") == refs[0]
    )
    assert response.result["status"] == "completed"
    assert persisted["status"] == "completed"
    assert "host." + refs[0] not in SchedulerStore(response_overlay).load(full=True).hosts


def test_sovereign_institution_can_originate_progressive_alliance_proposal(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    policy_path = root / "game/rules/autonomy/living-world.json"
    policy = json.loads(policy_path.read_text())
    policy["sovereign_diplomacy"]["proposal_interval_reviews"] = 1
    profile = policy["sovereign_diplomacy"]["profiles"]["faction_konoha"]
    profile["partner_refs"] = ["faction_suna"]
    profile["proposal_cycle"] = ["trade", "nonaggression", "alliance"]
    policy_path.write_text(json.dumps(policy, indent=2) + "\n")

    diplomacy_path = root / "state/reg/diplomacy.json"
    diplomacy = json.loads(diplomacy_path.read_text())
    now = RepositoryStore(root).read_json("state/meta.json")["time"]
    for kind in ("trade", "nonaggression"):
        ref = f"agreement.test.konoha_suna_{kind}"
        diplomacy["agreements"][ref] = {
            "id": ref, "agreement_type": kind, "party_refs": ["faction_konoha", "faction_suna"],
            "status": "active", "proposed_by": "faction_konoha",
            "consent_refs": ["faction_konoha", "faction_suna"], "rejection_refs": [],
            "terms": [f"Existing {kind} basis for progressive alliance test."],
            "opened_at": now, "effective_at": now, "ended_at": None, "evidence_refs": [],
            "visibility": "public", "provisions": {},
        }
    diplomacy_path.write_text(json.dumps(diplomacy, indent=2) + "\n")

    institutions = json.loads((root / "state/world/institutions-konoha.json").read_text())["payload"]["institutions"]
    institution = next(row for row in institutions if row.get("id") == "institution.konoha.hokage_administration")
    planner = CampaignCommandPlanner(RepositoryStore(root))
    world_events = planner._world_events()
    writes: dict[str, dict] = {}
    command = _command(root, "advance_time", {"target_time": now}, "sovereign-diplomacy", "canon_hiruzen")
    result = planner._review_sovereign_diplomacy(
        institution=institution,
        at=CampaignTime.parse(now),
        command=command,
        world_events=world_events,
        record_writes=writes,
    )
    assert result is not None
    assert result["kind"] == "diplomacy_proposal"
    assert result["agreement_type"] == "alliance"
    proposed = writes["state/reg/diplomacy.json"]["agreements"][result["agreement_ref"]]
    assert proposed["proposed_by"] == "faction_konoha"
    assert proposed["party_refs"] == ["faction_konoha", "faction_suna"]
    assert proposed["consent_refs"] == ["faction_konoha"]
    assert proposed["status"] == "proposed"


def test_due_treaty_defense_obligation_is_autonomously_resolved_into_conflict_state(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    diplomacy_path = root / "state/reg/diplomacy.json"
    diplomacy = json.loads(diplomacy_path.read_text())
    now = RepositoryStore(root).read_json("state/meta.json")["time"]
    agreement_ref = "agreement.test.konoha_guarantees_hyuga_auto"
    diplomacy["agreements"][agreement_ref] = {
        "id": agreement_ref, "agreement_type": "guarantee",
        "party_refs": ["faction_konoha", "faction.hyuga_clan"], "status": "active",
        "proposed_by": "faction_konoha", "consent_refs": ["faction_konoha", "faction.hyuga_clan"],
        "rejection_refs": [], "terms": ["Konoha guarantees Hyuga defense for autonomous treaty-response testing."],
        "opened_at": now, "effective_at": now, "ended_at": None, "evidence_refs": [],
        "visibility": "public", "provisions": {"guarantor_ref": "faction_konoha", "protected_ref": "faction.hyuga_clan"},
    }
    diplomacy_path.write_text(json.dumps(diplomacy, indent=2) + "\n")

    conflict_ref = "conflict.test.tang_attacks_hyuga_auto"
    start, overlay = _plan(root, _command(root, "conflict_resolution", {
        "action": "start", "conflict_ref": conflict_ref, "name": "Autonomous guarantee response fixture",
        "side_refs": ["house.tang", "faction.hyuga_clan"],
        "objectives": {"house.tang": ["Compel concessions."], "faction.hyuga_clan": ["Defend itself."]},
    }, "guarantee-auto-war", "pc_wei_tang"))
    refs = start.result["defense_commitment_refs"]
    assert len(refs) == 1
    commitment_ref = refs[0]
    due_at = next(
        row["due_at"] for row in overlay.read_json("state/reg/commitments.json")["records"]
        if row.get("id") == commitment_ref
    )
    _commit(root, start)

    # Isolate the treaty boundary from unrelated player-facing campaign events.
    # This tests the due reducer itself, not the GM continuation policy.
    repo = RepositoryStore(root)
    scheduler_store = SchedulerStore(repo)
    scheduler = scheduler_store.load(full=True)
    kept_events = [
        event for event in scheduler.queue.snapshot()
        if event.payload.get("commitment_id") == commitment_ref
    ]
    assert len(kept_events) == 1
    kept_hosts = {event.target_host: scheduler.hosts[event.target_host] for event in kept_events}
    scheduler.queue.replace(kept_events)
    scheduler.hosts = kept_hosts
    scheduler.metrics["pending_event_count"] = len(kept_events)
    scheduler.metrics["host_count"] = len(kept_hosts)
    for relative, payload in scheduler_store.write_images(scheduler).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    advance, after = _plan(root, _command(root, "advance_time", {"target_time": due_at}, "guarantee-auto-due", "pc_wei_tang"))
    commitment = next(
        row for row in after.read_json("state/reg/commitments.json")["records"]
        if row.get("id") == commitment_ref
    )
    assert commitment["status"] == "completed", advance.result.get("commitment_reviews")
    conflict = after.read_json("state/conflict/registry.json")["records"][conflict_ref]
    assert "faction_konoha" in conflict["side_refs"]
    assert conflict["support_alignments"]["faction_konoha"] == "faction.hyuga_clan"
    incidents = after.read_json("state/reg/diplomacy.json")["incidents"]
    assert any(row.get("kind") == "treaty_obligation_honored" and row.get("evidence_ref") for row in incidents)
    assert advance.result["commitment_reviews"][-1]["treaty_decision"] == "comply"
