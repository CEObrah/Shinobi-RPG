"""Conserved monthly enterprise production for faction workshops/apothecaries.

These reducers consume real faction inputs and finite worker time.  They do not
create passive level-based revenue: sale proceeds come from a finite regional
market cash pool or ordinary aggregate customer demand.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .production import workshop_quote, medicine_quote
from .regional_economy import execute_sale, unit_market_price_cash


def _market_cash_transfer(market: Mapping[str, Any], requested_cash: int) -> tuple[dict[str, Any], int]:
    """Move bounded aggregate-customer cash out of one regional market.

    This helper deliberately has no minting fallback.  If the surrounding
    economy cannot pay for a service or illicit extraction, the faction earns
    less.  Callers credit the returned amount to the faction treasury, keeping
    tracked currency conserved.
    """
    m = copy.deepcopy(dict(market))
    available = max(0, int(m.get("cash_pool", 0)))
    paid = min(available, max(0, int(requested_cash)))
    m["cash_pool"] = available - paid
    return m, paid


def operate_brotherhood_livelihood_month(
    market: Mapping[str, Any], *, worker_count: int, average_commerce: int,
    general_labor_cash_per_hour: int,
) -> dict[str, Any]:
    """Settle pooled labour/brokerage for an authored brotherhood society.

    Exact workers are assigned through ``trade_service`` elsewhere.  Its
    registered 420/1000 time share is converted into roughly 101 working hours
    per 30-day month, so earnings have the same finite-time opportunity cost as
    institutional training.  Commerce affects efficiency but cannot create
    work without real workers or a paying regional market.
    """
    workers = max(0, int(worker_count))
    if workers <= 0:
        return {"market": copy.deepcopy(dict(market)), "cash_earned": 0, "worker_count": 0, "labor_hours": 0, "reason": "no_assigned_workers"}
    wage = max(1, int(general_labor_cash_per_hour))
    hours_per_worker = 30 * 8 * 420 // 1000
    commerce = max(0, int(average_commerce))
    efficiency_milli = max(800, min(1250, 850 + commerce * 5))
    gross_target = workers * hours_per_worker * wage * efficiency_milli // 1000
    market_after, earned = _market_cash_transfer(market, gross_target)
    return {
        "market": market_after,
        "cash_earned": earned,
        "worker_count": workers,
        "labor_hours": workers * hours_per_worker,
        "average_commerce": commerce,
        "efficiency_milli": efficiency_milli,
        "reason": "served" if earned > 0 else "market_cash_unavailable",
    }


def operate_criminal_enterprise_month(
    market: Mapping[str, Any], *, enterprise_level: int, registered_ventures: int,
    worker_count: int, average_commerce: int, risk_tolerance: int,
    general_labor_cash_per_hour: int,
) -> dict[str, Any]:
    """Settle bounded local smuggling/extortion/fencing proceeds.

    This is the routine economic side of an already-registered criminal
    enterprise.  It does not replace route robbery, combat, or government
    evidence.  One assigned ``trade_service`` operator is required per active
    venture, the registered enterprise scale caps simultaneous ventures, and
    every copper received leaves the finite regional market cash pool.
    """
    level = max(0, min(5, int(enterprise_level)))
    ventures = max(0, int(registered_ventures))
    workers = max(0, int(worker_count))
    active = min(ventures, workers)
    if level <= 0 or ventures <= 0:
        return {"market": copy.deepcopy(dict(market)), "cash_earned": 0, "active_ventures": 0, "worker_count": workers, "reason": "no_registered_capacity"}
    if active <= 0:
        return {"market": copy.deepcopy(dict(market)), "cash_earned": 0, "active_ventures": 0, "worker_count": workers, "reason": "no_assigned_workers"}
    wage = max(1, int(general_labor_cash_per_hour))
    # One venture is economically comparable to one full-time small crew.  The
    # actual cell operator personally gives up 42% institutional time; the
    # venture value also covers the cell's aggregate field/fencing activity.
    base_venture_cash = wage * 8 * 30
    level_efficiency_milli = 800 + level * 100
    commerce_efficiency_milli = max(850, min(1200, 900 + max(0, int(average_commerce)) * 4))
    risk_milli = max(800, min(1200, 800 + max(0, min(100, int(risk_tolerance))) * 4))
    gross_target = active * base_venture_cash
    gross_target = gross_target * level_efficiency_milli // 1000
    gross_target = gross_target * commerce_efficiency_milli // 1000
    gross_target = gross_target * risk_milli // 1000
    market_after, earned = _market_cash_transfer(market, gross_target)
    return {
        "market": market_after,
        "cash_earned": earned,
        "registered_ventures": ventures,
        "active_ventures": active,
        "worker_count": workers,
        "average_commerce": max(0, int(average_commerce)),
        "risk_tolerance": max(0, min(100, int(risk_tolerance))),
        "reason": "operated" if earned > 0 else "market_cash_unavailable",
    }


def _max_batches_from_inputs(stock: Mapping[str, int], inputs: Mapping[str, int]) -> int:
    limits=[]
    for ref, qty in inputs.items():
        q=max(0,int(qty))
        if q>0: limits.append(max(0,int(stock.get(ref,0)))//q)
    return min(limits) if limits else 0


def operate_workshop_month(
    inventory: Mapping[str, Any], market: Mapping[str, Any], *, region_id: str,
    recipe_ref: str, workshop_level: int, crafting_skill: int,
    available_worker_hours: int, reserve_quantity: int, max_batches: int,
) -> dict[str, Any]:
    inv=copy.deepcopy(dict(inventory)); m=copy.deepcopy(dict(market))
    q=workshop_quote(recipe_ref,workshop_level=workshop_level,crafting_skill=crafting_skill)
    hours=max(1,int(q['active_hours']))
    raw=inv.setdefault('raw_materials',{})
    equipment=inv.setdefault('equipment',{})
    if not isinstance(raw,dict) or not isinstance(equipment,dict): raise ValueError('jianghu workshop inventory invalid')
    by_hours=max(0,int(available_worker_hours))//hours
    by_inputs=_max_batches_from_inputs(raw,q['inputs'])
    batches=min(max(0,int(max_batches)),by_hours,by_inputs)
    if batches<=0:
        return {'inventory':inv,'market':m,'batches':0,'produced':0,'sold':0,'cash_earned':0,'reason':'capacity_or_inputs'}
    for ref,qty in q['inputs'].items():
        after=max(0,int(raw.get(ref,0)))-int(qty)*batches
        if after>0: raw[ref]=after
        else: raw.pop(ref,None)
    output=str(q['output_item']); produced=int(q['output_quantity'])*batches
    before=max(0,int(equipment.get(output,0))); equipment[output]=before+produced
    sellable=max(0,int(equipment[output])-max(0,int(reserve_quantity)))
    sold=0; earned=0
    market_stock=m.get('stock',{}) if isinstance(m.get('stock'),Mapping) else {}
    if sellable>0 and output in market_stock and max(0,int(m.get('cash_pool',0)))>0:
        try:
            unit=max(1,unit_market_price_cash(region_id,output,market_stock)*950//1000)
            sold=min(sellable,max(0,int(m.get('cash_pool',0)))//unit)
            if sold>0:
                sale=execute_sale(region_id,output,sold,m,seller_stock=int(equipment[output]),seller_cash=0)
                equipment[output]=int(sale['seller_stock_after'])
                earned=int(sale['seller_cash_after'])
                m=sale['market_state_after']
        except (KeyError,ValueError,TypeError):
            pass
    return {'inventory':inv,'market':m,'batches':batches,'produced':produced,'sold':sold,'cash_earned':earned,
            'labor_hours':hours*batches,'recipe_ref':recipe_ref,'output_item':output,'reason':'produced'}


def operate_apothecary_month(
    inventory: Mapping[str, Any], market: Mapping[str, Any], *, recipe_ref: str,
    apothecary_level: int, medicine_skill: int, available_worker_hours: int,
    reserve_doses: int, max_batches: int,
) -> dict[str, Any]:
    inv=copy.deepcopy(dict(inventory)); m=copy.deepcopy(dict(market))
    q=medicine_quote(recipe_ref,apothecary_level=apothecary_level,medicine_skill=medicine_skill)
    hours=max(1,int(q['labor_hours']))
    herbs=inv.setdefault('herbs',{}); medicines=inv.setdefault('medicines',{})
    if not isinstance(herbs,dict) or not isinstance(medicines,dict): raise ValueError('jianghu apothecary inventory invalid')
    by_hours=max(0,int(available_worker_hours))//hours
    by_inputs=_max_batches_from_inputs(herbs,q['ingredients'])
    batches=min(max(0,int(max_batches)),by_hours,by_inputs)
    if batches<=0:
        return {'inventory':inv,'market':m,'batches':0,'produced':0,'sold':0,'cash_earned':0,'reason':'capacity_or_inputs'}
    for ref,qty in q['ingredients'].items():
        after=max(0,int(herbs.get(ref,0)))-int(qty)*batches
        if after>0: herbs[ref]=after
        else: herbs.pop(ref,None)
    produced=int(q['output_quantity'])*batches
    before=max(0,int(medicines.get(recipe_ref,0))); medicines[recipe_ref]=before+produced
    # Ordinary apothecary customers consume medicines rather than becoming a
    # second regional stock ledger.  The market cash pool is the conserved
    # aggregate counterparty and sales never exceed actual produced stock.
    sellable=max(0,int(medicines[recipe_ref])-max(0,int(reserve_doses)))
    sold=0; earned=0
    if sellable>0 and max(0,int(m.get('cash_pool',0)))>0:
        unit=max(1,int(q['total_base_cost_cash'])*125//max(1,int(q['output_quantity']))//100)
        # Correct arithmetic: 125% of per-dose base cost.
        unit=max(1,int(q['total_base_cost_cash'])*125//(100*max(1,int(q['output_quantity']))))
        sold=min(sellable,max(0,int(m.get('cash_pool',0)))//unit)
        earned=sold*unit
        if sold>0:
            medicines[recipe_ref]-=sold
            if medicines[recipe_ref]<=0: medicines.pop(recipe_ref,None)
            m['cash_pool']=max(0,int(m.get('cash_pool',0)))-earned
    return {'inventory':inv,'market':m,'batches':batches,'produced':produced,'sold':sold,'cash_earned':earned,
            'labor_hours':hours*batches,'recipe_ref':recipe_ref,'reason':'produced'}


__all__=[
    'operate_apothecary_month','operate_workshop_month',
    'operate_brotherhood_livelihood_month','operate_criminal_enterprise_month',
]
