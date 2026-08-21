"""Universal martial-faction derived scale and deterministic autonomy review."""
from __future__ import annotations
from typing import Any,Mapping

from .faction_state import resolved_faction_type


def derived_scale(faction:Mapping[str,Any])->dict[str,Any]:
    pop=max(0,int(faction.get('exact_population',faction.get('population',0))))
    b=faction.get('buildings',{}); e=faction.get('enterprises',{})
    infra=sum(max(0,int(v)) for v in b.values()) if isinstance(b,Mapping) else 0
    enterprise=sum(max(0,int(v)) for v in e.values()) if isinstance(e,Mapping) else 0
    treasury=max(0,int(faction.get('starting_assets',{}).get('treasury_cash',faction.get('treasury_cash',0))))
    score=pop*4+infra*12+enterprise*15+min(300,treasury//10000)
    label='tiny' if score<250 else ('small' if score<500 else ('established' if score<900 else ('regional' if score<1500 else 'great')))
    return {'scale_score':score,'presentation_label':label}


def autonomy_review(
    faction:Mapping[str,Any],*,food_reserve_days:int,cash_reserve_months:int,injured_martial:int,
    open_contracts:int,recruitment_capacity:int,training_capacity:int,office_vacancies:int=0,
    known_hostile_relations:int=0,market_shortages:int=0,active_projects:int=0,
    institutional_stress_milli:int=0,
)->dict[str,Any]:
    """Return priorities only when a production consumer exists at the frontier."""
    p=faction.get('autonomy_policy',{}) if isinstance(faction.get('autonomy_policy'),Mapping) else {}
    actions=[]
    if food_reserve_days<30: actions.append(('secure_food',1000))
    if cash_reserve_months<max(1,int(p.get('reserve_cash_months',6))): actions.append(('preserve_or_earn_cash',900+int(p.get('financial_caution',50))))
    if injured_martial>0: actions.append(('recover_injured',850+injured_martial))
    if office_vacancies>0: actions.append(('fill_offices',800+office_vacancies))
    if market_shortages>0: actions.append(('address_market_shortage',650+market_shortages))
    if known_hostile_relations>0: actions.append(('address_hostile_relation',620+known_hostile_relations*4+int(p.get('risk_tolerance',50))))
    if training_capacity>0: actions.append(('train_members',500+int(p.get('training_priority',50))))
    if recruitment_capacity>0: actions.append(('recruit',450+int(p.get('recruitment_priority',50))))
    if open_contracts>0 and resolved_faction_type(faction) in {'escort_agency','contract_hall','martial_house','sect','brotherhood_society'}:
        actions.append(('evaluate_contracts',400+int(p.get('risk_tolerance',50))))
    # Solvent institutions should not remain frozen at their seed footprint or
    # enterprise scale.  The executor still has to prove land, materials,
    # people, treasury and project capacity before any project can start.
    if active_projects<=0 and institutional_stress_milli<=0 and cash_reserve_months>=8:
        actions.append(('invest_growth',360+int(p.get('financial_caution',50))//2))
    # Friendly diplomacy is deliberately low priority and one-off; it never
    # manufactures an alliance flag or automatic combat bonus.
    if cash_reserve_months>=8:
        actions.append(('consider_diplomacy',250+max(0,50-int(p.get('financial_caution',50))//2)))
    actions.sort(key=lambda x:(-x[1],x[0]))
    return {'ordered_actions':[a for a,_ in actions],'scored_actions':[{'action':a,'score':s} for a,s in actions]}
