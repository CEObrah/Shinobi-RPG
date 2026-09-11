"""Monthly durable-equipment repair frontier using real workshop capacity."""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .duties import derive_duty_assignments
from .equipment_lifecycle import repair_material_requirements, repair_quote as equipment_repair_quote
from .equipment_state import compact_equipment_ledger, hydrate_equipment_ledger
from .infrastructure import workshop_capacity
from .manpower import is_faction_member

_EQUIPMENT_LEDGER_PATH = "state/martial-world/equipment-ledger.json"


def settle_equipment_maintenance_frontier(
    *, events: Sequence[Mapping[str, Any]], at: datetime, player_ref: str,
    equipment_ledger: Mapping[str, Any], writes: dict[str, Any], reviews: list[dict[str, Any]],
    inventory_cache: dict[str, tuple[str, dict[str, Any]]],
    load_faction: Callable[[str], tuple[str, dict[str, Any]]],
    load_inventory: Callable[[str], tuple[str, dict[str, Any]]],
    load_roster: Callable[[str], tuple[str, dict[str, Any]]],
    unavailable_person_refs: Callable[[], set[str]],
    usable_martial_people: Callable[..., list[dict[str, Any]]],
) -> dict[str, Any]:
    ledger = copy.deepcopy(dict(equipment_ledger))
    for event in events:
        if event.get("kind") != "equipment_maintenance_review":
            continue
        fid = str(event.get("owner_ref") or "")
        if not fid:
            continue
        _fpath, faction = load_faction(fid); _rpath, roster = load_roster(fid); ipath, inventory = load_inventory(fid)
        buildings = faction.get("buildings", {}) if isinstance(faction.get("buildings"), Mapping) else {}
        workshop_level = max(0, int(buildings.get("armory_workshop", 0)))
        physical = workshop_capacity(buildings, faction.get("infrastructure", {})) if workshop_level > 0 else {}
        repair_bays = max(0, int(physical.get("repair_bays", 0)))
        unavailable_now = unavailable_person_refs()
        roster_people = [p for p in roster.get("people", []) if isinstance(p, Mapping)] if isinstance(roster.get("people", []), list) else []
        repair_work = derive_duty_assignments(
            faction, roster_people, year=at.year, month=at.month, unavailable_refs=sorted(unavailable_now),
            protected_refs=([player_ref] if player_ref else ["pc_wei_tang"]),
        )
        repair_assignments = repair_work.get("assignments", {})
        workers = [
            p for p in usable_martial_people(roster, exclude_committed=unavailable_now)
            if repair_assignments.get(str(p.get("person_id") or "")) == "workshop_service"
        ]
        workers.sort(key=lambda p: (-int((p.get("professional_skills") or {}).get("crafting", 0)), str(p.get("person_id", ""))))
        active_workers = workers[:repair_bays]
        if not active_workers:
            reviews.append({"kind": "equipment_maintenance_review", "event_id": event.get("event_id"), "faction_ref": fid, "result": "no_repair_capacity"})
            continue
        crafting_skill = max(int((p.get("professional_skills") or {}).get("crafting", 0)) for p in active_workers)
        logical = hydrate_equipment_ledger(ledger)
        loadouts = logical.get("person_loadouts", {}) if isinstance(logical, Mapping) else {}
        faction_refs = {
            str(p.get("person_id")) for p in roster.get("people", [])
            if isinstance(p, Mapping) and isinstance(p.get("person_id"), str) and is_faction_member(p)
        } if isinstance(roster.get("people", []), list) else set()
        candidates: list[tuple[int, str, str]] = []
        if isinstance(loadouts, Mapping):
            for person_ref in sorted(faction_refs):
                row = loadouts.get(person_ref)
                if not isinstance(row, Mapping): continue
                items = row.get("items", {}) if isinstance(row.get("items"), Mapping) else {}
                cond = row.get("condition_milli", {}) if isinstance(row.get("condition_milli"), Mapping) else {}
                for item_ref, qty in items.items():
                    if int(qty) <= 0: continue
                    current = max(0, min(1000, int(cond.get(item_ref, 1000))))
                    if current < 1000: candidates.append((current, person_ref, str(item_ref)))
        candidates.sort()
        raw_materials = inventory.get("raw_materials", {}) if isinstance(inventory.get("raw_materials"), Mapping) else {}
        raw_materials = {str(k): max(0, int(v)) for k, v in raw_materials.items()}
        labor_hours_left = len(active_workers) * 105
        repaired: list[dict[str, Any]] = []
        for current, person_ref, item_ref in candidates:
            if labor_hours_left <= 0 or len(repaired) >= max(1, repair_bays * 8): break
            try:
                quote = equipment_repair_quote(integrity_milli=current, target_integrity_milli=1000, crafting_skill=crafting_skill)
                req = repair_material_requirements(item_ref=item_ref, integrity_restored_milli=int(quote["integrity_restored_milli"]), quantity=1)
            except (KeyError, ValueError):
                continue
            hours = max(0, int(quote.get("crafting_hours", 0)))
            if hours > labor_hours_left or any(raw_materials.get(ref, 0) < int(qty) for ref, qty in req.items()): continue
            for ref, qty in req.items(): raw_materials[ref] = raw_materials.get(ref, 0) - int(qty)
            prow = loadouts.get(person_ref)
            if not isinstance(prow, dict): prow = copy.deepcopy(dict(prow or {})); loadouts[person_ref] = prow
            conditions = prow.setdefault("condition_milli", {})
            if not isinstance(conditions, dict): conditions = {}; prow["condition_milli"] = conditions
            conditions[item_ref] = 1000; labor_hours_left -= hours
            repaired.append({"person_ref": person_ref, "item_ref": item_ref, "integrity_before_milli": current, "labor_hours": hours, "materials": req})
        if repaired:
            inventory["raw_materials"] = raw_materials; ledger = compact_equipment_ledger(logical)
            writes[ipath] = inventory; inventory_cache[fid] = (ipath, inventory); writes[_EQUIPMENT_LEDGER_PATH] = ledger
            reviews.append({"kind": "equipment_maintenance_review", "event_id": event.get("event_id"), "faction_ref": fid, "result": "repaired", "repair_count": len(repaired), "repairs": repaired[:16]})
        else:
            reviews.append({"kind": "equipment_maintenance_review", "event_id": event.get("event_id"), "faction_ref": fid, "result": "no_repairable_damage"})
    return ledger


__all__ = ["settle_equipment_maintenance_frontier"]
