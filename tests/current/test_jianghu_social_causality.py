import copy
import json
from pathlib import Path

import pytest

from shinobi_runtime.martial_world.faction_relations import (
    coalition_target_refs_for_faction,
    refresh_war_coalitions,
)
from shinobi_runtime.martial_world.social_causality import (
    add_personal_obligation,
    add_vow,
    apply_martial_events,
    belief_for,
    hostile_target_pressure,
    internal_action_consensus,
    martial_profile,
    prune_incidental_martial_familiarity,
    record_belief,
    record_martial_contact,
    resolve_personal_obligation,
    revise_belief,
    vow_conflicts,
)
from shinobi_runtime.martial_world.strategic_autonomy import choose_hostile_action

ROOT = Path(__file__).resolve().parents[2]


def load(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def test_personal_obligation_reinforces_one_current_row_then_resolves():
    base = {"schema": "jianghu-social-state-1.0", "relationships": {}}
    first = add_personal_obligation(
        base, actor_ref="a", counterparty_ref="b", kind="life_debt",
        strength=60, created_at="0061-01-01T00:00:00",
    )
    second = add_personal_obligation(
        first["state_after"], actor_ref="a", counterparty_ref="b", kind="life_debt",
        strength=60, created_at="0061-02-01T00:00:00",
    )
    rows = second["state_after"]["obligations"]
    assert list(rows) == [first["obligation_ref"]]
    assert rows[first["obligation_ref"]]["strength"] == 80
    assert rows[first["obligation_ref"]]["created_at"] == "0061-01-01T00:00:00"
    closed = resolve_personal_obligation(second["state_after"], obligation_ref_value=first["obligation_ref"])
    assert closed["resolved"] is True
    assert "obligations" not in closed["state_after"]


def test_belief_is_current_per_observer_claim_and_evidence_revises_it():
    state = {"schema": "jianghu-social-state-1.0", "relationships": {}}
    first = record_belief(
        state, observer_ref="observer", claim_ref="claim.route", subject_ref="movement.1",
        claim_kind="cargo_value", confidence_milli=350, stance="supports",
        source_ref="speaker.a", value_cash=50_000,
    )
    second = record_belief(
        first["state_after"], observer_ref="observer", claim_ref="claim.route", subject_ref="movement.1",
        claim_kind="cargo_value", confidence_milli=700, stance="supports",
        source_ref="speaker.b", value_cash=90_000,
    )
    assert len(second["state_after"]["beliefs"]) == 1
    current = belief_for(second["state_after"], observer_ref="observer", claim_ref="claim.route")
    assert current["source_ref"] == "speaker.b"
    assert current["value_cash"] == 90_000
    revised = revise_belief(
        second["state_after"], belief_ref_value=second["belief_ref"], observer_ref="observer",
        evidence_stance="disproved", evidence_confidence_milli=950,
    )
    assert revised["belief"]["stance"] == "disproved"
    assert revised["belief"]["confidence_milli"] == 950


def test_vows_and_debts_create_real_hostility_pressure_without_forcing_outcome():
    state = {"schema": "jianghu-social-state-1.0", "relationships": {}}
    state = add_personal_obligation(
        state, actor_ref="a", counterparty_ref="b", kind="life_debt",
        strength=80, created_at="x",
    )["state_after"]
    state = add_vow(
        state, person_ref="a", kind="protect_person", strength=90,
        declared_at="x", subject_ref="b",
    )["state_after"]
    pressure = hostile_target_pressure(state, actor_ref="a", target_ref="b")
    assert pressure == -100
    conflicts = vow_conflicts(
        state, person_ref="a", action_kind="attack", target_ref="b", targeting_intent="lethal",
    )
    assert {row["kind"] for row in conflicts} == {"protect_person"}


def test_internal_faction_decision_derives_temporary_loyalty_camps_from_family_and_debt():
    family = {
        "marriages": {"m": {"status": "married", "spouse_refs": ["elder", "target_spouse"]}},
        "parentage": {},
    }
    social = add_personal_obligation(
        {"schema": "jianghu-social-state-1.0", "relationships": {}},
        actor_ref="leader", counterparty_ref="target_friend", kind="life_debt",
        strength=90, created_at="x",
    )["state_after"]
    hostile = internal_action_consensus(
        social, family,
        decision_person_refs=["elder", "leader"],
        person_faction_by_ref={"target_spouse": "target", "target_friend": "target"},
        target_faction_ref="target", target_member_refs=["target_spouse", "target_friend"],
        action_kind="hostile",
    )
    assert "elder" in hostile["oppose_refs"]
    assert hostile["scores"]["leader"] < 0
    aid = internal_action_consensus(
        social, family,
        decision_person_refs=["elder", "leader"],
        person_faction_by_ref={"target_spouse": "target", "target_friend": "target"},
        target_faction_ref="target", target_member_refs=["target_spouse", "target_friend"],
        action_kind="aid",
    )
    assert aid["scores"]["leader"] > 0
    assert aid["scores"]["elder"] > 0


def test_war_coalitions_are_sparse_current_facts_and_feed_existing_war_choice():
    relations = {
        "schema": "jianghu-faction-relations-state-1.0",
        "edges": [
            {"from_faction": "a", "to_faction": "enemy", "hostility": 90},
            {"from_faction": "b", "to_faction": "enemy", "hostility": 90},
            {"from_faction": "a", "to_faction": "b", "trust": 30},
            {"from_faction": "b", "to_faction": "a", "trust": 30},
        ],
    }
    formed = refresh_war_coalitions(relations, at_iso="0061-09-01T00:00:00", faction_refs={"a", "b", "enemy"})
    assert len(formed.get("coalitions", {})) == 1
    assert coalition_target_refs_for_faction(formed, "a") == {"enemy"}
    # Find a deterministic month where the bounded coalition pressure changes
    # the already-lawful war strike from no action to action. It never creates
    # hostility or a target on its own.
    edges = [row for row in relations["edges"] if row["from_faction"] == "a"]
    found = False
    for year in range(61, 75):
        for month in range(1, 13):
            plain = choose_hostile_action(
                edges, faction_ref="a", year=year, month=month, risk_tolerance=10,
                faction_type="sect", coalition_target_refs=set(),
            )
            joined = choose_hostile_action(
                edges, faction_ref="a", year=year, month=month, risk_tolerance=10,
                faction_type="sect", coalition_target_refs={"enemy"},
            )
            if plain is None and joined is not None:
                assert joined["target_faction_ref"] == "enemy"
                assert joined["coalition_pressure"] is True
                found = True
                break
        if found:
            break
    assert found
    # Ending the shared war basis replaces, rather than archives, the coalition.
    ended = copy.deepcopy(formed)
    for row in ended["edges"]:
        if row.get("from_faction") == "b" and row.get("to_faction") == "enemy":
            row["hostility"] = 0
    refreshed = refresh_war_coalitions(ended, at_iso="0061-10-01T00:00:00", faction_refs={"a", "b", "enemy"})
    assert "coalitions" not in refreshed


def test_martial_familiarity_is_bounded_and_changes_default_tactical_role(monkeypatch):
    import shinobi_runtime.martial_world.exact_combat as exact

    state = {"schema": "jianghu-social-state-1.0", "relationships": {}}
    for _ in range(30):
        state = record_martial_contact(
            state, observer_ref="a", opponent_ref="b", opponent_action_kind="staff_sweep", exposure_gain=5,
        )["state_after"]
    profile = martial_profile(state, observer_ref="a", opponent_ref="b")
    assert profile["exposure"] == 100
    assert profile["control_pressure"] == 100
    captured = {}

    def fake_default(actor_ref, person, ledger, *, target_distance_mm, role=None):
        captured["role"] = role
        return "unarmed_strike", "body_unarmed"

    monkeypatch.setattr(exact, "_default_weapon_for", fake_default)
    exact.default_action_for(
        combat={"positions": {"a": {"x_mm": 0, "y_mm": 0}, "b": {"x_mm": 1000, "y_mm": 0}}},
        people={"a": {}, "b": {}}, equipment_ledger={}, actor_ref="a", target_ref="b",
        martial_familiarity=state,
    )
    assert captured["role"] == "control"


def test_martial_familiarity_ignores_same_side_contact_and_prunes_incidental_impressions():
    state = {"schema": "jianghu-social-state-1.0", "relationships": {}}
    state = apply_martial_events(
        state,
        [
            {"actor_ref": "a", "actual_ref": "ally", "action_kind": "cut"},
            {"actor_ref": "a", "actual_ref": "enemy", "action_kind": "cut"},
        ],
        side_by_ref={"a": "side_a", "ally": "side_a", "enemy": "side_b"},
    )
    assert martial_profile(state, observer_ref="ally", opponent_ref="a") is None
    assert martial_profile(state, observer_ref="a", opponent_ref="ally") is None
    assert martial_profile(state, observer_ref="enemy", opponent_ref="a") is not None
    assert martial_profile(state, observer_ref="a", opponent_ref="enemy") is not None
    pruned = prune_incidental_martial_familiarity(state)
    assert "martial_familiarity" not in pruned


def test_autonomous_mass_combat_does_not_materialize_new_pairwise_familiarity(monkeypatch):
    import shinobi_runtime.martial_world.combat_simulation as sim

    roster = load("state/martial-world/people/house_tang.json")["people"]
    selected = [copy.deepcopy(row) for row in roster[:6]]
    people = {row["person_id"]: row for row in selected}
    side_a = [row["person_id"] for row in selected[:3]]
    side_b = [row["person_id"] for row in selected[3:]]
    calls = {"count": 0}

    def fake_resolve_exchange(**kwargs):
        calls["count"] += 1
        combat_after = copy.deepcopy(kwargs["combat"])
        if calls["count"] >= 3:
            combat_after["status"] = "resolved"
            combat_after["winner_side"] = "side_a"
        return {
            "combat_after": combat_after,
            "people_after": copy.deepcopy(kwargs["people"]),
            "equipment_ledger_after": copy.deepcopy(kwargs["equipment_ledger"]),
            "events": [{
                "actor_ref": kwargs["player_ref"],
                "intended_ref": kwargs["player_target_ref"],
                "actual_ref": kwargs["player_target_ref"],
                "action_kind": kwargs["player_action_kind"],
                "result": "miss",
            }],
        }

    monkeypatch.setattr(sim, "resolve_exchange", fake_resolve_exchange)
    result = sim.simulate_exact_combat(
        combat_ref="mass-familiarity", side_a_refs=side_a, side_b_refs=side_b,
        people=people, equipment_ledger=load("state/martial-world/equipment-ledger.json"),
        doctrines={}, zone_ref="site.house_tang", started_at="0061-09-12T00:00:00",
        objective={"kind": "eliminate"}, targeting_intent="disable", max_exchanges=3,
        social_state={"schema": "jianghu-social-state-1.0", "relationships": {}},
    )
    assert calls["count"] == 3
    assert "martial_familiarity" not in result["social_state_after"]


def test_personal_obligation_can_redirect_npc_target_without_granting_combat_success(monkeypatch):
    import shinobi_runtime.martial_world.exact_combat as exact

    roster = load("state/martial-world/people/house_tang.json")["people"]
    player = copy.deepcopy(next(row for row in roster if row["person_id"] == "pc_wei_tang"))
    ally = copy.deepcopy(next(row for row in roster if row["person_id"] == "char.zhu"))
    enemy = copy.deepcopy(next(row for row in roster if row["person_id"] == "char.kai"))
    player["faction_ref"] = ally["faction_ref"] = "fa"
    enemy["faction_ref"] = "fb"
    people = {row["person_id"]: row for row in (player, ally, enemy)}
    combat = exact.initialize_combat(
        combat_ref="social-target", side_a_refs=[player["person_id"], ally["person_id"]],
        side_b_refs=[enemy["person_id"]], people=people, zone_ref="site.house_tang", started_at="0061-09-12T00:00:00",
        objective={"kind": "eliminate", "target_refs": [enemy["person_id"]]},
        equipment_ledger=load("state/martial-world/equipment-ledger.json"),
    )

    def forced_plan(combat, *, side, people, doctrine):
        if side == "side_a":
            assignments = {player["person_id"]: {"target_ref": enemy["person_id"], "role": "pressure"}, ally["person_id"]: {"target_ref": enemy["person_id"], "role": "pressure"}}
        else:
            assignments = {enemy["person_id"]: {"target_ref": player["person_id"], "role": "pressure"}}
        combat.setdefault("team_plans", {})[side] = {"assignments": assignments}
        return combat["team_plans"][side]

    monkeypatch.setattr(exact, "_refresh_team_plan", forced_plan)
    social = {"schema": "jianghu-social-state-1.0", "relationships": {}}
    social = add_personal_obligation(
        social, actor_ref=enemy["person_id"], counterparty_ref=player["person_id"],
        kind="life_debt", strength=100, created_at="x",
    )["state_after"]
    social = add_personal_obligation(
        social, actor_ref=enemy["person_id"], counterparty_ref=ally["person_id"],
        kind="vengeance", strength=100, created_at="x",
    )["state_after"]
    result = exact.resolve_exchange(
        combat=combat, people=people, equipment_ledger=load("state/martial-world/equipment-ledger.json"), doctrines={},
        player_ref=player["person_id"], player_action_kind="unarmed_strike", player_target_ref=enemy["person_id"],
        player_weapon_ref="body_unarmed", player_hit_zone="chest", player_targeting_intent="disable",
        martial_familiarity=social,
    )
    enemy_event = next(row for row in result["events"] if row.get("actor_ref") == enemy["person_id"] and "intended_ref" in row)
    assert enemy_event["intended_ref"] == ally["person_id"]


def test_verified_belief_survives_weaker_later_hearsay_and_drives_current_decision_pressure():
    from shinobi_runtime.martial_world.social_causality import belief_action_pressure

    state = {"schema": "jianghu-social-state-1.0", "relationships": {}}
    verified = record_belief(
        state, observer_ref="elder", claim_ref="crime.target", subject_ref="target",
        claim_kind="property_crime_responsibility", confidence_milli=900,
        stance="confirmed", source_ref="witness", evidence_ref="property_claim:target:item",
    )
    weakened = record_belief(
        verified["state_after"], observer_ref="elder", claim_ref="crime.target", subject_ref="target",
        claim_kind="property_crime_responsibility", confidence_milli=950,
        stance="supports", source_ref="rumor",
    )
    row = belief_for(weakened["state_after"], observer_ref="elder", claim_ref="crime.target")
    assert row["stance"] == "confirmed"
    assert row["evidence_ref"] == "property_claim:target:item"
    assert belief_action_pressure(
        weakened["state_after"], actor_ref="elder", target_person_refs=["target"], action_kind="hostile",
    ) > 0
    assert belief_action_pressure(
        weakened["state_after"], actor_ref="elder", target_person_refs=["target"], action_kind="aid",
    ) < 0


def test_social_sparse_maps_enforce_bounded_current_state():
    state = {"schema": "jianghu-social-state-1.0", "relationships": {}}
    for index in range(32):
        state = add_personal_obligation(
            state, actor_ref="actor", counterparty_ref=f"counterparty.{index}", kind="life_debt",
            strength=10, created_at="x",
        )["state_after"]
    with pytest.raises(ValueError, match="obligation limit"):
        add_personal_obligation(
            state, actor_ref="actor", counterparty_ref="counterparty.overflow", kind="life_debt",
            strength=10, created_at="x",
        )

    belief_state = {"schema": "jianghu-social-state-1.0", "relationships": {}}
    for index in range(80):
        belief_state = record_belief(
            belief_state, observer_ref="observer", claim_ref=f"claim.{index}", subject_ref=f"subject.{index}",
            claim_kind="cargo_value", confidence_milli=100 + index, value_cash=index,
        )["state_after"]
    assert len(belief_state["beliefs"]) == 64

    martial_state = {"schema": "jianghu-social-state-1.0", "relationships": {}}
    for index in range(50):
        martial_state = record_martial_contact(
            martial_state, observer_ref="observer", opponent_ref=f"opponent.{index}", exposure_gain=index + 1,
        )["state_after"]
    rows = [
        row for row in martial_state["martial_familiarity"].values()
        if row["observer_ref"] == "observer"
    ]
    assert len(rows) == 8
    assert min(row["exposure"] for row in rows) >= 43


def test_internal_consensus_consumes_personal_belief_without_treating_it_as_truth():
    social = record_belief(
        {"schema": "jianghu-social-state-1.0", "relationships": {}},
        observer_ref="elder", claim_ref="accusation", subject_ref="target_member",
        claim_kind="property_crime_responsibility", confidence_milli=900, stance="supports",
        source_ref="rumor_source",
    )["state_after"]
    hostile = internal_action_consensus(
        social, {"marriages": {}, "parentage": {}}, decision_person_refs=["elder"],
        person_faction_by_ref={"target_member": "target"}, target_faction_ref="target",
        target_member_refs=["target_member"], action_kind="hostile",
    )
    assert hostile["scores"]["elder"] > 0
    refuted = revise_belief(
        social, belief_ref_value="belief:elder|accusation", observer_ref="elder",
        evidence_stance="disproved", evidence_confidence_milli=950,
    )["state_after"]
    after = internal_action_consensus(
        refuted, {"marriages": {}, "parentage": {}}, decision_person_refs=["elder"],
        person_faction_by_ref={"target_member": "target"}, target_faction_ref="target",
        target_member_refs=["target_member"], action_kind="hostile",
    )
    assert after["scores"]["elder"] < 0


def test_war_coalition_requires_pairwise_compatibility_not_transitive_chain():
    relations = {
        "schema": "jianghu-faction-relations-state-1.0",
        "edges": [
            {"from_faction": source, "to_faction": "enemy", "hostility": 90}
            for source in ("a", "b", "c")
        ] + [
            {"from_faction": "a", "to_faction": "b", "trust": 30},
            {"from_faction": "b", "to_faction": "a", "trust": 30},
            {"from_faction": "b", "to_faction": "c", "trust": 30},
            {"from_faction": "c", "to_faction": "b", "trust": 30},
            {"from_faction": "a", "to_faction": "c", "hostility": 90},
            {"from_faction": "c", "to_faction": "a", "hostility": 90},
        ],
    }
    formed = refresh_war_coalitions(relations, at_iso="0061-09-01T00:00:00", faction_refs={"a", "b", "c", "enemy"})
    coalitions = list(formed.get("coalitions", {}).values())
    assert len(coalitions) == 1
    assert coalitions[0]["member_faction_refs"] == ["a", "b"]
    assert "c" not in coalitions[0]["member_faction_refs"]


def test_faction_retirement_immediately_invalidates_affected_current_coalitions():
    from shinobi_runtime.martial_world.faction_transitions import retire_faction_relations

    state = {
        "schema": "jianghu-faction-relations-state-1.0",
        "edges": [
            {"from_faction": "a", "to_faction": "enemy", "hostility": 90},
            {"from_faction": "b", "to_faction": "enemy", "hostility": 90},
        ],
        "coalitions": {
            "coalition": {
                "member_faction_refs": ["a", "b"], "target_faction_ref": "enemy",
                "purpose": "mutual_war_pressure", "formed_at": "x",
            },
        },
    }
    retired = retire_faction_relations(state, "b")
    assert "coalitions" not in retired
    assert all(row.get("from_faction") != "b" and row.get("to_faction") != "b" for row in retired["edges"])


def test_autonomous_combat_driver_uses_personal_target_pressure_and_obeys_nonlethal_vow(monkeypatch):
    import shinobi_runtime.martial_world.combat_simulation as sim

    roster = load("state/martial-world/people/house_tang.json")["people"]
    people_by_id = {row["person_id"]: copy.deepcopy(row) for row in roster}
    driver = people_by_id["char.zhu"]
    protected = people_by_id["char.ling"]
    avenged = people_by_id["char.kai"]
    driver["faction_ref"] = "fa"
    protected["faction_ref"] = avenged["faction_ref"] = "fb"
    people = {row["person_id"]: row for row in (driver, protected, avenged)}
    social = {"schema": "jianghu-social-state-1.0", "relationships": {}}
    social = add_personal_obligation(
        social, actor_ref=driver["person_id"], counterparty_ref=protected["person_id"],
        kind="life_debt", strength=100, created_at="x",
    )["state_after"]
    social = add_personal_obligation(
        social, actor_ref=driver["person_id"], counterparty_ref=avenged["person_id"],
        kind="vengeance", strength=100, created_at="x",
    )["state_after"]
    social = add_vow(
        social, person_ref=driver["person_id"], kind="nonlethal", strength=100, declared_at="x",
    )["state_after"]
    captured = {}

    def fake_resolve_exchange(**kwargs):
        captured["target_ref"] = kwargs["player_target_ref"]
        captured["intent"] = kwargs["player_targeting_intent"]
        combat_after = copy.deepcopy(kwargs["combat"])
        combat_after["status"] = "resolved"
        combat_after["winner_side"] = "side_a"
        return {
            "combat_after": combat_after,
            "people_after": copy.deepcopy(kwargs["people"]),
            "equipment_ledger_after": copy.deepcopy(kwargs["equipment_ledger"]),
            "events": [{
                "actor_ref": kwargs["player_ref"], "intended_ref": kwargs["player_target_ref"],
                "actual_ref": kwargs["player_target_ref"], "action_kind": kwargs["player_action_kind"], "result": "miss",
            }],
        }

    monkeypatch.setattr(sim, "resolve_exchange", fake_resolve_exchange)
    result = sim.simulate_exact_combat(
        combat_ref="autonomous-social", side_a_refs=[driver["person_id"]],
        side_b_refs=[protected["person_id"], avenged["person_id"]], people=people,
        equipment_ledger=load("state/martial-world/equipment-ledger.json"), doctrines={},
        zone_ref="site.house_tang", started_at="0061-09-12T00:00:00", objective={"kind": "eliminate"},
        targeting_intent="lethal", max_exchanges=1, social_state=social,
    )
    assert captured == {"target_ref": avenged["person_id"], "intent": "disable"}
    assert any(row["kind"] == "nonlethal" for row in result["social_state_after"]["vows"].values())


def test_autonomous_combat_closes_known_broken_nonaggression_promise(monkeypatch):
    import shinobi_runtime.martial_world.combat_simulation as sim

    roster = load("state/martial-world/people/house_tang.json")["people"]
    people_by_id = {row["person_id"]: copy.deepcopy(row) for row in roster}
    actor = people_by_id["char.zhu"]
    target = people_by_id["char.kai"]
    actor["faction_ref"] = "fa"; target["faction_ref"] = "fb"
    social = add_personal_obligation(
        {"schema": "jianghu-social-state-1.0", "relationships": {}},
        actor_ref=actor["person_id"], counterparty_ref=target["person_id"],
        kind="promise_nonaggression", strength=90, created_at="x",
    )["state_after"]

    def fake_resolve_exchange(**kwargs):
        combat_after = copy.deepcopy(kwargs["combat"])
        combat_after["status"] = "resolved"; combat_after["winner_side"] = "side_a"
        return {
            "combat_after": combat_after, "people_after": copy.deepcopy(kwargs["people"]),
            "equipment_ledger_after": copy.deepcopy(kwargs["equipment_ledger"]),
            "events": [{
                "actor_ref": kwargs["player_ref"], "intended_ref": kwargs["player_target_ref"],
                "actual_ref": kwargs["player_target_ref"], "action_kind": kwargs["player_action_kind"], "result": "miss",
            }],
        }

    monkeypatch.setattr(sim, "resolve_exchange", fake_resolve_exchange)
    result = sim.simulate_exact_combat(
        combat_ref="autonomous-breach", side_a_refs=[actor["person_id"]], side_b_refs=[target["person_id"]],
        people={actor["person_id"]: actor, target["person_id"]: target},
        equipment_ledger=load("state/martial-world/equipment-ledger.json"), doctrines={},
        zone_ref="site.house_tang", started_at="0061-09-12T00:00:00", objective={"kind": "eliminate"},
        targeting_intent="disable", max_exchanges=1, social_state=social,
    )
    assert "obligations" not in result["social_state_after"]
    edge = result["social_state_after"]["relationships"][f"{target['person_id']}|{actor['person_id']}"]
    assert edge["trust"] < 0


def test_personal_aid_duty_returns_exact_obligation_that_can_be_closed_after_real_aid():
    from shinobi_runtime.martial_world.social_causality import personal_aid_duty_target

    state = {"schema": "jianghu-social-state-1.0", "relationships": {}}
    first = add_personal_obligation(
        state, actor_ref="elder.a", counterparty_ref="creditor.a", kind="life_debt",
        strength=70, created_at="x",
    )
    second = add_personal_obligation(
        first["state_after"], actor_ref="elder.b", counterparty_ref="creditor.b", kind="promise_aid",
        strength=65, created_at="x",
    )
    chosen = personal_aid_duty_target(
        second["state_after"], decision_person_refs=["elder.a", "elder.b"],
        counterparty_faction_by_ref={"creditor.a": "ally", "creditor.b": "ally"},
        relation_edges=[{"to_faction": "ally", "hostility": 0, "trust": 30}], own_faction_ref="home",
    )
    assert chosen["target_faction_ref"] == "ally"
    assert chosen["obligation_ref"] == first["obligation_ref"]
    closed = resolve_personal_obligation(second["state_after"], obligation_ref_value=chosen["obligation_ref"])
    assert closed["resolved"] is True
    assert first["obligation_ref"] not in closed["state_after"].get("obligations", {})
    assert second["obligation_ref"] in closed["state_after"].get("obligations", {})


def test_autonomous_combat_reuses_one_hydrated_equipment_view_and_compacts_once(monkeypatch):
    import shinobi_runtime.martial_world.combat_simulation as sim

    roster=load('state/martial-world/people/house_tang.json')['people']
    a=copy.deepcopy(roster[0]); b=copy.deepcopy(roster[3])
    a['person_id']='fast.a'; b['person_id']='fast.b'
    people={'fast.a':a,'fast.b':b}
    source={'schema':'jianghu-equipment-ledger-1.0','policy_assignments':{},'person_loadouts':{}}
    before=copy.deepcopy(source); seen={}

    def fake_resolve_exchange(**kwargs):
        seen['hydrated']=kwargs.get('equipment_ledger_hydrated')
        seen['compact']=kwargs.get('compact_equipment_result')
        seen['mutate']=kwargs.get('mutate_equipment_ledger')
        seen['atomic']=bool(kwargs['combat'].get('_atomic_frontier_time'))
        # The autonomous working view may materialize participant-local empty rows.
        assert {'fast.a','fast.b'} <= set(kwargs['equipment_ledger'].get('person_loadouts',{}))
        combat_after=copy.deepcopy(kwargs['combat']); combat_after['status']='resolved'; combat_after['winner_side']='side_a'
        return {
            'combat_after':combat_after,'people_after':copy.deepcopy(kwargs['people']),
            'equipment_ledger_after':kwargs['equipment_ledger'],'events':[],
        }

    monkeypatch.setattr(sim,'resolve_exchange',fake_resolve_exchange)
    result=sim.simulate_exact_combat(
        combat_ref='autonomous-fastpath',side_a_refs=['fast.a'],side_b_refs=['fast.b'],people=people,
        equipment_ledger=source,doctrines={},zone_ref='site.house_tang',started_at='0061-09-12T00:00:00',
        objective={'kind':'eliminate'},targeting_intent='disable',max_exchanges=1,
    )
    assert seen=={'hydrated':True,'compact':False,'mutate':True,'atomic':True}
    assert source==before
    assert result['equipment_ledger_after'].get('person_loadouts',{})=={}
