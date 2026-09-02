"""Bounded faction-armory custody for live strategic operations.

Faction inventories own aggregate armory stock.  Exact combat, however, can use
only equipment that an exact person currently carries.  This module bridges the
two authorities without materializing permanent loadouts for every faction
member: a live operation may issue finite stock to its exact participants, keep
one compact return obligation on the deployment, and reconcile surviving stock
when the operation closes.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from .equipment_state import compact_equipment_ledger, effective_person_loadout, hydrate_equipment_ledger
from .property import provenance_claim, set_nonholder_claim

_ROOT = Path(__file__).resolve().parents[3]
_EQUIPMENT = _ROOT / "game" / "data" / "martial-world" / "equipment.json"
_IDENTITIES = _ROOT / "game" / "data" / "martial-world" / "faction-identities.json"

_OPERATIONAL_KINDS = frozenset({"faction_raid", "faction_war_strike", "custody_rescue", "faction_reconnaissance", "allied_defense_reinforcement", "route_attack"})
_RANGED_AMMO_PER_BOW = 12


@lru_cache(maxsize=1)
def _weapon_catalog() -> dict[str, dict[str, Any]]:
    payload = json.loads(_EQUIPMENT.read_text(encoding="utf-8"))
    rows = payload.get("weapon_catalog", {}) if isinstance(payload, Mapping) else {}
    if not isinstance(rows, Mapping):
        raise ValueError("jianghu equipment weapon catalog invalid")
    return {str(ref): copy.deepcopy(dict(row)) for ref, row in rows.items() if isinstance(row, Mapping)}


@lru_cache(maxsize=1)
def _authored_weapon_preferences() -> dict[str, tuple[str, ...]]:
    payload = json.loads(_IDENTITIES.read_text(encoding="utf-8"))
    rows = payload.get("identities", {}) if isinstance(payload, Mapping) else {}
    if not isinstance(rows, Mapping):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for faction_ref, raw in rows.items():
        if not isinstance(raw, Mapping):
            continue
        refs = tuple(str(x) for x in raw.get("weapons", []) if isinstance(x, str) and x)
        if refs:
            out[str(faction_ref)] = refs
    return out


def _skills(person: Mapping[str, Any]) -> Mapping[str, Any]:
    rows = person.get("martial_skills", {}) if isinstance(person, Mapping) else {}
    return rows if isinstance(rows, Mapping) else {}


def _usable_weapon_refs(items: Mapping[str, Any]) -> list[str]:
    weapons = _weapon_catalog()
    out: list[str] = []
    for ref, raw_qty in items.items():
        try:
            qty = int(raw_qty)
        except (TypeError, ValueError):
            continue
        row = weapons.get(str(ref))
        if qty <= 0 or not isinstance(row, Mapping):
            continue
        discipline = str(row.get("discipline") or "")
        if discipline == "bow":
            if int(items.get("item_arrow", 0)) > 0:
                out.append(str(ref))
        elif discipline in {"sword", "spear", "hidden_weapons"}:
            out.append(str(ref))
    return out


def _weapon_score(
    *, faction_ref: str, person: Mapping[str, Any], weapon_ref: str, row: Mapping[str, Any],
) -> tuple[int, int, int, str]:
    def _n(key: str) -> int:
        value = row.get(key, 0)
        return max(0, int(value)) if isinstance(value, (int, float)) else 0

    discipline = str(row.get("discipline") or "")
    skill = max(0, int(_skills(person).get(discipline, 0)))
    preferred = weapon_ref in set(_authored_weapon_preferences().get(faction_ref, ()))
    combat_value = _n("impact") + _n("cut") + _n("pierce") + _n("penetration") + _n("precision") + _n("control")
    # Authored institutional weapon identity breaks close ties, while the exact
    # person's actual discipline remains the dominant competence signal.  Raw
    # handling alone must not make a dagger displace a full battlefield sword.
    return (skill, 1 if preferred else 0, combat_value, weapon_ref)


def issue_operation_equipment(
    *, operation: Mapping[str, Any], faction_ref: str, participant_refs: Sequence[str],
    people_by_ref: Mapping[str, Mapping[str, Any]], inventory: Mapping[str, Any],
    equipment_ledger: Mapping[str, Any],
) -> dict[str, Any]:
    """Issue finite armory weapons to otherwise-unarmed combat participants.

    The function is transaction-safe over detached after-images.  Calling it a
    second time for an operation that already owns ``issued_equipment`` is a
    no-op, preventing retries from double-debiting stock.
    """
    op = copy.deepcopy(dict(operation))
    inv = copy.deepcopy(dict(inventory))
    ledger = hydrate_equipment_ledger(equipment_ledger)
    existing_issue = op.get("issued_equipment")
    if isinstance(existing_issue, Mapping) and existing_issue:
        return {
            "operation_after": op, "inventory_after": inv,
            "equipment_ledger_after": compact_equipment_ledger(ledger),
            "issued_person_count": 0, "issued_item_count": 0,
        }
    if str(op.get("operation_kind") or "") not in _OPERATIONAL_KINDS:
        return {
            "operation_after": op, "inventory_after": inv,
            "equipment_ledger_after": compact_equipment_ledger(ledger),
            "issued_person_count": 0, "issued_item_count": 0,
        }

    stock = inv.setdefault("equipment", {})
    if not isinstance(stock, dict):
        raise ValueError("jianghu faction equipment stock invalid")
    weapons = _weapon_catalog()
    loadouts = ledger.setdefault("person_loadouts", {})
    if not isinstance(loadouts, dict):
        raise ValueError("jianghu person loadouts invalid")
    issued: dict[str, dict[str, int]] = {}
    baseline: dict[str, dict[str, int]] = {}
    baseline_claimed: dict[str, dict[str, int]] = {}

    for person_ref in [str(x) for x in participant_refs if isinstance(x, str) and x]:
        person = people_by_ref.get(person_ref)
        if not isinstance(person, Mapping):
            continue
        logical = effective_person_loadout(ledger, person_ref)
        logical_items = logical.get("items", {}) if isinstance(logical.get("items"), Mapping) else {}
        if _usable_weapon_refs(logical_items):
            continue

        candidates: list[tuple[tuple[int, int, int, str], str]] = []
        for weapon_ref, row in weapons.items():
            if max(0, int(stock.get(weapon_ref, 0))) <= 0:
                continue
            discipline = str(row.get("discipline") or "")
            if discipline not in {"sword", "spear", "bow", "hidden_weapons"}:
                continue
            if discipline == "bow" and max(0, int(stock.get("item_arrow", 0))) <= 0:
                continue
            weapon_claim = provenance_claim(ledger, person_ref, weapon_ref)
            if isinstance(weapon_claim, Mapping) and str(weapon_claim.get("owner_ref") or "") not in {"", faction_ref}:
                # One fungible item row can represent personal remainder plus one
                # non-holder legal owner, but not two different external owners.
                # Avoid creating an ownership state the property ledger cannot
                # faithfully express if this issued item is later separated.
                continue
            if discipline == "bow":
                arrow_claim = provenance_claim(ledger, person_ref, "item_arrow")
                if isinstance(arrow_claim, Mapping) and str(arrow_claim.get("owner_ref") or "") not in {"", faction_ref}:
                    continue
            candidates.append((_weapon_score(
                faction_ref=faction_ref, person=person, weapon_ref=weapon_ref, row=row,
            ), weapon_ref))
        if not candidates:
            continue
        weapon_ref = max(candidates, key=lambda row: row[0])[1]
        weapon = weapons[weapon_ref]

        row = loadouts.setdefault(person_ref, {"items": {}, "condition_milli": {}})
        if not isinstance(row, dict):
            raise ValueError("jianghu person loadout invalid")
        items = row.setdefault("items", {})
        cond = row.setdefault("condition_milli", {})
        if not isinstance(items, dict) or not isinstance(cond, dict):
            raise ValueError("jianghu person loadout invalid")
        person_baseline: dict[str, int] = {weapon_ref: max(0, int(items.get(weapon_ref, 0)))}
        weapon_claim = provenance_claim(ledger, person_ref, weapon_ref)
        person_claimed: dict[str, int] = {
            weapon_ref: max(0, int(weapon_claim.get("quantity", 0))) if isinstance(weapon_claim, Mapping) else 0
        }
        stock[weapon_ref] = max(0, int(stock.get(weapon_ref, 0))) - 1
        if stock[weapon_ref] <= 0:
            stock.pop(weapon_ref, None)
        items[weapon_ref] = max(0, int(items.get(weapon_ref, 0))) + 1
        cond.setdefault(weapon_ref, 1000)
        person_issue = {weapon_ref: 1}

        if str(weapon.get("discipline") or "") == "bow":
            arrow_claim = provenance_claim(ledger, person_ref, "item_arrow")
            arrows = min(_RANGED_AMMO_PER_BOW, max(0, int(stock.get("item_arrow", 0))))
            if arrows > 0:
                person_baseline["item_arrow"] = max(0, int(items.get("item_arrow", 0)))
                person_claimed["item_arrow"] = max(0, int(arrow_claim.get("quantity", 0))) if isinstance(arrow_claim, Mapping) else 0
                stock["item_arrow"] = max(0, int(stock.get("item_arrow", 0))) - arrows
                if stock["item_arrow"] <= 0:
                    stock.pop("item_arrow", None)
                items["item_arrow"] = max(0, int(items.get("item_arrow", 0))) + arrows
                person_issue["item_arrow"] = arrows
        issued[person_ref] = person_issue
        baseline[person_ref] = person_baseline
        baseline_claimed[person_ref] = person_claimed

    if issued:
        op["issued_equipment"] = issued
        op["issued_equipment_baseline"] = baseline
        op["issued_equipment_claim_baseline"] = baseline_claimed
    return {
        "operation_after": op,
        "inventory_after": inv,
        "equipment_ledger_after": compact_equipment_ledger(ledger),
        "issued_person_count": len(issued),
        "issued_item_count": sum(sum(items.values()) for items in issued.values()),
    }


def materialize_faction_field_equipment(
    *,
    faction_ref: str,
    participant_refs: Sequence[str],
    people_by_ref: Mapping[str, Mapping[str, Any]],
    inventory: Mapping[str, Any],
    equipment_ledger: Mapping[str, Any],
    status: str = "field_issue_in_custody",
) -> dict[str, Any]:
    """Conservatively materialize aggregate armory stock onto exact field holders.

    This is for bounded contacts that do not own a long-lived deployment row,
    such as a spontaneous route interception. It reuses the same finite issue
    policy as strategic operations, then immediately detaches the short-lived
    return obligation into explicit source-faction title. Physical items remain
    on the exact people who carry them; nothing teleports back when the contact
    ends and nothing is created from skill or faction identity alone.

    Re-entry is naturally idempotent: already armed people are skipped by
    ``issue_operation_equipment`` and aggregate stock is debited only for newly
    materialized exact items.
    """
    synthetic_owner = {"operation_kind": "route_attack"}
    issued = issue_operation_equipment(
        operation=synthetic_owner,
        faction_ref=faction_ref,
        participant_refs=participant_refs,
        people_by_ref=people_by_ref,
        inventory=inventory,
        equipment_ledger=equipment_ledger,
    )
    issued_count = max(0, int(issued.get("issued_person_count", 0)))
    issued_item_count = max(0, int(issued.get("issued_item_count", 0)))
    ledger_after = issued["equipment_ledger_after"]
    if issued_count <= 0:
        return {
            "inventory_after": copy.deepcopy(dict(issued["inventory_after"])),
            "equipment_ledger_after": copy.deepcopy(dict(ledger_after)),
            "materialized_person_count": 0,
            "materialized_item_count": 0,
        }
    detached = detach_operation_issue_holders(
        operation=issued["operation_after"],
        source_faction_ref=faction_ref,
        holder_refs=participant_refs,
        equipment_ledger=ledger_after,
        status=status,
    )
    operation_after = detached.get("operation_after", {})
    if isinstance(operation_after, Mapping) and operation_after.get("issued_equipment"):
        raise ValueError("field issue retained transient return obligation")
    return {
        "inventory_after": copy.deepcopy(dict(issued["inventory_after"])),
        "equipment_ledger_after": copy.deepcopy(dict(detached["equipment_ledger_after"])),
        "materialized_person_count": issued_count,
        "materialized_item_count": issued_item_count,
    }


def detach_operation_issue_holders(
    *, operation: Mapping[str, Any], source_faction_ref: str, holder_refs: Sequence[str],
    equipment_ledger: Mapping[str, Any], status: str = "operation_issue_separated",
) -> dict[str, Any]:
    """Detach separated live holders without teleporting source-owned gear.

    The exact item remains in the holder's physical loadout.  Only the sparse
    operation return obligation is removed; legal title to the still-held
    issued increment becomes an explicit provenance claim for the source
    faction.  This is the live-separation analogue of corpse-held issue cleanup.
    """
    op = copy.deepcopy(dict(operation))
    ledger = hydrate_equipment_ledger(equipment_ledger)
    holders = {str(ref) for ref in holder_refs if isinstance(ref, str) and ref}
    issued = op.get("issued_equipment", {})
    baseline = op.get("issued_equipment_baseline", {}) if isinstance(op.get("issued_equipment_baseline"), Mapping) else {}
    claim_baseline = op.get("issued_equipment_claim_baseline", {}) if isinstance(op.get("issued_equipment_claim_baseline"), Mapping) else {}
    if not isinstance(issued, Mapping) or not issued or not holders:
        return {"operation_after": op, "equipment_ledger_after": compact_equipment_ledger(ledger), "detached_holder_count": 0}

    detached = 0
    remaining_issued = copy.deepcopy(dict(issued))
    remaining_baseline = copy.deepcopy(dict(baseline)) if isinstance(baseline, Mapping) else {}
    remaining_claim_baseline = copy.deepcopy(dict(claim_baseline)) if isinstance(claim_baseline, Mapping) else {}
    for holder_ref in sorted(holders):
        raw_items = issued.get(holder_ref)
        if not isinstance(raw_items, Mapping):
            continue
        held_items = effective_person_loadout(ledger, holder_ref).get("items", {})
        held_items = held_items if isinstance(held_items, Mapping) else {}
        holder_baseline = baseline.get(holder_ref, {}) if isinstance(baseline, Mapping) else {}
        holder_claim_baseline = claim_baseline.get(holder_ref, {}) if isinstance(claim_baseline, Mapping) else {}
        for item_ref, raw_qty in raw_items.items():
            qty = max(0, int(raw_qty))
            if qty <= 0:
                continue
            held = max(0, int(held_items.get(str(item_ref), 0)))
            before = max(0, int(holder_baseline.get(str(item_ref), 0))) if isinstance(holder_baseline, Mapping) else 0
            issued_still_held = min(qty, max(0, held - before))
            if issued_still_held <= 0:
                continue
            prior_claim = provenance_claim(ledger, holder_ref, str(item_ref))
            prior_owner = str(prior_claim.get("owner_ref") or "") if isinstance(prior_claim, Mapping) else ""
            prior_qty = max(0, int(prior_claim.get("quantity", 0))) if isinstance(prior_claim, Mapping) else 0
            expected_prior_qty = max(0, int(holder_claim_baseline.get(str(item_ref), 0))) if isinstance(holder_claim_baseline, Mapping) else 0
            if prior_owner and prior_owner != source_faction_ref:
                raise ValueError("mixed legal owners require separate property identity")
            claim_qty = min(held, max(prior_qty, expected_prior_qty) + issued_still_held)
            ledger = set_nonholder_claim(
                ledger, holder_ref=holder_ref, item_ref=str(item_ref), owner_ref=source_faction_ref,
                quantity=claim_qty, status=status,
            )
        remaining_issued.pop(holder_ref, None)
        remaining_baseline.pop(holder_ref, None)
        remaining_claim_baseline.pop(holder_ref, None)
        detached += 1

    if remaining_issued:
        op["issued_equipment"] = remaining_issued
    else:
        op.pop("issued_equipment", None)
    if remaining_baseline:
        op["issued_equipment_baseline"] = remaining_baseline
    else:
        op.pop("issued_equipment_baseline", None)
    if remaining_claim_baseline:
        op["issued_equipment_claim_baseline"] = remaining_claim_baseline
    else:
        op.pop("issued_equipment_claim_baseline", None)
    return {
        "operation_after": op, "equipment_ledger_after": compact_equipment_ledger(ledger),
        "detached_holder_count": detached,
    }



def reclaim_operation_equipment(
    *, operation: Mapping[str, Any], inventory: Mapping[str, Any], equipment_ledger: Mapping[str, Any],
) -> dict[str, Any]:
    """Return all still-held operation-issued items to the source armory.

    Ammunition or thrown weapons already consumed by exact combat remain spent.
    Missing issued quantities are reported as lost rather than conjured back.
    """
    op = copy.deepcopy(dict(operation))
    inv = copy.deepcopy(dict(inventory))
    ledger = hydrate_equipment_ledger(equipment_ledger)
    issued = op.get("issued_equipment", {})
    baseline = op.get("issued_equipment_baseline", {}) if isinstance(op.get("issued_equipment_baseline"), Mapping) else {}
    if not isinstance(issued, Mapping) or not issued:
        op.pop("issued_equipment", None)
        op.pop("issued_equipment_baseline", None)
        op.pop("issued_equipment_claim_baseline", None)
        return {
            "operation_after": op, "inventory_after": inv,
            "equipment_ledger_after": compact_equipment_ledger(ledger),
            "recovered": {}, "lost_or_consumed": {},
        }

    stock = inv.setdefault("equipment", {})
    if not isinstance(stock, dict):
        raise ValueError("jianghu faction equipment stock invalid")
    loadouts = ledger.setdefault("person_loadouts", {})
    if not isinstance(loadouts, dict):
        raise ValueError("jianghu person loadouts invalid")
    recovered: dict[str, int] = {}
    lost: dict[str, int] = {}

    for person_ref, raw_items in issued.items():
        if not isinstance(person_ref, str) or not isinstance(raw_items, Mapping):
            continue
        row = loadouts.get(person_ref)
        items = row.get("items", {}) if isinstance(row, Mapping) and isinstance(row.get("items"), Mapping) else {}
        cond = row.get("condition_milli", {}) if isinstance(row, Mapping) and isinstance(row.get("condition_milli"), Mapping) else {}
        if not isinstance(items, dict):
            items = dict(items)
        if not isinstance(cond, dict):
            cond = dict(cond)
        for item_ref, raw_qty in raw_items.items():
            qty = max(0, int(raw_qty))
            if qty <= 0:
                continue
            held = max(0, int(items.get(str(item_ref), 0)))
            holder_baseline = baseline.get(person_ref, {}) if isinstance(baseline, Mapping) else {}
            baseline_qty = max(0, int(holder_baseline.get(str(item_ref), 0))) if isinstance(holder_baseline, Mapping) else 0
            amount = min(qty, max(0, held - baseline_qty))
            if amount > 0:
                items[str(item_ref)] = held - amount
                if items[str(item_ref)] <= 0:
                    items.pop(str(item_ref), None)
                    cond.pop(str(item_ref), None)
                stock[str(item_ref)] = max(0, int(stock.get(str(item_ref), 0))) + amount
                recovered[str(item_ref)] = recovered.get(str(item_ref), 0) + amount
            missing = qty - amount
            if missing > 0:
                lost[str(item_ref)] = lost.get(str(item_ref), 0) + missing
        if isinstance(row, dict):
            row["items"] = items
            row["condition_milli"] = cond
    op.pop("issued_equipment", None)
    op.pop("issued_equipment_baseline", None)
    op.pop("issued_equipment_claim_baseline", None)
    return {
        "operation_after": op,
        "inventory_after": inv,
        "equipment_ledger_after": compact_equipment_ledger(ledger),
        "recovered": recovered,
        "lost_or_consumed": lost,
    }


__all__ = ["detach_operation_issue_holders", "issue_operation_equipment", "reclaim_operation_equipment"]
