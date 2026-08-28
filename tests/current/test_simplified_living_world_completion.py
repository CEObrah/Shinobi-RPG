import ast
import copy
import importlib.util
import json
import pytest
from pathlib import Path

from shinobi_runtime.martial_world.faction_registry import register_faction, unregister_faction
from shinobi_runtime.martial_world.institutional_lifecycle import advance_institution
from shinobi_runtime.martial_world.institutional_offices import required_office_names
from shinobi_runtime.martial_world.scheduler import sync_faction_activity
from shinobi_runtime.martial_world.strategic_autonomy import choose_hostile_action
from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier

ROOT = Path(__file__).resolve().parents[2]


def _person(ref: str, offices=()):
    return {
        "person_id": ref,
        "name": ref,
        "birth_year": 20,
        "joined_year": 40,
        "membership_grade": "full",
        "standing_offices": list(offices),
        "attributes": {"strength": 50, "speed": 50, "dexterity": 50, "endurance": 50, "perception": 50, "intelligence": 50, "willpower": 50},
        "martial_skills": {"unarmed": 40, "command": 40, "stealth_scouting": 40},
        "professional_skills": {"administration": 40, "commerce": 40, "medicine": 20, "crafting": 20, "instruction": 40},
        "health": {"status": "ready", "consciousness": 100, "injuries": []},
    }


def test_small_faction_loses_obsolete_managed_office_but_keeps_noncore_title():
    faction = {
        "faction_id": "faction.test_gang",
        "type": "outlaw_faction",
        "training": {"unarmed": 40},
        "buildings": {},
        "infrastructure": {},
    }
    people = [_person("p0", ("leader", "treasurer", "heir"))] + [_person(f"p{i}") for i in range(1, 8)]
    roster = {"schema": "jianghu-person-lite-roster-1.0", "faction_ref": faction["faction_id"], "people": people}
    assert required_office_names(faction, roster) == frozenset({"leader"})
    after = advance_institution(faction, roster, year=61, month=1, social={})["roster"]
    offices = after["people"][0]["standing_offices"]
    assert "leader" in offices
    assert "treasurer" not in offices
    assert "heir" in offices


def test_dynamic_faction_registry_and_scheduler_follow_current_existence():
    registry = {"schema": "jianghu-faction-registry-1.0", "faction_refs": ["faction.a"]}
    registry = register_faction(registry, "faction.player_founded")
    assert registry["faction_refs"] == ["faction.a", "faction.player_founded"]
    schedule = {
        "schema": "jianghu-scheduler-1.0",
        "settled_through": "0061-01-01T00:00:00",
        "recurring": {
            "faction_monthly": {
                "interval_days": 30,
                "next_due_at": "0061-01-31T00:00:00",
                "owner_cursor": 0,
                "owner_refs": ["faction.a"],
                "event_kinds": ["faction_review", "faction_upkeep", "faction_member_cycle", "equipment_maintenance_review"],
            },
            "faction_annual": {
                "interval_days": 365,
                "next_due_at": "0062-01-01T00:00:00",
                "owner_cursor": 0,
                "owner_refs": ["faction.a"],
                "event_kinds": ["annual_faction_life_review"],
            },
        },
        "one_off": {},
    }
    from datetime import datetime
    synced = sync_faction_activity(schedule, faction_ids=registry["faction_refs"], now=datetime(61, 1, 1))
    assert synced["recurring"]["faction_monthly"]["owner_refs"] == ["faction.a", "faction.player_founded"]
    assert synced["recurring"]["faction_annual"]["owner_refs"] == ["faction.a", "faction.player_founded"]
    registry = unregister_faction(registry, "faction.a")
    synced = sync_faction_activity(synced, faction_ids=registry["faction_refs"], now=datetime(61, 1, 2))
    assert synced["recurring"]["faction_monthly"]["owner_refs"] == ["faction.player_founded"]


def test_outlaw_rivalry_uses_predatory_affordance_instead_of_honor_challenge():
    edges = [{"from_faction": "faction.test_outlaw", "to_faction": "faction.target", "hostility": 35}]
    actions = [
        choose_hostile_action(
            edges,
            faction_ref="faction.test_outlaw",
            year=61,
            month=month,
            risk_tolerance=100,
            faction_type="outlaw_faction",
            outlaw_subtype="road_band",
        )
        for month in range(1, 121)
    ]
    actions = [row for row in actions if row is not None]
    assert actions
    assert all(row["action"] != "formal_challenge" for row in actions)
    assert all(row["operation_intent"] in {"robbery", "revenge_strike"} for row in actions)


def test_time_soak_imports_the_same_public_settlement_entrypoint_as_live_play():
    spec = importlib.util.spec_from_file_location("run_long_horizon_test", ROOT / "tools/run_long_horizon.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.settle_martial_world_frontier is settle_martial_world_frontier


def test_frontier_bridge_contains_services_not_a_second_event_engine():
    bridge = ROOT / "runtime/shinobi_runtime/martial_world/frontier_bridge.py"
    route = ROOT / "runtime/shinobi_runtime/martial_world/route_frontier.py"
    bridge_text = bridge.read_text(encoding="utf-8")
    route_text = route.read_text(encoding="utf-8")
    assert "from .route_frontier import settle_route_frontier" in bridge_text
    assert "settle_route_frontier(" in bridge_text
    assert "def settle_route_frontier(" in route_text
    assert "time_integration_legacy" not in bridge_text
    # Architecture is enforced by authority/delegation invariants above, not an
    # arbitrary source-file line-count ceiling.


def test_semantic_resolution_methods_are_not_shadowed_by_mixin_mro():
    methods = {}
    for path in sorted((ROOT / "runtime/shinobi_runtime/commands").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_jianghu_") and node.name.endswith("_resolution"):
                methods.setdefault(node.name, []).append(path.name)
    duplicates = {name: paths for name, paths in methods.items() if len(paths) > 1}
    assert duplicates == {}


def test_extinct_faction_leaves_current_registry_but_keeps_dormant_estate_assets():
    from shinobi_runtime.martial_world.faction_existence import settle_extinctions_from_touched_rosters

    fid = "faction.test_extinct"
    faction_path = f"state/martial-world/factions/{fid}.json"
    roster_path = f"state/martial-world/people/{fid}.json"
    registry_path = "state/martial-world/faction-registry.json"
    relations_path = "state/martial-world/faction-relations.json"
    owners = {
        registry_path: {"schema": "jianghu-faction-registry-1.0", "faction_refs": [fid, "faction.other"]},
        faction_path: {
            "schema": "jianghu-faction-state-1.0",
            "faction_id": fid,
            "treasury_cash": 12345,
            "buildings": {"main_hall": 1},
        },
        relations_path: {
            "schema": "jianghu-faction-relations-state-1.0",
            "edges": [
                {"from_faction": fid, "to_faction": "faction.other", "hostility": 50},
                {"from_faction": "faction.other", "to_faction": fid, "hostility": 50},
                {"from_faction": "faction.other", "to_faction": "faction.third", "trust": 1},
            ],
            "coalitions": {
                "coalition.dead": {
                    "member_faction_refs": [fid, "faction.other"],
                    "target_faction_ref": "faction.third",
                    "purpose": "mutual_war_pressure",
                    "formed_at": "x",
                },
            },
        },
        "state/martial-world/social.json": {
            "schema": "jianghu-social-state-1.0",
            "vows": {
                "vow:survivor|loyal_to_faction|dead": {
                    "person_ref": "survivor", "kind": "loyal_to_faction", "strength": 80,
                    "declared_at": "x", "faction_ref": fid,
                },
                "vow:survivor|nonlethal|self": {
                    "person_ref": "survivor", "kind": "nonlethal", "strength": 80, "declared_at": "x",
                },
            },
        },
    }
    writes = {
        roster_path: {
            "schema": "jianghu-person-lite-roster-1.0",
            "faction_ref": fid,
            "people": [
                {"person_id": "dead.1", "health": {"status": "dead"}},
                {"person_id": "dead.2", "health": {"status": "dead"}},
            ],
        }
    }

    def read_json(path):
        return copy.deepcopy(owners[path])

    def load_faction(ref):
        assert ref == fid
        return faction_path, copy.deepcopy(owners[faction_path])

    result = settle_extinctions_from_touched_rosters(
        read_json=read_json,
        writes=writes,
        relations_state=owners[relations_path],
        load_faction=load_faction,
        relations_path=relations_path,
    )
    assert result["extinct_refs"] == [fid]
    assert result["registry"]["faction_refs"] == ["faction.other"]
    # The estate still exists with conserved cash/buildings; only institution status changes.
    extinct_owner = writes[faction_path]
    assert extinct_owner["status"] == "extinct"
    assert extinct_owner["treasury_cash"] == 12345
    assert extinct_owner["buildings"] == {"main_hall": 1}
    assert result["relations"]["edges"] == [
        {"from_faction": "faction.other", "to_faction": "faction.third", "trust": 1}
    ]
    assert "coalitions" not in result["relations"]
    social_after = writes["state/martial-world/social.json"]
    assert all(row.get("faction_ref") != fid for row in social_after.get("vows", {}).values())
    assert any(row.get("kind") == "nonlethal" for row in social_after.get("vows", {}).values())


def test_dead_leader_does_not_extinguish_faction_while_any_member_lives():
    from shinobi_runtime.martial_world.faction_existence import settle_extinctions_from_touched_rosters

    fid = "faction.test_survives"
    fpath = f"state/martial-world/factions/{fid}.json"
    rpath = f"state/martial-world/people/{fid}.json"
    owners = {
        "state/martial-world/faction-registry.json": {"schema": "jianghu-faction-registry-1.0", "faction_refs": [fid]},
        fpath: {"schema": "jianghu-faction-state-1.0", "faction_id": fid, "treasury_cash": 100},
        "state/martial-world/faction-relations.json": {"schema": "jianghu-faction-relations-state-1.0", "edges": []},
    }
    writes = {
        rpath: {
            "schema": "jianghu-person-lite-roster-1.0",
            "faction_ref": fid,
            "people": [
                {"person_id": "leader", "standing_offices": ["leader"], "health": {"status": "dead"}},
                {"person_id": "survivor", "health": {"status": "ready"}},
            ],
        }
    }
    result = settle_extinctions_from_touched_rosters(
        read_json=lambda path: copy.deepcopy(owners[path]),
        writes=writes,
        relations_state=owners["state/martial-world/faction-relations.json"],
        load_faction=lambda ref: (fpath, copy.deepcopy(owners[fpath])),
    )
    assert result["extinct_refs"] == []
    assert result["registry"]["faction_refs"] == [fid]
    assert fpath not in writes


def test_runtime_faction_materialization_registers_only_a_conserved_exact_bundle():
    from shinobi_runtime.martial_world.faction_existence import register_materialized_faction_bundle
    from shinobi_runtime.martial_world.faction_state import hydrate_faction_state, resolved_faction_type

    fid = "faction.runtime_shadow_hall"
    registry = {"schema": "jianghu-faction-registry-1.0", "faction_refs": []}
    faction = {
        "schema": "jianghu-faction-state-1.0",
        "faction_id": fid,
        "name": "Shadow Hall",
        "type": "outlaw_faction",
        "outlaw_subtype": "urban_gang",
        "headquarters": "luoyang",
        "local_site_ref": "site.runtime_shadow_hall",
        "treasury_cash": 5000,
        "buildings": {},
        "enterprises": {},
    }
    roster = {
        "schema": "jianghu-person-lite-roster-1.0",
        "faction_ref": fid,
        "people": [{"person_id": "founder", "health": {"status": "ready"}}],
    }
    inventory = {
        "schema": "jianghu-faction-inventory-1.0",
        "faction_ref": fid,
        "equipment": {},
        "raw_materials": {},
        "medicines": {},
        "transport_assets": {},
        "food_ration_days": 0,
    }
    after = register_materialized_faction_bundle(
        registry=registry, faction=faction, roster=roster, inventory=inventory,
    )
    assert after["faction_refs"] == [fid]
    hydrated = hydrate_faction_state(faction)
    assert hydrated["name"] == "Shadow Hall"
    assert resolved_faction_type(hydrated) == "outlaw_faction"

    dead_roster = copy.deepcopy(roster)
    dead_roster["people"][0]["health"]["status"] = "dead"
    import pytest
    with pytest.raises(ValueError, match="living exact member"):
        register_materialized_faction_bundle(
            registry=registry, faction=faction, roster=dead_roster, inventory=inventory,
        )


def test_extinction_fails_closed_when_required_social_authority_is_missing():
    from shinobi_runtime.martial_world.faction_existence import settle_extinctions_from_touched_rosters

    fid = "faction.test_extinct_missing_social"
    fpath = f"state/martial-world/factions/{fid}.json"
    rpath = f"state/martial-world/people/{fid}.json"
    registry_path = "state/martial-world/faction-registry.json"
    relations_path = "state/martial-world/faction-relations.json"
    owners = {
        registry_path: {"schema":"jianghu-faction-registry-1.0","faction_refs":[fid]},
        fpath: {"schema":"jianghu-faction-state-1.0","faction_id":fid,"treasury_cash":1},
        relations_path: {"schema":"jianghu-faction-relations-state-1.0","edges":[]},
    }
    writes = {
        rpath: {"schema":"jianghu-person-lite-roster-1.0","faction_ref":fid,"people":[{"person_id":"dead","health":{"status":"dead"}}]},
    }

    def read_json(path):
        if path not in owners:
            raise FileNotFoundError(path)
        return copy.deepcopy(owners[path])

    with pytest.raises(FileNotFoundError):
        settle_extinctions_from_touched_rosters(
            read_json=read_json,
            writes=writes,
            relations_state=owners[relations_path],
            load_faction=lambda ref: (fpath, copy.deepcopy(owners[fpath])),
            relations_path=relations_path,
        )


def test_extinction_fails_closed_when_social_vow_authority_is_malformed():
    from shinobi_runtime.martial_world.faction_existence import settle_extinctions_from_touched_rosters

    fid = "faction.test_extinct_bad_social"
    fpath = f"state/martial-world/factions/{fid}.json"
    rpath = f"state/martial-world/people/{fid}.json"
    registry_path = "state/martial-world/faction-registry.json"
    relations_path = "state/martial-world/faction-relations.json"
    social_path = "state/martial-world/social.json"
    owners = {
        registry_path: {"schema":"jianghu-faction-registry-1.0","faction_refs":[fid]},
        fpath: {"schema":"jianghu-faction-state-1.0","faction_id":fid,"treasury_cash":1},
        relations_path: {"schema":"jianghu-faction-relations-state-1.0","edges":[]},
        social_path: {"schema":"jianghu-social-state-1.0","vows":[]},
    }
    writes = {
        rpath: {"schema":"jianghu-person-lite-roster-1.0","faction_ref":fid,"people":[{"person_id":"dead","health":{"status":"dead"}}]},
    }

    with pytest.raises(ValueError, match="social vows"):
        settle_extinctions_from_touched_rosters(
            read_json=lambda path: copy.deepcopy(owners[path]),
            writes=writes,
            relations_state=owners[relations_path],
            load_faction=lambda ref: (fpath, copy.deepcopy(owners[fpath])),
            relations_path=relations_path,
        )
