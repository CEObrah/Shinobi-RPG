"""Conserved standing-retinue support equipment and combat support policy."""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from .equipment_state import compact_equipment_ledger, effective_person_loadout, hydrate_equipment_ledger
from .property import provenance_claim, set_nonholder_claim

_ROOT = Path(__file__).resolve().parents[3]
_LOADOUTS = _ROOT / "game" / "data" / "martial-world" / "equipment-loadouts.json"
_MEDICINE = _ROOT / "game" / "data" / "martial-world" / "medicine.json"


@lru_cache(maxsize=1)
def _role_issue_table() -> dict[str, dict[str, Any]]:
    doc = json.loads(_LOADOUTS.read_text(encoding="utf-8"))
    rows = doc.get("retinue_role_issues", {}) if isinstance(doc, Mapping) else {}
    if not isinstance(rows, Mapping):
        raise ValueError("retinue role issue table invalid")
    return {str(ref): copy.deepcopy(dict(row)) for ref, row in rows.items() if isinstance(row, Mapping)}


@lru_cache(maxsize=1)
def emergency_field_stabilization_policy() -> dict[str, Any]:
    doc = json.loads(_MEDICINE.read_text(encoding="utf-8"))
    row = doc.get("emergency_field_stabilization", {}) if isinstance(doc, Mapping) else {}
    if not isinstance(row, Mapping):
        raise ValueError("emergency field stabilization policy invalid")
    required = {
        "access_radius_mm", "minimum_continuous_access_ms", "treatment_minutes_for_score",
        "environment_milli", "patient_condition_floor_milli", "shock_penalty_milli_per_point",
        "physician_kit_item_ref", "medical_supply_item_ref", "medical_supply_consumed_per_attempt",
    }
    if not required.issubset(row):
        raise ValueError("emergency field stabilization policy incomplete")
    return copy.deepcopy(dict(row))


def provision_retinue_role_issue(
    *, role: str, faction_ref: str, person_ref: str,
    inventory: Mapping[str, Any], equipment_ledger: Mapping[str, Any],
) -> dict[str, Any]:
    """Issue missing registered role equipment from exact faction stock.

    This is custody, not creation.  Existing held quantities satisfy the issue,
    faction stock is debited only for the missing quantity actually issued, and
    ad-hoc role equipment remains legally owned by the issuing faction.
    Shortages are returned explicitly rather than filled synthetically.
    """
    issue = _role_issue_table().get(str(role))
    inv = copy.deepcopy(dict(inventory))
    ledger = hydrate_equipment_ledger(equipment_ledger)
    if not isinstance(issue, Mapping):
        return {
            "inventory_after": inv,
            "equipment_ledger_after": compact_equipment_ledger(ledger),
            "issued": {}, "shortfall": {}, "fully_provisioned": True,
        }
    equipment = inv.setdefault("equipment", {})
    if not isinstance(equipment, dict):
        raise ValueError("retinue role issue inventory equipment invalid")
    loadouts = ledger.setdefault("person_loadouts", {})
    if not isinstance(loadouts, dict):
        raise ValueError("retinue role issue loadouts invalid")
    load = loadouts.setdefault(str(person_ref), {"items": {}, "condition_milli": {}})
    if not isinstance(load, dict):
        raise ValueError("retinue role issue person loadout invalid")
    items = load.setdefault("items", {})
    condition = load.setdefault("condition_milli", {})
    if not isinstance(items, dict) or not isinstance(condition, dict):
        raise ValueError("retinue role issue person items invalid")
    issued: dict[str, int] = {}
    shortfall: dict[str, int] = {}
    wanted = issue.get("items", {}) if isinstance(issue.get("items"), Mapping) else {}
    for raw_item_ref, raw_target_qty in wanted.items():
        item_ref = str(raw_item_ref)
        target_qty = max(0, int(raw_target_qty))
        held = max(0, int(items.get(item_ref, 0)))
        missing = max(0, target_qty - held)
        if missing <= 0:
            continue
        stock = max(0, int(equipment.get(item_ref, 0)))
        moved = min(stock, missing)
        if moved > 0:
            equipment[item_ref] = stock - moved
            items[item_ref] = held + moved
            condition.setdefault(item_ref, 1000)
            issued[item_ref] = moved
            existing = provenance_claim(ledger, str(person_ref), item_ref)
            if isinstance(existing, Mapping) and str(existing.get("owner_ref") or "") not in {"", str(faction_ref)}:
                raise ValueError("retinue role issue mixed legal owners")
            prior_claim = max(0, int(existing.get("quantity", 0))) if isinstance(existing, Mapping) else 0
            ledger = set_nonholder_claim(
                ledger, holder_ref=str(person_ref), item_ref=item_ref,
                owner_ref=str(faction_ref), quantity=prior_claim + moved,
                status="retinue_role_issue",
            )
            # set_nonholder_claim returns a deep copy, so rebind the hydrated
            # loadout handles for any following item in this same issue.
            loadouts = ledger.setdefault("person_loadouts", {})
            load = loadouts.setdefault(str(person_ref), {"items": {}, "condition_milli": {}})
            items = load.setdefault("items", {})
            condition = load.setdefault("condition_milli", {})
        remaining = missing - moved
        if remaining > 0:
            shortfall[item_ref] = remaining
    return {
        "inventory_after": inv,
        "equipment_ledger_after": compact_equipment_ledger(ledger),
        "issued": dict(sorted(issued.items())),
        "shortfall": dict(sorted(shortfall.items())),
        "fully_provisioned": not shortfall,
    }


__all__ = ["emergency_field_stabilization_policy", "provision_retinue_role_issue"]
