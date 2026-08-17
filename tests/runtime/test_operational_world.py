import json
from pathlib import Path
import shutil

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.store import RegisteredSchemaValidator, RegisteredTemplateValidator, RepositoryStore
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.information import InformationStore
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
        request_id=f"operational-{suffix}-{meta['revision']}",
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


def _world_event_ids(root: Path, count: int = 8) -> list[str]:
    rows = RepositoryStore(root).read_json("state/reg/world-events.json").get("events", [])
    return [row["id"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)][-count:]


def _advance_meta_days(root: Path, days: int = 7) -> None:
    from shinobi_runtime.sim.events import CampaignTime
    path = root / "state/meta.json"
    meta = json.loads(path.read_text())
    meta["time"] = str(CampaignTime.parse(meta["time"]).add_seconds(days * 24 * 60 * 60))
    path.write_text(json.dumps(meta, indent=2) + "\n")


def test_commerce_lifecycle_conserves_cargo_and_currency(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    before = RepositoryStore(root).read_json("state/inventory/registry.json")
    before_currency = sum(int(v.get("currency.ryo", 0)) for v in before["holders"].values() if isinstance(v, dict))
    before_kunai = sum(int(v.get("weapon_kunai", 0)) for v in before["holders"].values() if isinstance(v, dict))
    wei_cash = before["holders"]["pc_wei_tang"]["currency.ryo"]
    house_cash = before["holders"]["house.tang"]["currency.ryo"]

    contract = "trade.contract.test.kunai"
    steps = [
        ("offer", {
            "action": "offer_contract", "contract_ref": contract,
            "client_ref": "house.tang", "carrier_ref": "pc_wei_tang",
            "source_holder_ref": "pc_wei_tang", "destination_holder_ref": "house.tang",
            "item_ref": "weapon_kunai", "quantity": 2, "unit_price_ryo": 1000,
            "route_ref": "route_konoha_fire_west", "classification": "restricted", "contraband": False,
        }),
        ("accept", {"action": "accept_contract", "contract_ref": contract}),
        ("fund", {"action": "fund_contract", "contract_ref": contract}),
        ("dispatch", {"action": "dispatch", "contract_ref": contract}),
        ("deliver", {"action": "deliver", "contract_ref": contract}),
    ]
    for suffix, payload in steps:
        plan, _ = _plan(root, _command(root, "commerce_resolution", payload, suffix, "pc_wei_tang"))
        _commit(root, plan)

    after = RepositoryStore(root).read_json("state/inventory/registry.json")
    after_currency = sum(int(v.get("currency.ryo", 0)) for v in after["holders"].values() if isinstance(v, dict))
    after_kunai = sum(int(v.get("weapon_kunai", 0)) for v in after["holders"].values() if isinstance(v, dict))
    assert after_currency == before_currency
    assert after_kunai == before_kunai
    assert after["holders"]["house.tang"]["currency.ryo"] == house_cash - 2000
    assert after["holders"]["pc_wei_tang"]["currency.ryo"] == wei_cash + 2000
    assert after["holders"]["house.tang"]["weapon_kunai"] == 2
    registry = RepositoryStore(root).read_json("state/reg/commerce.json")
    assert registry["contracts"][contract]["status"] == "delivered"
    assert registry["shipments"]["shipment.test.kunai"]["status"] == "delivered"


def test_research_consumes_materials_requires_distinct_evidence_and_produces_delivered_claim(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    stock_before = RepositoryStore(root).read_json("state/stock/oto-network.json")
    events = _world_event_ids(root, 8)
    assert len(events) >= 4
    project = "research.test.oto_medical"
    start, _ = _plan(root, _command(root, "research_resolution", {
        "action": "start", "project_ref": project,
        "institution_ref": "faction.oto_research_cells", "lead_ref": "canon_kabuto",
        "place_ref": "place.oto.hidden_laboratory", "project_kind": "medical",
        "stock_ref": "stock.force_oto_network", "hypothesis": "Existing compounds can improve field stabilization reliability.",
        "classification": "secret",
    }, "research-start", "canon_orochimaru"))
    _commit(root, start)
    stock_after_start = RepositoryStore(root).read_json("state/stock/oto-network.json")
    assert stock_after_start["medical_kits"] == stock_before["medical_kits"] - 2
    assert stock_after_start["antidotes"] == stock_before["antidotes"] - 4

    for idx, evidence_ref in enumerate(events[-4:]):
        _advance_meta_days(root)
        plan, overlay = _plan(root, _command(root, "research_resolution", {
            "action": "advance", "project_ref": project, "evidence_ref": evidence_ref,
        }, f"research-advance-{idx}", "canon_orochimaru"))
        _commit(root, plan)

    research = RepositoryStore(root).read_json("state/reg/research.json")["projects"][project]
    assert research["status"] == "succeeded"
    assert research["progress_milli"] == 1000
    assert len(research["result_claim_refs"]) == 1
    claim_ref = research["result_claim_refs"][0]
    info = InformationStore(RepositoryStore(root))
    assert info.claim(claim_ref) is not None
    assert info.holder_knows("canon_kabuto", claim_ref)
    assert info.holder_knows("faction.oto_research_cells", claim_ref)
    deliveries = [info.delivery(ref) for ref in info.holder_delivery_refs("faction.oto_research_cells")]
    assert any(row and row.get("claim_id") == claim_ref and row.get("recipient_ref") == "faction.oto_research_cells" for row in deliveries)

    _advance_meta_days(root)
    with pytest.raises(CommandRejectedError, match="research_project_not_active"):
        _plan(root, _command(root, "research_resolution", {
            "action": "advance", "project_ref": project, "evidence_ref": events[-1],
        }, "research-after-success", "canon_orochimaru"))

    prototype, _ = _plan(root, _command(root, "research_resolution", {
        "action": "prototype", "project_ref": project,
        "candidate_kind": "manufacturing_recipe",
        "candidate_ref": "manufacturing.konoha.field_medical_kit",
    }, "research-prototype", "canon_orochimaru"))
    _commit(root, prototype)
    for attempt in range(1, 12):
        current = RepositoryStore(root).read_json("state/reg/research.json")["projects"][project]
        if current["prototype_status"] == "validated":
            break
        _advance_meta_days(root)
        tested, _ = _plan(root, _command(root, "research_resolution", {
            "action": "test_prototype", "project_ref": project,
        }, f"research-prototype-test-{attempt}", "canon_orochimaru"))
        _commit(root, tested)
    validated = RepositoryStore(root).read_json("state/reg/research.json")["projects"][project]
    assert validated["prototype_status"] == "validated"
    assert validated["successful_test_count"] >= 2
    assert len(validated["prototype_test_refs"]) >= 2
    approved, approved_overlay = _plan(root, _command(root, "research_resolution", {
        "action": "approve", "project_ref": project,
    }, "research-prototype-approve", "canon_orochimaru"))
    approved_project = approved_overlay.read_json("state/reg/research.json")["projects"][project]
    assert approved_project["prototype_status"] == "approved"
    assert approved_project["approved_at"] is not None
    assert approved_project["candidate_ref"] == "manufacturing.konoha.field_medical_kit"


def test_security_sector_consumes_real_stock_and_alarm_delivers_evidence(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    evidence = _world_event_ids(root, 2)[-1]
    stock_before = RepositoryStore(root).read_json("state/stock/konoha-shinobi.json")
    establish, _ = _plan(root, _command(root, "security_network_resolution", {
        "action": "establish_sector", "sector_ref": "security.test.konoha_gate",
        "owner_ref": "faction_konoha", "place_ref": "place.konoha.gate",
        "route_refs": ["route_konoha_fire_west"], "stock_ref": "stock.force_konoha_shinobi",
        "classification": "restricted",
    }, "security-establish", "canon_hiruzen"))
    _commit(root, establish)
    stock_after = RepositoryStore(root).read_json("state/stock/konoha-shinobi.json")
    assert stock_after["barrier_anchor_sets"] == stock_before["barrier_anchor_sets"] - 1
    assert stock_after["sensor_relays"] == stock_before["sensor_relays"] - 1

    with pytest.raises(CommandRejectedError, match="security_alarm_requires_evidence"):
        _plan(root, _command(root, "security_network_resolution", {
            "action": "raise_alarm", "alarm_ref": "alarm.test.invalid",
            "sector_ref": "security.test.konoha_gate", "subject_ref": "canon_orochimaru",
            "evidence_ref": "event.does_not_exist", "recipient_refs": ["canon_hiruzen"],
        }, "security-no-evidence", "canon_hiruzen"))

    alarm, overlay = _plan(root, _command(root, "security_network_resolution", {
        "action": "raise_alarm", "alarm_ref": "alarm.test.valid",
        "sector_ref": "security.test.konoha_gate", "subject_ref": "canon_orochimaru",
        "evidence_ref": evidence, "recipient_refs": ["canon_hiruzen", "faction_konoha"],
    }, "security-alarm", "canon_hiruzen"))
    assert overlay.read_json("state/reg/security-networks.json")["alarms"]["alarm.test.valid"]["status"] == "open"
    info = InformationStore(overlay)
    claims = [ref for ref in info.holder_claim_refs("faction_konoha") if ref.startswith("claim.security.")]
    assert len(claims) == 1
    claim_ref = claims[0]
    assert info.holder_knows("faction_konoha", claim_ref)
    deliveries = [info.delivery(ref) for ref in info.holder_delivery_refs("faction_konoha")]
    assert any(row and row.get("claim_id") == claim_ref and row.get("recipient_ref") == "faction_konoha" for row in deliveries)


def test_mission_market_moves_only_from_real_evidence_and_registered_signal(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    evidence = _world_event_ids(root, 2)[-1]
    before = RepositoryStore(root).read_json("state/reg/mission-markets.json")["markets"]["market_konoha_missions"]["demand_scores"]["escort"]
    plan, overlay = _plan(root, _command(root, "mission_market_resolution", {
        "action": "record_signal", "market_ref": "market_konoha_missions",
        "evidence_ref": evidence, "axis": "demand", "signal_ref": "escort", "direction": 1,
    }, "market-signal", "canon_hiruzen"))
    market = overlay.read_json("state/reg/mission-markets.json")["markets"]["market_konoha_missions"]
    assert market["demand_scores"]["escort"] == min(1000, before + 50)
    assert evidence in market["evidence_refs"]

    with pytest.raises(CommandRejectedError, match="mission_market_evidence_invalid"):
        _plan(root, _command(root, "mission_market_resolution", {
            "action": "record_signal", "market_ref": "market_konoha_missions",
            "evidence_ref": "event.not_real", "axis": "demand", "signal_ref": "escort", "direction": 1,
        }, "market-no-evidence", "canon_hiruzen"))


def test_commerce_delivery_collects_governance_route_tax_without_minting(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    governance_path = root / "state/reg/governance.json"
    governance = json.loads(governance_path.read_text())
    now = RepositoryStore(root).read_json("state/meta.json")["time"]
    governance["jurisdictions"]["jurisdiction.test.konoha_trade_tax"] = {
        "id": "jurisdiction.test.konoha_trade_tax",
        "place_ref": "place.konoha",
        "sovereign_ref": "faction_konoha",
        "administration_ref": "faction_konoha",
        "status": "hidden_village",
        "established_at": now,
        "updated_at": now,
        "population_pool_ref": "pool.konoha.civilian_general",
        "treasury_holder_ref": "treasury.konoha",
        "recruitment_rights": True,
        "tax_milli": 100,
        "integration_milli": 1000,
        "resistance_milli": 0,
        "garrison_force_ref": "force.konoha.shinobi",
        "recognition_agreement_refs": [],
        "parent_jurisdiction_ref": None,
        "basis_refs": ["fixture.test.commerce_tax"],
        "visibility": "public",
    }
    governance_path.write_text(json.dumps(governance, indent=2) + "\n")

    before = RepositoryStore(root).read_json("state/inventory/registry.json")
    before_total = sum(int(v.get("currency.ryo", 0)) for v in before["holders"].values() if isinstance(v, dict))
    before_treasury = before["holders"]["treasury.konoha"].get("currency.ryo", 0)
    before_carrier = before["holders"]["pc_wei_tang"].get("currency.ryo", 0)
    gross = 2000
    contract = "trade.contract.test.taxed_kunai"
    steps = [
        ("offer", {
            "action": "offer_contract", "contract_ref": contract,
            "client_ref": "house.tang", "carrier_ref": "pc_wei_tang",
            "source_holder_ref": "pc_wei_tang", "destination_holder_ref": "house.tang",
            "item_ref": "weapon_kunai", "quantity": 2, "unit_price_ryo": 1000,
            "route_ref": "route_konoha_fire_west", "classification": "restricted", "contraband": False,
        }),
        ("accept", {"action": "accept_contract", "contract_ref": contract}),
        ("fund", {"action": "fund_contract", "contract_ref": contract}),
        ("dispatch", {"action": "dispatch", "contract_ref": contract}),
    ]
    for suffix, payload in steps:
        plan, _ = _plan(root, _command(root, "commerce_resolution", payload, f"tax-{suffix}", "pc_wei_tang"))
        _commit(root, plan)

    deliver, overlay = _plan(root, _command(root, "commerce_resolution", {"action": "deliver", "contract_ref": contract}, "tax-deliver", "pc_wei_tang"))
    inventory = overlay.read_json("state/inventory/registry.json")
    after_total = sum(int(v.get("currency.ryo", 0)) for v in inventory["holders"].values() if isinstance(v, dict))
    assert after_total == before_total
    assert deliver.result["tax_ryo"] == 200
    assert deliver.result["carrier_net_ryo"] == gross - 200
    assert inventory["holders"]["treasury.konoha"]["currency.ryo"] == before_treasury + 200
    assert inventory["holders"]["pc_wei_tang"]["currency.ryo"] == before_carrier + gross - 200
    row = overlay.read_json("state/reg/commerce.json")["contracts"][contract]
    assert row["tax_settlements"] == [{
        "jurisdiction_ref": "jurisdiction.test.konoha_trade_tax",
        "treasury_holder_ref": "treasury.konoha",
        "tax_milli": 100,
        "amount_ryo": 200,
    }]


def test_contraband_dispatch_automatically_runs_route_security_detection(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    establish, _ = _plan(root, _command(root, "security_network_resolution", {
        "action": "establish_sector", "sector_ref": "security.test.auto_route",
        "owner_ref": "faction_konoha", "place_ref": "place.konoha.gate",
        "route_refs": ["route_konoha_fire_west"], "stock_ref": "stock.force_konoha_shinobi",
        "classification": "restricted",
    }, "security-auto-establish", "canon_hiruzen"))
    _commit(root, establish)

    security_path = root / "state/reg/security-networks.json"
    security = json.loads(security_path.read_text())
    sector = security["sectors"]["security.test.auto_route"]
    sector["coverage_milli"] = 1000
    sector["detection_milli"] = 1000
    sector["authorized_owner_refs"] = ["faction_konoha"]
    security_path.write_text(json.dumps(security, indent=2) + "\n")

    contract = "trade.contract.test.auto_detected_contraband"
    setup = [
        ("offer", {
            "action": "offer_contract", "contract_ref": contract,
            "client_ref": "house.tang", "carrier_ref": "pc_wei_tang",
            "source_holder_ref": "pc_wei_tang", "destination_holder_ref": "house.tang",
            "item_ref": "weapon_kunai", "quantity": 1, "unit_price_ryo": 1000,
            "route_ref": "route_konoha_fire_west", "classification": "secret", "contraband": True,
        }),
        ("accept", {"action": "accept_contract", "contract_ref": contract}),
        ("fund", {"action": "fund_contract", "contract_ref": contract}),
    ]
    for suffix, payload in setup:
        plan, _ = _plan(root, _command(root, "commerce_resolution", payload, f"security-auto-{suffix}", "pc_wei_tang"))
        _commit(root, plan)

    dispatch, overlay = _plan(root, _command(root, "commerce_resolution", {"action": "dispatch", "contract_ref": contract}, "security-auto-dispatch", "pc_wei_tang"))
    assert len(dispatch.result["security_detections"]) == 1
    detection = dispatch.result["security_detections"][0]
    assert detection["route_ref"] == "route_konoha_fire_west"
    security_after = overlay.read_json("state/reg/security-networks.json")
    assert detection["id"] in security_after["detections"]
    alarm = security_after["alarms"][detection["alarm_ref"]]
    assert alarm["status"] == "open"
    info = InformationStore(overlay)
    claims = [ref for ref in info.holder_claim_refs("faction_konoha") if ref.startswith("claim.security.")]
    assert claims
    assert any(info.claim(ref).get("subject_ref", "").startswith("shipment.") for ref in claims if info.claim(ref))


def test_trade_agreement_reduces_real_route_tax_at_commerce_settlement(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    now = RepositoryStore(root).read_json("state/meta.json")["time"]
    governance_path = root / "state/reg/governance.json"
    governance = json.loads(governance_path.read_text())
    governance["jurisdictions"]["jurisdiction.test.konoha_trade_treaty"] = {
        "id": "jurisdiction.test.konoha_trade_treaty",
        "place_ref": "place.konoha",
        "sovereign_ref": "faction_konoha",
        "administration_ref": "faction_konoha",
        "status": "hidden_village",
        "established_at": now,
        "updated_at": now,
        "population_pool_ref": "pool.konoha.civilian_general",
        "treasury_holder_ref": "treasury.konoha",
        "recruitment_rights": True,
        "tax_milli": 100,
        "integration_milli": 1000,
        "resistance_milli": 0,
        "garrison_force_ref": "force.konoha.shinobi",
        "recognition_agreement_refs": [],
        "parent_jurisdiction_ref": None,
        "basis_refs": ["fixture.test.trade_treaty"],
        "visibility": "public",
    }
    governance_path.write_text(json.dumps(governance, indent=2) + "\n")
    diplomacy_path = root / "state/reg/diplomacy.json"
    diplomacy = json.loads(diplomacy_path.read_text())
    diplomacy["agreements"]["agreement.test.konoha_tang_trade"] = {
        "id": "agreement.test.konoha_tang_trade",
        "agreement_type": "trade",
        "party_refs": ["faction_konoha", "house.tang"],
        "status": "active",
        "proposed_by": "house.tang",
        "consent_refs": ["faction_konoha", "house.tang"],
        "rejection_refs": [],
        "terms": ["Preferential route tariff."],
        "opened_at": now,
        "effective_at": now,
        "ended_at": None,
        "evidence_refs": [],
        "visibility": "public",
        "provisions": {"tariff_multiplier_milli": 500, "place_refs": ["place.konoha"], "route_refs": ["route_konoha_fire_west"]},
        "settlement_count": 0,
        "last_settled_at": None,
    }
    diplomacy_path.write_text(json.dumps(diplomacy, indent=2) + "\n")

    contract = "trade.contract.test.treaty_tax"
    steps = [
        ("offer", {
            "action": "offer_contract", "contract_ref": contract,
            "client_ref": "house.tang", "carrier_ref": "pc_wei_tang",
            "source_holder_ref": "pc_wei_tang", "destination_holder_ref": "house.tang",
            "item_ref": "weapon_kunai", "quantity": 2, "unit_price_ryo": 1000,
            "route_ref": "route_konoha_fire_west", "classification": "restricted", "contraband": False,
        }),
        ("accept", {"action": "accept_contract", "contract_ref": contract}),
        ("fund", {"action": "fund_contract", "contract_ref": contract}),
        ("dispatch", {"action": "dispatch", "contract_ref": contract}),
    ]
    for suffix, payload in steps:
        plan, _ = _plan(root, _command(root, "commerce_resolution", payload, f"treaty-tax-{suffix}", "pc_wei_tang"))
        _commit(root, plan)

    deliver, overlay = _plan(root, _command(root, "commerce_resolution", {
        "action": "deliver", "contract_ref": contract,
    }, "treaty-tax-deliver", "pc_wei_tang"))
    assert deliver.result["tax_ryo"] == 100
    assert deliver.result["carrier_net_ryo"] == 1900
    tax = overlay.read_json("state/reg/commerce.json")["contracts"][contract]["tax_settlements"][0]
    assert tax["tax_milli"] == 100
    assert tax["treaty_tariff_multiplier_milli"] == 500
    assert tax["treaty_agreement_refs"] == ["agreement.test.konoha_tang_trade"]
    assert tax["amount_ryo"] == 100
