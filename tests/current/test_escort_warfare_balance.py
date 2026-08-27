import shinobi_runtime.martial_world.route_frontier as route_frontier
from shinobi_runtime.martial_world.route_activity import route_controlling_refs
from shinobi_runtime.martial_world.commitments import extend_commitment_resources
from shinobi_runtime.martial_world.escort import (
    minimum_martial_escorts,
    ordinary_public_lot_quantity,
    plan_escort_objective,
    quote_escort_objective,
    route_transport_plan,
)
from shinobi_runtime.martial_world.money import format_copper
from shinobi_runtime.martial_world.warfare import local_frontage_count, strategic_operation_targeting_intent


def test_strategic_operation_targeting_intent_preserves_saved_or_objective_policy():
    assert strategic_operation_targeting_intent({"operation_kind": "faction_raid", "operation_intent": "robbery"}) == "disable"
    assert strategic_operation_targeting_intent({"operation_kind": "faction_raid", "operation_intent": "punitive_expedition"}) == "lethal"
    assert strategic_operation_targeting_intent({"operation_kind": "faction_war_strike"}) == "lethal"
    assert strategic_operation_targeting_intent({"operation_kind": "custody_rescue"}) == "disable"
    assert strategic_operation_targeting_intent({"operation_kind": "faction_war_strike", "targeting_intent": "disable"}) == "disable"


def _route(**extra):
    row = {
        "id": "route.test",
        "from": "a",
        "to": "b",
        "distance_km": 120,
        "terrain": "hills",
        "road_quality": "maintained",
        "allowed_modes": ["convoy"],
        "toll_cash": 20,
    }
    row.update(extra)
    return row


def _travel():
    return {
        "mode_speed_km_per_day": {"convoy": 24},
        "terrain_time_milli": {"hills": 1150},
        "road_time_milli": {"maintained": 1000},
    }


def test_aggregate_shortage_becomes_one_normal_physical_public_lot_not_a_mega_convoy():
    assert ordinary_public_lot_quantity("food_ration_day", 7_322_985) == 12_000
    objective = plan_escort_objective(
        kind="escort_shipment",
        route=_route(), travel=_travel(),
        source_place_ref="a", destination_place_ref="b",
        item_ref="food_ration_day", quantity=12_000, cargo_value_cash=324_000,
    )
    assert objective["cargo_mass_kg"] == 12_000
    assert objective["transport_mode"] == "aggregate_freight"
    assert objective["freight_capacity_kg"] == 12_000
    assert objective["civilian_crew_count"] > 0
    assert "wagon_count" not in objective and "draft_animal_count" not in objective
    assert objective["minimum_escort_count"] >= 2
    assert quote_escort_objective(objective)["total_reward_cash"] < 50_000


def test_public_lot_target_is_not_a_world_convoy_or_escort_cap():
    transport = route_transport_plan(cargo_kg=1_200_000, route=_route())
    assert transport["transport_mode"] == "aggregate_freight"
    assert transport["freight_capacity_kg"] == 1_200_000
    escorts = minimum_martial_escorts(
        transport=transport,
        protected_people=0,
        distance_km_tenths=1200,
        terrain="hills",
        threat_score=70,
    )
    assert escorts > 24


def test_person_escort_uses_people_and_route_risk_without_fake_wagons():
    objective = plan_escort_objective(
        kind="escort_party",
        route=_route(), travel=_travel(),
        source_place_ref="a", destination_place_ref="b",
        protected_people_count=20, civilian_party_kind="pilgrims",
    )
    assert objective["escort_kind"] == "person"
    assert objective["protected_people_count"] == 20
    assert objective["cargo_mass_kg"] == 0
    assert objective["transport_mode"] == "travel_party"
    assert objective["freight_capacity_kg"] == 0
    assert "wagon_count" not in objective and "pack_animal_count" not in objective
    assert objective["minimum_escort_count"] > 2


def test_mixed_convoy_carries_both_people_and_goods():
    objective = plan_escort_objective(
        kind="escort_mixed_convoy",
        route=_route(), travel=_travel(),
        source_place_ref="a", destination_place_ref="b",
        item_ref="food_ration_day", quantity=6000, cargo_value_cash=162_000,
        protected_people_count=5, civilian_party_kind="merchant_principals",
    )
    assert objective["escort_kind"] == "mixed"
    assert objective["protected_people_count"] == 5
    assert objective["cargo_mass_kg"] == 6000
    assert objective["transport_mode"] == "aggregate_freight"
    assert objective["freight_capacity_kg"] == 6000


def test_local_exact_frontage_scales_with_physical_site_and_is_not_the_force_size():
    cramped = local_frontage_count({"capacity": 25, "site_type": "inn"})
    open_ground = local_frontage_count({"capacity": 10_000, "site_type": "tournament_ground"})
    assert cramped < open_ground
    assert open_ground > 24


def test_existing_commitment_can_expand_to_a_large_exact_muster_without_second_owner():
    state = {
        "schema": "jianghu-commitment-state-1.0",
        "commitments": {
            "commitment:war.test": {
                "commitment_ref": "commitment:war.test",
                "activity_ref": "war.test",
                "activity_kind": "faction_war_strike",
                "kind": "faction_war_strike",
                "actor_ref": "p0",
                "owner_ref": "house.test",
                "resources": [{"kind": "person", "ref": "p0", "owner_ref": "house.test"}],
                "person_refs": ["p0"],
                "started_at": "0061-01-01T00:00:00",
                "status": "active",
            }
        },
        "person_index": {"p0": "commitment:war.test"},
    }
    resources = [("person", f"p{i}", "house.test") for i in range(1, 61)]
    after = extend_commitment_resources(state, activity_ref="war.test", resources=resources)
    row = after["commitments"]["commitment:war.test"]
    assert len(row["person_refs"]) == 61
    assert len(after["commitments"]) == 1
    assert len(after["person_index"]) == 61


def test_player_facing_money_uses_copper_and_taels():
    assert format_copper(25) == "25 copper"
    assert format_copper(1000) == "1 tael"
    assert format_copper(3118) == "3 taels, 118 copper"


def test_active_escort_commits_exact_people_and_route_attack_uses_exact_combat(monkeypatch):
    """Route danger may decide *whether* contact occurs, never its casualties.

    Once hostile contact exists, the movement's exact escort refs and exact
    outlaw refs must be handed to the exact combat resolver. Its person
    after-images are then written back to those same persistent identities.
    """
    import copy
    import json
    from datetime import datetime, timedelta
    from pathlib import Path

    import shinobi_runtime.martial_world.warfare as warfare
    from shinobi_runtime.martial_world.commitments import derived_commitment_state
    from shinobi_runtime.martial_world.faction_state import hydrate_faction_state
    from shinobi_runtime.martial_world.health import wound_from_contact
    from shinobi_runtime.martial_world.person_state import hydrate_roster_state
    from shinobi_runtime.martial_world.scheduler import due_events, initial_schedule, sync_route_activity
    from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier

    root = Path(__file__).resolve().parents[2]

    def load(rel):
        return json.loads((root / rel).read_text())

    blocked = set(derived_commitment_state(load).get("person_index", {}))
    escort_fid = "long_road_escorts"
    escort_faction = hydrate_faction_state(load(f"state/martial-world/factions/{escort_fid}.json"))
    escort_roster = hydrate_roster_state(
        load(f"state/martial-world/people/{escort_fid}.json"), faction=escort_faction,
    )
    escort_refs = [
        str(person["person_id"])
        for person in escort_roster["people"]
        if str(person.get("person_id")) not in blocked
        and (person.get("health", {}) or {}).get("status") != "dead"
    ][:2]
    assert len(escort_refs) == 2

    route_ref = "route.luoyang.changan"
    geography = load("game/data/martial-world/geography.json")
    route = next(row for row in geography["routes"] if row.get("id") == route_ref)
    source_place = str(route["from"]); destination_place = str(route["to"])
    source_region = str(geography["places"][source_place]["climate_profile"])
    destination_region = str(geography["places"][destination_place]["climate_profile"])
    movement_ref = "escort:integration:test"
    movement = {
        "movement_ref": movement_ref,
        "movement_kind": "escort_contract",
        "contract_ref": movement_ref,
        "objective_kind": "escort_shipment",
        "route_ref": route_ref,
        "origin_place_ref": source_place,
        "destination_place_ref": destination_place,
        "item_ref": "food_ration_day",
        "quantity": 10,
        "cargo_value_cash": 1000,
        "beneficiary_ref": escort_fid,
        "participant_refs": escort_refs,
        "escort_refs": escort_refs,
        "protected_person_refs": [],
        "protected_people_count": 0,
        "started_at": "0061-09-13T21:15:00",
        "last_progress_at": "0061-09-13T21:15:00",
        "elapsed_seconds": 0,
        "required_seconds": 100 * 3600,
        "known_escort_count": len(escort_refs),
        "status": "active",
        "repelled_outlaw_refs": [],
    }
    overlay = {
        "state/martial-world/route-operations.json": {
            "schema": "jianghu-route-operations-state-1.0",
            "movements": {movement_ref: movement},
            "contacts": {},
        }
    }

    def read_json(rel):
        return copy.deepcopy(overlay[rel] if rel in overlay else load(rel))

    # The active route owner itself is the personnel commitment. There is no
    # second persisted commitment record to synchronize.
    derived = derived_commitment_state(read_json)
    for ref in escort_refs:
        assert ref in derived["person_index"]

    start = datetime(61, 9, 13, 21, 15)
    schedule = sync_route_activity(
        initial_schedule(start=start, faction_ids=[], region_ids=[], route_ids=[]),
        active_route_ids=[route_ref], now=start,
    )
    at = datetime.fromisoformat(schedule["recurring"]["route_daily"]["next_due_at"])
    events = due_events(schedule, after=start, through=at)

    # Force the *opportunity/decision* portion only. Outcome remains delegated
    # to the exact resolver, whose write-back wiring is what this regression
    # protects. Exact-combat mechanics have their own deterministic tests.
    monkeypatch.setattr(route_frontier, "route_exposure", lambda **_kw: {
        "threat_milli": 1000, "witness_milli": 1000, "patrol_suppression_milli": 0,
    })
    monkeypatch.setattr(route_frontier, "stable_permille", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(route_frontier, "interception_decision", lambda **_kwargs: {"attack": True, "intent": "rob_cargo"})
    call = {}

    def fake_exact_combat(**kwargs):
        call.update(kwargs)
        people_after = copy.deepcopy(kwargs["people"])
        target_ref = str(kwargs["side_a_refs"][0])
        target = people_after[target_ref]
        health = copy.deepcopy(target.get("health", {}))
        injuries = list(health.get("injuries", []))
        injuries.append(wound_from_contact(
            structure_ref="left_forearm", cut=30, pierce=0, blunt=20,
            penetration=0, created_at=str(kwargs["started_at"]),
        ))
        health["injuries"] = injuries
        health["status"] = "injured"
        target["health"] = health
        return {
            "people_after": people_after,
            "equipment_ledger_after": copy.deepcopy(kwargs["equipment_ledger"]),
            "winner_side": None,
            "resolved": False,
            "exchanges": 1,
        }

    monkeypatch.setattr(route_frontier, "simulate_exact_combat", fake_exact_combat)
    result = settle_martial_world_frontier(
        read_json=read_json, schedule=schedule, events=events, at=at,
    )

    assert call["side_a_refs"] == escort_refs
    assert call["side_b_refs"]
    assert all(str(ref).startswith("mw.person.") for ref in call["side_b_refs"])
    assert call["objective"]["movement_ref"] == movement_ref
    assert call["max_exchanges"] == 4

    stored = result["writes"][f"state/martial-world/people/{escort_fid}.json"]
    hydrated = hydrate_roster_state(stored, faction=escort_faction)
    target = next(person for person in hydrated["people"] if person["person_id"] == escort_refs[0])
    assert target["health"]["status"] == "injured"
    assert target["health"]["injuries"]

    # The attackers do not teleport home or become immediately reusable merely
    # because this bounded interception failed to take the convoy. Their exact
    # surviving bodies own a real return movement until they reach home.
    route_after = result["writes"]["state/martial-world/route-operations.json"]
    retreats = [
        row for ref, row in route_after["movements"].items()
        if ref != movement_ref
        and row.get("movement_kind") == "raid_return"
        and row.get("quantity") == 0
        and route_controlling_refs(row)
    ]
    assert retreats
    retreat = retreats[0]
    assert set(route_controlling_refs(retreat)) == set(call["side_b_refs"])
    after_overlay = dict(overlay)
    after_overlay.update({str(k): copy.deepcopy(v) for k, v in result["writes"].items()})
    after_commitments = derived_commitment_state(
        lambda rel: copy.deepcopy(after_overlay[rel] if rel in after_overlay else load(rel))
    )
    assert all(ref in after_commitments["person_index"] for ref in call["side_b_refs"])


def test_autonomous_escort_review_persists_real_party_and_derived_personnel_claim(monkeypatch):
    """An NPC escort acceptance must create one real route owner with exact people.

    The route movement, not a parallel reservation save, owns those escorts.
    Derived availability must therefore see the same people as unavailable
    immediately after the frontier commits.
    """
    import copy
    import json
    from datetime import datetime, timedelta
    from pathlib import Path

    import shinobi_runtime.martial_world.warfare as warfare
    import shinobi_runtime.martial_world.autonomy_frontier as autonomy_frontier
    from shinobi_runtime.martial_world.commitments import derived_commitment_state
    from shinobi_runtime.martial_world.contracts import create_contract_owner
    from shinobi_runtime.martial_world.faction_state import hydrate_faction_state
    from shinobi_runtime.martial_world.person_state import hydrate_roster_state
    from shinobi_runtime.martial_world.scheduler import initial_schedule
    from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier

    root = Path(__file__).resolve().parents[2]

    def load(rel):
        return json.loads((root / rel).read_text())

    fid = "long_road_escorts"
    faction = hydrate_faction_state(load(f"state/martial-world/factions/{fid}.json"))
    assert faction["headquarters"] == "luoyang"
    roster = hydrate_roster_state(load(f"state/martial-world/people/{fid}.json"), faction=faction)

    route_ref = "route.luoyang.changan"
    geography = load("game/data/martial-world/geography.json")
    route = next(row for row in geography["routes"] if row.get("id") == route_ref)
    source_region = str(geography["places"]["luoyang"]["climate_profile"])
    destination_region = str(geography["places"]["changan"]["climate_profile"])
    start = datetime(61, 9, 14, 10, 15)
    objective = {
        "kind": "escort_shipment",
        "route_ref": route_ref,
        "source_place_ref": "luoyang",
        "destination_place_ref": "changan",
        "item_ref": "food_ration_day",
        "quantity": 1,
        "cargo_value_cash": 27,
        "minimum_escort_count": 2,
    }
    from shinobi_runtime.martial_world.escort import hydrate_contract_escort_objective

    expected_escort_count = hydrate_contract_escort_objective(
        objective, geography=geography, travel=load("game/data/martial-world/travel.json"),
    )["minimum_escort_count"]
    contract = create_contract_owner(
        contract_type="escort",
        issuer_ref=f"market:{source_region}",
        beneficiary_ref=None,
        offered_at=start.isoformat(),
        expires_at=(start + timedelta(days=30)).isoformat(),
        reward_cash=100,
        funding_cash=100,
        objective=objective,
        source_ref="test.autonomous_escort",
    )
    cid = str(contract["contract_id"])

    contracts = copy.deepcopy(load("state/martial-world/contracts/index.json"))
    contracts.setdefault("active", {})[cid] = contract
    route_ops = copy.deepcopy(load("state/martial-world/route-operations.json"))
    route_ops.setdefault("movements", {}).pop(cid, None)
    overlay = {
        "state/martial-world/contracts/index.json": contracts,
        "state/martial-world/route-operations.json": route_ops,
    }

    def read_json(rel):
        return copy.deepcopy(overlay[rel] if rel in overlay else load(rel))

    # Isolate contract evaluation. The rest of this test is the production
    # executor, including exact-person selection, reservation conflict checks,
    # training pause, cargo removal, and route-owner persistence.
    monkeypatch.setattr(
        autonomy_frontier,
        "autonomy_review",
        lambda *_args, **_kwargs: {
            "ordered_actions": ["evaluate_contracts"],
            "scored_actions": [{"action": "evaluate_contracts", "score": 1}],
        },
    )

    schedule = initial_schedule(start=start - timedelta(hours=1), faction_ids=[], region_ids=[], route_ids=[])
    event = {"kind": "faction_review", "owner_ref": fid, "event_id": "test:auto-escort"}
    result = settle_martial_world_frontier(
        read_json=read_json,
        schedule=schedule,
        events=[event],
        at=start,
    )

    contract_after = result["writes"]["state/martial-world/contracts/index.json"]["active"][cid]
    assert contract_after["status"] == "in_progress"
    participants = list(contract_after["participants"])
    assert len(participants) == expected_escort_count
    assert len(set(participants)) == expected_escort_count

    movement = result["writes"]["state/martial-world/route-operations.json"]["movements"][cid]
    assert movement["movement_kind"] == "escort_contract"
    assert route_controlling_refs(movement) == participants
    assert movement["participant_refs"] == participants
    assert movement["beneficiary_ref"] == fid

    roster_refs = {str(person["person_id"]) for person in roster["people"]}
    assert set(participants) <= roster_refs

    after_overlay = dict(overlay)
    after_overlay.update({str(k): copy.deepcopy(v) for k, v in result["writes"].items()})

    def read_after(rel):
        return copy.deepcopy(after_overlay[rel] if rel in after_overlay else load(rel))

    derived = derived_commitment_state(read_after)
    for ref in participants:
        claim_ref = derived["person_index"][ref]
        assert derived["commitments"][claim_ref]["activity_ref"] == cid


def test_successful_escort_delivers_then_rests_returns_home_before_people_are_free(monkeypatch):
    import copy
    import json
    from datetime import datetime, timedelta
    from pathlib import Path

    import shinobi_runtime.martial_world.warfare as warfare
    from shinobi_runtime.martial_world.commitments import derived_commitment_state
    from shinobi_runtime.martial_world.faction_state import hydrate_faction_state
    from shinobi_runtime.martial_world.person_state import hydrate_roster_state
    from shinobi_runtime.martial_world.scheduler import initial_schedule
    from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier

    root = Path(__file__).resolve().parents[2]

    def load(rel):
        return json.loads((root / rel).read_text())

    fid = "long_road_escorts"
    faction = hydrate_faction_state(load(f"state/martial-world/factions/{fid}.json"))
    roster = hydrate_roster_state(load(f"state/martial-world/people/{fid}.json"), faction=faction)
    blocked = set(derived_commitment_state(load).get("person_index", {}))
    escort_refs = [
        str(p["person_id"]) for p in roster["people"]
        if str(p.get("person_id")) not in blocked and (p.get("health", {}) or {}).get("status") != "dead"
    ][:2]
    assert len(escort_refs) == 2

    route_ref = "route.luoyang.changan"
    geography = load("game/data/martial-world/geography.json")
    route = next(row for row in geography["routes"] if row.get("id") == route_ref)
    source = str(route["from"]); destination = str(route["to"])
    start = datetime(61, 9, 15, 21, 15)
    cid = "contract:return-loop:test"
    contract = {
        "contract_type": "escort", "issuer_ref": "market:central_plain", "beneficiary_ref": fid,
        "status": "in_progress", "offered_at": (start - timedelta(days=2)).isoformat(),
        "expires_at": (start + timedelta(days=10)).isoformat(), "escrow_cash": 1000,
        "reward_cash": 1000, "objective": {"kind": "escort_shipment", "route_ref": route_ref,
        "source_place_ref": source, "destination_place_ref": destination, "item_ref": "food_ration_day", "quantity": 1},
        "source_ref": "test:return-loop", "participants": escort_refs,
    }
    contracts = copy.deepcopy(load("state/martial-world/contracts/index.json"))
    contracts.setdefault("active", {})[cid] = contract
    route_ops = copy.deepcopy(load("state/martial-world/route-operations.json"))
    route_ops.setdefault("movements", {})[cid] = {
        "movement_kind": "escort_contract", "contract_ref": cid, "route_ref": route_ref,
        "origin_place_ref": source, "destination_place_ref": destination,
        "item_ref": "food_ration_day", "quantity": 1, "beneficiary_ref": fid,
        "escort_refs": escort_refs, "protected_person_refs": [], "participant_refs": escort_refs,
        "started_at": (start - timedelta(days=1)).isoformat(), "last_progress_at": (start - timedelta(days=1)).isoformat(),
        "elapsed_seconds": 0, "required_seconds": 24 * 3600, "status": "active",
    }
    overlay = {
        "state/martial-world/contracts/index.json": contracts,
        "state/martial-world/route-operations.json": route_ops,
    }

    def reader(mapping):
        def read(rel):
            return copy.deepcopy(mapping[rel] if rel in mapping else load(rel))
        return read

    monkeypatch.setattr(route_frontier, "route_exposure", lambda **_kw: {
        "threat_milli": 0, "witness_milli": 0, "patrol_suppression_milli": 1000,
    })
    schedule = initial_schedule(start=start - timedelta(hours=1), faction_ids=[], region_ids=[], route_ids=[])
    event = {"kind": "route_activity_cycle", "owner_ref": route_ref, "event_id": "route:return:delivery"}
    first = settle_martial_world_frontier(read_json=reader(overlay), schedule=schedule, events=[event], at=start)
    after = dict(overlay); after.update({str(k): copy.deepcopy(v) for k, v in first["writes"].items()})
    assert cid not in after["state/martial-world/contracts/index.json"]["active"]
    returning = after["state/martial-world/route-operations.json"]["movements"][cid]
    assert returning["movement_kind"] == "escort_return"
    assert returning["participant_refs"] == escort_refs
    assert returning["destination_place_ref"] == source
    assert returning["status"] in {"lodging_rest", "field_rest"}
    assert route_ref in first["schedule_after"]["recurring"]["route_daily"]["owner_refs"]
    still_busy = derived_commitment_state(reader(after))["person_index"]
    assert set(escort_refs) <= set(still_busy)

    second_at = start + timedelta(days=1)
    second = settle_martial_world_frontier(
        read_json=reader(after), schedule=first["schedule_after"],
        events=[{"kind": "route_activity_cycle", "owner_ref": route_ref, "event_id": "route:return:rest"}], at=second_at,
    )
    after.update({str(k): copy.deepcopy(v) for k, v in second["writes"].items()})
    returning = after["state/martial-world/route-operations.json"]["movements"][cid]
    assert returning["status"] == "returning"
    assert route_ref in second["schedule_after"]["recurring"]["route_daily"]["owner_refs"]
    assert set(escort_refs) <= set(derived_commitment_state(reader(after))["person_index"])

    # The repaired return leg uses fresh weather/terrain timing rather than
    # reusing the old outbound 24-hour snapshot. Advance bounded route cycles
    # until the actual return plan completes.
    schedule_after = second["schedule_after"]
    cursor = second_at
    for day in range(1, 41):
        cursor += timedelta(days=1)
        current_route = str(after["state/martial-world/route-operations.json"]["movements"][cid].get("route_ref") or route_ref)
        step = settle_martial_world_frontier(
            read_json=reader(after), schedule=schedule_after,
            events=[{"kind": "route_activity_cycle", "owner_ref": current_route, "event_id": f"route:return:home:{day}"}], at=cursor,
        )
        after.update({str(k): copy.deepcopy(v) for k, v in step["writes"].items()})
        schedule_after = step["schedule_after"]
        if cid not in after["state/martial-world/route-operations.json"]["movements"]:
            break
    assert cid not in after["state/martial-world/route-operations.json"]["movements"]
    free = derived_commitment_state(reader(after))["person_index"]
    assert not (set(escort_refs) & set(free))
    home_roster = hydrate_roster_state(after[f"state/martial-world/people/{fid}.json"], faction=faction)
    by_ref = {p["person_id"]: p for p in home_roster["people"]}
    sites = load("game/data/martial-world/local-sites.json").get("sites", {})
    assert all(
        by_ref[ref]["location_ref"] == source
        or (sites.get(by_ref[ref]["location_ref"], {}) or {}).get("parent_place_ref") == source
        for ref in escort_refs
    )


def test_kidnapped_house_member_triggers_real_family_rescue_then_exact_base_combat(monkeypatch):
    """Information dispatches exact kin/fighters; only base combat frees the captive."""
    import copy
    import json
    from datetime import datetime, timedelta
    from pathlib import Path

    import shinobi_runtime.martial_world.warfare as warfare
    from shinobi_runtime.martial_world.commitments import derived_commitment_state
    from shinobi_runtime.martial_world.scheduler import initial_schedule
    from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier

    root = Path(__file__).resolve().parents[2]

    def load(rel):
        return json.loads((root / rel).read_text())

    captive_ref = "pc_wei_tang"
    captor_fid = "faction.red_willow_band"
    captor_site = "site.red_willow_band"
    responder_fid = "house_tang"
    start = datetime(61, 9, 14, 10, 15)
    custody_id = "custody:test:family-rescue"

    # The person has physically reached the outlaw base. The custody record is
    # the one independent owner of their restraint and holder institution.
    house_roster = copy.deepcopy(load("state/martial-world/people/house_tang.json"))
    for person in house_roster["people"]:
        if person.get("person_id") == captive_ref:
            person["location_ref"] = captor_site
            break
    custody = {
        "schema": "jianghu-custody-state-1.0",
        "records": [{
            "custody_id": custody_id,
            "person_ref": captive_ref,
            "captor_ref": "mw.person.faction.red_willow_band.0012",
            "holder_faction_ref": captor_fid,
            "status": "restrained",
            "location_ref": captor_site,
            "basis": "test_kidnapping",
            "started_at": "0061-09-14T00:00:00",
        }],
    }
    overlay = {
        "state/martial-world/custody.json": custody,
        "state/martial-world/people/house_tang.json": house_roster,
    }

    def reader(mapping):
        def read(rel):
            return copy.deepcopy(mapping[rel] if rel in mapping else load(rel))
        return read

    schedule = initial_schedule(start=start, faction_ids=[], region_ids=[], route_ids=[])
    response_event = {
        "event_id": f"custody_response:{custody_id}",
        "kind": "custody_response_due",
        "due_at": start.isoformat(),
        "owner_ref": custody_id,
        "person_ref": captive_ref,
        "holder_faction_ref": captor_fid,
        "responder_faction_ref": responder_fid,
        "information_source": "surviving_escort_report",
        "requires_player_decision": False,
    }
    first = settle_martial_world_frontier(
        read_json=reader(overlay), schedule=schedule, events=[response_event], at=start,
    )
    after = dict(overlay)
    after.update({str(k): copy.deepcopy(v) for k, v in first["writes"].items()})

    review = next(row for row in first["reviews"] if row.get("kind") == "custody_response_due")
    assert review["result"] == "rescue_dispatched"
    op_ref = review["operation_ref"]
    op = after["state/martial-world/deployments.json"]["deployments"][op_ref]
    assert op["operation_kind"] == "custody_rescue"
    assert op["target_site_ref"] == captor_site
    assert op["target_faction_ref"] == captor_fid
    assert captive_ref not in op["participant_refs"]
    assert op["participant_refs"][:2] == ["char.zhu", "char.ling"]
    for ref in op["participant_refs"]:
        assert ref in derived_commitment_state(reader(after))["person_index"]

    custody_after = after["state/martial-world/custody.json"]["records"][0]
    assert custody_after["informed_faction_refs"] == [responder_fid]
    # Rescue status is derived from the deployment owner, not duplicated on custody.
    assert "response_status" not in custody_after

    # The strategic decision may estimate defenses, but the actual rescue uses
    # the exact current defenders and exact combat. Force only the deterministic
    # combat outcome here so this regression tests orchestration/write-back.
    call = {}

    calls = []

    def fake_exact_combat(**kwargs):
        call.update(kwargs)
        calls.append(copy.deepcopy(kwargs))
        people_after = copy.deepcopy(kwargs["people"])
        # This regression tests orchestration rather than combat mechanics. Mark
        # the contacted defenders unable to continue so a successful frontage
        # can advance to the next real defenders instead of replaying forever.
        for ref in kwargs["side_b_refs"]:
            health = copy.deepcopy(people_after[ref].get("health", {}))
            health["status"] = "dead"
            people_after[ref]["health"] = health
        return {
            "people_after": people_after,
            "equipment_ledger_after": copy.deepcopy(kwargs["equipment_ledger"]),
            "winner_side": "side_a",
            "resolved": True,
            "exchanges": 3,
        }

    monkeypatch.setattr(warfare, "simulate_exact_combat", fake_exact_combat)
    # Every strategic operation now passes through its real departure frontier,
    # even when source and target share a settlement. That is where provisions,
    # warning/call-to-arms and other outbound obligations are settled.
    departure_event = next(
        row for row in first["schedule_after"].get("one_off", {}).values()
        if row.get("kind") == "faction_operation_departure" and row.get("owner_ref") == op_ref
    )
    departure_at = datetime.fromisoformat(departure_event["due_at"])
    departed = settle_martial_world_frontier(
        read_json=reader(after), schedule=first["schedule_after"], events=[departure_event], at=departure_at,
    )
    after.update({str(k): copy.deepcopy(v) for k, v in departed["writes"].items()})
    op = after["state/martial-world/deployments.json"]["deployments"][op_ref]
    arrival_event = next(
        row for row in departed["schedule_after"].get("one_off", {}).values()
        if row.get("kind") == "faction_operation_arrival" and row.get("owner_ref") == op_ref
    )
    arrival_at = datetime.fromisoformat(arrival_event["due_at"])
    second = settle_martial_world_frontier(
        read_json=reader(after), schedule=departed["schedule_after"], events=[arrival_event], at=arrival_at,
    )
    after.update({str(k): copy.deepcopy(v) for k, v in second["writes"].items()})

    assert calls
    assert set().union(*(set(row["side_a_refs"]) for row in calls)) <= set(op["participant_refs"])
    assert all(row["side_b_refs"] for row in calls)
    assert all(str(ref).startswith("mw.person.faction.red_willow_band.") for ref in call["side_b_refs"])
    assert call["zone_ref"] == captor_site
    assert call["objective"]["kind"] == "custody_rescue"
    assert call["environment"]["terrain"] == "urban"
    assert int(call["environment"]["movement_milli"]) > 0
    assert isinstance(call["environment"]["obstacles"], list)
    assert after["state/martial-world/custody.json"]["records"] == []

    returning = after["state/martial-world/deployments.json"]["deployments"][op_ref]
    # Rescue ends custody, but the runtime may not choose Wei's next journey.
    # The rescuers return under their existing purpose owner while Wei remains
    # physically at the rescue site for a hard player-facing travel decision.
    assert returning["status"] == "return_preparing"
    assert returning["pending_travel_direction"] == "return"
    assert captive_ref not in returning["participant_refs"]
    assert captive_ref not in derived_commitment_state(reader(after))["person_index"]
    decision = next(row for row in second["handoffs"] if row.get("kind") == "player_rescued_travel_decision")
    assert decision["person_ref"] == captive_ref
    assert decision["location_ref"] == captor_site
    assert decision["requires_player_decision"] is True

    return_departure_at = arrival_at + timedelta(seconds=1)
    third = settle_martial_world_frontier(
        read_json=reader(after), schedule=second["schedule_after"],
        events=[{
            "event_id": f"operation_departure:return:{op_ref}",
            "kind": "faction_operation_departure",
            "due_at": return_departure_at.isoformat(),
            "owner_ref": op_ref,
            "direction": "return",
            "arrival_event_kind": "faction_operation_return",
            "requires_player_decision": False,
        }],
        at=return_departure_at,
    )
    after.update({str(k): copy.deepcopy(v) for k, v in third["writes"].items()})
    traveling = after["state/martial-world/deployments.json"]["deployments"][op_ref]
    assert traveling["status"] == "traveling_return"
    assert captive_ref not in traveling["participant_refs"]
    # House Tang and Red Willow are separate sites in the same Luoyang
    # settlement, so this particular return legitimately has no road edge.
    assert traveling["source_place_ref"] == traveling["target_place_ref"] == "luoyang"
    assert "physical_movement_ref" not in traveling

    local_return_at = datetime.fromisoformat(traveling["arrival_at"])
    fourth = settle_martial_world_frontier(
        read_json=reader(after), schedule=third["schedule_after"],
        events=[{
            "event_id": f"operation_return:{op_ref}",
            "kind": "faction_operation_return",
            "due_at": local_return_at.isoformat(),
            "owner_ref": op_ref,
            "requires_player_decision": False,
        }],
        at=local_return_at,
    )
    after.update({str(k): copy.deepcopy(v) for k, v in fourth["writes"].items()})
    assert op_ref not in after["state/martial-world/deployments.json"]["deployments"]
    from shinobi_runtime.martial_world.faction_state import hydrate_faction_state
    from shinobi_runtime.martial_world.person_state import hydrate_roster_state
    returned_faction = hydrate_faction_state(
        after.get("state/martial-world/factions/house_tang.json", load("state/martial-world/factions/house_tang.json"))
    )
    returned_roster = hydrate_roster_state(after["state/martial-world/people/house_tang.json"], faction=returned_faction)
    player = next(person for person in returned_roster["people"] if person.get("person_id") == captive_ref)
    assert player.get("location_ref") == captor_site
