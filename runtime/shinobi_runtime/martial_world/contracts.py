"""Deterministic persistent Jianghu contract pricing and lifecycle."""
from __future__ import annotations
import copy, json, hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'
def _data(): return json.loads((_MW/'contracts.json').read_text(encoding='utf-8'))

def escort_quote(*,distance_km_tenths:int,cargo_value_cash:int,threat_score:int,escort_count:int,normal_travel_hours:int,deadline_hours:int)->dict[str,int]:
    if min(distance_km_tenths,cargo_value_cash,threat_score,escort_count,normal_travel_hours,deadline_hours)<0 or escort_count<=0: raise ValueError('escort quote input invalid')
    s=_data()['finite_types']['escort']; km=(distance_km_tenths+9)//10
    base=int(s['base_admin_cash']); distance=km*escort_count*int(s['distance_cash_per_km_per_escort'])
    liability=cargo_value_cash*int(s['cargo_liability_milli'])//1000
    threat=threat_score*escort_count*int(s['threat_premium_cash_per_score_per_escort'])
    saved=max(0,normal_travel_hours-deadline_hours); deadline=saved*int(s['deadline_premium_per_hour_saved_cash'])
    total=base+distance+liability+threat+deadline
    return {'base_cash':base,'distance_cash':distance,'cargo_liability_cash':liability,'threat_premium_cash':threat,'deadline_premium_cash':deadline,'total_reward_cash':total}

def create_contract_owner(*,contract_type:str,issuer_ref:str,beneficiary_ref:str|None,offered_at:str,expires_at:str,reward_cash:int,funding_cash:int,objective:Mapping[str,Any],source_ref:str)->dict[str,Any]:
    if contract_type not in _data()['finite_types']: raise KeyError(contract_type)
    if reward_cash<0 or funding_cash<reward_cash: raise ValueError('contract not fully funded')
    seed='\0'.join([contract_type,issuer_ref,source_ref,offered_at,str(objective)])
    cid='contract.'+hashlib.sha256(seed.encode()).hexdigest()[:24]
    return {'contract_id':cid,'contract_type':contract_type,'issuer_ref':issuer_ref,'beneficiary_ref':beneficiary_ref,'status':'offered','offered_at':offered_at,'expires_at':expires_at,'escrow_cash':reward_cash,'reward_cash':reward_cash,'objective':copy.deepcopy(dict(objective)),'source_ref':source_ref,'participants':[]}

def transition(contract:Mapping[str,Any],*,at:str,to_status:str,actor_ref:str|None=None,participants:list[str]|None=None)->dict[str,Any]:
    allowed={
      'offered':{'accepted','expired'},'accepted':{'in_progress','expired'},'in_progress':{'objective_resolved','failed'},
      'objective_resolved':{'settled'},'settled':set(),'failed':set(),'expired':set()}
    cur=str(contract.get('status')); 
    if to_status not in allowed.get(cur,set()): raise ValueError('invalid contract transition')
    out=copy.deepcopy(dict(contract)); out['status']=to_status
    if participants is not None:
        if len(set(participants))!=len(participants): raise ValueError('duplicate participants')
        out['participants']=list(participants)
    return out

def settle_payment(contract:Mapping[str,Any],*,success:bool)->dict[str,Any]:
    if contract.get('status') not in {'objective_resolved','failed'}: raise ValueError('contract not ready for settlement')
    escrow=int(contract.get('escrow_cash',0)); reward=int(contract.get('reward_cash',0))
    paid=min(escrow,reward) if success else 0; refund=escrow-paid
    return {'paid_cash':paid,'refunded_cash':refund,'escrow_after':0}

def funded_contract_offer(*,issuer_cash:int,contract_type:str,issuer_ref:str,beneficiary_ref:str|None,offered_at:str,expires_at:str,reward_cash:int,objective:Mapping[str,Any],source_ref:str)->dict[str,Any]:
    if issuer_cash<reward_cash: raise ValueError('issuer funding insufficient')
    contract=create_contract_owner(contract_type=contract_type,issuer_ref=issuer_ref,beneficiary_ref=beneficiary_ref,offered_at=offered_at,expires_at=expires_at,reward_cash=reward_cash,funding_cash=issuer_cash,objective=objective,source_ref=source_ref)
    return {'contract':contract,'issuer_cash_after':issuer_cash-reward_cash}
