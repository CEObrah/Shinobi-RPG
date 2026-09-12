import copy
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from shinobi_runtime.commands.campaign_planner import CampaignCommandPlanner
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.martial_world.route_contact_reconciliation import (
    normalize_resolved_route_contact_context,
    reconcile_resolved_player_route_contact_records,
)
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]
ROUTE_PATH = "state/martial-world/route-operations.json"
COMBAT_PATH = "state/martial-world/combats.json"
SCHEDULE_PATH = "state/martial-world/scheduler.json"


def _copy_live_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")
    (root / "runtime").mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "runtime/contracts", root / "runtime/contracts")
    return root


def _stale_player_contact(repo: RepositoryStore):
    meta = repo.read_json("state/meta.json")
    player_ref = str(meta["player_id"])
    routes = repo.read_json(ROUTE_PATH)
    combats = repo.read_json(COMBAT_PATH)
    rows = routes.get("movements", {})
    combat_rows = combats.get("combats", {})
    matches = []
    for movement_ref, movement in rows.items():
        if not isinstance(movement, dict):
            continue
        if movement.get("status") != "contact_pending" or player_ref not in movement.get("participant_refs", []):
            continue
        combat_ref = movement.get("combat_ref")
        combat = combat_rows.get(combat_ref)
        if isinstance(combat, dict) and combat.get("status") == "resolved":
            matches.append((movement_ref, movement, str(combat_ref), combat))
    if not matches:
        pytest.skip("canonical live baseline no longer contains a stale resolved player route contact")
    assert len(matches) == 1, "live regression fixture must contain at most one stale resolved player route contact"
    return player_ref, matches[0]


def _segment_wakes(schedule, movement_ref):
    rows = schedule.get("one_off", {}) if isinstance(schedule, dict) else {}
    return {
        event_id: copy.deepcopy(row)
        for event_id, row in rows.items()
        if isinstance(row, dict)
        and row.get("kind") == "route_activity_cycle"
        and row.get("exact_segment_due") is True
        and row.get("movement_ref") == movement_ref
    }


def test_live_stale_resolved_contact_reconciles_without_advancing_other_routes(tmp_path):
    repo = RepositoryStore(_copy_live_repository(tmp_path))
    meta = repo.read_json("state/meta.json")
    player_ref, (movement_ref, movement_before, combat_ref, _combat) = _stale_player_contact(repo)
    route_before = repo.read_json(ROUTE_PATH)
    schedule_before = repo.read_json(SCHEDULE_PATH)
    other_before = {
        ref: copy.deepcopy(row)
        for ref, row in route_before.get("movements", {}).items()
        if ref != movement_ref
    }
    old_wakes = _segment_wakes(schedule_before, movement_ref)

    at = datetime.fromisoformat(str(meta["time"]).removeprefix("SE-"))
    writes = reconcile_resolved_player_route_contact_records(
        read_json=repo.read_json,
        at=at,
        player_ref=player_ref,
        combat_ref=combat_ref,
    )

    assert ROUTE_PATH in writes
    assert SCHEDULE_PATH in writes
    route_after = writes[ROUTE_PATH]
    movement_after = route_after.get("movements", {}).get(movement_ref)
    assert not (
        isinstance(movement_after, dict)
        and movement_after.get("status") == "contact_pending"
        and movement_after.get("combat_ref") == combat_ref
    )
    for ref, row in other_before.items():
        assert route_after.get("movements", {}).get(ref) == row

    new_wakes = _segment_wakes(writes[SCHEDULE_PATH], movement_ref)
    if isinstance(movement_after, dict) and movement_after.get("status") in {"active", "resting", "waiting_for_lodging", "awaiting_return_logistics"}:
        assert new_wakes
        if old_wakes:
            assert new_wakes != old_wakes
    assert movement_before.get("elapsed_seconds") == route_before["movements"][movement_ref].get("elapsed_seconds")


def test_production_advance_time_stages_legacy_contact_repair_atomically(tmp_path):
    repo = RepositoryStore(_copy_live_repository(tmp_path))
    meta = repo.read_json("state/meta.json")
    player_ref, (movement_ref, _movement, combat_ref, _combat) = _stale_player_contact(repo)
    now = datetime.fromisoformat(str(meta["time"]).removeprefix("SE-"))
    target = now + timedelta(seconds=1)
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="test.resolved-route-contact.advance",
        actor_id=player_ref,
        command_type="advance_time",
        expected_revision=int(meta["revision"]),
        submitted_at="2026-09-04T04:31:00Z",
        payload={"target_time": "SE-" + target.isoformat()},
        mode="gameplay",
    )

    planner = CampaignCommandPlanner(repo)
    preview = planner.preview(command)
    assert preview.status == "ready"
    plan = planner.plan(command)
    route_raw = plan.writes.get(ROUTE_PATH)
    assert route_raw is not None
    route_after = json.loads(route_raw.decode("utf-8"))
    movement_after = route_after.get("movements", {}).get(movement_ref)
    assert not (
        isinstance(movement_after, dict)
        and movement_after.get("status") == "contact_pending"
        and movement_after.get("combat_ref") == combat_ref
    )


def test_play_context_normalization_retires_stale_choice_without_enemy_identity(tmp_path):
    repo = RepositoryStore(_copy_live_repository(tmp_path))
    player_ref, (movement_ref, _movement, _combat_ref, combat) = _stale_player_contact(repo)
    opposing_refs = {
        str(ref)
        for side_refs in combat.get("sides", {}).values()
        if isinstance(side_refs, list) and player_ref not in side_refs
        for ref in side_refs
        if isinstance(ref, str)
    }
    context = {
        "campaign": {"player_id": player_ref},
        "player": {"person_id": player_ref},
        "scene": {
            "movement_context": {"movement_ref": movement_ref},
            "activity_handoff": {
                "event_id": "contact:stale",
                "kind": "hostile_contact",
                "requires_player_decision": True,
                "interrupts_continuation": True,
            },
        },
        "gm_scene_context": {
            "scene_direction": {
                "protected_player_decision_pending": True,
                "narrative_stage_hint": "decision_handoff",
                "close_risks": ["protected_player_decision"],
            },
            "wei_observations_and_known_scene_evidence": {},
        },
    }

    normalized = normalize_resolved_route_contact_context(context, repo.read_json)
    handoff = normalized["scene"]["activity_handoff"]
    assert handoff["requires_player_decision"] is False
    assert handoff["handoff_status"] == "superseded_by_resolved_combat"
    assert normalized["scene"]["resolved_route_contact"]["combat_status"] == "resolved"
    assert normalized["gm_scene_context"]["scene_direction"]["protected_player_decision_pending"] is False

    serialized = json.dumps(normalized, sort_keys=True)
    for ref in opposing_refs:
        assert ref not in serialized


def test_negotiated_passage_reconciliation_conserves_cash_on_physical_attacker_return():
    """Settlement silver must travel with the attackers; passage must not teleport either side."""
    meta = json.loads((ROOT / "state/meta.json").read_text())
    base_route = json.loads((ROOT / ROUTE_PATH).read_text())
    base_combats = json.loads((ROOT / COMBAT_PATH).read_text())
    fixture = json.loads((ROOT / "tests/fixtures/black_lance_fresh_group_certification.json").read_text())
    player_ref = str(fixture["player_ref"])
    combat_fixture = fixture["combat"]
    player_side = next(
        list(members) for members in combat_fixture["sides"].values()
        if isinstance(members, list) and player_ref in members
    )
    attacker_side = next(
        list(members) for members in combat_fixture["sides"].values()
        if isinstance(members, list) and player_ref not in members
    )

    movement_ref = "test.negotiated-passage.movement"
    contact_ref = "test.negotiated-passage.contact"
    combat_ref = "test.negotiated-passage.combat"
    route_state = copy.deepcopy(base_route)
    movements = route_state.setdefault("movements", {})
    contacts = route_state.setdefault("contacts", {})

    occupied = set(player_side) | set(attacker_side)
    for ref, row in list(movements.items()):
        if not isinstance(row, dict):
            continue
        carried = set(row.get("participant_refs", [])) | set(row.get("captive_refs", [])) | set(row.get("rescued_refs", []))
        if occupied.intersection(carried):
            movements.pop(ref, None)
    movements[movement_ref] = {
        "movement_kind": "player_strategic_travel",
        "purpose_ref": "test.negotiated-passage",
        "route_ref": "route.changan.huashan",
        "route_refs": ["route.changan.huashan"],
        "route_index": 0,
        "journey_nodes": ["changan", "huashan"],
        "segment_required_seconds": [387180],
        "segment_provisioning_seconds": [387180],
        "provisioning_seconds": 387180,
        "segment_edge_start_milli": [0],
        "segment_edge_end_milli": [1000],
        "origin_place_ref": "changan",
        "destination_place_ref": "huashan",
        "segment_origin_place_ref": "changan",
        "segment_destination_place_ref": "huashan",
        "participant_refs": list(player_side),
        "leader_ref": player_ref,
        "beneficiary_ref": "house_tang",
        "mode": "foot",
        "started_at": str(meta["time"]).removeprefix("SE-"),
        "last_progress_at": str(meta["time"]).removeprefix("SE-"),
        "elapsed_seconds": 21213,
        "required_seconds": 387180,
        "edge_start_milli": 0,
        "edge_end_milli": 1000,
        "status": "contact_pending",
        "protected_person_refs": [],
        "item_ref": "",
        "quantity": 0,
        "contact_ref": contact_ref,
        "combat_ref": combat_ref,
        "contact_attacker_faction_ref": "black_lance_company",
        "contact_attacker_refs": list(attacker_side),
        "contact_intent": "hostile_interception",
        "contact_settlement_cash": 4000,
        "contact_settlement_payer_ref": player_ref,
    }
    contacts[contact_ref] = {
        "status": "active",
        "movement_ref": movement_ref,
        "route_ref": "route.changan.huashan",
        "combat_ref": combat_ref,
        "attacker_faction_ref": "black_lance_company",
        "attacker_refs": list(attacker_side),
        "escort_refs": list(player_side),
        "attacker_intent": "hostile_interception",
        "motive_kind": "opportunistic_predation",
        "settlement_terms": {"minimum_cash": 4000, "opening_demand_cash": 5000},
    }

    combat_state = copy.deepcopy(base_combats)
    combat_rows = combat_state.setdefault("combats", {})
    for ref, row in list(combat_rows.items()):
        if not isinstance(row, dict):
            continue
        members = {
            str(person_ref)
            for side in row.get("sides", {}).values()
            if isinstance(side, list)
            for person_ref in side
            if isinstance(person_ref, str)
        }
        if occupied.intersection(members):
            combat_rows.pop(ref, None)
    combat_rows[combat_ref] = {
        "combat_id": combat_ref,
        "status": "resolved",
        "elapsed_ms": 0,
        "zone_ref": "route.changan.huashan",
        "objective": {"kind": "preserve_route_mission", "movement_ref": movement_ref},
        "sides": {"side_a": list(player_side), "side_b": list(attacker_side)},
        "combatants": {ref: {"status_families": []} for ref in [*player_side, *attacker_side]},
        "winner_side": "side_a",
        "resolution_kind": "negotiated_passage",
        "settlement_cash": 4000,
    }

    overrides = {ROUTE_PATH: route_state, COMBAT_PATH: combat_state}

    def read_json(path: str):
        if path in overrides:
            return copy.deepcopy(overrides[path])
        file_path = ROOT / path
        if not file_path.exists():
            raise FileNotFoundError(path)
        return json.loads(file_path.read_text())

    faction_before = read_json("state/martial-world/factions/black_lance_company.json")
    at = datetime.fromisoformat(str(meta["time"]).removeprefix("SE-"))
    writes = reconcile_resolved_player_route_contact_records(
        read_json=read_json, at=at, player_ref=player_ref, combat_ref=combat_ref,
    )

    route_after = writes[ROUTE_PATH]
    resumed = route_after["movements"][movement_ref]
    assert resumed["status"] == "active"
    assert resumed["elapsed_seconds"] == 21213
    assert resumed["edge_start_milli"] == 0
    assert resumed["edge_end_milli"] == 1000
    assert "contact_ref" not in resumed and "combat_ref" not in resumed
    assert contact_ref not in route_after.get("contacts", {})
    assert combat_ref not in writes[COMBAT_PATH]["combats"]

    returns = [
        row for row in route_after["movements"].values()
        if isinstance(row, dict)
        and row.get("movement_kind") == "raid_return"
        and row.get("beneficiary_ref") == "black_lance_company"
        and row.get("cash_quantity") == 4000
    ]
    assert len(returns) == 1
    assert set(returns[0]["participant_refs"]) == set(attacker_side)
    faction_after = writes.get("state/martial-world/factions/black_lance_company.json", faction_before)
    assert faction_after.get("treasury_cash") == faction_before.get("treasury_cash")


def test_postcombat_attacker_presence_splits_return_stragglers_dead_and_future_reinforcement():
    """Closing route combat must never collapse every attacker back to stale headquarters."""
    meta = json.loads((ROOT / "state/meta.json").read_text())
    base_route = json.loads((ROOT / ROUTE_PATH).read_text())
    base_combats = json.loads((ROOT / COMBAT_PATH).read_text())
    fixture = json.loads((ROOT / "tests/fixtures/black_lance_fresh_group_certification.json").read_text())
    player_ref = str(fixture["player_ref"])
    combat_fixture = fixture["combat"]
    player_side = next(
        list(members) for members in combat_fixture["sides"].values()
        if isinstance(members, list) and player_ref in members
    )
    attacker_side = next(
        list(members) for members in combat_fixture["sides"].values()
        if isinstance(members, list) and player_ref not in members
    )
    assert len(attacker_side) >= 5
    escaped_ref, down_ref, dead_ref, future_ref = attacker_side[:4]

    movement_ref = "test.postcombat-presence.movement"
    contact_ref = "test.postcombat-presence.contact"
    combat_ref = "test.postcombat-presence.combat"
    route_state = copy.deepcopy(base_route)
    movements = route_state.setdefault("movements", {})
    contacts = route_state.setdefault("contacts", {})
    occupied = set(player_side) | set(attacker_side)
    for ref, row in list(movements.items()):
        if not isinstance(row, dict):
            continue
        carried = (
            set(row.get("participant_refs", []))
            | set(row.get("protected_person_refs", []))
            | set(row.get("captive_refs", []))
            | set(row.get("rescued_refs", []))
        )
        if occupied.intersection(carried):
            movements.pop(ref, None)
    movements[movement_ref] = {
        "movement_kind": "player_strategic_travel",
        "purpose_ref": "test.postcombat-presence",
        "route_ref": "route.changan.huashan",
        "route_refs": ["route.changan.huashan"],
        "route_index": 0,
        "journey_nodes": ["changan", "huashan"],
        "segment_required_seconds": [387180],
        "segment_provisioning_seconds": [387180],
        "provisioning_seconds": 387180,
        "segment_edge_start_milli": [0],
        "segment_edge_end_milli": [1000],
        "origin_place_ref": "changan",
        "destination_place_ref": "huashan",
        "segment_origin_place_ref": "changan",
        "segment_destination_place_ref": "huashan",
        "participant_refs": list(player_side),
        "leader_ref": player_ref,
        "beneficiary_ref": "house_tang",
        "mode": "foot",
        "started_at": str(meta["time"]).removeprefix("SE-"),
        "last_progress_at": str(meta["time"]).removeprefix("SE-"),
        "elapsed_seconds": 21213,
        "required_seconds": 387180,
        "edge_start_milli": 0,
        "edge_end_milli": 1000,
        "status": "contact_pending",
        "protected_person_refs": [],
        "item_ref": "",
        "quantity": 0,
        "contact_ref": contact_ref,
        "combat_ref": combat_ref,
        "contact_attacker_faction_ref": "black_lance_company",
        "contact_attacker_refs": list(attacker_side),
        "contact_intent": "hostile_interception",
    }
    contacts[contact_ref] = {
        "status": "active",
        "movement_ref": movement_ref,
        "route_ref": "route.changan.huashan",
        "combat_ref": combat_ref,
        "attacker_faction_ref": "black_lance_company",
        "attacker_refs": list(attacker_side),
        "escort_refs": list(player_side),
        "attacker_intent": "hostile_interception",
        "motive_kind": "opportunistic_predation",
    }

    combat_state = copy.deepcopy(base_combats)
    combat_rows = combat_state.setdefault("combats", {})
    for ref, row in list(combat_rows.items()):
        if not isinstance(row, dict):
            continue
        members = {
            str(person_ref)
            for side in row.get("sides", {}).values()
            if isinstance(side, list)
            for person_ref in side
            if isinstance(person_ref, str)
        }
        if occupied.intersection(members):
            combat_rows.pop(ref, None)
    combatants = {ref: {"status_families": []} for ref in [*player_side, *attacker_side]}
    combatants[escaped_ref]["status_families"] = ["escaped"]
    combatants[down_ref]["status_families"] = ["incapacitated"]
    combatants[dead_ref]["status_families"] = ["dead"]
    combatants[future_ref].update(status_families=["reinforcing"], reinforcement_at_ms=60_000)
    combat_rows[combat_ref] = {
        "combat_id": combat_ref,
        "status": "resolved",
        "elapsed_ms": 1000,
        "zone_ref": "route.changan.huashan",
        "objective": {"kind": "preserve_route_mission", "movement_ref": movement_ref},
        "sides": {"side_a": list(player_side), "side_b": list(attacker_side)},
        "combatants": combatants,
        "winner_side": "side_a",
    }

    overrides = {ROUTE_PATH: route_state, COMBAT_PATH: combat_state}

    def read_json(path: str):
        if path in overrides:
            return copy.deepcopy(overrides[path])
        file_path = ROOT / path
        if not file_path.exists():
            raise FileNotFoundError(path)
        return json.loads(file_path.read_text())

    attacker_roster_path = "state/martial-world/people/black_lance_company.json"
    attacker_roster_before = read_json(attacker_roster_path)
    people_before = {
        str(row.get("person_id")): copy.deepcopy(row)
        for row in attacker_roster_before.get("people", [])
        if isinstance(row, dict) and isinstance(row.get("person_id"), str)
    }
    assert dead_ref in people_before and future_ref in people_before

    at = datetime.fromisoformat(str(meta["time"]).removeprefix("SE-"))
    writes = reconcile_resolved_player_route_contact_records(
        read_json=read_json, at=at, player_ref=player_ref, combat_ref=combat_ref,
    )
    route_after = writes[ROUTE_PATH]
    returns = [
        row for row in route_after.get("movements", {}).values()
        if isinstance(row, dict)
        and row.get("movement_kind") == "raid_return"
        and row.get("beneficiary_ref") == "black_lance_company"
    ]
    immediate = [row for row in returns if row.get("status") != "awaiting_return_logistics"]
    recovery = [row for row in returns if row.get("status") == "awaiting_return_logistics" and row.get("postcombat_recovery")]
    assert len(immediate) == 1
    assert escaped_ref not in immediate[0].get("participant_refs", [])
    assert down_ref not in immediate[0].get("participant_refs", [])
    assert dead_ref not in immediate[0].get("participant_refs", [])
    assert future_ref not in immediate[0].get("participant_refs", [])

    assert len(recovery) == 1
    assert set(recovery[0]["participant_refs"]) == {escaped_ref, down_ref}
    assert recovery[0].get("separated_person_refs") == [escaped_ref]
    assert dead_ref not in recovery[0]["participant_refs"]
    assert future_ref not in recovery[0]["participant_refs"]

    attacker_roster_after = writes[attacker_roster_path]
    people_after = {
        str(row.get("person_id")): row
        for row in attacker_roster_after.get("people", [])
        if isinstance(row, dict) and isinstance(row.get("person_id"), str)
    }
    assert people_after[dead_ref]["location_ref"] == "route.changan.huashan"
    assert people_after[future_ref].get("location_ref") == people_before[future_ref].get("location_ref")
