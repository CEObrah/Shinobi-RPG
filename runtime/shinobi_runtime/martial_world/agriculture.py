"""Deterministic crop-cycle settlement using registered climate/weather."""
from __future__ import annotations
import json
from functools import lru_cache
from datetime import datetime,timedelta
from pathlib import Path
from typing import Any,Mapping
from .weather import weather_snapshot
_ROOT=Path(__file__).resolve().parents[3]
_MW=_ROOT/'game/data/martial-world'

@lru_cache(maxsize=1)
def _data(): return json.loads((_MW/'agriculture.json').read_text(encoding='utf-8'))

def crop_record(crop_ref:str) -> Mapping[str,Any]:
    d=_data(); row=d.get('food_crops',{}).get(crop_ref) or d.get('medicinal_herbs',{}).get(crop_ref)
    if not isinstance(row,Mapping): raise KeyError(crop_ref)
    return row

def daily_food_consumption_per_person() -> int:
    rules=_data().get('settlement_rules',{})
    if not isinstance(rules,Mapping): raise ValueError('agriculture settlement rules missing')
    return max(1,int(rules.get('daily_food_consumption_per_person',1)))

def climate_suitability_milli(*,world_seed:str,place_id:str,planted_at:datetime,crop_ref:str,agriculture_level:int) -> dict[str,int]:
    crop=crop_record(crop_ref); days=int(crop['growth_days']); lo,hi=[int(x) for x in crop['optimal_temperature_c_tenths']]
    temp_penalty=0; wet_blocks=0
    for day in range(days):
        snap=weather_snapshot(world_seed=world_seed,at=planted_at+timedelta(days=day,hours=12),place_id=place_id)
        t=int(snap['temperature_c_tenths'])
        if t<lo: temp_penalty += min(400,lo-t)
        elif t>hi: temp_penalty += min(400,t-hi)
        if int(snap['precipitation_milli'])>0: wet_blocks += 1
    avg_temp_penalty=temp_penalty//max(1,days)
    temperature_factor=max(350,1000-avg_temp_penalty*2)
    rainfall_milli=wet_blocks*1000//max(1,days)
    water_need=int(crop.get('water_need_milli',500))
    water_factor=max(400,1000-abs(rainfall_milli-water_need)//2)
    raw=temperature_factor*water_factor//1000
    if agriculture_level>=5:
        # Irrigation, shade and protected beds cannot create free crops; they
        # reduce climate mismatch while land, seed, labor and time remain real.
        raw=max(800,raw)
    return {'temperature_factor_milli':temperature_factor,'water_factor_milli':water_factor,'climate_suitability_milli':max(300,min(1100,raw))}

def harvest_quote(*,world_seed:str,place_id:str,crop_ref:str,planted_mu:int,planted_at:datetime,agriculture_level:int,labor_coverage_milli:int=1000) -> dict[str,Any]:
    crop=crop_record(crop_ref)
    if planted_mu<=0: raise ValueError('planted_mu')
    if planted_at.month not in crop.get('planting_months',[]): raise ValueError('planting month not allowed')
    climate=climate_suitability_milli(world_seed=world_seed,place_id=place_id,planted_at=planted_at,crop_ref=crop_ref,agriculture_level=agriculture_level)
    infra=(700,800,900,1000,1100,1200)[max(0,min(5,agriculture_level))]
    base=int(crop.get('yield_food_per_mu',crop.get('yield_herb_units_per_mu',0)))
    labor=max(0,min(1100,labor_coverage_milli)); c=int(climate['climate_suitability_milli'])
    output=base*planted_mu*labor*infra*c//1_000_000_000
    return {'crop_ref':crop_ref,'planted_mu':planted_mu,'planted_at':planted_at.isoformat(),
            'harvest_at':(planted_at+timedelta(days=int(crop['growth_days']))).isoformat(),
            'seed_cost_cash':int(crop['seed_cost_cash_per_mu'])*planted_mu,
            'labor_hours':int(crop['labor_days_per_mu'])*8*planted_mu,
            'climate':climate,'infrastructure_milli':infra,'output_units':output}


def monthly_enterprise_settlement(
    *, world_seed: str, faction_ref: str, at: datetime, managed_land_mu: int,
    agriculture_level: int, medicine_level: int, available_cash: int,
    labor_cash_per_hour: int, operating_efficiency_milli: int = 1000,
) -> dict[str, Any]:
    """Settle one month of an aggregate faction farm enterprise.

    Managed acreage belongs to the faction. Individual crop parcels and harvest
    timers are deliberately not entities. Production is a deterministic monthly
    flow from that owned land, with real operating cash paid into the regional
    economy and physical food/herbs added to faction inventory.
    """
    land=max(0,int(managed_land_mu)); cash=max(0,int(available_cash))
    level=max(0,min(5,int(agriculture_level)))
    if land<=0 or level<=0:
        return {'managed_land_mu':land,'operated_land_mu':0,'cash_spent':0,'food_ration_days':0,'herb_ref':None,'herb_units':0}
    efficiency=max(250,min(2000,int(operating_efficiency_milli)))
    herb_mu=max(0,land//5) if int(medicine_level)>0 else 0
    food_mu=max(0,land-herb_mu)
    food=crop_record('staple_grain')
    herb_refs=sorted(_data().get('medicinal_herbs',{}))
    herb_ref=None
    herb=None
    if herb_mu>0 and herb_refs:
        import hashlib
        digest=hashlib.sha256(f"{world_seed}|{faction_ref}|{at.year:04d}-{at.month:02d}|aggregate-herb".encode('utf-8')).digest()
        herb_ref=herb_refs[int.from_bytes(digest[:4],'big')%len(herb_refs)]
        herb=crop_record(herb_ref)

    def monthly_cost(row: Mapping[str,Any], mu: int) -> int:
        if mu<=0:return 0
        growth=max(1,int(row.get('growth_days',30)))
        seed=max(0,int(row.get('seed_cost_cash_per_mu',0)))*mu*30
        labor_hours=max(0,int(row.get('labor_days_per_mu',0)))*8*mu*30
        labor_cost=(labor_hours*max(1,int(labor_cash_per_hour))*1000 + efficiency*growth-1)//(efficiency*growth)
        return (seed+growth-1)//growth + labor_cost

    food_cost=monthly_cost(food,food_mu); herb_cost=monthly_cost(herb,herb_mu) if herb is not None else 0
    full_cost=food_cost+herb_cost
    scale_milli=1000 if full_cost<=0 else min(1000,cash*1000//full_cost)
    if scale_milli<=0:
        return {'managed_land_mu':land,'operated_land_mu':0,'cash_spent':0,'food_ration_days':0,'herb_ref':herb_ref,'herb_units':0}
    operated_food=food_mu*scale_milli//1000
    operated_herb=herb_mu*scale_milli//1000
    if operated_food+operated_herb<=0:
        return {'managed_land_mu':land,'operated_land_mu':0,'cash_spent':0,'food_ration_days':0,'herb_ref':herb_ref,'herb_units':0}
    cash_spent=min(cash,monthly_cost(food,operated_food)+(monthly_cost(herb,operated_herb) if herb is not None else 0))
    infra=(700,800,900,1000,1100,1200)[level]
    def monthly_output(row: Mapping[str,Any], mu: int, key: str) -> int:
        if mu<=0:return 0
        growth=max(1,int(row.get('growth_days',30)))
        base=max(0,int(row.get(key,0)))
        return base*mu*30*infra//(growth*1000)
    return {
        'managed_land_mu':land,'operated_land_mu':operated_food+operated_herb,'cash_spent':cash_spent,
        'food_ration_days':monthly_output(food,operated_food,'yield_food_per_mu'),
        'herb_ref':herb_ref,'herb_units':monthly_output(herb,operated_herb,'yield_herb_units_per_mu') if herb is not None else 0,
    }
