"""Directed current faction relationships and diplomacy scoring."""
from __future__ import annotations
import copy, hashlib, json
from pathlib import Path
from typing import Any, Mapping

from .upkeep import monthly_upkeep_quote
from .faction_politics import conflict_stage
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'
def _data(): return json.loads((_MW/'faction-relations.json').read_text())
def apply_relation_event(edge:Mapping[str,Any]|None,*,from_faction:str,to_faction:str,event_kind:str)->dict[str,Any]:
    d=_data(); delta=d['event_deltas'].get(event_kind)
    if delta is None: raise KeyError(event_kind)
    out=copy.deepcopy(dict(edge or {'from_faction':from_faction,'to_faction':to_faction}))
    if out['from_faction']!=from_faction or out['to_faction']!=to_faction: raise ValueError('relation direction mismatch')
    for k,(lo,hi) in d['axes'].items():
        value=max(lo,min(hi,int(out.get(k,0))+int(delta.get(k,0))))
        if value: out[k]=value
        else: out.pop(k,None)
    return out
def settle_positive_obligation(
    edge:Mapping[str,Any]|None, *, from_faction:str, to_faction:str, amount:int,
)->dict[str,Any]:
    """Consume a bounded current debt without creating reciprocal history."""
    out=copy.deepcopy(dict(edge or {'from_faction':from_faction,'to_faction':to_faction}))
    if out.get('from_faction')!=from_faction or out.get('to_faction')!=to_faction:
        raise ValueError('relation direction mismatch')
    current=max(0,int(out.get('obligation',0)))
    remaining=max(0,current-max(0,int(amount)))
    if remaining:
        out['obligation']=remaining
    else:
        out.pop('obligation',None)
    return out


def resolve_friendly_aid_transfer(
    source: Mapping[str, Any], source_inventory: Mapping[str, Any],
    target: Mapping[str, Any], target_inventory: Mapping[str, Any],
) -> dict[str, Any]:
    """Return conserved faction treasury after-images for one bounded aid transfer."""
    def monthly_cash_need(row: Mapping[str, Any], inventory: Mapping[str, Any]) -> int:
        transport = inventory.get("transport_capacity", {}) if isinstance(inventory.get("transport_capacity"), Mapping) else {}
        quote = monthly_upkeep_quote(
            row, rider_capacity_slots=int(transport.get("rider_slots", 0)),
            freight_capacity_kg=int(transport.get("freight_capacity_kg", 0)),
        )
        return max(1, int(quote.get("total_cash", 1)))

    source_after = copy.deepcopy(dict(source)); target_after = copy.deepcopy(dict(target))
    source_monthly = monthly_cash_need(source_after, source_inventory)
    target_monthly = monthly_cash_need(target_after, target_inventory)
    source_cash = max(0, int(source_after.get("treasury_cash", 0)))
    target_cash = max(0, int(target_after.get("treasury_cash", 0)))
    spendable = max(0, source_cash - source_monthly * 8)
    need = max(0, target_monthly * 4 - target_cash)
    if spendable <= 0 or need <= 0:
        return {"result": "target_not_in_need"}
    amount = max(0, int(min(need, max(100, spendable // 4), max(250, source_monthly * 2))))
    if amount <= 0:
        return {"result": "aid_not_affordable"}
    source_after["treasury_cash"] = source_cash - amount
    target_after["treasury_cash"] = target_cash + amount
    return {"result": "aid_transferred", "cash": amount, "source_after": source_after, "target_after": target_after}

def diplomacy_score(edge:Mapping[str,Any],*,proposal_value_cash:int,proposal_cost_cash:int,strategic_fit:int,risk:int)->int:
    trust=int(edge.get('trust',0)); respect=int(edge.get('respect',0)); hostility=int(edge.get('hostility',0)); obligation=int(edge.get('obligation',0))
    value=max(-100, min(100, (int(proposal_value_cash)-int(proposal_cost_cash))//1000))
    return trust*4 + respect*2 - hostility*5 + obligation*2 + value*3 + int(strategic_fit)*3 - int(risk)*2
def evaluate_proposal(edge:Mapping[str,Any],**kwargs)->dict[str,Any]:
    score=diplomacy_score(edge,**kwargs); return {'score':score,'accept':score>=100,'counteroffer':-50<=score<100,'reject':score<-50}
def proposal_kind_supported(kind:str)->bool:
    return str(kind) in {str(x) for x in _data().get('proposal_kinds',[]) if isinstance(x,str)}


def refresh_war_coalitions(
    state: Mapping[str, Any], *, at_iso: str, faction_refs: set[str] | None = None,
) -> dict[str, Any]:
    """Recompute sparse current coalitions from real shared war pressure.

    A coalition is not a permanent political party. It exists only while two or
    more current factions are already at war with the same target and have
    enough mutual trust/non-hostility to coordinate. Re-running this function
    replaces the current coalition set and therefore closes stale coalitions
    without an append-only diplomatic history.
    """
    out=copy.deepcopy(dict(state))
    edges=out.get("edges",[])
    if not isinstance(edges,list):
        raise ValueError("faction relation edges invalid")
    allowed={str(x) for x in faction_refs} if faction_refs is not None else None
    directed={
        (str(row.get("from_faction") or ""),str(row.get("to_faction") or "")):row
        for row in edges if isinstance(row,Mapping)
    }
    attackers_by_target:dict[str,set[str]]={}
    for row in edges:
        if not isinstance(row,Mapping) or conflict_stage(row)!="war":
            continue
        source=str(row.get("from_faction") or ""); target=str(row.get("to_faction") or "")
        if not source or not target or source==target:
            continue
        if allowed is not None and (source not in allowed or target not in allowed):
            continue
        attackers_by_target.setdefault(target,set()).add(source)

    def compatible(a:str,b:str)->bool:
        ab=directed.get((a,b),{}); ba=directed.get((b,a),{})
        h=max(int(ab.get("hostility",0)),int(ba.get("hostility",0)))
        trust=int(ab.get("trust",0))+int(ba.get("trust",0))
        return h<=12 and trust>=30

    prior=out.get("coalitions",{})
    prior=prior if isinstance(prior,Mapping) else {}
    current:dict[str,dict[str,Any]]={}
    for target,sources in sorted(attackers_by_target.items()):
        remaining=set(sources)
        while remaining:
            seed=sorted(remaining)[0]; component={seed}; remaining.remove(seed)
            # A coalition requires mutual tolerance across every participant,
            # not merely a transitive trust chain. A-B and B-C compatibility
            # must not pull mutually hostile A and C into one institution.
            for other in sorted(list(remaining)):
                if all(compatible(other, member) for member in component):
                    component.add(other); remaining.remove(other)
            if len(component)<2:
                continue
            members=sorted(component)
            digest=hashlib.sha256((target+"|"+"|".join(members)).encode("utf-8")).hexdigest()[:12]
            ref=f"coalition:shared-war:{target}:{digest}"
            old=prior.get(ref) if isinstance(prior,Mapping) else None
            current[ref]={
                "member_faction_refs":members,
                "target_faction_ref":target,
                "purpose":"mutual_war_pressure",
                "formed_at":str(old.get("formed_at") or at_iso) if isinstance(old,Mapping) else str(at_iso),
            }
    if current: out["coalitions"]=current
    else: out.pop("coalitions",None)
    return out


def coalition_target_refs_for_faction(state: Mapping[str, Any], faction_ref: str) -> set[str]:
    rows=state.get("coalitions",{}) if isinstance(state,Mapping) else {}
    if not isinstance(rows,Mapping): return set()
    return {
        str(row.get("target_faction_ref")) for row in rows.values()
        if isinstance(row,Mapping) and faction_ref in {str(x) for x in row.get("member_faction_refs",[]) if isinstance(x,str)}
        and str(row.get("target_faction_ref") or "")
    }


def refresh_coalition_decision_view(
    state: Mapping[str, Any], *, at_iso: str, faction_refs: set[str], refresh: bool,
) -> tuple[dict[str, Any], dict[str, set[str]]]:
    """Return current relation state plus the bounded coalition target view.

    The bridge should orchestrate the monthly boundary, not implement coalition
    recomputation or rebuild the decision projection itself.
    """
    current = refresh_war_coalitions(state, at_iso=at_iso, faction_refs=faction_refs) if refresh else copy.deepcopy(dict(state))
    return current, {fid: coalition_target_refs_for_faction(current, fid) for fid in sorted(faction_refs)}

_TREATY_KINDS=frozenset({'non_aggression','mutual_defense','alliance','truce'})

def treaty_ref(a:str,b:str,kind:str)->str:
    parties=sorted((str(a),str(b)))
    digest=hashlib.sha256((parties[0]+'|'+parties[1]+'|'+str(kind)).encode('utf-8')).hexdigest()[:16]
    return f'treaty:{kind}:{digest}'

def active_treaty_kinds(state:Mapping[str,Any],a:str,b:str)->set[str]:
    rows=state.get('treaties',{}) if isinstance(state,Mapping) else {}
    if not isinstance(rows,Mapping): return set()
    parties={str(a),str(b)}
    return {
        str(row.get('kind')) for row in rows.values()
        if isinstance(row,Mapping) and row.get('status','active')=='active'
        and {str(x) for x in row.get('party_faction_refs',[]) if isinstance(x,str)}==parties
        and str(row.get('kind') or '') in _TREATY_KINDS
    }

def treaty_forbids_hostilities(state:Mapping[str,Any],a:str,b:str)->bool:
    return bool(active_treaty_kinds(state,a,b)&{'non_aggression','truce'})

def stage_treaty(state:Mapping[str,Any],*,a:str,b:str,kind:str,at_iso:str)->dict[str,Any]:
    if kind not in _TREATY_KINDS: raise ValueError('unsupported treaty kind')
    if not a or not b or a==b: raise ValueError('treaty parties invalid')
    out=copy.deepcopy(dict(state)); rows=out.setdefault('treaties',{})
    if not isinstance(rows,dict): raise ValueError('treaty registry invalid')
    ref=treaty_ref(a,b,kind)
    rows[ref]={'treaty_ref':ref,'kind':kind,'party_faction_refs':sorted((a,b)),'signed_at':str(at_iso),'status':'active'}
    return out

def end_treaty(state:Mapping[str,Any],*,a:str,b:str,kind:str)->dict[str,Any]:
    out=copy.deepcopy(dict(state)); rows=out.get('treaties',{})
    if not isinstance(rows,dict): return out
    rows.pop(treaty_ref(a,b,kind),None)
    if not rows: out.pop('treaties',None)
    return out
