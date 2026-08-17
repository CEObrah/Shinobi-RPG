from __future__ import annotations

import json
from pathlib import Path

from shinobi_runtime.api import campaign_stable_operations as stable
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.diplomacy import (
    border_route_restriction,
    client_state_access_basis,
    defense_obligation_specs,
    hostility_barrier,
    trade_tariff_multiplier_milli,
)
from shinobi_runtime.information import InformationStore
from shinobi_runtime.membership_routes import stage_house_change, stage_team_change
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store import RepositoryStore


def _write_json(root: Path, relative: str, value: object) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def test_house_access_remains_exact_beyond_legacy_512_member_ceiling(tmp_path: Path) -> None:
    members = ["pc_wei_tang"] + [f"person.house.member.{idx:04d}" for idx in range(700)]
    _write_json(tmp_path, "state/index/owners.json", {
        "prefix_index": {"house": "state/index/owners/house.json"},
    })
    _write_json(tmp_path, "state/index/owners/house.json", {
        "owners": {"house.tang": "state/house/tang.json"},
    })
    _write_json(tmp_path, "state/house/tang.json", {
        "schema": "house",
        "id": "house.tang",
        "member_ids": members,
    })
    route_writes: dict[str, dict] = {}
    stage_house_change(
        RepositoryStore(tmp_path), route_writes, house_ref="house.tang",
        after_members=tuple(members),
    )
    for relative, record in route_writes.items():
        _write_json(tmp_path, relative, record)
    operations = stable.RouteAwareCampaignOperations.__new__(stable.RouteAwareCampaignOperations)
    operations.repository = RepositoryStore(tmp_path)

    window = operations._player_house_member_ids("pc_wei_tang", limit=32)
    assert len(window) == 32
    assert operations._player_is_house_peer(
        player_id="pc_wei_tang", person_id="person.house.member.0699"
    ) is True
    assert operations._player_can_read_person(
        player_id="pc_wei_tang", person_id="person.house.member.0699"
    ) is True


def test_player_team_discovery_treats_128_as_output_window_not_world_ceiling(tmp_path: Path) -> None:
    team_refs = [f"team.player.{idx:03d}" for idx in range(129)]
    _write_json(tmp_path, "state/team/registry.json", {
        "active_teams": team_refs,
    })
    _write_json(tmp_path, "state/index/owners.json", {
        "prefix_index": {"team": "state/index/owners/team.json"},
    })
    owners = {}
    for idx, team_ref in enumerate(team_refs):
        path = f"state/team/player-{idx:03d}.json"
        owners[team_ref] = path
        _write_json(tmp_path, path, {
            "schema": "exact-team",
            "id": team_ref,
            "status": "active",
            "member_refs": ["pc_wei_tang", f"person.peer.{idx:03d}"],
        })
    _write_json(tmp_path, "state/index/owners/team.json", {"owners": owners})
    route_writes: dict[str, dict] = {}
    repo = RepositoryStore(tmp_path)
    for idx, team_ref in enumerate(team_refs):
        stage_team_change(
            repo, route_writes, team_ref=team_ref,
            after_members=("pc_wei_tang", f"person.peer.{idx:03d}"),
        )
    for relative, record in route_writes.items():
        _write_json(tmp_path, relative, record)
    operations = stable.RouteAwareCampaignOperations.__new__(stable.RouteAwareCampaignOperations)
    operations.repository = RepositoryStore(tmp_path)

    refs = operations._player_exact_team_refs("pc_wei_tang")
    assert len(refs) == 128
    assert operations._player_exact_team_ref_count("pc_wei_tang") == 129


def test_promotion_exam_team_registry_has_no_legacy_256_world_ceiling() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "runtime/shinobi_runtime/commands/promotion_exam_cycle.py"
    ).read_text(encoding="utf-8")
    assert "_MAX_TEAM_SCAN" not in source
    assert "len(refs) >" not in source


def test_formation_inspection_routes_exactly_beyond_legacy_registry_and_per_registry_caps(tmp_path: Path) -> None:
    registries = {
        f"force.test.{idx:03d}": f"state/formation/force-test-{idx:03d}.json"
        for idx in range(65)
    }
    target_force = "force.test.064"
    target_path = registries[target_force]
    formations = [
        {"id": f"formation.test.{idx:04d}", "force_ref": target_force}
        for idx in range(513)
    ]
    target_ref = formations[-1]["id"]
    _write_json(tmp_path, target_path, {
        "schema": "formation-registry",
        "force_ref": target_force,
        "formations": formations,
    })
    _write_json(tmp_path, "state/formation/index.json", {
        "schema": "formation-registry-index",
        "authority": False,
        "registries": registries,
        "formation_routes": {
            target_ref: {"force_ref": target_force, "registry_path": target_path},
        },
    })
    operations = stable.RouteAwareCampaignOperations.__new__(stable.RouteAwareCampaignOperations)
    operations.repository = RepositoryStore(tmp_path)

    path, formation = operations._formation_record(target_ref)
    assert path == target_path
    assert formation["id"] == target_ref


def test_subject_information_query_reads_one_bounded_subject_shard_not_lifetime_buckets() -> None:
    holder = "canon_hiruzen"
    subject = "faction.oto_research_cells"
    path = InformationStore.subject_shard_path(holder, subject)
    holder_hash = InformationStore.holder_hash(holder)
    bucket = path.rsplit("subject-", 1)[1].split(".", 1)[0]
    payload = {
        "schema": "information-knowledge-subject-shard",
        "holder_ref": holder,
        "holder_hash": holder_hash,
        "bucket": bucket,
        "subjects": {subject: [f"claim.test.{idx:03d}" for idx in range(64)]},
    }

    class Repo:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def read_optional_bytes(self, relative: str):
            self.calls.append(relative)
            if relative != path:
                raise AssertionError(f"bounded subject query touched unrelated lifetime shard: {relative}")
            return json.dumps(payload).encode("utf-8")

    repo = Repo()
    refs = InformationStore(repo).holder_subject_claim_refs(holder, subject, limit=8)
    assert refs == [f"claim.test.{idx:03d}" for idx in range(56, 64)]
    assert repo.calls == [path]


def test_diplomacy_policy_helpers_make_treaty_types_operational() -> None:
    registry = {
        "agreements": {
            "agreement.nonaggression": {
                "status": "active", "agreement_type": "nonaggression",
                "party_refs": ["state.a", "state.b"], "provisions": {},
            },
            "agreement.alliance": {
                "status": "active", "agreement_type": "alliance",
                "party_refs": ["state.b", "state.c"], "provisions": {},
            },
            "agreement.guarantee": {
                "status": "active", "agreement_type": "guarantee",
                "party_refs": ["state.b", "state.d"],
                "provisions": {"guarantor_ref": "state.d", "protected_ref": "state.b"},
            },
            "agreement.client": {
                "status": "active", "agreement_type": "client_state",
                "party_refs": ["state.e", "state.f"],
                "provisions": {"patron_ref": "state.e", "client_ref": "state.f"},
            },
            "agreement.border": {
                "status": "active", "agreement_type": "border",
                "party_refs": ["state.a", "state.b"],
                "provisions": {"place_refs": ["place.b"], "route_refs": ["route_allowed"]},
            },
            "agreement.trade": {
                "status": "active", "agreement_type": "trade",
                "party_refs": ["state.a", "merchant.x"],
                "provisions": {"tariff_multiplier_milli": 500, "place_refs": ["place.a"], "route_refs": ["route_trade"]},
            },
            "agreement.ceasefire": {
                "status": "active", "agreement_type": "ceasefire_framework",
                "party_refs": ["state.g", "state.h"], "provisions": {},
            },
        }
    }
    assert hostility_barrier(registry, initiator_refs=["state.a"], target_refs=["state.b"])[1] == "nonaggression"
    assert hostility_barrier(registry, initiator_refs=["state.g"], target_refs=["state.h"])[1] == "ceasefire_framework"
    obligations = defense_obligation_specs(
        registry, initiator_refs=["state.a"], target_refs=["state.b"], conflict_ref="conflict.test"
    )
    assert {(row["agreement_type"], row["obligor_ref"]) for row in obligations} == {
        ("alliance", "state.c"), ("guarantee", "state.d")
    }
    assert client_state_access_basis(
        registry, force_owner_ref="state.e", sovereign_ref="state.f", administration_ref=None
    ) == "agreement:agreement.client:client_state_access"
    assert border_route_restriction(
        registry, force_owner_ref="state.a", sovereign_ref="state.b", administration_ref=None,
        destination_ref="place.b", route_ref="route_forbidden",
    ) == ("agreement.border", False)
    assert trade_tariff_multiplier_milli(
        registry, taxing_party_refs=["state.a"], commerce_party_refs=["merchant.x"],
        place_ref="place.a", route_ref="route_trade",
    ) == (500, ["agreement.trade"])


def test_founded_settlement_civil_economy_moves_only_conserved_tax_money(tmp_path: Path) -> None:
    source_mechanics = Path(__file__).resolve().parents[2] / "game/data/mechanics/governance.json"
    _write_json(tmp_path, "game/data/mechanics/governance.json", json.loads(source_mechanics.read_text()))
    _write_json(tmp_path, "state/world/routes-and-settlements.json", {
        "payload": {"places": [{"id": "place.test.settlement", "country_id": "country.test"}]}
    })
    planner = RepositoryCommandPlanner(RepositoryStore(tmp_path))
    governance = {
        "jurisdictions": {
            "jurisdiction.test": {
                "id": "jurisdiction.test", "place_ref": "place.test.settlement",
                "status": "settlement", "population_pool_ref": "pool.test.residents",
                "treasury_holder_ref": "treasury.test", "integration_milli": 800,
                "resistance_milli": 100, "tax_milli": 100,
            }
        }
    }
    population = {
        "pools": {
            "pool.test.residents": {
                "category": "settlement_resident", "count": 100,
                "profile": {"dimension_counts": {"age_band": {"adult": 60, "adolescent": 20, "child": 20}}},
            }
        }
    }
    holders = {
        "economy.private.test": {"currency.ryo": 10_000_000},
        "treasury.test": {"currency.ryo": 50_000},
    }
    finance = {
        "accounts": {
            "economy.private.test": {"kind": "private_economy", "scope_ref": "country.test"},
        }
    }
    before = sum(row["currency.ryo"] for row in holders.values())
    reviews = planner._settle_governed_civil_economies(
        governance, population, holders, finance,
        at=CampaignTime.parse("SE-0061-03-01T07:00:00"), compacted_months=1,
    )
    after = sum(row["currency.ryo"] for row in holders.values())
    row = governance["jurisdictions"]["jurisdiction.test"]["civil_economy"]

    assert len(reviews) == 1
    assert row["resident_count"] == 100
    assert row["workforce_count"] == 65
    assert row["gross_activity_ryo"] > row["consumption_ryo"]
    assert row["tax_due_ryo"] == row["tax_paid_ryo"] > 0
    assert holders["treasury.test"]["currency.ryo"] > 50_000
    assert after == before


def test_special_combat_and_manufacturing_discovery_truncate_instead_of_invalidating_large_valid_sets(tmp_path: Path) -> None:
    from shinobi_runtime.api import operations as base_operations
    from shinobi_runtime.api import campaign_manufacturing_discovery as manufacturing

    player_id = "pc_wei_tang"
    _write_json(tmp_path, "state/reg/jinchuriki.json", {"records": []})
    _write_json(tmp_path, "state/reg/puppets.json", {
        "puppets": [
            {"id": f"puppet.test.{idx:03d}", "owner_id": player_id}
            for idx in range(130)
        ]
    })
    _write_json(tmp_path, "state/reg/summons.json", {
        "profiles": {
            f"summon.test.{idx:03d}": {"contract_owner": player_id}
            for idx in range(70)
        }
    })
    operations = base_operations.CampaignOperations.__new__(base_operations.CampaignOperations)
    operations.repository = RepositoryStore(tmp_path)
    special = operations._player_special_combat_state(player_id)
    assert special["puppet_count"] == 130
    assert special["puppets_truncated"] is True
    assert len(special["puppets"]) == 128
    assert special["summon_count"] == 70
    assert special["summons_truncated"] is True
    assert len(special["summons"]) == 64

    recipes = {
        f"recipe.test.{idx:03d}": {
            "output_item_ref": f"item.test.{idx:03d}",
            "output_quantity_per_batch": 1,
        }
        for idx in range(70)
    }
    _write_json(tmp_path, "game/data/mechanics/institution-projects.json", {
        "manufacturing_recipes": recipes,
        "manufacturing_schedule": {"standing_weekly_active_hours": 48},
    })
    discovery = manufacturing.RouteAwareCampaignOperations.__new__(manufacturing.RouteAwareCampaignOperations)
    discovery.repository = RepositoryStore(tmp_path)
    rows, weekly_hours, recipe_count = discovery._manufacturing_catalog()
    assert weekly_hours == 48
    assert recipe_count == 70
    assert len(rows) == 64


def test_settlement_development_priority_spends_conserved_treasury_and_reduces_later_pressure(tmp_path: Path) -> None:
    source_mechanics = Path(__file__).resolve().parents[2] / "game/data/mechanics/governance.json"
    _write_json(tmp_path, "game/data/mechanics/governance.json", json.loads(source_mechanics.read_text()))
    _write_json(tmp_path, "state/world/routes-and-settlements.json", {
        "payload": {"places": [{"id": "place.test.pressured", "country_id": "country.test"}]}
    })
    planner = RepositoryCommandPlanner(RepositoryStore(tmp_path))
    governance = {"jurisdictions": {"jurisdiction.test": {
        "id": "jurisdiction.test", "place_ref": "place.test.pressured", "status": "settlement",
        "population_pool_ref": "pool.test.residents", "treasury_holder_ref": "treasury.test",
        "integration_milli": 800, "resistance_milli": 100, "tax_milli": 100,
    }}}
    population = {"pools": {"pool.test.residents": {
        "category": "settlement_resident", "count": 1000,
        "profile": {"dimension_counts": {"age_band": {"adult": 600, "adolescent": 200, "child": 200}}},
    }}}
    holders = {
        "economy.private.test": {"currency.ryo": 25_000_000},
        "treasury.test": {"currency.ryo": 100_000},
    }
    finance = {"accounts": {"economy.private.test": {"kind": "private_economy", "scope_ref": "country.test"}}}
    total_before = sum(row["currency.ryo"] for row in holders.values())

    planner._settle_governed_civil_economies(
        governance, population, holders, finance,
        at=CampaignTime.parse("SE-0061-03-01T07:00:00"), compacted_months=1,
    )
    first = dict(governance["jurisdictions"]["jurisdiction.test"]["civil_economy"])
    assert first["development_priority"] == "housing_infrastructure"
    assert first["civic_investment_priority"] == "housing_infrastructure"
    assert first["civic_investment_ryo"] > 0
    assert first["infrastructure_capacity_milli"] > 0
    assert sum(row["currency.ryo"] for row in holders.values()) == total_before

    planner._settle_governed_civil_economies(
        governance, population, holders, finance,
        at=CampaignTime.parse("SE-0061-04-01T07:00:00"), compacted_months=1,
    )
    second = governance["jurisdictions"]["jurisdiction.test"]["civil_economy"]
    assert second["infrastructure_pressure_milli"] < first["infrastructure_pressure_milli"]
    assert second["infrastructure_capacity_milli"] >= first["infrastructure_capacity_milli"]
    assert sum(row["currency.ryo"] for row in holders.values()) == total_before
