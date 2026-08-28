from shinobi_runtime.martial_world.autonomous_factions import liquidate_inventory_to_market


def test_criminal_loot_sale_moves_real_goods_and_real_market_cash():
    faction = {"population": 10, "treasury_cash": 1000, "autonomy_policy": {"financial_caution": 50}}
    inventory = {"food_ration_days": 0, "raw_materials": {"metal_kg": 20}, "equipment": {}}
    market = {"region_id": "central_plain", "stock": {"metal_kg": 1000}, "cash_pool": 100000}
    before_total_cash = faction["treasury_cash"] + market["cash_pool"]
    before_goods = inventory["raw_materials"]["metal_kg"] + market["stock"]["metal_kg"]
    result = liquidate_inventory_to_market(
        faction, inventory, market, region_id="central_plain", max_trade_value_cash=100000,
    )
    assert result["quantity"] > 0
    assert result["cash_earned"] > 0
    assert result["faction"]["treasury_cash"] + result["market"]["cash_pool"] == before_total_cash
    assert result["inventory"]["raw_materials"].get("metal_kg", 0) + result["market"]["stock"]["metal_kg"] == before_goods


def test_criminal_loot_sale_is_limited_by_market_cash():
    faction = {"population": 10, "treasury_cash": 0}
    inventory = {"food_ration_days": 0, "raw_materials": {"metal_kg": 100}, "equipment": {}}
    market = {"region_id": "central_plain", "stock": {"metal_kg": 1000}, "cash_pool": 1}
    result = liquidate_inventory_to_market(
        faction, inventory, market, region_id="central_plain", max_trade_value_cash=100000,
    )
    assert result["quantity"] == 0
    assert result["cash_earned"] == 0
