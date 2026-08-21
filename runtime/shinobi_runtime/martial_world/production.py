"""Deterministic workshop and apothecary production quotes."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Mapping
from .economy import lot_value_cash
_ROOT=Path(__file__).resolve().parents[3]
_MW=_ROOT/'game/data/martial-world'

def _load(name): return json.loads((_MW/name).read_text(encoding='utf-8'))

def workshop_quote(recipe_ref:str, *, workshop_level:int, crafting_skill:int) -> dict[str,Any]:
    d=_load('workshop.json'); recipe=d['recipes'].get(recipe_ref)
    if not isinstance(recipe,Mapping): raise KeyError(recipe_ref)
    req_level=int(recipe.get('minimum_workshop_level',1)); req_skill=int(recipe.get('minimum_crafting_skill',0))
    if workshop_level<req_level: raise ValueError('workshop level insufficient')
    if crafting_skill<req_skill: raise ValueError('crafting skill insufficient')
    inputs={str(k):int(v) for k,v in recipe.get('inputs',{}).items()}
    hours=int(recipe.get('active_hours',recipe.get('labor_hours',0)))
    labor_rate=int(_load('economy.json')['labor']['skilled_labor_cash_per_hour'])
    return {'recipe_ref':recipe_ref,'inputs':inputs,'output_item':recipe['output'],'output_quantity':int(recipe['quantity']),
            'active_hours':hours,'material_value_cash':lot_value_cash(inputs),'labor_value_cash':hours*labor_rate,
            'total_base_cost_cash':lot_value_cash(inputs)+hours*labor_rate}

def medicine_quote(recipe_ref:str, *, apothecary_level:int, medicine_skill:int) -> dict[str,Any]:
    d=_load('medicine.json'); recipe=d['recipes'].get(recipe_ref)
    if not isinstance(recipe,Mapping): raise KeyError(recipe_ref)
    if apothecary_level<int(recipe.get('minimum_apothecary_level',1)): raise ValueError('apothecary level insufficient')
    if medicine_skill<int(recipe.get('minimum_medicine_skill',0)): raise ValueError('medicine skill insufficient')
    hours=int(recipe.get('labor_hours',recipe.get('active_hours',0)))
    rate=int(_load('economy.json')['labor']['skilled_labor_cash_per_hour'])
    ingredients={str(k):int(v) for k,v in recipe.get('ingredients',{}).items()}
    agriculture=_load('agriculture.json'); herbs=agriculture.get('medicinal_herbs',{})
    ingredient_value=0
    for ref,qty in ingredients.items():
        row=herbs.get(ref)
        if not isinstance(row,Mapping): raise KeyError(ref)
        ingredient_value += qty*int(row['base_value_cash_per_herb_unit'])
    labor_value=hours*rate
    return {'recipe_ref':recipe_ref,'ingredients':ingredients,
            'output_quantity':int(recipe.get('batch_size',recipe.get('output_quantity',1))), 'labor_hours':hours,
            'ingredient_value_cash':ingredient_value,'labor_value_cash':labor_value,'total_base_cost_cash':ingredient_value+labor_value,
            'effect':recipe.get('effect'),'saturation':recipe.get('saturation')}

def consume_inputs(stock:Mapping[str,int], inputs:Mapping[str,int]) -> dict[str,int]:
    after={str(k):int(v) for k,v in stock.items()}
    for ref,qty in inputs.items():
        if after.get(ref,0)<qty: raise ValueError(f'insufficient:{ref}')
    for ref,qty in inputs.items(): after[ref]-=qty
    return after

def medicine_dose_effect(recipe_ref:str, *, current_saturation:int) -> dict[str,Any]:
    d=_load('medicine.json'); recipe=d['recipes'].get(recipe_ref)
    if not isinstance(recipe,Mapping): raise KeyError(recipe_ref)
    sat=d['saturation']; current=max(0,int(current_saturation)); multiplier=max(0,100-current)
    scaled={}
    for key,value in recipe.get('effect',{}).items():
        if isinstance(value,int) and not isinstance(value,bool) and key!='duration_hours': scaled[key]=value*multiplier//100
        else: scaled[key]=value
    after=current+int(sat['dose_saturation_gain']); toxicity=max(0,after-100)
    return {'recipe_ref':recipe_ref,'category':recipe['category'],'effect_multiplier_pct':multiplier,
            'applied_effect':scaled,'saturation_after':min(100,after),'toxicity_burden_added':toxicity}


def start_medicine_batch(
    recipe_ref: str,
    *,
    apothecary_level: int,
    medicine_skill: int,
    herb_stock: Mapping[str, int],
    started_at: str,
    batches: int = 1,
) -> dict[str, Any]:
    """Consume exact herbs and create a deterministic timed batch project."""
    from datetime import datetime, timedelta
    if batches <= 0:
        raise ValueError("batches invalid")
    q = medicine_quote(recipe_ref, apothecary_level=apothecary_level, medicine_skill=medicine_skill)
    required = {k: int(v) * batches for k, v in q["ingredients"].items()}
    after = consume_inputs(herb_stock, required)
    start = datetime.fromisoformat(started_at)
    # One qualified workstation/lead apothecary handles the quoted active time.
    # Additional batches are sequential unless a caller assigns multiple lawful
    # workstations as separate projects.
    active_hours = int(q["labor_hours"]) * batches
    completes = start + timedelta(hours=active_hours)
    return {
        "project_type": "medicine_batch",
        "recipe_ref": recipe_ref,
        "batches": batches,
        "started_at": start.isoformat(),
        "completes_at": completes.isoformat(),
        "ingredients_consumed": required,
        "herb_stock_after_start": after,
        "output_quantity": int(q["output_quantity"]) * batches,
        "status": "in_progress",
    }


def complete_medicine_batch(project: Mapping[str, Any], *, at: str, medicine_stock: Mapping[str, int]) -> dict[str, Any]:
    """Finish a started medicine batch only after its registered completion time."""
    from datetime import datetime
    if project.get("project_type") != "medicine_batch" or project.get("status") != "in_progress":
        raise ValueError("medicine project invalid")
    now = datetime.fromisoformat(at)
    due = datetime.fromisoformat(str(project["completes_at"]))
    if now < due:
        raise ValueError("medicine batch not complete")
    stock = {str(k): int(v) for k, v in medicine_stock.items()}
    ref = str(project["recipe_ref"])
    stock[ref] = stock.get(ref, 0) + int(project["output_quantity"])
    done = dict(project); done["status"] = "completed"; done["completed_at"] = now.isoformat()
    return {"project_after": done, "medicine_stock_after": stock}


def poison_quote(recipe_ref: str, *, apothecary_level: int, medicine_skill: int) -> dict[str, Any]:
    """Quote a fictional poison batch from abstract registered reagent units."""
    d = _load('poisons.json')
    recipe = d.get('production', {}).get('recipes', {}).get(recipe_ref)
    if not isinstance(recipe, Mapping):
        raise KeyError(recipe_ref)
    if apothecary_level < int(recipe.get('minimum_apothecary_level', 1)):
        raise ValueError('apothecary level insufficient')
    if medicine_skill < int(recipe.get('minimum_medicine_skill', 0)):
        raise ValueError('medicine skill insufficient')
    inputs = {str(k): int(v) for k, v in recipe.get('inputs', {}).items()}
    return {
        'recipe_ref': recipe_ref,
        'inputs': inputs,
        'output_item': f'poison_{recipe_ref}',
        'output_quantity': int(recipe.get('batch_size', 1)),
        'labor_hours': int(recipe.get('labor_hours', 1)),
    }
