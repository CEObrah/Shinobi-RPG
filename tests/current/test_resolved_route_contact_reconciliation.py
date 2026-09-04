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
