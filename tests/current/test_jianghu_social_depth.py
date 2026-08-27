from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.martial_world.death_lifecycle import clean_social_and_custody_for_deaths
from shinobi_runtime.martial_world.faction_relations import (
    coalition_target_refs_for_faction,
    refresh_war_coalitions,
)
from shinobi_runtime.martial_world.social_causality import (
    add_personal_obligation,
    add_vow,
    apply_martial_events,
    belief_for,
    breach_hostile_commitments,
    internal_action_consensus,
    martial_profile,
    prune_beliefs_for_subject_refs,
    record_belief,
    resolve_personal_obligation,
    revise_belief,
    vow_conflicts,
)
from shinobi_runtime.martial_world.strategic_autonomy import choose_hostile_action


def test_personal_obligations_are_sparse_reinforced_and_resolved_without_history():
    state = {"schema": "jianghu-social-state-1.0"}
    first = add_personal_obligation(
        state,
        actor_ref="person.debtor",
        counterparty_ref="person.creditor",
        kind="life_debt",
        strength=60,
        created_at="SE-0061-09-14T09:15:00",
    )
    second = add_personal_obligation(
        first["state_after"],
        actor_ref="person.debtor",
        counterparty_ref="person.creditor",
        kind="life_debt",
        strength=60,
        created_at="SE-0061-10-01T00:00:00",
    )
    rows = second["state_after"]["obligations"]
    assert list(rows) == [first["obligation_ref"]]
    assert rows[first["obligation_ref"]]["strength"] == 80
    assert rows[first["obligation_ref"]]["created_at"] == "SE-0061-09-14T09:15:00"
    assert "history" not in second["state_after"]

    closed = resolve_personal_obligation(second["state_after"], obligation_ref_value=first["obligation_ref"])
    assert closed["resolved"] is True
    assert "obligations" not in closed["state_after"]


def test_hostile_breach_closes_promises_and_conflicting_vows_but_not_life_debt_or_vengeance():
    state = {"schema": "jianghu-social-state-1.0"}
    for kind in ("life_debt", "promise_aid", "promise_protect", "promise_nonaggression", "vengeance"):
        state = add_personal_obligation(
            state,
            actor_ref="person.a",
            counterparty_ref="person.b",
            kind=kind,
            strength=70,
            created_at="SE-0061-09-14T09:15:00",
        )["state_after"]
    state = add_vow(
        state,
        person_ref="person.a",
        kind="protect_person",
        subject_ref="person.b",
        strength=90,
        declared_at="SE-0061-09-14T09:15:00",
    )["state_after"]
    state = add_vow(
        state,
        person_ref="person.a",
        kind="nonlethal",
        strength=80,
        declared_at="SE-0061-09-14T09:15:00",
    )["state_after"]

    result = breach_hostile_commitments(
        state,
        actor_ref="person.a",
        target_ref="person.b",
        targeting_intent="lethal",
    )
    remaining_kinds = {row["kind"] for row in result["state_after"]["obligations"].values()}
    assert remaining_kinds == {"life_debt", "vengeance"}
    assert len(result["broken_obligation_refs"]) == 3
    assert len(result["broken_vow_refs"]) == 2
    assert "vows" not in result["state_after"]


def test_beliefs_are_exact_current_claims_and_route_subjects_prune_cleanly():
    state = {"schema": "jianghu-social-state-1.0"}
    created = record_belief(
        state,
        observer_ref="person.observer",
        claim_ref="route-value:movement.1",
        subject_ref="movement.1",
        claim_kind="cargo_value",
        confidence_milli=600,
        stance="supports",
        source_ref="person.speaker",
        value_cash=100_000,
    )
    ref = created["belief_ref"]
    assert belief_for(created["state_after"], observer_ref="person.observer", claim_ref="route-value:movement.1")["value_cash"] == 100_000

    revised = revise_belief(
        created["state_after"],
        belief_ref_value=ref,
        observer_ref="person.observer",
        evidence_stance="disproved",
        evidence_confidence_milli=950,
    )
    row = revised["state_after"]["beliefs"][ref]
    assert row["stance"] == "disproved"
    assert row["confidence_milli"] == 950

    pruned = prune_beliefs_for_subject_refs(revised["state_after"], ["movement.1"])
    assert "beliefs" not in pruned
    assert "belief_history" not in pruned


def test_vows_have_real_conflicts_without_forcing_player_action():
    state = add_vow(
        {"schema": "jianghu-social-state-1.0"},
        person_ref="person.a",
        kind="nonlethal",
        strength=100,
        declared_at="SE-0061-09-14T09:15:00",
    )["state_after"]
    state = add_vow(
        state,
        person_ref="person.a",
        kind="no_poison",
        strength=90,
        declared_at="SE-0061-09-14T09:15:00",
    )["state_after"]
    conflicts = vow_conflicts(
        state,
        person_ref="person.a",
        action_kind="combat",
        target_ref="person.b",
        targeting_intent="lethal",
        poison_ref="poison.test",
    )
    assert {row["kind"] for row in conflicts} == {"nonlethal", "no_poison"}
    # The reducer reports conflicts; it does not reject the declared action.
    assert state["vows"]


def test_martial_familiarity_is_bounded_current_opponent_learning_not_combat_history():
    state = {"schema": "jianghu-social-state-1.0"}
    events = [
        {"actor_ref": "person.a", "actual_ref": "person.b", "action_kind": "cut"},
        {"actor_ref": "person.a", "actual_ref": "person.b", "action_kind": "cut"},
        {"actor_ref": "person.b", "actual_ref": "person.a", "action_kind": "bow_shot"},
    ]
    state = apply_martial_events(state, events)
    a_knows_b = martial_profile(state, observer_ref="person.a", opponent_ref="person.b")
    b_knows_a = martial_profile(state, observer_ref="person.b", opponent_ref="person.a")
    assert a_knows_b and b_knows_a
    assert 0 < a_knows_b["exposure"] <= 100
    assert a_knows_b["ranged_pressure"] > 0
    assert b_knows_a["melee_pressure"] > 0
    assert len(state["martial_familiarity"]) == 2
    assert not any("history" in key for key in state)


def test_conflicting_loyalties_derive_temporary_camps_from_family_debt_and_vows():
    social = {"schema": "jianghu-social-state-1.0"}
    social = add_personal_obligation(
        social,
        actor_ref="person.elder",
        counterparty_ref="person.target_kin",
        kind="life_debt",
        strength=80,
        created_at="SE-0061-09-14T09:15:00",
    )["state_after"]
    social = add_vow(
        social,
        person_ref="person.elder",
        kind="repay_debts",
        strength=80,
        declared_at="SE-0061-09-14T09:15:00",
    )["state_after"]
    family = {
        "parentage": {
            "person.elder": {"parent_refs": ["person.parent"]},
            "person.target_kin": {"parent_refs": ["person.parent"]},
        }
    }
    faction_by_person = {"person.target_kin": "faction.target"}

    hostile = internal_action_consensus(
        social,
        family,
        decision_person_refs=["person.elder"],
        person_faction_by_ref=faction_by_person,
        target_faction_ref="faction.target",
        target_member_refs=["person.target_kin"],
        action_kind="attack",
    )
    assert hostile["oppose_refs"] == ["person.elder"]
    assert hostile["pressure"] <= -70

    aid = internal_action_consensus(
        social,
        family,
        decision_person_refs=["person.elder"],
        person_faction_by_ref=faction_by_person,
        target_faction_ref="faction.target",
        target_member_refs=["person.target_kin"],
        action_kind="aid",
    )
    assert aid["support_refs"] == ["person.elder"]
    assert aid["pressure"] > 0
    assert "camp" not in social and "decision_history" not in social


def test_shared_war_coalitions_exist_only_while_real_war_and_compatibility_exist():
    state = {
        "schema": "jianghu-faction-relations-state-1.0",
        "edges": [
            {"from_faction": "faction.a", "to_faction": "faction.enemy", "hostility": 70},
            {"from_faction": "faction.b", "to_faction": "faction.enemy", "hostility": 75},
            {"from_faction": "faction.a", "to_faction": "faction.b", "trust": 20},
            {"from_faction": "faction.b", "to_faction": "faction.a", "trust": 20},
        ],
    }
    current = refresh_war_coalitions(state, at_iso="SE-0061-10-01T00:00:00")
    assert len(current.get("coalitions", {})) == 1
    assert coalition_target_refs_for_faction(current, "faction.a") == {"faction.enemy"}
    coalition = next(iter(current["coalitions"].values()))
    assert coalition["member_faction_refs"] == ["faction.a", "faction.b"]

    # End one member's war. The current coalition is recomputed away rather
    # than archived into a history ledger.
    ended = dict(current)
    ended["edges"] = [dict(row) for row in current["edges"]]
    for row in ended["edges"]:
        if row.get("from_faction") == "faction.b" and row.get("to_faction") == "faction.enemy":
            row["hostility"] = 20
    closed = refresh_war_coalitions(ended, at_iso="SE-0061-11-01T00:00:00")
    assert "coalitions" not in closed
    assert "coalition_history" not in closed


def test_coalition_pressure_only_boosts_an_already_lawful_war_target():
    war_edge = {"from_faction": "faction.a", "to_faction": "faction.enemy", "hostility": 65}
    found = None
    for year in range(61, 161):
        for month in range(1, 13):
            base = choose_hostile_action(
                [war_edge], faction_ref="faction.a", year=year, month=month,
                risk_tolerance=0, faction_type="sect", coalition_target_refs=set(),
            )
            coordinated = choose_hostile_action(
                [war_edge], faction_ref="faction.a", year=year, month=month,
                risk_tolerance=0, faction_type="sect", coalition_target_refs={"faction.enemy"},
            )
            if base is None and coordinated is not None:
                found = coordinated
                break
        if found is not None:
            break
    assert found is not None
    assert found["action"] == "faction_war_strike"
    assert found["coalition_pressure"] is True

    peace_edge = {"from_faction": "faction.a", "to_faction": "faction.friend", "hostility": 5, "trust": 50}
    assert choose_hostile_action(
        [peace_edge], faction_ref="faction.a", year=61, month=9,
        risk_tolerance=100, faction_type="sect", coalition_target_refs={"faction.friend"},
    ) is None


def test_death_cleanup_closes_dead_person_social_authority_without_archaeology():
    social = {"schema": "jianghu-social-state-1.0"}
    social = add_personal_obligation(
        social, actor_ref="person.dead", counterparty_ref="person.live", kind="life_debt",
        strength=60, created_at="SE-0061-09-14T09:15:00",
    )["state_after"]
    social = add_vow(
        social, person_ref="person.live", kind="protect_person", subject_ref="person.dead",
        strength=70, declared_at="SE-0061-09-14T09:15:00",
    )["state_after"]
    social = apply_martial_events(
        social, [{"actor_ref": "person.dead", "actual_ref": "person.live", "action_kind": "cut"}],
    )
    social = record_belief(
        social, observer_ref="person.dead", claim_ref="claim.1", subject_ref="place.1",
        claim_kind="test_claim", confidence_milli=700,
    )["state_after"]

    social_after, custody_after, released = clean_social_and_custody_for_deaths(
        social, {"records": []}, dead_refs=["person.dead"],
    )
    assert "obligations" not in social_after
    assert "vows" not in social_after
    assert "martial_familiarity" not in social_after
    assert "beliefs" not in social_after
    assert custody_after["records"] == []
    assert released == set()


def test_social_command_surface_advertises_full_current_lifecycle():
    spec = COMMAND_SPECS["jianghu_social_resolution"]
    assert set(spec.variants) == {
        "promise",
        "make_vow",
        "release_vow",
        "forgive_obligation",
        "renounce_obligation",
        "hear_claim",
        "investigate",
    }


def _social_command(root, payload, *, request_id):
    from shinobi_runtime.commands.envelope import CommandEnvelope
    from shinobi_runtime.commands.planner import RepositoryCommandPlanner
    from shinobi_runtime.store.repository import RepositoryStore

    repo = RepositoryStore(root)
    meta = repo.read_json("state/meta.json")
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id=request_id,
        actor_id=meta["player_id"],
        command_type="jianghu_social_resolution",
        expected_revision=meta["revision"],
        submitted_at="2026-08-25T00:00:00Z",
        payload=payload,
        mode="gameplay",
    )
    return repo, RepositoryCommandPlanner(repo), command


def test_social_command_promise_forgiveness_renunciation_and_heard_claim_use_real_writes(tmp_path):
    import json
    import shutil
    from pathlib import Path

    root = tmp_path / "campaign"
    shutil.copytree(Path(__file__).resolve().parents[2] / "state", root / "state")
    shutil.copytree(Path(__file__).resolve().parents[2] / "game", root / "game")

    # A declared promise creates one exact unresolved obligation.
    repo, planner, command = _social_command(
        root,
        {"action": "promise", "other_ref": "char.zhu", "promise_kind": "aid", "strength": 70},
        request_id="social.promise",
    )
    preview = planner.preview(command)
    assert preview.status == "ready"
    built = planner._build(command)
    social_after = json.loads(built.writes["state/martial-world/social.json"])
    ref = "obligation:pc_wei_tang|char.zhu|promise_aid"
    assert social_after["obligations"][ref]["strength"] == 70

    # A debtor cannot erase the beneficiary's claim by calling it forgiveness.
    (root / "state/martial-world/social.json").write_text(json.dumps(social_after), encoding="utf-8")
    repo, planner, command = _social_command(
        root, {"action": "forgive_obligation", "obligation_ref": ref}, request_id="social.bad-forgive",
    )
    import pytest
    from shinobi_runtime.api.contracts import CommandRejectedError
    with pytest.raises(CommandRejectedError, match="jianghu_obligation_forgiveness_not_owned"):
        planner.preview(command)

    # The promisor can openly renounce the duty while the beneficiary is present,
    # but the current relationship records the breach instead of silently erasing it.
    before = social_after["relationships"]["char.zhu|pc_wei_tang"]["trust"]
    repo, planner, command = _social_command(
        root, {"action": "renounce_obligation", "obligation_ref": ref}, request_id="social.renounce",
    )
    preview = planner.preview(command)
    assert preview.status == "ready"
    built = planner._build(command)
    renounced = json.loads(built.writes["state/martial-world/social.json"])
    assert ref not in renounced.get("obligations", {})
    assert renounced["relationships"]["char.zhu|pc_wei_tang"]["trust"] < before

    # A beneficiary may forgive an actual debt owed to them.
    owed = add_personal_obligation(
        renounced,
        actor_ref="char.zhu",
        counterparty_ref="pc_wei_tang",
        kind="life_debt",
        strength=60,
        created_at="SE-0061-09-14T09:15:00",
    )
    owed_ref = owed["obligation_ref"]
    (root / "state/martial-world/social.json").write_text(json.dumps(owed["state_after"]), encoding="utf-8")
    repo, planner, command = _social_command(
        root, {"action": "forgive_obligation", "obligation_ref": owed_ref}, request_id="social.good-forgive",
    )
    assert planner.preview(command).status == "ready"

    # Beliefs are reachable from real dialogue context. The source must be present,
    # and the subject must be a current exact route movement.
    route_ops = json.loads((root / "state/martial-world/route-operations.json").read_text(encoding="utf-8"))
    movement_ref = next(iter(route_ops["movements"]))
    repo, planner, command = _social_command(
        root,
        {
            "action": "hear_claim",
            "source_ref": "char.zhu",
            "subject_ref": movement_ref,
            "claim_kind": "cargo_value",
            "claimed_value_cash": 123_456,
        },
        request_id="social.hear",
    )
    assert planner.preview(command).status == "ready"
    built = planner._build(command)
    heard = json.loads(built.writes["state/martial-world/social.json"])
    rows = [row for row in heard.get("beliefs", {}).values() if row.get("observer_ref") == "pc_wei_tang" and row.get("subject_ref") == movement_ref]
    assert len(rows) == 1
    assert rows[0]["source_ref"] == "char.zhu"
    assert rows[0]["value_cash"] == 123_456

    # A spoken accusation is also stored as belief rather than fact. It can be
    # false, and investigation consults only a registered current evidence token.
    ledger_path = root / "state/martial-world/equipment-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger.setdefault("provenance_exceptions", {}).setdefault("char.kai", {})["test_sword"] = {
        "owner_ref": "house_tang", "quantity": 1, "status": "seized",
    }
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    evidence_ref = "property_claim:char.kai:test_sword"
    repo, planner, command = _social_command(
        root,
        {
            "action": "hear_claim", "source_ref": "char.zhu", "subject_ref": "char.kai",
            "claim_kind": "property_crime_responsibility", "evidence_ref": evidence_ref,
        },
        request_id="social.hear-crime",
    )
    assert planner.preview(command).status == "ready"
    built = planner._build(command)
    accused = json.loads(built.writes["state/martial-world/social.json"])
    crime_ref = next(
        ref for ref, row in accused["beliefs"].items()
        if row.get("observer_ref") == "pc_wei_tang"
        and row.get("subject_ref") == "char.kai"
        and row.get("claim_kind") == "property_crime_responsibility"
    )
    assert accused["beliefs"][crime_ref]["stance"] in {"supports", "uncertain"}
    (root / "state/martial-world/social.json").write_text(json.dumps(accused), encoding="utf-8")
    repo, planner, command = _social_command(
        root, {"action": "investigate", "belief_ref": crime_ref}, request_id="social.investigate-crime",
    )
    assert planner.preview(command).status == "ready"
    investigated = json.loads(planner._build(command).writes["state/martial-world/social.json"] )
    assert investigated["beliefs"][crime_ref]["stance"] == "confirmed"
