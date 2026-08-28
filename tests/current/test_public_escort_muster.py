import copy

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.jianghu_institutional import JianghuInstitutionalCommandsMixin
from shinobi_runtime.commands.jianghu_institutional_escort import JianghuInstitutionalEscortCommandsMixin
from shinobi_runtime.martial_world.institutional_operations import OPERATIONS_PATH
from shinobi_runtime.sim.events import CampaignTime


CONTRACTS = "state/martial-world/contracts/index.json"
ROUTES = "state/martial-world/route-operations.json"
SCHEDULE = "state/martial-world/scheduler.json"
GEOGRAPHY = "game/data/martial-world/geography.json"
TRAVEL = "game/data/martial-world/travel.json"
SITES = "game/data/martial-world/local-sites.json"
FACTION = "state/martial-world/factions/house_tang.json"
INVENTORY = "state/martial-world/inventories/house_tang.json"
MARKET = "state/martial-world/markets/central_plain.json"
SCENE = "state/scene.json"


class _Repository:
    def __init__(self, records):
        self.records = records

    def read_json(self, path):
        if path not in self.records:
            raise FileNotFoundError(path)
        return copy.deepcopy(self.records[path])


class _Harness(JianghuInstitutionalEscortCommandsMixin, JianghuInstitutionalCommandsMixin):
    scene_path = SCENE

    def __init__(self, records, people):
        self.repository = _Repository(records)
        self.people = people

    def _require_jianghu(self, meta):
        return None

    def _institutional_person(self, ref):
        person = copy.deepcopy(self.people[ref])
        return "state/martial-world/people/house_tang.json", {"people": list(self.people.values())}, 0, person

    def _require_person_available_for_activity(self, ref, *_args):
        return None

    def _pause_institutional_training_now(self, refs, current_time):
        return FACTION, copy.deepcopy(self.repository.records[FACTION]), "state/martial-world/people/house_tang.json", {"people": list(self.people.values())}

    def _simple_plan(self, command, meta, current_time, *, writes_records, code, result, **kwargs):
        return {
            "code": code,
            "writes": writes_records,
            "result": result,
            "scene": copy.deepcopy(kwargs.get("scene")),
        }


def _fixture(origin="luoyang"):
    refs = ["pc_wei_tang", "retinue.medic", "retinue.guard", "retinue.scout"] + [f"temp.{i}" for i in range(8)]
    people = {
        ref: {
            "person_id": ref,
            "faction_ref": "house_tang",
            "place_ref": origin,
            "health": {"status": "ready", "consciousness": 100},
        }
        for ref in refs
    }
    operation = {
        "operation_ref": "mission:escort:test",
        "faction_ref": "house_tang",
        "mission_kind": "escort",
        "operation_kind": "escort_contract",
        "linked_contract_ref": "contract.test",
        "phase": "approved",
        "participant_refs": refs,
        "commander_ref": "pc_wei_tang",
    }
    records = {
        OPERATIONS_PATH: {"schema": "jianghu-institutional-operations-state-1.0", "active": {operation["operation_ref"]: operation}, "archive": {}},
        CONTRACTS: {"active": {"contract.test": {"contract_type": "escort", "status": "accepted", "beneficiary_ref": "house_tang", "participants": ["pc_wei_tang"], "objective": {"source_place_ref": "huashan", "destination_place_ref": "changan"}}}},
        ROUTES: {"schema": "jianghu-route-operations-state-1.0", "movements": {}, "contacts": {}},
        SCHEDULE: {"schema": "jianghu-scheduler-1.0", "settled_through": "0061-09-14T09:15:00", "recurring": {}, "one_off": {}},
        GEOGRAPHY: {"places": {"luoyang": {"climate_profile": "central_plain"}, "huashan": {"climate_profile": "central_plain"}, "changan": {"climate_profile": "central_plain"}}},
        TRAVEL: {},
        SITES: {"sites": {}},
        FACTION: {"faction_id": "house_tang", "treasury_cash": 100},
        INVENTORY: {"faction_ref": "house_tang", "food_ration_days": 100},
        MARKET: {"region_id": "central_plain", "cash_pool": 20},
        SCENE: {
            "location_id": "site.house_tang",
            "present_person_ids": ["pc_wei_tang"],
            "visible_person_ids": ["pc_wei_tang"],
        },
    }
    return refs, people, records


def _command(action="dispatch"):
    class Command:
        actor_id = "pc_wei_tang"
        command_type = "jianghu_institutional_operation_resolution"
        payload = {"action": action, "operation_ref": "mission:escort:test"}
    return Command()


def _patch_physics(monkeypatch, captured):
    import shinobi_runtime.commands.jianghu_institutional_escort as module

    monkeypatch.setattr(module, "read_faction", lambda repository, faction_ref: (FACTION, copy.deepcopy(repository.records[FACTION])))
    monkeypatch.setattr(module, "person_place", lambda person, **_kwargs: person["place_ref"])
    monkeypatch.setattr(module, "hydrate_contract_escort_objective", lambda objective, **_kwargs: {"source_place_ref": "huashan", "minimum_escort_count": 12})
    monkeypatch.setattr(module, "derived_commitment_state", lambda read_json: {})
    monkeypatch.setattr(module, "reserve_resources", lambda state, **_kwargs: state)
    monkeypatch.setattr(
        module,
        "travel_plan",
        lambda **_kwargs: {
            "travel_hours": 3.0,
            "toll_cash": 5,
            "nodes": ["luoyang", "huashan"],
            "segments": [{"edge_id": "route.luoyang.huashan", "origin_place_ref": "luoyang", "destination_place_ref": "huashan", "hours": 3.0, "toll_cash": 5}],
        },
    )
    monkeypatch.setattr(module, "provisioning_journey_seconds", lambda plan: 10800)
    monkeypatch.setattr(
        module,
        "reserve_faction_rations",
        lambda inventory, **kwargs: ({**copy.deepcopy(inventory), "food_ration_days": int(inventory["food_ration_days"]) - int(kwargs["participant_count"])}, {"source_kind": "faction", "source_ref": "house_tang", "participant_count": int(kwargs["participant_count"]), "planned_travel_seconds": int(kwargs["travel_seconds"]), "ration_days_reserved": int(kwargs["participant_count"]), "ration_days_consumed": 0, "journey_elapsed_seconds": 0}),
    )

    def build_route_journey(**kwargs):
        captured.update(copy.deepcopy(kwargs))
        return {
            "movement_ref": kwargs["movement_ref"],
            "movement_kind": kwargs["movement_kind"],
            "participant_refs": list(kwargs["participants"]),
            "leader_ref": kwargs["leader_ref"],
            "route_ref": kwargs["plan"]["segments"][0]["edge_id"],
            "destination_place_ref": "huashan",
            **copy.deepcopy(kwargs.get("extra", {})),
        }

    def stage_route_journey(*, route_state, schedule, movement_ref, movement, now):
        route_after = copy.deepcopy(route_state)
        route_after.setdefault("movements", {})[movement_ref] = copy.deepcopy(movement)
        return route_after, copy.deepcopy(schedule)

    monkeypatch.setattr(module, "build_route_journey", build_route_journey)
    monkeypatch.setattr(module, "stage_route_journey", stage_route_journey)


def test_approved_escort_dispatch_musters_exact_roster_to_contract_origin(monkeypatch):
    refs, people, records = _fixture()
    captured = {}
    _patch_physics(monkeypatch, captured)
    harness = _Harness(records, people)

    result = harness._jianghu_institutional_operation_resolution(
        _command(), {"world_seed": "test", "player_id": "pc_wei_tang"}, CampaignTime.parse("SE-0061-09-14T09:15:00")
    )

    assert result["code"] == "jianghu_institutional_escort_muster_dispatched"
    assert captured["movement_kind"] == "player_strategic_travel"
    assert captured["participants"] == refs
    assert captured["leader_ref"] == "pc_wei_tang"
    movement_ref = result["result"]["movement_ref"]
    assert result["writes"][ROUTES]["movements"][movement_ref]["participant_refs"] == refs
    assert result["writes"][OPERATIONS_PATH]["active"]["mission:escort:test"]["phase"] == "mustering"
    assert result["writes"][OPERATIONS_PATH]["active"]["mission:escort:test"]["muster_destination_place_ref"] == "huashan"
    assert result["writes"][INVENTORY]["food_ration_days"] == 88
    assert result["writes"][FACTION]["treasury_cash"] == 95
    assert result["writes"][MARKET]["cash_pool"] == 25
    assert "state/martial-world/deployments.json" not in result["writes"]
    assert result["scene"]["location_id"] == "route.luoyang.huashan"
    assert result["scene"]["present_person_ids"] == refs
    assert result["scene"]["visible_person_ids"] == refs


def test_escort_muster_does_not_move_scene_when_campaign_player_is_not_in_party(monkeypatch):
    refs, people, records = _fixture()
    captured = {}
    _patch_physics(monkeypatch, captured)
    harness = _Harness(records, people)

    result = harness._jianghu_institutional_operation_resolution(
        _command(), {"world_seed": "test", "player_id": "pc.not_in_party"}, CampaignTime.parse("SE-0061-09-14T09:15:00")
    )

    assert result["code"] == "jianghu_institutional_escort_muster_dispatched"
    assert result["scene"] is None
    assert records[SCENE]["location_id"] == "site.house_tang"


def test_escort_dispatch_at_contract_origin_marks_muster_ready_without_travel(monkeypatch):
    refs, people, records = _fixture(origin="huashan")
    captured = {}
    _patch_physics(monkeypatch, captured)
    harness = _Harness(records, people)

    result = harness._jianghu_institutional_operation_resolution(
        _command(), {"world_seed": "test", "player_id": "pc_wei_tang"}, CampaignTime.parse("SE-0061-09-14T09:15:00")
    )

    assert result["code"] == "jianghu_institutional_escort_muster_ready"
    assert captured == {}
    row = result["writes"][OPERATIONS_PATH]["active"]["mission:escort:test"]
    assert row["phase"] == "mustering"
    assert row["mustered_at"] == "0061-09-14T09:15:00"
    assert ROUTES not in result["writes"]
    assert result["scene"] is None


def test_active_mustering_escort_cannot_be_paper_cancelled():
    refs, people, records = _fixture()
    row = records[OPERATIONS_PATH]["active"]["mission:escort:test"]
    row["phase"] = "mustering"
    row["muster_movement_ref"] = "escort_muster:test"
    records[ROUTES]["movements"]["escort_muster:test"] = {"movement_kind": "player_strategic_travel"}
    harness = _Harness(records, people)
    with pytest.raises(CommandRejectedError) as exc:
        harness._jianghu_institutional_operation_resolution(
            _command("cancel"), {"world_seed": "test"}, CampaignTime.parse("SE-0061-09-14T09:15:00")
        )
    assert "jianghu_institutional_in_field_cannot_paper_cancel" in str(exc.value)


def test_arrived_muster_can_delegate_to_normal_prestart_cancellation():
    refs, people, records = _fixture(origin="huashan")
    row = records[OPERATIONS_PATH]["active"]["mission:escort:test"]
    row["phase"] = "mustering"
    row["muster_movement_ref"] = "escort_muster:finished"
    harness = _Harness(records, people)
    result = harness._institutional_escort_operation_specialization(
        _command("cancel"), {"world_seed": "test"}, CampaignTime.parse("SE-0061-09-14T09:15:00")
    )
    assert result is None


def test_non_escort_institutional_actions_delegate_to_existing_owner():
    refs, people, records = _fixture()
    records[OPERATIONS_PATH]["active"]["mission:escort:test"]["mission_kind"] = "reconnaissance"
    harness = _Harness(records, people)
    result = harness._institutional_escort_operation_specialization(
        _command(), {"world_seed": "test"}, CampaignTime.parse("SE-0061-09-14T09:15:00")
    )
    assert result is None
