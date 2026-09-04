"""Sparse legal ownership/provenance over physical equipment custody.

Quantity remains owned by the equipment ledger.  This module stores only the
exception where current holder and legal owner differ; ordinary personally
owned goods need no extra record.  Faction policy issue ownership is derived
from the static loadout policy rather than duplicated in state.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .equipment_state import assigned_policy, compact_equipment_ledger, effective_person_loadout, hydrate_equipment_ledger, loadout_policy


def recovery_demand_ref(*, owner_ref: str, holder_ref: str, item_ref: str) -> str:
    return f"recovery:{owner_ref}:{holder_ref}:{item_ref}"


def issue_recovery_demand(
    ledger: Mapping[str,Any], *, owner_ref: str, holder_ref: str, item_ref: str,
    quantity: int, issued_at: str, evidence_ref: str | None = None,
    property_ref: str | None = None,
) -> dict[str,Any]:
    """Persist one current known demand for return of physically held property.

    The demand exists only when the owner knows who currently holds the item.
    It is current state, not an append-only grievance log. Re-issuing the same
    demand updates quantity/evidence while preserving a single sparse owner.
    """
    if not owner_ref or not holder_ref or not item_ref or owner_ref == holder_ref or int(quantity) <= 0:
        raise ValueError("property recovery demand invalid")
    out=copy.deepcopy(dict(ledger)); demands=out.setdefault("recovery_demands",{})
    if not isinstance(demands,dict):raise ValueError("jianghu property recovery demands invalid")
    ref=recovery_demand_ref(owner_ref=owner_ref,holder_ref=holder_ref,item_ref=item_ref)
    row={"owner_ref":str(owner_ref),"holder_ref":str(holder_ref),"item_ref":str(item_ref),"quantity":int(quantity),"status":"active","issued_at":str(issued_at)}
    if evidence_ref:row["evidence_ref"]=str(evidence_ref)
    if property_ref:row["property_ref"]=str(property_ref)
    demands[ref]=row
    return out


def clear_recovery_demand(
    ledger: Mapping[str,Any], *, owner_ref: str, holder_ref: str, item_ref: str,
) -> dict[str,Any]:
    out=copy.deepcopy(dict(ledger)); demands=out.get("recovery_demands",{})
    if demands in (None,{}):return out
    if not isinstance(demands,dict):raise ValueError("jianghu property recovery demands invalid")
    demands.pop(recovery_demand_ref(owner_ref=owner_ref,holder_ref=holder_ref,item_ref=item_ref),None)
    if not demands:out.pop("recovery_demands",None)
    return out


def active_recovery_demands(ledger: Mapping[str,Any], *, owner_ref: str | None = None, holder_ref: str | None = None) -> list[dict[str,Any]]:
    raw=ledger.get("recovery_demands",{}) if isinstance(ledger,Mapping) else {}
    if raw in (None,{}):return []
    if not isinstance(raw,Mapping):raise ValueError("jianghu property recovery demands invalid")
    rows=[]
    for demand_ref,row in raw.items():
        if not isinstance(demand_ref,str) or not isinstance(row,Mapping) or row.get("status","active")!="active":continue
        if owner_ref is not None and row.get("owner_ref")!=owner_ref:continue
        if holder_ref is not None and row.get("holder_ref")!=holder_ref:continue
        rows.append({"demand_ref":demand_ref,**dict(row)})
    rows.sort(key=lambda x:(str(x.get("owner_ref")),str(x.get("holder_ref")),str(x.get("item_ref"))))
    return rows


def policy_owned_quantity(ledger: Mapping[str,Any], holder_ref: str, item_ref: str) -> int:
    policy_ref=assigned_policy(ledger,holder_ref)
    policy=loadout_policy(policy_ref) if isinstance(policy_ref,str) else None
    if not isinstance(policy,Mapping):return 0
    items=policy.get('items',{}) if isinstance(policy.get('items'),Mapping) else {}
    base=max(0,int(items.get(item_ref,0)))
    current=max(0,int(effective_person_loadout(ledger,holder_ref).get('items',{}).get(item_ref,0)))
    return min(base,current)


def provenance_claim(ledger: Mapping[str,Any], holder_ref: str, item_ref: str) -> dict[str,Any]|None:
    p=ledger.get('provenance_exceptions',{}) if isinstance(ledger,Mapping) else {}
    row=p.get(holder_ref,{}) if isinstance(p,Mapping) else {}
    claim=row.get(item_ref) if isinstance(row,Mapping) else None
    return dict(claim) if isinstance(claim,Mapping) else None


def personally_owned_quantity(ledger: Mapping[str,Any], holder_ref: str, item_ref: str) -> int:
    held=max(0,int(effective_person_loadout(ledger,holder_ref).get('items',{}).get(item_ref,0)))
    policy_owned=policy_owned_quantity(ledger,holder_ref,item_ref)
    claim=provenance_claim(ledger,holder_ref,item_ref)
    other=max(0,int(claim.get('quantity',0))) if isinstance(claim,Mapping) and claim.get('owner_ref')!=holder_ref else 0
    return max(0,held-policy_owned-other)


def set_nonholder_claim(ledger: Mapping[str,Any], *, holder_ref: str, item_ref: str, owner_ref: str, quantity: int, property_ref: str|None=None, status: str='held_by_other') -> dict[str,Any]:
    out=copy.deepcopy(dict(ledger)); prov=out.setdefault('provenance_exceptions',{})
    if not isinstance(prov,dict):raise ValueError('jianghu equipment provenance invalid')
    rows=prov.setdefault(holder_ref,{})
    if not isinstance(rows,dict):raise ValueError('jianghu equipment provenance holder invalid')
    if quantity<=0 or owner_ref==holder_ref:
        rows.pop(item_ref,None)
        if not rows:prov.pop(holder_ref,None)
        if not prov:out.pop('provenance_exceptions',None)
        return out
    row={'owner_ref':str(owner_ref),'quantity':int(quantity),'status':str(status)}
    if property_ref:row['property_ref']=str(property_ref)
    rows[item_ref]=row
    return out


def move_claim_after_seizure(ledger: Mapping[str,Any], *, from_holder: str, to_holder: str, item_ref: str, quantity: int, original_owner_ref: str|None=None, property_ref: str|None=None) -> dict[str,Any]:
    """Move non-holder legal ownership with a physical seizure.

    The new holder gains possession only.  Legal ownership remains with the
    prior owner (or prior holder when the seized property was personally owned).
    """
    if quantity<=0:raise ValueError('quantity invalid')
    claim=provenance_claim(ledger,from_holder,item_ref)
    owner=str(claim.get('owner_ref')) if isinstance(claim,Mapping) and claim.get('owner_ref') else str(original_owner_ref or from_holder)
    existing=provenance_claim(ledger,to_holder,item_ref)
    if isinstance(existing,Mapping) and existing.get('owner_ref') not in {None,owner}:
        raise ValueError('mixed legal owners require separate property identity')
    current=max(0,int(existing.get('quantity',0))) if isinstance(existing,Mapping) else 0
    out=set_nonholder_claim(ledger,holder_ref=to_holder,item_ref=item_ref,owner_ref=owner,quantity=current+quantity,property_ref=property_ref or (str(claim.get('property_ref')) if isinstance(claim,Mapping) and claim.get('property_ref') else None),status='seized')
    if isinstance(claim,Mapping):
        remaining=max(0,int(claim.get('quantity',0))-quantity)
        out=set_nonholder_claim(out,holder_ref=from_holder,item_ref=item_ref,owner_ref=owner,quantity=remaining,property_ref=str(claim.get('property_ref')) if claim.get('property_ref') else None,status=str(claim.get('status') or 'held_by_other'))
    return out


def property_evidence_ref(ledger: Mapping[str,Any], *, holder_ref: str, item_ref: str) -> str | None:
    """Return the compact evidence key for a live non-holder ownership claim.

    The key is intentionally only a routing token.  Crime/government reducers
    must re-read and validate the current provenance claim instead of trusting
    caller-supplied ownership prose.
    """
    claim=provenance_claim(ledger,holder_ref,item_ref)
    if not isinstance(claim,Mapping):
        return None
    owner=str(claim.get('owner_ref') or '')
    if not owner or owner==holder_ref or int(claim.get('quantity',0))<=0:
        return None
    return f'property_claim:{holder_ref}:{item_ref}'


def validate_property_evidence(ledger: Mapping[str,Any], evidence_ref: str, *, holder_ref: str) -> dict[str,Any] | None:
    """Resolve a caller evidence token only when a live claim proves it."""
    prefix=f'property_claim:{holder_ref}:'
    if not isinstance(evidence_ref,str) or not evidence_ref.startswith(prefix):
        return None
    item_ref=evidence_ref[len(prefix):]
    if not item_ref or ':' in item_ref:
        return None
    claim=provenance_claim(ledger,holder_ref,item_ref)
    if not isinstance(claim,Mapping):
        return None
    owner=str(claim.get('owner_ref') or '')
    if not owner or owner==holder_ref or int(claim.get('quantity',0))<=0:
        return None
    out=dict(claim); out['holder_ref']=holder_ref; out['item_ref']=item_ref
    return out


def detach_faction_policy_holders(
    ledger: Mapping[str, Any], *, source_faction_ref: str, holder_refs: list[str] | tuple[str, ...] | set[str],
) -> dict[str, Any]:
    """Materialize source-issued gear when exact people leave that faction."""
    source = str(source_faction_ref or "")
    departing = {str(ref) for ref in holder_refs if isinstance(ref, str) and str(ref)}
    if not source:
        raise ValueError("institutional equipment source invalid")
    if not departing:
        return {
            "equipment_ledger_after": compact_equipment_ledger(ledger),
            "detached_policy_holder_count": 0,
            "materialized_claim_count": 0,
        }
    out = hydrate_equipment_ledger(ledger)
    assignments = out.get("policy_assignments", {})
    if assignments in (None, {}):
        assignments = {}; out["policy_assignments"] = assignments
    if not isinstance(assignments, dict):
        raise ValueError("jianghu equipment policy assignments invalid")
    loadouts = out.setdefault("person_loadouts", {})
    if not isinstance(loadouts, dict):
        raise ValueError("jianghu person loadouts invalid")
    provenance = out.setdefault("provenance_exceptions", {})
    if not isinstance(provenance, dict):
        raise ValueError("jianghu equipment provenance invalid")
    detached = 0; materialized_claims = 0
    for policy_ref, raw_refs in list(assignments.items()):
        policy = loadout_policy(str(policy_ref))
        if not isinstance(policy, Mapping) or str(policy.get("faction_ref") or "") != source:
            continue
        if not isinstance(raw_refs, list):
            raise ValueError("jianghu equipment policy assignment list invalid")
        base_items = policy.get("items", {}) if isinstance(policy.get("items"), Mapping) else {}
        kept: list[str] = []
        for holder_ref in raw_refs:
            holder = str(holder_ref or "")
            if holder not in departing:
                kept.append(holder); continue
            row = loadouts.get(holder)
            if not isinstance(row, Mapping):
                row = effective_person_loadout(out, holder); loadouts[holder] = copy.deepcopy(dict(row))
            current_items = row.get("items", {}) if isinstance(row.get("items"), Mapping) else {}
            holder_claims = provenance.setdefault(holder, {})
            if not isinstance(holder_claims, dict):
                raise ValueError("jianghu equipment provenance holder invalid")
            for item_ref, raw_base in base_items.items():
                item = str(item_ref); base_qty = max(0, int(raw_base)); current_qty = max(0, int(current_items.get(item, 0)))
                policy_qty = min(base_qty, current_qty)
                if policy_qty <= 0: continue
                existing = holder_claims.get(item); existing_qty = 0; property_ref = None
                if isinstance(existing, Mapping):
                    existing_owner = str(existing.get("owner_ref") or "")
                    if existing_owner and existing_owner != source:
                        raise ValueError("mixed legal owners require separate property identity")
                    existing_qty = max(0, int(existing.get("quantity", 0)))
                    if isinstance(existing.get("property_ref"), str) and existing.get("property_ref"):
                        property_ref = str(existing.get("property_ref"))
                total = min(current_qty, policy_qty + existing_qty)
                claim = {"owner_ref": source, "quantity": total, "status": "former_policy_issue"}
                if property_ref: claim["property_ref"] = property_ref
                holder_claims[item] = claim; materialized_claims += 1
            detached += 1
        if kept: assignments[str(policy_ref)] = sorted(set(kept))
        else: assignments.pop(str(policy_ref), None)
    if not assignments: out.pop("policy_assignments", None)
    if not provenance: out.pop("provenance_exceptions", None)
    return {
        "equipment_ledger_after": compact_equipment_ledger(out),
        "detached_policy_holder_count": detached,
        "materialized_claim_count": materialized_claims,
    }


def transfer_faction_property_authority(
    ledger: Mapping[str, Any], *, source_faction_ref: str, target_faction_ref: str,
) -> dict[str, Any]:
    """Transfer all current legal equipment authority in an absorbed estate.

    Physical custody never moves here. Explicit non-holder claims and recovery
    demands are re-keyed to the successor institution. Source-policy issues are
    materialized into exact holder loadouts before the policy assignment is
    removed, then the successor institution receives a sparse provenance claim
    for only the quantities formerly owned by that source policy.
    """
    source = str(source_faction_ref or "")
    target = str(target_faction_ref or "")
    if not source or not target or source == target:
        raise ValueError("institutional property transfer refs invalid")

    out = hydrate_equipment_ledger(ledger)
    provenance = out.setdefault("provenance_exceptions", {})
    if not isinstance(provenance, dict):
        raise ValueError("jianghu equipment provenance invalid")

    transferred_claims = 0
    for holder_ref, item_rows in list(provenance.items()):
        if not isinstance(item_rows, dict):
            raise ValueError("jianghu equipment provenance holder invalid")
        for item_ref, raw_claim in list(item_rows.items()):
            if not isinstance(raw_claim, Mapping) or str(raw_claim.get("owner_ref") or "") != source:
                continue
            row = copy.deepcopy(dict(raw_claim))
            row["owner_ref"] = target
            item_rows[str(item_ref)] = row
            transferred_claims += 1

    assignments = out.get("policy_assignments", {})
    if assignments in (None, {}):
        assignments = {}
        out["policy_assignments"] = assignments
    if not isinstance(assignments, dict):
        raise ValueError("jianghu equipment policy assignments invalid")
    loadouts = out.setdefault("person_loadouts", {})
    if not isinstance(loadouts, dict):
        raise ValueError("jianghu person loadouts invalid")

    materialized_policy_holders = 0
    for policy_ref, refs in list(assignments.items()):
        policy = loadout_policy(str(policy_ref))
        if not isinstance(policy, Mapping) or str(policy.get("faction_ref") or "") != source:
            continue
        if not isinstance(refs, list):
            raise ValueError("jianghu equipment policy assignment list invalid")
        base_items = policy.get("items", {}) if isinstance(policy.get("items"), Mapping) else {}
        for holder_ref in list(refs):
            if not isinstance(holder_ref, str) or not holder_ref:
                continue
            row = loadouts.get(holder_ref)
            if not isinstance(row, Mapping):
                row = effective_person_loadout(out, holder_ref)
                loadouts[holder_ref] = copy.deepcopy(dict(row))
            current_items = row.get("items", {}) if isinstance(row.get("items"), Mapping) else {}
            for item_ref, raw_base_qty in base_items.items():
                qty = min(max(0, int(raw_base_qty)), max(0, int(current_items.get(str(item_ref), 0))))
                if qty <= 0:
                    continue
                holder_claims = provenance.setdefault(holder_ref, {})
                if not isinstance(holder_claims, dict):
                    raise ValueError("jianghu equipment provenance holder invalid")
                existing = holder_claims.get(str(item_ref))
                existing_qty = 0
                if isinstance(existing, Mapping):
                    existing_owner = str(existing.get("owner_ref") or "")
                    if existing_owner != target:
                        raise ValueError("mixed legal owners require separate property identity")
                    existing_qty = max(0, int(existing.get("quantity", 0)))
                total = existing_qty + qty
                if total > max(0, int(current_items.get(str(item_ref), 0))):
                    raise ValueError("institutional property claim exceeds physical custody")
                holder_claims[str(item_ref)] = {
                    "owner_ref": target, "quantity": total, "status": "institutional_successor",
                }
                transferred_claims += 1
            materialized_policy_holders += 1
        assignments.pop(policy_ref, None)

    if not assignments:
        out.pop("policy_assignments", None)
    if not provenance:
        out.pop("provenance_exceptions", None)

    raw_demands = out.get("recovery_demands", {})
    if raw_demands not in (None, {}) and not isinstance(raw_demands, Mapping):
        raise ValueError("jianghu property recovery demands invalid")
    transferred_demands = 0
    closed_demands = 0
    if isinstance(raw_demands, Mapping) and raw_demands:
        rebuilt: dict[str, dict[str, Any]] = {}
        for demand_ref, raw in raw_demands.items():
            if not isinstance(raw, Mapping):
                continue
            owner_ref = str(raw.get("owner_ref") or "")
            if owner_ref != source:
                rebuilt[str(demand_ref)] = copy.deepcopy(dict(raw))
                continue
            holder_ref = str(raw.get("holder_ref") or "")
            item_ref = str(raw.get("item_ref") or "")
            claim = provenance_claim(out, holder_ref, item_ref) if holder_ref and item_ref else None
            if not isinstance(claim, Mapping) or str(claim.get("owner_ref") or "") != target:
                closed_demands += 1
                continue
            qty = min(max(0, int(raw.get("quantity", 0))), max(0, int(claim.get("quantity", 0))))
            if qty <= 0:
                closed_demands += 1
                continue
            row = copy.deepcopy(dict(raw))
            row["owner_ref"] = target
            row["quantity"] = qty
            ref = recovery_demand_ref(owner_ref=target, holder_ref=holder_ref, item_ref=item_ref)
            rebuilt[ref] = row
            transferred_demands += 1
        if rebuilt:
            out["recovery_demands"] = rebuilt
        else:
            out.pop("recovery_demands", None)

    return {
        "equipment_ledger_after": compact_equipment_ledger(out),
        "transferred_claim_count": transferred_claims,
        "transferred_recovery_demand_count": transferred_demands,
        "closed_recovery_demand_count": closed_demands,
        "materialized_policy_holder_count": materialized_policy_holders,
    }


__all__=['active_recovery_demands','detach_faction_policy_holders','clear_recovery_demand','issue_recovery_demand','move_claim_after_seizure','personally_owned_quantity','policy_owned_quantity','property_evidence_ref','provenance_claim','recovery_demand_ref','set_nonholder_claim','transfer_faction_property_authority','validate_property_evidence']
