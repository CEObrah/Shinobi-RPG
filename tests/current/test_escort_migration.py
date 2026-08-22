import copy
import json
from pathlib import Path

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.martial_world.escort_migration import plan_escort_policy_v3_migration
from shinobi_runtime.store import RegisteredSchemaValidator, RegisteredTemplateValidator, RepositoryStore
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionPlanner

ROOT = Path(__file__).resolve().parents[2]
HUASHAN = "contract.3d229e0d9f89e6e306088a4d"
CONTRACTS = "state/martial-world/contracts/index.json"


def load(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def _synthetic_legacy_read():
    market_path = "state/martial-world/markets/temperate_mountain.json"
    market = copy.deepcopy(load(market_path))
    market["cash_pool"] = max(5_000_000, int(market.get("cash_pool", 0)))
    contract = {
        "contract_id": "contract.test.legacy.huashan",
        "contract_type": "escort",
        "issuer_ref": "market:temperate_mountain",
        "beneficiary_ref": "house_tang",
        "status": "accepted",
        "offered_at": "0061-09-13T00:00:00",
        "expires_at": "0061-09-20T00:00:00",
        "escrow_cash": 2_375_471,
        "reward_cash": 2_375_471,
        "objective": {
            "kind": "escort_shipment",
            "route_ref": "route.changan.huashan",
            "source_region": "temperate_mountain",
            "destination_region": "central_plain",
            "item_ref": "food_ration_day",
            "quantity": 7_322_985,
            "cargo_value_cash": 197_720_595,
            "minimum_escort_count": 3,
        },
        "source_ref": "trade_demand_review:temperate_mountain:central_plain:food_ration_day",
        "participants": ["pc_wei_tang"],
    }
    contract_index = {
        "schema": "jianghu-contract-index-1.0",
        "active": {contract["contract_id"]: contract},
        "completed_count": 0,
        "failed_count": 0,
        "expired_count": 0,
    }

    def read(rel: str):
        if rel == CONTRACTS:
            return copy.deepcopy(contract_index)
        if rel == market_path:
            return copy.deepcopy(market)
        return load(rel)

    return read, market_path, contract_index, market


def test_legacy_mega_escort_migrates_to_physical_fair_contract_with_exact_cash_conservation():
    read, market_path, before_index, before_market = _synthetic_legacy_read()
    result = plan_escort_policy_v3_migration(read)
    assert result["migrated_contract_refs"] == ["contract.test.legacy.huashan"]
    assert result["accepted_contract_refs"] == ["contract.test.legacy.huashan"]
    assert result["refund_cash"] > 0
    assert result["market_cash_delta"] + result["escrow_delta"] == 0
    assert set(result["writes"]) == {CONTRACTS, market_path}

    after = result["writes"][CONTRACTS]["active"]["contract.test.legacy.huashan"]
    objective = after["objective"]
    assert after["contract_id"] == before_index["active"]["contract.test.legacy.huashan"]["contract_id"]
    assert after["status"] == "accepted"
    assert after["beneficiary_ref"] == "house_tang"
    assert after["participants"] == ["pc_wei_tang"]
    assert objective["escort_policy_version"] == 3
    assert objective["quantity"] == 12_000
    assert objective["cargo_mass_kg"] == 12_000
    assert objective["wagon_count"] == 10
    assert objective["draft_animal_count"] == 20
    assert objective["civilian_crew_count"] > 0
    assert objective["minimum_escort_count"] >= 2
    assert 5_000 <= after["reward_cash"] < 50_000
    assert after["escrow_cash"] == after["reward_cash"]

    before_cash = int(before_market["cash_pool"]) + int(before_index["active"]["contract.test.legacy.huashan"]["escrow_cash"])
    after_cash = int(result["writes"][market_path]["cash_pool"]) + int(after["escrow_cash"])
    assert after_cash == before_cash
    assert "state/meta.json" not in result["writes"]
    assert "state/martial-world/scheduler.json" not in result["writes"]
    assert "state/martial-world/route-operations.json" not in result["writes"]


def test_escort_migration_is_idempotent_after_first_after_image():
    read, _market_path, _before_index, _before_market = _synthetic_legacy_read()
    first = plan_escort_policy_v3_migration(read)

    def read_after(rel: str):
        if rel in first["writes"]:
            return copy.deepcopy(first["writes"][rel])
        return read(rel)

    second = plan_escort_policy_v3_migration(read_after)
    assert second["writes"] == {}
    assert second["migrated_contract_refs"] == []
    assert second["refund_cash"] == 0
    assert second["topup_cash"] == 0


def test_current_huashan_acceptance_survives_policy_migration_or_is_already_migrated():
    before = load(CONTRACTS)
    original = before["active"][HUASHAN]
    result = plan_escort_policy_v3_migration(load)
    after_index = result["writes"].get(CONTRACTS, before)
    after = after_index["active"][HUASHAN]

    assert after["contract_id"] == HUASHAN
    assert after["status"] == "accepted"
    assert after["beneficiary_ref"] == original["beneficiary_ref"] == "house_tang"
    assert after["participants"] == original["participants"]
    assert after["offered_at"] == original["offered_at"]
    assert after["expires_at"] == original["expires_at"]
    assert after["source_ref"] == original["source_ref"]
    assert int(after["objective"].get("escort_policy_version", 0)) >= 3
    assert after["objective"]["quantity"] <= 12_000
    assert after["objective"]["wagon_count"] > 0
    assert after["reward_cash"] < 50_000
    assert after["escrow_cash"] == after["reward_cash"]
    if int(original.get("reward_cash", 0)) > after["reward_cash"]:
        assert result["refund_cash"] > 0


def test_current_migration_after_images_pass_registered_schema_and_template_contracts():
    repository = RepositoryStore(ROOT)
    result = plan_escort_policy_v3_migration(repository.read_json)
    if not result["writes"]:
        # State-only deployment maintenance may already have committed v3. In
        # that case idempotence itself is the relevant current-state assertion.
        return
    meta = repository.read_json("state/meta.json")
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="test.escort-policy-v3.maintenance",
        actor_id=meta["player_id"],
        command_type="maintenance_escort_policy_v3",
        expected_revision=meta["revision"],
        submitted_at="2026-08-22T00:00:00Z",
        mode="maintenance",
        payload={"migration": "escort_policy_v3"},
    )
    writes = {
        path: (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        for path, value in result["writes"].items()
    }
    manifest = TransactionPlanner(repository).plan(
        command,
        transaction_id="tx.maintenance.test.escort-policy-v3",
        created_at="2026-08-22T00:00:00Z",
        writes=writes,
    )
    overlay = StagedOverlay(repository, manifest)
    paths = tuple(sorted(writes))
    RegisteredSchemaValidator(repository).validate_overlay(overlay, paths)
    RegisteredTemplateValidator(repository).validate_overlay(overlay, paths)
