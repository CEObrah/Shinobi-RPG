"""Conserved autonomous faction actions for the Jianghu monthly frontier.

The scheduler may decide *that* an institution needs food, cash or recruitment,
but this module owns the small pure reducers that turn those priorities into
conserved current-state changes.  There are no decision-history ledgers here:
callers persist only the resulting faction/inventory/market/person facts.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .regional_economy import execute_purchase, execute_sale, unit_market_price_cash

_MARKET_RAW_ITEMS = frozenset({
    "metal_kg", "timber_kg", "hardwood_kg", "leather_kg", "cloth_m",
    "charcoal_kg", "rope_m", "stone_kg", "brick_tile_kg", "lime_kg",
})


def membership_recruitment_gap(faction: Mapping[str, Any]) -> int:
    """Return new members needed to restore the faction's configured membership target."""
    policy = faction.get("recruitment_policy", {}) if isinstance(faction.get("recruitment_policy"), Mapping) else {}
    target = max(0, int(policy.get("target_membership", 0)))
    population = max(0, int(faction.get("population", 0)))
    return max(0, target - population)


def seasonal_recruitment_capacity(faction: Mapping[str, Any], *, season_id: str) -> int:
    policy = faction.get("recruitment_policy", {}) if isinstance(faction.get("recruitment_policy"), Mapping) else {}
    maximum = max(0, int(policy.get("maximum_intake_per_season", 0)))
    season = faction.get("recruitment_season", {}) if isinstance(faction.get("recruitment_season"), Mapping) else {}
    used = max(0, int(season.get("intake_used", 0))) if season.get("season_id") == season_id else 0
    return max(0, maximum - used)


def monthly_recruitment_tranche(
    faction: Mapping[str, Any], *, season_id: str, months_left_in_season: int, desired_gap: int | None = None,
) -> int:
    gap = membership_recruitment_gap(faction) if desired_gap is None else max(0, int(desired_gap))
    remaining = min(gap, seasonal_recruitment_capacity(faction, season_id=season_id))
    if remaining <= 0:
        return 0
    months = max(1, int(months_left_in_season))
    return max(1, (remaining + months - 1) // months)


def secure_food_purchase(
    faction: Mapping[str, Any], inventory: Mapping[str, Any], market: Mapping[str, Any], *,
    region_id: str, target_reserve_days: int = 60,
) -> dict[str, Any]:
    """Buy conserved food from the local regional market up to a reserve target."""
    faction_after = copy.deepcopy(dict(faction))
    inventory_after = copy.deepcopy(dict(inventory))
    market_after = copy.deepcopy(dict(market))
    population = max(1, int(faction_after.get("population", 0)))
    target = population * max(1, int(target_reserve_days))
    before = max(0, int(inventory_after.get("food_ration_days", 0)))
    need = max(0, target - before)
    stock = market_after.get("stock", {}) if isinstance(market_after.get("stock"), Mapping) else {}
    available = max(0, int(stock.get("food_ration_day", 0)))
    cash = max(0, int(faction_after.get("treasury_cash", 0)))
    if need <= 0:
        return {"faction": faction_after, "inventory": inventory_after, "market": market_after, "quantity": 0, "cash_spent": 0, "reason": "reserve_sufficient"}
    if available <= 0 or cash <= 0:
        return {"faction": faction_after, "inventory": inventory_after, "market": market_after, "quantity": 0, "cash_spent": 0, "reason": "market_or_cash_unavailable"}
    try:
        unit = unit_market_price_cash(region_id, "food_ration_day", stock)
    except (KeyError, TypeError, ValueError):
        return {"faction": faction_after, "inventory": inventory_after, "market": market_after, "quantity": 0, "cash_spent": 0, "reason": "food_not_traded"}
    quantity = min(need, available, cash // max(1, unit))
    if quantity <= 0:
        return {"faction": faction_after, "inventory": inventory_after, "market": market_after, "quantity": 0, "cash_spent": 0, "reason": "insufficient_cash"}
    result = execute_purchase(region_id, "food_ration_day", quantity, market_after, buyer_cash=cash)
    faction_after["treasury_cash"] = int(result["buyer_cash_after"])
    inventory_after["food_ration_days"] = before + quantity
    return {
        "faction": faction_after, "inventory": inventory_after,
        "market": result["market_state_after"], "quantity": quantity,
        "cash_spent": int(result["quote"]["total_price_cash"]), "reason": "purchased",
    }


def procure_project_materials(
    faction: Mapping[str, Any], inventory: Mapping[str, Any], market: Mapping[str, Any], *,
    region_id: str, required_materials: Mapping[str, Any],
) -> dict[str, Any]:
    """Buy only missing construction materials from the real regional market.

    Construction recipes are physical requirements, but factions should not be
    forced to have every brick, tile, or sack of lime in their seed inventory.
    A solvent institution may procure the exact deficit from finite regional
    stock at the same scarcity-aware prices used by ordinary market purchases.
    The reducer is transactional: callers receive after-images only after every
    required deficit can be funded and supplied.
    """
    faction_after = copy.deepcopy(dict(faction))
    inventory_after = copy.deepcopy(dict(inventory))
    market_after = copy.deepcopy(dict(market))
    raw = inventory_after.setdefault("raw_materials", {})
    if not isinstance(raw, dict):
        raise ValueError("jianghu raw-material inventory invalid")
    cash = max(0, int(faction_after.get("treasury_cash", 0)))
    purchased: dict[str, int] = {}
    cash_spent = 0
    for item_ref in sorted(str(k) for k in required_materials):
        required = max(0, int(required_materials.get(item_ref, 0)))
        have = max(0, int(raw.get(item_ref, 0)))
        deficit = max(0, required - have)
        if deficit <= 0:
            continue
        if item_ref not in _MARKET_RAW_ITEMS:
            raise ValueError(f"project material is not regionally traded:{item_ref}")
        result = execute_purchase(region_id, item_ref, deficit, market_after, buyer_cash=cash)
        spent = int(result["quote"]["total_price_cash"])
        cash = int(result["buyer_cash_after"])
        cash_spent += spent
        market_after = copy.deepcopy(dict(result["market_state_after"]))
        raw[item_ref] = have + deficit
        purchased[item_ref] = deficit
    faction_after["treasury_cash"] = cash
    return {
        "faction": faction_after,
        "inventory": inventory_after,
        "market": market_after,
        "purchased": purchased,
        "cash_spent": cash_spent,
    }


def _sale_stock(inventory: Mapping[str, Any], item_ref: str) -> int:
    if item_ref == "food_ration_day":
        return max(0, int(inventory.get("food_ration_days", 0)))
    raw = inventory.get("raw_materials", {}) if isinstance(inventory.get("raw_materials"), Mapping) else {}
    return max(0, int(raw.get(item_ref, 0)))


def _set_sale_stock(inventory: dict[str, Any], item_ref: str, quantity: int) -> None:
    if item_ref == "food_ration_day":
        inventory["food_ration_days"] = max(0, int(quantity)); return
    raw = inventory.setdefault("raw_materials", {})
    if not isinstance(raw, dict):
        raise ValueError("jianghu raw-material inventory invalid")
    if quantity > 0:
        raw[item_ref] = int(quantity)
    else:
        raw.pop(item_ref, None)



def sell_surplus_to_market(
    faction: Mapping[str, Any], inventory: Mapping[str, Any], market: Mapping[str, Any], *,
    region_id: str, shortage_only: bool = False, max_trade_value_cash: int | None = None,
    allowed_items: set[str] | None = None,
) -> dict[str, Any]:
    """Sell one bounded surplus lot into the real market cash/stock authority.

    Food keeps sixty faction-days.  ``allowed_items`` can narrow a livelihood to
    its own lawful outputs, such as agriculture selling grain without becoming a
    general merchant house.  Raw materials are never liquidated wholesale:
    financial caution controls a bounded monthly fraction, leaving the majority
    of current stock in institutional custody for construction/repair/production.
    """
    faction_after = copy.deepcopy(dict(faction))
    inventory_after = copy.deepcopy(dict(inventory))
    market_after = copy.deepcopy(dict(market))
    stock = market_after.get("stock", {}) if isinstance(market_after.get("stock"), Mapping) else {}
    cash_pool = max(0, int(market_after.get("cash_pool", 0)))
    value_cap = None if max_trade_value_cash is None else max(0, int(max_trade_value_cash))
    if cash_pool <= 0:
        return {"faction": faction_after, "inventory": inventory_after, "market": market_after, "quantity": 0, "cash_earned": 0, "reason": "market_cash_unavailable"}
    population = max(1, int(faction_after.get("population", 0)))
    policy = faction_after.get("autonomy_policy", {}) if isinstance(faction_after.get("autonomy_policy"), Mapping) else {}
    caution = max(0, min(100, int(policy.get("financial_caution", 50))))
    sale_fraction_milli = max(50, min(300, 300 - caution * 2))
    candidates: list[tuple[int, str, int]] = []
    tradable = _MARKET_RAW_ITEMS | {"food_ration_day"}
    if allowed_items is not None:
        tradable &= {str(x) for x in allowed_items}
    for item_ref in sorted(set(str(x) for x in stock) & tradable):
        current = _sale_stock(inventory_after, item_ref)
        if current <= 0:
            continue
        if shortage_only and int(stock.get(item_ref, 0)) > 0:
            continue
        if item_ref == "food_ration_day":
            surplus = max(0, current - population * 60)
        else:
            # Preserve at least 75% of current material stock.  The bounded
            # sale fraction can be smaller for cautious institutions.
            surplus = min(current // 4, current * sale_fraction_milli // 1000)
        if surplus <= 0:
            continue
        try:
            unit = max(1, unit_market_price_cash(region_id, item_ref, stock) * 950 // 1000)
        except (KeyError, TypeError, ValueError):
            continue
        affordable = cash_pool // unit
        throughput = surplus if value_cap is None else value_cap // unit
        quantity = min(surplus, affordable, throughput)
        if quantity <= 0:
            continue
        candidates.append((unit * quantity, item_ref, quantity))
    if not candidates:
        return {"faction": faction_after, "inventory": inventory_after, "market": market_after, "quantity": 0, "cash_earned": 0, "reason": "no_lawful_surplus"}
    candidates.sort(key=lambda row: (-row[0], row[1]))
    _value, item_ref, quantity = candidates[0]
    current = _sale_stock(inventory_after, item_ref)
    result = execute_sale(
        region_id, item_ref, quantity, market_after,
        seller_stock=current, seller_cash=max(0, int(faction_after.get("treasury_cash", 0))),
    )
    _set_sale_stock(inventory_after, item_ref, int(result["seller_stock_after"]))
    faction_after["treasury_cash"] = int(result["seller_cash_after"])
    return {
        "faction": faction_after, "inventory": inventory_after,
        "market": result["market_state_after"], "item_ref": item_ref,
        "quantity": quantity, "cash_earned": int(result["quote"]["total_price_cash"]), "reason": "sold",
    }


def _inventory_market_stock(inventory: Mapping[str, Any], item_ref: str) -> tuple[str, int]:
    if item_ref == "food_ration_day":
        return "food_ration_days", max(0, int(inventory.get("food_ration_days", 0)))
    for bucket in ("raw_materials", "equipment", "medicines", "herbs"):
        rows = inventory.get(bucket, {}) if isinstance(inventory.get(bucket), Mapping) else {}
        if item_ref in rows:
            return bucket, max(0, int(rows.get(item_ref, 0)))
    return "", 0


def _set_inventory_market_stock(inventory: dict[str, Any], location: str, item_ref: str, quantity: int) -> None:
    qty = max(0, int(quantity))
    if location == "food_ration_days":
        inventory["food_ration_days"] = qty
        return
    rows = inventory.setdefault(location, {})
    if not isinstance(rows, dict):
        raise ValueError("jianghu inventory bucket invalid")
    if qty > 0:
        rows[item_ref] = qty
    else:
        rows.pop(item_ref, None)


def liquidate_inventory_to_market(
    faction: Mapping[str, Any], inventory: Mapping[str, Any], market: Mapping[str, Any], *,
    region_id: str, max_trade_value_cash: int, maximum_fraction_milli: int = 500,
) -> dict[str, Any]:
    """Fence one bounded inventory lot through the finite regional market.

    This is the criminal-faction counterpart to lawful merchant surplus sales.
    Goods remain real faction inventory until sold; sale proceeds come only from
    the market cash pool.  The reducer does not care how custody was obtained,
    so robbed cargo, seized equipment and ordinary surplus obey the same economy.
    """
    faction_after = copy.deepcopy(dict(faction))
    inventory_after = copy.deepcopy(dict(inventory))
    market_after = copy.deepcopy(dict(market))
    market_stock = market_after.get("stock", {}) if isinstance(market_after.get("stock"), Mapping) else {}
    cash_pool = max(0, int(market_after.get("cash_pool", 0)))
    value_cap = max(0, int(max_trade_value_cash))
    fraction = max(50, min(1000, int(maximum_fraction_milli)))
    if cash_pool <= 0 or value_cap <= 0:
        return {"faction": faction_after, "inventory": inventory_after, "market": market_after, "quantity": 0, "cash_earned": 0, "reason": "market_or_fence_capacity_unavailable"}

    candidates: list[tuple[int, str, str, int]] = []
    population = max(1, int(faction_after.get("population", 0)))
    for item_ref in sorted(str(x) for x in market_stock):
        location, current = _inventory_market_stock(inventory_after, item_ref)
        if not location or current <= 0:
            continue
        if item_ref == "food_ration_day":
            sellable = max(0, current - population * 30)
        else:
            sellable = max(0, current * fraction // 1000)
            if sellable <= 0 and current > 1:
                sellable = 1
        if sellable <= 0:
            continue
        try:
            unit = max(1, unit_market_price_cash(region_id, item_ref, market_stock) * 950 // 1000)
        except (KeyError, TypeError, ValueError):
            continue
        quantity = min(sellable, cash_pool // unit, value_cap // unit)
        if quantity <= 0:
            continue
        candidates.append((unit * quantity, item_ref, location, quantity))
    if not candidates:
        return {"faction": faction_after, "inventory": inventory_after, "market": market_after, "quantity": 0, "cash_earned": 0, "reason": "no_market_absorbable_inventory"}

    candidates.sort(key=lambda row: (-row[0], row[1]))
    _value, item_ref, location, quantity = candidates[0]
    _loc, current = _inventory_market_stock(inventory_after, item_ref)
    result = execute_sale(
        region_id, item_ref, quantity, market_after,
        seller_stock=current, seller_cash=max(0, int(faction_after.get("treasury_cash", 0))),
    )
    _set_inventory_market_stock(inventory_after, location, item_ref, int(result["seller_stock_after"]))
    faction_after["treasury_cash"] = int(result["seller_cash_after"])
    return {
        "faction": faction_after, "inventory": inventory_after, "market": result["market_state_after"],
        "item_ref": item_ref, "quantity": quantity,
        "cash_earned": int(result["quote"]["total_price_cash"]), "reason": "sold",
    }


__all__ = [
    "membership_recruitment_gap", "monthly_recruitment_tranche", "seasonal_recruitment_capacity",
    "procure_project_materials", "secure_food_purchase", "sell_surplus_to_market", "liquidate_inventory_to_market",
]
