"""Deterministic 30-day faction upkeep.

Routine transport is stored as pooled service capacity, not exact animal/vehicle
counts. Upkeep therefore prices the maintained rider/freight capacity itself.
"""
from __future__ import annotations
from typing import Any, Mapping
from .aggregate_transport import freight_service_units
from .agriculture import daily_food_consumption_per_person

def monthly_upkeep_quote(faction: Mapping[str, Any], *, rider_capacity_slots: int=0, freight_capacity_kg: int=0) -> dict[str,int]:
    pop=max(0,int(faction.get("population",faction.get("exact_population",0))))
    buildings=faction.get("buildings",{}) if isinstance(faction.get("buildings"),Mapping) else {}
    enterprises=faction.get("enterprises",{}) if isinstance(faction.get("enterprises"),Mapping) else {}
    food=pop*30*daily_food_consumption_per_person(); household_cash=pop*35
    building_cash=sum(max(0,int(level))**2*70 for level in buildings.values() if isinstance(level,int) and not isinstance(level,bool))
    enterprise_cash=sum(max(0,int(level))**2*70 for level in enterprises.values() if isinstance(level,int) and not isinstance(level,bool))
    equipment_repair_cash=0
    service_units=max(0,int(rider_capacity_slots))+freight_service_units(freight_capacity_kg)
    transport_capacity_days=service_units*30; transport_cash=transport_capacity_days*18
    total=household_cash+building_cash+enterprise_cash+transport_cash
    return {"food_ration_days":food,"household_cash":household_cash,"building_maintenance_cash":building_cash,"enterprise_operating_cash":enterprise_cash,"equipment_repair_reserve_cash":equipment_repair_cash,"transport_capacity_days":transport_capacity_days,"transport_operating_cash":transport_cash,"total_cash":total}
