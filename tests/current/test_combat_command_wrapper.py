import json
import shutil
from pathlib import Path

from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.martial_world.social_causality import add_vow

ROOT = Path(__file__).resolve().parents[2]


def _copy_runtime_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")
    (root / "runtime").mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "runtime/contracts", root / "runtime/contracts")
    # These combat regressions need a coherent local fight fixture.  The
    # supplied live campaign may legitimately have Wei committed to an escort
    # route, which must not make a standalone combat test depend on save timing.
    route_path = root / "state/martial-world/route-operations.json"
    route_state = json.loads(route_path.read_text())
    movements = route_state.get("movements", {})
    if isinstance(movements, dict):
        route_state["movements"] = {
            ref: row for ref, row in movements.items()
            if not (
                isinstance(row, dict)
                and "pc_wei_tang" in [
                    str(member_ref) for member_ref in row.get("participant_refs", [])
                    if isinstance(member_ref, str)
                ]
            )
        }
    route_path.write_text(json.dumps(route_state))
    # The copied live save may also contain a legitimate active exact combat.
    # Strip only combats physically owning Wei so these standalone wrapper
    # regressions can build their own synthetic fight without weakening the
    # production invariant that one person cannot occupy two active combats.
    combat_path = root / "state/martial-world/combats.json"
    combat_state = json.loads(combat_path.read_text())
    combats = combat_state.get("combats", {})
    if isinstance(combats, dict):
        combat_state["combats"] = {
            ref: row for ref, row in combats.items()
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
    return root


def _apply_plan(repo: RepositoryStore, plan) -> None:
    for path, content in plan.writes.items():
        repo.replace_image(path, content)


def test_live_combat_start_and_bare_attack_exchange_preview_and_plan_end_to_end(tmp_path):
    root = _copy_runtime_repository(tmp_path)
    repo = RepositoryStore(root)
    planner = RepositoryCommandPlanner(repo)
    meta = repo.read_json("state/meta.json")
    combat_ref = "combat.test.live-wrapper"

    start = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="test.live-wrapper.start",
        actor_id=meta["player_id"],
        command_type="jianghu_combat_resolution",
        expected_revision=meta["revision"],
        submitted_at="2026-08-27T00:00:00Z",
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
    assert planner.preview(start).status == "ready"
    start_plan = planner.plan(start)
    _apply_plan(repo, start_plan)

    current = repo.read_json("state/meta.json")
    exchange = CommandEnvelope(
        campaign_id=current["campaign_id"],
        request_id="test.live-wrapper.exchange",
        actor_id=current["player_id"],
        command_type="jianghu_combat_resolution",
        expected_revision=current["revision"],
        submitted_at="2026-08-27T00:00:01Z",
        payload={"action": "exchange", "combat_ref": combat_ref},
        mode="gameplay",
    )
    preview = planner.preview(exchange)
    assert preview.status == "ready"
    assert preview.code == "jianghu_combat_exchange_resolved"

    plan = planner.plan(exchange)
    assert plan.result["combat_ref"] == combat_ref
    assert plan.result["events"]
    assert any(row.get("actor_ref") == current["player_id"] for row in plan.result["events"])
    assert plan.result["world_time"] > current["time"]


def test_high_level_attack_scope_runs_multiple_doctrine_exchanges_in_one_command(tmp_path):
    root = _copy_runtime_repository(tmp_path)
    repo = RepositoryStore(root)
    planner = RepositoryCommandPlanner(repo)
    meta = repo.read_json("state/meta.json")
    combat_ref = "combat.test.scoped-attack"

    start = CommandEnvelope(
        campaign_id=meta["campaign_id"], request_id="test.scoped.start",
        actor_id=meta["player_id"], command_type="jianghu_combat_resolution",
        expected_revision=meta["revision"], submitted_at="2026-08-27T00:01:00Z",
        payload={
            "action": "start", "combat_ref": combat_ref,
            "side_a_refs": [meta["player_id"]], "side_b_refs": ["char.zhu"],
            "objective": {"kind": "eliminate", "target_refs": ["char.zhu"]},
            "awareness_mode": "mutual", "initial_range_band": 2,
        }, mode="gameplay",
    )
    start_plan = planner.plan(start)
    _apply_plan(repo, start_plan)

    current = repo.read_json("state/meta.json")
    attack = CommandEnvelope(
        campaign_id=current["campaign_id"], request_id="test.scoped.attack",
        actor_id=current["player_id"], command_type="jianghu_combat_resolution",
        expected_revision=current["revision"], submitted_at="2026-08-27T00:01:01Z",
        payload={"action": "exchange", "combat_ref": combat_ref, "exchange_count": 3},
        mode="gameplay",
    )
    preview = planner.preview(attack)
    assert preview.status == "ready"
    plan = planner.plan(attack)
    assert plan.result["exchanges_resolved"] == 3
    assert plan.result["scope_stop_reason"] == "scope_complete"
    assert plan.result["continuation_required"] is False
    assert len([row for row in plan.result["events"] if row.get("actor_ref") == current["player_id"]]) >= 3
    assert plan.result["world_time"] > current["time"]


def test_bare_attack_respects_nonlethal_vow_but_explicit_lethal_intent_can_break_it(tmp_path):
    def prepared_repo(root_name: str):
        root = _copy_runtime_repository(tmp_path / root_name)
        repo = RepositoryStore(root)
        meta = repo.read_json("state/meta.json")
        social_path = "state/martial-world/social.json"
        social = repo.read_json(social_path)
        social = add_vow(
            social, person_ref=meta["player_id"], kind="nonlethal", strength=100,
            declared_at="0061-01-01T00:00:00",
        )["state_after"]
        repo.replace_image(social_path, (json.dumps(social, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
        planner = RepositoryCommandPlanner(repo)
        combat_ref = f"combat.test.vow-{root_name}"
        start = CommandEnvelope(
            campaign_id=meta["campaign_id"], request_id=f"test.vow.{root_name}.start",
            actor_id=meta["player_id"], command_type="jianghu_combat_resolution",
            expected_revision=meta["revision"], submitted_at="2026-08-27T00:02:00Z",
            payload={
                "action":"start", "combat_ref":combat_ref,
                "side_a_refs":[meta["player_id"]], "side_b_refs":["char.zhu"],
                "objective":{"kind":"eliminate", "target_refs":["char.zhu"]},
                "awareness_mode":"mutual", "initial_range_band":2,
            }, mode="gameplay",
        )
        start_plan=planner.plan(start); _apply_plan(repo,start_plan)
        return repo, RepositoryCommandPlanner(repo), combat_ref

    repo, planner, combat_ref = prepared_repo("auto")
    current=repo.read_json("state/meta.json")
    bare=CommandEnvelope(
        campaign_id=current["campaign_id"], request_id="test.vow.auto.exchange",
        actor_id=current["player_id"], command_type="jianghu_combat_resolution",
        expected_revision=current["revision"], submitted_at="2026-08-27T00:02:01Z",
        payload={"action":"exchange", "combat_ref":combat_ref}, mode="gameplay",
    )
    bare_plan=planner.plan(bare)
    player_events=[row for row in bare_plan.result["events"] if row.get("actor_ref")==current["player_id"]]
    assert player_events
    assert all(row.get("targeting_intent")=="disable" for row in player_events)
    assert not bare_plan.result["broken_vow_refs"]

    repo2, planner2, combat_ref2 = prepared_repo("explicit")
    current2=repo2.read_json("state/meta.json")
    lethal=CommandEnvelope(
        campaign_id=current2["campaign_id"], request_id="test.vow.explicit.exchange",
        actor_id=current2["player_id"], command_type="jianghu_combat_resolution",
        expected_revision=current2["revision"], submitted_at="2026-08-27T00:02:01Z",
        payload={"action":"exchange", "combat_ref":combat_ref2, "targeting_intent":"lethal"}, mode="gameplay",
    )
    lethal_plan=planner2.plan(lethal)
    lethal_events=[row for row in lethal_plan.result["events"] if row.get("actor_ref")==current2["player_id"]]
    assert lethal_events
    assert all(row.get("targeting_intent")=="lethal" for row in lethal_events)
    assert lethal_plan.result["broken_vow_refs"]


def test_duration_scoped_attack_advances_until_requested_combat_time_budget(tmp_path):
    root=_copy_runtime_repository(tmp_path / "duration")
    repo=RepositoryStore(root); planner=RepositoryCommandPlanner(repo); meta=repo.read_json("state/meta.json")
    combat_ref="combat.test.duration-scope"
    start=CommandEnvelope(
        campaign_id=meta["campaign_id"],request_id="test.duration.start",actor_id=meta["player_id"],
        command_type="jianghu_combat_resolution",expected_revision=meta["revision"],submitted_at="2026-08-27T00:03:00Z",
        payload={"action":"start","combat_ref":combat_ref,"side_a_refs":[meta["player_id"]],"side_b_refs":["char.zhu"],
                 "objective":{"kind":"eliminate","target_refs":["char.zhu"]},"awareness_mode":"mutual","initial_range_band":2},
        mode="gameplay",
    )
    _apply_plan(repo,planner.plan(start)); current=repo.read_json("state/meta.json")
    combats_before=repo.read_json("state/martial-world/combats.json")
    elapsed_before=int(combats_before["combats"][combat_ref].get("elapsed_ms",0))
    cmd=CommandEnvelope(
        campaign_id=current["campaign_id"],request_id="test.duration.attack",actor_id=current["player_id"],
        command_type="jianghu_combat_resolution",expected_revision=current["revision"],submitted_at="2026-08-27T00:03:01Z",
        payload={"action":"exchange","combat_ref":combat_ref,"duration_seconds":1},mode="gameplay",
    )
    plan=planner.plan(cmd)
    combat_after=json.loads(plan.writes["state/martial-world/combats.json"].decode("utf-8"))["combats"][combat_ref]
    assert int(combat_after["elapsed_ms"])-elapsed_before >= 1000
    assert plan.result["exchanges_resolved"] >= 1
    assert plan.result["scope_stop_reason"] in {"scope_complete","combat_resolved"}
    assert plan.result["continuation_required"] is False


def test_until_resolution_scope_finishes_or_returns_explicit_continuation_frontier(tmp_path):
    root=_copy_runtime_repository(tmp_path / "until")
    repo=RepositoryStore(root); planner=RepositoryCommandPlanner(repo); meta=repo.read_json("state/meta.json")
    combat_ref="combat.test.until-resolution"
    start=CommandEnvelope(
        campaign_id=meta["campaign_id"],request_id="test.until.start",actor_id=meta["player_id"],
        command_type="jianghu_combat_resolution",expected_revision=meta["revision"],submitted_at="2026-08-27T00:04:00Z",
        payload={"action":"start","combat_ref":combat_ref,"side_a_refs":[meta["player_id"]],"side_b_refs":["char.zhu"],
                 "objective":{"kind":"eliminate","target_refs":["char.zhu"]},"awareness_mode":"mutual","initial_range_band":2},
        mode="gameplay",
    )
    _apply_plan(repo,planner.plan(start)); current=repo.read_json("state/meta.json")
    cmd=CommandEnvelope(
        campaign_id=current["campaign_id"],request_id="test.until.attack",actor_id=current["player_id"],
        command_type="jianghu_combat_resolution",expected_revision=current["revision"],submitted_at="2026-08-27T00:04:01Z",
        payload={"action":"exchange","combat_ref":combat_ref,"until_resolution":True},mode="gameplay",
    )
    plan=planner.plan(cmd)
    assert plan.result["exchanges_resolved"] >= 1
    if plan.result["combat_status"] == "resolved":
        assert plan.result["scope_stop_reason"] == "combat_resolved"
        assert plan.result["continuation_required"] is False
    else:
        assert plan.result["scope_stop_reason"] == "execution_frontier"
        assert plan.result["continuation_required"] is True


def test_bounded_combat_does_not_convert_internal_type_errors_into_partial_success(monkeypatch):
    import shinobi_runtime.commands.jianghu_extended as extended

    combat = {
        "status":"active", "elapsed_ms":0,
        "sides":{"side_a":["wei"], "side_b":["enemy"]},
        "combatants":{"wei":{"status_families":[]}, "enemy":{"status_families":[]}},
    }
    people = {
        "wei":{"person_id":"wei", "faction_ref":"a", "health":{"status":"healthy", "consciousness":100}},
        "enemy":{"person_id":"enemy", "faction_ref":"b", "health":{"status":"healthy", "consciousness":100}},
    }
    calls={"count":0}

    monkeypatch.setattr(extended, "default_target_for", lambda **_kw: "enemy")
    monkeypatch.setattr(extended, "default_action_for", lambda **_kw: ("unarmed_strike", "body_unarmed"))

    def fake_exchange(**kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise TypeError("internal invariant bug")
        after=dict(kwargs["combat"]); after["elapsed_ms"]=int(after.get("elapsed_ms",0))+250
        return {
            "combat_after":after,
            "people_after":kwargs["people"],
            "equipment_ledger_after":kwargs["equipment_ledger"],
            "events":[{"actor_ref":"wei", "intended_ref":"enemy", "result":"miss"}],
        }

    monkeypatch.setattr(extended, "resolve_exchange", fake_exchange)
    import pytest
    with pytest.raises(TypeError, match="internal invariant bug"):
        extended._resolve_player_combat_span(
            combat=combat, people=people,
            equipment_ledger={"schema":"jianghu-equipment-ledger-1.0", "policy_assignments":{}, "person_loadouts":{}},
            doctrines={}, player_ref="wei", social_state={}, player_retinue_context=None,
            raw_target_ref="auto", raw_action_kind="auto", raw_weapon_ref="auto", hit_zone="auto",
            target_structure_ref=None, targeting_intent=None, explicit_poison_ref=None, poison_auto=False,
            explicit_qi_allocation_milli={}, qi_auto=False, exchange_count=2, duration_seconds=None,
            until_resolution=False,
        )


def test_bounded_combat_does_not_convert_internal_value_errors_into_partial_success(monkeypatch):
    import shinobi_runtime.commands.jianghu_extended as extended
    combat={"status":"active","elapsed_ms":0,"sides":{"side_a":["wei"],"side_b":["enemy"]},"combatants":{"wei":{"status_families":[]},"enemy":{"status_families":[]}}}
    people={"wei":{"person_id":"wei","faction_ref":"a","health":{"status":"healthy","consciousness":100}},"enemy":{"person_id":"enemy","faction_ref":"b","health":{"status":"healthy","consciousness":100}}}
    calls={"count":0}
    monkeypatch.setattr(extended,"default_target_for",lambda **_kw:"enemy")
    monkeypatch.setattr(extended,"default_action_for",lambda **_kw:("unarmed_strike","body_unarmed"))
    def fake_exchange(**kwargs):
        calls["count"]+=1
        if calls["count"]==2:
            raise ValueError("corrupt combat invariant")
        after=dict(kwargs["combat"]); after["elapsed_ms"]=int(after.get("elapsed_ms",0))+250
        return {"combat_after":after,"people_after":kwargs["people"],"equipment_ledger_after":kwargs["equipment_ledger"],"events":[{"actor_ref":"wei","intended_ref":"enemy","result":"miss"}]}
    monkeypatch.setattr(extended,"resolve_exchange",fake_exchange)
    import pytest
    with pytest.raises(ValueError,match="corrupt combat invariant"):
        extended._resolve_player_combat_span(
            combat=combat,people=people,equipment_ledger={"schema":"jianghu-equipment-ledger-1.0","policy_assignments":{},"person_loadouts":{}},
            doctrines={},player_ref="wei",social_state={},player_retinue_context=None,raw_target_ref="auto",raw_action_kind="auto",raw_weapon_ref="auto",hit_zone="auto",
            target_structure_ref=None,targeting_intent=None,explicit_poison_ref=None,poison_auto=False,explicit_qi_allocation_milli={},qi_auto=False,
            exchange_count=2,duration_seconds=None,until_resolution=False,
        )


def test_bounded_explicit_target_becoming_incapacitated_is_a_clean_tactical_frontier(monkeypatch):
    import shinobi_runtime.commands.jianghu_extended as extended
    combat={"status":"active","elapsed_ms":0,"sides":{"side_a":["wei"],"side_b":["enemy","other"]},"combatants":{"wei":{"status_families":[]},"enemy":{"status_families":[]},"other":{"status_families":[]}}}
    people={"wei":{"person_id":"wei","faction_ref":"a","health":{"status":"healthy","consciousness":100}},"enemy":{"person_id":"enemy","faction_ref":"b","health":{"status":"healthy","consciousness":100}},"other":{"person_id":"other","faction_ref":"b","health":{"status":"healthy","consciousness":100}}}
    calls={"count":0}
    monkeypatch.setattr(extended,"default_action_for",lambda **_kw:("unarmed_strike","body_unarmed"))
    def fake_exchange(**kwargs):
        calls["count"]+=1
        after=dict(kwargs["combat"]); after["elapsed_ms"]=250
        next_people={k:dict(v) for k,v in kwargs["people"].items()}
        next_people["enemy"]={**next_people["enemy"],"health":{"status":"incapacitated","consciousness":0}}
        return {"combat_after":after,"people_after":next_people,"equipment_ledger_after":kwargs["equipment_ledger"],"events":[{"actor_ref":"wei","intended_ref":"enemy","result":"contact"}]}
    monkeypatch.setattr(extended,"resolve_exchange",fake_exchange)
    result=extended._resolve_player_combat_span(
        combat=combat,people=people,equipment_ledger={"schema":"jianghu-equipment-ledger-1.0","policy_assignments":{},"person_loadouts":{}},
        doctrines={},player_ref="wei",social_state={},player_retinue_context=None,raw_target_ref="enemy",raw_action_kind="auto",raw_weapon_ref="auto",hit_zone="auto",
        target_structure_ref=None,targeting_intent=None,explicit_poison_ref=None,poison_auto=False,explicit_qi_allocation_milli={},qi_auto=False,
        exchange_count=3,duration_seconds=None,until_resolution=False,
    )
    assert calls["count"]==1
    assert result["scope_stop_reason"]=="explicit_target_unavailable"
    assert result["exchanges_resolved"]==1
