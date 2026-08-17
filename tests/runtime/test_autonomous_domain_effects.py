from __future__ import annotations

import copy
from pathlib import Path
import shutil

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.store import RegisteredSchemaValidator, RegisteredTemplateValidator, RepositoryStore
from shinobi_runtime.sim.events import CampaignTime

ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_ID = "shinobi-wei-main"
SUBMITTED = "2026-08-14T00:00:00Z"


def _copy_campaign(tmp_path: Path) -> Path:
    campaign = tmp_path / "campaign"
    shutil.copytree(ROOT, campaign, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc"))
    return campaign


def _command(repo: RepositoryStore, suffix: str) -> CommandEnvelope:
    meta = repo.read_json("state/meta.json")
    return CommandEnvelope(
        campaign_id=CAMPAIGN_ID, request_id=f"autonomous-effect-{suffix}-{meta['revision']}",
        actor_id="pc_wei_tang", command_type="advance_time", expected_revision=meta["revision"],
        submitted_at=SUBMITTED, payload={"target_time": meta["time"]}, mode="gameplay",
    )


def _template_validate(repo: RepositoryStore, writes: dict[str, dict]) -> None:
    templates = RegisteredTemplateValidator(repo)
    schemas = RegisteredSchemaValidator(repo)
    for path, row in writes.items():
        if not path.endswith(".json") or not isinstance(row, dict) or not isinstance(row.get("schema"), str):
            continue
        schema = row["schema"]
        template = templates.templates.get(schema)
        if template is not None:
            templates._validate_document(row, template, label=path)
        validator = schemas.validators.get(schema)
        if validator is not None:
            validator.validate(row)


def _operation(planner: CampaignCommandPlanner, faction_ref: str, template_id: str, at: CampaignTime) -> dict:
    program = planner._institutional_program(faction_ref)
    template = next(row for row in program["operation_templates"] if row["id"] == template_id)
    return planner._operation_spec_from_template(
        faction_id=faction_ref,
        actor=program.get("default_actor_ref") or {
            "faction.bounty_broker_network": "support.bounty.goro_kaneda",
            "faction.kiri_hunter_office": "support.kiri.hunter_captain",
            "faction.oto_research_cells": "canon_orochimaru",
            "faction.ame_security_network": "canon_nagato",
            "faction.konoha_mission_office": "canon_hiruzen",
        }[faction_ref],
        at=at, template=template,
    )


def _apply(root: Path, operation: dict, suffix: str):
    repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    writes: dict[str, dict] = {}
    world_events = copy.deepcopy(planner._world_events())
    _rule, blocked = planner._autonomous_effect_preflight(operation, record_writes=writes)
    assert blocked is None
    event_ref = repo.read_json("state/reg/world-events.json")["events"][-1]["id"]
    result = planner._apply_autonomous_operation_effect(
        operation=operation, faction_id=str(operation["owner_ref"]),
        actor=str(operation["authority_ref"]), at=at,
        evidence_event_ref=event_ref, record_writes=writes,
    )
    assert result["status"] == "applied"
    _template_validate(repo, writes)
    return writes, result["refs"]


def test_bounty_and_hunter_operations_create_exact_legal_cases(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path); planner = CampaignCommandPlanner(RepositoryStore(root))
    at = CampaignTime.parse(RepositoryStore(root).read_json("state/meta.json")["time"])
    bounty = _operation(planner, "faction.bounty_broker_network", "bounty.verify_target", at)
    writes, refs = _apply(root, bounty, "bounty")
    case_ref = next(ref for ref in refs if ref.startswith("case."))
    case = writes["state/reg/legal-cases.json"]["cases"][case_ref]
    assert case["status"] == "open"
    assert case["bounty"]["amount_ryo"] == 0
    assert case["evidence_refs"]

    hunter = _operation(planner, "faction.kiri_hunter_office", "kiri.missing_nin_pursuit", at)
    writes, refs = _apply(root, hunter, "hunter")
    case_ref = next(ref for ref in refs if ref.startswith("case."))
    case = writes["state/reg/legal-cases.json"]["cases"][case_ref]
    assert case["status"] == "warranted"
    assert case["warrant"]["status"] == "active"


def test_research_operation_consumes_stock_and_persists_exact_project(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path); repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    op = _operation(planner, "faction.oto_research_cells", "oto.experiment", at)
    before = repo.read_json("state/stock/oto-network.json")["field_packs"]
    writes, refs = _apply(root, op, "research")
    project_ref = next(ref for ref in refs if ref.startswith("research."))
    assert writes["state/stock/oto-network.json"]["field_packs"] == before - 1
    project = writes["state/reg/research.json"]["projects"][project_ref]
    assert project["status"] == "active"
    assert 0 < project["progress_milli"] < 1000
    assert project["evidence_refs"]
    assert project["result_claim_refs"] == []


def test_security_operation_consumes_real_network_stock_when_establishing_new_sector(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path); repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    op = _operation(planner, "faction.ame_security_network", "ame.sector_surveillance", at)

    # The shipped campaign already contains this exact security sector. Remove
    # only that derived sector in the disposable test copy so this regression
    # exercises first establishment rather than charging materials twice for a
    # routine review.
    registry_path = root / "state/reg/security-networks.json"
    registry = __import__("json").loads(registry_path.read_text(encoding="utf-8"))
    place_ref = op["place_refs"][0]
    owner_ref = op["owner_ref"]
    import hashlib
    sector_ref = f"security.autonomy.{hashlib.sha256(f'{owner_ref}\x00{place_ref}'.encode()).hexdigest()[:20]}"
    registry["sectors"].pop(sector_ref, None)
    registry_path.write_text(__import__("json").dumps(registry, indent=2) + "\n", encoding="utf-8")

    repo = RepositoryStore(root)
    before = repo.read_json("state/stock/ame-shinobi.json")
    writes, refs = _apply(root, op, "security")
    after = writes["state/stock/ame-shinobi.json"]
    assert after["barrier_anchor_sets"] == before["barrier_anchor_sets"] - 1
    assert after["sensor_relays"] == before["sensor_relays"] - 1
    assert sector_ref in refs
    assert writes["state/reg/security-networks.json"]["sectors"][sector_ref]["status"] == "active"


def test_existing_security_sector_review_does_not_consume_establishment_stock_again(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path); repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    op = _operation(planner, "faction.ame_security_network", "ame.sector_surveillance", at)
    before = repo.read_json("state/stock/ame-shinobi.json")
    writes, refs = _apply(root, op, "security-review")
    assert "state/stock/ame-shinobi.json" not in writes
    assert repo.read_json("state/stock/ame-shinobi.json") == before
    sector_ref = next(ref for ref in refs if ref.startswith("security."))
    assert writes["state/reg/security-networks.json"]["sectors"][sector_ref]["status"] == "active"


def test_mission_office_operation_updates_market_only_from_evidence(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path); repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    op = _operation(planner, "faction.konoha_mission_office", "mission.market_review", at)
    before = repo.read_json("state/reg/mission-markets.json")["markets"]["market_konoha_missions"]["demand_scores"]["investigation"]
    writes, refs = _apply(root, op, "market")
    market = writes["state/reg/mission-markets.json"]["markets"]["market_konoha_missions"]
    assert market["demand_scores"]["investigation"] == min(1000, before + 50)
    assert market["evidence_refs"]


def _persist_writes(root: Path, writes: dict[str, dict]) -> None:
    for relative, row in writes.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(__import__('json').dumps(row, indent=2) + "\n", encoding="utf-8")


def test_merchant_caravan_moves_conserved_market_stock_into_real_shipment_and_escrow(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path); repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    program = planner._institutional_program("faction.fire_merchant_network")
    template = next(row for row in program["operation_templates"] if row["id"] == "merchant.caravan_contract")
    op = planner._operation_spec_from_template(
        faction_id="faction.fire_merchant_network", actor="support.merchant.fumiko_takahara", at=at, template=template,
    )
    op["subject_kind"] = "route"; op["subject_ref"] = "route_konoha_wave"; op["route_refs"] = ["route_konoha_wave"]
    before_stock = repo.read_json("state/stock/market-konoha.json")["items"]
    before_money = repo.read_json("state/inventory/registry.json")["holders"]["economy.land_waves.private"]["currency.ryo"]
    writes, refs = _apply(root, op, "merchant")
    contract_ref = next(ref for ref in refs if ref.startswith("trade.contract."))
    shipment_ref = next(ref for ref in refs if ref.startswith("shipment."))
    contract = writes["state/reg/commerce.json"]["contracts"][contract_ref]
    shipment = writes["state/reg/commerce.json"]["shipments"][shipment_ref]
    item_ref = shipment["item_ref"]; quantity = shipment["quantity"]
    assert writes["state/stock/market-konoha.json"]["items"][item_ref] == before_stock[item_ref] - quantity
    assert shipment["status"] == "in_transit"
    assert shipment["route_ref"] == "route_konoha_wave"
    assert contract["destination_holder_ref"] == "aggregate.trade.waves_town"
    inventory = writes["state/inventory/registry.json"]["holders"]
    assert inventory["economy.land_waves.private"]["currency.ryo"] == before_money - contract["total_ryo"]
    assert inventory[contract["escrow_holder_ref"]]["currency.ryo"] == contract["total_ryo"]


def test_due_autonomous_caravan_delivers_and_releases_escrow_without_item_duplication(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path); repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    program = planner._institutional_program("faction.fire_merchant_network")
    template = next(row for row in program["operation_templates"] if row["id"] == "merchant.caravan_contract")
    op = planner._operation_spec_from_template(
        faction_id="faction.fire_merchant_network", actor="support.merchant.fumiko_takahara", at=at, template=template,
    )
    op["subject_kind"] = "route"; op["subject_ref"] = "route_konoha_wave"; op["route_refs"] = ["route_konoha_wave"]
    writes, refs = _apply(root, op, "merchant-deliver")
    shipment_ref = next(ref for ref in refs if ref.startswith("shipment."))
    contract_ref = next(ref for ref in refs if ref.startswith("trade.contract."))
    _persist_writes(root, writes)
    repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    later = at.add_seconds(8 * 24 * 60 * 60)
    staged: dict[str, dict] = {}
    world_events = copy.deepcopy(planner._world_events())
    settled = planner._settle_due_autonomous_shipments(
        faction_id="faction.fire_merchant_network", at=later, command=_command(repo, "merchant-deliver"),
        world_events=world_events, record_writes=staged,
    )
    assert any(row.get("shipment_ref") == shipment_ref and row.get("status") == "delivered" for row in settled)
    commerce = staged["state/reg/commerce.json"]
    assert commerce["shipments"][shipment_ref]["status"] == "delivered"
    assert commerce["contracts"][contract_ref]["status"] == "delivered"
    escrow_ref = commerce["contracts"][contract_ref]["escrow_holder_ref"]
    holders = staged["state/inventory/registry.json"]["holders"]
    assert holders.get(escrow_ref, {}).get("currency.ryo", 0) == 0
    assert commerce["route_metrics"]["route_konoha_wave"]["delivered_count"] == 1


def test_border_audit_can_only_inspect_real_in_transit_cargo_and_does_not_seize_legal_goods(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path); repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    merchant_program = planner._institutional_program("faction.fire_merchant_network")
    merchant_template = next(row for row in merchant_program["operation_templates"] if row["id"] == "merchant.caravan_contract")
    merchant = planner._operation_spec_from_template(
        faction_id="faction.fire_merchant_network", actor="support.merchant.fumiko_takahara", at=at, template=merchant_template,
    )
    merchant["subject_kind"] = "route"; merchant["subject_ref"] = "route_konoha_wave"; merchant["route_refs"] = ["route_konoha_wave"]
    writes, refs = _apply(root, merchant, "merchant-border")
    shipment_ref = next(ref for ref in refs if ref.startswith("shipment."))
    _persist_writes(root, writes)
    repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    border_program = planner._institutional_program("faction.fire_border_authority")
    border_template = next(row for row in border_program["operation_templates"] if row["id"] == "border.crossing_audit")
    border = planner._operation_spec_from_template(
        faction_id="faction.fire_border_authority", actor="support.border.kazuo_murata", at=at, template=border_template,
    )
    border["subject_kind"] = "route"; border["subject_ref"] = "route_konoha_wave"; border["route_refs"] = ["route_konoha_wave"]
    staged: dict[str, dict] = {}
    rule, blocked = planner._autonomous_effect_preflight(border, record_writes=staged)
    assert rule and blocked is None
    event_ref = repo.read_json("state/reg/world-events.json")["events"][-1]["id"]
    result = planner._apply_autonomous_operation_effect(
        operation=border, faction_id="faction.fire_border_authority", actor="support.border.kazuo_murata", at=at,
        evidence_event_ref=event_ref, record_writes=staged,
    )
    assert result["status"] == "applied"
    crossing_ref = next(ref for ref in result["refs"] if ref.startswith("crossing."))
    commerce = staged["state/reg/commerce.json"]
    assert commerce["crossings"][crossing_ref]["result"] == "cleared"
    assert commerce["shipments"][shipment_ref]["status"] == "in_transit"


def test_smuggling_run_procures_real_gray_market_cargo_before_contraband_dispatch(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path); repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    program = planner._institutional_program("faction.fire_smuggling_network")
    template = next(row for row in program["operation_templates"] if row["id"] == "smuggling.contraband_run")
    op = planner._operation_spec_from_template(
        faction_id="faction.fire_smuggling_network", actor="support.smuggling.reina_kurosawa", at=at, template=template,
    )
    op["subject_kind"] = "route"; op["subject_ref"] = "route_konoha_wave"; op["route_refs"] = ["route_konoha_wave"]
    before_market = repo.read_json("state/stock/market-konoha.json")["items"]
    before_cache = repo.read_json("state/stock/fire-smuggling-konoha-cache.json")["items"]
    assert before_cache and all(value == 0 for value in before_cache.values())
    writes, refs = _apply(root, op, "smuggling-procurement")
    shipment_ref = next(ref for ref in refs if ref.startswith("shipment."))
    contract_ref = next(ref for ref in refs if ref.startswith("trade.contract."))
    shipment = writes["state/reg/commerce.json"]["shipments"][shipment_ref]
    contract = writes["state/reg/commerce.json"]["contracts"][contract_ref]
    assert shipment["status"] == "in_transit"
    assert shipment["route_ref"] == "route_konoha_wave"
    assert contract["contraband"] is True
    assert contract["source_holder_ref"] == "stock.fire_smuggling_network.konoha_cache"
    item_ref = shipment["item_ref"]; quantity = shipment["quantity"]
    assert writes["state/stock/market-konoha.json"]["items"][item_ref] == before_market[item_ref] - quantity
    # Procurement enters the real cache and dispatch removes the same quantity
    # in one transaction; the cache is storage authority, not free starting stock.
    assert writes["state/stock/fire-smuggling-konoha-cache.json"]["items"].get(item_ref, 0) == 0
    assert writes["state/inventory/registry.json"]["holders"][contract["escrow_holder_ref"]]["currency.ryo"] == contract["total_ryo"]


def test_verified_bounty_request_can_be_funded_by_requesting_authority_then_accepted_by_eligible_hunter_team(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path); repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    # Broker verifies a concrete target for Kiri; verification itself spends no Kiri money.
    program = planner._institutional_program("faction.bounty_broker_network")
    template = next(row for row in program["operation_templates"] if row["id"] == "bounty.verify_target")
    verify = planner._operation_spec_from_template(
        faction_id="faction.bounty_broker_network", actor="support.bounty.goro_kaneda", at=at, template=template,
    )
    verify["subject_ref"] = "canon_zabuza"; verify["client_ref"] = "faction.kiri_hunter_office"
    writes, refs = _apply(root, verify, "bounty-verify-chain")
    case_ref = next(ref for ref in refs if ref.startswith("case."))
    case = writes["state/reg/legal-cases.json"]["cases"][case_ref]
    assert case["requester_ref"] == "faction.kiri_hunter_office"
    assert case["bounty"]["status"] == "none"
    _persist_writes(root, writes)

    # Kiri's own autonomous authority may choose to fund the verified request.
    repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    program = planner._institutional_program("faction.kiri_hunter_office")
    template = next(row for row in program["operation_templates"] if row["id"] == "kiri.bounty_funding_review")
    fund = planner._operation_spec_from_template(
        faction_id="faction.kiri_hunter_office", actor="canon_ao", at=at, template=template,
    )
    assert fund["subject_ref"] == case_ref
    treasury_before = repo.read_json("state/inventory/registry.json")["holders"]["treasury.kiri"]["currency.ryo"]
    staged, refs = _apply(root, fund, "bounty-fund-chain")
    funded = staged["state/reg/legal-cases.json"]["cases"][case_ref]
    assert funded["bounty"]["status"] == "posted"
    assert funded["bounty"]["payer_ref"] == "faction.kiri_hunter_office"
    assert funded["bounty"]["payer_holder_ref"] == "treasury.kiri"
    assert funded["bounty"]["amount_ryo"] > 0
    holders = staged["state/inventory/registry.json"]["holders"]
    assert holders["treasury.kiri"]["currency.ryo"] == treasury_before - funded["bounty"]["amount_ryo"]
    assert holders[funded["bounty"]["escrow_holder_ref"]]["currency.ryo"] == funded["bounty"]["amount_ryo"]
    _persist_writes(root, staged)

    # Acceptance belongs to the hunter organization, not the broker. It selects only a real Kiri hunter team.
    repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    template = next(row for row in planner._institutional_program("faction.kiri_hunter_office")["operation_templates"] if row["id"] == "kiri.bounty_acceptance")
    accept = planner._operation_spec_from_template(
        faction_id="faction.kiri_hunter_office", actor="canon_ao", at=at, template=template,
    )
    assert accept["subject_ref"] == case_ref
    staged, refs = _apply(root, accept, "bounty-accept-chain")
    bounty = staged["state/reg/legal-cases.json"]["cases"][case_ref]["bounty"]
    assert bounty["hunter_refs"] == ["team.kiri.hunter.office.auto.a29a55f55d97"]


def test_yamanaka_interrogation_requires_real_detained_case_and_persists_custody_into_analysis(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    # Establish one exact detention and link it to one legal case.  The
    # autonomous operation may analyze that custody, but may not invent a
    # detainee or silently discard the custody basis after starting research.
    custody_path = root / "state/reg/custody.json"
    custody = __import__('json').loads(custody_path.read_text())
    custody_ref = "custody.test.kabuto"
    now = "SE-0061-06-11T07:00:00"
    custody["records"][custody_ref] = {
        "id": custody_ref, "subject_kind": "exact", "subject_ref": "canon_kabuto",
        "force_ref": None, "count": 1, "custodian_ref": "faction.yamanaka_intelligence",
        "place_ref": "place.konoha.interrogation", "status": "detained",
        "captured_at": now, "detained_at": now, "updated_at": now,
        "summary": "Test detention for interrogation provenance.", "visibility": "secret",
        "source_combat_ref": None,
    }
    custody_path.write_text(__import__('json').dumps(custody, indent=2) + "\n")
    legal_path = root / "state/reg/legal-cases.json"
    legal = __import__('json').loads(legal_path.read_text())
    case_ref = "case.test.kabuto.interrogation"
    legal["cases"][case_ref] = {
        "id": case_ref, "case_kind": "counterintelligence", "issuer_ref": "faction.yamanaka_intelligence",
        "subject_ref": "canon_kabuto", "requester_ref": "faction.yamanaka_intelligence",
        "status": "open", "opened_at": now, "updated_at": now,
        "summary": "Test counterintelligence case.", "visibility": "secret",
        "offense_refs": [], "evidence_refs": [],
        "warrant": {"status": "none", "authority_ref": None, "issued_at": None},
        "bounty": {"status": "none", "payer_ref": None, "payer_holder_ref": None, "escrow_holder_ref": None,
                   "amount_ryo": 0, "hunter_refs": [], "posted_at": None, "verified_evidence_refs": [], "settled_at": None},
        "custody_ref": custody_ref, "disposition": None,
    }
    legal_path.write_text(__import__('json').dumps(legal, indent=2) + "\n")

    repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    program = planner._institutional_program("faction.yamanaka_intelligence")
    template = next(row for row in program["operation_templates"] if row["id"] == "yamanaka.interrogation")
    op = planner._operation_spec_from_template(
        faction_id="faction.yamanaka_intelligence", actor="canon_inoichi", at=at, template=template,
    )
    assert op["subject_ref"] == case_ref
    writes, refs = _apply(root, op, "yamanaka-custody")
    project_ref = next(ref for ref in refs if ref.startswith("research."))
    project = writes["state/reg/research.json"]["projects"][project_ref]
    assert project["subject_ref"] == "canon_kabuto"
    assert project["custody_ref"] == custody_ref
    assert project["status"] == "active"


def test_yamanaka_interrogation_blocks_when_case_has_no_active_custody(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path); repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    program = planner._institutional_program("faction.yamanaka_intelligence")
    template = next(row for row in program["operation_templates"] if row["id"] == "yamanaka.interrogation")
    # No legal case/custody currently exists in the baseline, so the operation
    # has no lawful subject rather than inventing somebody to interrogate.
    op = planner._operation_spec_from_template(
        faction_id="faction.yamanaka_intelligence", actor="canon_inoichi", at=at, template=template,
    )
    assert op["subject_ref"] is None
    rule, blocked = planner._autonomous_effect_preflight(op, record_writes={})
    assert rule is not None
    assert blocked == "custody interrogation requires a detained legal case"


def test_counter_smuggling_seizure_opens_evidence_backed_legal_case(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path); repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    smuggling_program = planner._institutional_program("faction.fire_smuggling_network")
    smuggling_template = next(row for row in smuggling_program["operation_templates"] if row["id"] == "smuggling.contraband_run")
    smuggling = planner._operation_spec_from_template(
        faction_id="faction.fire_smuggling_network", actor="support.smuggling.reina_kurosawa", at=at, template=smuggling_template,
    )
    smuggling["subject_kind"] = "route"; smuggling["subject_ref"] = "route_konoha_wave"; smuggling["route_refs"] = ["route_konoha_wave"]
    writes, refs = _apply(root, smuggling, "smuggling-seize-source")
    shipment_ref = next(ref for ref in refs if ref.startswith("shipment."))
    _persist_writes(root, writes)

    repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    border_program = planner._institutional_program("faction.fire_border_authority")
    border_template = next(row for row in border_program["operation_templates"] if row["id"] == "border.counter_smuggling")
    border = planner._operation_spec_from_template(
        faction_id="faction.fire_border_authority", actor="support.border.kazuo_murata", at=at, template=border_template,
    )
    border["subject_kind"] = "route"; border["subject_ref"] = "route_konoha_wave"; border["route_refs"] = ["route_konoha_wave"]
    staged: dict[str, dict] = {}
    rule, blocked = planner._autonomous_effect_preflight(border, record_writes=staged)
    assert rule and blocked is None
    event_ref = repo.read_json("state/reg/world-events.json")["events"][-1]["id"]
    result = planner._apply_autonomous_operation_effect(
        operation=border, faction_id="faction.fire_border_authority", actor="support.border.kazuo_murata", at=at,
        evidence_event_ref=event_ref, record_writes=staged,
    )
    assert result["status"] == "applied"
    case_ref = next(ref for ref in result["refs"] if ref.startswith("case.smuggling."))
    case = staged["state/reg/legal-cases.json"]["cases"][case_ref]
    assert case["case_kind"] == "smuggling_seizure"
    assert case["subject_ref"] == "faction.fire_smuggling_network"
    assert case["evidence_refs"] == [event_ref]
    assert case["warrant"]["status"] == "none"  # seizure evidence is not automatic arrest authority
    assert staged["state/reg/commerce.json"]["shipments"][shipment_ref]["status"] == "seized"
