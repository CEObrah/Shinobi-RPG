import shinobi_runtime.martial_world.route_frontier as route_frontier
from shinobi_runtime.martial_world.route_frontier import bounded_raid_retreat_ref
from shinobi_runtime.martial_world.route_activity import route_controlling_refs
from shinobi_runtime.martial_world.escort_living_world import (
    best_route_observer,
    interception_decision,
    observed_escort_strength,
    route_interception_opportunity_permille,
)


def _person(ref: str, *, perception: int, intelligence: int, scouting: int = 0):
    return {
        "person_id": ref,
        "attributes": {"perception": perception, "intelligence": intelligence},
        "martial_skills": {"stealth_scouting": scouting},
        "strength": 40,
        "speed": 40,
        "dexterity": 40,
        "endurance": 40,
        "qi": 0,
        "qi_control": 0,
    }


def test_route_observation_uses_an_actual_best_available_witness():
    weak = _person("weak", perception=20, intelligence=20, scouting=10)
    scout = _person("scout", perception=80, intelligence=70, scouting=75)
    assert best_route_observer([weak, scout])["person_id"] == "scout"


def test_escort_strength_is_estimated_not_exact_character_sheet_knowledge():
    observer = _person("observer", perception=45, intelligence=40, scouting=35)
    escorts = [
        _person("escort.a", perception=50, intelligence=50),
        _person("escort.b", perception=55, intelligence=45),
    ]
    seen = observed_escort_strength(
        observer=observer, escorts=escorts, world_seed="test", observation_ref="route.test|day1",
    )
    assert seen["visible_escort_count"] == 2
    assert seen["estimated_combat_index"] > 0
    assert 0 < seen["confidence_milli"] < 1000


def test_ordinary_faction_needs_serious_grievance_before_route_interception():
    assert route_interception_opportunity_permille(
        attacker_faction_type="martial_house", route_threat_milli=1000,
        witness_milli=900, hostility=40, observer_confidence_milli=900,
    ) == 0
    assert route_interception_opportunity_permille(
        attacker_faction_type="martial_house", route_threat_milli=0,
        witness_milli=900, hostility=75, observer_confidence_milli=900,
    ) > 0


def test_criminal_value_motive_does_not_replace_strength_check():
    decision = interception_decision(
        attacker_faction_type="outlaw_faction", relation=None,
        own_available_martial=2, own_combat_index=30,
        observed_escort_count=8, observed_escort_combat_index=100,
        cargo_value_cash=500_000, ransom_value_cash=200_000,
        risk_tolerance=100, government_risk_milli=0,
        minimum_attack_advantage_milli=1100,
    )
    assert decision["attack"] is False


def test_public_route_intelligence_lists_known_road_operators_without_predicting_attacks():
    from shinobi_runtime.martial_world.route_intelligence import route_intelligence_brief

    brief = route_intelligence_brief("route.luoyang.changan")
    assert brief["source_place_ref"] in {"luoyang", "changan"}
    assert brief["destination_place_ref"] in {"luoyang", "changan"}
    assert brief["source_place_ref"] != brief["destination_place_ref"]
    assert brief["known_route_threats"]
    assert all(row["information_confidence"] == "established_public_presence" for row in brief["known_route_threats"])
    assert all("faction_ref" in row and "name" in row for row in brief["known_route_threats"])
    assert "will_attack" not in str(brief)
    assert brief["settlement_presence"]


def test_house_tang_local_estate_spur_does_not_invent_outlaw_risk():
    from shinobi_runtime.martial_world.route_intelligence import route_intelligence_brief

    brief = route_intelligence_brief("route.luoyang.rural_estates")
    assert brief["known_route_threats"] == []


def test_kidnapping_information_requires_a_real_report_or_public_witness():
    from shinobi_runtime.martial_world.captivity_lifecycle import kidnapping_report_delay_hours

    assert kidnapping_report_delay_hours(
        route_hours=72, surviving_reporters=0, public_witness_milli=200,
    ) is None
    survivor_delay = kidnapping_report_delay_hours(
        route_hours=72, surviving_reporters=1, public_witness_milli=0,
    )
    rumor_delay = kidnapping_report_delay_hours(
        route_hours=72, surviving_reporters=0, public_witness_milli=700,
    )
    assert survivor_delay is not None and rumor_delay is not None
    assert survivor_delay < rumor_delay


def test_player_close_kin_and_household_faction_are_derived_from_family_owner():
    import json
    from pathlib import Path
    from shinobi_runtime.martial_world.captivity_lifecycle import close_kin_refs, family_household_faction

    root = Path(__file__).resolve().parents[2]
    family = json.loads((root / "state/martial-world/family.json").read_text())
    kin = close_kin_refs(family, "pc_wei_tang")
    assert kin[:2] == ["char.zhu", "char.ling"]
    assert family_household_faction(family, "pc_wei_tang") == "house_tang"


def test_raid_return_reaches_real_hideout_before_captor_can_demand_ransom():
    import copy
    import json
    from datetime import datetime, timedelta
    from pathlib import Path

    from shinobi_runtime.martial_world.scheduler import initial_schedule
    from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier

    root = Path(__file__).resolve().parents[2]
    def load(rel):
        return json.loads((root / rel).read_text())
    def reader(mapping):
        def read(rel):
            return copy.deepcopy(mapping[rel] if rel in mapping else load(rel))
        return read

    captive_ref = "pc_wei_tang"
    captor_fid = "faction.red_willow_band"
    raider_ref = "mw.person.faction.red_willow_band.0001"
    custody_id = "custody:test:delayed-ransom"
    movement_ref = "seizure_return:test:delayed-ransom"
    route_ref = "route.luoyang.changan"
    at = datetime(61, 9, 14, 10, 15)
    started_at = at - timedelta(hours=1)
    overlay = {
        "state/martial-world/custody.json": {
            "schema": "jianghu-custody-state-1.0",
            "records": [{
                "custody_id": custody_id,
                "person_ref": captive_ref,
                "captor_ref": raider_ref,
                "holder_faction_ref": captor_fid,
                "status": "restrained",
                "location_ref": route_ref,
                "basis": "test_kidnapping",
                "started_at": at.isoformat(),
            }],
        },
        "state/martial-world/route-operations.json": {
            "schema": "jianghu-route-operations-state-1.0",
            "movements": {
                movement_ref: {
                    "movement_kind": "raid_return",
                    "route_ref": route_ref,
                    "destination_place_ref": "luoyang",
                    "beneficiary_ref": captor_fid,
                    "participant_refs": [raider_ref, captive_ref],
                    "raider_refs": [raider_ref],
                    "captive_refs": [captive_ref],
                    "item_ref": "",
                    "quantity": 0,
                    "started_at": started_at.isoformat(),
                    "last_progress_at": started_at.isoformat(),
                    "elapsed_seconds": 0,
                    "required_seconds": 3600,
                    "status": "active",
                }
            },
            "contacts": {},
        },
    }
    schedule = initial_schedule(start=started_at, faction_ids=[], region_ids=[], route_ids=[])
    first = settle_martial_world_frontier(
        read_json=reader(overlay), schedule=schedule,
        events=[{"kind": "route_activity_cycle", "owner_ref": route_ref, "event_id": "route:test:raid-return"}],
        at=at,
    )
    after = dict(overlay)
    after.update({str(k): copy.deepcopy(v) for k, v in first["writes"].items()})
    assert movement_ref not in after["state/martial-world/route-operations.json"]["movements"]
    custody = after["state/martial-world/custody.json"]["records"][0]
    assert custody["location_ref"] == "site.red_willow_band"
    assert "ransom_demand_cash" not in custody

    captor_events = [
        row for row in first["schedule_after"].get("one_off", {}).values()
        if row.get("kind") == "custody_captor_review" and row.get("owner_ref") == custody_id
    ]
    assert len(captor_events) == 1
    review_event = captor_events[0]
    review_at = datetime.fromisoformat(review_event["due_at"])
    assert review_at > at

    second = settle_martial_world_frontier(
        read_json=reader(after), schedule=first["schedule_after"], events=[review_event], at=review_at,
    )
    after.update({str(k): copy.deepcopy(v) for k, v in second["writes"].items()})
    demanded = after["state/martial-world/custody.json"]["records"][0]
    assert demanded["ransom_demand_cash"] > 0
    assert demanded["ransom_recipient_faction_ref"] == "house_tang"
    assert demanded["ransom_demanded_at"] == review_at.isoformat()
    message_events = [
        row for row in second["schedule_after"].get("one_off", {}).values()
        if row.get("kind") == "custody_response_due" and row.get("owner_ref") == custody_id
    ]
    assert len(message_events) == 1
    assert datetime.fromisoformat(message_events[0]["due_at"]) > review_at


def test_npc_ransom_moves_real_cash_then_returns_exact_captive_home():
    import copy
    import json
    from datetime import datetime, timedelta
    from pathlib import Path

    from shinobi_runtime.martial_world.commitments import derived_commitment_state
    from shinobi_runtime.martial_world.scheduler import initial_schedule
    from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier

    root = Path(__file__).resolve().parents[2]
    def load(rel):
        return json.loads((root / rel).read_text())
    def reader(mapping):
        def read(rel):
            return copy.deepcopy(mapping[rel] if rel in mapping else load(rel))
        return read

    responder_fid = "golden_river_escorts"
    holder_fid = "faction.red_willow_band"
    captive_ref = "mw.person.golden_river_escorts.0001"
    custody_id = "custody:test:npc-ransom"
    demand = 1_000
    at = datetime(61, 9, 14, 10, 15)

    responder = copy.deepcopy(load(f"state/martial-world/factions/{responder_fid}.json"))
    holder = copy.deepcopy(load(f"state/martial-world/factions/{holder_fid}.json"))
    roster = copy.deepcopy(load(f"state/martial-world/people/{responder_fid}.json"))
    # Make rescue genuinely unavailable rather than patching the decision. The
    # exact captive remains live, but every other field member is temporarily
    # retired from field work in this isolated fixture.
    for person in roster["people"]:
        if person.get("person_id") == captive_ref:
            person["location_ref"] = "site.red_willow_band"
        else:
            person["retired_from_field"] = True
    cash_before = int(responder["treasury_cash"])
    holder_cash_before = int(holder["treasury_cash"])
    custody = {
        "schema": "jianghu-custody-state-1.0",
        "records": [{
            "custody_id": custody_id,
            "person_ref": captive_ref,
            "captor_ref": "mw.person.faction.red_willow_band.0001",
            "holder_faction_ref": holder_fid,
            "status": "restrained",
            "location_ref": "site.red_willow_band",
            "basis": "test_kidnapping",
            "started_at": at.isoformat(),
            "ransom_demand_cash": demand,
            "ransom_demanded_at": at.isoformat(),
            "ransom_recipient_faction_ref": responder_fid,
        }],
    }
    overlay = {
        f"state/martial-world/factions/{responder_fid}.json": responder,
        f"state/martial-world/factions/{holder_fid}.json": holder,
        f"state/martial-world/people/{responder_fid}.json": roster,
        "state/martial-world/custody.json": custody,
    }
    schedule = initial_schedule(start=at, faction_ids=[], region_ids=[], route_ids=[])
    response_event = {
        "event_id": f"custody_response:{custody_id}:{responder_fid}",
        "kind": "custody_response_due", "due_at": at.isoformat(), "owner_ref": custody_id,
        "person_ref": captive_ref, "holder_faction_ref": holder_fid,
        "responder_faction_ref": responder_fid, "information_source": "ransom_message",
        "requires_player_decision": False,
    }
    first = settle_martial_world_frontier(
        read_json=reader(overlay), schedule=schedule, events=[response_event], at=at,
    )
    after = dict(overlay)
    after.update({str(k): copy.deepcopy(v) for k, v in first["writes"].items()})
    row = next(x for x in first["reviews"] if x.get("kind") == "custody_response_due")
    assert row["responses"][0]["result"] == "rescue_force_unavailable"
    assert row["responses"][0]["ransom_result"] == "ransom_paid"
    op_ref = row["responses"][0]["repatriation_operation_ref"]
    assert after["state/martial-world/custody.json"]["records"] == []
    assert int(after[f"state/martial-world/factions/{responder_fid}.json"]["treasury_cash"]) == cash_before - demand
    assert int(after[f"state/martial-world/factions/{holder_fid}.json"]["treasury_cash"]) == holder_cash_before + demand
    assert (
        int(after[f"state/martial-world/factions/{responder_fid}.json"]["treasury_cash"])
        + int(after[f"state/martial-world/factions/{holder_fid}.json"]["treasury_cash"])
        == cash_before + holder_cash_before
    )
    op = after["state/martial-world/deployments.json"]["deployments"][op_ref]
    assert op["operation_kind"] == "captive_repatriation"
    assert op["participant_refs"] == [captive_ref]
    assert captive_ref in derived_commitment_state(reader(after))["person_index"]

    departure_event = next(
        row for row in first["schedule_after"].get("one_off", {}).values()
        if row.get("kind") == "faction_operation_departure" and row.get("owner_ref") == op_ref
    )
    departure_at = datetime.fromisoformat(departure_event["due_at"])
    second = settle_martial_world_frontier(
        read_json=reader(after), schedule=first["schedule_after"],
        events=[departure_event], at=departure_at,
    )
    after.update({str(k): copy.deepcopy(v) for k, v in second["writes"].items()})
    traveling = after["state/martial-world/deployments.json"]["deployments"][op_ref]
    assert traveling["status"] == "traveling_return"
    movement_ref = traveling["physical_movement_ref"]
    assert movement_ref in after["state/martial-world/route-operations.json"]["movements"]
    assert captive_ref in derived_commitment_state(reader(after))["person_index"]

    schedule_after = second["schedule_after"]
    # Advance each exact physical route segment to its deterministic completion
    # boundary. Multi-edge journeys may change route owner between iterations.
    for ordinal in range(12):
        movements = after["state/martial-world/route-operations.json"]["movements"]
        if movement_ref not in movements:
            break
        movement = movements[movement_ref]
        last_at = datetime.fromisoformat(movement["last_progress_at"])
        remaining = max(1, int(movement.get("required_seconds", 1)) - int(movement.get("elapsed_seconds", 0)))
        route_at = last_at + timedelta(seconds=remaining)
        route_event = {
            "event_id": f"test:repatriation-route:{ordinal}:{movement['route_ref']}",
            "kind": "route_activity_cycle", "due_at": route_at.isoformat(),
            "owner_ref": movement["route_ref"], "requires_player_decision": False,
        }
        advanced = settle_martial_world_frontier(
            read_json=reader(after), schedule=schedule_after, events=[route_event], at=route_at,
        )
        after.update({str(k): copy.deepcopy(v) for k, v in advanced["writes"].items()})
        schedule_after = advanced["schedule_after"]
    else:
        raise AssertionError("repatriation route failed to complete in bounded segments")

    assert movement_ref not in after["state/martial-world/route-operations.json"]["movements"]
    assert op_ref not in after["state/martial-world/deployments.json"]["deployments"]
    assert captive_ref not in derived_commitment_state(reader(after))["person_index"]
    returned = next(p for p in after[f"state/martial-world/people/{responder_fid}.json"]["people"] if p.get("person_id") == captive_ref)
    assert returned.get("location_ref") in {None, "site.golden_river_escorts"}


def test_returning_raiders_can_be_intercepted_and_custody_follows_exact_combat(monkeypatch):
    """Loot/captives do not gain safe passage after the first robbery.

    A second geographically valid predator must fight the actual living raiders.
    If it wins, the old return owner closes, cargo remains physical in the new
    return party, and the existing custody record changes holder rather than
    duplicating or auto-ransoming the captive.
    """
    import copy
    import json
    from datetime import datetime
    from pathlib import Path

    from shinobi_runtime.martial_world.scheduler import initial_schedule
    from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier

    root = Path(__file__).resolve().parents[2]

    def load(rel):
        return json.loads((root / rel).read_text())

    def reader(mapping):
        def read(rel):
            return copy.deepcopy(mapping[rel] if rel in mapping else load(rel))
        return read

    route_ref = "route.luoyang.changan"
    holder_fid = "faction.red_willow_band"
    raider_ref = "mw.person.faction.red_willow_band.0001"
    wounded_ref = "mw.person.faction.red_willow_band.0002"
    captive_ref = "mw.person.golden_river_escorts.0001"
    custody_id = "custody:test:reinterception"
    movement_ref = "seizure_return:test:reinterception"
    op_ref = "operation:test:linked-raid-return"
    # Keep the synthetic encounter at or after the current institutional
    # training frontier so the fixture tests route interception rather than
    # asking the live roster to move its training epoch backward.
    at = datetime(61, 9, 28, 10, 15)
    deployments = copy.deepcopy(load("state/martial-world/deployments.json"))
    deployments["deployments"] = {
        op_ref: {
            "faction_ref": holder_fid, "target_faction_ref": "golden_river_escorts",
            "operation_kind": "faction_raid", "operation_intent": "kidnapping",
            "targeting_intent": "disable", "participant_refs": [raider_ref, wounded_ref, captive_ref],
            "captive_refs": [captive_ref], "return_escort_refs": [raider_ref, wounded_ref],
            "issued_equipment": {raider_ref: {"weapon_jian": 1}, wounded_ref: {"weapon_jian": 1}},
            "issued_equipment_baseline": {raider_ref: {"weapon_jian": 0}, wounded_ref: {"weapon_jian": 0}},
            "issued_equipment_claim_baseline": {raider_ref: {"weapon_jian": 0}, wounded_ref: {"weapon_jian": 0}},
            "source_place_ref": "luoyang", "target_place_ref": "changan",
            "status": "traveling_return", "physical_movement_ref": movement_ref,
            "started_at": at.isoformat(),
        }
    }

    holder_roster = copy.deepcopy(load(f"state/martial-world/people/{holder_fid}.json"))
    for person in holder_roster["people"]:
        if person.get("person_id") == raider_ref:
            person["personal_cash"] = 17
        elif person.get("person_id") == wounded_ref:
            person["personal_cash"] = 5
    captive_roster = copy.deepcopy(load("state/martial-world/people/golden_river_escorts.json"))
    for person in captive_roster["people"]:
        if person.get("person_id") == captive_ref:
            person["personal_cash"] = 0
            break

    overlay = {
        f"state/martial-world/people/{holder_fid}.json": holder_roster,
        "state/martial-world/people/golden_river_escorts.json": captive_roster,
        "state/martial-world/deployments.json": deployments,
        "state/martial-world/custody.json": {
            "schema": "jianghu-custody-state-1.0",
            "records": [{
                "custody_id": custody_id,
                "person_ref": captive_ref,
                "captor_ref": raider_ref,
                "holder_faction_ref": holder_fid,
                "status": "restrained",
                "location_ref": route_ref,
                "basis": "test_kidnapping",
                "started_at": at.isoformat(),
            }],
        },
        "state/martial-world/equipment-ledger.json": {
            "schema": "jianghu-equipment-ledger-1.0",
            "person_loadouts": {
                raider_ref: {"items": {"weapon_jian": 1}},
                wounded_ref: {"items": {"weapon_jian": 1}},
            },
        },
        "state/martial-world/route-operations.json": {
            "schema": "jianghu-route-operations-state-1.0",
            "movements": {
                movement_ref: {
                    "movement_kind": "raid_return",
                    "purpose_ref": op_ref, "operation_ref": op_ref,
                    "route_ref": route_ref,
                    "origin_place_ref": "changan",
                    "destination_place_ref": "luoyang",
                    "beneficiary_ref": holder_fid,
                    "participant_refs": [raider_ref, wounded_ref, captive_ref],
                    "escort_refs": [raider_ref, wounded_ref],
                    "protected_person_refs": [captive_ref],
                    "raider_refs": [raider_ref, wounded_ref],
                    "captive_refs": [captive_ref],
                    "item_ref": "food_ration_day",
                    "quantity": 20,
                    "started_at": at.isoformat(),
                    "last_progress_at": at.isoformat(),
                    "elapsed_seconds": 0,
                    "required_seconds": 72 * 3600,
                    "status": "active",
                }
            },
            "contacts": {},
        },
    }

    monkeypatch.setattr(route_frontier, "route_exposure", lambda **_kw: {
        "threat_milli": 1400, "witness_milli": 1000, "patrol_suppression_milli": 0,
    })
    monkeypatch.setattr(route_frontier, "stable_permille", lambda *_a, **_kw: 0)
    monkeypatch.setattr(route_frontier, "interception_decision", lambda **_kw: {
        "attack": True, "intent": "rob_cargo", "advantage_milli": 2000,
        "required_advantage_milli": 1000, "motive_score": 999,
    })
    call = {}

    def attacker_wins(**kwargs):
        call.update(kwargs)
        people_after = copy.deepcopy(kwargs["people"])
        wounded = people_after[wounded_ref]
        health = copy.deepcopy(wounded.get("health", {})) if isinstance(wounded.get("health"), dict) else {}
        health["status"] = "incapacitated"
        health["consciousness"] = 0
        wounded["health"] = health
        return {
            "people_after": people_after,
            "equipment_ledger_after": copy.deepcopy(kwargs["equipment_ledger"]),
            "winner_side": "side_b", "resolved": True, "exchanges": 1,
        }

    monkeypatch.setattr(route_frontier, "simulate_exact_combat", attacker_wins)
    schedule = initial_schedule(start=at, faction_ids=[], region_ids=[], route_ids=[])
    result = settle_martial_world_frontier(
        read_json=reader(overlay), schedule=schedule,
        events=[{"kind": "route_activity_cycle", "owner_ref": route_ref, "event_id": "route:test:reinterception"}],
        at=at,
    )
    after = dict(overlay)
    after.update({str(k): copy.deepcopy(v) for k, v in result["writes"].items()})

    # Restrained people are physically present but are not made combatants for
    # their captors merely because they share the route party.
    assert call["side_a_refs"] == [raider_ref, wounded_ref]
    assert captive_ref not in call["side_a_refs"]
    assert call["side_b_refs"]

    movements = after["state/martial-world/route-operations.json"]["movements"]
    assert movement_ref not in movements
    successor = next(row for ref, row in movements.items() if str(ref).startswith("seizure_return:"))
    new_holder = successor["beneficiary_ref"]
    assert new_holder != holder_fid
    assert successor["quantity"] == 20
    assert successor["cash_quantity"] == 22
    new_holder_path = f"state/martial-world/factions/{new_holder}.json"
    before_new_holder = load(new_holder_path)
    after_new_holder = after.get(new_holder_path, before_new_holder)
    assert int(after_new_holder.get("treasury_cash", 0)) == int(before_new_holder.get("treasury_cash", 0))
    holder_after = after[f"state/martial-world/people/{holder_fid}.json"]["people"]
    assert next(p for p in holder_after if p.get("person_id") == raider_ref).get("personal_cash", 0) == 0
    assert next(p for p in holder_after if p.get("person_id") == wounded_ref).get("personal_cash", 0) == 0
    assert captive_ref in successor["participant_refs"]
    assert captive_ref not in route_controlling_refs(successor)

    # The original strategic purpose loses the objective but not its surviving
    # people/equipment return lifecycle. It is re-linked to a cargo-free retreat
    # rather than remaining on the dead movement or freeing the raider on-road.
    linked = after["state/martial-world/deployments.json"]["deployments"][op_ref]
    assert linked["participant_refs"] == [raider_ref, wounded_ref]
    assert set(linked["issued_equipment"]) == {raider_ref, wounded_ref}
    assert "captive_refs" not in linked
    retreat_ref = linked["physical_movement_ref"]
    assert retreat_ref != movement_ref
    retreat = movements[retreat_ref]
    assert retreat["movement_kind"] == "faction_operation_travel"
    assert retreat["purpose_ref"] == op_ref
    assert retreat["participant_refs"] == [raider_ref, wounded_ref]
    assert route_controlling_refs(retreat) == [raider_ref]
    assert retreat.get("quantity", 0) == 0

    custody = after["state/martial-world/custody.json"]["records"]
    assert len(custody) == 1
    assert custody[0]["custody_id"] == custody_id
    assert custody[0]["holder_faction_ref"] == new_holder
    assert custody[0]["captor_ref"] in route_controlling_refs(successor)
    assert "ransom_demand_cash" not in custody[0]


def test_raid_retreat_identity_is_bounded_even_after_nested_interceptions():
    parent = "merchant_trade:root"
    refs = []
    for day in range(1, 20):
        ref = bounded_raid_retreat_ref(
            parent, f"faction.outlaw.{day}", f"0061-12-{day:02d}T21:15:00",
        )
        refs.append(ref)
        assert len(ref) <= 48
        assert ref == bounded_raid_retreat_ref(
            parent, f"faction.outlaw.{day}", f"0061-12-{day:02d}T21:15:00",
        )
        parent = ref
    assert len(set(refs)) == len(refs)
    assert all(ref.count("raid_retreat:") == 1 for ref in refs)


def test_nonambulatory_captive_does_not_pay_ransom_until_self_travel_is_possible():
    import copy
    import json
    from datetime import datetime
    from pathlib import Path

    from shinobi_runtime.martial_world.scheduler import initial_schedule
    from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier

    root = Path(__file__).resolve().parents[2]
    def load(rel):
        return json.loads((root / rel).read_text())
    def reader(mapping):
        def read(rel):
            return copy.deepcopy(mapping[rel] if rel in mapping else load(rel))
        return read

    responder_fid = "golden_river_escorts"
    holder_fid = "faction.red_willow_band"
    captive_ref = "mw.person.golden_river_escorts.0001"
    custody_id = "custody:test:nonambulatory-ransom"
    demand = 1_000
    at = datetime(61, 9, 14, 10, 15)

    responder = copy.deepcopy(load(f"state/martial-world/factions/{responder_fid}.json"))
    holder = copy.deepcopy(load(f"state/martial-world/factions/{holder_fid}.json"))
    roster = copy.deepcopy(load(f"state/martial-world/people/{responder_fid}.json"))
    for person in roster["people"]:
        if person.get("person_id") == captive_ref:
            person["location_ref"] = "site.red_willow_band"
            person["health"] = {"status": "unconscious", "consciousness": 0}
        else:
            person["retired_from_field"] = True
    responder_cash = int(responder["treasury_cash"])
    holder_cash = int(holder["treasury_cash"])
    deployments = copy.deepcopy(load("state/martial-world/deployments.json"))
    deployments["deployments"] = {}
    custody = {
        "schema": "jianghu-custody-state-1.0",
        "records": [{
            "custody_id": custody_id,
            "person_ref": captive_ref,
            "captor_ref": "mw.person.faction.red_willow_band.0001",
            "holder_faction_ref": holder_fid,
            "status": "restrained",
            "location_ref": "site.red_willow_band",
            "basis": "test_kidnapping",
            "started_at": at.isoformat(),
            "ransom_demand_cash": demand,
            "ransom_demanded_at": at.isoformat(),
            "ransom_recipient_faction_ref": responder_fid,
        }],
    }
    overlay = {
        f"state/martial-world/factions/{responder_fid}.json": responder,
        f"state/martial-world/factions/{holder_fid}.json": holder,
        f"state/martial-world/people/{responder_fid}.json": roster,
        "state/martial-world/custody.json": custody,
        "state/martial-world/deployments.json": deployments,
    }
    schedule = initial_schedule(start=at, faction_ids=[], region_ids=[], route_ids=[])
    result = settle_martial_world_frontier(
        read_json=reader(overlay), schedule=schedule,
        events=[{
            "event_id": f"custody_response:{custody_id}:{responder_fid}",
            "kind": "custody_response_due", "due_at": at.isoformat(), "owner_ref": custody_id,
            "person_ref": captive_ref, "holder_faction_ref": holder_fid,
            "responder_faction_ref": responder_fid, "information_source": "ransom_message",
            "requires_player_decision": False,
        }],
        at=at,
    )
    after = dict(overlay)
    after.update({str(k): copy.deepcopy(v) for k, v in result["writes"].items()})
    row = next(x for x in result["reviews"] if x.get("kind") == "custody_response_due")
    response = row["responses"][0]
    assert response["result"] == "rescue_force_unavailable"
    assert response["ransom_result"] == "ransom_waiting_for_recovery"
    assert int(after[f"state/martial-world/factions/{responder_fid}.json"]["treasury_cash"]) == responder_cash
    assert int(after[f"state/martial-world/factions/{holder_fid}.json"]["treasury_cash"]) == holder_cash
    assert len(after["state/martial-world/custody.json"]["records"]) == 1
    assert after["state/martial-world/deployments.json"]["deployments"] == {}
    rechecks = [
        event for event in result["schedule_after"].get("one_off", {}).values()
        if event.get("kind") == "custody_response_due" and event.get("owner_ref") == custody_id
    ]
    assert len(rechecks) == 1
    assert rechecks[0]["information_source"] == "ransom_recovery_recheck"


def test_civilian_restraint_raises_real_advantage_required_for_convoy_attack():
    common = dict(
        attacker_faction_type='martial_house', relation={'hostility': 75, 'trust': 0},
        own_available_martial=4, own_combat_index=50,
        observed_escort_count=4, observed_escort_combat_index=60,
        cargo_value_cash=0, ransom_value_cash=0,
        risk_tolerance=50, government_risk_milli=0,
        minimum_attack_advantage_milli=1100,
    )
    unrestrained = interception_decision(**common, civilian_restraint=0)
    restrained = interception_decision(**common, civilian_restraint=100)

    assert unrestrained['attack'] is True
    assert restrained['attack'] is False
    assert restrained['expected_cost_score'] > unrestrained['expected_cost_score']
    assert restrained['civilian_restraint'] == 100
