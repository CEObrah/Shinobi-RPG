import copy
import importlib.util
import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from shinobi_runtime.martial_world.environment import place_terrain, site_combat_terrain
from shinobi_runtime.martial_world.faction_state import faction_admission_policy
from shinobi_runtime.martial_world.institutional_evolution_frontier import (
    default_dynamic_outlaw_profile, founder_curriculum, founder_recruitment_policy,
)
from shinobi_runtime.martial_world.institutional_obligations import (
    faction_retirement_blockers, member_transition_blockers, member_transition_bound_person_refs,
)
from shinobi_runtime.martial_world.people import deterministic_sex
from shinobi_runtime.martial_world.operational_equipment import (
    detach_operation_issue_holders, issue_operation_equipment, reclaim_operation_equipment,
)
from shinobi_runtime.martial_world.route_activity import compact_route_movement_roles, route_controlling_refs
from shinobi_runtime.martial_world.scheduler import initial_schedule, route_ids_needing_service
from shinobi_runtime.martial_world.site_control import active_site_controller, active_site_controllers
from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier

ROOT = Path(__file__).resolve().parents[2]


def _load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _reader(overlay):
    def read(path):
        if path in overlay:
            return copy.deepcopy(overlay[path])
        p = ROOT / path
        if not p.exists():
            raise FileNotFoundError(path)
        return json.loads(p.read_text(encoding="utf-8"))
    return read


def test_government_custody_shape_is_registered_for_production_admission():
    from shinobi_runtime.martial_world.crime_custody import create_government_custody_record
    from shinobi_runtime.store import RepositoryStore, RegisteredSchemaValidator, RegisteredTemplateValidator

    owner = {
        "schema": "jianghu-custody-state-1.0",
        "records": [create_government_custody_record(
            person_ref="mw.person.test.subject", jurisdiction_ref="central_plain",
            at="0061-12-12T21:15:00", detention_site_ref="site.test.detention",
            basis="active_warrant:test", offense="theft", guard_strength=7,
        )],
    }
    repo = RepositoryStore(ROOT)
    schema_validator = RegisteredSchemaValidator(repo)
    schema_validator.validators["jianghu-custody-state-1.0"].validate(owner)
    template_validator = RegisteredTemplateValidator(repo)
    RegisteredTemplateValidator._validate_document(
        owner, template_validator.templates["jianghu-custody-state-1.0"],
        label="test-government-custody",
    )


def test_extracted_operation_departure_compacts_reserved_inventory():
    from shinobi_runtime.martial_world.operational_equipment import reclaim_operation_equipment

    deployments = _load("state/martial-world/deployments.json")
    op_ref = "operation:faction_raid:faction.red_road_band:faction.misty_ridge_sect:006109"
    operation = copy.deepcopy(deployments["deployments"][op_ref])
    fid = str(operation["faction_ref"])
    ipath = f"state/martial-world/inventories/{fid}.json"
    inventory = _load(ipath)
    equipment = _load("state/martial-world/equipment-ledger.json")
    reclaimed = reclaim_operation_equipment(
        operation=operation, inventory=inventory, equipment_ledger=equipment,
    )
    operation = reclaimed["operation_after"]
    inventory = reclaimed["inventory_after"]
    equipment = reclaimed["equipment_ledger_after"]
    operation["status"] = "mobilizing"
    for field in ("physical_movement_ref", "route_refs", "travel_hours", "arrival_at", "return_arrival_at", "pending_travel_direction"):
        operation.pop(field, None)
    deployments["deployments"] = {op_ref: operation}
    inventory["herbs"] = {}
    route_state = _load("state/martial-world/route-operations.json")
    movements = route_state.get("movements", {})
    if isinstance(movements, dict):
        route_state["movements"] = {
            ref: row for ref, row in movements.items()
            if not (isinstance(row, dict) and str(row.get("purpose_ref") or "") == op_ref)
        }
    overlay = {
        "state/martial-world/deployments.json": deployments,
        ipath: inventory,
        "state/martial-world/equipment-ledger.json": equipment,
        "state/martial-world/route-operations.json": route_state,
    }
    at = datetime(61, 9, 14, 10, 15)
    event = {
        "kind": "faction_operation_departure", "owner_ref": op_ref,
        "event_id": f"operation_departure:{op_ref}", "direction": "outbound",
        "requires_player_decision": False,
    }
    result = settle_martial_world_frontier(
        read_json=_reader(overlay),
        schedule=initial_schedule(start=at - timedelta(hours=1), faction_ids=[], region_ids=[], route_ids=[]),
        events=[event], at=at,
    )
    stored = result["writes"][ipath]
    assert "herbs" not in stored
    assert int(stored["food_ration_days"]) < int(inventory["food_ration_days"])
    issued_op = result["writes"]["state/martial-world/deployments.json"]["deployments"][op_ref]
    assert issued_op.get("issued_equipment")
    assert len(issued_op["issued_equipment"]) > 0
    assert "state/martial-world/equipment-ledger.json" in result["writes"]
    assert sum(stored.get("equipment", {}).values()) < sum(inventory.get("equipment", {}).values())
    review = next(row for row in result["reviews"] if row.get("kind") == "faction_operation_departure")
    assert review["result"] == "physical_route_started"
    assert review["issued_person_count"] == len(issued_op["issued_equipment"])


def test_same_settlement_operation_departure_does_not_swallow_armory_corruption():
    from shinobi_runtime.martial_world import warfare
    from shinobi_runtime.martial_world.operational_equipment import reclaim_operation_equipment

    deployments = _load("state/martial-world/deployments.json")
    op_ref = "operation:faction_raid:faction.red_road_band:faction.misty_ridge_sect:006109"
    operation = copy.deepcopy(deployments["deployments"][op_ref])
    fid = str(operation["faction_ref"])
    ipath = f"state/martial-world/inventories/{fid}.json"
    inventory = _load(ipath)
    equipment = _load("state/martial-world/equipment-ledger.json")
    reclaimed = reclaim_operation_equipment(
        operation=operation, inventory=inventory, equipment_ledger=equipment,
    )
    operation = reclaimed["operation_after"]
    inventory = reclaimed["inventory_after"]
    equipment = reclaimed["equipment_ledger_after"]
    operation["status"] = "mobilizing"
    operation["target_place_ref"] = operation["source_place_ref"]
    for field in ("physical_movement_ref", "route_refs", "travel_hours", "arrival_at", "return_arrival_at", "pending_travel_direction"):
        operation.pop(field, None)
    deployments["deployments"] = {op_ref: operation}
    inventory["equipment"] = []
    at = datetime(61, 9, 14, 10, 15)
    with pytest.raises(ValueError, match="faction equipment stock invalid"):
        warfare.settle_faction_operation_departures(
            read_json=_reader({
                "state/martial-world/deployments.json": deployments,
                ipath: inventory,
                "state/martial-world/equipment-ledger.json": equipment,
            }),
            writes={},
            events=[{
                "kind": "faction_operation_departure", "owner_ref": op_ref,
                "event_id": f"operation_departure:{op_ref}", "direction": "outbound",
                "requires_player_decision": False,
            }],
            at=at,
            schedule_after=initial_schedule(start=at - timedelta(hours=1), faction_ids=[], region_ids=[], route_ids=[]),
        )


def test_custody_rescue_return_carries_rescued_person_without_making_them_controller():
    op_ref = "operation:test:rescue-role-return"
    rescuer_ref = "char.zhu"
    rescued_ref = "mw.person.golden_river_escorts.0001"
    deployments = _load("state/martial-world/deployments.json")
    deployments["deployments"] = {
        op_ref: {
            "faction_ref": "house_tang",
            "target_faction_ref": "golden_river_escorts",
            "operation_kind": "custody_rescue",
            "participant_refs": [rescuer_ref, rescued_ref],
            "captive_ref": rescued_ref,
            "source_place_ref": "luoyang",
            "target_place_ref": "changan",
            "status": "return_preparing",
            "pending_travel_direction": "return",
        }
    }
    at = datetime(61, 9, 14, 10, 15)
    result = settle_martial_world_frontier(
        read_json=_reader({"state/martial-world/deployments.json": deployments}),
        schedule=initial_schedule(start=at - timedelta(hours=1), faction_ids=[], region_ids=[], route_ids=[]),
        events=[{
            "kind": "faction_operation_departure", "owner_ref": op_ref,
            "event_id": f"operation_departure:return:{op_ref}", "direction": "return",
            "arrival_event_kind": "faction_operation_return", "requires_player_decision": False,
        }],
        at=at,
    )
    op = result["writes"]["state/martial-world/deployments.json"]["deployments"][op_ref]
    movement_ref = op["physical_movement_ref"]
    movement = result["writes"]["state/martial-world/route-operations.json"]["movements"][movement_ref]
    assert movement["participant_refs"] == [rescuer_ref, rescued_ref]
    assert movement["protected_person_refs"] == [rescued_ref]
    assert movement["rescued_refs"] == [rescued_ref]
    assert route_controlling_refs(movement) == [rescuer_ref]


def test_nonlocal_raid_return_moves_seized_payload_out_of_deployment_into_route_owner():
    from shinobi_runtime.martial_world import warfare

    op_ref = "operation:faction_raid:faction.red_road_band:faction.misty_ridge_sect:006109"
    deployments = _load("state/martial-world/deployments.json")
    op = copy.deepcopy(deployments["deployments"][op_ref])
    op["status"] = "return_preparing"
    op["pending_travel_direction"] = "return"
    op["seized_cash"] = 73
    op["seized_item_ref"] = "brick_tile_kg"
    op["seized_quantity"] = 11
    op["seized_cargo_bucket"] = "raw_materials"
    deployments["deployments"] = {op_ref: op}
    route_state = _load("state/martial-world/route-operations.json")
    route_state["movements"] = {}
    route_state["contacts"] = {}
    overlay = {
        "state/martial-world/deployments.json": deployments,
        "state/martial-world/route-operations.json": route_state,
    }
    at = datetime(61, 9, 15, 12, 0)
    result = warfare.settle_faction_operation_departures(
        read_json=_reader(overlay), writes={},
        events=[{
            "kind": "faction_operation_departure", "owner_ref": op_ref,
            "event_id": f"operation_departure:return:{op_ref}", "direction": "return",
            "arrival_event_kind": "faction_operation_return", "requires_player_decision": False,
        }],
        at=at,
        schedule_after=initial_schedule(start=at - timedelta(hours=1), faction_ids=[], region_ids=[], route_ids=[]),
    )
    stored = result["writes"]["state/martial-world/deployments.json"]["deployments"][op_ref]
    movement_ref = stored["physical_movement_ref"]
    movement = result["writes"]["state/martial-world/route-operations.json"]["movements"][movement_ref]
    assert movement["movement_kind"] == "raid_return"
    assert movement["cash_quantity"] == 73
    assert movement["item_ref"] == "brick_tile_kg"
    assert movement["quantity"] == 11
    for key in ("seized_cash", "seized_item_ref", "seized_quantity", "seized_cargo_bucket", "captive_refs", "return_escort_refs"):
        assert key not in stored


def test_operation_equipment_is_finite_and_reclaims_only_unconsumed_stock():
    faction_ref = "faction.test.archers"
    person_ref = "mw.person.test.archer"
    operation = {
        "faction_ref": faction_ref,
        "operation_kind": "faction_raid",
        "participant_refs": [person_ref],
    }
    inventory = {
        "faction_ref": faction_ref,
        "equipment": {"weapon_bow": 1, "item_arrow": 5},
    }
    ledger = {"schema": "jianghu-equipment-ledger-1.0", "person_loadouts": {}}
    person = {"person_id": person_ref, "martial_skills": {"bow": 80, "sword": 0, "spear": 0}}

    issued = issue_operation_equipment(
        operation=operation, faction_ref=faction_ref, participant_refs=[person_ref],
        people_by_ref={person_ref: person}, inventory=inventory, equipment_ledger=ledger,
    )
    assert issued["operation_after"]["issued_equipment"][person_ref] == {
        "weapon_bow": 1, "item_arrow": 5,
    }
    assert issued["inventory_after"].get("equipment", {}) == {}
    carried = issued["equipment_ledger_after"]["person_loadouts"][person_ref]["items"]
    assert carried == {"item_arrow": 5, "weapon_bow": 1}

    # Three arrows survive the fight. Exact combat has consumed the other two.
    spent_ledger = copy.deepcopy(issued["equipment_ledger_after"])
    spent_ledger["person_loadouts"][person_ref]["items"]["item_arrow"] = 3
    closed = reclaim_operation_equipment(
        operation=issued["operation_after"], inventory=issued["inventory_after"],
        equipment_ledger=spent_ledger,
    )
    assert "issued_equipment" not in closed["operation_after"]
    assert closed["inventory_after"]["equipment"] == {"item_arrow": 3, "weapon_bow": 1}
    assert closed["recovered"] == {"item_arrow": 3, "weapon_bow": 1}
    assert closed["lost_or_consumed"] == {"item_arrow": 2}
    assert person_ref not in closed["equipment_ledger_after"].get("person_loadouts", {})


def test_operation_equipment_preserves_preexisting_personal_ammunition_on_reclaim():
    faction_ref = "faction.test.archers"
    person_ref = "mw.person.test.archer.baseline"
    operation = {
        "faction_ref": faction_ref, "operation_kind": "faction_raid",
        "participant_refs": [person_ref],
    }
    ledger = {
        "schema": "jianghu-equipment-ledger-1.0",
        "person_loadouts": {person_ref: {"items": {"item_arrow": 3}}},
    }
    issued = issue_operation_equipment(
        operation=operation, faction_ref=faction_ref, participant_refs=[person_ref],
        people_by_ref={person_ref: {"person_id": person_ref, "martial_skills": {"bow": 80}}},
        inventory={"faction_ref": faction_ref, "equipment": {"weapon_bow": 1, "item_arrow": 5}},
        equipment_ledger=ledger,
    )
    assert issued["operation_after"]["issued_equipment_baseline"][person_ref]["item_arrow"] == 3
    carried = copy.deepcopy(issued["equipment_ledger_after"])
    carried["person_loadouts"][person_ref]["items"]["item_arrow"] = 4
    settled = reclaim_operation_equipment(
        operation=issued["operation_after"], inventory=issued["inventory_after"], equipment_ledger=carried,
    )
    assert settled["inventory_after"]["equipment"] == {"item_arrow": 1, "weapon_bow": 1}
    assert settled["equipment_ledger_after"]["person_loadouts"][person_ref]["items"] == {"item_arrow": 3}
    assert settled["lost_or_consumed"] == {"item_arrow": 4}
    assert "issued_equipment_baseline" not in settled["operation_after"]


def test_operation_equipment_does_not_create_mixed_third_party_arrow_title():
    faction_ref = "faction.test.archers"
    person_ref = "mw.person.test.claimed.arrows"
    ledger = {
        "schema": "jianghu-equipment-ledger-1.0",
        "person_loadouts": {person_ref: {"items": {"item_arrow": 3}}},
        "provenance_exceptions": {
            person_ref: {"item_arrow": {"owner_ref": "faction.other", "quantity": 3, "status": "held_by_other"}},
        },
    }
    result = issue_operation_equipment(
        operation={"faction_ref": faction_ref, "operation_kind": "faction_raid", "participant_refs": [person_ref]},
        faction_ref=faction_ref, participant_refs=[person_ref],
        people_by_ref={person_ref: {"person_id": person_ref, "martial_skills": {"bow": 90}}},
        inventory={"faction_ref": faction_ref, "equipment": {"weapon_bow": 1, "item_arrow": 12}},
        equipment_ledger=ledger,
    )
    assert result["issued_person_count"] == 0
    assert result["inventory_after"]["equipment"] == {"weapon_bow": 1, "item_arrow": 12}
    assert result["equipment_ledger_after"]["provenance_exceptions"][person_ref]["item_arrow"]["owner_ref"] == "faction.other"


def test_detaching_live_operation_issue_keeps_item_on_holder_with_source_title():
    faction_ref = "faction.test.source"
    person_ref = "mw.person.test.separated"
    issued = issue_operation_equipment(
        operation={"faction_ref": faction_ref, "operation_kind": "faction_war_strike", "participant_refs": [person_ref]},
        faction_ref=faction_ref, participant_refs=[person_ref],
        people_by_ref={person_ref: {"person_id": person_ref, "martial_skills": {"sword": 80}}},
        inventory={"faction_ref": faction_ref, "equipment": {"weapon_jian": 1}},
        equipment_ledger={"schema": "jianghu-equipment-ledger-1.0", "person_loadouts": {}},
    )
    detached = detach_operation_issue_holders(
        operation=issued["operation_after"], source_faction_ref=faction_ref, holder_refs=[person_ref],
        equipment_ledger=issued["equipment_ledger_after"],
    )
    assert "issued_equipment" not in detached["operation_after"]
    assert detached["equipment_ledger_after"]["person_loadouts"][person_ref]["items"] == {"weapon_jian": 1}
    claim = detached["equipment_ledger_after"]["provenance_exceptions"][person_ref]["weapon_jian"]
    assert claim == {"owner_ref": faction_ref, "quantity": 1, "status": "operation_issue_separated"}


def test_operation_equipment_refuses_mixed_third_party_fungible_title():
    faction_ref = "faction.test.source"
    person_ref = "mw.person.test.claimed"
    ledger = {
        "schema": "jianghu-equipment-ledger-1.0",
        "person_loadouts": {person_ref: {"items": {"item_arrow": 3}}},
        "provenance_exceptions": {
            person_ref: {
                "item_arrow": {"owner_ref": "faction.other", "quantity": 3, "status": "seized"},
            },
        },
    }
    result = issue_operation_equipment(
        operation={"faction_ref": faction_ref, "operation_kind": "faction_raid", "participant_refs": [person_ref]},
        faction_ref=faction_ref, participant_refs=[person_ref],
        people_by_ref={person_ref: {"person_id": person_ref, "martial_skills": {"bow": 90}}},
        inventory={"faction_ref": faction_ref, "equipment": {"weapon_bow": 1, "item_arrow": 12}},
        equipment_ledger=ledger,
    )
    assert result["issued_person_count"] == 0
    assert result["inventory_after"]["equipment"] == {"weapon_bow": 1, "item_arrow": 12}
    assert "issued_equipment" not in result["operation_after"]


def test_route_frontier_detaches_live_issue_holder_absent_from_linked_movement():
    fid = "faction.red_road_band"
    separated = "mw.person.faction.red_road_band.0006"
    traveler = "mw.person.faction.red_road_band.0007"
    op_ref = "operation:test:live-issue-separation"
    movement_ref = "movement:test:live-issue-separation"

    deployments = _load("state/martial-world/deployments.json")
    deployments["deployments"] = {
        op_ref: {
            "faction_ref": fid, "operation_kind": "faction_raid", "status": "returning",
            "participant_refs": [separated, traveler], "physical_movement_ref": movement_ref,
            "issued_equipment": {
                separated: {"weapon_jian": 1}, traveler: {"weapon_jian": 1},
            },
            "issued_equipment_baseline": {
                separated: {"weapon_jian": 0}, traveler: {"weapon_jian": 0},
            },
            "issued_equipment_claim_baseline": {
                separated: {"weapon_jian": 0}, traveler: {"weapon_jian": 0},
            },
        },
    }
    route_ops = _load("state/martial-world/route-operations.json")
    route_ops["movements"] = {
        movement_ref: {
            "movement_kind": "faction_operation_travel", "purpose_ref": op_ref,
            "operation_ref": op_ref, "beneficiary_ref": fid,
            "route_ref": "route.kunming.dali", "status": "contact_pending",
            "participant_refs": [traveler], "escort_refs": [traveler],
        },
    }
    ledger = _load("state/martial-world/equipment-ledger.json")
    ledger.setdefault("person_loadouts", {})[separated] = {"items": {"weapon_jian": 1}}
    ledger.setdefault("person_loadouts", {})[traveler] = {"items": {"weapon_jian": 1}}
    overlay = {
        "state/martial-world/deployments.json": deployments,
        "state/martial-world/route-operations.json": route_ops,
        "state/martial-world/equipment-ledger.json": ledger,
    }
    at = datetime(61, 9, 14, 10, 15)
    result = settle_martial_world_frontier(
        read_json=_reader(overlay),
        schedule=initial_schedule(start=at - timedelta(hours=1), faction_ids=[], region_ids=[], route_ids=[]),
        events=[{
            "kind": "route_activity_cycle", "owner_ref": "route.kunming.dali",
            "event_id": "test:live-issue-separation", "requires_player_decision": False,
        }],
        at=at,
    )
    op_after = result["writes"]["state/martial-world/deployments.json"]["deployments"][op_ref]
    assert op_after["issued_equipment"] == {traveler: {"weapon_jian": 1}}
    assert op_after["issued_equipment_baseline"] == {traveler: {"weapon_jian": 0}}
    claim = result["writes"]["state/martial-world/equipment-ledger.json"]["provenance_exceptions"][separated]["weapon_jian"]
    assert claim == {
        "owner_ref": fid, "quantity": 1, "status": "operation_issue_separated",
    }
    assert result["writes"]["state/martial-world/equipment-ledger.json"]["person_loadouts"][separated]["items"] == {"weapon_jian": 1}


def test_operation_equipment_never_conjures_more_weapons_than_armory_stock():
    faction_ref = "faction.test.small_armory"
    refs = [f"mw.person.test.fighter.{i}" for i in range(3)]
    people = {ref: {"person_id": ref, "martial_skills": {"sword": 50}} for ref in refs}
    result = issue_operation_equipment(
        operation={"faction_ref": faction_ref, "operation_kind": "faction_war_strike", "participant_refs": refs},
        faction_ref=faction_ref, participant_refs=refs, people_by_ref=people,
        inventory={"faction_ref": faction_ref, "equipment": {"weapon_jian": 1}},
        equipment_ledger={"schema": "jianghu-equipment-ledger-1.0", "person_loadouts": {}},
    )
    assert result["issued_person_count"] == 1
    assert len(result["operation_after"]["issued_equipment"]) == 1
    assert result["inventory_after"].get("equipment", {}) == {}


def test_frontier_write_boundary_compacts_inventory_buckets():
    fid = "house_tang"
    ipath = "state/martial-world/inventories/house_tang.json"
    inventory = _load(ipath)
    inventory["herbs"] = {}
    overlay = {ipath: inventory}
    at = datetime(61, 10, 13, 21, 15)
    schedule = initial_schedule(start=at - timedelta(hours=1), faction_ids=[], region_ids=[], route_ids=[])
    result = settle_martial_world_frontier(
        read_json=_reader(overlay), schedule=schedule,
        events=[{"kind": "faction_upkeep", "owner_ref": fid, "event_id": "test:upkeep:compact-inventory"}],
        at=at,
    )
    stored = result["writes"][ipath]
    assert "herbs" not in stored


def test_monthly_autonomy_sees_post_upkeep_food_reserve_and_replenishes(monkeypatch):
    import shinobi_runtime.martial_world.autonomy_frontier as autonomy_frontier

    fid = "faction.travelers_brotherhood"
    fpath = f"state/martial-world/factions/{fid}.json"
    rpath = f"state/martial-world/people/{fid}.json"
    ipath = f"state/martial-world/inventories/{fid}.json"
    mpath = "state/martial-world/markets/tibetan_highland.json"
    faction = _load(fpath)
    roster = _load(rpath)
    inventory = _load(ipath)
    market = _load(mpath)
    population = sum(
        1 for person in roster["people"]
        if not (isinstance(person.get("health"), dict) and person.get("health", {}).get("status") == "dead")
    )
    assert population > 0

    # Thirty-one reserve days become one after this month's exact upkeep. The
    # autonomy pass occurs later in the same frontier and must see that depleted
    # after-image, not the inventory object cached before upkeep.
    inventory["food_ration_days"] = 31 * population
    faction["treasury_cash"] = 100_000_000
    overlay = {fpath: faction, ipath: inventory, mpath: market}
    monkeypatch.setattr(
        autonomy_frontier, "autonomy_review",
        lambda *_args, **_kwargs: {
            "ordered_actions": ["secure_food"],
            "scored_actions": [{"action": "secure_food", "score": 1000}],
        },
    )

    at = datetime(61, 10, 13, 21, 15)
    result = settle_martial_world_frontier(
        read_json=_reader(overlay),
        schedule=initial_schedule(start=at - timedelta(days=1), faction_ids=[], region_ids=[], route_ids=[]),
        events=[
            {"kind": "faction_upkeep", "owner_ref": fid, "event_id": "test:food-cache:upkeep"},
            {"kind": "faction_review", "owner_ref": fid, "event_id": "test:food-cache:review"},
        ],
        at=at,
    )

    review = next(
        row for row in result["reviews"]
        if row.get("kind") == "faction_review" and row.get("faction_ref") == fid
    )
    assert review["food_reserve_days"] == 1
    food_action = next(row for row in review["executed_actions"] if row.get("action") == "secure_food")
    assert food_action["result"] == "purchased"
    assert food_action["quantity"] > 0
    assert result["writes"][ipath]["food_ration_days"] == 60 * population

    cash_before = int(faction["treasury_cash"]) + int(market["cash_pool"])
    cash_after = int(result["writes"][fpath]["treasury_cash"]) + int(result["writes"][mpath]["cash_pool"])
    assert cash_after == cash_before


def test_route_role_compaction_keeps_one_exact_party_authority():
    refs = [f"mw.person.test.{i:04d}" for i in range(103)]
    retreat = compact_route_movement_roles({
        "movement_kind": "raid_return", "participant_refs": refs,
        "escort_refs": list(refs), "raider_refs": list(refs),
        "protected_person_refs": [], "captive_refs": [], "rescued_refs": [],
    })
    assert retreat["participant_refs"] == refs
    assert "escort_refs" not in retreat
    assert "raider_refs" not in retreat
    assert route_controlling_refs(retreat) == refs

    escort = compact_route_movement_roles({
        "movement_kind": "escort_contract", "participant_refs": refs[:6],
        "escort_refs": list(refs[:6]),
    })
    assert escort["participant_refs"] == refs[:6]
    assert "escort_refs" not in escort
    assert route_controlling_refs(escort) == refs[:6]

    principal = "mw.person.test.principal"
    mixed = compact_route_movement_roles({
        "movement_kind": "escort_contract",
        "participant_refs": refs[:3] + [principal],
        "escort_refs": refs[:3],
    })
    assert mixed["escort_refs"] == refs[:3]
    assert route_controlling_refs(mixed) == refs[:3]


def test_route_service_statuses_share_one_scheduler_authority():
    movements = {
        "a": {"route_ref": "road.a", "status": "active"},
        "b": {"route_ref": "road.b", "status": "traveling"},
        "c": {"route_ref": "road.c", "status": "lodging_rest"},
        "d": {"route_ref": "road.d", "status": "party_extinguished"},
        "e": {"route_ref": "road.e", "status": "completed"},
    }
    assert route_ids_needing_service(movements) == ["road.a", "road.b", "road.c", "road.d"]


def test_party_extinguished_merchant_route_salvages_cargo_and_unused_provisions_locally():
    route_ref = "route.luoyang.changan"
    movement_ref = "test:merchant:extinguished"
    inventory_path = "state/martial-world/inventories/house_tang.json"
    market_path = "state/martial-world/markets/central_plain.json"
    inventory = _load(inventory_path)
    market = _load(market_path)
    cloth_before = int(inventory["raw_materials"]["cloth_m"])
    food_before = int(inventory["food_ration_days"])
    market_cloth_before = int(market["stock"].get("cloth_m", 0))
    market_food_before = int(market["stock"].get("food_ration_day", 0))
    inventory["raw_materials"]["cloth_m"] = cloth_before - 17
    inventory["food_ration_days"] = food_before - 7
    route_ops = {
        "schema": "jianghu-route-operations-state-1.0",
        "movements": {
            movement_ref: {
                "movement_kind": "merchant_trade",
                "route_ref": route_ref,
                "origin_place_ref": "luoyang",
                "destination_place_ref": "changan",
                "segment_origin_place_ref": "luoyang",
                "segment_destination_place_ref": "changan",
                "required_seconds": 604800,
                "elapsed_seconds": 172800,
                "last_progress_at": "0061-09-13T09:15:00",
                "beneficiary_ref": "house_tang",
                "participant_refs": [],
                "status": "party_extinguished",
                "item_ref": "cloth_m",
                "quantity": 17,
                "provision_reservation": {
                    "source_kind": "faction", "source_ref": "house_tang",
                    "participant_count": 1, "planned_travel_seconds": 604800,
                    "ration_days_reserved": 7, "ration_days_consumed": 2,
                    "journey_elapsed_seconds": 172800,
                },
            }
        },
        "contacts": {},
    }
    overlay = {
        inventory_path: inventory,
        market_path: market,
        "state/martial-world/route-operations.json": route_ops,
    }
    at = datetime(61, 9, 15, 9, 15)
    schedule = initial_schedule(start=at - timedelta(hours=1), faction_ids=[], region_ids=[], route_ids=[])
    result = settle_martial_world_frontier(
        read_json=_reader(overlay), schedule=schedule,
        events=[{"kind": "route_activity_cycle", "owner_ref": route_ref, "event_id": "test:extinguished:route"}],
        at=at,
    )
    assert movement_ref not in result["writes"]["state/martial-world/route-operations.json"]["movements"]
    # With no surviving controller, cargo and unused provisions remain where the
    # caravan was extinguished. They enter the local aggregate market instead of
    # teleporting back to the beneficiary's distant inventory.
    assert inventory_path not in result["writes"]
    after_market = result["writes"][market_path]
    assert int(after_market["stock"]["cloth_m"]) == market_cloth_before + 17
    assert int(after_market["stock"]["food_ration_day"]) == market_food_before + 5
    review = next(row for row in result["reviews"] if row.get("kind") == "route_activity_cycle")
    outcome = review["closed_outcomes"][movement_ref]
    assert outcome["cargo_salvaged_locally"] == 17
    assert outcome["salvaged_ration_days"] == 5


def test_party_extinguished_raid_return_releases_captive_and_salvages_assets_locally():
    route_ref = "route.luoyang.changan"
    movement_ref = "test:raid-return:extinguished"
    op_ref = "operation:test:raid-return:extinguished"
    captive_ref = "mw.person.house_tang.1050"
    market_path = "state/martial-world/markets/central_plain.json"
    market = _load(market_path)
    cloth_before = int(market["stock"]["cloth_m"])
    food_before = int(market["stock"]["food_ration_day"])
    route_ops = {
        "schema": "jianghu-route-operations-state-1.0",
        "movements": {
            movement_ref: {
                "movement_kind": "raid_return", "purpose_ref": op_ref, "operation_ref": op_ref, "route_ref": route_ref,
                "origin_place_ref": "luoyang", "destination_place_ref": "changan",
                "segment_origin_place_ref": "luoyang", "segment_destination_place_ref": "changan",
                "required_seconds": 86400, "elapsed_seconds": 1800,
                "beneficiary_ref": "house_tang", "participant_refs": [captive_ref],
                "escort_refs": [], "raider_refs": [], "captive_refs": [captive_ref],
                "protected_person_refs": [captive_ref], "status": "party_extinguished",
                "item_ref": "cloth_m", "quantity": 17,
                "provision_reservation": {
                    "source_kind": "faction", "source_ref": "house_tang",
                    "participant_count": 2, "planned_travel_seconds": 86400,
                    "ration_days_reserved": 7, "ration_days_consumed": 2,
                    "journey_elapsed_seconds": 1800,
                },
            }
        },
        "contacts": {},
    }
    custody = _load("state/martial-world/custody.json")
    custody["records"] = [{
        "custody_id": "custody:test:raid-return", "person_ref": captive_ref,
        "captor_ref": "mw.person.house_tang.1032", "holder_faction_ref": "house_tang",
        "status": "restrained", "location_ref": route_ref, "basis": "test",
        "started_at": "0061-09-14T00:00:00",
    }]
    deployments = _load("state/martial-world/deployments.json")
    deployments["deployments"] = {
        op_ref: {
            "faction_ref": "house_tang", "target_faction_ref": "faction.red_willow_band",
            "operation_kind": "faction_raid", "operation_intent": "kidnapping",
            "participant_refs": [captive_ref], "status": "traveling_return",
            "physical_movement_ref": movement_ref, "source_place_ref": "changan",
            "target_place_ref": "luoyang",
        }
    }
    overlay = {
        market_path: market, "state/martial-world/custody.json": custody,
        "state/martial-world/route-operations.json": route_ops,
        "state/martial-world/deployments.json": deployments,
    }
    at = datetime(61, 9, 15, 9, 15)
    schedule = initial_schedule(start=at - timedelta(hours=1), faction_ids=[], region_ids=[], route_ids=[])
    result = settle_martial_world_frontier(
        read_json=_reader(overlay), schedule=schedule,
        events=[{"kind": "route_activity_cycle", "owner_ref": route_ref, "event_id": "test:extinguished:raid"}],
        at=at,
    )
    assert movement_ref not in result["writes"]["state/martial-world/route-operations.json"]["movements"]
    after_market = result["writes"][market_path]
    assert int(after_market["stock"]["cloth_m"]) == cloth_before + 17
    assert int(after_market["stock"]["food_ration_day"]) == food_before + 5
    records_after = result["writes"]["state/martial-world/custody.json"]["records"]
    assert not any(row.get("person_ref") == captive_ref for row in records_after if isinstance(row, dict))
    review = next(row for row in result["reviews"] if row.get("kind") == "route_activity_cycle")
    outcome = review["closed_outcomes"][movement_ref]
    assert outcome["released_captive_refs"] == [captive_ref]
    assert outcome["salvaged_quantity"] == 17
    assert outcome["salvaged_ration_days"] == 5
    assert outcome["operation_closed"] is True
    assert op_ref not in result["writes"]["state/martial-world/deployments.json"]["deployments"]


def test_battlefield_terrain_projection_preserves_strategic_geography():
    assert place_terrain({"kind":"major_city","climate_profile":"temperate_mountain"}) == "urban"
    assert place_terrain({"kind":"martial_headquarters","climate_profile":"temperate_mountain"}) == "mountain"
    assert place_terrain({"kind":"rural_holding","climate_profile":"middle_yangtze"}) == "river_plain"
    assert place_terrain({"kind":"martial_headquarters","climate_profile":"tibetan_highland"}) == "highland"
    assert place_terrain({"kind":"rural_holding","climate_profile":"northwest_dry"}) == "desert"
    assert site_combat_terrain(
        {"site_type":"tournament_ground"},
        {"kind":"city","climate_profile":"temperate_mountain"},
    ) == "mountain"


def test_current_site_control_uses_mutable_faction_owners_not_static_bootstrap_owner():
    assert active_site_controller(_load, "site.house_tang") == "house_tang"
    assert active_site_controllers(_load, "site.house_tang") == ["house_tang"]


def test_authored_and_dynamic_admission_policies_share_one_authority():
    tang = faction_admission_policy("house_tang")
    assert tang["minimum_entry_age"] == 6
    assert set(tang["allowed_sexes"]) == {"female", "male"}

    shaolin = faction_admission_policy("shaolin")
    assert shaolin["minimum_entry_age"] == 8
    assert shaolin["allowed_sexes"] == ["male"]
    assert {
        deterministic_sex(
            stable=f"candidate:{index}", faction_id="faction.dynamic.shaolin.splinter",
            admission_policy=shaolin,
        )
        for index in range(32)
    } == {"male"}

    dynamic = faction_admission_policy(
        "faction.dynamic.test",
        {"admission_policy": {"model": "restricted", "allowed_sexes": ["female"], "minimum_entry_age": 11}},
    )
    assert dynamic == {"model": "restricted", "allowed_sexes": ["female"], "minimum_entry_age": 11}


def test_founder_culture_is_derived_from_real_people_and_dynamic_outlaw_routes_are_local():
    founders = [
        {
            "person_id": "a", "health": {"status": "healthy"},
            "martial_skills": {"sword": 80, "unarmed": 40, "command": 30},
            "professional_skills": {"instruction": 50, "administration": 25},
            "qi": 60, "qi_control": 45,
            "aptitudes": {"martial": 120, "qi": 110},
        },
        {
            "person_id": "b", "health": {"status": "healthy"},
            "martial_skills": {"sword": 60, "unarmed": 55, "stealth_scouting": 50},
            "professional_skills": {"commerce": 40, "instruction": 20},
            "qi": 40, "qi_control": 35,
            "aptitudes": {"martial": 100, "qi": 90},
        },
    ]
    curriculum = founder_curriculum(founders)
    assert curriculum["sword"] == 100
    assert curriculum["unarmed"] > 0
    assert curriculum["instruction"] > 0
    policy = founder_recruitment_policy(founders)
    assert policy["minimum_martial_aptitude"] == 73
    assert policy["minimum_qi_aptitude"] == 66
    assert policy["target_membership"] == 16

    profile = default_dynamic_outlaw_profile(place_ref="luoyang", site_type="guild_hall")
    assert profile["outlaw_subtype"] == "urban_gang"
    assert "route.luoyang.changan" in profile["operating_routes"]
    assert all("luoyang" in route_ref for route_ref in profile["operating_routes"])
    assert profile["outlaw_policy"]["retreat_loss_threshold_pct"] == 30


def test_current_projects_have_site_time_and_planned_staffing():
    projects = _load("state/martial-world/projects.json")["projects"]
    assert len(projects) == 27
    for row in projects.values():
        assert row["site_ref"]
        assert row["last_progress_at"] == row["started_at"]
        for role in ("skilled", "management", "general"):
            assert row[f"planned_{role}_worker_count"] == len(row.get(f"{role}_worker_refs", []))


def test_faction_retirement_is_blocked_by_live_contract_and_tournament_obligations():
    overlay = {
        "state/martial-world/contracts/index.json": {
            "active": {
                "contract.test": {
                    "status": "accepted", "beneficiary_ref": "house_tang",
                    "issuer_ref": "market:central_plain", "participants": ["pc_wei_tang"],
                }
            }
        },
        "state/martial-world/tournaments.json": {
            "tournaments": {
                "tournament.test": {
                    "status": "registration_open",
                    "registrations": [{"entrant_ref": "pc_wei_tang", "faction_ref": "house_tang"}],
                    "delegations": {"house_tang": {"entrant_refs": ["pc_wei_tang"]}},
                }
            }
        },
        "state/martial-world/projects.json": {"projects": {}},
        "state/martial-world/deployments.json": {"deployments": {}},
        "state/martial-world/route-operations.json": {"movements": {}},
        "state/martial-world/custody.json": {"records": []},
    }
    blockers = faction_retirement_blockers(_reader(overlay), "house_tang")
    assert {row["kind"] for row in blockers} == {"contract", "tournament"}
    member = member_transition_blockers(
        _reader(overlay), ["pc_wei_tang"], source_faction_ref="house_tang",
    )
    assert {row["kind"] for row in member} == {"contract", "tournament"}


def test_membership_transition_binding_includes_time_free_contract_and_tournament_obligations():
    overlay = {
        "state/martial-world/projects.json": {"projects": {}},
        "state/martial-world/deployments.json": {"deployments": {}},
        "state/martial-world/route-operations.json": {"movements": {}},
        "state/martial-world/custody.json": {"records": []},
        "state/martial-world/contracts/index.json": {
            "active": {
                "contract.accepted": {
                    "status": "accepted", "beneficiary_ref": "house_tang",
                    "issuer_ref": "market:central_plain", "participants": ["member.contract"],
                },
                "contract.offered": {
                    "status": "offered", "beneficiary_ref": "",
                    "issuer_ref": "market:central_plain", "participants": [],
                },
            }
        },
        "state/martial-world/tournaments.json": {
            "tournaments": {
                "tournament.open": {
                    "status": "registration_open",
                    "registrations": [{"entrant_ref": "member.entrant", "faction_ref": "house_tang"}],
                    "delegations": {"house_tang": {"spectator_refs": ["member.delegate"]}},
                }
            }
        },
    }
    bound = member_transition_bound_person_refs(_reader(overlay))
    assert {"member.contract", "member.entrant", "member.delegate"} <= bound
    assert "contract.offered" not in bound


def test_split_cannot_move_estate_while_parent_project_is_physically_bound_to_it():
    overlay = {
        "state/martial-world/projects.json": {
            "projects": {
                "project.test": {
                    "faction_ref": "house_tang", "site_ref": "site.captured",
                    "project_type": "building_expansion", "general_worker_refs": [],
                    "skilled_worker_refs": [], "management_worker_refs": [],
                }
            }
        },
        "state/martial-world/deployments.json": {"deployments": {}},
        "state/martial-world/route-operations.json": {"movements": {}},
        "state/martial-world/custody.json": {"records": []},
        "state/martial-world/contracts/index.json": {"active": {}},
        "state/martial-world/tournaments.json": {"tournaments": {}},
    }
    blockers = member_transition_blockers(
        _reader(overlay), ["member.a"], source_faction_ref="house_tang",
        moving_site_refs=["site.captured"],
    )
    assert blockers == [{
        "kind": "project", "ref": "project.test", "reason": "project_bound_to_moving_site",
    }]


def test_retirement_cannot_erase_incoming_live_deployment_target():
    overlay = {
        "state/martial-world/projects.json": {"projects": {}},
        "state/martial-world/deployments.json": {
            "deployments": {
                "deployment.test": {
                    "status": "traveling_outbound", "faction_ref": "red_tiger_stronghold",
                    "target_faction_ref": "house_tang", "participant_refs": ["attacker.a"],
                }
            }
        },
        "state/martial-world/route-operations.json": {"movements": {}},
        "state/martial-world/custody.json": {"records": []},
        "state/martial-world/contracts/index.json": {"active": {}},
        "state/martial-world/tournaments.json": {"tournaments": {}},
    }
    blockers = faction_retirement_blockers(_reader(overlay), "house_tang")
    assert blockers == [{
        "kind": "deployment", "ref": "deployment.test", "reason": "deployment_involves_faction",
    }]


def test_project_elapsed_time_uses_real_calendar_delta_not_minimum_duration_per_wake():
    from shinobi_runtime.martial_world.project_frontier import _advance_project, _elapsed_work_days

    project = {
        "project_type": "building_expansion", "faction_ref": "house_tang", "site_ref": "site.house_tang",
        "started_at": "0061-09-01T00:00:00", "last_progress_at": "0061-09-01T00:00:00",
        "elapsed_calendar_days": 0, "minimum_calendar_days": 15,
        "general_labor_hours_remaining": 1000, "skilled_labor_hours_remaining": 500,
        "general_worker_refs": ["g"], "skilled_worker_refs": ["s"], "management_worker_refs": [],
        "planned_general_worker_count": 1, "planned_skilled_worker_count": 1, "planned_management_worker_count": 0,
        "building_type": "residential_compound", "additional_footprint_m2": 100,
    }
    days, settled = _elapsed_work_days(project, datetime(61, 9, 3, 12, 0))
    assert days == 2
    assert settled == "0061-09-03T00:00:00"
    after = _advance_project(project, days=days)
    assert after["elapsed_calendar_days"] == 2
    assert after["general_labor_hours_remaining"] == 984
    assert after["skilled_labor_hours_remaining"] == 488


def test_project_restaff_excludes_busy_and_cross_site_people_and_preserves_target_headcount():
    from shinobi_runtime.martial_world.commitments import reserve_resources
    from shinobi_runtime.martial_world.project_frontier import _restaff_project

    faction = {"faction_id": "house_tang", "local_site_ref": "site.house_tang", "headquarters": "luoyang"}
    project = {
        "project_type": "building_expansion", "faction_ref": "house_tang", "site_ref": "site.house_tang",
        "started_at": "0061-09-01T00:00:00", "last_progress_at": "0061-09-01T00:00:00",
        "planned_skilled_worker_count": 1, "planned_management_worker_count": 0, "planned_general_worker_count": 2,
        "skilled_worker_refs": [], "management_worker_refs": [], "general_worker_refs": [],
    }
    roster = {"people": [
        {"person_id": "skilled", "professional_skills": {"crafting": 80}, "health": {"status": "healthy"}},
        {"person_id": "general", "professional_skills": {"crafting": 2}, "health": {"status": "healthy"}},
        {"person_id": "busy", "professional_skills": {"crafting": 100}, "health": {"status": "healthy"}},
        {"person_id": "away", "location_ref": "site.other", "professional_skills": {"crafting": 99}, "health": {"status": "healthy"}},
    ]}
    commitments = reserve_resources(
        {"commitments": {}, "person_index": {}}, resources=[("person", "busy", "house_tang")],
        actor_ref="busy", owner_ref="house_tang", activity_ref="other", activity_kind="travel",
        started_at="0061-09-01T00:00:00", location_ref="site.house_tang",
    )
    after, commitments_after, added = _restaff_project(
        project, roster, commitments, faction_ref="house_tang", project_ref="project:test",
        location_ref="site.house_tang", faction=faction, physically_unavailable_refs=set(),
    )
    assert after["skilled_worker_refs"] == ["skilled"]
    assert after["general_worker_refs"] == ["general"]
    assert after["planned_general_worker_count"] == 2
    assert "busy" not in added and "away" not in added
    assert commitments_after["person_index"]["busy"] == "commitment:other"
    assert commitments_after["person_index"]["skilled"] == "commitment:project:test"


def test_extinct_faction_project_suspends_without_refund_progress_or_scheduler_loop():
    from shinobi_runtime.martial_world.commitments import release_resources, reserve_resources
    from shinobi_runtime.martial_world.project_frontier import settle_project_frontier

    project_ref = "project:test:extinct"
    projects = {"schema": "jianghu-project-registry-1.0", "projects": {project_ref: {
        "project_type": "building_expansion", "building_type": "residential_compound", "additional_footprint_m2": 100,
        "faction_ref": "dead_house", "site_ref": "site.dead_house",
        "started_at": "0061-09-01T00:00:00", "last_progress_at": "0061-09-04T00:00:00",
        "elapsed_calendar_days": 3, "minimum_calendar_days": 10,
        "general_labor_hours_remaining": 100, "skilled_labor_hours_remaining": 50,
        "planned_skilled_worker_count": 1, "planned_management_worker_count": 0, "planned_general_worker_count": 1,
        "skilled_worker_refs": ["dead.a"], "management_worker_refs": [], "general_worker_refs": ["dead.b"],
    }}}
    commitments = reserve_resources(
        {"commitments": {}, "person_index": {}},
        resources=[("person", "dead.a", "dead_house"), ("person", "dead.b", "dead_house")],
        actor_ref="dead.a", owner_ref="dead_house", activity_ref=project_ref, activity_kind="construction",
        started_at="0061-09-01T00:00:00", location_ref="site.dead_house",
    )
    writes, reviews, pending = {}, [], []

    def load_faction(fid):
        assert fid == "dead_house"
        return "state/martial-world/factions/dead_house.json", {"faction_id": fid, "status": "extinct", "local_site_ref": "site.dead_house"}

    def load_roster(_fid):
        raise AssertionError("extinct project must not get a living roster turn")

    def settle_resume(refs, *, activity_ref, commitments_state):
        assert set(refs) == {"dead.a", "dead.b"}
        return release_resources(commitments_state, activity_ref=activity_ref)

    after = settle_project_frontier(
        events=[{"kind": "autonomous_project_due", "owner_ref": project_ref, "event_id": "due"}],
        at=datetime(61, 9, 20), projects_state=projects, commitments_state=commitments,
        writes=writes, reviews=reviews, pending_one_off_events=pending,
        faction_cache={}, roster_cache={}, load_faction=load_faction, load_roster=load_roster,
        settle_and_resume_people=settle_resume,
        pause_people_for_commitment=lambda *_args: (_ for _ in ()).throw(AssertionError("must not pause extinct staff")),
        unavailable_person_refs=lambda: set(),
    )
    row = writes["state/martial-world/projects.json"]["projects"][project_ref]
    assert row["status"] == "suspended_extinct"
    assert row["elapsed_calendar_days"] == 3
    assert row["general_labor_hours_remaining"] == 100
    assert row["planned_skilled_worker_count"] == 1
    assert row["skilled_worker_refs"] == [] and row["general_worker_refs"] == []
    assert pending == []
    assert f"commitment:{project_ref}" not in after["commitments"]
    assert reviews[-1]["result"] == "suspended_extinct"


def test_secondary_estate_building_completion_mutates_captured_site_not_primary_headquarters():
    from shinobi_runtime.martial_world.project_frontier import _apply_completion

    faction = {
        "faction_id": "house_tang", "local_site_ref": "site.house_tang",
        "buildings": {"residential_compound": 3},
        "infrastructure": {"facilities": {"residential_compound": {"footprint_m2": 1000}}},
        "controlled_estates": {
            "site.captured": {
                "source_faction_ref": "old_house", "acquired_at": "0061-09-10T00:00:00", "status": "occupied",
                "headquarters_place_ref": "changan", "buildings": {"residential_compound": 2},
                "infrastructure": {"facilities": {"residential_compound": {"footprint_m2": 300}}}, "enterprises": {},
            }
        },
    }
    project = {
        "project_type": "building_expansion", "site_ref": "site.captured",
        "building_type": "residential_compound", "additional_footprint_m2": 125,
    }
    _apply_completion(faction, project)
    assert faction["infrastructure"]["facilities"]["residential_compound"]["footprint_m2"] == 1000
    assert faction["controlled_estates"]["site.captured"]["infrastructure"]["facilities"]["residential_compound"]["footprint_m2"] == 425


def test_dynamic_faction_leader_title_comes_from_current_institution_type_not_static_identity_table():
    from shinobi_runtime.martial_world.faction_state import faction_presentation_identity
    from shinobi_runtime.martial_world.titles import derive_social_titles

    dynamic = {"faction_id": "faction.dynamic_river", "type": "outlaw_faction", "name": "River Blades"}
    identity = faction_presentation_identity("faction.dynamic_river", dynamic)
    assert identity["faction_type"] == "outlaw_faction"
    assert identity["leader_title"] == "Chief"
    titles = derive_social_titles(
        {"person_id": "leader.dynamic", "standing_offices": ["leader"]},
        faction_identity=identity, family_state={}, observer_knows_identity=True,
        observer_knows_office=True, observer_knows_faction=True,
    )
    assert titles == ["Chief"]



def test_noop_roundtrip_gate_includes_materialized_dynamic_faction(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc", "artifacts"))
    dynamic = "faction.dynamic_noop_test"
    source = "beggars_society"
    faction = json.loads((root / f"state/martial-world/factions/{source}.json").read_text())
    faction["faction_id"] = dynamic
    (root / f"state/martial-world/factions/{dynamic}.json").write_text(json.dumps(faction, indent=2) + "\n")
    roster = json.loads((root / f"state/martial-world/people/{source}.json").read_text())
    roster["faction_ref"] = dynamic
    (root / f"state/martial-world/people/{dynamic}.json").write_text(json.dumps(roster, indent=2) + "\n")
    inventory = json.loads((root / f"state/martial-world/inventories/{source}.json").read_text())
    inventory["faction_ref"] = dynamic
    (root / f"state/martial-world/inventories/{dynamic}.json").write_text(json.dumps(inventory, indent=2) + "\n")
    registry_path = root / "state/martial-world/faction-registry.json"
    registry = json.loads(registry_path.read_text())
    registry["faction_refs"] = sorted(set(registry["faction_refs"] + [dynamic]))
    registry_path.write_text(json.dumps(registry, indent=2) + "\n")
    proc = subprocess.run(
        ["python", "tools/verify_noop_roundtrip.py"], cwd=root,
        text=True, capture_output=True, check=False,
        env={**__import__('os').environ, "PYTHONPATH": "runtime"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    assert report["checked_factions"] == 241
    assert report["durable_faction_owner_refs"] == 241


def test_autonomous_project_due_is_not_double_emitted_as_generic_calendar_event():
    from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier
    projects = _load("state/martial-world/projects.json")
    project_ref, project = next(iter(projects["projects"].items()))
    event = {
        "event_id": f"autonomous_project_due:{project_ref}", "kind": "autonomous_project_due",
        "owner_ref": project_ref, "due_at": "0061-09-15T09:15:00", "requires_player_decision": False,
    }
    schedule = _load("state/martial-world/scheduler.json")
    schedule = copy.deepcopy(schedule)
    schedule.setdefault("one_off", {})[event["event_id"]] = copy.deepcopy(event)
    result = settle_martial_world_frontier(
        read_json=_reader({}), schedule=schedule, events=[event], at=datetime(61, 9, 15, 9, 15),
    )
    project_reviews = [row for row in result.get("reviews", []) if row.get("kind") == "autonomous_project_due"]
    generic = [row for row in result.get("reviews", []) if row.get("kind") == "calendar_event"]
    assert len(project_reviews) == 1
    assert generic == []


def test_closed_contract_expiry_oneoff_is_pruned_immediately():
    from shinobi_runtime.martial_world.scheduler import prune_contract_expiry_events
    schedule = {
        "schema": "jianghu-scheduler-1.0", "settled_through": "0061-09-14T09:15:00",
        "recurring": {}, "one_off": {
            "contract_expiry_due:contract.live": {
                "event_id": "contract_expiry_due:contract.live", "kind": "contract_expiry_due",
                "due_at": "0061-10-14T09:15:00", "owner_ref": "contract.live", "requires_player_decision": False,
            },
            "contract_expiry_due:contract.closed": {
                "event_id": "contract_expiry_due:contract.closed", "kind": "contract_expiry_due",
                "due_at": "0061-10-14T09:15:00", "owner_ref": "contract.closed", "requires_player_decision": False,
            },
            "family_birth_due:child": {
                "event_id": "family_birth_due:child", "kind": "family_birth_due",
                "due_at": "0062-01-01T00:00:00", "owner_ref": "house_tang", "requires_player_decision": False,
            },
        },
    }
    after = prune_contract_expiry_events(schedule, {"contract.live": {"status": "accepted"}})
    assert "contract_expiry_due:contract.live" in after["one_off"]
    assert "contract_expiry_due:contract.closed" not in after["one_off"]
    assert "family_birth_due:child" in after["one_off"]
    departed = prune_contract_expiry_events(schedule, {"contract.live": {"status": "in_progress"}})
    assert "contract_expiry_due:contract.live" not in departed["one_off"]


def test_voluntary_departure_stages_both_exact_person_owners_before_global_family_reads(monkeypatch):
    import shinobi_runtime.martial_world.faction_cycle_frontier as faction_cycle_frontier

    fid = "faction.grey_bridge_band"
    person_ref = "mw.person.faction.grey_bridge_band.5001"
    fpath = f"state/martial-world/factions/{fid}.json"
    rpath = f"state/martial-world/people/{fid}.json"
    ipath = f"state/martial-world/inventories/{fid}.json"
    faction = _load(fpath)
    roster = _load(rpath)
    inventory = _load(ipath)
    assert any(row.get("person_id") == person_ref for row in roster["people"] if isinstance(row, dict))

    # Force real hardship so the monthly member-cycle reaches voluntary exit,
    # then choose the exact identity that exposed the long-horizon regression.
    faction["treasury_cash"] = 0
    inventory["food_ration_days"] = 0
    monkeypatch.setattr(
        faction_cycle_frontier,
        "annual_voluntary_departure_refs",
        lambda *args, **kwargs: [person_ref],
    )
    overlay = {fpath: faction, rpath: roster, ipath: inventory}
    at = datetime(61, 10, 13, 21, 15)
    result = settle_martial_world_frontier(
        read_json=_reader(overlay),
        schedule=initial_schedule(start=at - timedelta(days=1), faction_ids=[], region_ids=[], route_ids=[]),
        events=[
            {"event_id": "test:departure:upkeep", "kind": "faction_upkeep", "owner_ref": fid, "requires_player_decision": False},
            {"event_id": "test:departure:members", "kind": "faction_member_cycle", "owner_ref": fid, "requires_player_decision": False},
        ],
        at=at,
    )

    roster_after = result["writes"][rpath]["people"]
    independents_after = result["writes"]["state/martial-world/independent-people.json"]["people"]
    assert sum(row.get("person_id") == person_ref for row in roster_after if isinstance(row, dict)) == 0
    assert sum(row.get("person_id") == person_ref for row in independents_after if isinstance(row, dict)) == 1
    review = next(row for row in result["reviews"] if row.get("kind") == "faction_member_cycle")
    assert review["departures"] == 1


def test_government_failed_contact_does_not_persist_attempt_history():
    from shinobi_runtime.martial_world.regional_frontier import settle_regional_frontier

    subject_ref = "mw.person.test.wanted"
    warrant_ref = f"warrant:{subject_ref}"
    government = {
        "schema": "jianghu-government-state-1.0",
        "attention": {subject_ref: {"attention": 80, "bounty_cash": 100, "prior_offenses": 1}},
        "warrants": {warrant_ref: {
            "subject_ref": subject_ref, "offense": "theft", "bounty_cash": 100,
            "bounty_escrow_cash": 100, "status": "active", "evidence_ref": "evidence:test",
            "issued_at": "0061-12-01T00:00:00", "jurisdiction_ref": "central_plain",
        }},
        "regional_capacity": {},
    }
    custody = {"schema": "jianghu-custody-state-1.0", "records": []}
    market = _load("state/martial-world/markets/central_plain.json")
    writes, reviews, handoffs = {}, [], []
    subject = {"person_id": subject_ref, "location_ref": "site.test.wanted", "health": {"status": "healthy"}}
    settle_regional_frontier(
        events=[{"kind": "regional_market_cycle", "owner_ref": "central_plain", "event_id": "test:response"}],
        at_iso="0061-12-12T21:15:00", player_ref="pc_wei_tang",
        government_state=government,
        government_troops={
            "default_regional_capacity": {"militia": 1, "standard": 0, "elite": 0},
            "monthly_reconstitution": {"militia": 0, "standard": 0, "elite": 0},
            "contact_resolution": {"militia_power": 1, "standard_power": 1, "elite_power": 1, "detention_advantage_milli": 1800},
        },
        custody_state=custody, writes=writes, reviews=reviews, handoffs=handoffs, market_cache={},
        load_market=lambda _region: ("state/martial-world/markets/central_plain.json", copy.deepcopy(market)),
        load_person_ref=lambda _ref: ("house_tang", "state/martial-world/people/house_tang.json", {}, 0, copy.deepcopy(subject)),
        unavailable_person_refs=lambda: set(), pause_people_for_commitment=lambda *_args: None,
        person_combat_index=lambda _person: 10_000,
        site_rows={"site.test.wanted": {"parent_place_ref": "luoyang"}},
        place_region={"luoyang": "central_plain"},
    )
    warrant = government["warrants"][warrant_ref]
    assert warrant["status"] == "pursuing"
    assert not ({"failed_contacts", "last_contact_at", "last_deployment"} & set(warrant))


def test_government_attention_uses_registered_monthly_decay_once_across_region_shards():
    from shinobi_runtime.martial_world.government import decay_attention_rows
    from shinobi_runtime.martial_world.regional_frontier import settle_regional_frontier

    assert decay_attention_rows({
        "repeat": {"attention": 8, "bounty_cash": 0, "prior_offenses": 1},
        "ephemeral": {"attention": 4, "bounty_cash": 0, "prior_offenses": 0},
    }) == {
        "repeat": {"attention": 3, "bounty_cash": 0, "prior_offenses": 1},
    }

    government = {
        "schema": "jianghu-government-state-1.0",
        "attention": {"subject": {"attention": 8, "bounty_cash": 0, "prior_offenses": 1}},
        "warrants": {}, "regional_capacity": {},
    }
    markets = {
        "central_plain": _load("state/martial-world/markets/central_plain.json"),
        "yunnan_highland": _load("state/martial-world/markets/yunnan_highland.json"),
    }
    writes, reviews = {}, []

    def load_market(region):
        return f"state/martial-world/markets/{region}.json", copy.deepcopy(markets[region])

    common = dict(
        at_iso="0061-10-13T21:15:00", player_ref="pc_wei_tang",
        government_state=government, government_troops={"default_regional_capacity": {}, "monthly_reconstitution": {}},
        custody_state={"records": []}, writes=writes, reviews=reviews, handoffs=[], market_cache={},
        load_market=load_market,
        load_person_ref=lambda _ref: (_ for _ in ()).throw(AssertionError("no warrant lookup expected")),
        unavailable_person_refs=lambda: set(), pause_people_for_commitment=lambda *_args: None,
        person_combat_index=lambda _person: 1, site_rows={},
        place_region={"a": "central_plain", "b": "yunnan_highland"},
    )
    settle_regional_frontier(
        events=[{"kind": "regional_market_cycle", "owner_ref": "central_plain", "event_id": "r1"}],
        **common,
    )
    assert government["attention"]["subject"]["attention"] == 3

    # The second scheduler shard at the same monthly timestamp must not apply
    # a second five-point decay.
    settle_regional_frontier(
        events=[{"kind": "regional_market_cycle", "owner_ref": "yunnan_highland", "event_id": "r2"}],
        **common,
    )
    assert government["attention"]["subject"]["attention"] == 3


def test_training_pause_is_derived_and_never_persisted_on_person_state():
    from shinobi_runtime.martial_world.commitments import derived_commitment_state
    from shinobi_runtime.martial_world.faction_state import read_faction, roster_path
    from shinobi_runtime.martial_world.person_state import compact_person_state, hydrate_roster_state
    from shinobi_runtime.martial_world.training import institutional_training_pause_refs
    from shinobi_runtime.store import RepositoryStore

    repo = RepositoryStore(ROOT)
    commitments = derived_commitment_state(repo.read_json)
    busy = set(commitments.get("person_index", {}))
    persisted = set()
    derived = set()
    for fid in _load("state/martial-world/faction-registry.json")["faction_refs"]:
        _fpath, faction = read_faction(repo, fid)
        raw = repo.read_json(roster_path(fid))
        roster = hydrate_roster_state(raw, faction=faction)
        derived.update(institutional_training_pause_refs(
            faction, [p for p in roster.get("people", []) if isinstance(p, dict)],
            unavailable_refs=sorted(busy),
        ))
        for person in raw.get("people", []):
            if not isinstance(person, dict):
                continue
            state = person.get("training_state", {}) if isinstance(person.get("training_state"), dict) else {}
            if state.get("institutional_paused") is True:
                persisted.add(str(person.get("person_id") or ""))
    assert persisted == set()
    assert derived == busy

    logical = {
        "person_id": "test.pause", "membership_grade": "full",
        "training_state": {"institutional_paused": True},
    }
    stored = compact_person_state(logical, faction_ref="test", home_location="site.test")
    assert "training_state" not in stored


def test_paused_refs_exclude_busy_instructor_and_student_before_training_snapshot():
    from shinobi_runtime.martial_world.training import settle_and_reset_faction_training_cycle

    def person(ref, sword, instruction):
        return {
            "person_id": ref, "membership_grade": "full", "birth_year": 30,
            "attributes": {"strength":50,"speed":50,"agility":50,"endurance":50,"perception":50,"intelligence":50,"willpower":50},
            "martial_skills": {"sword":sword,"spear":0,"bow":0,"hidden_weapons":0,"unarmed":0,"stealth_scouting":0,"command":0},
            "professional_skills": {"instruction":instruction,"medicine":0,"crafting":0,"commerce":0,"administration":0},
            "aptitudes": {"martial":100,"physical":100,"cognitive":100,"qi":100,"leadership":100},
            "health": {"status":"ready","consciousness":100,"injuries":[]}, "qi":20, "qi_control":20,
        }

    faction = {
        "faction_id":"test", "local_site_ref":"site.test",
        "training":{"sword":100,"spear":0,"bow":0,"hidden_weapons":0,"unarmed":0,"stealth_scouting":0,"command":0,"qi":0,"qi_control":0},
        "buildings":{"training_hall":2,"training_grounds":2,"library_records":1},
        "infrastructure":{"estate_area_m2":6000,"facilities":{"training_hall":{"footprint_m2":1000},"training_grounds":{"footprint_m2":4000},"library_records":{"footprint_m2":500}}},
        "training_epoch":{"started_at":"0061-01-01T00:00:00","settled_through":"0061-01-01T00:00:00","intensity_milli":1000},
    }
    student = person("student", 20, 0); student["location_ref"] = "site.test"
    teacher = person("teacher", 180, 180); teacher["location_ref"] = "site.test"
    roster = {"faction_ref":"test","people":[student,teacher]}
    faction_after, roster_after, _ = settle_and_reset_faction_training_cycle(
        faction, roster, at_iso="0061-02-01T00:00:00", paused_refs=["student","teacher"],
    )
    after = {p["person_id"]: p for p in roster_after["people"]}
    assert after["student"]["martial_skills"]["sword"] == 20
    assert after["teacher"]["martial_skills"]["sword"] == 180
    assert after["student"]["training_state"]["institutional_paused"] is True
    assert after["teacher"]["training_state"]["institutional_paused"] is True
    assert faction_after["training_epoch"]["started_at"] == "0061-02-01T00:00:00"


def test_route_state_never_persists_derived_cargo_value_cash():
    route_ops = _load("state/martial-world/route-operations.json")
    for movement in route_ops.get("movements", {}).values():
        if isinstance(movement, dict):
            assert "cargo_value_cash" not in movement


def test_person_storage_compacts_injury_defaults_but_preserves_permanent_outcomes():
    from shinobi_runtime.martial_world.person_state import compact_person_state

    acute = {
        "zone": "chest", "created_at": "0061-10-01T00:00:00",
        "cut": 0, "pierce": 0, "blunt": 80, "penetration": 0, "severity": 80,
        "bleeding_ml_per_min": 0, "fracture": 0, "tendon_damage": 0,
        "nerve_damage": 0, "organ_trauma": 20, "pain": 40,
        "function_loss_pct": 10, "treated": False, "healing_progress_milli": 1000,
        "structure_ref": None, "side": None, "structure_damage": 0,
        "functional_effects": {}, "permanent": False,
        "permanent_outcome": None, "permanent_effects": {},
        "stabilized": False, "healed": False,
    }
    permanent = copy.deepcopy(acute)
    permanent.update({
        "zone": "head", "structure_ref": "left_eye", "side": "left",
        "permanent": True, "permanent_outcome": "destroyed:left_eye",
        "permanent_effects": {"vision_left": 100},
        "functional_effects": {"vision_left": 100},
        "function_loss_pct": 100, "stabilized": True, "healed": True,
        "healing_progress_milli": 100000,
    })
    logical = {
        "person_id": "test.injury", "membership_grade": "full",
        "health": {"status": "injured", "injuries": [acute, permanent]},
    }
    stored = compact_person_state(logical, faction_ref="test", home_location="site.test")
    acute_after, permanent_after = stored["health"]["injuries"]
    for key in ("permanent", "permanent_outcome", "permanent_effects", "stabilized", "healed"):
        assert key not in acute_after
    assert permanent_after["permanent"] is True
    assert permanent_after["permanent_outcome"] == "destroyed:left_eye"
    assert permanent_after["permanent_effects"] == {"vision_left": 100}
    assert permanent_after["stabilized"] is True
    assert permanent_after["healed"] is True


def test_person_contract_registers_current_injury_and_medicine_shapes_without_pause_storage():
    template = _load("runtime/contracts/templates/jianghu-person-lite-roster-1.0.template.json")
    objects = template["object_contracts"]
    types = template["type_contracts"]
    injury = objects["/people/*/health/injuries/*"]
    for key in ("permanent", "permanent_outcome", "permanent_effects", "stabilized", "healed"):
        assert key in injury["allowed_keys"]
    assert objects["/people/*/health/injuries/*/permanent_effects"]["mode"] == "open_map"
    effect = objects["/people/*/medicine_state/active_effects/*"]
    assert effect["mode"] == "closed"
    assert set(effect["allowed_keys"]) == {"recipe_ref", "category", "started_at", "expires_at", "modifiers"}
    assert objects["/people/*/medicine_state/active_effects/*/modifiers"]["mode"] == "open_map"
    assert types["/people/*/medicine_state/active_effects/*/modifiers/*"] == ["integer"]
    assert "institutional_paused" not in objects["/people/*/training_state"]["allowed_keys"]
    assert "/people/*/training_state/institutional_paused" not in types


def test_reputation_contract_registers_public_evidence_audience_shape():
    template = _load("runtime/contracts/templates/jianghu-reputation-state-1.0.template.json")
    objects = template["object_contracts"]
    types = template["type_contracts"]
    row = objects["/audiences/*"]
    assert row["mode"] == "closed"
    assert set(row["allowed_keys"]) == {
        "tournament_points", "documented_contract_points", "documented_duel_points",
        "reputation", "public_score",
    }
    assert objects["/audiences/*/reputation"]["mode"] == "closed"
    assert set(objects["/audiences/*/reputation"]["allowed_keys"]) == {"martial_respect", "confidence"}
    assert types["/audiences/*/reputation/martial_respect"] == ["integer"]
    assert types["/audiences/*/reputation/confidence"] == ["integer"]

def test_long_horizon_enforces_production_state_admission_per_frontier():
    spec = importlib.util.spec_from_file_location("long_horizon_audit", ROOT / "tools/run_long_horizon.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    valid = _load("state/martial-world/government.json")
    module._validate_frontier_writes({"state/martial-world/government.json": valid})

    invalid = copy.deepcopy(valid)
    invalid["unregistered_test_field"] = 1
    try:
        module._validate_frontier_writes({"state/martial-world/government.json": invalid})
    except ValueError as exc:
        assert "unregistered keys" in str(exc)
    else:
        raise AssertionError("long-horizon verifier admitted an unregistered state field")



def test_merchant_trade_return_persists_outcome_not_sale_cash():
    route_ops = _load("state/martial-world/route-operations.json")
    movement_ref, raw = next(
        (ref, row)
        for ref, row in route_ops.get("movements", {}).items()
        if isinstance(row, dict)
        and row.get("movement_kind") == "merchant_trade"
        and row.get("trade_leg") == "outbound"
    )
    movement = copy.deepcopy(raw)
    required = max(1, int(movement.get("required_seconds", 0)))
    movement["elapsed_seconds"] = required - 1
    movement["last_progress_at"] = "0061-09-14T09:14:59"
    isolated = {
        "schema": route_ops["schema"],
        "movements": {movement_ref: movement},
        "contacts": {},
    }
    overlay = {"state/martial-world/route-operations.json": isolated}
    at = datetime(61, 9, 14, 9, 15, 0)
    schedule = initial_schedule(
        start=at - timedelta(seconds=1), faction_ids=[], region_ids=[], route_ids=[]
    )
    result = settle_martial_world_frontier(
        read_json=_reader(overlay), schedule=schedule,
        events=[{
            "kind": "route_activity_cycle",
            "owner_ref": movement["route_ref"],
            "event_id": "test:merchant:return-state",
        }],
        at=at,
    )
    after = result["writes"]["state/martial-world/route-operations.json"]["movements"]
    assert all("sale_cash" not in row for row in after.values() if isinstance(row, dict))
    returning = after[movement_ref]
    assert returning["trade_leg"] == "return"
    assert returning["trade_outcome"] in {"sold", "unsold", "cargo_lost"}



def test_strategic_warfare_death_closes_cross_faction_family_and_estate_immediately():
    from shinobi_runtime.martial_world.commitments import derived_commitment_state
    from shinobi_runtime.martial_world.faction_state import roster_path
    from shinobi_runtime.martial_world.warfare import _clear_dead_current_state

    registry = _load("state/martial-world/faction-registry.json")
    busy = set(derived_commitment_state(_reader({})).get("person_index", {}))
    dead_choice = None
    for fid in registry["faction_refs"]:
        roster = _load(roster_path(fid))
        living = [
            person for person in roster.get("people", [])
            if isinstance(person, dict)
            and (person.get("health", {}) if isinstance(person.get("health"), dict) else {}).get("status") != "dead"
        ]
        leaders = [
            person for person in living
            if "leader" in (person.get("standing_offices", []) if isinstance(person.get("standing_offices"), list) else [])
            and str(person.get("person_id") or "") != "pc_wei_tang"
        ]
        qualified_successors = [
            person for person in living
            if person not in leaders
            and str(person.get("membership_grade") or "") in {"full", "senior", "elite", "elder"}
            and str(person.get("person_id") or "") != "pc_wei_tang"
        ]
        if leaders and qualified_successors:
            dead_choice = (fid, str(leaders[0]["person_id"]), roster)
            break
    assert dead_choice is not None
    dead_fid, dead_ref, dead_roster = dead_choice

    spouse_choice = None
    for fid in registry["faction_refs"]:
        if fid == dead_fid:
            continue
        roster = _load(roster_path(fid))
        for person in roster.get("people", []):
            if not isinstance(person, dict):
                continue
            ref = str(person.get("person_id") or "")
            health = person.get("health", {}) if isinstance(person.get("health"), dict) else {}
            if ref and ref not in busy and health.get("status") != "dead":
                spouse_choice = (fid, ref, roster)
                break
        if spouse_choice is not None:
            break
    assert spouse_choice is not None
    spouse_fid, spouse_ref, spouse_roster = spouse_choice

    dead_roster = copy.deepcopy(dead_roster)
    dead_person = next(p for p in dead_roster["people"] if p.get("person_id") == dead_ref)
    dead_person["personal_cash"] = 41
    dead_person["standing_offices"] = ["leader"]
    dead_health = copy.deepcopy(dead_person.get("health", {}))
    dead_health["status"] = "dead"
    dead_person["health"] = dead_health

    spouse_roster = copy.deepcopy(spouse_roster)
    spouse_person = next(p for p in spouse_roster["people"] if p.get("person_id") == spouse_ref)
    spouse_before = int(spouse_person.get("personal_cash", 0))

    family_schema = _load("state/martial-world/family.json").get("schema", "jianghu-family-state-1.0")
    family = {
        "schema": family_schema,
        "marriages": {"test.warfare.marriage": {"spouse_refs": [dead_ref, spouse_ref], "status": "married"}},
        "parentage": {},
        "households": {"test.warfare.house": {"member_refs": [dead_ref, spouse_ref], "head_ref": dead_ref}},
        "succession_claims": {},
    }
    overlay = {
        roster_path(dead_fid): dead_roster,
        roster_path(spouse_fid): spouse_roster,
        "state/martial-world/family.json": family,
    }
    writes = {}
    _clear_dead_current_state(
        view=_reader(overlay), writes=writes, dead={dead_ref},
        involved_factions=(dead_fid, spouse_fid), at=datetime(61, 9, 15, 12, 0),
    )

    dead_after = next(p for p in writes[roster_path(dead_fid)]["people"] if p.get("person_id") == dead_ref)
    spouse_after = next(p for p in writes[roster_path(spouse_fid)]["people"] if p.get("person_id") == spouse_ref)
    assert int(dead_after.get("personal_cash", 0)) == 0
    assert dead_after.get("standing_offices", []) == []
    living_leaders = [
        person for person in writes[roster_path(dead_fid)]["people"]
        if isinstance(person, dict)
        and (person.get("health", {}) if isinstance(person.get("health"), dict) else {}).get("status") != "dead"
        and "leader" in (person.get("standing_offices", []) if isinstance(person.get("standing_offices"), list) else [])
    ]
    assert len(living_leaders) == 1
    assert living_leaders[0]["person_id"] != dead_ref
    assert int(spouse_after.get("personal_cash", 0)) == spouse_before + 41
    family_after = writes["state/martial-world/family.json"]
    assert family_after["marriages"]["test.warfare.marriage"]["status"] == "widowed"
    assert family_after["households"]["test.warfare.house"]["head_ref"] == spouse_ref

    # Strategic warfare is an extracted frontier. Estate settlement must not
    # leave hydrated/default cash fields behind merely because the shared
    # frontier compactor is bypassed.
    from shinobi_runtime.martial_world.person_state import compact_roster_state, hydrate_roster_state
    from shinobi_runtime.martial_world.faction_state import hydrate_faction_state, faction_path
    for fid in (dead_fid, spouse_fid):
        faction_after = hydrate_faction_state(writes[faction_path(fid)])
        roster_after = writes[roster_path(fid)]
        assert roster_after == compact_roster_state(
            hydrate_roster_state(roster_after, faction=faction_after), faction=faction_after,
        )



def test_direct_command_death_fills_nonhereditary_leadership_vacancy_immediately():
    from shinobi_runtime.commands.planner import RepositoryCommandPlanner
    from shinobi_runtime.martial_world.commitments import derived_commitment_state
    from shinobi_runtime.martial_world.faction_state import roster_path
    from shinobi_runtime.sim.events import CampaignTime
    from shinobi_runtime.store import RepositoryStore

    store = RepositoryStore(ROOT)
    planner = RepositoryCommandPlanner(store)
    busy = set(derived_commitment_state(store.read_json).get("person_index", {}))
    registry = store.read_json("state/martial-world/faction-registry.json")
    chosen = None
    for fid in registry.get("faction_refs", []):
        roster = store.read_json(roster_path(fid))
        living = [
            person for person in roster.get("people", [])
            if isinstance(person, dict)
            and (person.get("health", {}) if isinstance(person.get("health"), dict) else {}).get("status") != "dead"
        ]
        leaders = [
            person for person in living
            if "leader" in (person.get("standing_offices", []) if isinstance(person.get("standing_offices"), list) else [])
            and str(person.get("person_id") or "") not in busy
            and str(person.get("person_id") or "") != "pc_wei_tang"
        ]
        successors = [
            person for person in living
            if person not in leaders
            and str(person.get("membership_grade") or "") in {"full", "senior", "elite", "elder"}
            and str(person.get("person_id") or "") not in busy
            and str(person.get("person_id") or "") != "pc_wei_tang"
        ]
        if leaders and successors:
            chosen = (str(fid), str(leaders[0]["person_id"]), roster)
            break
    assert chosen is not None
    fid, dead_ref, roster = chosen
    roster = copy.deepcopy(roster)
    dead_person = next(person for person in roster["people"] if person.get("person_id") == dead_ref)
    health = copy.deepcopy(dead_person.get("health", {})); health["status"] = "dead"; health["consciousness"] = 0
    dead_person["health"] = health

    family = copy.deepcopy(store.read_json("state/martial-world/family.json"))
    claims = family.get("succession_claims", {})
    if isinstance(claims, dict):
        family["succession_claims"] = {
            key: row for key, row in claims.items()
            if not (isinstance(row, dict) and row.get("faction_ref") == fid)
        }
    result = planner._cleanup_command_deaths(
        {roster_path(fid): roster, "state/martial-world/family.json": family},
        {dead_ref}, CampaignTime.parse("SE-0061-09-15T12:00:00"),
    )
    after = result[roster_path(fid)]
    leaders = [
        person for person in after.get("people", [])
        if isinstance(person, dict)
        and (person.get("health", {}) if isinstance(person.get("health"), dict) else {}).get("status") != "dead"
        and "leader" in (person.get("standing_offices", []) if isinstance(person.get("standing_offices"), list) else [])
    ]
    assert len(leaders) == 1
    assert leaders[0]["person_id"] != dead_ref


def test_strategic_warfare_last_member_death_settles_estate_before_extinction():
    from shinobi_runtime.martial_world.faction_state import faction_path, roster_path
    from shinobi_runtime.martial_world.warfare import _clear_dead_current_state

    registry = copy.deepcopy(_load("state/martial-world/faction-registry.json"))
    template_fid = next(fid for fid in registry["faction_refs"] if fid != "house_tang")
    template_faction = copy.deepcopy(_load(faction_path(template_fid)))
    template_roster = copy.deepcopy(_load(roster_path(template_fid)))
    synthetic_fid = "faction.audit_last_member"
    synthetic_ref = "mw.person.audit.last_member"
    template_faction["faction_id"] = synthetic_fid
    template_faction["treasury_cash"] = 100
    template_faction.pop("status", None)
    person = copy.deepcopy(template_roster["people"][0])
    person["person_id"] = synthetic_ref
    person["personal_cash"] = 25
    person["standing_offices"] = ["leader"]
    health = copy.deepcopy(person.get("health", {})); health["status"] = "dead"; person["health"] = health
    synthetic_roster = copy.deepcopy(template_roster)
    synthetic_roster["faction_ref"] = synthetic_fid
    synthetic_roster["people"] = [person]
    registry["faction_refs"] = list(registry["faction_refs"]) + [synthetic_fid]
    family_schema = _load("state/martial-world/family.json").get("schema", "jianghu-family-state-1.0")
    relations = {"schema": _load("state/martial-world/faction-relations.json").get("schema"), "edges": [
        {"from_faction": synthetic_fid, "to_faction": "house_tang", "relation": "hostile"},
        {"from_faction": "house_tang", "to_faction": synthetic_fid, "relation": "hostile"},
    ]}
    overlay = {
        "state/martial-world/faction-registry.json": registry,
        faction_path(synthetic_fid): template_faction,
        roster_path(synthetic_fid): synthetic_roster,
        "state/martial-world/family.json": {
            "schema": family_schema, "marriages": {}, "parentage": {}, "households": {}, "succession_claims": {},
        },
        "state/martial-world/faction-relations.json": relations,
        "state/martial-world/custody.json": {
            "schema": "jianghu-custody-state-1.0",
            "records": [{
                "custody_id": "custody:audit:last-holder",
                "person_ref": "pc_wei_tang",
                "captor_ref": synthetic_ref,
                "holder_faction_ref": synthetic_fid,
                "status": "restrained",
                "location_ref": str(template_faction.get("local_site_ref") or "site.audit"),
                "basis": "audit", "started_at": "0061-09-15T11:00:00",
            }],
        },
    }
    writes = {}
    _clear_dead_current_state(
        view=_reader(overlay), writes=writes, dead={synthetic_ref},
        involved_factions=(synthetic_fid,), at=datetime(61, 9, 15, 12, 0),
    )
    extinct = writes[faction_path(synthetic_fid)]
    dead_after = writes[roster_path(synthetic_fid)]["people"][0]
    registry_after = writes["state/martial-world/faction-registry.json"]
    relation_after = writes["state/martial-world/faction-relations.json"]
    assert extinct["status"] == "extinct"
    assert int(extinct["treasury_cash"]) == 125
    assert int(dead_after.get("personal_cash", 0)) == 0
    assert synthetic_fid not in registry_after["faction_refs"]
    assert all(
        edge.get("from_faction") != synthetic_fid and edge.get("to_faction") != synthetic_fid
        for edge in relation_after.get("edges", [])
    )
    assert writes["state/martial-world/custody.json"]["records"] == []


def test_failed_escort_cargo_return_fails_closed_without_real_market_owner():
    from shinobi_runtime.martial_world.route_frontier import _credit_failed_escort_cargo_to_origin_market

    def missing_market(_region):
        raise FileNotFoundError("missing")

    for region, loader, expected in (
        ("", missing_market, "region unresolved"),
        ("central_plain", missing_market, "market unresolved"),
    ):
        try:
            _credit_failed_escort_cargo_to_origin_market(
                origin_region=region, item_ref="grain_jin", quantity=9,
                load_market=loader, writes={}, market_cache={},
            )
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError("failed escort cargo was allowed to vanish without a destination")

    writes = {}
    cache = {}
    market = {"region_id": "central_plain", "cash_pool": 10, "stock": {"grain_jin": 4}}
    _credit_failed_escort_cargo_to_origin_market(
        origin_region="central_plain", item_ref="grain_jin", quantity=9,
        load_market=lambda _region: ("state/martial-world/markets/central_plain.json", market),
        writes=writes, market_cache=cache,
    )
    assert writes["state/martial-world/markets/central_plain.json"]["stock"]["grain_jin"] == 13
    assert cache["central_plain"][1]["stock"]["grain_jin"] == 13



def test_extinct_institution_cannot_keep_living_person_in_custody():
    from shinobi_runtime.martial_world.death_lifecycle import release_custody_held_by_extinct_factions

    custody = {
        "schema": "jianghu-custody-state-1.0",
        "records": [
            {"custody_id":"a","person_ref":"living.a","captor_ref":"dead.guard","holder_faction_ref":"dead.house","status":"restrained"},
            {"custody_id":"b","person_ref":"living.b","captor_ref":"guard.b","holder_faction_ref":"living.house","status":"restrained"},
        ],
    }
    after, released = release_custody_held_by_extinct_factions(custody, extinct_refs=["dead.house"])
    assert [row["custody_id"] for row in after["records"]] == ["b"]
    assert released == [{"custody_id":"a","person_ref":"living.a","holder_faction_ref":"dead.house"}]


def test_tournament_double_forfeit_cannot_create_dead_champion_or_fake_points():
    from shinobi_runtime.martial_world.tournaments import advance_individual_competition

    tournament = {
        "event_id": "regional:double-forfeit", "tournament_ref": "regional:double-forfeit",
        "status": "bracket_ready",
        "registrations": [
            {"entrant_ref": "person.dead.a", "public_qualifying_score": 10},
            {"entrant_ref": "person.dead.b", "public_qualifying_score": 9},
        ],
        "bracket": [["person.dead.a", "person.dead.b"]],
        "round_number": 1, "round_winners": [], "round_participant_count": 2,
    }
    people = {
        "person.dead.a": {"person_id": "person.dead.a", "health": {"status": "dead", "consciousness": 0}},
        "person.dead.b": {"person_id": "person.dead.b", "health": {"status": "dead", "consciousness": 0}},
    }
    result = advance_individual_competition(
        tournament, people=people, equipment_ledger={}, doctrines={}, combats_state={"combats": {}},
        zone_ref="site.test", at_iso="0062-04-15T09:00:00", max_matches=8,
    )
    assert result["completed"] is True
    assert result["champion_ref"] is None
    assert result["tournament_after"]["placements"] == {}
    assert result["winner_points"] == {}
    assert result["resolved_pairs"] == []


def test_tournament_single_forfeit_advances_only_usable_entrant_without_match_points_or_runner_up():
    from shinobi_runtime.martial_world.tournaments import advance_individual_competition, placement_payouts

    tournament = {
        "event_id": "regional:single-forfeit", "tournament_ref": "regional:single-forfeit",
        "status": "bracket_ready",
        "registrations": [
            {"entrant_ref": "person.ready", "public_qualifying_score": 10},
            {"entrant_ref": "person.dead", "public_qualifying_score": 9},
        ],
        "bracket": [["person.ready", "person.dead"]],
        "round_number": 1, "round_winners": [], "round_participant_count": 2,
        "prize_escrow_cash": 10_000,
        "prize_payout_permille": {"first": 500, "second": 250, "third": 150, "fourth": 100},
    }
    people = {
        "person.ready": {"person_id": "person.ready", "health": {"status": "ready", "consciousness": 100}},
        "person.dead": {"person_id": "person.dead", "health": {"status": "dead", "consciousness": 0}},
    }
    result = advance_individual_competition(
        tournament, people=people, equipment_ledger={}, doctrines={}, combats_state={"combats": {}},
        zone_ref="site.test", at_iso="0062-04-15T09:00:00", max_matches=8,
    )
    after = result["tournament_after"]
    assert result["completed"] is True
    assert after["placements"] == {"first": "person.ready"}
    assert result["winner_points"] == {}
    assert result["resolved_pairs"] == []
    assert placement_payouts(after) == [{"place": "first", "entrant_ref": "person.ready", "cash": 10_000}]


def test_tournament_unusable_entrant_cannot_advance_on_bye():
    from shinobi_runtime.martial_world.tournaments import advance_individual_competition

    tournament = {
        "event_id": "regional:dead-bye", "tournament_ref": "regional:dead-bye",
        "tournament_kind": "regional_martial_tournament",
        "status": "bracket_ready",
        "registrations": [
            {"entrant_ref": "person.dead", "public_qualifying_score": 30},
            {"entrant_ref": "person.ready.a", "public_qualifying_score": 20},
            {"entrant_ref": "person.ready.b", "public_qualifying_score": 10},
        ],
        "bracket": [["person.dead", None], ["person.ready.a", "person.ready.b"]],
        "round_number": 1, "round_winners": [], "round_participant_count": 3,
    }
    people = {
        "person.dead": {"person_id": "person.dead", "health": {"status": "dead", "consciousness": 0}},
        "person.ready.a": {"person_id": "person.ready.a", "health": {"status": "ready", "consciousness": 100}},
        "person.ready.b": {"person_id": "person.ready.b", "health": {"status": "ready", "consciousness": 100}},
    }
    result = advance_individual_competition(
        tournament, people=people, equipment_ledger={}, doctrines={}, combats_state={"combats": {}},
        zone_ref="site.test", at_iso="0062-04-15T09:00:00", max_matches=16, max_exchanges=1,
    )
    assert result["completed"] is True
    assert result["champion_ref"] in {"person.ready.a", "person.ready.b"}
    assert result["champion_ref"] != "person.dead"
    assert "person.dead" not in result["tournament_after"].get("placements", {}).values()


def test_death_prunes_contract_principals_and_refunds_impossible_predeparture_contracts():
    from shinobi_runtime.martial_world.death_lifecycle import prune_dead_from_durable_activities
    from shinobi_runtime.martial_world.faction_registry import current_faction_refs

    market = _load("state/martial-world/markets/central_plain.json")
    cash_before = int(market["cash_pool"])
    contracts = {
        "schema": "jianghu-contract-index-1.0",
        "active": {
            "contract.partial": {
                "contract_id": "contract.partial", "contract_type": "escort",
                "issuer_ref": "market:central_plain", "beneficiary_ref": "house_tang",
                "status": "accepted", "offered_at": "0061-09-14T09:00:00",
                "expires_at": "0061-10-14T09:00:00", "escrow_cash": 111,
                "reward_cash": 111, "objective": {"kind": "escort_shipment"},
                "source_ref": "test", "participants": ["person.dead", "person.survivor"],
            },
            "contract.all-dead": {
                "contract_id": "contract.all-dead", "contract_type": "escort",
                "issuer_ref": "market:central_plain", "beneficiary_ref": "house_tang",
                "status": "accepted", "offered_at": "0061-09-14T09:00:00",
                "expires_at": "0061-10-14T09:00:00", "escrow_cash": 222,
                "reward_cash": 222, "objective": {"kind": "escort_shipment"},
                "source_ref": "test", "participants": ["person.dead"],
            },
            "contract.dead-client": {
                "contract_id": "contract.dead-client", "contract_type": "escort",
                "issuer_ref": "market:central_plain", "beneficiary_ref": None,
                "status": "offered", "offered_at": "0061-09-14T09:00:00",
                "expires_at": "0061-10-14T09:00:00", "escrow_cash": 333,
                "reward_cash": 333,
                "objective": {"kind": "escort_person", "protected_person_refs": ["person.dead"]},
                "source_ref": "test", "participants": [],
            },
            "contract.in-progress": {
                "contract_id": "contract.in-progress", "contract_type": "escort",
                "issuer_ref": "market:central_plain", "beneficiary_ref": "house_tang",
                "status": "in_progress", "offered_at": "0061-09-14T09:00:00",
                "expires_at": "0061-10-14T09:00:00", "escrow_cash": 444,
                "reward_cash": 444, "objective": {"kind": "escort_shipment"},
                "source_ref": "test", "participants": ["person.dead", "person.survivor"],
            },
        },
    }
    schedule = {
        "schema": "jianghu-scheduler-state-1.0", "settled_through": "0061-09-14T09:15:00",
        "recurring": {},
        "one_off": {
            f"contract_expiry_due:{ref}": {
                "event_id": f"contract_expiry_due:{ref}", "kind": "contract_expiry_due",
                "owner_ref": ref, "due_at": "0061-10-14T09:00:00",
                "requires_player_decision": False,
            }
            for ref in contracts["active"]
        },
    }
    overlay = {
        "state/martial-world/contracts/index.json": contracts,
        "state/martial-world/markets/central_plain.json": market,
        "state/martial-world/scheduler.json": schedule,
    }
    read = _reader(overlay)
    writes = {}
    result = prune_dead_from_durable_activities(
        read_json=read, writes=writes, dead_refs=["person.dead"],
        faction_refs=current_faction_refs(read),
    )
    active = writes["state/martial-world/contracts/index.json"]["active"]
    assert active["contract.partial"]["participants"] == ["person.survivor"]
    assert active["contract.in-progress"]["participants"] == ["person.survivor"]
    assert active["contract.in-progress"]["escrow_cash"] == 444
    assert "contract.all-dead" not in active
    assert "contract.dead-client" not in active
    assert result["closed_contract_refs"] == ["contract.all-dead", "contract.dead-client"]
    assert result["refunded_contract_cash"] == 555
    assert writes["state/martial-world/markets/central_plain.json"]["cash_pool"] == cash_before + 555
    remaining_events = writes["state/martial-world/scheduler.json"]["one_off"]
    assert "contract_expiry_due:contract.all-dead" not in remaining_events
    assert "contract_expiry_due:contract.dead-client" not in remaining_events
    assert "contract_expiry_due:contract.partial" in remaining_events
    assert "contract_expiry_due:contract.in-progress" in remaining_events


def test_death_prunes_current_tournament_delegation_roles_but_preserves_paid_registration_record():
    from shinobi_runtime.martial_world.death_lifecycle import prune_dead_from_durable_activities
    from shinobi_runtime.martial_world.faction_registry import current_faction_refs

    tournaments = {
        "schema": "jianghu-tournament-state-1.0",
        "tournaments": {
            "tournament.death.presence": {
                "status": "registration_open",
                "registrations": [
                    {"entrant_ref": "person.dead.delegate", "faction_ref": "house_tang", "fee_cash": 10},
                ],
                "delegations": {
                    "house_tang": {
                        "faction_ref": "house_tang",
                        "entrant_refs": ["person.dead.delegate", "person.living.delegate"],
                        "spectator_refs": ["person.dead.delegate"],
                        "leader_refs": ["person.dead.delegate"],
                        "senior_refs": ["person.living.delegate"],
                        "present_count": 2,
                    },
                },
            },
        },
    }
    overlay = {"state/martial-world/tournaments.json": tournaments}
    read = _reader(overlay)
    writes = {}
    result = prune_dead_from_durable_activities(
        read_json=read, writes=writes, dead_refs=["person.dead.delegate"],
        faction_refs=current_faction_refs(read),
    )
    after = writes["state/martial-world/tournaments.json"]["tournaments"]["tournament.death.presence"]
    assert after["registrations"][0]["entrant_ref"] == "person.dead.delegate"
    delegation = after["delegations"]["house_tang"]
    assert delegation["entrant_refs"] == ["person.living.delegate"]
    assert delegation["spectator_refs"] == []
    assert delegation["leader_refs"] == []
    assert delegation["senior_refs"] == ["person.living.delegate"]
    assert delegation["present_count"] == 1
    assert "state/martial-world/tournaments.json" in result["changed_paths"]


def test_estate_claim_waits_for_external_value_return_owners_but_not_physical_projects():
    from shinobi_runtime.martial_world.institutional_obligations import estate_claim_value_blockers

    fid = "faction.extinct.audit"
    overlay = {
        "state/martial-world/contracts/index.json": {
            "active": {
                "contract.issuer": {"status": "offered", "issuer_ref": fid, "beneficiary_ref": None},
                "contract.other": {"status": "offered", "issuer_ref": "market:central_plain", "beneficiary_ref": None},
            }
        },
        "state/martial-world/route-operations.json": {
            "movements": {
                "route.owned": {"status": "returning", "beneficiary_ref": fid},
                "route.incoming-hostile": {"status": "active", "beneficiary_ref": "house_tang", "target_faction_ref": fid},
            }
        },
        "state/martial-world/projects.json": {
            "projects": {"project.physical": {"faction_ref": fid, "site_ref": "site.audit", "completed": False}}
        },
    }
    blockers = estate_claim_value_blockers(_reader(overlay), fid)
    assert {(row["kind"], row["ref"]) for row in blockers} == {
        ("contract", "contract.issuer"), ("route_movement", "route.owned")
    }


def test_semantic_verifier_allows_lawful_tang_progression():
    import importlib.util
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "verify_jianghu_semantics_future_test", root / "tools/verify_jianghu_semantics.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    roster = json.loads((root / "state/martial-world/people/house_tang.json").read_text())
    kai = copy.deepcopy(next(row for row in roster["people"] if row.get("person_id") == "char.kai"))
    kai["martial_skills"]["sword"] += 7
    kai["professional_skills"]["instruction"] = 4
    kai["qi_control"] += 2
    kai["membership_grade"] = "junior"
    assert module.tang_identity_errors("char.kai", kai) == []
    broken = copy.deepcopy(kai)
    broken["birth_year"] += 1
    assert any("birth year" in err for err in module.tang_identity_errors("char.kai", broken))


def test_strategic_arrival_rechecks_current_site_controller_and_does_not_attack_new_neutral_owner(monkeypatch):
    from shinobi_runtime.martial_world import warfare
    from shinobi_runtime.martial_world.commitments import derived_commitment_state
    from shinobi_runtime.martial_world.faction_state import faction_path, roster_path

    registry = copy.deepcopy(_load("state/martial-world/faction-registry.json"))
    source_fid = "house_tang"
    target_fid = next(fid for fid in registry["faction_refs"] if fid != source_fid)
    source = copy.deepcopy(_load(faction_path(source_fid)))
    target = copy.deepcopy(_load(faction_path(target_fid)))
    target_site = str(target.get("local_site_ref") or "")
    target_place = str(target.get("headquarters") or "")
    assert target_site

    # Simulate the estate having changed hands while the force was already on
    # the road: old owner is now dormant, source faction currently controls the
    # physical site.
    registry["faction_refs"] = [fid for fid in registry["faction_refs"] if fid != target_fid]
    if target_fid not in registry.get("dormant_estate_refs", []):
        registry.setdefault("dormant_estate_refs", []).append(target_fid)
    target["status"] = "extinct"
    source.setdefault("controlled_estates", {})[target_site] = {
        "source_faction_ref": target_fid, "status": "occupied",
        "headquarters_place_ref": target_place, "buildings": {}, "infrastructure": {}, "enterprises": {},
    }

    busy = set(derived_commitment_state(_reader({})).get("person_index", {}))
    source_roster = _load(roster_path(source_fid))
    participant = next(
        str(person["person_id"]) for person in source_roster["people"]
        if isinstance(person, dict)
        and str(person.get("person_id") or "") not in busy
        and person.get("health", {}).get("status") != "dead"
    )
    deployments = copy.deepcopy(_load("state/martial-world/deployments.json"))
    op_ref = "operation.audit.changed-controller"
    deployments.setdefault("deployments", {})[op_ref] = {
        "operation_ref": op_ref, "operation_kind": "faction_raid", "status": "arrived_pending",
        "faction_ref": source_fid, "target_faction_ref": target_fid,
        "participant_refs": [participant], "target_site_ref": target_site,
        "target_place_ref": target_place, "source_place_ref": str(source.get("headquarters") or "luoyang"),
        "started_at": "0061-09-15T08:00:00",
    }
    overlay = {
        "state/martial-world/faction-registry.json": registry,
        faction_path(source_fid): source,
        faction_path(target_fid): target,
        "state/martial-world/deployments.json": deployments,
    }
    monkeypatch.setattr(
        warfare, "simulate_exact_combat",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("control-change arrival must not attack neutral/current source owner")),
    )
    at = datetime(61, 9, 15, 12, 0)
    result = warfare.settle_faction_operation_arrivals(
        read_json=_reader(overlay), writes={},
        events=[{"kind": "faction_operation_arrival", "owner_ref": op_ref, "event_id": "audit:arrival"}],
        at=at, schedule_after=_load("state/martial-world/scheduler.json"),
    )
    review = next(row for row in result["reviews"] if row.get("operation_ref") == op_ref)
    assert review["battle_outcome"] == "target_control_changed"
    assert review["current_controller_ref"] == source_fid
    after_op = result["writes"]["state/martial-world/deployments.json"]["deployments"][op_ref]
    assert after_op["status"] == "return_preparing"
    assert after_op["participant_refs"] == [participant]
    assert f"operation_departure:return:{op_ref}" in result["schedule_after"]["one_off"]


def test_large_raid_return_compacts_duplicate_roles_without_party_cap():
    from shinobi_runtime.martial_world.route_activity import compact_route_movement_roles, route_controlling_refs
    raiders = [f"raider.{i:03d}" for i in range(103)]
    captives = ["captive.a", "captive.b"]
    movement = {
        "movement_kind": "raid_return",
        "participant_refs": raiders + captives,
        "escort_refs": list(raiders),
        "raider_refs": list(raiders),
        "protected_person_refs": list(captives),
        "captive_refs": list(captives),
        "rescued_refs": [],
    }
    compact = compact_route_movement_roles(movement)
    assert len(compact["participant_refs"]) == 105
    assert "escort_refs" not in compact
    assert "raider_refs" not in compact
    assert route_controlling_refs(compact) == raiders
    assert all(ref not in route_controlling_refs(compact) for ref in captives)


def test_government_custody_guard_strength_is_a_real_escape_rescue_barrier():
    from shinobi_runtime.martial_world.crime_custody import government_rescue_infiltration

    weak = {
        "attributes": {"dexterity": 10, "perception": 10, "intelligence": 10},
        "martial_skills": {"stealth_scouting": 0},
        "professional_skills": {},
    }
    strong = {
        "attributes": {"dexterity": 90, "perception": 90, "intelligence": 90},
        "martial_skills": {"stealth_scouting": 100},
        "professional_skills": {},
    }
    assert government_rescue_infiltration(actor=weak, guard_strength=7, hour=12)["success"] is False
    assert government_rescue_infiltration(actor=strong, guard_strength=2, hour=23)["success"] is True

    command_source = (ROOT / "runtime/shinobi_runtime/commands/jianghu_extended.py").read_text(encoding="utf-8")
    assert "if holder_kind=='government':" in command_source
    assert "government_rescue_infiltration(" in command_source


def test_government_sentence_release_removes_hot_custody_and_resumes_person():
    from shinobi_runtime.martial_world.crime_custody import create_government_custody_record
    from shinobi_runtime.martial_world.regional_frontier import settle_regional_frontier

    record = create_government_custody_record(
        person_ref="mw.person.test.detained", jurisdiction_ref="central_plain",
        at="0061-12-12T21:15:00", detention_site_ref="site.test.detention",
        basis="active_warrant:test", offense="theft", guard_strength=3, sentence_days=2,
    )
    custody = {"schema": "jianghu-custody-state-1.0", "records": [record]}
    writes = {}; reviews = []; resumed = []
    settle_regional_frontier(
        events=[{"kind":"government_custody_release_due","owner_ref":record["custody_id"],"event_id":"release:test"}],
        at_iso=record["sentence_release_at"], player_ref="pc_wei_tang",
        government_state={"attention":{},"warrants":{}}, government_troops={}, custody_state=custody,
        writes=writes, reviews=reviews, handoffs=[], market_cache={},
        load_market=lambda _r: (_ for _ in ()).throw(AssertionError("market should not load")),
        load_person_ref=lambda _r: (_ for _ in ()).throw(AssertionError("person should not load")),
        unavailable_person_refs=lambda:set(), pause_people_for_commitment=lambda *_:None,
        person_combat_index=lambda _p:0, site_rows={}, place_region={}, pending_one_off_events=[],
        resume_people_training=lambda refs: resumed.extend(refs),
    )
    assert writes["state/martial-world/custody.json"]["records"] == []
    assert resumed == ["mw.person.test.detained"]
    assert reviews == [{"kind":"government_custody_release","person_ref":"mw.person.test.detained","custody_id":record["custody_id"],"result":"sentence_completed"}]


def test_tournament_frontier_reports_new_physical_deaths_for_universal_cleanup(monkeypatch):
    import shinobi_runtime.martial_world.tournament_frontier as tournament_frontier

    entrant_a = "mw.person.test.tournament.a"
    entrant_b = "mw.person.test.tournament.b"
    fid = "house_tang"
    tref = "tournament:regional_martial_tournament:0062-04-15:individual"
    tournament = {
        "tournament_ref": tref,
        "event_id": "regional_martial_tournament",
        "tournament_kind": "regional_martial_tournament",
        "status": "bracket_ready",
        "registrations": [
            {"entrant_ref": entrant_a, "faction_ref": fid, "public_qualifying_score": 10},
            {"entrant_ref": entrant_b, "faction_ref": fid, "public_qualifying_score": 9},
        ],
        "delegations": {},
        "faction_performance_points": {},
        "prize_escrow_cash": 0,
        "competition_days_completed": 0,
    }
    people = [
        {"person_id": entrant_a, "health": {"status": "ready", "consciousness": 100}},
        {"person_id": entrant_b, "health": {"status": "ready", "consciousness": 100}},
    ]
    roster = {"schema": "jianghu-person-lite-roster-1.0", "faction_ref": fid, "people": people}
    faction = {"schema": "jianghu-faction-state-1.0", "faction_id": fid, "doctrine": {}}

    def fake_advance(current, **kwargs):
        after_people = {ref: copy.deepcopy(dict(person)) for ref, person in kwargs["people"].items()}
        dead = after_people[entrant_a]
        dead["health"] = {"status": "dead", "consciousness": 0}
        return {
            "tournament_after": copy.deepcopy(dict(current)),
            "people_after": after_people,
            "equipment_ledger_after": copy.deepcopy(kwargs["equipment_ledger"]),
            "combats_state_after": copy.deepcopy(kwargs["combats_state"]),
            "winner_points": {},
            "resolved_pairs": [],
            "waiting_for_player": False,
            "continuation_required": True,
            "matches_resolved_count": 1,
        }

    monkeypatch.setattr(tournament_frontier, "advance_individual_competition", fake_advance)
    writes = {}
    result = tournament_frontier.settle_tournament_frontier(
        sorted_events=[{
            "kind": "tournament_competition_continue", "event_id": "test:tournament:continue",
            "tournament_ref": tref, "owner_ref": tref, "due_at": "0062-04-15T09:00:00",
            "competition_session_index": 1,
        }],
        at=datetime(62, 4, 15, 9, 0), at_iso="0062-04-15T09:00:00", world_seed="test",
        player_ref="", all_faction_ids=[fid],
        tournament_state={"schema": "jianghu-tournament-state-1.0", "tournaments": {tref: tournament}},
        deployments_state={"schema": "jianghu-deployment-state-1.0", "deployments": {}},
        civilian_state={"schema": "jianghu-civilian-state-1.0", "places": {}},
        reputation_state={"schema": "jianghu-reputation-state-1.0"},
        social_state={"schema": "jianghu-social-state-1.0", "relationships": {}, "courtships": {}},
        equipment_ledger={"schema": "jianghu-equipment-ledger-1.0", "person_loadouts": {}},
        combats_state={"schema": "jianghu-combat-state-1.0", "combats": {}},
        commitments_state={"schema": "jianghu-commitment-state-1.0", "reservations": []},
        writes=writes, reviews=[], handoffs=[], pending_one_off_events=[],
        faction_cache={}, inventory_cache={}, market_cache={}, roster_cache={},
        local_sites={}, site_rows={}, place_region={}, relation_index={},
        load_faction=lambda _fid: (f"state/martial-world/factions/{fid}.json", copy.deepcopy(faction)),
        load_inventory=lambda _fid: (f"state/martial-world/inventories/{fid}.json", {"faction_ref": fid}),
        load_market=lambda _region: (_ for _ in ()).throw(FileNotFoundError(_region)),
        load_roster=lambda _fid: (f"state/martial-world/people/{fid}.json", copy.deepcopy(roster)),
        load_person_ref=lambda ref: (fid, f"state/martial-world/people/{fid}.json", copy.deepcopy(roster), 0, next(copy.deepcopy(p) for p in people if p["person_id"] == ref)),
        current_faction_type=lambda _fid: "family_house",
        person_place=lambda *_args, **_kwargs: "",
        person_combat_index=lambda _person: 0,
        unavailable_person_refs=lambda: set(),
        usable_martial_people=lambda *_args, **_kwargs: copy.deepcopy(people),
        pause_people_for_commitment=lambda *_args, **_kwargs: None,
        settle_and_resume_people=lambda *_args, **_kwargs: {},
        apply_directed_relation_event=lambda *_args, **_kwargs: None,
    )
    assert result["newly_dead_refs"] == [entrant_a]
    stored = writes[f"state/martial-world/people/{fid}.json"]
    stored_a = next(row for row in stored["people"] if row["person_id"] == entrant_a)
    assert stored_a["health"]["status"] == "dead"


def test_strategic_robbery_uses_saved_disable_intent_and_secures_cash_only_on_local_return(monkeypatch):
    from shinobi_runtime.martial_world import warfare

    canonical_ref = "operation:faction_raid:black_lance_company:faction.south_gate_martial_school:006109"
    deployments = _load("state/martial-world/deployments.json")
    op = copy.deepcopy(deployments["deployments"][canonical_ref])
    op_ref = "operation:test:strategic-robbery"
    op["operation_ref"] = op_ref
    op["status"] = "arrived_pending"
    op["operation_intent"] = "robbery"
    op["targeting_intent"] = "disable"
    op.pop("physical_movement_ref", None)
    test_deployments = {"schema": deployments["schema"], "deployments": {op_ref: op}}
    route_state = _load("state/martial-world/route-operations.json")
    route_state["movements"] = {}
    route_state["contacts"] = {}
    source_path = f"state/martial-world/factions/{op['faction_ref']}.json"
    target_path = f"state/martial-world/factions/{op['target_faction_ref']}.json"
    source_before = _load(source_path)
    target_before = _load(target_path)
    target_cash_before = int(target_before["treasury_cash"])
    source_cash_before = int(source_before["treasury_cash"])
    overlay = {
        "state/martial-world/deployments.json": test_deployments,
        "state/martial-world/route-operations.json": route_state,
    }
    seen = {}

    def fake_combat(**kwargs):
        seen["targeting_intent"] = kwargs["targeting_intent"]
        return {
            "people_after": copy.deepcopy(kwargs["people"]),
            "equipment_ledger_after": copy.deepcopy(kwargs["equipment_ledger"]),
            "winner_side": "side_a", "resolved": True, "exchanges": 1,
        }

    monkeypatch.setattr(warfare, "simulate_exact_combat", fake_combat)
    at = datetime(61, 9, 15, 12, 0)
    schedule = initial_schedule(start=at - timedelta(hours=1), faction_ids=[], region_ids=[], route_ids=[])
    arrival = warfare.settle_faction_operation_arrivals(
        read_json=_reader(overlay), writes={},
        events=[{"kind": "faction_operation_arrival", "owner_ref": op_ref, "event_id": "test:raid:robbery:arrival"}],
        at=at, schedule_after=schedule,
    )
    after = dict(overlay)
    after.update({str(k): copy.deepcopy(v) for k, v in arrival["writes"].items()})
    assert seen["targeting_intent"] == "disable"
    review = next(row for row in arrival["reviews"] if row.get("operation_ref") == op_ref)
    assert review["raid_objective"]["result"] == "cash_seized_in_transit"
    seized = int(review["raid_objective"]["cash"])
    assert seized > 0
    assert int(after[target_path]["treasury_cash"]) == target_cash_before - seized
    assert int(after[source_path]["treasury_cash"]) == source_cash_before
    returning = after["state/martial-world/deployments.json"]["deployments"][op_ref]
    assert returning["status"] == "return_preparing"
    assert int(returning["seized_cash"]) == seized

    departure_event = next(
        row for row in arrival["schedule_after"].get("one_off", {}).values()
        if row.get("kind") == "faction_operation_departure" and row.get("owner_ref") == op_ref
    )
    departure_at = datetime.fromisoformat(departure_event["due_at"])
    departure = warfare.settle_faction_operation_departures(
        read_json=_reader(after), writes={}, events=[departure_event], at=departure_at,
        schedule_after=arrival["schedule_after"],
    )
    after.update({str(k): copy.deepcopy(v) for k, v in departure["writes"].items()})
    traveling = after["state/martial-world/deployments.json"]["deployments"][op_ref]
    assert traveling["status"] == "traveling_return"
    assert "physical_movement_ref" not in traveling
    assert int(after[source_path]["treasury_cash"]) == source_cash_before

    return_event = next(
        row for row in departure["schedule_after"].get("one_off", {}).values()
        if row.get("kind") == "faction_operation_return" and row.get("owner_ref") == op_ref
    )
    return_at = datetime.fromisoformat(return_event["due_at"])
    finished = warfare.settle_faction_operation_returns(
        read_json=_reader(after), writes={}, events=[return_event], at=return_at,
        schedule_after=departure["schedule_after"],
    )
    after.update({str(k): copy.deepcopy(v) for k, v in finished["writes"].items()})
    assert op_ref not in after["state/martial-world/deployments.json"]["deployments"]
    assert int(after[source_path]["treasury_cash"]) == source_cash_before + seized
    assert int(after[source_path]["treasury_cash"]) + int(after[target_path]["treasury_cash"]) == source_cash_before + target_cash_before


def test_strategic_kidnapping_creates_real_custody_and_local_return_uses_deployment_as_transit_owner(monkeypatch):
    from shinobi_runtime.martial_world import warfare

    canonical_ref = "operation:faction_raid:faction.red_willow_band:faction.southern_merchants_society:006109"
    deployments = _load("state/martial-world/deployments.json")
    op = copy.deepcopy(deployments["deployments"][canonical_ref])
    op_ref = "operation:test:strategic-kidnapping"
    op["operation_ref"] = op_ref
    op["status"] = "arrived_pending"
    op["operation_intent"] = "kidnapping"
    op["targeting_intent"] = "disable"
    op.pop("physical_movement_ref", None)
    test_deployments = {"schema": deployments["schema"], "deployments": {op_ref: op}}
    route_state = _load("state/martial-world/route-operations.json")
    route_state["movements"] = {}
    route_state["contacts"] = {}
    overlay = {
        "state/martial-world/deployments.json": test_deployments,
        "state/martial-world/route-operations.json": route_state,
        "state/martial-world/custody.json": {"schema": "jianghu-custody-state-1.0", "records": []},
    }
    seen = {}

    def fake_combat(**kwargs):
        seen["targeting_intent"] = kwargs["targeting_intent"]
        people_after = copy.deepcopy(kwargs["people"])
        for ref in kwargs["side_b_refs"]:
            health = copy.deepcopy(people_after[ref].get("health", {}))
            health["status"] = "incapacitated"
            health["consciousness"] = 0
            people_after[ref]["health"] = health
        return {
            "people_after": people_after,
            "equipment_ledger_after": copy.deepcopy(kwargs["equipment_ledger"]),
            "winner_side": "side_a", "resolved": True, "exchanges": 2,
        }

    monkeypatch.setattr(warfare, "simulate_exact_combat", fake_combat)
    at = datetime(61, 9, 15, 12, 0)
    schedule = initial_schedule(start=at - timedelta(hours=1), faction_ids=[], region_ids=[], route_ids=[])
    arrival = warfare.settle_faction_operation_arrivals(
        read_json=_reader(overlay), writes={},
        events=[{"kind": "faction_operation_arrival", "owner_ref": op_ref, "event_id": "test:raid:kidnap:arrival"}],
        at=at, schedule_after=schedule,
    )
    after = dict(overlay)
    after.update({str(k): copy.deepcopy(v) for k, v in arrival["writes"].items()})
    assert seen["targeting_intent"] == "disable"
    review = next(row for row in arrival["reviews"] if row.get("operation_ref") == op_ref)
    assert review["raid_objective"]["result"] == "captive_seized_in_transit"
    captive_ref = review["raid_objective"]["captive_ref"]
    custody = after["state/martial-world/custody.json"]["records"]
    record = next(row for row in custody if row.get("person_ref") == captive_ref)
    assert record["holder_faction_ref"] == op["faction_ref"]
    assert record["location_ref"] == _load(f"state/martial-world/factions/{op['target_faction_ref']}.json")["local_site_ref"]
    returning = after["state/martial-world/deployments.json"]["deployments"][op_ref]
    assert captive_ref in returning["participant_refs"]
    assert captive_ref in returning["captive_refs"]
    assert returning["status"] == "return_preparing"

    departure_event = next(
        row for row in arrival["schedule_after"].get("one_off", {}).values()
        if row.get("kind") == "faction_operation_departure" and row.get("owner_ref") == op_ref
    )
    departure_at = datetime.fromisoformat(departure_event["due_at"])
    departure = warfare.settle_faction_operation_departures(
        read_json=_reader(after), writes={}, events=[departure_event], at=departure_at,
        schedule_after=arrival["schedule_after"],
    )
    after.update({str(k): copy.deepcopy(v) for k, v in departure["writes"].items()})
    traveling = after["state/martial-world/deployments.json"]["deployments"][op_ref]
    assert traveling["status"] == "traveling_return"
    assert "physical_movement_ref" not in traveling
    custody = after["state/martial-world/custody.json"]["records"]
    record = next(row for row in custody if row.get("person_ref") == captive_ref)
    assert record["location_ref"] == op_ref


def test_third_party_rescue_local_return_stages_real_owner_repatriation():
    import copy
    import json
    from datetime import datetime
    from pathlib import Path


    from shinobi_runtime.martial_world.scheduler import initial_schedule
    from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier

    root = Path(__file__).resolve().parents[2]
    def load(rel):
        return json.loads((root / rel).read_text())
    rescued_ref = "mw.person.golden_river_escorts.0001"
    rescuer_ref = "char.zhu"
    op_ref = "operation:test:third-party-rescue-return"
    at = datetime(61, 9, 14, 10, 15)

    golden_roster = copy.deepcopy(load("state/martial-world/people/golden_river_escorts.json"))
    for person in golden_roster["people"]:
        if person.get("person_id") == rescued_ref:
            person["location_ref"] = "site.red_willow_band"
            break
    tang_roster = copy.deepcopy(load("state/martial-world/people/house_tang.json"))
    deployments = copy.deepcopy(load("state/martial-world/deployments.json"))
    deployments["deployments"] = {
        op_ref: {
            "faction_ref": "house_tang",
            "target_faction_ref": "faction.red_willow_band",
            "operation_kind": "custody_rescue",
            "participant_refs": [rescuer_ref, rescued_ref],
            "source_place_ref": "luoyang",
            "source_site_ref": "site.house_tang",
            "target_place_ref": "luoyang",
            "target_site_ref": "site.red_willow_band",
            "status": "traveling_return",
            "started_at": at.isoformat(),
            "arrival_at": at.isoformat(),
            "repatriate_after_return": {
                "person_ref": rescued_ref,
                "owner_faction_ref": "golden_river_escorts",
                "cause_ref": "custody:test:third-party",
            },
        }
    }
    overlay = {
        "state/martial-world/deployments.json": deployments,
        "state/martial-world/people/golden_river_escorts.json": golden_roster,
        "state/martial-world/people/house_tang.json": tang_roster,
    }
    def reader(rel):
        return copy.deepcopy(overlay[rel] if rel in overlay else load(rel))

    schedule = initial_schedule(start=at, faction_ids=[], region_ids=[], route_ids=[])
    result = settle_martial_world_frontier(
        read_json=reader, schedule=schedule,
        events=[{
            "event_id": f"operation_return:{op_ref}", "kind": "faction_operation_return",
            "due_at": at.isoformat(), "owner_ref": op_ref, "requires_player_decision": False,
        }],
        at=at,
    )
    after = dict(overlay)
    after.update({str(k): copy.deepcopy(v) for k, v in result["writes"].items()})
    rows = after["state/martial-world/deployments.json"]["deployments"]
    assert op_ref not in rows
    followup_ref = next(ref for ref, row in rows.items() if row.get("operation_kind") == "captive_repatriation")
    followup = rows[followup_ref]
    assert followup["participant_refs"] == [rescued_ref]
    assert followup["faction_ref"] == "golden_river_escorts"
    assert followup["target_place_ref"] == "luoyang"
    assert followup["source_place_ref"] == "kaifeng"
    departure = next(
        event for event in result["schedule_after"].get("one_off", {}).values()
        if event.get("kind") == "faction_operation_departure" and event.get("owner_ref") == followup_ref
    )
    assert departure["direction"] == "return"
    rescued = next(
        person for person in after["state/martial-world/people/golden_river_escorts.json"]["people"]
        if person.get("person_id") == rescued_ref
    )
    assert rescued.get("location_ref") == "site.house_tang"


def test_shared_cargo_credit_uses_canonical_material_catalog_for_omitted_materials():
    from shinobi_runtime.martial_world.frontier_support import credit_cargo_to_inventory

    inventory = {"raw_materials": {}, "equipment": {}}
    credit_cargo_to_inventory(inventory, item_ref="brick_tile_kg", quantity=17)
    credit_cargo_to_inventory(inventory, item_ref="lime_kg", quantity=9)
    credit_cargo_to_inventory(inventory, item_ref="fletching_set_24", quantity=3)
    assert inventory["raw_materials"] == {
        "brick_tile_kg": 17,
        "lime_kg": 9,
        "fletching_set_24": 3,
    }
    assert inventory["equipment"] == {}


def test_route_public_offense_classifies_actual_contact_intent():
    from shinobi_runtime.martial_world.route_frontier import _public_offense_for_route_intent

    assert _public_offense_for_route_intent("kidnap_principal") == "kidnapping"
    assert _public_offense_for_route_intent("rob_cargo") == "robbery"
    assert _public_offense_for_route_intent("revenge") == "assault"
    assert _public_offense_for_route_intent("hostile_interception") == "assault"


def test_monthly_autonomy_death_prunes_live_deployment_and_route_same_frontier(monkeypatch):
    import copy
    import json
    from datetime import datetime, timedelta
    from pathlib import Path


    import shinobi_runtime.martial_world.autonomy_frontier as autonomy_frontier
    from shinobi_runtime.martial_world.commitments import derived_commitment_state
    from shinobi_runtime.martial_world.scheduler import initial_schedule
    from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier

    root = Path(__file__).resolve().parents[2]

    def load(rel):
        return json.loads((root / rel).read_text())

    fid = "faction.red_road_band"
    victim = "mw.person.faction.red_road_band.0006"
    survivor = "mw.person.faction.red_road_band.0007"
    op_ref = "operation:faction_raid:faction.red_road_band:faction.misty_ridge_sect:006109"
    movement_ref = f"{op_ref}:return:test"

    roster = copy.deepcopy(load(f"state/martial-world/people/{fid}.json"))
    for person in roster["people"]:
        if person.get("person_id") != victim:
            continue
        person["health"] = {
            "status": "incapacitated",
            "consciousness": 50,
            "shock": 150,
            "injuries": [{
                "zone": "chest", "severity": 200, "organ_trauma": 200,
                "pain": 200, "bleeding_ml_per_min": 0,
                "healing_progress_milli": 0, "treated": False,
            }],
        }
        break

    deployments = copy.deepcopy(load("state/martial-world/deployments.json"))
    op = copy.deepcopy(deployments["deployments"][op_ref])
    op["status"] = "returning"
    op["participant_refs"] = [victim, survivor]
    op["physical_movement_ref"] = movement_ref
    deployments["deployments"][op_ref] = op

    route_ops = copy.deepcopy(load("state/martial-world/route-operations.json"))
    # The supplied live save may already contain this raid's outbound movement.
    # Build a coherent synthetic returning fixture instead of double-booking the
    # same exact people into both the live outbound owner and this return owner.
    movements = route_ops.setdefault("movements", {})
    for existing_ref, existing in list(movements.items()):
        if isinstance(existing, dict) and str(existing.get("operation_ref") or "") == op_ref:
            movements.pop(existing_ref, None)
    movements[movement_ref] = {
        "movement_kind": "raid_return",
        "purpose_ref": op_ref,
        "operation_ref": op_ref,
        "beneficiary_ref": fid,
        "route_ref": "route.kunming.dali",
        "route_refs": ["route.kunming.dali"],
        "route_index": 0,
        "journey_nodes": ["kunming", "dali"],
        "origin_place_ref": "kunming",
        "destination_place_ref": "dali",
        "segment_origin_place_ref": "kunming",
        "segment_destination_place_ref": "dali",
        "segment_required_seconds": [86400],
        "required_seconds": 86400,
        "elapsed_seconds": 0,
        "started_at": "0061-10-13T20:15:00",
        "last_progress_at": "0061-10-13T20:15:00",
        "status": "active",
        "participant_refs": [victim, survivor],
        "raider_refs": [victim, survivor],
        "captive_refs": [],
        "leader_ref": victim,
    }

    overlay = {
        f"state/martial-world/people/{fid}.json": roster,
        "state/martial-world/deployments.json": deployments,
        "state/martial-world/route-operations.json": route_ops,
    }

    def read_json(rel):
        return copy.deepcopy(overlay[rel] if rel in overlay else load(rel))

    monkeypatch.setattr(
        autonomy_frontier,
        "autonomy_review",
        lambda *_args, **_kwargs: {"ordered_actions": [], "scored_actions": []},
    )
    monkeypatch.setattr(
        autonomy_frontier,
        "apply_autonomous_clinical_treatment",
        lambda _f, r, i, **_kwargs: {
            "roster": copy.deepcopy(r), "inventory": copy.deepcopy(i),
            "treated_refs": [], "doses_used": 0,
        },
    )

    at = datetime(61, 10, 13, 21, 15)
    schedule = initial_schedule(start=at - timedelta(hours=1), faction_ids=[], region_ids=[], route_ids=[])
    result = settle_martial_world_frontier(
        read_json=read_json,
        schedule=schedule,
        events=[
            {"kind": "faction_review", "owner_ref": fid, "event_id": "test:autonomy-death"},
            {
                "kind": "route_activity_cycle", "owner_ref": "route.kunming.dali",
                "event_id": "test:autonomy-death:same-frontier-route",
                "requires_player_decision": False,
            },
        ],
        at=at,
    )
    after = dict(overlay)
    after.update({str(k): copy.deepcopy(v) for k, v in result["writes"].items()})

    roster_after = after[f"state/martial-world/people/{fid}.json"]
    victim_after = next(p for p in roster_after["people"] if p.get("person_id") == victim)
    assert victim_after["health"]["status"] == "dead"

    op_after = after["state/martial-world/deployments.json"]["deployments"][op_ref]
    assert victim not in op_after.get("participant_refs", [])
    movement_after = after["state/martial-world/route-operations.json"]["movements"][movement_ref]
    assert victim not in movement_after.get("participant_refs", [])
    assert victim not in movement_after.get("raider_refs", [])
    assert movement_after.get("leader_ref") == survivor
    assert victim not in derived_commitment_state(lambda rel: copy.deepcopy(after[rel] if rel in after else load(rel)))["person_index"]


def test_death_controller_loss_distinguishes_stranded_carried_person_from_recoverable_controller():
    from shinobi_runtime.martial_world.death_lifecycle import prune_dead_from_durable_activities
    from shinobi_runtime.martial_world.faction_registry import current_faction_refs

    dead = "mw.person.test.dead.controller"
    wounded = "mw.person.house_tang.1032"
    rescued = "mw.person.golden_river_escorts.0001"
    route_ops = {
        "schema": "jianghu-route-operations-state-1.0",
        "movements": {
            "route:test:carried-only": {
                "movement_kind": "faction_operation_travel", "route_ref": "route.luoyang.changan",
                "purpose_ref": "operation:test:carried-only", "journey_phase": "return",
                "participant_refs": [dead, rescued], "escort_refs": [dead],
                "protected_person_refs": [rescued], "rescued_refs": [rescued],
                "status": "active",
            },
            "route:test:wounded-potential": {
                "movement_kind": "faction_operation_travel", "route_ref": "route.luoyang.changan",
                "purpose_ref": "operation:test:wounded-potential", "journey_phase": "return",
                "participant_refs": [dead, wounded, rescued], "escort_refs": [dead],
                "protected_person_refs": [rescued], "rescued_refs": [rescued],
                "status": "active",
            },
        },
        "contacts": {},
    }
    read = _reader({"state/martial-world/route-operations.json": route_ops})
    writes = {}
    result = prune_dead_from_durable_activities(
        read_json=read, writes=writes, dead_refs=[dead], faction_refs=current_faction_refs(read),
    )
    after = writes["state/martial-world/route-operations.json"]["movements"]
    carried_only = after["route:test:carried-only"]
    assert carried_only["participant_refs"] == [rescued]
    assert route_controlling_refs(carried_only) == []
    assert carried_only["status"] == "party_extinguished"

    recoverable = after["route:test:wounded-potential"]
    assert recoverable["participant_refs"] == [wounded, rescued]
    assert recoverable["status"] == "awaiting_return_logistics"
    assert route_controlling_refs(recoverable) == []
    assert result["extinguished_route_refs"] == ["route:test:carried-only"]


def test_extinguished_custody_rescue_return_strands_rescued_npc_and_stages_real_repatriation():
    movement_ref = "route:test:rescue-party-lost"
    op_ref = "operation:test:rescue-party-lost"
    rescued_ref = "mw.person.golden_river_escorts.0001"
    route_ref = "route.luoyang.changan"
    route_ops = {
        "schema": "jianghu-route-operations-state-1.0",
        "movements": {
            movement_ref: {
                "movement_kind": "faction_operation_travel", "route_ref": route_ref,
                "purpose_ref": op_ref, "operation_ref": op_ref,
                "origin_place_ref": "changan", "destination_place_ref": "luoyang",
                "segment_origin_place_ref": "changan", "segment_destination_place_ref": "luoyang",
                "required_seconds": 86400, "elapsed_seconds": 1200,
                "beneficiary_ref": "house_tang", "participant_refs": [rescued_ref],
                "escort_refs": [], "protected_person_refs": [rescued_ref], "rescued_refs": [rescued_ref],
                "journey_phase": "return", "status": "party_extinguished",
            }
        },
        "contacts": {},
    }
    deployments = _load("state/martial-world/deployments.json")
    deployments["deployments"] = {
        op_ref: {
            "faction_ref": "house_tang", "target_faction_ref": "faction.red_willow_band",
            "operation_kind": "custody_rescue", "participant_refs": [rescued_ref],
            "captive_ref": rescued_ref, "custody_id": "custody:test:rescue-party-lost",
            "source_place_ref": "luoyang", "target_place_ref": "changan",
            "status": "traveling_return", "physical_movement_ref": movement_ref,
            "repatriate_after_return": {
                "person_ref": rescued_ref, "owner_faction_ref": "golden_river_escorts",
                "cause_ref": "custody:test:rescue-party-lost",
            },
        }
    }
    at = datetime(61, 9, 15, 9, 15)
    result = settle_martial_world_frontier(
        read_json=_reader({
            "state/martial-world/route-operations.json": route_ops,
            "state/martial-world/deployments.json": deployments,
        }),
        schedule=initial_schedule(start=at - timedelta(hours=1), faction_ids=[], region_ids=[], route_ids=[]),
        events=[{"kind": "route_activity_cycle", "owner_ref": route_ref, "event_id": "test:rescue-party-lost"}],
        at=at,
    )
    movements = result["writes"]["state/martial-world/route-operations.json"]["movements"]
    assert movement_ref not in movements
    after_deployments = result["writes"]["state/martial-world/deployments.json"]["deployments"]
    assert op_ref not in after_deployments
    rep_refs = [ref for ref, row in after_deployments.items() if isinstance(row, dict) and row.get("operation_kind") == "captive_repatriation"]
    assert len(rep_refs) == 1
    rep = after_deployments[rep_refs[0]]
    assert rep["participant_refs"] == [rescued_ref]
    assert rep["target_place_ref"] == "changan"
    assert rep["source_place_ref"] == "kaifeng"
    review = next(row for row in result["reviews"] if row.get("kind") == "route_activity_cycle")
    outcome = review["closed_outcomes"][movement_ref]
    assert outcome["party_extinguished"] is True
    assert outcome["stranded_rescued_refs"] == [rescued_ref]
    assert outcome["repatriation_operation_refs"] == rep_refs


def test_extinguished_escort_with_surviving_protected_client_strands_client_at_real_endpoint():
    contract_ref = "contract:test:extinguished-client"
    movement_ref = "route:test:extinguished-client"
    protected_ref = "mw.person.house_tang.1050"
    route_ref = "route.changan.huashan"

    contracts = _load("state/martial-world/contracts/index.json")
    sample = copy.deepcopy(next(iter(contracts["active"].values())))
    sample.update({
        "contract_type": "escort", "issuer_ref": "market:central_plain",
        "beneficiary_ref": "jade_gate_escorts", "status": "in_progress",
        "escrow_cash": 123, "reward_cash": 123,
        "objective": {
            "kind": "escort_person", "route_ref": route_ref,
            "source_place_ref": "changan", "destination_place_ref": "huashan",
            "protected_person_refs": [protected_ref],
        },
        "participants": [],
    })
    contracts["active"] = {contract_ref: sample}
    market_path = "state/martial-world/markets/central_plain.json"
    market = _load(market_path)
    market_before = int(market["cash_pool"])
    route_ops = {
        "schema": "jianghu-route-operations-state-1.0",
        "movements": {
            movement_ref: {
                "movement_kind": "escort_contract", "contract_ref": contract_ref,
                "route_ref": route_ref, "origin_place_ref": "changan", "destination_place_ref": "huashan",
                "segment_origin_place_ref": "changan", "segment_destination_place_ref": "huashan",
                "required_seconds": 86400, "elapsed_seconds": 1200,
                "beneficiary_ref": "jade_gate_escorts", "participant_refs": [protected_ref],
                "escort_refs": [], "protected_person_refs": [protected_ref],
                "status": "party_extinguished",
            },
        },
        "contacts": {},
    }
    at = datetime(61, 9, 15, 9, 15)
    result = settle_martial_world_frontier(
        read_json=_reader({
            "state/martial-world/contracts/index.json": contracts,
            "state/martial-world/route-operations.json": route_ops,
            market_path: market,
        }),
        schedule=initial_schedule(start=at - timedelta(hours=1), faction_ids=[], region_ids=[], route_ids=[]),
        events=[{"kind": "route_activity_cycle", "owner_ref": route_ref, "event_id": "test:extinguished-client"}],
        at=at,
    )
    assert movement_ref not in result["writes"]["state/martial-world/route-operations.json"]["movements"]
    assert contract_ref not in result["writes"]["state/martial-world/contracts/index.json"]["active"]
    assert int(result["writes"][market_path]["cash_pool"]) == market_before + 123
    tang_roster = result["writes"]["state/martial-world/people/house_tang.json"]
    protected = next(row for row in tang_roster["people"] if row.get("person_id") == protected_ref)
    location_ref = str(protected.get("location_ref") or "")
    sites = _load("game/data/martial-world/local-sites.json").get("sites", {})
    site = sites.get(location_ref, {}) if isinstance(sites, dict) else {}
    assert location_ref == "changan" or site.get("parent_place_ref") == "changan"
    review = next(row for row in result["reviews"] if row.get("kind") == "route_activity_cycle")
    outcome = review["closed_outcomes"][movement_ref]
    assert outcome["stranded_carried_refs"] == [protected_ref]
    assert outcome["stranded_place_ref"] == "changan"
