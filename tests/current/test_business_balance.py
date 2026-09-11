import json
from pathlib import Path

import pytest

from shinobi_runtime.martial_world.compensation import monthly_stipend
from shinobi_runtime.martial_world.enterprise_operations import (
    operate_apothecary_month,
    operate_brotherhood_livelihood_month,
    operate_criminal_enterprise_month,
    operate_workshop_month,
)
from shinobi_runtime.martial_world.escort import plan_escort_objective, quote_escort_objective
from shinobi_runtime.martial_world.upkeep import monthly_upkeep_quote

ROOT = Path(__file__).resolve().parents[2]


def _market(cash=1_000_000):
    return {"cash_pool": cash, "stock": {}}


def _route():
    return {
        "id": "route.balance",
        "from": "a",
        "to": "b",
        "distance_km": 120,
        "terrain": "hills",
        "road_quality": "maintained",
        "allowed_modes": ["convoy"],
        "toll_cash": 20,
    }


def _travel():
    return {
        "mode_speed_km_per_day": {"convoy": 24},
        "terrain_time_milli": {"hills": 1150},
        "road_time_milli": {"maintained": 1000},
    }


def test_brotherhood_income_is_real_worker_time_and_finite_market_cash():
    op = operate_brotherhood_livelihood_month(
        _market(), worker_count=1, average_commerce=60, general_labor_cash_per_hour=30,
    )
    assert op["labor_hours"] == 100
    assert 0 < op["cash_earned"] <= 2 * 30 * op["labor_hours"]
    assert op["market"]["cash_pool"] == 1_000_000 - op["cash_earned"]


def test_criminal_premium_comes_from_risk_not_hidden_full_time_crew():
    op = operate_criminal_enterprise_month(
        _market(), enterprise_level=5, registered_ventures=1, worker_count=1,
        average_commerce=100, risk_tolerance=100, general_labor_cash_per_hour=30,
    )
    assert op["labor_hours"] == 100
    assert 0 < op["cash_earned"] <= 2 * 30 * op["labor_hours"]
    assert op["market"]["cash_pool"] == 1_000_000 - op["cash_earned"]


def test_workshop_and_apothecary_levels_do_not_create_output_without_inputs():
    workshop = operate_workshop_month(
        {"raw_materials": {}, "equipment": {}}, _market(), region_id="central_plain",
        recipe_ref="jian", workshop_level=5, crafting_skill=100,
        available_worker_hours=1000, reserve_quantity=0, max_batches=100,
    )
    assert workshop["batches"] == 0 and workshop["cash_earned"] == 0

    apothecary = operate_apothecary_month(
        {"herbs": {}, "medicines": {}}, _market(), recipe_ref="stamina_tonic",
        apothecary_level=5, medicine_skill=100, available_worker_hours=1000,
        reserve_doses=0, max_batches=100,
    )
    assert apothecary["batches"] == 0 and apothecary["cash_earned"] == 0


def test_typical_escort_is_institutional_scale_not_regional_wealth_percentage():
    objective = plan_escort_objective(
        kind="escort_shipment", route=_route(), travel=_travel(),
        source_place_ref="a", destination_place_ref="b",
        item_ref="food_ration_day", quantity=12_000, cargo_value_cash=324_000,
    )
    quote = quote_escort_objective(objective)
    reward = quote["total_reward_cash"]
    escort_hours = objective["minimum_escort_count"] * objective["expected_travel_hours"]
    assert 5_000 <= reward <= 50_000
    assert reward // max(1, escort_hours) <= 60
    assert quote["cargo_liability_cash"] <= objective["cargo_value_cash"] // 500


def test_member_cash_is_a_stipend_because_living_support_is_already_in_kind():
    compensation = json.loads((ROOT / "game/data/martial-world/compensation.json").read_text())
    elite = {"membership_grade": "elite", "standing_offices": []}
    stipend = monthly_stipend(elite)
    market_month = 30 * 8 * 30  # general labor: cash/hour * hours/day * days/month
    assert stipend == 300
    assert stipend < market_month // 10
    assert "allowance" in compensation["stipend_rule"].lower()
    upkeep = monthly_upkeep_quote({"population": 1, "buildings": {}, "enterprises": {}})
    assert upkeep["food_ration_days"] == 30
    assert upkeep["household_cash"] > 0


def test_enterprise_levels_describe_efficiency_and_scale_not_passive_cash_multipliers():
    data = json.loads((ROOT / "game/data/martial-world/enterprises.json").read_text())
    rows = data["finite_types"]
    for enterprise, spec in rows.items():
        levels = spec.get("levels", {})
        for level, row in levels.items():
            assert "passive_revenue_cash" not in row, (enterprise, level)
            assert "monthly_free_cash" not in row, (enterprise, level)
            assert int(row.get("operating_efficiency_milli", 1000)) <= 1500


def test_escort_policy_contains_no_maximum_headcount_or_cargo_cap():
    data = json.loads((ROOT / "game/data/martial-world/contracts.json").read_text())
    escort = data["finite_types"]["escort"]
    assert "merchant_convoy_max_cargo_mass_kg" not in escort
    policy = escort["escort_count_policy"]
    assert not any("maximum" in key or "max_escort" in key for key in policy)


def test_workshop_sale_fails_closed_when_market_pricing_authority_is_invalid():
    inventory = {
        "raw_materials": {"metal_kg": 2, "hardwood_kg": 1, "leather_kg": 1, "charcoal_kg": 12},
        "equipment": {},
    }
    market = {"region_id": "invalid_region", "cash_pool": 1_000_000, "stock": {"weapon_jian": 1}}
    with pytest.raises(KeyError):
        operate_workshop_month(
            inventory, market, region_id="invalid_region", recipe_ref="jian", workshop_level=5,
            crafting_skill=100, available_worker_hours=1000, reserve_quantity=0, max_batches=1,
        )
