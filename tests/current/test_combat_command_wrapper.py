import copy
import json
import shutil
from pathlib import Path

from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.martial_world.social_causality import add_vow
from shinobi_runtime.martial_world.equipment_state import effective_person_loadout

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
    # Current-save location is also mutable campaign truth.  Normalize only the
    # copied synthetic combat participants to one known site so wrapper tests do
    # not depend on wherever the live campaign currently has Wei or Tang Zhu.
    roster_path = root / "state/martial-world/people/house_tang.json"
    roster_state = json.loads(roster_path.read_text())
    people = roster_state.get("people", [])
    if isinstance(people, list):
        for person in people:
            if isinstance(person, dict) and person.get("person_id") in {"pc_wei_tang", "char.zhu"}:
                person["location_ref"] = "site.changan.inn"
    roster_path.write_text(json.dumps(roster_state))
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


def test_scene_prop_promotes_through_real_command_wrapper_without_inventory_minting(tmp_path):
    root = _copy_runtime_repository(tmp_path / "scene-prop")
    repo = RepositoryStore(root)
    planner = RepositoryCommandPlanner(repo)
    meta = repo.read_json("state/meta.json")
    player_ref = meta["player_id"]

    open_scene = CommandEnvelope(
        campaign_id=meta["campaign_id"], request_id="test.scene-prop.open", actor_id=player_ref,
        command_type="jianghu_scene_session_resolution", expected_revision=meta["revision"],
        submitted_at="2026-08-27T00:05:00Z",
        payload={"action":"open", "kind":"conversation", "participant_refs":[player_ref], "purpose":"brief local continuity fixture"},
        mode="gameplay",
    )
    opened = planner.plan(open_scene)
    _apply_plan(repo, opened)
    session_ref = opened.result["session_ref"]

    current = repo.read_json("state/meta.json")
    source = CommandEnvelope(
        campaign_id=current["campaign_id"], request_id="test.scene-prop.source", actor_id=player_ref,
        command_type="jianghu_scene_session_resolution", expected_revision=current["revision"],
        submitted_at="2026-08-27T00:05:01Z",
        payload={
            "action":"record_fact", "session_ref":session_ref, "actor_ref":player_ref,
            "fact_kind":"object_state", "description":"A ceramic bowl is already on the table within Wei's reach.",
            "participant_refs":[], "basis_refs":[player_ref],
            "improvised_prop":{"form":"small_rigid", "material":"ceramic", "condition":"intact"},
        }, mode="gameplay",
    )
    source_plan = planner.plan(source)
    _apply_plan(repo, source_plan)
    source_ref = source_plan.result["fact_ref"]

    current = repo.read_json("state/meta.json")
    typed = CommandEnvelope(
        campaign_id=current["campaign_id"], request_id="test.scene-prop.typed", actor_id=player_ref,
        command_type="jianghu_scene_session_resolution", expected_revision=current["revision"],
        submitted_at="2026-08-27T00:05:02Z",
        payload={
            "action":"record_fact", "session_ref":session_ref, "actor_ref":player_ref,
            "fact_kind":"object_state", "description":"Wei takes up the already-established ceramic bowl.",
            "participant_refs":[], "basis_refs":[source_ref],
            "improvised_prop":{"form":"small_rigid", "material":"ceramic", "condition":"intact"},
        }, mode="gameplay",
    )
    typed_plan = planner.plan(typed)
    _apply_plan(repo, typed_plan)
    prop_ref = typed_plan.result["fact_ref"]

    before_equipment = repo.read_json("state/martial-world/equipment-ledger.json")
    current = repo.read_json("state/meta.json")
    combat_ref = "combat.test.scene-prop-wrapper"
    start = CommandEnvelope(
        campaign_id=current["campaign_id"], request_id="test.scene-prop.start", actor_id=player_ref,
        command_type="jianghu_combat_resolution", expected_revision=current["revision"],
        submitted_at="2026-08-27T00:05:03Z",
        payload={
            "action":"start", "combat_ref":combat_ref,
            "side_a_refs":[player_ref], "side_b_refs":["char.zhu"],
            "objective":{"kind":"eliminate", "target_refs":["char.zhu"]},
            "awareness_mode":"mutual", "initial_range_band":2,
        }, mode="gameplay",
    )
    start_plan = planner.plan(start)
    _apply_plan(repo, start_plan)

    closed = repo.read_json("state/martial-world/scene-session.json")
    assert closed["status"] == "closed"
    assert closed["close_reason"] == "hard_interruption"

    current = repo.read_json("state/meta.json")
    exchange = CommandEnvelope(
        campaign_id=current["campaign_id"], request_id="test.scene-prop.exchange", actor_id=player_ref,
        command_type="jianghu_combat_resolution", expected_revision=current["revision"],
        submitted_at="2026-08-27T00:05:04Z",
        payload={"action":"exchange", "combat_ref":combat_ref, "improvised_prop_fact_ref":prop_ref},
        mode="gameplay",
    )
    preview = planner.preview(exchange)
    assert preview.status == "ready"
    plan = planner.plan(exchange)
    combat_after = json.loads(plan.writes["state/martial-world/combats.json"].decode("utf-8"))["combats"][combat_ref]
    transient = combat_after["combatants"][player_ref]["improvised_weapon_state"]
    assert transient["fact_ref"] == prop_ref
    assert transient["source_object_fact_ref"] == source_ref
    assert transient["durable_item_created"] is False
    assert any(
        row.get("actor_ref") == player_ref and row.get("action_kind") == "improvised_strike"
        for row in plan.result["events"]
    )
    # Planning the exchange must not mutate the repository before commit.
    assert repo.read_json("state/martial-world/equipment-ledger.json") == before_equipment
    # The improvised scene prop is combat-local and must never become durable
    # inventory. The exchange may still legitimately wear real weapons carried
    # by either participant, so do not require the entire equipment ledger to
    # remain byte-for-byte unchanged.
    planned_equipment = (
        json.loads(plan.writes["state/martial-world/equipment-ledger.json"].decode("utf-8"))
        if "state/martial-world/equipment-ledger.json" in plan.writes
        else before_equipment
    )
    before_player_items = effective_person_loadout(before_equipment, player_ref).get("items", {})
    after_player_items = effective_person_loadout(planned_equipment, player_ref).get("items", {})
    assert after_player_items == before_player_items
    assert prop_ref not in json.dumps(planned_equipment, sort_keys=True)


def test_declared_combat_span_preserves_chronological_projection_across_exchanges(monkeypatch):
    import shinobi_runtime.commands.jianghu_extended as extended

    combat = {
        "status": "active", "elapsed_ms": 0,
        "sides": {"side_a": ["wei", "ally"], "side_b": ["enemy"]},
        "combatants": {
            "wei": {"status_families": []},
            "ally": {"status_families": []},
            "enemy": {"status_families": []},
        },
    }
    people = {
        "wei": {"person_id": "wei", "faction_ref": "a", "health": {"status": "healthy", "consciousness": 100}},
        "ally": {"person_id": "ally", "faction_ref": "a", "health": {"status": "healthy", "consciousness": 100}},
        "enemy": {"person_id": "enemy", "faction_ref": "b", "health": {"status": "healthy", "consciousness": 100}},
    }
    calls = {"count": 0}
    monkeypatch.setattr(extended, "default_target_for", lambda **_kw: "enemy")
    monkeypatch.setattr(extended, "default_action_for", lambda **_kw: ("unarmed_strike", "body_unarmed"))

    def fake_exchange(**kwargs):
        calls["count"] += 1
        n = calls["count"]
        after = copy.deepcopy(dict(kwargs["combat"]))
        after["elapsed_ms"] = n * 500
        beats = [{"kind": f"ordinary_{n}", "at_ms": n * 500, "salience": "ordinary"}]
        if n == 1:
            beats.append({
                "kind": "critical_ally_casualty", "actor_ref": "ally", "at_ms": 450,
                "salience": "protected", "must_narrate_before_next_decision": True,
            })
        return {
            "combat_after": after,
            "people_after": copy.deepcopy(kwargs["people"]),
            "equipment_ledger_after": copy.deepcopy(kwargs["equipment_ledger"]),
            "events": [],
            "narrative_projection": {
                "beats": beats,
                "narration_rules": [f"rule_{n}"],
                "current_visibility": {"visible_hostiles_current": n},
            },
        }

    monkeypatch.setattr(extended, "resolve_exchange", fake_exchange)
    result = extended._resolve_player_combat_span(
        combat=combat, people=people,
        equipment_ledger={"schema": "jianghu-equipment-ledger-1.0", "policy_assignments": {}, "person_loadouts": {}},
        doctrines={}, player_ref="wei", social_state={}, player_retinue_context=None,
        raw_target_ref="auto", raw_action_kind="auto", raw_weapon_ref="auto", hit_zone="auto",
        target_structure_ref=None, targeting_intent=None, explicit_poison_ref=None, poison_auto=False,
        explicit_qi_allocation_milli={}, qi_auto=False, exchange_count=2, duration_seconds=None,
        until_resolution=False,
    )
    projection = result["narrative_projection"]
    assert result["exchanges_resolved"] == 2
    assert projection["schema"] == "shinobi-combat-narrative-projection-1.1"
    assert [row["kind"] for row in projection["beats"]] == [
        "critical_ally_casualty", "ordinary_1", "ordinary_2"
    ]
    assert projection["protected_salience_count"] == 1
    assert projection["current_visibility"] == {"visible_hostiles_current": 2}
    assert projection["narration_rules"] == ["rule_1", "rule_2"]


def test_route_owned_resolved_combat_cannot_be_closed_before_route_reconciliation(tmp_path):
    import pytest
    from shinobi_runtime.api.contracts import CommandRejectedError

    root = _copy_runtime_repository(tmp_path / "route-owned-end")
    combat_path = root / "state/martial-world/combats.json"
    state = json.loads(combat_path.read_text())
    combat_ref = "combat.test.route-owned-resolved"
    state.setdefault("combats", {})[combat_ref] = {
        "combat_id": combat_ref,
        "status": "resolved",
        "winner_side": "side_a",
        "elapsed_ms": 1000,
        "sides": {"side_a": ["pc_wei_tang"], "side_b": ["char.zhu"]},
        "combatants": {
            "pc_wei_tang": {"status_families": []},
            "char.zhu": {"status_families": ["escaped"]},
        },
        "objective": {
            "kind": "preserve_route_mission",
            "movement_ref": "escort_muster:test-route",
        },
    }
    combat_path.write_text(json.dumps(state))

    repo = RepositoryStore(root)
    planner = RepositoryCommandPlanner(repo)
    meta = repo.read_json("state/meta.json")
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"], request_id="test.route-owned.end",
        actor_id=meta["player_id"], command_type="jianghu_combat_resolution",
        expected_revision=meta["revision"], submitted_at="2026-09-04T00:00:00Z",
        payload={"action": "end", "combat_ref": combat_ref}, mode="gameplay",
    )
    with pytest.raises(CommandRejectedError, match="jianghu_combat_pending_route_resolution"):
        planner.plan(command)
