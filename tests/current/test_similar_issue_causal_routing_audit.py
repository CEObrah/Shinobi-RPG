from __future__ import annotations

from datetime import datetime

from shinobi_runtime.martial_world.captivity_frontier import settle_captivity_frontier


def test_ransom_message_chases_moved_recipient_before_granting_information():
    review_at = datetime(61, 9, 14, 10, 0)
    custody_id = "custody:test:moving-ransom-recipient"
    captive_ref = "person:test:captive"
    custody_state = {
        "schema": "jianghu-custody-state-1.0",
        "records": [{
            "custody_id": custody_id,
            "person_ref": captive_ref,
            "holder_faction_ref": "faction.red_willow_band",
            "status": "restrained",
            "location_ref": "luoyang",
        }],
    }
    factions = {
        "faction.red_willow_band": {
            "faction_id": "faction.red_willow_band",
            "headquarters": "luoyang",
            "autonomy_policy": {"risk_tolerance": 50},
        },
        "house_tang": {
            "faction_id": "house_tang",
            "headquarters": "luoyang",
            "autonomy_policy": {"risk_tolerance": 50},
            "treasury_cash": 1_000_000,
        },
    }

    def load_faction(fid):
        return f"state/factions/{fid}.json", factions[fid]

    def load_person_ref(ref):
        assert ref == captive_ref
        return "house_tang", "state/person.json", {}, 0, {
            "person_id": captive_ref,
            "social_rank": "commoner",
        }

    rescue_calls: list[tuple[str, str]] = []

    def rescue(fid, record, _choice=None):
        rescue_calls.append((fid, str(record.get("custody_id") or "")))
        return {"result": "response_deferred"}

    # Captor valuation/feasibility creates a human-policy decision; it does not
    # invent ransom terms or dispatch a message before the GM chooses "demand".
    first_pending: list[dict] = []
    first_handoffs: list[dict] = []
    settle_captivity_frontier(
        events=[{
            "kind": "custody_captor_review",
            "event_id": "review:test:moving-ransom-recipient",
            "owner_ref": custody_id,
            "person_ref": captive_ref,
        }],
        at=review_at,
        world_seed="test-seed", player_ref="",
        family_state={}, custody_state=custody_state,
        deployments_state={"deployments": []}, writes={}, reviews=[],
        handoffs=first_handoffs, pending_one_off_events=first_pending,
        load_faction=load_faction, load_person_ref=load_person_ref,
        start_custody_rescue_operation=rescue,
        apply_directed_relation_event=lambda *_args: None,
        faction_cache={}, get_commitments_state=lambda: {},
        set_commitments_state=lambda _row: None,
    )
    assert len(first_pending) == 1
    judgment = first_pending[0]
    assert judgment["kind"] == "npc_judgment_due"
    assert judgment["judgment_kind"] == "captor_ransom_policy"
    demand = next(row for row in judgment["options"] if row.get("intent") == "demand")
    committed = {
        "event_id": "npc_judgment_committed:test:moving-ransom-recipient",
        "kind": "npc_judgment_committed",
        "due_at": review_at.isoformat(),
        "owner_ref": custody_id,
        "actor_ref": judgment["actor_ref"],
        "judgment_kind": judgment["judgment_kind"],
        "decision_ref": judgment["decision_ref"],
        "selected_option": demand,
        "decision_context": judgment["decision_context"],
    }
    message_pending: list[dict] = []
    settle_captivity_frontier(
        events=[committed], at=review_at, world_seed="test-seed", player_ref="",
        family_state={}, custody_state=custody_state,
        deployments_state={"deployments": []}, writes={}, reviews=[], handoffs=[],
        pending_one_off_events=message_pending, load_faction=load_faction,
        load_person_ref=load_person_ref, start_custody_rescue_operation=rescue,
        apply_directed_relation_event=lambda *_args: None, faction_cache={},
        get_commitments_state=lambda: {}, set_commitments_state=lambda _row: None,
    )
    assert len(message_pending) == 1
    message = message_pending[0]
    assert message["information_source"] == "ransom_message"
    assert message["message_origin_location_ref"] == "luoyang"
    assert message["message_target_location_ref"] == "luoyang"

    # The addressed institution moves before the local courier reaches the old
    # headquarters. Arrival at the stale endpoint must not grant knowledge.
    factions["house_tang"] = {**factions["house_tang"], "headquarters": "kaifeng"}
    due_at = datetime.fromisoformat(message["due_at"])
    second_pending: list[dict] = []
    second_reviews: list[dict] = []
    settle_captivity_frontier(
        events=[message], at=due_at, world_seed="test-seed", player_ref="",
        family_state={}, custody_state=custody_state,
        deployments_state={"deployments": []}, writes={}, reviews=second_reviews,
        handoffs=[], pending_one_off_events=second_pending,
        load_faction=load_faction, load_person_ref=load_person_ref,
        start_custody_rescue_operation=rescue,
        apply_directed_relation_event=lambda *_args: None, faction_cache={},
        get_commitments_state=lambda: {}, set_commitments_state=lambda _row: None,
    )
    assert rescue_calls == []
    assert "house_tang" not in custody_state["records"][0].get("informed_faction_refs", [])
    assert any(row.get("result") == "ransom_message_rerouted" for row in second_reviews)
    assert len(second_pending) == 1
    rerouted = second_pending[0]
    assert rerouted["message_origin_location_ref"] == "luoyang"
    assert rerouted["message_target_location_ref"] == "kaifeng"
    assert rerouted["information_route_status"] == "rerouted_in_transit"
    assert datetime.fromisoformat(rerouted["due_at"]) > due_at

    # Only arrival at the current endpoint grants institutional knowledge.  The
    # institution's rescue/pay/defer choice remains a fresh GM judgment.
    third_pending: list[dict] = []
    third_reviews: list[dict] = []
    settle_captivity_frontier(
        events=[rerouted], at=datetime.fromisoformat(rerouted["due_at"]),
        world_seed="test-seed", player_ref="", family_state={},
        custody_state=custody_state, deployments_state={"deployments": []},
        writes={}, reviews=third_reviews, handoffs=[],
        pending_one_off_events=third_pending, load_faction=load_faction,
        load_person_ref=load_person_ref, start_custody_rescue_operation=rescue,
        apply_directed_relation_event=lambda *_args: None, faction_cache={},
        get_commitments_state=lambda: {}, set_commitments_state=lambda _row: None,
    )
    assert rescue_calls == []
    assert "house_tang" in custody_state["records"][0].get("informed_faction_refs", [])
    response = next(row for row in third_pending if row.get("kind") == "npc_judgment_due")
    assert response["judgment_kind"] == "captive_response_policy"
    assert {row.get("intent") for row in response["options"]} >= {"defer", "rescue", "pay_ransom"}


def test_government_warrant_mobilizes_before_player_contact():
    from shinobi_runtime.martial_world.regional_frontier import settle_regional_frontier

    subject_ref = "pc_wei_tang"
    warrant_ref = "warrant:test:player-mobilization"
    government = {
        "schema": "jianghu-government-state-1.0",
        "attention": {subject_ref: {"attention": 100, "bounty_cash": 0, "prior_offenses": 1}},
        "warrants": {warrant_ref: {
            "subject_ref": subject_ref,
            "offense": "assault",
            "status": "active",
            "jurisdiction_ref": "central_plain",
            "evidence_ref": "evidence:test",
        }},
        "regional_capacity": {},
    }
    custody = {"schema": "jianghu-custody-state-1.0", "records": []}
    market = {
        "schema": "jianghu-regional-market-state-1.0",
        "region_id": "central_plain",
        "inventory": {},
        "price_index_milli": 1000,
        "cash_pool": 0,
        "last_cycle": 0,
    }
    subject = {"person_id": subject_ref, "location_ref": "site.test.player"}
    pending: list[dict] = []
    handoffs: list[dict] = []
    reviews: list[dict] = []
    writes: dict = {}
    common = dict(
        player_ref=subject_ref,
        government_state=government,
        government_troops={
            "default_regional_capacity": {"militia": 5, "standard": 2, "elite": 1},
            "monthly_reconstitution": {"militia": 0, "standard": 0, "elite": 0},
            "contact_resolution": {"militia_power": 35, "standard_power": 65, "elite_power": 95, "detention_advantage_milli": 1800},
        },
        custody_state=custody,
        writes=writes,
        reviews=reviews,
        handoffs=handoffs,
        market_cache={},
        load_market=lambda _region: ("state/martial-world/markets/central_plain.json", dict(market)),
        load_person_ref=lambda _ref: ("house_tang", "state/martial-world/people/house_tang.json", {}, 0, dict(subject)),
        unavailable_person_refs=lambda: set(),
        pause_people_for_commitment=lambda *_args: None,
        person_combat_index=lambda _person: 1,
        site_rows={"site.test.player": {"parent_place_ref": "luoyang"}},
        place_region={"luoyang": "central_plain"},
        pending_one_off_events=pending,
    )
    review_at = datetime(61, 10, 1, 6, 0)
    settle_regional_frontier(
        events=[{"kind": "regional_market_cycle", "owner_ref": "central_plain", "event_id": "test:gov:monthly"}],
        at_iso=review_at.isoformat(),
        **common,
    )
    assert handoffs == []
    assert government["warrants"][warrant_ref]["status"] == "mobilizing"
    assert len(pending) == 1
    contact = pending.pop()
    assert contact["kind"] == "government_contact_due"
    assert datetime.fromisoformat(contact["due_at"]) == review_at.replace(day=2)

    settle_regional_frontier(events=[contact], at_iso=contact["due_at"], **common)
    summons = [row for row in handoffs if row.get("kind") == "government_summons"]
    assert len(summons) == 1
    assert summons[0]["requires_player_decision"] is True
    assert summons[0]["delivered_to_player"] is True


def test_government_warrant_requires_gm_policy_before_public_cash_moves():
    from shinobi_runtime.martial_world.regional_frontier import settle_regional_frontier

    subject_ref = "person:test:outlaw"
    government = {
        "schema": "jianghu-government-state-1.0",
        "attention": {subject_ref: {"attention": 80, "bounty_cash": 0, "prior_offenses": 1}},
        "warrants": {},
        "regional_capacity": {},
    }
    custody = {"schema": "jianghu-custody-state-1.0", "records": []}
    market = {
        "schema": "jianghu-regional-market-state-1.0",
        "region_id": "central_plain",
        "inventory": {}, "price_index_milli": 1000,
        "cash_pool": 10_000, "last_cycle": 0,
    }
    writes: dict = {}
    reviews: list[dict] = []
    market_cache: dict = {}
    at_iso = datetime(61, 10, 1, 6, 0).isoformat()
    common = dict(
        player_ref="pc_wei_tang",
        government_state=government,
        government_troops={}, custody_state=custody, writes=writes,
        reviews=reviews, handoffs=[], market_cache=market_cache,
        load_market=lambda _region: ("state/martial-world/markets/central_plain.json", dict(market)),
        load_person_ref=lambda _ref: (_ for _ in ()).throw(KeyError(_ref)),
        unavailable_person_refs=lambda: set(),
        pause_people_for_commitment=lambda *_args: None,
        person_combat_index=lambda _person: 1,
        site_rows={}, place_region={}, pending_one_off_events=[],
    )

    # Objective attention alone is not an official decision.
    settle_regional_frontier(events=[], at_iso=at_iso, **common)
    assert government["warrants"] == {}
    assert market["cash_pool"] == 10_000
    assert not any(path.endswith("central_plain.json") for path in writes)

    committed = {
        "kind": "npc_judgment_committed",
        "event_id": "npc_judgment_committed:test:government-policy",
        "judgment_kind": "government_enforcement_policy",
        "decision_ref": "npc-judgment:test:government-policy",
        "selected_option": {
            "intent": "issue_warrant",
            "subject_ref": subject_ref,
            "jurisdiction_ref": "central_plain",
            "offense": "violent_robbery",
            "evidence_ref": "evidence:test:robbery",
            "bounty_cash": 1_500,
        },
        "decision_context": {"subject_ref": subject_ref, "jurisdiction_ref": "central_plain"},
    }
    settle_regional_frontier(events=[committed], at_iso=at_iso, **common)
    warrant = government["warrants"][f"warrant:{subject_ref}"]
    assert warrant["status"] == "active"
    assert warrant["bounty_cash"] == 1_500
    assert warrant["decision_origin"] == "gm_npc_judgment"
    market_path = "state/martial-world/markets/central_plain.json"
    assert writes[market_path]["cash_pool"] == 8_500
    assert government["attention"][subject_ref]["bounty_cash"] == 1_500
