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
