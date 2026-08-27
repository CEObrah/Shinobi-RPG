import json
import shutil
from datetime import datetime
from pathlib import Path

from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.martial_world.aggregate_transport import (
    active_reserved_capacity,
    civilian_transport_capacity,
    faction_transport_capacity,
)
from shinobi_runtime.martial_world.calendar_modifiers import (
    escort_demand_milli,
    government_attention_milli,
    trade_capital_milli,
)
from shinobi_runtime.martial_world.calendar_participation import active_event_opportunities
from shinobi_runtime.martial_world.events import calendar_events_between
from shinobi_runtime.martial_world.faction_politics import faction_camp
from shinobi_runtime.martial_world.faction_state import (
    allows_ordinary_membership_exit,
    faction_membership_tenure,
    resolved_faction_type,
)
from shinobi_runtime.martial_world.government_finance import fund_bounty_escrow
from shinobi_runtime.martial_world.tournaments import event_profile, tournament_person_eligible
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


def load(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def test_canonical_transport_is_pooled_reserved_and_not_individual_animals():
    route_state = load("state/martial-world/route-operations.json")
    movements = route_state.get("movements", {})
    assert len(movements) == 20
    for row in movements.values():
        assert int(row.get("required_seconds", 0)) > 0
        assert "required_hours" not in row and "elapsed_hours" not in row
        assert "wagon_count" not in row and "draft_animal_count" not in row
        reservation = row.get("transport_reservation")
        assert isinstance(reservation, dict)
        assert reservation.get("provider_kind") in {"faction_pool", "civilian_logistics"}
        assert isinstance(reservation.get("provider_ref"), str) and reservation["provider_ref"]

    inventories = sorted((ROOT / "state/martial-world/inventories").glob("*.json"))
    assert len(inventories) == 240
    for path in inventories:
        inventory = json.loads(path.read_text(encoding="utf-8"))
        assert "transport_assets" not in inventory
        capacity = faction_transport_capacity(inventory)
        assert capacity["rider_slots"] >= 0 and capacity["freight_capacity_kg"] >= 0
        faction_ref = str(inventory.get("faction_ref") or "")
        used = active_reserved_capacity(route_state, provider_kind="faction_pool", provider_ref=faction_ref)
        assert used["rider_slots"] <= capacity["rider_slots"]
        assert used["freight_capacity_kg"] <= capacity["freight_capacity_kg"]

    populations = load("state/martial-world/civilian-populations.json").get("places", {})
    providers = {
        str(row.get("transport_reservation", {}).get("provider_ref"))
        for row in movements.values()
        if isinstance(row, dict)
        and isinstance(row.get("transport_reservation"), dict)
        and row["transport_reservation"].get("provider_kind") == "civilian_logistics"
    }
    for place_ref in providers:
        pool = populations.get(place_ref) or {}
        cap = civilian_transport_capacity(int(pool.get("current_population", 0)))
        used = active_reserved_capacity(route_state, provider_kind="civilian_logistics", provider_ref=place_ref)
        assert used["freight_capacity_kg"] <= cap["freight_capacity_kg"]
        assert used["crew_capacity"] <= cap["crew_capacity"]


def test_canonical_raids_are_remustered_from_unique_real_people():
    deployments = load("state/martial-world/deployments.json").get("deployments", {})
    raids = [
        row for row in deployments.values()
        if isinstance(row, dict)
        and row.get("operation_kind") == "faction_raid"
        and row.get("status") not in {"completed", "cancelled", "failed"}
    ]
    assert raids
    assert all(len(row.get("participant_refs", [])) >= 3 for row in raids)
    assert all(row.get("mobilization_basis") == "stealth_coordination" for row in raids)
    participant_refs = [ref for row in raids for ref in row.get("participant_refs", [])]
    assert len(participant_refs) == len(set(participant_refs))


def test_dead_persistent_people_no_longer_trap_personal_cash():
    dead = []
    for path in sorted((ROOT / "state/martial-world/people").glob("*.json")):
        roster = json.loads(path.read_text(encoding="utf-8"))
        for person in roster.get("people", []):
            if isinstance(person, dict) and person.get("health", {}).get("status") == "dead":
                dead.append(person)
    independent = load("state/martial-world/independent-people.json")
    for person in independent.get("people", []):
        if isinstance(person, dict) and person.get("health", {}).get("status") == "dead":
            dead.append(person)
    assert len(dead) == 12
    assert sum(max(0, int(person.get("personal_cash", 0))) for person in dead) == 0


def test_dynamic_faction_identity_resolves_from_current_owner_first():
    faction = {
        "faction_id": "faction.runtime_created",
        "type": "outlaw_faction",
        "membership_tenure": "life_service",
        "jianghu_camp": "outlaw",
    }
    assert resolved_faction_type(faction) == "outlaw_faction"
    assert faction_membership_tenure(faction["faction_id"], faction) == "life_service"
    assert allows_ordinary_membership_exit(faction["faction_id"], faction) is False
    assert faction_camp(faction["faction_id"], faction) == "outlaw"


def test_government_bounty_escrow_is_real_conserved_cash():
    market = {"cash_pool": 1000}
    existing = {"bounty_escrow_cash": 300}
    funded = fund_bounty_escrow(market, existing_warrant=existing, desired_cash=2000)
    assert funded["escrow_cash"] == 1300
    assert funded["escrow_added_cash"] == 1000
    assert funded["market_after"]["cash_pool"] == 0
    assert market["cash_pool"] + existing["bounty_escrow_cash"] == funded["market_after"]["cash_pool"] + funded["escrow_cash"]


def test_midyear_junior_tournament_uses_real_shared_tournament_lifecycle():
    world = load("game/data/martial-world/world-events.json")
    spec = world["calendar_events"]["midyear_junior_tournament"]
    assert spec.get("formats") == ["individual"]
    assert spec.get("registration_closes_days_before") == 3
    assert spec.get("registration_opens_days_before_close") == 60
    assert spec.get("convergence_days_before") == 1
    assert world["host_cycles"].get("midyear_junior_tournament")
    profile = event_profile("midyear_junior_tournament")
    assert int(profile["entry_fee_cash"]) > 0
    assert int(profile["max_exchanges_per_match"]) > 0
    junior = {"birth_year": 45, "membership_grade": "junior", "health": {"status": "ready", "consciousness": 100}}
    full = {"birth_year": 45, "membership_grade": "full", "health": {"status": "ready", "consciousness": 100}}
    child = {"birth_year": 49, "membership_grade": "junior", "health": {"status": "ready", "consciousness": 100}}
    assert tournament_person_eligible("midyear_junior_tournament", junior, year=61)
    assert not tournament_person_eligible("midyear_junior_tournament", full, year=61)
    assert not tournament_person_eligible("midyear_junior_tournament", child, year=61)
    event = next(row for row in calendar_events_between(datetime(61, 6, 15).date(), datetime(61, 6, 15).date()) if row["event_id"] == "midyear_junior_tournament")
    assert event.get("registration_opens_on") and event.get("registration_closes_on") and event.get("host_place_id")


def test_named_calendar_events_have_real_effect_or_real_interaction_surface():
    events = load("game/data/martial-world/world-events.json")["calendar_events"]
    for event_id, row in events.items():
        assert row.get("simulation_mode") != "narrative_only", event_id
        assert not row.get("flavor_only", False), event_id
        participation = row.get("participation")
        assert isinstance(participation, dict), event_id
        assert isinstance(participation.get("player_modes"), list), event_id
        assert row.get("simulation_effect") or row.get("formats") or participation.get("player_modes"), event_id

    assert trade_capital_milli(datetime(61, 3, 12), review_window_days=30) == 1500
    assert government_attention_milli(datetime(61, 8, 9), review_window_days=30) == 1250
    assert escort_demand_milli(datetime(61, 4, 20), review_window_days=30) == 1400


def test_active_trade_fair_projects_real_local_interaction_surface():
    sites = load("game/data/martial-world/local-sites.json")["sites"]
    market = next(ref for ref, row in sites.items() if row.get("parent_place_ref") == "luoyang" and row.get("site_type") == "market")
    rows = active_event_opportunities(
        at=datetime(61, 9, 15, 9, 0, 0), player_site_ref=market,
        player_faction_ref="house_tang", player_faction_headquarters="site.house_tang", sites=sites,
    )
    fair = next(row for row in rows if row["event_id"] == "autumn_trade_fair")
    assert fair["local_available"] is True
    assert {"trade", "attend", "socialize"} <= set(fair["player_modes"])
    assert "jianghu_calendar_event_resolution" in fair["command_hints"]
    assert market in fair["eligible_site_refs"]


def test_calendar_event_attendance_and_socializing_are_real_timed_commands(tmp_path):
    root = tmp_path / "campaign"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")
    meta_path = root / "state/meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["time"] = "SE-0061-09-15T09:00:00"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    scheduler_path = root / "state/martial-world/scheduler.json"
    scheduler = json.loads(scheduler_path.read_text(encoding="utf-8"))
    scheduler["settled_through"] = "0061-09-15T09:00:00"
    scheduler_path.write_text(json.dumps(scheduler), encoding="utf-8")
    sites = json.loads((root / "game/data/martial-world/local-sites.json").read_text(encoding="utf-8"))["sites"]
    market = next(ref for ref, row in sites.items() if row.get("parent_place_ref") == "luoyang" and row.get("site_type") == "market")
    roster_path = root / "state/martial-world/people/house_tang.json"
    roster = json.loads(roster_path.read_text(encoding="utf-8"))
    for person in roster["people"]:
        if person.get("person_id") == meta["player_id"]:
            person["location_ref"] = market
    roster_path.write_text(json.dumps(roster), encoding="utf-8")
    (root / "state/scene.json").write_text(json.dumps({
        "schema": "scene", "scene_id": "test.calendar.trade_fair", "location_id": market,
        "present_person_ids": [meta["player_id"]], "visible_person_ids": [meta["player_id"]],
    }), encoding="utf-8")
    planner = RepositoryCommandPlanner(RepositoryStore(root))
    event_ref = "calendar:autumn_trade_fair:0061-09-15"
    attend = CommandEnvelope(
        campaign_id=meta["campaign_id"], request_id="request.audit.calendar.attend", actor_id=meta["player_id"],
        command_type="jianghu_calendar_event_resolution", expected_revision=meta["revision"],
        submitted_at="2026-08-23T03:00:00Z", payload={"action": "attend", "event_ref": event_ref}, mode="autonomous",
    )
    plan = planner.plan(attend)
    assert plan.result["world_time"] == "SE-0061-09-15T10:00:00"
    assert plan.result["exact_attendee_person_ids"]
    other_ref = str(plan.result["exact_attendee_person_ids"][0])
    socialize = CommandEnvelope(
        campaign_id=meta["campaign_id"], request_id="request.audit.calendar.social", actor_id=meta["player_id"],
        command_type="jianghu_calendar_event_resolution", expected_revision=meta["revision"],
        submitted_at="2026-08-23T03:00:01Z",
        payload={"action": "socialize", "event_ref": event_ref, "other_ref": other_ref}, mode="autonomous",
    )
    social_plan = planner.plan(socialize)
    assert social_plan.result["world_time"] == "SE-0061-09-15T09:30:00"
    assert social_plan.result["other_ref"] == other_ref
    assert "state/martial-world/social.json" in social_plan.writes
