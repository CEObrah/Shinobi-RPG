"""Deterministic 30-day faction upkeep.

Upkeep represents ordinary consumed services/supplies.  It never includes a
fictional equipment-repair reserve: damaged equipment is repaired only through
real craftsmen, workstations, materials and time.
"""
from __future__ import annotations
from typing import Any, Mapping


def monthly_upkeep_quote(faction:Mapping[str,Any],*,riding_horses:int=0,pack_animals:int=0)->dict[str,int]:
    pop=max(0,int(faction.get('population',faction.get('exact_population',0))))
    buildings=faction.get('buildings',{}) if isinstance(faction.get('buildings'),Mapping) else {}
    enterprises=faction.get('enterprises',{}) if isinstance(faction.get('enterprises'),Mapping) else {}
    food=pop*30
    # Members already receive explicit personal stipends.  Household overhead is
    # therefore communal consumables/services, not a second full wage bill.
    household_cash=pop*35
    building_cash=sum(max(0,int(level))**2*70 for level in buildings.values() if isinstance(level,int) and not isinstance(level,bool))
    # Enterprise-specific production/transport inputs are paid by their actual
    # operation.  This is only fixed organizational overhead.
    enterprise_cash=sum(max(0,int(level))**2*70 for level in enterprises.values() if isinstance(level,int) and not isinstance(level,bool))
    equipment_repair_cash=0
    animal_feed_days=(max(0,riding_horses)+max(0,pack_animals))*30
    animal_cash=animal_feed_days*18
    total=household_cash+building_cash+enterprise_cash+animal_cash
    return {
        'food_ration_days':food,
        'household_cash':household_cash,
        'building_maintenance_cash':building_cash,
        'enterprise_operating_cash':enterprise_cash,
        'equipment_repair_reserve_cash':equipment_repair_cash,
        'animal_feed_days':animal_feed_days,
        'animal_feed_cash':animal_cash,
        'total_cash':total,
    }
