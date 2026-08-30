import json
import shutil
from pathlib import Path

from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


def _copy_runtime_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")
    (root / "runtime").mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "runtime/contracts", root / "runtime/contracts")

    route_path = root / "state/martial-world/route-operations.json"
    route_state = json.loads(route_path.read_text())
    movements = route_state.get("movements", {})
    if isinstance(movements, dict):
        route_state["movements"] = {
            ref: row
            for ref, row in movements.items()
            if not (
                isinstance(row, dict)
                and "pc_wei_tang" in [
                    str(member_ref)
                    for member_ref in row.get("participant_refs", [])
                    if isinstance(member_ref, str)
                ]
            )
        }
    route_path.write_text(json.dumps(route_state))

    combat_path = root / "state/martial-world/combats.json"
    combat_state = json.loads(combat_path.read_text())
    combats = combat_state.get("combats", {})
    if isinstance(combats, dict):
        combat_state["combats"] = {
            ref: row
            for ref, row in combats.items()
            if not (
                isinstance(row, dict)
                and row.get("status") == "active"
                and "pc_wei_tang" in {
                    str(member_ref)
                    for members in row.get("sides", {}).values()
                    if isinstance(members, list)
                    for member_ref in members
                    if isinstance(member_ref, str)
                }
            )
        }
    combat_path.write_text(json.dumps(combat_state))

    roster_path = root / "state/martial-world/people/house_tang.json"
    roster_state = json.loads(roster_path.read_text())
    people = roster_state.get("people", [])
    if isinstance(people, list):
        for person in people:
            if isinstance(person, dict) and person.get("person_id") in {"pc_wei_tang", "char.zhu"}:
                person["location_ref"] = "site.changan.inn"
    roster_path.write_text(json.dumps(roster_state))

    ledger_path = root / "state/martial-world/interaction-attempts.json"
    ledger_path.write_text(json.dumps({
        "schema": "jianghu-interaction-attempt-ledger-1.0",
        "authority": False,
        "mechanical_consequence_authority": False,
        "total_recorded": 1,
        "attempts": [{
            "attempt_ref": "interaction_attempt_legacy",
            "at": "SE-0061-01-01T00:00:00",
            "surface_digest": "legacy-digest",
            "actor_ref": "pc_wei_tang",
            "target_ref": "char.zhu",
            "action": "request",
            "process_ref": None,
            "player_statement": "Legacy interaction.",
            "posture": None,
            "topic": None,
            "scopes": [],
            "world_response_status": "not_established_by_attempt",
            "scene_session_ref": None,
            "thread_status": "not_applicable",
            "resolved_at": None,
            "response_ref": None,
        }],
    }))
    return root


def _apply_plan(repo: RepositoryStore, plan) -> None:
    for path, content in plan.writes.items():
        repo.replace_image(path, content)


def _attempt_by_ref(ledger, attempt_ref):
    return next(row for row in ledger["attempts"] if row.get("attempt_ref") == attempt_ref)


def test_combat_side_parley_and_reply_are_transaction_valid_and_legacy_compatible(tmp_path):
    root = _copy_runtime_repository(tmp_path)
    repo = RepositoryStore(root)
    planner = RepositoryCommandPlanner(repo)
    meta = repo.read_json("state/meta.json")
    combat_ref = "combat.test.parley-transaction"

    start = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="test.parley-transaction.start",
        actor_id=meta["player_id"],
        command_type="jianghu_combat_resolution",
        expected_revision=meta["revision"],
        submitted_at="2026-08-28T00:00:00Z",
        payload={
            "action": "start",
            "combat_ref": combat_ref,
            "side_a_refs": [meta["player_id"]],
            "side_b_refs": ["char.zhu"],
            "objective": {"kind": "eliminate", "target_refs": ["char.zhu"]},
            "awareness_mode": "mutual",
            "initial_range_band": 2,
        },
        mode="gameplay",
    )
    start_plan = planner.plan(start)
    _apply_plan(repo, start_plan)

    current = repo.read_json("state/meta.json")
    meta_before = repo.read_bytes("state/meta.json")
    ledger_before = repo.read_bytes("state/martial-world/interaction-attempts.json")
    parley = CommandEnvelope(
        campaign_id=current["campaign_id"],
        request_id="test.parley-transaction.ask",
        actor_id=current["player_id"],
        command_type="jianghu_interaction_resolution",
        expected_revision=current["revision"],
        submitted_at="2026-08-28T00:00:01Z",
        payload={
            "action": "ask",
            "target_ref": combat_ref,
            "player_statement": "State your business.",
            "topic": "parley",
        },
        mode="gameplay",
    )

    preview = planner.preview(parley)

    assert preview.status == "ready"
    assert preview.code == "jianghu_interaction_recorded"
    assert repo.read_bytes("state/meta.json") == meta_before
    assert repo.read_bytes("state/martial-world/interaction-attempts.json") == ledger_before

    plan = planner.plan(parley)
    assert plan.result["target_kind"] == "opposing_combat_side"
    staged = json.loads(plan.writes["state/martial-world/interaction-attempts.json"].decode("utf-8"))
    legacy = _attempt_by_ref(staged, "interaction_attempt_legacy")
    assert legacy.get("target_kind") is None
    question = next(
        row for row in staged["attempts"]
        if row.get("target_ref") == combat_ref and row.get("target_kind") == "opposing_combat_side"
    )
    assert question["thread_status"] == "open"
    assert question["world_response_status"] == "not_established_by_attempt"
    assert repo.read_bytes("state/meta.json") == meta_before
    assert repo.read_bytes("state/martial-world/interaction-attempts.json") == ledger_before

    _apply_plan(repo, plan)
    current = repo.read_json("state/meta.json")
    question_ref = question["attempt_ref"]
    world_time_before_reply = current["time"]
    reply = CommandEnvelope(
        campaign_id=current["campaign_id"],
        request_id="test.parley-transaction.reply",
        actor_id=current["player_id"],
        command_type="jianghu_scene_session_resolution",
        expected_revision=current["revision"],
        submitted_at="2026-08-28T00:00:02Z",
        payload={
            "action": "record_speech",
            "session_ref": combat_ref,
            "speaker_ref": combat_ref,
            "statement": "You are not owed an explanation. Turn back.",
            "speech_kind": "nonbinding_response",
            "basis_refs": [combat_ref, question_ref],
            "resolves_question_ref": question_ref,
        },
        mode="gameplay",
    )

    reply_preview = planner.preview(reply)
    assert reply_preview.status == "ready"
    assert reply_preview.code == "jianghu_combat_parley_speech_recorded"
    reply_plan = planner.plan(reply)
    assert reply_plan.result["speaker_ref"] == combat_ref
    assert reply_plan.result["speaker_kind"] == "opposing_combat_side"
    assert reply_plan.result["mechanical_consequence_authority"] is False
    staged_meta = json.loads(reply_plan.writes["state/meta.json"].decode("utf-8"))
    assert staged_meta["time"] == world_time_before_reply

    answered_ledger = json.loads(
        reply_plan.writes["state/martial-world/interaction-attempts.json"].decode("utf-8")
    )
    answered = _attempt_by_ref(answered_ledger, question_ref)
    assert answered["thread_status"] == "answered"
    assert answered["response_ref"] == reply_plan.result["speech_ref"]
    history = json.loads(
        reply_plan.writes["state/martial-world/scene-history-head.json"].decode("utf-8")
    )["recent"][-1]
    assert history["session_ref"] == combat_ref
    assert history["speaker_ref"] == combat_ref
    assert history["resolves_question_ref"] == question_ref
    assert history["mechanical_consequence_authority"] is False


def test_combat_side_explicit_final_speech_does_not_open_response_thread(tmp_path):
    root = _copy_runtime_repository(tmp_path)
    repo = RepositoryStore(root)
    planner = RepositoryCommandPlanner(repo)
    meta = repo.read_json("state/meta.json")
    combat_ref = "combat.test.parley-final-line"

    start = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="test.parley-final.start",
        actor_id=meta["player_id"],
        command_type="jianghu_combat_resolution",
        expected_revision=meta["revision"],
        submitted_at="2026-08-28T00:00:00Z",
        payload={
            "action": "start",
            "combat_ref": combat_ref,
            "side_a_refs": [meta["player_id"]],
            "side_b_refs": ["char.zhu"],
            "objective": {"kind": "eliminate", "target_refs": ["char.zhu"]},
            "awareness_mode": "mutual",
            "initial_range_band": 2,
        },
        mode="gameplay",
    )
    _apply_plan(repo, planner.plan(start))
    current = repo.read_json("state/meta.json")
    final_line = CommandEnvelope(
        campaign_id=current["campaign_id"],
        request_id="test.parley-final.speak",
        actor_id=current["player_id"],
        command_type="jianghu_interaction_resolution",
        expected_revision=current["revision"],
        submitted_at="2026-08-28T00:00:01Z",
        payload={
            "action": "speak",
            "target_ref": combat_ref,
            "player_statement": "Enough. No answer needed.",
            "expects_response": False,
        },
        mode="gameplay",
    )

    plan = planner.plan(final_line)
    staged = json.loads(plan.writes["state/martial-world/interaction-attempts.json"].decode("utf-8"))
    row = next(
        item for item in staged["attempts"]
        if item.get("target_ref") == combat_ref and item.get("player_statement") == "Enough. No answer needed."
    )
    assert row["expects_response"] is False
    assert row["thread_status"] == "not_applicable"
