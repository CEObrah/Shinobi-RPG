from __future__ import annotations

import copy
import json
from pathlib import Path

from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


def _planner() -> RepositoryCommandPlanner:
    return RepositoryCommandPlanner(RepositoryStore(ROOT))


def _formation(force_slug: str, formation_id: str) -> dict:
    record = json.loads((ROOT / f"state/formation/{force_slug}.json").read_text())
    return next(copy.deepcopy(row) for row in record["formations"] if row["id"] == formation_id)


def test_aggregate_battle_consumes_real_method_ammunition_and_sustainment() -> None:
    planner = _planner()
    mechanics = json.loads((ROOT / "game/data/mechanics/formation-resolution.json").read_text())
    formation = _formation("force-iron-samurai", "formation.iron.samurai.1")
    stock = json.loads((ROOT / "state/stock/iron-samurai.json").read_text())
    before = copy.deepcopy(stock)

    adjusted, usage = planner._prepare_aggregate_combat_supply(
        formation=formation,
        action="attack",
        range_band=2,
        aggregate_count=formation["personnel_total"],
        stock=stock,
        mechanics=mechanics,
        exchanges=2,
    )

    assert usage["selected_method_counts"].get("bow", 0) > 0
    assert usage["demand"].get("arrows", 0) > 0
    assert usage["consumed"].get("arrows", 0) > 0
    assert usage["demand"].get("rations_days", 0) > 0
    assert usage["demand"].get("water_liters", 0) > 0
    assert stock["arrows"] == before["arrows"] - usage["consumed"]["arrows"]
    assert stock["rations_days"] == before["rations_days"] - usage["consumed"]["rations_days"]
    assert stock["water_liters"] == before["water_liters"] - usage["consumed"]["water_liters"]
    assert usage["sustainment_state"] == "supported"
    assert adjusted["components"] != []


def test_aggregate_battle_supply_shortage_never_mints_ammunition_or_food() -> None:
    planner = _planner()
    mechanics = json.loads((ROOT / "game/data/mechanics/formation-resolution.json").read_text())
    formation = _formation("force-iron-samurai", "formation.iron.samurai.1")
    stock = json.loads((ROOT / "state/stock/iron-samurai.json").read_text())
    stock["arrows"] = 0
    stock["rations_days"] = 0
    stock["water_liters"] = 0

    adjusted, usage = planner._prepare_aggregate_combat_supply(
        formation=formation,
        action="attack",
        range_band=2,
        aggregate_count=formation["personnel_total"],
        stock=stock,
        mechanics=mechanics,
        exchanges=2,
    )

    assert usage["demand"].get("arrows", 0) > 0
    assert usage["consumed"].get("arrows", 0) == 0
    assert usage["method_supply_milli"].get("bow") == 0
    assert usage["sustainment_supply_milli"] == 0
    assert usage["sustainment_state"] == "cut_off"
    assert stock["arrows"] == 0
    assert stock["rations_days"] == 0
    assert stock["water_liters"] == 0

    ranged = next(row for row in adjusted["components"] if row.get("role") == "ranged_control")
    # No arrows means the ranged element cannot pretend its bow is still a
    # fully supplied equipment method for this resolution.
    assert "bow" not in ranged["capability_state"].get("equipment_methods", [])


def test_aggregate_supply_rates_are_static_and_method_specific() -> None:
    mechanics = json.loads((ROOT / "game/data/mechanics/formation-resolution.json").read_text())
    cfg = mechanics["aggregate_consumables"]
    assert cfg["method_usage_per_person_per_exchange_milli"]["bow"] == {"arrows": 1500}
    assert set(cfg["method_usage_per_person_per_exchange_milli"]["thrown_tools"]) == {"kunai", "shuriken"}
    assert cfg["sustainment"]["rations_person_days_per_battle_day_milli"] > 0
    assert cfg["sustainment"]["water_liters_per_person_day_milli"] > 0
