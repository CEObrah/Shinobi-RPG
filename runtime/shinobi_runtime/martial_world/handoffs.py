"""One salience classifier for autonomous Jianghu results."""
from __future__ import annotations
from typing import Any,Mapping
HARD={'hostile_contact','irreversible_treatment_choice','contract_acceptance_deadline','office_offer','marriage_proposal','surrender_or_mercy','custody_execution_decision'}
SOFT={'funded_contract_offer','ranking_publication','tournament_registration','faction_report','family_checkin','formal_challenge','succession_notice','trade_opportunity','bounty_notice','tournament_result','government_summons'}
def classify_handoff(event:Mapping[str,Any])->dict[str,Any]:
    kind=event.get('kind')
    if event.get('requires_player_decision') is True or kind in HARD: return {'class':'hard_decision','interrupts_event_seeking':True,'requires_player_decision':True}
    if event.get('delivered_to_player') is True or kind in SOFT: return {'class':'soft_player_facing','interrupts_event_seeking':True,'requires_player_decision':False}
    return {'class':'internal','interrupts_event_seeking':False,'requires_player_decision':False}
