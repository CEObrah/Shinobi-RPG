import json
from pathlib import Path

from shinobi_runtime.martial_world.strategic_autonomy import (
    choose_friendly_aid_target,
    choose_hostile_action,
    choose_investment_priority,
    tournament_match_relation_event,
    tournament_spectator_interested,
    tournament_travel_interested,
    tournament_spectator_interested,
)
from shinobi_runtime.martial_world.faction_politics import (
    conflict_stage,
    cross_camp_pressure,
    faction_camp,
)
from shinobi_runtime.martial_world.membership import grade_eligibility
from shinobi_runtime.martial_world.equipment_lifecycle import repair_material_requirements, repair_quote
from shinobi_runtime.martial_world.independent_people import compact_independent_person, hydrate_independent_person
from shinobi_runtime.martial_world.world_health import annual_voluntary_departure_refs
from shinobi_runtime.martial_world.duties import duty_staffing_requirements
from shinobi_runtime.martial_world.enterprise_operations import (
    operate_brotherhood_livelihood_month,
    operate_criminal_enterprise_month,
)
from shinobi_runtime.martial_world.autonomous_factions import procure_project_materials, sell_surplus_to_market
from shinobi_runtime.martial_world.events import calendar_events_between, tournament_preparation_days
from shinobi_runtime.martial_world.faction_relations import apply_relation_event
from shinobi_runtime.martial_world import tournaments as tournament_runtime
from shinobi_runtime.martial_world.time_integration import settle_martial_world_frontier
from datetime import date, datetime

ROOT = Path(__file__).resolve().parents[2]


def test_hostile_autonomy_is_bounded_and_escalates_rivalry_feud_and_war():
    moderate = [{"from_faction": "a", "to_faction": "b", "hostility": 44}]
    seed_feud = [{"from_faction": "a", "to_faction": "b", "hostility": 45}]
    severe = [{"from_faction": "a", "to_faction": "b", "hostility": 80}]

    challenge = next(
        (choose_hostile_action(moderate, faction_ref="a", year=61, month=m, risk_tolerance=100)
         for m in range(1, 61)
         if choose_hostile_action(moderate, faction_ref="a", year=61, month=m, risk_tolerance=100) is not None),
        None,
    )
    war_strike = next(
        (choose_hostile_action(severe, faction_ref="a", year=61, month=m, risk_tolerance=100)
         for m in range(1, 61)
         if choose_hostile_action(severe, faction_ref="a", year=61, month=m, risk_tolerance=100) is not None),
        None,
    )
    seed_raid = next(
        (choose_hostile_action(seed_feud, faction_ref="a", year=61, month=m, risk_tolerance=100)
         for m in range(1, 61)
         if choose_hostile_action(seed_feud, faction_ref="a", year=61, month=m, risk_tolerance=100) is not None),
        None,
    )
    assert challenge and challenge["action"] == "formal_challenge"
    assert seed_raid and seed_raid["action"] == "faction_raid"
    assert war_strike and war_strike["action"] == "faction_war_strike"
    # A live war may sustain a third bounded front, but never exceed the
    # authored global front limit.
    assert any(
        choose_hostile_action(
            severe, faction_ref="a", year=61, month=m,
            risk_tolerance=100, active_strategic_operations=2,
        ) is not None
        for m in range(1, 61)
    )
    assert choose_hostile_action(
        severe, faction_ref="a", year=61, month=1,
        risk_tolerance=100, active_strategic_operations=3,
    ) is None


def test_growth_priority_prefers_real_capacity_bottlenecks_and_blocks_parallel_projects():
    faction = {
        "buildings": {"residential_compound": 3, "training_grounds": 3},
        "enterprises": {"escort_service": 3},
        "infrastructure": {
            "facilities": {
                "residential_compound": {"footprint_m2": 4000},
                "training_grounds": {"footprint_m2": 5000},
            }
        },
    }
    intent = choose_investment_priority(
        faction,
        living_population=85,
        residential_capacity=100,
        training_capacity=200,
        cash_reserve_months=12,
        active_projects=0,
        stress_milli=0,
    )
    assert intent and intent["kind"] == "expand_building"
    assert intent["building_type"] == "residential_compound"
    assert intent["additional_footprint_m2"] > 0
    assert choose_investment_priority(
        faction,
        living_population=85,
        residential_capacity=100,
        training_capacity=200,
        cash_reserve_months=12,
        active_projects=1,
        stress_milli=0,
    ) is None


def test_friendly_aid_requires_reserves_and_a_real_friendly_edge():
    edges = [{"from_faction": "a", "to_faction": "b", "trust": 60, "respect": 40, "hostility": 0}]
    target = next(
        (choose_friendly_aid_target(edges, faction_ref="a", year=61, month=m, cash_reserve_months=12)
         for m in range(1, 121)
         if choose_friendly_aid_target(edges, faction_ref="a", year=61, month=m, cash_reserve_months=12) is not None),
        None,
    )
    assert target == "b"
    assert choose_friendly_aid_target(edges, faction_ref="a", year=61, month=1, cash_reserve_months=7) is None


def test_membership_progression_can_create_elites_without_making_elder_easy():
    elite_candidate = {
        "martial_skills": {"sword": 80},
        "qi": 60,
        "qi_control": 60,
    }
    assert grade_eligibility(
        elite_candidate,
        target_grade="elite",
        service_days=1825,
        primary_discipline="sword",
    )["eligible"]
    assert not grade_eligibility(
        elite_candidate,
        target_grade="elder",
        service_days=3650,
        primary_discipline="sword",
        elder_open_seat=True,
    )["eligible"]

    elder_candidate = {
        "martial_skills": {"sword": 90},
        "qi": 80,
        "qi_control": 75,
    }
    assert grade_eligibility(
        elder_candidate,
        target_grade="elder",
        service_days=3650,
        primary_discipline="sword",
        elder_open_seat=True,
    )["eligible"]
    assert not grade_eligibility(
        elder_candidate,
        target_grade="elder",
        service_days=3650,
        primary_discipline="sword",
        elder_open_seat=False,
    )["eligible"]


def test_real_equipment_repair_consumes_labor_and_recipe_materials():
    quote = repair_quote(integrity_milli=600, target_integrity_milli=1000, crafting_skill=100)
    assert quote["integrity_restored_milli"] == 400
    assert quote["crafting_hours"] > 0
    mats = repair_material_requirements(item_ref="weapon_jian", integrity_restored_milli=400)
    assert mats and all(v > 0 for v in mats.values())


def test_independent_person_roundtrip_preserves_identity_but_removes_faction_rank_and_duty():
    person = {
        "person_id": "person.test",
        "name": "Test Person",
        "faction_ref": "house_test",
        "membership_grade": "full",
        "standing_duty_ref": "kitchen_service",
        "birth_year": 30,
        "sex": "male",
        "attributes": {},
        "martial_skills": {},
        "professional_skills": {},
        "aptitudes": {},
        "qi": 0,
        "qi_control": 0,
        "health": {"status": "ready", "consciousness": 100, "injuries": []},
    }
    compact = compact_independent_person(person)
    assert compact["person_id"] == "person.test"
    assert "faction_ref" not in compact
    assert "membership_grade" not in compact
    assert "standing_duty_ref" not in compact
    hydrated = hydrate_independent_person(compact)
    assert hydrated["person_id"] == "person.test"


def test_voluntary_departure_respects_active_commitment_protection():
    people = [
        {
            "person_id": f"person.churn.{idx}",
            "membership_grade": "junior",
            "birth_year": 20,
            "health": {"status": "ready", "consciousness": 100, "injuries": []},
        }
        for idx in range(1000)
    ]
    candidates = annual_voluntary_departure_refs(
        people,
        faction_ref="faction.churn_test",
        year=61,
        hardship_milli=1000,
        maximum=20,
        period_key="stress-test",
    )
    assert candidates
    protected = candidates[0]
    after = annual_voluntary_departure_refs(
        people,
        faction_ref="faction.churn_test",
        year=61,
        hardship_milli=1000,
        protected_refs=[protected],
        maximum=20,
        period_key="stress-test",
    )
    assert protected not in after


def test_authored_brotherhood_livelihood_assigns_real_trade_service_time():
    faction = {
        "type": "brotherhood_society",
        "population": 24,
        "buildings": {"main_hall": 2, "residential_compound": 2},
        "enterprises": {
            "agriculture_landholding": 0,
            "trade_merchant_business": 0,
            "crafting_workshop": 0,
            "medicine_apothecary": 0,
            "escort_service": 0,
            "school_tuition": 0,
        },
    }
    req = duty_staffing_requirements(faction)
    assert req["trade_service"] == 8


def test_criminal_enterprise_requires_real_cell_operators_and_conserves_market_cash():
    faction = {
        "type": "outlaw_faction",
        "population": 35,
        "buildings": {"main_hall": 2, "residential_compound": 2},
        "enterprises": {"criminal_enterprise": 2},
        "enterprise_scale": {"criminal_enterprise": {"registered_cells_or_ventures": 3}},
    }
    req = duty_staffing_requirements(faction)
    assert req["trade_service"] == 3
    market = {"cash_pool": 100_000, "stock": {}}
    result = operate_criminal_enterprise_month(
        market,
        enterprise_level=2,
        registered_ventures=3,
        worker_count=3,
        average_commerce=30,
        risk_tolerance=50,
        general_labor_cash_per_hour=30,
    )
    assert result["active_ventures"] == 3
    assert result["cash_earned"] > 0
    assert result["market"]["cash_pool"] + result["cash_earned"] == market["cash_pool"]
    blocked = operate_criminal_enterprise_month(
        market,
        enterprise_level=2,
        registered_ventures=3,
        worker_count=0,
        average_commerce=30,
        risk_tolerance=50,
        general_labor_cash_per_hour=30,
    )
    assert blocked["cash_earned"] == 0


def test_brotherhood_livelihood_conserves_regional_cash_and_needs_workers():
    market = {"cash_pool": 50_000, "stock": {}}
    result = operate_brotherhood_livelihood_month(
        market,
        worker_count=6,
        average_commerce=30,
        general_labor_cash_per_hour=30,
    )
    assert result["labor_hours"] == 6 * (30 * 8 * 420 // 1000)
    assert result["cash_earned"] > 0
    assert result["market"]["cash_pool"] + result["cash_earned"] == market["cash_pool"]


def test_autonomous_project_material_procurement_uses_finite_market_stock_and_cash():
    faction = {"treasury_cash": 100_000}
    inventory = {"raw_materials": {"brick_tile_kg": 20}}
    market = {
        "schema": "jianghu-market-state-1.0",
        "region_id": "central_plain",
        "stock": {"brick_tile_kg": 1000, "lime_kg": 1000},
        "cash_pool": 50_000,
    }
    result = procure_project_materials(
        faction, inventory, market,
        region_id="central_plain",
        required_materials={"brick_tile_kg": 120, "lime_kg": 30},
    )
    assert result["purchased"] == {"brick_tile_kg": 100, "lime_kg": 30}
    assert result["inventory"]["raw_materials"]["brick_tile_kg"] == 120
    assert result["inventory"]["raw_materials"]["lime_kg"] == 30
    assert result["faction"]["treasury_cash"] + result["cash_spent"] == faction["treasury_cash"]
    assert result["market"]["cash_pool"] == market["cash_pool"] + result["cash_spent"]
    assert result["market"]["stock"]["brick_tile_kg"] == 900
    assert result["market"]["stock"]["lime_kg"] == 970


def test_every_authored_outlaw_seed_has_registered_criminal_enterprise_capacity():
    identities = json.loads((ROOT / "game/data/martial-world/faction-identities.json").read_text())["identities"]
    world = json.loads((ROOT / "game/data/martial-world/world-seed.json").read_text())["martial_factions"]
    for faction_ref, identity in identities.items():
        if identity.get("faction_type") != "outlaw_faction":
            continue
        assert int(world[faction_ref].get("enterprises", {}).get("criminal_enterprise", 0)) > 0, faction_ref
        state = json.loads((ROOT / f"state/martial-world/factions/{faction_ref}.json").read_text())
        assert int(state.get("enterprises", {}).get("criminal_enterprise", 0)) > 0, faction_ref
        assert int(state.get("enterprise_scale", {}).get("criminal_enterprise", {}).get("registered_cells_or_ventures", 0)) > 0, faction_ref


def test_tournament_travel_interest_is_broad_without_a_field_slot_cap():
    regional_profile = tournament_runtime.event_profile("regional_martial_tournament")
    great_profile = tournament_runtime.event_profile("great_jianghu_tournament")
    regional = sum(
        tournament_travel_interested(
            faction_ref=f"faction.travel.{idx}", tournament_ref="regional:62-04-15",
            tournament_kind="regional_martial_tournament", training_priority=50, risk_tolerance=50,
            entry_fee_cash=regional_profile["entry_fee_cash"], current_prize_cash=100_000,
            prestige_weight=regional_profile["prestige_weight"],
        )
        for idx in range(200)
    )
    great = sum(
        tournament_travel_interested(
            faction_ref=f"faction.travel.{idx}", tournament_ref="great:64-09-01",
            tournament_kind="great_jianghu_tournament", training_priority=50, risk_tolerance=50,
            entry_fee_cash=great_profile["entry_fee_cash"], current_prize_cash=1_000_000,
            prestige_weight=great_profile["prestige_weight"],
        )
        for idx in range(200)
    )
    assert 50 < regional < 140
    assert great == 200
    assert "max_entrants" not in regional_profile
    assert "reserved_travel_slots" not in great_profile


def test_year_62_chengdu_regional_has_real_nearby_travel_interest():
    tournament_ref = "tournament:regional_martial_tournament:0062-04-15:individual"
    nearby = ("qingcheng", "emei", "faction.south_market_boxing_school")
    profile = tournament_runtime.event_profile("regional_martial_tournament")
    interested = []
    for faction_ref in nearby:
        faction = json.loads((ROOT / f"state/martial-world/factions/{faction_ref}.json").read_text())
        policy = faction.get("autonomy_policy", {})
        if tournament_travel_interested(
            faction_ref=faction_ref, tournament_ref=tournament_ref,
            tournament_kind="regional_martial_tournament",
            training_priority=int(policy.get("training_priority", 50)),
            risk_tolerance=int(policy.get("risk_tolerance", 50)),
            entry_fee_cash=profile["entry_fee_cash"], current_prize_cash=100_000,
            prestige_weight=profile["prestige_weight"],
        ):
            interested.append(faction_ref)
    assert interested


def test_calendar_generates_annual_regional_and_four_year_great_tournaments():
    rows_62 = calendar_events_between(date(62, 1, 1), date(62, 12, 31))
    regional = next(row for row in rows_62 if row["event_id"] == "regional_martial_tournament")
    assert regional["date"] == "0062-04-15"
    assert regional["registration_closes_on"] == "0062-04-12"
    assert regional["registration_opens_on"] == "0062-01-12"
    assert regional["convergence_days_before"] == 2
    assert regional["host_place_id"] == "chengdu"
    assert not any(row["event_id"] == "great_jianghu_tournament" for row in rows_62)

    rows_64 = calendar_events_between(date(64, 1, 1), date(64, 12, 31))
    great = next(row for row in rows_64 if row["event_id"] == "great_jianghu_tournament")
    assert great["registration_closes_on"] == "0064-08-24"
    assert great["advance_notice_on"] == "0063-09-02"
    assert great["registration_opens_on"] == "0063-12-28"
    assert tournament_preparation_days("great_jianghu_tournament", host_place_id="luoyang") == 240
    assert great["convergence_days_before"] == 7
    assert great["host_place_id"] == "luoyang"


def test_great_tournament_preparation_window_covers_every_connected_place_under_worst_registered_travel_factors():
    from math import ceil
    from shinobi_runtime.martial_world.travel import shortest_route

    geo = json.loads((ROOT / "game/data/martial-world/geography.json").read_text())
    travel = json.loads((ROOT / "game/data/martial-world/travel.json").read_text())
    worst_weather = max(int(x) for x in travel["weather_time_milli"].values())
    worst_ground = max(int(x) for x in travel["ground_time_milli"].values())
    worst_route_days = 0
    for place_ref in geo["places"]:
        route = shortest_route(start=place_ref, end="luoyang", mode="foot")
        days = ceil(float(route["baseline_hours"]) * worst_weather * worst_ground / 1_000_000 / 24)
        worst_route_days = max(worst_route_days, days)
    preparation_days = tournament_preparation_days("great_jianghu_tournament", host_place_id="luoyang")
    assert worst_route_days == 180
    assert preparation_days >= worst_route_days + 60


def test_tournament_departure_planning_returns_only_recomputed_weather_safe_departure():
    from datetime import datetime
    from shinobi_runtime.martial_world.travel import latest_safe_departure

    # This route/season previously made the old two-step fixed-point planner
    # oscillate between a fast and slow weather regime and schedule a late
    # arrival.  The new planner must return a departure it actually re-evaluated
    # as safe.
    result = latest_safe_departure(
        world_seed="jianghu-wei-main-canonical-0061",
        not_before=datetime.fromisoformat("0063-12-28T18:01:00"),
        target_arrival=datetime.fromisoformat("0064-08-23T21:00:00"),
        start="kunming", end="luoyang", mode="foot",
    )
    assert result["reachable"] is True
    assert datetime.fromisoformat(result["arrival_at"]) <= datetime.fromisoformat(result["target_arrival_at"])
    assert datetime.fromisoformat(result["departure_at"]) > datetime.fromisoformat("0064-04-01T00:00:00")


def test_tournament_profiles_are_faction_funded_and_have_no_fixed_purse_or_slots():
    regional = tournament_runtime.event_profile("regional_martial_tournament")
    great = tournament_runtime.event_profile("great_jianghu_tournament")
    assert regional["entry_fee_cash"] == 5_000
    assert great["entry_fee_cash"] == 10_000
    for profile in (regional, great):
        assert "prize_share_permille" not in profile
        assert "host_operations_share_permille" not in profile
        assert profile["placement_faction_share_permille"] == 700
        assert profile["placement_personal_share_permille"] == 300
    assert regional["prestige_weight"] == 85
    assert regional["allows_outlaw_factions"] is False
    assert regional["convergence_days_before"] == 2
    assert regional["matches_per_competition_session"] == 16
    assert regional["competition_sessions_per_day"] == 2
    assert regional["spectator_delegation_floor"] == 1
    assert regional["major_spectator_delegation_floor"] == 3
    assert regional["leader_attendance_permille"] == 500
    assert regional["attendee_host_cash_per_person_day"] == 100
    assert great["prestige_weight"] == 100
    assert great["allows_outlaw_factions"] is True
    assert great["safe_conduct_on_official_grounds"] is True
    assert great["faction_interest_floor_permille"] == 1000
    assert great["major_sect_competitor_floor"] == 3
    assert great["major_institution_competitor_floor"] == 2
    assert great["ordinary_competitor_floor"] == 1
    assert great["convergence_days_before"] == 7
    assert great["convergence_contacts_per_faction_per_day"] == 2
    assert great["matches_per_competition_session"] == 24
    assert great["competition_sessions_per_day"] == 4
    assert great["spectator_delegation_floor"] == 3
    assert great["major_spectator_delegation_floor"] == 8
    assert great["major_sect_spectator_delegation_floor"] == 12
    assert great["leader_attendance_permille"] == 1000
    assert great["attendee_host_cash_per_person_day"] == 140
    assert tournament_runtime.estimated_host_days("great_jianghu_tournament", 538) == 21
    assert tournament_runtime.estimated_host_days("regional_martial_tournament", 33) == 6
    config = json.loads((ROOT / "game/data/martial-world/tournaments.json").read_text())
    assert "prizes" not in config
    assert "max_entrants" not in config["event_profiles"]["regional_martial_tournament"]
    assert "reserved_travel_slots" not in config["event_profiles"]["great_jianghu_tournament"]

def test_paid_registration_builds_prize_from_faction_fee_with_no_host_cut():
    opened = tournament_runtime.open_tournament(
        event_id="regional:test", format_ref="individual",
        organizer_ref="government.chengdu", great=False,
    )
    assert opened["prize_escrow_cash"] == 0
    assert opened["entry_fee_cash"] == 5000
    reg = tournament_runtime.register(
        opened, entrant_ref="person.a", qualifying_score=10,
        payer_cash=100_000, alive=True, medically_eligible=True,
    )
    after = reg["tournament_after"]
    assert reg["payer_cash_after"] == 95_000
    assert reg["prize_contribution_cash"] == 5_000
    assert "organizer_fee_income_cash" not in reg
    assert after["prize_escrow_cash"] == 5_000
    assert after["entry_fees_collected_cash"] == 5_000
    assert "host_operations_cash_collected" not in after
    assert 100_000 == reg["payer_cash_after"] + after["prize_escrow_cash"]
    after = tournament_runtime.add_attendance_prize_cash(
        after, amount_cash=2_500, source_kind="faction_delegate_ticket",
    )
    after = tournament_runtime.add_attendance_prize_cash(
        after, amount_cash=7_500, source_kind="public_spectator_ticket",
    )
    assert after["prize_escrow_cash"] == 15_000
    assert after["delegate_ticket_cash_collected"] == 2_500
    assert after["public_ticket_cash_collected"] == 7_500


def test_round_one_loser_can_win_full_losers_bracket_and_finish_third():
    tournament = {
        "event_id": "great:losers-bracket",
        "status": "registration_open",
        "registrations": [
            {"entrant_ref": f"person.{idx}", "public_qualifying_score": 100 - idx}
            for idx in range(8)
        ],
    }
    tournament = tournament_runtime.close_registration(tournament)
    target = "person.7"
    losers_bracket_wins = 0
    while True:
        nxt = tournament_runtime.begin_next_match(tournament)
        tournament = nxt["tournament_after"]
        if nxt["completed"]:
            break
        a, b = nxt["pair"]
        phase = tournament.get("active_phase")
        if phase == "losers_bracket" and target in {a, b}:
            winner = target
            losers_bracket_wins += 1
        else:
            winner = a
        tournament = tournament_runtime.record_match_winner(tournament, winner_ref=winner)
    loss = next(row for row in tournament["championship_losers"] if row["person_ref"] == target)
    assert loss["lost_round"] == 1
    assert losers_bracket_wins == 3
    assert tournament["placements"]["third"] == target




def test_losers_bracket_handles_odd_even_fields_byes_and_pays_the_entire_purse():
    for field_size in range(2, 65):
        tournament = tournament_runtime.open_tournament(
            event_id=f"great:field:{field_size}", format_ref="individual",
            organizer_ref="government.imperial", great=True,
        )
        for idx in range(field_size):
            registration = tournament_runtime.register(
                tournament, entrant_ref=f"person.{idx:03d}",
                qualifying_score=field_size - idx, payer_cash=10_000,
                alive=True, medically_eligible=True,
            )
            tournament = registration["tournament_after"]
        tournament = tournament_runtime.close_registration(tournament)
        match_count = 0
        while True:
            next_match = tournament_runtime.begin_next_match(tournament)
            tournament = next_match["tournament_after"]
            if next_match["completed"]:
                break
            tournament = tournament_runtime.record_match_winner(
                tournament, winner_ref=next_match["pair"][0],
            )
            match_count += 1
            assert match_count <= field_size * 4
        assert tournament["placements"].get("first")
        assert tournament["placements"].get("second")
        if field_size >= 3:
            assert tournament["placements"].get("third")
        if field_size >= 4:
            assert tournament["placements"].get("fourth")
        expected_matches = 1 if field_size == 2 else field_size * 2 - 4
        assert match_count == expected_matches
        payouts = tournament_runtime.placement_payouts(tournament)
        assert sum(row["cash"] for row in payouts) == tournament["prize_escrow_cash"]

def test_all_paid_tournament_attendance_joins_prize_and_entire_purse_is_paid_out():
    tournament = tournament_runtime.open_tournament(
        event_id="great:funding", format_ref="individual",
        organizer_ref="government.imperial", great=True,
    )
    sponsor_cash = 100_000
    entrants = ["person.a", "person.b", "person.c", "person.d"]
    for score, entrant in enumerate(entrants, start=1):
        reg = tournament_runtime.register(
            tournament, entrant_ref=entrant, qualifying_score=score,
            payer_cash=sponsor_cash, alive=True, medically_eligible=True,
        )
        tournament = reg["tournament_after"]
        sponsor_cash = reg["payer_cash_after"]
    tournament = tournament_runtime.add_attendance_prize_cash(
        tournament, amount_cash=12_000, source_kind="faction_delegate_ticket",
    )
    tournament = tournament_runtime.add_attendance_prize_cash(
        tournament, amount_cash=8_000, source_kind="public_spectator_ticket",
    )
    tournament["placements"] = {
        "first": "person.a", "second": "person.b",
        "third": "person.c", "fourth": "person.d",
    }
    assert tournament["entry_fees_collected_cash"] == 40_000
    assert tournament["delegate_ticket_cash_collected"] == 12_000
    assert tournament["public_ticket_cash_collected"] == 8_000
    assert "host_operations_cash_collected" not in tournament
    assert tournament["prize_escrow_cash"] == 60_000
    payouts = tournament_runtime.placement_payouts(tournament)
    assert [row["place"] for row in payouts] == ["first", "second", "third", "fourth"]
    assert sum(row["cash"] for row in payouts) == 60_000
    assert payouts[0]["cash"] == 27_000


def test_tournament_bracket_accepts_large_paid_field_without_configured_slot_limit():
    tournament = tournament_runtime.open_tournament(
        event_id="regional:large", format_ref="individual",
        organizer_ref="government.chengdu", great=False,
    )
    sponsor_cash = 1_000_000
    for idx in range(73):
        reg = tournament_runtime.register(
            tournament, entrant_ref=f"person.{idx:03d}", qualifying_score=idx,
            payer_cash=sponsor_cash, alive=True, medically_eligible=True,
        )
        tournament = reg["tournament_after"]
        sponsor_cash = reg["payer_cash_after"]
    closed = tournament_runtime.close_registration(tournament)
    assert len(closed["registrations"]) == 73
    assert len(closed["bracket"]) == 37
    assert closed["prize_escrow_cash"] == 73 * 5_000
    assert closed["phase"] == "championship"


def test_all_rotating_hosts_have_real_tournament_grounds():
    world_events = json.loads((ROOT / "game/data/martial-world/world-events.json").read_text())
    local_sites = json.loads((ROOT / "game/data/martial-world/local-sites.json").read_text())["sites"]
    hosts = world_events["host_cycles"]["regional_martial_tournament"]
    for host in hosts:
        grounds = [
            row for row in local_sites.values()
            if row.get("parent_place_ref") == host and row.get("site_type") == "tournament_ground"
        ]
        assert grounds, host
    luoyang = next(row for row in local_sites.values() if row.get("site_ref") == "site.luoyang.great_tournament_ground")
    assert luoyang["owner_ref"] == "government.imperial"


def test_major_tournament_interest_can_nominate_multiple_people_without_a_per_faction_cap():
    from shinobi_runtime.martial_world.strategic_autonomy import tournament_entrant_interested
    profile = tournament_runtime.event_profile("great_jianghu_tournament")
    nominated = [
        idx for idx in range(20)
        if tournament_entrant_interested(
            faction_ref="faction.major", person_ref=f"person.major.{idx}",
            tournament_ref="great:64-09-01", tournament_kind="great_jianghu_tournament",
            entrant_order=idx, training_priority=60, risk_tolerance=60,
            prestige_weight=profile["prestige_weight"],
            candidate_combat_index=max(70.0, 100.0 - idx), best_combat_index=100.0,
            additional_competitor_interest_permille=profile["additional_competitor_interest_permille"],
            additional_competitor_decay_permille=profile["additional_competitor_decay_permille"],
            additional_competitor_relative_strength_permille=profile["additional_competitor_relative_strength_permille"],
        )
    ]
    assert len(nominated) >= 2
    assert any(idx >= 3 for idx in nominated)


def test_great_tournament_major_sects_always_attempt_and_send_serious_delegations():
    from shinobi_runtime.martial_world.strategic_autonomy import tournament_entrant_interested
    profile = tournament_runtime.event_profile("great_jianghu_tournament")
    major_sects = ("shaolin", "wudang", "mount_hua", "emei", "quanzhen", "songshan", "qingcheng", "kunlun")
    for faction_ref in major_sects:
        faction = json.loads((ROOT / f"state/martial-world/factions/{faction_ref}.json").read_text())
        assert tournament_travel_interested(
            faction_ref=faction_ref,
            tournament_ref="tournament:great_jianghu_tournament:0064-09-01:individual",
            tournament_kind="great_jianghu_tournament",
            training_priority=0,
            risk_tolerance=0,
            entry_fee_cash=profile["entry_fee_cash"],
            current_prize_cash=0,
            prestige_weight=profile["prestige_weight"],
            faction_type="sect",
            living_members=int(faction.get("population", 0)),
            faction_interest_floor_permille=profile["faction_interest_floor_permille"],
            major_sect_population_threshold=profile["major_sect_population_threshold"],
        )
        for entrant_order in range(profile["major_sect_competitor_floor"]):
            assert tournament_entrant_interested(
                faction_ref=faction_ref,
                person_ref=f"person.{faction_ref}.{entrant_order}",
                tournament_ref="tournament:great_jianghu_tournament:0064-09-01:individual",
                tournament_kind="great_jianghu_tournament",
                entrant_order=entrant_order,
                training_priority=0,
                risk_tolerance=0,
                prestige_weight=profile["prestige_weight"],
                faction_type="sect",
                living_members=int(faction.get("population", 0)),
                major_sect_population_threshold=profile["major_sect_population_threshold"],
                major_sect_competitor_floor=profile["major_sect_competitor_floor"],
                major_institution_population_threshold=profile["major_institution_population_threshold"],
                major_institution_competitor_floor=profile["major_institution_competitor_floor"],
                ordinary_competitor_floor=profile["ordinary_competitor_floor"],
                candidate_combat_index=100.0 - entrant_order,
                best_combat_index=100.0,
                additional_competitor_interest_permille=profile["additional_competitor_interest_permille"],
                additional_competitor_decay_permille=profile["additional_competitor_decay_permille"],
                additional_competitor_relative_strength_permille=profile["additional_competitor_relative_strength_permille"],
            )


def test_great_tournament_is_near_universal_for_other_lawful_factions():
    profile = tournament_runtime.event_profile("great_jianghu_tournament")
    interested = sum(
        tournament_travel_interested(
            faction_ref=f"faction.lawful.{idx}", tournament_ref="great:64-09-01",
            tournament_kind="great_jianghu_tournament", training_priority=20, risk_tolerance=20,
            entry_fee_cash=profile["entry_fee_cash"], current_prize_cash=0,
            prestige_weight=profile["prestige_weight"], faction_type="martial_school",
            living_members=40, faction_interest_floor_permille=profile["faction_interest_floor_permille"],
            major_sect_population_threshold=profile["major_sect_population_threshold"],
        )
        for idx in range(1000)
    )
    assert interested >= 990


def test_great_tournament_reaches_almost_the_entire_canonical_jianghu_and_every_major_sect_attempts():
    profile = tournament_runtime.event_profile("great_jianghu_tournament")
    identities = json.loads((ROOT / "game/data/martial-world/faction-identities.json").read_text())["identities"]
    world = json.loads((ROOT / "game/data/martial-world/world-seed.json").read_text())["martial_factions"]
    interested = 0
    major_sects = 0
    for faction_ref, identity in identities.items():
        faction = json.loads((ROOT / f"state/martial-world/factions/{faction_ref}.json").read_text())
        static = world[faction_ref]
        policy = static.get("autonomy_policy", {})
        living = int(faction.get("population", 0))
        tries = tournament_travel_interested(
            faction_ref=faction_ref,
            tournament_ref="tournament:great_jianghu_tournament:0064-09-01:individual",
            tournament_kind="great_jianghu_tournament",
            training_priority=int(policy.get("training_priority", 50)),
            risk_tolerance=int(policy.get("risk_tolerance", 50)),
            entry_fee_cash=profile["entry_fee_cash"],
            current_prize_cash=0,
            prestige_weight=profile["prestige_weight"],
            faction_type=str(static.get("type") or identity.get("faction_type") or ""),
            living_members=living,
            faction_interest_floor_permille=profile["faction_interest_floor_permille"],
            major_sect_population_threshold=profile["major_sect_population_threshold"],
        )
        interested += int(tries)
        if str(static.get("type")) == "sect" and living >= profile["major_sect_population_threshold"]:
            major_sects += 1
            assert tries, faction_ref
    assert major_sects >= 15
    assert profile["allows_outlaw_factions"] is True
    assert interested * 1000 >= len(identities) * 980


def test_great_tournament_spectator_delegations_prioritize_major_sects_and_leaders_without_a_cap():
    profile = tournament_runtime.event_profile("great_jianghu_tournament")
    tref = "tournament:great_jianghu_tournament:0064-09-01:individual"
    # A major sect receives a real minimum delegation floor, including its
    # leader. Beyond that floor, attendance remains a declining marginal choice
    # rather than a configured maximum.
    for order in range(profile["major_sect_spectator_delegation_floor"]):
        assert tournament_spectator_interested(
            faction_ref="shaolin", person_ref=f"shaolin.spectator.{order}",
            tournament_ref=tref, tournament_kind="great_jianghu_tournament",
            spectator_order=order, is_leader=(order == 0), faction_type="sect", living_members=200,
            spectator_delegation_floor=profile["spectator_delegation_floor"],
            major_spectator_population_threshold=profile["major_spectator_population_threshold"],
            major_spectator_delegation_floor=profile["major_spectator_delegation_floor"],
            major_sect_spectator_delegation_floor=profile["major_sect_spectator_delegation_floor"],
            leader_attendance_permille=profile["leader_attendance_permille"],
            spectator_marginal_interest_permille=profile["spectator_marginal_interest_permille"],
            spectator_marginal_decay_permille=profile["spectator_marginal_decay_permille"],
        )
    assert tournament_spectator_interested(
        faction_ref="shaolin", person_ref="shaolin.leader", tournament_ref=tref,
        tournament_kind="great_jianghu_tournament", spectator_order=40, is_leader=True,
        faction_type="sect", living_members=200,
        spectator_delegation_floor=profile["spectator_delegation_floor"],
        major_spectator_population_threshold=profile["major_spectator_population_threshold"],
        major_spectator_delegation_floor=profile["major_spectator_delegation_floor"],
        major_sect_spectator_delegation_floor=profile["major_sect_spectator_delegation_floor"],
        leader_attendance_permille=profile["leader_attendance_permille"],
        spectator_marginal_interest_permille=profile["spectator_marginal_interest_permille"],
        spectator_marginal_decay_permille=profile["spectator_marginal_decay_permille"],
    )


def test_political_camps_are_complete_and_never_create_war_without_hostility():
    identities = json.loads((ROOT / "game/data/martial-world/faction-identities.json").read_text())["identities"]
    camps = {fid: faction_camp(fid) for fid in identities}
    assert len(camps) == 240
    assert set(camps.values()) == {"orthodox", "unorthodox", "outlaw"}
    assert sum(1 for value in camps.values() if value == "outlaw") == 70
    assert conflict_stage({"hostility": 0}) == "peace"
    assert conflict_stage({"hostility": 30}) == "rivalry"
    assert conflict_stage({"hostility": 45}) == "feud"
    assert conflict_stage({"hostility": 65}) == "war"
    assert choose_hostile_action(
        [{"from_faction": "shaolin", "to_faction": "beggars_society", "hostility": 0}],
        faction_ref="shaolin", year=64, month=8, risk_tolerance=100,
    ) is None


def test_great_tournament_senior_mediation_can_reduce_existing_hostility_but_not_invent_friendship():
    hostile = {"from_faction": "a", "to_faction": "b", "hostility": 65, "trust": -10, "respect": 20}
    mediated = apply_relation_event(hostile, from_faction="a", to_faction="b", event_kind="tournament_mediation")
    assert mediated["hostility"] == 60
    assert mediated["trust"] == -8
    assert mediated["respect"] == 23
    neutral = apply_relation_event(None, from_faction="a", to_faction="b", event_kind="tournament_contact")
    assert neutral.get("hostility", 0) == 0


def test_real_tournament_match_reports_pair_for_relation_evidence(monkeypatch):
    def fake_combat(**kwargs):
        return {
            "people_after": kwargs["people"],
            "equipment_ledger_after": kwargs["equipment_ledger"],
            "resolved": True,
            "winner_side": "side_a",
        }
    monkeypatch.setattr(tournament_runtime, "simulate_exact_combat", fake_combat)
    tournament = {
        "event_id": "regional:test", "tournament_ref": "regional:test",
        "status": "bracket_ready", "registrations": [
            {"entrant_ref": "person.a", "public_qualifying_score": 10},
            {"entrant_ref": "person.b", "public_qualifying_score": 9},
        ],
        "bracket": [["person.a", "person.b"]], "round_number": 1, "round_winners": [],
    }
    people = {
        "person.a": {"person_id": "person.a", "health": {"status": "ready", "consciousness": 100}},
        "person.b": {"person_id": "person.b", "health": {"status": "ready", "consciousness": 100}},
    }
    result = tournament_runtime.advance_individual_competition(
        tournament, people=people, equipment_ledger={}, doctrines={}, combats_state={"combats": {}},
        zone_ref="site.test", at_iso="0062-04-15T09:00:00", max_exchanges=1,
    )
    assert result["completed"]
    assert result["resolved_pairs"] == [["person.a", "person.b"]]


def test_large_tournament_resumes_across_bounded_competition_sessions_without_dropping_entrants(monkeypatch):
    def fake_combat(**kwargs):
        return {
            "people_after": kwargs["people"],
            "equipment_ledger_after": kwargs["equipment_ledger"],
            "resolved": True,
            "winner_side": "side_a",
        }
    monkeypatch.setattr(tournament_runtime, "simulate_exact_combat", fake_combat)
    tournament = {
        "event_id": "regional:session", "tournament_ref": "regional:session",
        "status": "registration_open", "registrations": [
            {"entrant_ref": f"person.{idx}", "public_qualifying_score": 100 - idx}
            for idx in range(8)
        ],
    }
    tournament = tournament_runtime.close_registration(tournament)
    people = {
        f"person.{idx}": {
            "person_id": f"person.{idx}",
            "health": {"status": "ready", "consciousness": 100},
        }
        for idx in range(8)
    }
    total_matches = 0
    sessions = 0
    while True:
        sessions += 1
        result = tournament_runtime.advance_individual_competition(
            tournament, people=people, equipment_ledger={}, doctrines={},
            combats_state={"combats": {}}, zone_ref="site.test",
            at_iso=f"0062-04-{14 + sessions:02d}T09:00:00",
            max_exchanges=1, max_matches=2,
        )
        total_matches += result["matches_resolved_count"]
        assert result["matches_resolved_count"] <= 2
        people = result["people_after"]
        tournament = result["tournament_after"]
        if result["completed"]:
            break
        assert result["continuation_required"]
    assert sessions == 6
    assert total_matches == 12
    assert tournament["status"] == "completed"
    assert tournament["champion_ref"] in people
    assert set(tournament["placements"]) == {"first", "second", "third", "fourth"}


def test_first_round_loser_can_win_full_losers_bracket_and_finish_third(monkeypatch):
    def fake_combat(**kwargs):
        a = kwargs["side_a_refs"][0]
        b = kwargs["side_b_refs"][0]
        phase = kwargs["objective"].get("phase")
        winner_side = "side_b" if phase == "losers_bracket" and b == "person.7" else "side_a"
        return {
            "people_after": kwargs["people"],
            "equipment_ledger_after": kwargs["equipment_ledger"],
            "resolved": True,
            "winner_side": winner_side,
        }
    monkeypatch.setattr(tournament_runtime, "simulate_exact_combat", fake_combat)
    tournament = {
        "event_id": "regional:losers-bracket", "tournament_ref": "regional:losers-bracket",
        "status": "registration_open", "registrations": [
            {"entrant_ref": f"person.{idx}", "public_qualifying_score": 100 - idx}
            for idx in range(8)
        ],
        "prize_escrow_cash": 100_000,
        "prize_payout_permille": {"first": 500, "second": 250, "third": 150, "fourth": 100},
    }
    tournament = tournament_runtime.close_registration(tournament)
    people = {
        f"person.{idx}": {"person_id": f"person.{idx}", "health": {"status": "ready", "consciousness": 100}}
        for idx in range(8)
    }
    result = tournament_runtime.advance_individual_competition(
        tournament, people=people, equipment_ledger={}, doctrines={}, combats_state={"combats": {}},
        zone_ref="site.test", at_iso="0062-04-15T09:00:00", max_exchanges=1, max_matches=100,
    )
    assert result["completed"]
    placements = result["tournament_after"]["placements"]
    assert placements["first"] == "person.0"
    assert placements["second"] == "person.1"
    # person.7 lost the first championship match to person.0, then won the
    # complete losers-bracket path and lawfully recovered to third place.
    assert placements["third"] == "person.7"
    payouts = tournament_runtime.placement_payouts(result["tournament_after"])
    assert sum(row["cash"] for row in payouts) == 100_000
    assert payouts == [
        {"place": "first", "entrant_ref": "person.0", "cash": 50_000},
        {"place": "second", "entrant_ref": "person.1", "cash": 25_000},
        {"place": "third", "entrant_ref": "person.7", "cash": 15_000},
        {"place": "fourth", "entrant_ref": placements["fourth"], "cash": 10_000},
    ]


def test_tournament_sportsmanship_can_create_future_aid_eligibility_from_real_interaction():
    edge = {"from_faction": "a", "to_faction": "b", "trust": 15, "respect": 8, "hostility": 10, "obligation": 0}
    after = apply_relation_event(edge, from_faction="a", to_faction="b", event_kind="tournament_sportsmanship")
    assert after["trust"] == 18
    assert after["hostility"] == 8
    assert any(
        choose_friendly_aid_target([after], faction_ref="a", year=62, month=m, cash_reserve_months=12) == "b"
        for m in range(1, 121)
    )


def test_real_tournament_matches_can_seed_bounded_rivalry_without_camp_auto_war():
    # Camp identity without contact creates nothing. Once a public match really
    # happens, a cross-camp pair may become rivals, but the event is deliberately
    # too small to jump a neutral relation into feud/war.
    outcomes = {
        tournament_match_relation_event(
            faction_a="shaolin", faction_b="faction.black_oar_pirates",
            tournament_ref="tournament:great_jianghu_tournament:0064-09-01:individual",
            person_a=f"shaolin.{idx}", person_b=f"pirate.{idx}", hostility=0,
        )
        for idx in range(40)
    }
    assert outcomes <= {"tournament_sportsmanship", "tournament_rivalry"}
    assert "tournament_rivalry" in outcomes
    rivalry = apply_relation_event(None, from_faction="shaolin", to_faction="faction.black_oar_pirates", event_kind="tournament_rivalry")
    assert rivalry["respect"] == 5
    assert rivalry["hostility"] == 3
    assert conflict_stage(rivalry) == "peace"


def test_agriculture_can_sell_only_local_food_surplus_without_merchant_liquidation():
    faction = {"population": 10, "treasury_cash": 0, "autonomy_policy": {"financial_caution": 50}}
    inventory = {"food_ration_days": 1000, "raw_materials": {"brick_tile_kg": 100}}
    market = {
        "schema": "jianghu-market-state-1.0", "region_id": "central_plain",
        "stock": {"food_ration_day": 0, "brick_tile_kg": 1000}, "cash_pool": 100_000,
    }
    result = sell_surplus_to_market(
        faction, inventory, market, region_id="central_plain", allowed_items={"food_ration_day"},
    )
    assert result["item_ref"] == "food_ration_day"
    assert result["quantity"] == 400
    assert result["inventory"]["food_ration_days"] == 600
    assert result["inventory"]["raw_materials"]["brick_tile_kg"] == 100
    assert result["faction"]["treasury_cash"] == result["cash_earned"] > 0
    assert result["market"]["cash_pool"] + result["cash_earned"] == market["cash_pool"]


def test_great_tournament_convergence_has_seven_distinct_public_days_and_bounded_pairing():
    profile = tournament_runtime.event_profile("great_jianghu_tournament")
    assert profile["convergence_days_before"] == 7
    themes = [
        tournament_runtime.convergence_day_theme("great_jianghu_tournament", day)
        for day in range(1, 8)
    ]
    assert len(set(themes)) == 7
    assert themes[0] == "delegation_reception_and_formal_greetings"
    assert themes[-1] == "opening_procession_rules_and_final_greetings"

    factions = [f"faction.{idx:03d}" for idx in range(201)]
    pairs = tournament_runtime.convergence_pairs(
        factions,
        tournament_ref="tournament:great_jianghu_tournament:0064-09-01:individual",
        day_index=3,
        contacts_per_faction=1,
    )
    assert len(pairs) == 100
    assert len(set(pairs)) == len(pairs)
    touched = [ref for pair in pairs for ref in pair]
    assert len(touched) == len(set(touched))
    assert pairs == tournament_runtime.convergence_pairs(
        factions,
        tournament_ref="tournament:great_jianghu_tournament:0064-09-01:individual",
        day_index=3,
        contacts_per_faction=1,
    )


def test_great_tournament_social_contacts_can_bootstrap_relations_without_random_alliance_flags():
    neutral = apply_relation_event(None, from_faction="a", to_faction="b", event_kind="tournament_contact")
    assert neutral["trust"] == 1
    assert neutral["respect"] == 2
    assert neutral.get("hostility", 0) == 0

    tense = apply_relation_event(
        {"from_faction": "a", "to_faction": "b", "hostility": 45, "respect": 5},
        from_faction="a", to_faction="b", event_kind="tournament_tension",
    )
    assert tense["hostility"] == 47
    assert tense["respect"] == 6
    assert tense["trust"] == -2


def test_great_tournament_access_and_funding_are_world_scale_not_slot_based():
    config = json.loads((ROOT / "game/data/martial-world/tournaments.json").read_text())
    great = config["event_profiles"]["great_jianghu_tournament"]
    assert great["allows_outlaw_factions"] is True
    assert great["safe_conduct_on_official_grounds"] is True
    assert great["entry_fee_cash"] == 10_000
    assert great["faction_interest_floor_permille"] == 1000
    assert "max_entrants" not in great
    assert "reserved_travel_slots" not in great
    assert "fixed_prize_cash" not in great
    assert "pre_funded_prize_cash" not in great


def test_all_canonical_factions_have_public_political_camps_and_pressure_is_symmetric():
    identities = json.loads((ROOT / "game/data/martial-world/faction-identities.json").read_text())["identities"]
    camps = {faction_ref: faction_camp(faction_ref) for faction_ref in identities}
    assert all(camp in {"orthodox", "unorthodox", "outlaw"} for camp in camps.values())
    assert camps["house_tang"] == "orthodox"
    assert camps["beggars_society"] == "unorthodox"
    assert camps["faction.black_oar_pirates"] == "outlaw"
    assert cross_camp_pressure("orthodox", "unorthodox") == cross_camp_pressure("unorthodox", "orthodox") == 10
    assert cross_camp_pressure("orthodox", "outlaw") == cross_camp_pressure("outlaw", "orthodox") == 25
    assert cross_camp_pressure("unorthodox", "outlaw") == cross_camp_pressure("outlaw", "unorthodox") == 15


def test_conflict_stage_derives_rivalry_feud_and_war_from_real_hostility():
    assert conflict_stage({"hostility": 0}) == "peace"
    assert conflict_stage({"hostility": 30}) == "rivalry"
    assert conflict_stage({"hostility": 45}) == "feud"
    assert conflict_stage({"hostility": 65}) == "war"


def test_great_tournament_spectator_policy_brings_leaders_and_real_member_delegations_without_a_cap():
    profile = tournament_runtime.event_profile("great_jianghu_tournament")
    tournament_ref = "tournament:great_jianghu_tournament:0064-09-01:individual"
    # A major sect is institutionally expected to bring at least the authored
    # delegation floor before marginal interest is even consulted.
    for order in range(profile["major_sect_spectator_delegation_floor"]):
        assert tournament_spectator_interested(
            faction_ref="shaolin", person_ref=f"person.shaolin.spectator.{order}",
            tournament_ref=tournament_ref, tournament_kind="great_jianghu_tournament",
            spectator_order=order, is_leader=False, faction_type="sect", living_members=150,
            spectator_delegation_floor=profile["spectator_delegation_floor"],
            major_spectator_population_threshold=profile["major_spectator_population_threshold"],
            major_spectator_delegation_floor=profile["major_spectator_delegation_floor"],
            major_sect_spectator_delegation_floor=profile["major_sect_spectator_delegation_floor"],
            leader_attendance_permille=profile["leader_attendance_permille"],
            spectator_marginal_interest_permille=profile["spectator_marginal_interest_permille"],
            spectator_marginal_decay_permille=profile["spectator_marginal_decay_permille"],
        )
    assert tournament_spectator_interested(
        faction_ref="shaolin", person_ref="person.shaolin.leader",
        tournament_ref=tournament_ref, tournament_kind="great_jianghu_tournament",
        spectator_order=100, is_leader=True, faction_type="sect", living_members=150,
        spectator_delegation_floor=profile["spectator_delegation_floor"],
        major_spectator_population_threshold=profile["major_spectator_population_threshold"],
        major_spectator_delegation_floor=profile["major_spectator_delegation_floor"],
        major_sect_spectator_delegation_floor=profile["major_sect_spectator_delegation_floor"],
        leader_attendance_permille=profile["leader_attendance_permille"],
        spectator_marginal_interest_permille=profile["spectator_marginal_interest_permille"],
        spectator_marginal_decay_permille=profile["spectator_marginal_decay_permille"],
    )
    config = json.loads((ROOT / "game/data/martial-world/tournaments.json").read_text())
    great = config["event_profiles"]["great_jianghu_tournament"]
    assert "max_spectators" not in great
    assert "max_delegation_size" not in great


def test_tournament_faction_performance_table_reports_depth_without_becoming_an_entrant_cap():
    owner_map = {
        "a.1": "faction.a", "a.2": "faction.a", "a.3": "faction.a",
        "b.1": "faction.b",
        "c.1": "faction.c", "c.2": "faction.c",
    }
    rows = tournament_runtime.faction_performance_standings(
        {"faction.a": 4, "faction.b": 3, "faction.c": 3}, owner_map, limit=8,
    )
    assert [row["faction_ref"] for row in rows] == ["faction.a", "faction.b", "faction.c"]
    assert rows[0] == {
        "faction_ref": "faction.a", "match_wins": 4, "entrant_count": 3,
        "wins_per_entrant_milli": 1333,
    }
    assert rows[1]["wins_per_entrant_milli"] == 3000
    assert tournament_runtime.faction_performance_standings({"faction.a": 4}, owner_map, limit=0) == []


def test_tournament_presence_merges_leader_competitors_with_spectator_delegation_idempotently():
    tournament = {
        "event_id": "tournament:great_jianghu_tournament:0064-09-01:individual",
        "delegations": {
            "diancang": {
                "faction_ref": "diancang",
                "entrant_refs": [],
                "spectator_refs": ["diancang.elder", "diancang.physician"],
                "leader_refs": [],
                "senior_refs": ["diancang.elder"],
                "present_count": 2,
            }
        },
    }
    after = tournament_runtime.merge_delegation_presence(
        tournament, faction_ref="diancang", camp="orthodox",
        entrant_refs=["diancang.sect_master"],
        leader_refs=["diancang.sect_master"],
        senior_refs=["diancang.sect_master"],
    )
    # Replaying the same physical arrival is idempotent and does not count the
    # sect master twice merely because leader and entrant are two roles.
    replay = tournament_runtime.merge_delegation_presence(
        after, faction_ref="diancang", camp="orthodox",
        entrant_refs=["diancang.sect_master"],
        leader_refs=["diancang.sect_master"],
        senior_refs=["diancang.sect_master"],
    )
    row = replay["delegations"]["diancang"]
    assert row["entrant_refs"] == ["diancang.sect_master"]
    assert row["leader_refs"] == ["diancang.sect_master"]
    assert row["senior_refs"] == ["diancang.elder", "diancang.sect_master"]
    assert row["spectator_refs"] == ["diancang.elder", "diancang.physician"]
    assert row["present_count"] == 3
    assert row["camp"] == "orthodox"


def test_great_tournament_program_pairs_senior_blocs_and_existing_feuds_without_all_pairs_growth():
    factions = ["orth.a", "orth.b", "unorth.a", "unorth.b", "out.a", "out.b"]
    camps = {
        "orth.a": "orthodox", "orth.b": "orthodox",
        "unorth.a": "unorthodox", "unorth.b": "unorthodox",
        "out.a": "outlaw", "out.b": "outlaw",
    }
    senior = list(factions)
    assembly = tournament_runtime.themed_convergence_pairs(
        factions, tournament_ref="great:64", day_index=4,
        tournament_kind="great_jianghu_tournament", theme="senior_faction_assembly",
        contacts_per_faction=1, senior_faction_refs=senior, camp_by_faction=camps,
    )
    assert set(assembly) == {("orth.a", "orth.b"), ("out.a", "out.b"), ("unorth.a", "unorth.b")}

    negotiations = tournament_runtime.themed_convergence_pairs(
        factions, tournament_ref="great:64", day_index=6,
        tournament_kind="great_jianghu_tournament", theme="private_negotiations_and_rivalry_mediation",
        contacts_per_faction=1, senior_faction_refs=senior, camp_by_faction=camps,
        hostility_by_pair={("orth.a", "out.a"): 70, ("orth.b", "unorth.b"): 45},
    )
    assert ("orth.a", "out.a") in negotiations
    assert ("orth.b", "unorth.b") in negotiations
    touched = [ref for pair in negotiations for ref in pair]
    assert len(touched) == len(set(touched))
    assert len(negotiations) <= len(factions) // 2


def test_regional_tournament_travel_interest_declines_with_real_route_burden_without_a_hard_distance_cap():
    profile = tournament_runtime.event_profile("regional_martial_tournament")
    nearby = sum(
        tournament_travel_interested(
            faction_ref=f"near.{idx}", tournament_ref="regional:62",
            tournament_kind="regional_martial_tournament", training_priority=50, risk_tolerance=50,
            entry_fee_cash=profile["entry_fee_cash"], current_prize_cash=100_000,
            prestige_weight=profile["prestige_weight"], travel_days_hint=5,
        )
        for idx in range(500)
    )
    distant = sum(
        tournament_travel_interested(
            faction_ref=f"far.{idx}", tournament_ref="regional:62",
            tournament_kind="regional_martial_tournament", training_priority=50, risk_tolerance=50,
            entry_fee_cash=profile["entry_fee_cash"], current_prize_cash=100_000,
            prestige_weight=profile["prestige_weight"], travel_days_hint=55,
        )
        for idx in range(500)
    )
    assert nearby > distant > 0


def test_great_tournament_registration_frontier_wires_real_competitor_and_delegate_departures():
    overlay = {}

    def read_json(rel):
        if rel in overlay:
            return json.loads(json.dumps(overlay[rel]))
        return json.loads((ROOT / rel).read_text())

    def absorb(result):
        for rel, doc in result.get("writes", {}).items():
            overlay[rel] = json.loads(json.dumps(doc))

    schedule = read_json("state/martial-world/scheduler.json")
    at = datetime(63, 12, 28, 9, 0, 0)
    event = {
        "event_id": "test:great-registration-production-frontier",
        "kind": "tournament_registration_open",
        "due_at": at.isoformat(),
        "tournament_kind": "great_jianghu_tournament",
        "competition_date": "0064-09-01",
        "registration_closes_on": "0064-08-24",
        "host_place_id": "luoyang",
        "owner_ref": "luoyang",
    }
    result = settle_martial_world_frontier(
        read_json=read_json, schedule=schedule, events=[event], at=at,
    )
    absorb(result)
    review = next(row for row in result["reviews"] if row.get("kind") == "tournament_registration_open")
    assert review["faction_attendance_attempts"] == 240
    assert review["spectator_delegations_planned"] == 240
    assert review["delegation_failure_counts"] == {}
    assert review["traveling_entrants_planned"] >= 400

    one_off = result["schedule_after"].get("one_off", {})
    competitor_departures = [
        row for row in one_off.values() if row.get("kind") == "tournament_trip_departure"
    ]
    delegation_departures = [
        row for row in one_off.values() if row.get("kind") == "tournament_delegation_departure"
    ]
    assert len(competitor_departures) == review["traveling_entrants_planned"]
    assert len(delegation_departures) == 240
    assert all(int(row.get("minimum_host_days", 0)) >= 10 for row in competitor_departures)
    assert all(int(row.get("minimum_host_days", 0)) >= 10 for row in delegation_departures)
    assert all(int(row.get("delegate_ticket_cash_per_day", 0)) > 0 for row in delegation_departures)

    tref = "tournament:great_jianghu_tournament:0064-09-01:individual"
    tournament = overlay["state/martial-world/tournaments.json"]["tournaments"][tref]
    local_count = review["local_paid_registrations"]
    assert tournament["prize_escrow_cash"] == local_count * tournament["entry_fee_cash"]
    assert "host_operations_cash_collected" not in tournament

    # Continue through the real close -> convergence production path. Public
    # civilian spectators remain aggregate: ticket cash leaves the host
    # regional market and enters prize escrow exactly once for that event day.
    close_at = datetime(64, 8, 24, 18, 0, 0)
    close_event = {
        "event_id": "test:great-registration-close-production-frontier",
        "kind": "tournament_registration_close",
        "due_at": close_at.isoformat(),
        "tournament_kind": "great_jianghu_tournament",
        "competition_date": "0064-09-01",
        "host_place_id": "luoyang",
        "owner_ref": "luoyang",
    }
    closed = settle_martial_world_frontier(
        read_json=read_json, schedule=result["schedule_after"], events=[close_event], at=close_at,
    )
    absorb(closed)
    tournament = overlay["state/martial-world/tournaments.json"]["tournaments"][tref]
    assert tournament["status"] == "bracket_ready"
    market_path = f"state/martial-world/markets/{tournament['host_region']}.json"
    market_cash_before = int(read_json(market_path)["cash_pool"])
    prize_before = int(tournament["prize_escrow_cash"])

    convergence_at = datetime(64, 8, 25, 9, 0, 0)
    convergence_event = {
        "event_id": "test:great-convergence-production-frontier",
        "kind": "tournament_convergence_day",
        "due_at": convergence_at.isoformat(),
        "tournament_kind": "great_jianghu_tournament",
        "competition_date": "0064-09-01",
        "host_place_id": "luoyang",
        "owner_ref": "luoyang",
        "convergence_day_index": 1,
        "convergence_day_count": 7,
    }
    converged = settle_martial_world_frontier(
        read_json=read_json, schedule=closed["schedule_after"],
        events=[convergence_event], at=convergence_at,
    )
    absorb(converged)
    convergence_review = next(
        row for row in converged["reviews"] if row.get("kind") == "tournament_convergence_day"
    )
    ticket_cash = int(convergence_review["public_ticket_cash"])
    assert ticket_cash > 0
    tournament_after = overlay["state/martial-world/tournaments.json"]["tournaments"][tref]
    market_cash_after = int(overlay[market_path]["cash_pool"])
    assert market_cash_before - market_cash_after == ticket_cash
    assert int(tournament_after["prize_escrow_cash"]) - prize_before == ticket_cash

    repeated = settle_martial_world_frontier(
        read_json=read_json, schedule=converged["schedule_after"],
        events=[dict(convergence_event, event_id="test:great-convergence-production-frontier-repeat")],
        at=convergence_at,
    )
    absorb(repeated)
    assert int(overlay[market_path]["cash_pool"]) == market_cash_after
    assert int(overlay["state/martial-world/tournaments.json"]["tournaments"][tref]["prize_escrow_cash"]) == int(tournament_after["prize_escrow_cash"])

