"""Conserved faction inventory operations for Jianghu stock authorities."""
from __future__ import annotations
import copy
from typing import Any, Mapping

_BUCKETS={'equipment','raw_materials','herbs','medicines'}

def _copy(inv:Mapping[str,Any])->dict[str,Any]: return copy.deepcopy(dict(inv))

def stock_quantity(inv:Mapping[str,Any],bucket:str,item_ref:str)->int:
    if bucket not in _BUCKETS: raise KeyError(bucket)
    return max(0,int(inv.get(bucket,{}).get(item_ref,0)))

def adjust_stock(inv:Mapping[str,Any],*,bucket:str,item_ref:str,delta:int)->dict[str,Any]:
    if bucket not in _BUCKETS: raise KeyError(bucket)
    out=_copy(inv); b=out.setdefault(bucket,{})
    before=max(0,int(b.get(item_ref,0))); after=before+int(delta)
    if after<0: raise ValueError('insufficient stock')
    b[item_ref]=after
    return out

def transfer_stock(source:Mapping[str,Any],destination:Mapping[str,Any],*,bucket:str,item_ref:str,quantity:int)->dict[str,Any]:
    if quantity<=0: raise ValueError('quantity invalid')
    src=adjust_stock(source,bucket=bucket,item_ref=item_ref,delta=-quantity)
    dst=adjust_stock(destination,bucket=bucket,item_ref=item_ref,delta=quantity)
    return {'source_after':src,'destination_after':dst,'quantity':quantity,'item_ref':item_ref,'bucket':bucket}

def consume_food(inv:Mapping[str,Any],*,ration_days:int)->dict[str,Any]:
    if ration_days<0: raise ValueError('ration_days invalid')
    out=_copy(inv); before=max(0,int(out.get('food_ration_days',0)))
    if before<ration_days: raise ValueError('insufficient food')
    out['food_ration_days']=before-ration_days
    return out

def add_food(inv:Mapping[str,Any],*,ration_days:int)->dict[str,Any]:
    if ration_days<0: raise ValueError('ration_days invalid')
    out=_copy(inv); out['food_ration_days']=max(0,int(out.get('food_ration_days',0)))+ration_days; return out

def transfer_transport(source:Mapping[str,Any],destination:Mapping[str,Any],*,asset_ref:str,quantity:int)->dict[str,Any]:
    if asset_ref not in {'riding_horses','pack_animals'}: raise KeyError(asset_ref)
    if quantity<=0: raise ValueError('quantity invalid')
    src=_copy(source); dst=_copy(destination); sb=src.setdefault('transport_assets',{}); db=dst.setdefault('transport_assets',{})
    before=max(0,int(sb.get(asset_ref,0)))
    if before<quantity: raise ValueError('insufficient transport assets')
    sb[asset_ref]=before-quantity; db[asset_ref]=max(0,int(db.get(asset_ref,0)))+quantity
    return {'source_after':src,'destination_after':dst}
