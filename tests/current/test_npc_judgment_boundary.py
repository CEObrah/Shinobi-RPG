from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from shinobi_runtime.api.gm_scene_context import build_gm_scene_context
from shinobi_runtime.martial_world import exact_combat as exact

ROOT = Path(__file__).resolve().parents[2]
BASE = json.loads((ROOT / "state/martial-world/people/house_tang.json").read_text())["people"][0]


def _fighter(ref: str, faction: str, *, skill: int = 75) -> dict:
    row = copy.deepcopy(BASE)
    row["person_id"] = ref
    row["name"] = ref
    row["faction_ref"] = faction
    row["health"] = {"status":"ready","injuries":[],"blood_lost_ml":0,"shock":0,"consciousness":100}
    row["fatigue_milli"] = 0
    row["qi"] = 0
    row["current_qi_milli"] = 0
    row["attributes"] = {k:75 for k in ("strength","speed","dexterity","endurance","perception","intelligence","willpower")}
    row["martial_skills"] = {k:0 for k in ("sword","spear","bow","hidden_weapons","unarmed","stealth_scouting","command")}
    row["martial_skills"]["sword"] = skill
    row.pop("combat_doctrine_ref", None)
    return row


def _ledger() -> dict:
    return {
        "schema":"jianghu-equipment-ledger-1.0",
        "policy_assignments":{},
        "person_loadouts":{
            "a":{"items":{"weapon_jian":1}},
            "b":{"items":{"weapon_jian":1}},
        },
    }


def _duel():
    people={"a":_fighter("a","fa"),"b":_fighter("b","fb")}
    ledger=_ledger()
    combat=exact.initialize_combat(
        combat_ref="judgment.duel",side_a_refs=("a",),side_b_refs=("b",),people=people,
        zone_ref="z",started_at="x",objective={"kind":"eliminate","target_refs":["b"]},
        awareness_mode="mutual",initial_range_band=0,equipment_ledger=ledger,
        initial_ready_weapons={"a":"weapon_jian","b":"weapon_jian"},
    )
    return combat,people,ledger


def test_sparse_direct_holding_does_not_hide_policy_weapon() -> None:
    ledger={
        "schema":"jianghu-equipment-ledger-1.0",
        "policy_assignments":{"house_tang_sword_issue":["wei"]},
        "person_loadouts":{"wei":{"items":{"weapon_needle":16}}},
    }
    items=exact._loadout_items(ledger,"wei")
    assert items["weapon_jian"] == 1
    assert items["weapon_needle"] == 16


def test_live_exact_combat_requires_gm_authored_npc_judgments() -> None:
    combat,people,ledger=_duel()
    with pytest.raises(ValueError,match="gm npc judgments required"):
        exact.resolve_exchange(
            combat=combat,people=people,equipment_ledger=ledger,doctrines={},player_ref="a",
            player_action_kind="hold",player_target_ref="a",player_weapon_ref="body_unarmed",
            require_gm_npc_judgments=True,
        )


def test_gm_judgment_envelope_scores_constraints_without_selecting_intent() -> None:
    combat,people,ledger=_duel()
    rows=exact.combat_npc_judgment_envelopes(
        combat=combat,people=people,equipment_ledger=ledger,doctrines={},player_ref="a",
    )
    assert len(rows)==1
    row=rows[0]
    assert row["actor_ref"]=="b"
    assert row["requires_llm_authored_intent"] is True
    assert set(row["allowed_intents"])=={"attack","hold","withdraw"}
    assert "intent" not in row
    assert row["visible_target_options"][0]["target_ref"]=="a"


def test_gm_authored_hold_is_validated_and_does_not_generate_attack() -> None:
    combat,people,ledger=_duel()
    result=exact.resolve_exchange(
        combat=combat,people=people,equipment_ledger=ledger,doctrines={},player_ref="a",
        player_action_kind="hold",player_target_ref="a",player_weapon_ref="body_unarmed",
        npc_judgments={"b":{"intent":"hold"}},require_gm_npc_judgments=True,
        compact_equipment_result=False,
    )
    b_events=[row for row in result["events"] if row.get("actor_ref")=="b"]
    assert any(row.get("result")=="holding_guard_position" and row.get("decision_origin")=="gm_npc_judgment" for row in b_events)
    assert not any(row.get("action_kind") in {"cut","thrust"} for row in b_events)


def test_gm_scene_context_preserves_private_combat_judgment_envelopes() -> None:
    context={
        "campaign":{"player_id":"a"},
        "scene":{
            "active_combat":True,
            "present_person_ids":["a"],"visible_person_ids":["a"],
            "gm_private_director_context":{
                "combat":{
                    "privacy":"gm_private_exact_combat_direction_not_player_knowledge",
                    "combat_ref":"judgment.duel","status":"active","elapsed_ms":0,
                    "npc_judgment_envelopes":[{"actor_ref":"hidden.b","requires_llm_authored_intent":True,"allowed_intents":["attack","hold","withdraw"]}],
                    "npc_judgment_contract":{"meaningful_choice_owner":"chatgpt","freshness":"one_exact_decision_frontier"},
                }
            },
        },
        "player":{"person_id":"a"},
    }
    built=build_gm_scene_context(context)
    private=built["gm_private_scene_truth"]["combat"]
    assert private["npc_judgment_envelopes"][0]["actor_ref"]=="hidden.b"
    assert private["npc_judgment_contract"]["meaningful_choice_owner"]=="chatgpt"


def test_failed_exact_elbow_precision_can_drift_to_adjacent_region() -> None:
    actor=_fighter("attacker","fa")
    actor["martial_skills"]["sword"]=45
    defender=_fighter("defender","fb")
    weapon=json.loads((ROOT/"game/data/martial-world/equipment.json").read_text())["weapon_catalog"]["weapon_jian"]
    contacted=[]
    for tick in range(32):
        damage=exact._contact_damage(
            actor=actor,defender=defender,weapon=weapon,weapon_ref="weapon_jian",action_kind="thrust",
            range_m=0.9,defense_force_milli=1000,hit_zone="elbow",target_structure_ref="right_elbow",
            created_at=f"t{tick}",precision_margin=-40,
        )
        contacted.append(damage["target_zone_contacted"])
        assert damage["target_structure_contacted"] is None
    assert any(zone != "elbow" for zone in contacted)


def test_durable_npc_judgment_command_commits_exact_current_option_without_advancing_time(tmp_path) -> None:
    import shutil
    from shinobi_runtime.commands.envelope import CommandEnvelope
    from shinobi_runtime.commands.planner import RepositoryCommandPlanner
    from shinobi_runtime.martial_world.npc_judgment import build_npc_judgment_event
    from shinobi_runtime.martial_world.scheduler import upsert_one_off_event
    from shinobi_runtime.store import RepositoryStore

    root = tmp_path / "repo"
    shutil.copytree(ROOT, root)
    meta = json.loads((root / "state/meta.json").read_text())
    scheduler_path = root / "state/martial-world/scheduler.json"
    schedule = json.loads(scheduler_path.read_text())
    event = build_npc_judgment_event(
        judgment_kind="faction_investment",
        at_iso=str(meta["time"]).removeprefix("SE-"),
        actor_ref="faction.black_lance_company",
        owner_ref="faction.black_lance_company",
        options=[{"kind":"expand_building","building_type":"training_yard","additional_footprint_m2":25}],
        decision_context={"advisory":"test"},
        source_event_id="faction_review:test",
    )
    schedule = upsert_one_off_event(schedule, event)
    scheduler_path.write_text(json.dumps(schedule, ensure_ascii=False, indent=2) + "\n")
    repo = RepositoryStore(root)
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"], request_id="request.test.npc.judgment",
        actor_id="gm.runtime", command_type="jianghu_npc_judgment_resolution",
        expected_revision=int(meta["revision"]), submitted_at="2026-09-11T16:00:00Z",
        payload={"event_id":event["event_id"],"option_ref":event["options"][0]["option_ref"]},
        mode="autonomous",
    )
    plan = RepositoryCommandPlanner(repo).plan(command)
    written = json.loads(plan.writes["state/martial-world/scheduler.json"].decode())
    assert event["event_id"] not in written["one_off"]
    committed = [row for row in written["one_off"].values() if row.get("kind")=="npc_judgment_committed"]
    assert len(committed)==1
    assert committed[0]["judgment_kind"]=="faction_investment"
    assert committed[0]["selected_option"]["option_ref"]==event["options"][0]["option_ref"]
    new_meta = json.loads(plan.writes["state/meta.json"].decode())
    assert new_meta["time"]==meta["time"]
    assert new_meta["revision"]==int(meta["revision"])+1


def test_durable_npc_judgment_cannot_select_player_voluntary_intent(tmp_path) -> None:
    import shutil
    from shinobi_runtime.api.contracts import CommandRejectedError
    from shinobi_runtime.commands.envelope import CommandEnvelope
    from shinobi_runtime.commands.planner import RepositoryCommandPlanner
    from shinobi_runtime.martial_world.npc_judgment import build_npc_judgment_event
    from shinobi_runtime.martial_world.scheduler import upsert_one_off_event
    from shinobi_runtime.store import RepositoryStore

    root = tmp_path / "repo"
    shutil.copytree(ROOT, root)
    meta = json.loads((root / "state/meta.json").read_text())
    scheduler_path = root / "state/martial-world/scheduler.json"
    schedule = json.loads(scheduler_path.read_text())
    event = build_npc_judgment_event(
        judgment_kind="faction_investment", at_iso=str(meta["time"]).removeprefix("SE-"),
        actor_ref=meta["player_id"], owner_ref=meta["player_id"],
        options=[{"kind":"expand_building","building_type":"training_yard","additional_footprint_m2":25}],
    )
    scheduler_path.write_text(json.dumps(upsert_one_off_event(schedule,event),ensure_ascii=False,indent=2)+"\n")
    command=CommandEnvelope(
        campaign_id=meta["campaign_id"],request_id="request.test.npc.player.block",
        actor_id="gm.runtime",command_type="jianghu_npc_judgment_resolution",
        expected_revision=int(meta["revision"]),submitted_at="2026-09-11T16:00:00Z",
        payload={"event_id":event["event_id"],"option_ref":event["options"][0]["option_ref"]},mode="autonomous",
    )
    with pytest.raises(CommandRejectedError,match="cannot_choose_player_intent"):
        RepositoryCommandPlanner(RepositoryStore(root)).preview(command)


def test_faction_judgment_cannot_hide_player_inside_exact_participant_payload(tmp_path) -> None:
    import shutil
    from shinobi_runtime.api.contracts import CommandRejectedError
    from shinobi_runtime.commands.envelope import CommandEnvelope
    from shinobi_runtime.commands.planner import RepositoryCommandPlanner
    from shinobi_runtime.martial_world.npc_judgment import build_npc_judgment_event
    from shinobi_runtime.martial_world.scheduler import upsert_one_off_event
    from shinobi_runtime.store import RepositoryStore

    root = tmp_path / "repo"
    shutil.copytree(ROOT, root)
    meta = json.loads((root / "state/meta.json").read_text())
    scheduler_path = root / "state/martial-world/scheduler.json"
    schedule = json.loads(scheduler_path.read_text())
    event = build_npc_judgment_event(
        judgment_kind="route_interception_policy",
        at_iso=str(meta["time"]).removeprefix("SE-"),
        actor_ref="house_tang",
        owner_ref="movement:test:nested-player",
        options=[{
            "intent":"attack",
            "attacker_faction_ref":"house_tang",
            "attacker_refs":[meta["player_id"], "npc.house_tang.test"],
            "leader_ref":meta["player_id"],
            # Targeting Wei would be lawful for an NPC, but volunteering Wei as
            # an attacker/leader is not. This regression exercises the nested
            # role-aware guard rather than the top-level actor_ref check.
            "target_ref":"npc.other.target",
        }],
    )
    scheduler_path.write_text(json.dumps(upsert_one_off_event(schedule,event),ensure_ascii=False,indent=2)+"\n")
    command=CommandEnvelope(
        campaign_id=meta["campaign_id"],request_id="request.test.npc.nested-player.block",
        actor_id="gm.runtime",command_type="jianghu_npc_judgment_resolution",
        expected_revision=int(meta["revision"]),submitted_at="2026-09-11T16:00:00Z",
        payload={"event_id":event["event_id"],"option_ref":event["options"][0]["option_ref"]},mode="autonomous",
    )
    with pytest.raises(CommandRejectedError,match="cannot_choose_player_intent"):
        RepositoryCommandPlanner(RepositoryStore(root)).preview(command)


def test_nested_player_guard_still_allows_npc_to_target_player() -> None:
    from shinobi_runtime.martial_world.npc_judgment import option_authors_player_intent

    assert option_authors_player_intent(
        judgment_kind="route_interception_policy",
        option={"intent":"attack","attacker_refs":["npc.bandit"],"target_ref":"pc_wei_tang"},
        player_ref="pc_wei_tang",
    ) is False


def test_durable_npc_judgment_blocks_player_hidden_in_faction_option(tmp_path) -> None:
    import shutil
    from shinobi_runtime.api.contracts import CommandRejectedError
    from shinobi_runtime.commands.envelope import CommandEnvelope
    from shinobi_runtime.commands.planner import RepositoryCommandPlanner
    from shinobi_runtime.martial_world.npc_judgment import (
        build_npc_judgment_event, option_authors_player_intent,
    )
    from shinobi_runtime.martial_world.scheduler import upsert_one_off_event
    from shinobi_runtime.store import RepositoryStore

    root = tmp_path / "repo"
    shutil.copytree(ROOT, root)
    meta = json.loads((root / "state/meta.json").read_text())
    player_ref = str(meta["player_id"])
    # NPCs may lawfully choose Wei as a target. The guard protects only roles
    # that would author Wei's voluntary participation or control.
    assert option_authors_player_intent(
        judgment_kind="route_interception_policy",
        option={"intent":"attack", "attacker_refs":["npc.raider"], "leader_ref":"npc.raider", "target_ref":player_ref},
        player_ref=player_ref,
    ) is False
    assert option_authors_player_intent(
        judgment_kind="route_interception_policy",
        option={"intent":"attack", "attacker_refs":[player_ref, "npc.raider"], "leader_ref":"npc.raider", "target_ref":"npc.target"},
        player_ref=player_ref,
    ) is True

    scheduler_path = root / "state/martial-world/scheduler.json"
    schedule = json.loads(scheduler_path.read_text())
    event = build_npc_judgment_event(
        judgment_kind="route_interception_policy",
        at_iso=str(meta["time"]).removeprefix("SE-"),
        actor_ref="faction.black_lance_company", owner_ref="faction.black_lance_company",
        options=[{
            "intent":"attack", "attacker_refs":[player_ref, "npc.raider"],
            "leader_ref":"npc.raider", "target_ref":"npc.target",
        }],
    )
    scheduler_path.write_text(json.dumps(upsert_one_off_event(schedule,event),ensure_ascii=False,indent=2)+"\n")
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"], request_id="request.test.npc.nested-player.block",
        actor_id="gm.runtime", command_type="jianghu_npc_judgment_resolution",
        expected_revision=int(meta["revision"]), submitted_at="2026-09-11T16:00:00Z",
        payload={"event_id":event["event_id"],"option_ref":event["options"][0]["option_ref"]},
        mode="autonomous",
    )
    with pytest.raises(CommandRejectedError, match="cannot_choose_player_intent"):
        RepositoryCommandPlanner(RepositoryStore(root)).preview(command)


def test_hostile_strategy_judgment_offers_stand_down_and_applies_exact_selected_detachment(monkeypatch) -> None:
    from datetime import datetime, timedelta
    import shinobi_runtime.martial_world.autonomy_frontier as autonomy_frontier
    from shinobi_runtime.martial_world.scheduler import initial_schedule
    from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier

    def load(rel: str):
        return json.loads((ROOT / rel).read_text())

    monkeypatch.setattr(
        autonomy_frontier, "autonomy_review",
        lambda *_args, **_kwargs: {
            "ordered_actions":["address_hostile_relation"],
            "scored_actions":[{"action":"address_hostile_relation","score":1}],
        },
    )
    meta = load("state/meta.json")
    at = datetime.fromisoformat(str(meta["time"]).removeprefix("SE-"))
    schedule = initial_schedule(start=at - timedelta(hours=1), faction_ids=[], region_ids=[], route_ids=[])
    faction_ref = "faction.black_oar_pirates"
    proposal = settle_martial_world_frontier(
        read_json=load, schedule=schedule,
        events=[{"kind":"faction_review","owner_ref":faction_ref,"event_id":"test:hostile-strategy"}],
        at=at,
    )
    judgments = [
        row for row in proposal["schedule_after"]["one_off"].values()
        if row.get("kind") == "npc_judgment_due" and row.get("judgment_kind") == "faction_hostile_strategy"
    ]
    assert len(judgments) == 1
    judgment = judgments[0]
    assert any(row.get("intent") == "stand_down" for row in judgment["options"])
    attacks = [row for row in judgment["options"] if row.get("intent") != "stand_down"]
    assert attacks
    assert len({tuple(row["participant_refs"]) for row in attacks}) > 1
    assert len({row["leader_ref"] for row in attacks}) > 1
    assert all(row["leader_ref"] in row["participant_refs"] for row in attacks)
    assert all(str(meta["player_id"]) not in row["participant_refs"] for row in attacks)

    selected = copy.deepcopy(attacks[-1])
    committed = {
        "event_id":"npc_judgment_committed:test-hostile-strategy",
        "kind":"npc_judgment_committed", "due_at":at.isoformat(),
        "owner_ref":faction_ref, "actor_ref":faction_ref,
        "judgment_kind":"faction_hostile_strategy", "decision_ref":judgment["decision_ref"],
        "selected_option":selected, "decision_context":copy.deepcopy(judgment["decision_context"]),
    }
    applied = settle_martial_world_frontier(
        read_json=load, schedule=schedule, events=[committed], at=at,
    )
    review = next(row for row in applied["reviews"] if row.get("kind") == "npc_judgment_applied")
    deployment = applied["writes"]["state/martial-world/deployments.json"]["deployments"][review["operation_ref"]]
    assert deployment["participant_refs"] == selected["participant_refs"]
    assert deployment["leader_ref"] == selected["leader_ref"]
    assert deployment["operation_intent"] == selected["operation_intent"]
    assert deployment["mobilization_basis"] == "gm_authored_exact_detachment"


def test_offscreen_exact_combat_fails_closed_without_authored_standing_policy() -> None:
    from shinobi_runtime.martial_world.combat_simulation import simulate_exact_combat

    people={"a":_fighter("a","fa"),"b":_fighter("b","fb")}
    with pytest.raises(ValueError,match="authored standing combat policy"):
        simulate_exact_combat(
            combat_ref="offscreen.no-policy",side_a_refs=["a"],side_b_refs=["b"],people=people,
            equipment_ledger=_ledger(),doctrines={},zone_ref="z",started_at="0061-01-01T00:00:00",
            objective={"kind":"eliminate"},targeting_intent="disable",max_exchanges=1,
        )


def test_offscreen_authored_policy_is_executed_through_explicit_npc_judgments(monkeypatch) -> None:
    import shinobi_runtime.martial_world.combat_simulation as simulation

    people={"a":_fighter("a","fa"),"b":_fighter("b","fb")}
    captured={}

    def fake_resolve_exchange(**kwargs):
        captured["required"] = kwargs.get("require_gm_npc_judgments")
        captured["npc_judgments"] = copy.deepcopy(kwargs.get("npc_judgments"))
        after=copy.deepcopy(kwargs["combat"])
        after["status"]="resolved"; after["winner_side"]="side_a"
        return {
            "combat_after":after,
            "people_after":copy.deepcopy(kwargs["people"]),
            "equipment_ledger_after":kwargs["equipment_ledger"],
            "events":[],
        }

    monkeypatch.setattr(simulation,"resolve_exchange",fake_resolve_exchange)
    result=simulation.simulate_exact_combat(
        combat_ref="offscreen.authored-policy",side_a_refs=["a"],side_b_refs=["b"],people=people,
        equipment_ledger=_ledger(),doctrines={},zone_ref="z",started_at="0061-01-01T00:00:00",
        objective={"kind":"eliminate"},targeting_intent="disable",max_exchanges=1,
        authored_combat_policy={
            "decision_origin":"gm_npc_judgment",
            "engagement_intent":"press_and_disable",
            "targeting_intent":"disable",
            "target_policy":"nearest_visible",
            "resource_policy":"none",
            "withdrawal_policy":"no_voluntary_withdrawal",
        },
    )
    assert result["resolved"] is True
    assert captured["required"] is True
    assert set(captured["npc_judgments"]) == {"b"}
    assert captured["npc_judgments"]["b"]["decision_origin"] == "gm_npc_judgment"
    assert captured["npc_judgments"]["b"]["intent"] in {"attack","hold"}


def test_friendly_aid_requires_exact_authored_amount_within_current_bounds() -> None:
    from shinobi_runtime.martial_world.faction_relations import (
        friendly_aid_transfer_envelope, resolve_friendly_aid_transfer,
    )

    source={"population":10,"treasury_cash":100_000,"institution_type":"house"}
    target={"population":10,"treasury_cash":0,"institution_type":"house"}
    inventory={"transport_capacity":{}}
    envelope=friendly_aid_transfer_envelope(source,inventory,target,inventory)
    assert envelope["result"]=="aid_available"
    minimum=int(envelope["minimum_cash"]); maximum=int(envelope["maximum_cash"])
    assert minimum<=maximum
    chosen=max(minimum,maximum//2)
    applied=resolve_friendly_aid_transfer(source,inventory,target,inventory,amount_cash=chosen)
    assert applied["result"]=="aid_transferred"
    assert applied["cash"]==chosen
    assert applied["source_after"]["treasury_cash"]==source["treasury_cash"]-chosen
    assert applied["target_after"]["treasury_cash"]==target["treasury_cash"]+chosen
    rejected=resolve_friendly_aid_transfer(source,inventory,target,inventory,amount_cash=maximum+1)
    assert rejected["result"]=="aid_amount_outside_current_envelope"


def test_route_interception_options_vary_complete_party_and_motive_without_player() -> None:
    from shinobi_runtime.martial_world.escort_living_world import interception_attack_options

    options=interception_attack_options(
        attacker_faction_ref="faction.band",target_movement_ref="movement.test",route_ref="route.test",
        candidate_attacker_refs=["pc_wei_tang","a","b","c","d"],player_ref="pc_wei_tang",
        decision={
            "advisory_force_count":3,"cargo_value_cash":5000,"ransom_value_cash":7000,
            "hostility":80,"criminal":True,
        },maximum_options=64,
    )
    assert options
    assert all("pc_wei_tang" not in row["attacker_refs"] for row in options)
    party_sets={tuple(sorted(row["attacker_refs"])) for row in options}
    motives={(row["contact_intent"],row["motive_kind"]) for row in options}
    assert len(party_sets)>1
    assert len(motives)>1
    assert all(row["leader_ref"] in row["attacker_refs"] for row in options)
