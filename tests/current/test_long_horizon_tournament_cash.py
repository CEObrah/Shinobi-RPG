import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_long_horizon_module():
    spec = importlib.util.spec_from_file_location("jianghu_run_long_horizon", ROOT / "tools/run_long_horizon.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_tracked_cash_includes_all_active_tournament_travel_reservations():
    mod = _load_long_horizon_module()
    before = mod._tracked_cash_metrics({})
    faction_rel = "state/martial-world/factions/diancang.json"
    faction = json.loads((ROOT / faction_rel).read_text())
    deployments_rel = "state/martial-world/deployments.json"
    deployments = json.loads((ROOT / deployments_rel).read_text())

    competitor = copy.deepcopy(faction)
    competitor["treasury_cash"] -= 12_345
    competitor_deployments = copy.deepcopy(deployments)
    competitor_deployments.setdefault("deployments", {})["probe:tournament-travel"] = {
        "operation_kind": "tournament_travel",
        "entry_fee_reserved_cash": 5_000,
        "host_spend_reserved_cash": 7_345,
    }
    competitor_after = mod._tracked_cash_metrics({
        faction_rel: competitor,
        deployments_rel: competitor_deployments,
    })
    assert competitor_after["total"] == before["total"]
    assert competitor_after["tournament_entry_reservations"] - before["tournament_entry_reservations"] == 5_000
    assert competitor_after["tournament_host_spend_reservations"] - before["tournament_host_spend_reservations"] == 7_345

    delegation = copy.deepcopy(faction)
    delegation["treasury_cash"] -= 15_000
    delegation_deployments = copy.deepcopy(deployments)
    delegation_deployments.setdefault("deployments", {})["probe:tournament-delegation"] = {
        "operation_kind": "tournament_delegation",
        "host_spend_reserved_cash": 9_000,
        "delegate_ticket_reserved_cash": 6_000,
    }
    delegation_after = mod._tracked_cash_metrics({
        faction_rel: delegation,
        deployments_rel: delegation_deployments,
    })
    assert delegation_after["total"] == before["total"]
    assert delegation_after["tournament_host_spend_reservations"] - before["tournament_host_spend_reservations"] == 9_000
    assert delegation_after["tournament_delegate_ticket_reservations"] - before["tournament_delegate_ticket_reservations"] == 6_000


def test_tracked_cash_includes_seized_raid_cash_while_it_is_physically_in_transit():
    mod = _load_long_horizon_module()
    before = mod._tracked_cash_metrics({})
    faction_rel = "state/martial-world/factions/diancang.json"
    faction = json.loads((ROOT / faction_rel).read_text())
    deployments_rel = "state/martial-world/deployments.json"
    deployments = json.loads((ROOT / deployments_rel).read_text())
    routes_rel = "state/martial-world/route-operations.json"
    routes = json.loads((ROOT / routes_rel).read_text())

    # Immediately after the objective is seized, the target treasury has been
    # debited but the silver is still carried by the strategic deployment.
    seized = 8_211
    target_after = copy.deepcopy(faction)
    target_after["treasury_cash"] -= seized
    deployment_after = copy.deepcopy(deployments)
    deployment_after.setdefault("deployments", {})["probe:raid-cash"] = {
        "operation_kind": "faction_raid",
        "seized_cash": seized,
    }
    metric = mod._tracked_cash_metrics({
        faction_rel: target_after,
        deployments_rel: deployment_after,
    })
    assert metric["total"] == before["total"]
    assert metric["raid_cash_in_deployments"] - before["raid_cash_in_deployments"] == seized

    # Starting the physical return moves the same silver from the deployment
    # owner to route cargo. It must remain conserved exactly once.
    deployment_return = copy.deepcopy(deployments)
    route_return = copy.deepcopy(routes)
    route_return.setdefault("movements", {})["probe:raid-return"] = {
        "movement_kind": "raid_return",
        "cash_quantity": seized,
    }
    metric = mod._tracked_cash_metrics({
        faction_rel: target_after,
        deployments_rel: deployment_return,
        routes_rel: route_return,
    })
    assert metric["total"] == before["total"]
    assert metric["raid_cash_in_route"] - before["raid_cash_in_route"] == seized


def test_long_horizon_frontier_budget_stops_before_an_extra_due_event_scan(monkeypatch, tmp_path):
    mod = _load_long_horizon_module()
    checkpoint = tmp_path / "bounded-checkpoint.json"
    checkpoint.write_text(json.dumps({
        "schema": mod._CHECKPOINT_SCHEMA,
        "days": 1,
        "start": "0061-08-14T21:15:00",
        "target": "0061-08-15T21:15:00",
        "schedule": {"schema": "probe", "settled_through": "0061-08-14T21:15:00"},
        "overlay": {},
        "frontiers": 0,
        "write_operations": 0,
        "maximum_writes_per_frontier": 0,
        "event_kinds": {},
        "review_kinds": {},
        "handoffs": {},
        "errors": [],
        "elapsed_seconds": 0.0,
        "before_people": 0,
        "before_parentage": 0,
        "before_tracked_cash": {"total": 0},
        "before_grade_counts": {},
        "before_bytes": 0,
    }))

    calls = {"due_events": 0}

    def fake_due_events(schedule, *, after, through):
        calls["due_events"] += 1
        return [{
            "event_id": "probe:1",
            "kind": "probe",
            "due_at": "0061-08-15T00:00:00",
            "schedule_class": "calendar",
        }]

    def fake_settle(*, read_json, schedule, events, at):
        return {
            "writes": {},
            "schedule_after": {**schedule, "settled_through": at.isoformat()},
            "reviews": [],
            "handoffs": [],
        }

    monkeypatch.setattr(mod, "due_events", fake_due_events)
    monkeypatch.setattr(mod, "settle_martial_world_frontier", fake_settle)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_long_horizon.py",
            "--days", "1",
            "--checkpoint", str(checkpoint),
            "--frontier-budget", "1",
            "--checkpoint-every", "1",
        ],
    )

    assert mod.main() == 2
    assert calls["due_events"] == 1


def test_long_horizon_checkpoint_has_single_writer_guard(tmp_path):
    mod = _load_long_horizon_module()
    checkpoint = tmp_path / "single-writer.json"
    mod._acquire_checkpoint_lock(checkpoint)
    try:
        mod._acquire_checkpoint_lock(checkpoint)
    except SystemExit as exc:
        assert "checkpoint already in use" in str(exc)
    else:
        raise AssertionError("second verifier acquired the same checkpoint concurrently")
