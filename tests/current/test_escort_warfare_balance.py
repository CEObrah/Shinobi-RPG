from shinobi_runtime.martial_world.commitments import extend_commitment_resources
from shinobi_runtime.martial_world.escort import (
    minimum_martial_escorts,
    ordinary_public_lot_quantity,
    plan_escort_objective,
    quote_escort_objective,
    route_transport_plan,
)
from shinobi_runtime.martial_world.money import format_copper
from shinobi_runtime.martial_world.warfare import local_frontage_count


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
        source_region="temperate_mountain", destination_region="central_plain",
        item_ref="food_ration_day", quantity=12_000, cargo_value_cash=324_000,
    )
    assert objective["cargo_mass_kg"] == 12_000
    assert objective["wagon_count"] == 10
    assert objective["draft_animal_count"] == 20
    assert objective["civilian_crew_count"] > 0
    assert objective["minimum_escort_count"] >= 2
    assert quote_escort_objective(objective)["total_reward_cash"] < 50_000


def test_public_lot_target_is_not_a_world_convoy_or_escort_cap():
    transport = route_transport_plan(cargo_kg=1_200_000, route=_route())
    assert transport["wagon_count"] == 1000
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
        source_region="temperate_mountain", destination_region="central_plain",
        protected_people_count=20, civilian_party_kind="pilgrims",
    )
    assert objective["escort_kind"] == "person"
    assert objective["protected_people_count"] == 20
    assert objective["cargo_mass_kg"] == 0
    assert objective["wagon_count"] == 0
    assert objective["pack_animal_count"] == 0
    assert objective["minimum_escort_count"] > 2


def test_mixed_convoy_carries_both_people_and_goods():
    objective = plan_escort_objective(
        kind="escort_mixed_convoy",
        route=_route(), travel=_travel(),
        source_region="temperate_mountain", destination_region="central_plain",
        item_ref="food_ration_day", quantity=6000, cargo_value_cash=162_000,
        protected_people_count=5, civilian_party_kind="merchant_principals",
    )
    assert objective["escort_kind"] == "mixed"
    assert objective["protected_people_count"] == 5
    assert objective["cargo_mass_kg"] == 6000
    assert objective["wagon_count"] == 5


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
