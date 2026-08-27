"""Sparse current-equipment custody over static loadout definitions.

Policy composition is reusable game data. Hot state stores the exact people who
currently hold a policy issue and only per-person quantity/condition deviations.
This preserves conservation without serializing the same six-item kit per person.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[3]
_LOADOUTS = _ROOT / "game" / "data" / "martial-world" / "equipment-loadouts.json"


def _int_quantity(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("jianghu equipment quantity invalid")
    result = int(value)
    if result < 0:
        raise ValueError("jianghu equipment quantity cannot be negative")
    return result


def _condition(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("jianghu equipment condition invalid")
    result = int(value)
    if result < 0 or result > 1000:
        raise ValueError("jianghu equipment condition out of range")
    return result


@lru_cache(maxsize=1)
def _policies() -> dict[str, dict[str, Any]]:
    payload = json.loads(_LOADOUTS.read_text(encoding="utf-8"))
    rows = payload.get("policies", {}) if isinstance(payload, Mapping) else {}
    if not isinstance(rows, Mapping):
        raise ValueError("jianghu equipment loadout policy table invalid")
    return {str(ref): copy.deepcopy(dict(row)) for ref, row in rows.items() if isinstance(row, Mapping)}


def loadout_policy(policy_ref: str) -> dict[str, Any] | None:
    row = _policies().get(policy_ref)
    return copy.deepcopy(row) if row is not None else None


def _assignment_map(ledger: Mapping[str, Any]) -> dict[str, list[str]]:
    raw = ledger.get("policy_assignments", {})
    if raw in (None, {}):
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("jianghu equipment policy assignments invalid")
    result: dict[str, list[str]] = {}
    seen_people: set[str] = set()
    for policy_ref, people in raw.items():
        policy_ref = str(policy_ref)
        if loadout_policy(policy_ref) is None:
            raise ValueError(f"unknown equipment loadout policy: {policy_ref}")
        if not isinstance(people, list):
            raise ValueError("jianghu equipment policy assignment list invalid")
        normalized: list[str] = []
        for person_ref in people:
            if not isinstance(person_ref, str) or not person_ref:
                raise ValueError("jianghu equipment assignment person invalid")
            if person_ref in seen_people:
                raise ValueError(f"person assigned multiple equipment policies: {person_ref}")
            seen_people.add(person_ref)
            normalized.append(person_ref)
        if normalized:
            result[policy_ref] = sorted(set(normalized))
    return result


def assigned_policy(ledger: Mapping[str, Any], person_ref: str) -> str | None:
    for policy_ref, people in _assignment_map(ledger).items():
        if person_ref in people:
            return policy_ref
    return None


def _base_row(policy_ref: str | None) -> dict[str, Any]:
    if not policy_ref:
        return {"items": {}, "condition_milli": {}}
    policy = loadout_policy(policy_ref)
    if policy is None:
        raise ValueError(f"unknown equipment loadout policy: {policy_ref}")
    items = {
        str(ref): _int_quantity(qty)
        for ref, qty in (policy.get("items", {}) if isinstance(policy.get("items"), Mapping) else {}).items()
        if _int_quantity(qty) > 0
    }
    condition = {
        str(ref): _condition(value)
        for ref, value in (policy.get("condition_milli", {}) if isinstance(policy.get("condition_milli"), Mapping) else {}).items()
        if ref in items
    }
    return {
        "items": items,
        "condition_milli": condition,
        "policy_ref": policy_ref,
        "faction_ref": policy.get("faction_ref"),
    }


def effective_person_loadout(ledger: Mapping[str, Any], person_ref: str) -> dict[str, Any]:
    """Return a full logical current loadout for one person."""
    policy_ref = assigned_policy(ledger, person_ref)
    out = _base_row(policy_ref)
    rows = ledger.get("person_loadouts", {})
    raw = rows.get(person_ref, {}) if isinstance(rows, Mapping) else {}
    if raw not in (None, {}) and not isinstance(raw, Mapping):
        raise ValueError("jianghu person loadout override invalid")

    items = dict(out.get("items", {}))
    overrides = raw.get("items", {}) if isinstance(raw, Mapping) else {}
    if isinstance(overrides, Mapping):
        for ref, qty in overrides.items():
            amount = _int_quantity(qty)
            if amount:
                items[str(ref)] = amount
            else:
                items.pop(str(ref), None)
    out["items"] = items

    condition = dict(out.get("condition_milli", {}))
    cond_overrides = raw.get("condition_milli", {}) if isinstance(raw, Mapping) else {}
    if isinstance(cond_overrides, Mapping):
        for ref, value in cond_overrides.items():
            ref = str(ref)
            if ref in items:
                condition[ref] = _condition(value)
    # Full-integrity is the logical default for held durable items even when no
    # explicit static condition is needed.
    for ref in items:
        condition.setdefault(ref, 1000)
    out["condition_milli"] = condition
    return out


def hydrate_equipment_ledger(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Expand sparse policy custody into the ordinary logical loadout mapping."""
    assignments = _assignment_map(ledger)
    rows = ledger.get("person_loadouts", {})
    if rows in (None, {}):
        rows = {}
    if not isinstance(rows, Mapping):
        raise ValueError("jianghu person loadouts invalid")
    people = set(str(ref) for ref in rows)
    for refs in assignments.values():
        people.update(refs)
    out: dict[str, Any] = {
        "schema": str(ledger.get("schema", "jianghu-equipment-ledger-1.0")),
        "policy_assignments": copy.deepcopy(assignments),
        "person_loadouts": {ref: effective_person_loadout(ledger, ref) for ref in sorted(people)},
    }
    # Legal ownership exceptions and active recovery demands are sparse
    # property authority, not loadout presentation.  Hydration must preserve
    # them exactly or a read/modify/write command could erase ownership merely
    # by expanding policy-based equipment custody.
    provenance = ledger.get("provenance_exceptions", {})
    if provenance not in (None, {}):
        if not isinstance(provenance, Mapping):
            raise ValueError("jianghu equipment provenance exceptions invalid")
        out["provenance_exceptions"] = copy.deepcopy(dict(provenance))
    demands = ledger.get("recovery_demands", {})
    if demands not in (None, {}):
        if not isinstance(demands, Mapping):
            raise ValueError("jianghu property recovery demands invalid")
        out["recovery_demands"] = copy.deepcopy(dict(demands))
    return out


def compact_equipment_ledger(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize logical current custody to policy assignments + deviations."""
    assignments = _assignment_map(ledger)
    rows = ledger.get("person_loadouts", {})
    if rows in (None, {}):
        rows = {}
    if not isinstance(rows, Mapping):
        raise ValueError("jianghu person loadouts invalid")

    assigned: dict[str, str] = {}
    for policy_ref, refs in assignments.items():
        for ref in refs:
            assigned[ref] = policy_ref

    compact_rows: dict[str, dict[str, Any]] = {}
    people = set(str(ref) for ref in rows) | set(assigned)
    for person_ref in sorted(people):
        raw = rows.get(person_ref, {})
        if raw not in (None, {}) and not isinstance(raw, Mapping):
            raise ValueError("jianghu person loadout invalid")
        policy_ref = assigned.get(person_ref)
        base = _base_row(policy_ref)
        base_items = dict(base.get("items", {}))
        base_cond = dict(base.get("condition_milli", {}))

        # Treat an already-sparse row as an override over its assignment, while
        # a hydrated/full row naturally compares as an absolute current view.
        raw_items = raw.get("items", {}) if isinstance(raw, Mapping) else {}
        if not isinstance(raw_items, Mapping):
            raise ValueError("jianghu person loadout items invalid")
        if policy_ref and any(_int_quantity(v) == 0 for v in raw_items.values()):
            current_items = dict(base_items)
            for ref, qty in raw_items.items():
                amount = _int_quantity(qty)
                if amount:
                    current_items[str(ref)] = amount
                else:
                    current_items.pop(str(ref), None)
        elif policy_ref and set(raw_items) != set(base_items) and not raw.get("policy_ref"):
            # Sparse on-disk assignment override. Merge it rather than treating
            # omission of unchanged base items as a return.
            current_items = dict(base_items)
            for ref, qty in raw_items.items():
                amount = _int_quantity(qty)
                if amount:
                    current_items[str(ref)] = amount
                else:
                    current_items.pop(str(ref), None)
        else:
            current_items = {str(ref): _int_quantity(qty) for ref, qty in raw_items.items() if _int_quantity(qty) > 0}

        item_delta: dict[str, int] = {}
        if policy_ref:
            for ref in sorted(set(base_items) | set(current_items)):
                current = int(current_items.get(ref, 0))
                if current != int(base_items.get(ref, 0)):
                    item_delta[ref] = current
        else:
            item_delta = {ref: qty for ref, qty in sorted(current_items.items()) if qty > 0}

        raw_cond = raw.get("condition_milli", {}) if isinstance(raw, Mapping) else {}
        if not isinstance(raw_cond, Mapping):
            raise ValueError("jianghu person loadout condition invalid")
        cond_delta: dict[str, int] = {}
        for ref in sorted(current_items):
            if ref in raw_cond:
                current = _condition(raw_cond[ref])
            elif policy_ref and ref in base_cond:
                current = _condition(base_cond[ref])
            else:
                current = 1000
            baseline = _condition(base_cond.get(ref, 1000)) if policy_ref else 1000
            if current != baseline:
                cond_delta[ref] = current

        row: dict[str, Any] = {}
        if item_delta:
            row["items"] = item_delta
        if cond_delta:
            row["condition_milli"] = cond_delta
        if row:
            compact_rows[person_ref] = row

    out: dict[str, Any] = {"schema": str(ledger.get("schema", "jianghu-equipment-ledger-1.0"))}
    provenance = ledger.get("provenance_exceptions", {})
    if provenance not in (None,{}) and not isinstance(provenance, Mapping):
        raise ValueError("jianghu equipment provenance exceptions invalid")
    if isinstance(provenance, Mapping) and provenance:
        compact_provenance={}
        for holder_ref,items in provenance.items():
            if not isinstance(holder_ref,str) or not isinstance(items,Mapping):raise ValueError("jianghu equipment provenance holder invalid")
            rows={}
            for item_ref,claim in items.items():
                if not isinstance(item_ref,str) or not isinstance(claim,Mapping):raise ValueError("jianghu equipment provenance claim invalid")
                owner_ref=claim.get("owner_ref"); quantity=_int_quantity(claim.get("quantity",0))
                if not isinstance(owner_ref,str) or not owner_ref or quantity<=0:continue
                row={"owner_ref":owner_ref,"quantity":quantity}
                if isinstance(claim.get("property_ref"),str) and claim.get("property_ref"):row["property_ref"]=str(claim["property_ref"])
                if isinstance(claim.get("status"),str) and claim.get("status"):row["status"]=str(claim["status"])
                rows[item_ref]=row
            if rows:compact_provenance[holder_ref]=rows
        if compact_provenance:out["provenance_exceptions"]=compact_provenance
    demands = ledger.get("recovery_demands", {})
    if demands not in (None,{}) and not isinstance(demands, Mapping):
        raise ValueError("jianghu property recovery demands invalid")
    if isinstance(demands, Mapping) and demands:
        compact_demands={}
        for demand_ref,raw in demands.items():
            if not isinstance(demand_ref,str) or not isinstance(raw,Mapping):
                raise ValueError("jianghu property recovery demand invalid")
            owner_ref=raw.get("owner_ref"); holder_ref=raw.get("holder_ref"); item_ref=raw.get("item_ref")
            quantity=_int_quantity(raw.get("quantity",0)); status=str(raw.get("status") or "active")
            if not all(isinstance(x,str) and x for x in (owner_ref,holder_ref,item_ref)) or quantity<=0 or status not in {"active","recovered","waived"}:
                continue
            row={"owner_ref":str(owner_ref),"holder_ref":str(holder_ref),"item_ref":str(item_ref),"quantity":quantity,"status":status}
            for key in ("issued_at","property_ref","evidence_ref"):
                if isinstance(raw.get(key),str) and raw.get(key):row[key]=str(raw[key])
            compact_demands[demand_ref]=row
        if compact_demands:out["recovery_demands"]=compact_demands
    if assignments:
        out["policy_assignments"] = assignments
    if compact_rows:
        out["person_loadouts"] = compact_rows
    return out


__all__ = [
    "assigned_policy",
    "compact_equipment_ledger",
    "effective_person_loadout",
    "hydrate_equipment_ledger",
    "loadout_policy",
]
