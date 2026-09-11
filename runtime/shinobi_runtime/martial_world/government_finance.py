"""Conserved public funding for government bounties."""
from __future__ import annotations
import copy
from typing import Any, Mapping

def fund_bounty_escrow(market:Mapping[str,Any],*,existing_warrant:Mapping[str,Any]|None,desired_cash:int)->dict[str,Any]:
    after=copy.deepcopy(dict(market)); existing=max(0,int(existing_warrant.get("bounty_escrow_cash",0))) if isinstance(existing_warrant,Mapping) else 0
    target=max(existing,max(0,int(desired_cash))); available=max(0,int(after.get("cash_pool",0))); added=min(available,max(0,target-existing)); after["cash_pool"]=available-added
    return {"market_after":after,"escrow_cash":existing+added,"escrow_added_cash":added}

def refund_bounty_escrow(market:Mapping[str,Any],warrant:Mapping[str,Any])->dict[str,Any]:
    after=copy.deepcopy(dict(market)); cash=max(0,int(warrant.get("bounty_escrow_cash",0))); after["cash_pool"]=max(0,int(after.get("cash_pool",0)))+cash
    return {"market_after":after,"refunded_cash":cash}

__all__=["fund_bounty_escrow","refund_bounty_escrow"]
