"""Finite aggregate civilian market stocks for the Jianghu world."""
from __future__ import annotations
import copy, json, math
from pathlib import Path
from typing import Any, Mapping
from .economy import base_value_cash

_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'
def _data(): return json.loads((_MW/'regional-economy.json').read_text(encoding='utf-8'))
def _geo(): return json.loads((_MW/'geography.json').read_text(encoding='utf-8'))

def region_for_place(place_id:str)->str:
    places=_geo()['places']; p=places.get(place_id)
    if not isinstance(p,Mapping): raise KeyError(place_id)
    cp=p['climate_profile']
    if cp not in _data()['regions']: raise KeyError(cp)
    return cp

def initial_market_state(region_id:str)->dict[str,Any]:
    row=_data()['regions'].get(region_id)
    if not isinstance(row,Mapping): raise KeyError(region_id)
    return {'schema':'jianghu-market-state-1.0','region_id':region_id,'stock':copy.deepcopy(row['initial_market_stock']),'cycles_settled':0,'cash_pool':int(row['initial_market_cash_pool'])}

def scarcity_milli(*,stock:int,target:int)->int:
    if target<=0: return 1000
    # 25% stock -> 1750; target -> 1000; 2x target -> 750. Bounded.
    ratio_milli=max(1,stock*1000//target)
    if ratio_milli<1000: return min(2000,1000+(1000-ratio_milli)*1000//1000)
    return max(650,1000-(ratio_milli-1000)*250//1000)

def unit_market_price_cash(region_id:str,item_ref:str,stock:Mapping[str,int])->int:
    row=_data()['regions'][region_id]; target=int(row['market_target_stock'].get(item_ref,0)); onhand=int(stock.get(item_ref,0))
    scarcity=scarcity_milli(stock=onhand,target=target)
    location=int(row['location_price_milli'])
    base=base_value_cash(item_ref)
    return max(1,(base*location*scarcity+999_999)//1_000_000)

def quote_purchase(region_id:str,item_ref:str,quantity:int,market_state:Mapping[str,Any])->dict[str,Any]:
    if quantity<=0: raise ValueError('quantity invalid')
    if market_state.get('region_id')!=region_id: raise ValueError('market state region mismatch')
    stock=market_state.get('stock',{}); available=int(stock.get(item_ref,0))
    if available<quantity: raise ValueError('market stock insufficient')
    unit=unit_market_price_cash(region_id,item_ref,stock)
    return {'region_id':region_id,'item_ref':item_ref,'quantity':quantity,'unit_price_cash':unit,'total_price_cash':unit*quantity,'stock_before':available,'stock_after':available-quantity}

def execute_purchase(region_id:str,item_ref:str,quantity:int,market_state:Mapping[str,Any],*,buyer_cash:int)->dict[str,Any]:
    q=quote_purchase(region_id,item_ref,quantity,market_state)
    if buyer_cash<q['total_price_cash']: raise ValueError('buyer funds insufficient')
    out=copy.deepcopy(dict(market_state)); out['stock'][item_ref]=q['stock_after']; out['cash_pool']=int(out.get('cash_pool',0))+q['total_price_cash']
    return {'quote':q,'market_state_after':out,'buyer_cash_after':buyer_cash-q['total_price_cash']}

def settle_cycles(market_state:Mapping[str,Any],*,cycles:int)->dict[str,Any]:
    if cycles<0: raise ValueError('cycles invalid')
    out=copy.deepcopy(dict(market_state)); region=out['region_id']; row=_data()['regions'][region]
    stock=out.setdefault('stock',{})
    for _ in range(cycles):
        for item,prod in row['production_per_30_days'].items():
            cap=int(row['storage_capacity'][item]); stock[item]=min(cap,int(stock.get(item,0))+int(prod))
        for item,demand in row['civilian_demand_per_30_days'].items():
            stock[item]=max(0,int(stock.get(item,0))-int(demand))
        out['cycles_settled']=int(out.get('cycles_settled',0))+1
    return out


def trade_shipment_opportunities(*,market_states:Mapping[str,Mapping[str,Any]], route_rows:list[Mapping[str,Any]], place_to_region:Mapping[str,str], item_refs:list[str]|None=None)->list[dict[str,Any]]:
    """Create deterministic shipment demand only from real regional surplus/shortage."""
    refs=item_refs or ['food_ration_day','timber_kg','metal_kg','hardwood_kg','leather_kg','cloth_m']
    data=_data(); out=[]
    for route in sorted(route_rows,key=lambda r:str(r.get('id',''))):
        a=place_to_region.get(str(route.get('from'))); b=place_to_region.get(str(route.get('to')))
        if not a or not b or a==b or a not in market_states or b not in market_states: continue
        for src,dst in ((a,b),(b,a)):
            ss=market_states[src]['stock']; ds=market_states[dst]['stock']; sr=data['regions'][src]; dr=data['regions'][dst]
            for item in refs:
                st=int(sr['market_target_stock'].get(item,0)); dt=int(dr['market_target_stock'].get(item,0))
                if st<=0 or dt<=0: continue
                surplus=max(0,int(ss.get(item,0))-st); shortage=max(0,dt-int(ds.get(item,0)))
                qty=min(surplus,shortage,max(1,dt//10))
                if qty<=0: continue
                out.append({'source_region':src,'destination_region':dst,'route_id':route['id'],'item_ref':item,'quantity':qty,'distance_km_tenths':int(round(float(route['distance_km'])*10))})
    return out


def quote_sale(region_id:str,item_ref:str,quantity:int,market_state:Mapping[str,Any])->dict[str,Any]:
    if quantity<=0: raise ValueError('quantity invalid')
    if market_state.get('region_id')!=region_id: raise ValueError('market state region mismatch')
    stock=market_state.get('stock',{}); unit=max(1,unit_market_price_cash(region_id,item_ref,stock)*950//1000); total=unit*quantity
    if int(market_state.get('cash_pool',0))<total: raise ValueError('market cash insufficient')
    return {'region_id':region_id,'item_ref':item_ref,'quantity':quantity,'unit_price_cash':unit,'total_price_cash':total,'stock_before':int(stock.get(item_ref,0)),'stock_after':int(stock.get(item_ref,0))+quantity}

def execute_sale(region_id:str,item_ref:str,quantity:int,market_state:Mapping[str,Any],*,seller_stock:int,seller_cash:int)->dict[str,Any]:
    if seller_stock<quantity: raise ValueError('seller stock insufficient')
    q=quote_sale(region_id,item_ref,quantity,market_state); out=copy.deepcopy(dict(market_state)); out['stock'][item_ref]=q['stock_after']; out['cash_pool']=int(out.get('cash_pool',0))-q['total_price_cash']
    return {'quote':q,'market_state_after':out,'seller_stock_after':seller_stock-quantity,'seller_cash_after':seller_cash+q['total_price_cash']}
