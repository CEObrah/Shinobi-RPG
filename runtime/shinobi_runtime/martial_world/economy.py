"""Single deterministic value ledger for the Jianghu economy."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Mapping
_ROOT=Path(__file__).resolve().parents[3]
_MW=_ROOT/'game/data/martial-world'

def _data(): return json.loads((_MW/'economy.json').read_text(encoding='utf-8'))

def base_value_cash(item_ref: str) -> int:
    d=_data()
    if item_ref in d.get('materials',{}): return int(d['materials'][item_ref]['base_value_cash'])
    if item_ref in d.get('consumables',{}): return int(d['consumables'][item_ref]['base_value_cash'])
    if item_ref in d.get('equipment_base_values_cash',{}): return int(d['equipment_base_values_cash'][item_ref])
    raise KeyError(item_ref)

def lot_value_cash(materials: Mapping[str,int]) -> int:
    total=0
    for ref,qty in materials.items():
        if isinstance(qty,bool) or not isinstance(qty,int) or qty<0: raise ValueError('material quantity')
        total += qty*base_value_cash(ref)
    return total

def market_price_cash(*, base_cash:int, location_price_milli:int=1000, scarcity_or_abundance_milli:int=1000, selling:bool=False) -> int:
    if min(base_cash, location_price_milli, scarcity_or_abundance_milli)<0: raise ValueError('price input')
    value=base_cash*location_price_milli*scarcity_or_abundance_milli
    if selling:
        return value*950//1_000_000_000
    return (value+999_999)//1_000_000
