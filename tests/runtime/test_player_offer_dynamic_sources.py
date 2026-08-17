from __future__ import annotations

import json
import shutil
from pathlib import Path

from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


def _copy_campaign(tmp_path: Path) -> Path:
    root = tmp_path / "campaign"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc"))
    return root


def _planner(root: Path) -> CampaignCommandPlanner:
    return CampaignCommandPlanner(RepositoryStore(root))


def test_dynamic_offer_lanes_fail_closed_without_persisted_sources(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    planner = _planner(root)
    candidates = dict(planner._player_offer_demand_candidates("faction.konoha_mission_office"))
    for key in ("case_investigation", "convoy_security", "counter_smuggling", "security_response"):
        assert key not in candidates


def test_dynamic_case_offer_requires_real_evidence_backed_case(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    legal_path = root / "state/reg/legal-cases.json"
    legal = json.loads(legal_path.read_text())
    legal["cases"]["case.test.fire"] = {
        "id": "case.test.fire",
        "case_kind": "smuggling_investigation",
        "issuer_ref": "faction.fire_border_authority",
        "subject_ref": "faction.fire_smuggling_network",
        "requester_ref": "faction.fire_border_authority",
        "status": "open",
        "opened_at": "SE-0061-06-01T00:00:00",
        "updated_at": "SE-0061-06-01T00:00:00",
        "summary": "Evidence-backed route irregularity.",
        "visibility": "restricted",
        "offense_refs": ["offense.smuggling"],
        "evidence_refs": ["event.test.evidence"],
        "warrant": {"status": "none", "authority_ref": None, "issued_at": None},
        "bounty": {"status": "none", "payer_ref": None, "payer_holder_ref": None, "escrow_holder_ref": None, "amount_ryo": 0, "hunter_refs": [], "posted_at": None, "verified_evidence_refs": [], "settled_at": None},
        "custody_ref": None,
        "disposition": None,
    }
    legal_path.write_text(json.dumps(legal, indent=2) + "\n")

    planner = _planner(root)
    candidates = dict(planner._player_offer_demand_candidates("faction.konoha_mission_office"))
    assert candidates["case_investigation"] == "investigate"
    template = planner._dynamic_player_offer_briefing_config(
        faction_id="faction.konoha_mission_office",
        objective_kind="investigate",
        demand_key="case_investigation",
        source_kind="legal_case",
    )
    assert template["subject_kind"] == "case"
    assert template["subject_ref"] == "case.test.fire"
    assert template["destination_place_ref"] is None
    assert "does not reveal" in template["intelligence_constraints"][1]


def test_dynamic_convoy_offer_uses_actual_registered_shipment_and_route(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    commerce_path = root / "state/reg/commerce.json"
    commerce = json.loads(commerce_path.read_text())
    commerce["contracts"]["trade.contract.test.convoy"] = {
        "id": "trade.contract.test.convoy", "status": "in_transit",
        "client_ref": "aggregate.trade.waves_town", "carrier_ref": "faction.fire_merchant_network",
        "source_holder_ref": "stock.market.konoha", "destination_holder_ref": "aggregate.trade.waves_town",
        "item_ref": "item_field_ration_1day", "quantity": 25, "unit_price_ryo": 100,
        "total_ryo": 2500, "escrow_holder_ref": "escrow.trade.test.convoy",
        "route_ref": "route_konoha_wave", "contraband": False,
        "opened_at": "SE-0061-06-01T00:00:00", "accepted_at": "SE-0061-06-01T00:00:00",
        "funded_at": "SE-0061-06-01T00:00:00", "dispatched_at": "SE-0061-06-01T00:00:00",
        "completed_at": None, "shipment_ref": "shipment.test.convoy", "classification": "public",
    }
    commerce["shipments"]["shipment.test.convoy"] = {
        "id": "shipment.test.convoy", "contract_ref": "trade.contract.test.convoy",
        "cargo_holder_ref": "cargo.shipment.test.convoy", "item_ref": "item_field_ration_1day",
        "quantity": 25, "route_ref": "route_konoha_wave", "origin_holder_ref": "stock.market.konoha",
        "destination_holder_ref": "aggregate.trade.waves_town", "custodian_ref": "faction.fire_merchant_network",
        "status": "in_transit", "dispatched_at": "SE-0061-06-01T00:00:00", "delivered_at": None,
        "seized_at": None, "classification": "public",
    }
    commerce_path.write_text(json.dumps(commerce, indent=2) + "\n")

    planner = _planner(root)
    candidates = dict(planner._player_offer_demand_candidates("faction.konoha_mission_office"))
    assert candidates["convoy_security"] == "protect"
    template = planner._dynamic_player_offer_briefing_config(
        faction_id="faction.konoha_mission_office",
        objective_kind="protect",
        demand_key="convoy_security",
        source_kind="lawful_shipment",
    )
    assert template["subject_ref"] == "shipment.test.convoy"
    assert template["route_id"] == "route_konoha_wave"
    assert template["origin_place_ref"] == "place.konoha"
    assert template["destination_place_ref"] == "place.waves.town"
