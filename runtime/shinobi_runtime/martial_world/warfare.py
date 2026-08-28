"""Strategic faction mobilization and local-frontage battle settlement.

A deployment owns the full exact force. Local exact combat resolves only the
people physically contacting at one frontage; reserves remain real people in the
same deployment rather than disappearing behind an arbitrary fighter cap.
"""
from __future__ import annotations

import copy
import math
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from .combat_simulation import simulate_exact_combat
from .allied_support import stage_defensive_calls_to_arms
from .commitments import derived_commitment_state, extend_commitment_resources, release_resources, remove_people_from_commitments
from .crime_custody import create_custody_record, custody_transition
from .equipment_state import compact_equipment_ledger, hydrate_equipment_ledger
from .environment import combat_environment, site_combat_terrain
from .faction_relations import apply_relation_event, treaty_forbids_hostilities
from .faction_politics import conflict_stage
from .faction_registry import current_faction_refs
from .faction_existence import settle_extinctions_from_touched_rosters
from .faction_state import compact_faction_state, faction_path, hydrate_faction_state, inventory_path, roster_path, with_derived_population
from .handoffs import classify_handoff
from .escort_living_world import escort_can_resume_field_travel, principal_ransom_value_cash
from .frontier_support import credit_cargo_to_inventory
from .inventory_state import compact_inventory_state, hydrate_inventory_state
from .institutional_lifecycle import settle_institutional_offices
from .institutional_operations import close_institutional_operation, stage_institutional_phase
from .manpower import combat_readiness_score, combat_ready_members
from .live_state import person_route
from .operational_equipment import issue_operation_equipment, reclaim_operation_equipment
from .physical_presence import physical_unavailable_person_refs
from .physical_travel import build_route_journey, stage_route_journey
from .travel_provisions import planned_journey_seconds, provisioning_journey_seconds, reserve_faction_rations
from .person_state import compact_roster_state, hydrate_roster_state, reconcile_faction_population
from .family_simulation import apply_recognized_succession
from .death_lifecycle import (
    clean_social_and_custody_for_deaths, close_family_authorities, exact_person_index, is_living,
    prune_dead_from_durable_activities, release_custody_held_by_extinct_factions, settle_exact_death_estates,
)
from .scheduler import upsert_one_off_event
from .repatriation import build_repatriation_operation
from .site_control import active_site_controller
from .strategic_autonomy import stable_permille
from .social_causality import (
    add_personal_obligation, close_family_refs, obligation_ref as personal_obligation_ref,
    obligations_for_actor, resolve_personal_obligation,
)
from .relationships import apply_relationship_event
from .travel import travel_plan
from .training import institutional_training_pause_refs, settle_and_reset_faction_training_cycle
from .weather import weather_snapshot

_DEPLOYMENTS = "state/martial-world/deployments.json"
_EQUIPMENT = "state/martial-world/equipment-ledger.json"
_RELATIONS = "state/martial-world/faction-relations.json"
_SOCIAL = "state/martial-world/social.json"
_CUSTODY = "state/martial-world/custody.json"
_FAMILY = "state/martial-world/family.json"
_GEOGRAPHY = "game/data/martial-world/geography.json"
_ECONOMY = "game/data/martial-world/economy.json"
_EQUIPMENT_DATA = "game/data/martial-world/equipment.json"
_LOCAL_SITES = "game/data/martial-world/local-sites.json"
_META = "state/meta.json"
_SCHEDULER = "state/martial-world/scheduler.json"
_ROUTE_OPERATIONS = "state/martial-world/route-operations.json"


class _View:
    def __init__(self, read_json: Callable[[str], Any], writes: Mapping[str, Any]) -> None:
        self._read_json = read_json
        self._writes = writes

    def __call__(self, path: str) -> Any:
        return self.read_json(path)

    def read_json(self, path: str) -> Any:
        if path in self._writes:
            return copy.deepcopy(self._writes[path])
        return copy.deepcopy(self._read_json(path))


def _load_faction(view: Callable[[str], Any], faction_ref: str) -> dict[str, Any]:
    faction = hydrate_faction_state(view(faction_path(faction_ref)))
    return with_derived_population(faction, view(roster_path(faction_ref)))


def _load_roster(view: Callable[[str], Any], faction_ref: str, faction: Mapping[str, Any]) -> dict[str, Any]:
    return hydrate_roster_state(view(roster_path(faction_ref)), faction=faction)


def _load_inventory(view: Callable[[str], Any], faction_ref: str) -> dict[str, Any]:
    return hydrate_inventory_state(view(inventory_path(faction_ref)))


def _person_owner_faction(view: _View, person_ref: str, preferred: Sequence[str] = ()) -> str:
    """Resolve a conserved roster owner through the transient RAM index.

    Preferred owners are checked first because a transaction may have just moved
    a person between rosters. The normal path then uses ``live_state.person_route``
    and therefore never persists a person-routing table in campaign state.
    """
    for faction_ref in [str(x) for x in preferred if isinstance(x, str) and x]:
        try:
            roster = view(roster_path(faction_ref))
        except FileNotFoundError:
            continue
        people = roster.get("people", []) if isinstance(roster, Mapping) else []
        if isinstance(people, list) and any(
            isinstance(person, Mapping) and str(person.get("person_id") or "") == person_ref
            for person in people
        ):
            return faction_ref
    try:
        faction_ref, _ordinal = person_route(view, person_ref)
        return faction_ref
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        # Disposable/long-horizon simulation commonly supplies a plain staged
        # read closure rather than a repository object, so the fast transient
        # person_route index may be unavailable. Fall back to one bounded exact
        # current-state scan instead of silently assigning the person to the
        # preferred/rescuing faction.
        try:
            index = exact_person_index(
                read_json=view, writes={}, faction_refs=current_faction_refs(view),
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            return ""
        route = index.get(person_ref)
        if isinstance(route, Mapping) and str(route.get("owner_kind") or "") == "faction":
            return str(route.get("owner_ref") or "")
        return ""


def _site_rows(view: Callable[[str], Any]) -> Mapping[str, Any]:
    try:
        data = view(_LOCAL_SITES)
    except FileNotFoundError:
        return {}
    rows = data.get("sites", {}) if isinstance(data, Mapping) else {}
    return rows if isinstance(rows, Mapping) else {}


def _person_place(person: Mapping[str, Any], faction: Mapping[str, Any], sites: Mapping[str, Any]) -> str:
    home = str(faction.get("headquarters") or "")
    loc = str(person.get("location_ref") or "")
    if not loc or loc == str(faction.get("local_site_ref") or ""):
        return home
    site = sites.get(loc)
    if isinstance(site, Mapping) and site.get("parent_place_ref"):
        return str(site.get("parent_place_ref"))
    return loc


def _arrival_site(sites: Mapping[str, Any], place_ref: str, fallback: str = "") -> str:
    rows = sorted(
        str(ref) for ref, row in sites.items()
        if isinstance(ref, str) and isinstance(row, Mapping)
        and str(row.get("parent_place_ref") or "") == place_ref
    )
    if rows:
        public = [
            ref for ref in rows
            if str((sites.get(ref) or {}).get("public_access", "public"))
            not in {"restricted_by_faction_policy", "private"}
        ]
        return (public or rows)[0]
    return fallback or place_ref


_NONLETHAL_STRATEGIC_INTENTS = frozenset({
    "robbery", "kidnapping", "cargo_seizure", "extortion", "cargo_diversion",
})


def strategic_operation_targeting_intent(operation: Mapping[str, Any]) -> str:
    """Resolve combat intent from the saved strategic objective.

    The autonomy layer already decides whether a raid is acquisitive/coercive
    or a genuinely lethal punitive/war action. Arrival must consume that saved
    decision rather than recomputing ``faction_raid == lethal``.
    """
    saved = str(operation.get("targeting_intent") or "")
    if saved in {"disable", "lethal"}:
        return saved
    kind = str(operation.get("operation_kind") or "")
    intent = str(operation.get("operation_intent") or "")
    if kind in {"formal_challenge", "custody_rescue", "faction_reconnaissance", "allied_defense_reinforcement"} or intent in _NONLETHAL_STRATEGIC_INTENTS:
        return "disable"
    return "lethal"


def _select_strategic_raid_cargo(
    view: Callable[[str], Any], inventory: Mapping[str, Any], *, carrier_count: int,
) -> dict[str, Any] | None:
    """Choose one bounded physical cargo stack from real current target stock."""
    carriers = max(0, int(carrier_count))
    if carriers <= 0:
        return None
    try:
        economy = view(_ECONOMY)
    except FileNotFoundError:
        economy = {}
    try:
        equipment_data = view(_EQUIPMENT_DATA)
    except FileNotFoundError:
        equipment_data = {}
    materials = economy.get("materials", {}) if isinstance(economy, Mapping) else {}
    consumables = economy.get("consumables", {}) if isinstance(economy, Mapping) else {}
    equipment_values = economy.get("equipment_base_values_cash", {}) if isinstance(economy, Mapping) else {}
    weapon_catalog = equipment_data.get("weapon_catalog", {}) if isinstance(equipment_data, Mapping) else {}
    capacity_milli_kg = carriers * 20_000
    candidates: list[tuple[int, int, str, str, int]] = []

    food_qty = max(0, int(inventory.get("food_ration_days", 0)))
    food_row = consumables.get("food_ration_day", {}) if isinstance(consumables, Mapping) else {}
    if food_qty > 0:
        unit_value = max(1, int(food_row.get("base_value_cash", 25))) if isinstance(food_row, Mapping) else 25
        take = min(food_qty, capacity_milli_kg // 1000)
        if take > 0:
            candidates.append((take * unit_value, unit_value, "food", "food_ration_day", take))

    raw = inventory.get("raw_materials", {}) if isinstance(inventory.get("raw_materials"), Mapping) else {}
    for item_ref, raw_qty in raw.items():
        row = materials.get(str(item_ref)) if isinstance(materials, Mapping) else None
        if not isinstance(row, Mapping):
            continue
        qty = max(0, int(raw_qty)); unit_value = max(1, int(row.get("base_value_cash", 1)))
        mass_milli = max(1, int(float(row.get("mass_kg", 1)) * 1000))
        take = min(qty, capacity_milli_kg // mass_milli)
        if take > 0:
            candidates.append((take * unit_value, unit_value, "raw_materials", str(item_ref), take))

    equipment = inventory.get("equipment", {}) if isinstance(inventory.get("equipment"), Mapping) else {}
    for item_ref, raw_qty in equipment.items():
        if not isinstance(equipment_values, Mapping) or str(item_ref) not in equipment_values:
            continue
        qty = max(0, int(raw_qty)); unit_value = max(1, int(equipment_values.get(str(item_ref), 1)))
        row = weapon_catalog.get(str(item_ref)) if isinstance(weapon_catalog, Mapping) else None
        mass_milli = max(250, int(float(row.get("mass_kg", 1.0)) * 1000)) if isinstance(row, Mapping) else 1000
        take = min(qty, capacity_milli_kg // mass_milli, carriers * 3)
        if take > 0:
            candidates.append((take * unit_value, unit_value, "equipment", str(item_ref), take))
    if not candidates:
        return None
    total_value, _unit_value, bucket, item_ref, qty = max(candidates, key=lambda row: (row[0], row[1], row[3]))
    return {"bucket": bucket, "item_ref": item_ref, "quantity": qty, "estimated_value_cash": total_value}


def _debit_strategic_raid_cargo(inventory: dict[str, Any], cargo: Mapping[str, Any]) -> None:
    bucket = str(cargo.get("bucket") or ""); item_ref = str(cargo.get("item_ref") or "")
    qty = max(0, int(cargo.get("quantity", 0)))
    if qty <= 0 or not item_ref:
        return
    if bucket == "food":
        inventory["food_ration_days"] = max(0, int(inventory.get("food_ration_days", 0)) - qty)
        return
    rows = inventory.setdefault(bucket, {})
    if not isinstance(rows, dict):
        raise ValueError("jianghu raid cargo bucket invalid")
    left = max(0, int(rows.get(item_ref, 0)) - qty)
    if left:
        rows[item_ref] = left
    else:
        rows.pop(item_ref, None)


def _apply_strategic_raid_objective(
    *, view: Callable[[str], Any], writes: dict[str, Any], operation_ref: str, operation: dict[str, Any],
    target_faction: dict[str, Any], target_fid: str, attacker_refs: Sequence[str], defender_refs: Sequence[str],
    people_after: Mapping[str, Mapping[str, Any]], commitments: Mapping[str, Any], target_site: str, at: datetime,
) -> tuple[dict[str, Any], dict[str, Any], Mapping[str, Any]]:
    """Execute the saved raid objective while keeping seized value physically in transit."""
    intent = str(operation.get("operation_intent") or "")
    active_carriers = [
        ref for ref in attacker_refs
        if isinstance(people_after.get(ref), Mapping) and _combat_active(people_after[ref], year=at.year)
    ]
    if not active_carriers:
        return operation, {"result": "no_combat_active_carriers", "operation_intent": intent}, commitments
    operation["return_escort_refs"] = list(active_carriers)

    if intent in {"robbery", "extortion"}:
        treasury = max(0, int(target_faction.get("treasury_cash", 0)))
        divisor = 8 if intent == "robbery" else 12
        carry_cap = len(active_carriers) * (2500 if intent == "robbery" else 1800)
        seized = min(treasury, carry_cap, max(1, treasury // divisor) if treasury else 0)
        if seized <= 0:
            return operation, {"result": "no_cash_to_seize", "operation_intent": intent}, commitments
        target_faction["treasury_cash"] = treasury - seized
        operation["seized_cash"] = max(0, int(operation.get("seized_cash", 0))) + seized
        return operation, {"result": "cash_seized_in_transit", "operation_intent": intent, "cash": seized}, commitments

    if intent in {"cargo_seizure", "cargo_diversion"}:
        try:
            target_inventory = _load_inventory(view, target_fid)
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            return operation, {"result": "target_inventory_unavailable", "operation_intent": intent}, commitments
        cargo = _select_strategic_raid_cargo(view, target_inventory, carrier_count=len(active_carriers))
        if not isinstance(cargo, Mapping):
            return operation, {"result": "no_cargo_to_seize", "operation_intent": intent}, commitments
        _debit_strategic_raid_cargo(target_inventory, cargo)
        writes[inventory_path(target_fid)] = compact_inventory_state(target_inventory)
        operation["seized_item_ref"] = str(cargo.get("item_ref") or "")
        operation["seized_quantity"] = max(0, int(cargo.get("quantity", 0)))
        operation["seized_cargo_bucket"] = str(cargo.get("bucket") or "")
        return operation, {"result": "cargo_seized_in_transit", "operation_intent": intent, **dict(cargo)}, commitments

    if intent == "kidnapping":
        try:
            custody = copy.deepcopy(dict(view(_CUSTODY)))
        except FileNotFoundError:
            custody = {"schema": "jianghu-custody-state-1.0", "records": []}
        rows = custody.setdefault("records", [])
        if not isinstance(rows, list):
            raise ValueError("jianghu custody records invalid")
        active_records = {
            str(row.get("person_ref") or "") for row in rows
            if isinstance(row, Mapping) and row.get("status") not in {"released", "escaped", "rescued", "executed"}
        }
        candidates: list[tuple[int, str]] = []
        for ref in defender_refs:
            person = people_after.get(ref)
            if not isinstance(person, Mapping) or not _living(person) or _combat_active(person, year=at.year) or ref in active_records:
                continue
            candidates.append((principal_ransom_value_cash(person), ref))
        if not candidates:
            return operation, {"result": "no_physically_subdued_kidnap_target", "operation_intent": intent}, commitments
        _value, captive_ref = max(candidates, key=lambda row: (row[0], row[1]))
        rows.append(create_custody_record(
            person_ref=captive_ref, captor_ref=active_carriers[0], at=at.isoformat(), location_ref=target_site,
            basis=f"strategic_kidnapping:{operation_ref}", holder_faction_ref=str(operation.get("faction_ref") or ""),
        ))
        try:
            commitments = extend_commitment_resources(
                commitments, activity_ref=operation_ref, resources=[("person", captive_ref, target_fid)],
            )
        except ValueError:
            rows.pop()
            return operation, {"result": "kidnap_target_unavailable", "operation_intent": intent}, commitments
        writes[_CUSTODY] = custody
        participants = [str(x) for x in operation.get("participant_refs", []) if isinstance(x, str) and x]
        if captive_ref not in participants:
            participants.append(captive_ref)
        operation["participant_refs"] = participants
        operation["captive_refs"] = sorted(set([str(x) for x in operation.get("captive_refs", []) if isinstance(x, str) and x] + [captive_ref]))
        return operation, {"result": "captive_seized_in_transit", "operation_intent": intent, "captive_ref": captive_ref}, commitments

    return operation, {"result": "no_seizable_objective", "operation_intent": intent}, commitments


def local_frontage_count(site: Mapping[str, Any] | None) -> int:
    """Return physically contacting bodies per side from local spatial scale.

    This bounds one exact contact patch, never the force or battle population.
    Open grounds expose more simultaneous frontage; interior compounds expose
    less. A larger deployment is resolved through additional local contacts.
    """
    row = site if isinstance(site, Mapping) else {}
    capacity = max(1, int(row.get("capacity", 25)))
    linear = max(1, math.isqrt(capacity))
    site_type = str(row.get("site_type") or "")
    if site_type in {"tournament_ground", "training_grounds", "market", "caravan_yard", "open_ground"}:
        linear *= 2
    elif site_type in {"inn", "tea_house", "clinic", "library", "government_office", "magistrate_office"}:
        linear = max(1, (linear + 1) // 2)
    return max(1, linear)


def _relation_hostility(state: Mapping[str, Any], source: str, target: str) -> int:
    rows = state.get("edges", []) if isinstance(state, Mapping) else []
    if not isinstance(rows, list):
        return 0
    values = [
        max(0, int(row.get("hostility", 0))) for row in rows
        if isinstance(row, Mapping)
        and {str(row.get("from_faction") or ""), str(row.get("to_faction") or "")} == {source, target}
    ]
    return max(values, default=0)


def _apply_relation(state: Mapping[str, Any], source: str, target: str, kind: str) -> dict[str, Any]:
    out = copy.deepcopy(dict(state)); rows = out.setdefault("edges", [])
    if not isinstance(rows, list):
        raise ValueError("jianghu faction relations invalid")
    found = None
    for i, row in enumerate(rows):
        if isinstance(row, Mapping) and row.get("from_faction") == source and row.get("to_faction") == target:
            found = i; break
    before = rows[found] if found is not None else None
    after = apply_relation_event(before, from_faction=source, to_faction=target, event_kind=kind)
    if found is None: rows.append(after)
    else: rows[found] = after
    return out


def _pause_people(
    faction: dict[str, Any], roster: dict[str, Any], refs: Sequence[str], *, at_iso: str,
    paused_refs: Sequence[str] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    selected = {str(x) for x in refs if isinstance(x, str)}
    if not selected:
        return faction, roster
    faction, roster, _ = settle_and_reset_faction_training_cycle(
        faction, roster, at_iso=at_iso, paused_refs=paused_refs,
    )
    people = roster.get("people", []) if isinstance(roster.get("people"), list) else []
    after: list[Any] = []
    for raw in people:
        if not isinstance(raw, Mapping) or str(raw.get("person_id") or "") not in selected:
            after.append(raw); continue
        person = copy.deepcopy(dict(raw))
        ts = copy.deepcopy(dict(person.get("training_state", {}))) if isinstance(person.get("training_state"), Mapping) else {}
        ts["institutional_paused"] = True
        person["training_state"] = ts
        after.append(person)
    roster["people"] = after
    return faction, roster


def _living(person: Mapping[str, Any]) -> bool:
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    return health.get("status") != "dead"


def _combat_active(person: Mapping[str, Any], *, year: int) -> bool:
    return _living(person) and combat_readiness_score(person, year=year) > 0


def expand_new_strategic_mobilizations(
    *, read_json: Callable[[str], Any], writes: dict[str, Any], at: datetime,
) -> list[dict[str, Any]]:
    """Expand newly-created raid/war seed deployments to lawful physical musters."""
    try:
        before = read_json(_DEPLOYMENTS)
    except FileNotFoundError:
        before = {"deployments": {}}
    after = writes.get(_DEPLOYMENTS)
    if not isinstance(after, Mapping):
        return []
    before_rows = before.get("deployments", {}) if isinstance(before, Mapping) else {}
    rows = copy.deepcopy(dict(after)).setdefault("deployments", {})
    if not isinstance(rows, dict) or not isinstance(before_rows, Mapping):
        return []
    view = _View(read_json, writes)
    commitments = derived_commitment_state(view)
    physically_unavailable = physical_unavailable_person_refs(view)
    try:
        relations = view(_RELATIONS)
    except FileNotFoundError:
        relations = {"edges": []}
    sites = _site_rows(view)
    reviews: list[dict[str, Any]] = []
    changed_deployments = False

    for op_ref in sorted(str(x) for x in rows if isinstance(x, str)):
        raw = rows.get(op_ref)
        # Operations that have not yet received a mobilization basis pass
        # through conserved mustering exactly once. Already-mustered current
        # operations are never remustered.
        if op_ref in before_rows and isinstance(raw, Mapping) and raw.get("mobilization_basis"):
            continue
        if not isinstance(raw, Mapping) or raw.get("status") not in {"mobilizing", "traveling_outbound"}:
            continue
        kind = str(raw.get("operation_kind") or "")
        if kind not in {"faction_raid", "faction_war_strike"}:
            continue
        fid = str(raw.get("faction_ref") or ""); target = str(raw.get("target_faction_ref") or "")
        if not fid or not target:
            continue
        faction = _load_faction(view, fid); roster = _load_roster(view, fid, faction); inventory = _load_inventory(view, fid)
        existing = [
            str(x) for x in raw.get("participant_refs", [])
            if isinstance(x, str) and str(x) not in physically_unavailable
        ]
        if not existing:
            continue
        index = commitments.get("person_index", {}) if isinstance(commitments.get("person_index"), Mapping) else {}
        paused_before_mobilization = {str(x) for x in index if isinstance(x, str)}
        blocked = (paused_before_mobilization - set(existing)) | physically_unavailable
        ready = combat_ready_members(
            [p for p in roster.get("people", []) if isinstance(p, Mapping)],
            year=at.year, unavailable_refs=blocked, minimum_age=16,
        )
        home = str(faction.get("headquarters") or "")
        ready = [p for p in ready if _person_place(p, faction, sites) == home]
        ordered = [str(p.get("person_id")) for p in ready if isinstance(p.get("person_id"), str)]
        ordered = list(dict.fromkeys(existing + ordered))
        total = len(ordered)
        if total <= len(existing):
            continue
        policy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
        risk = max(0, min(100, int(policy.get("risk_tolerance", 50))))
        hostility = _relation_hostility(relations, fid, target)
        if kind == "faction_raid":
            # Stealth/cohesion is a real constraint, so raid size grows
            # sublinearly with available manpower instead of using a hard cap.
            coordination = max(2, 2 + risk // 20)
            desired = max(len(existing), math.isqrt(max(1, total * coordination)))
        else:
            # Declared war commits a risk/hostility-driven share while retaining
            # a home/security reserve. The percentage is doctrine, not a headcount cap.
            mobilize_permille = min(900, 450 + risk * 3 + max(0, hostility - 50) * 2)
            reserve_permille = max(100, 300 - risk * 2)
            home_reserve = max(1, (total * reserve_permille + 999) // 1000)
            desired = min(max(0, total - home_reserve), (total * mobilize_permille + 999) // 1000)
            desired = max(len(existing), desired)
        desired = min(total, desired)
        per_person_food = max(1, ((int(float(raw.get("travel_hours", 24))) + 23) // 24) * 2 + 1)
        food = max(0, int(inventory.get("food_ration_days", 0)))
        home_food_reserve = max(0, int(faction.get("population", total))) * 14
        affordable_extra = max(0, food - home_food_reserve) // per_person_food
        extra_count = min(max(0, desired - len(existing)), affordable_extra)
        if extra_count <= 0:
            continue
        extras = [ref for ref in ordered if ref not in existing][:extra_count]
        if not extras:
            continue
        try:
            commitments = extend_commitment_resources(
                commitments, activity_ref=op_ref,
                resources=[("person", ref, fid) for ref in extras],
            )
        except ValueError:
            continue
        # Mobilization sizes against current provisions but does not spend them.
        # The one physical departure frontier reserves food for the whole force
        # once the actual weathered route leg begins.
        paused_before = institutional_training_pause_refs(
            faction, [p for p in roster.get("people", []) if isinstance(p, Mapping)],
            unavailable_refs=sorted(paused_before_mobilization),
        )
        faction, roster = _pause_people(
            faction, roster, extras, at_iso=at.isoformat(), paused_refs=paused_before,
        )
        current = copy.deepcopy(dict(raw)); current["participant_refs"] = existing + extras
        current["mobilized_force_count"] = len(current["participant_refs"])
        current["mobilization_basis"] = "stealth_coordination" if kind == "faction_raid" else "risk_hostility_fraction_with_home_reserve"
        rows[op_ref] = current; changed_deployments = True
        writes[faction_path(fid)] = compact_faction_state(faction)
        writes[roster_path(fid)] = compact_roster_state(roster, faction=faction)
        writes[inventory_path(fid)] = compact_inventory_state(inventory)
        reviews.append({
            "kind":"strategic_mobilization_expanded","operation_ref":op_ref,
            "operation_kind":kind,"seed_count":len(existing),"added_count":len(extras),
            "mobilized_count":len(existing)+len(extras),
        })
    if changed_deployments:
        out = copy.deepcopy(dict(after)); out["deployments"] = rows; writes[_DEPLOYMENTS] = out
    return reviews


def _settle_released_training(
    *, view: Callable[[str], Any], writes: dict[str, Any], released_refs: Sequence[str], at: datetime,
) -> None:
    """Close the old faction training epoch with released people still paused."""
    released = {str(ref) for ref in released_refs if isinstance(ref, str) and ref}
    if not released:
        return
    current = _View(view, writes)
    faction_refs = current_faction_refs(current)
    index = exact_person_index(read_json=current, writes={}, faction_refs=faction_refs)
    commitments = derived_commitment_state(current)
    busy = {str(ref) for ref in commitments.get("person_index", {}) if isinstance(ref, str)}
    by_faction: dict[str, set[str]] = {}
    for ref in sorted(released):
        route = index.get(ref)
        if not isinstance(route, Mapping) or route.get("owner_kind") != "faction":
            continue
        person = route.get("person")
        if not isinstance(person, Mapping) or not is_living(person):
            continue
        fid = str(route.get("owner_ref") or "")
        if fid:
            by_faction.setdefault(fid, set()).add(ref)
    for fid, refs in by_faction.items():
        faction = _load_faction(current, fid)
        roster = _load_roster(current, fid, faction)
        people = [p for p in roster.get("people", []) if isinstance(p, Mapping)]
        paused = institutional_training_pause_refs(
            faction, people, unavailable_refs=sorted(busy | refs),
        )
        faction, roster, _summary = settle_and_reset_faction_training_cycle(
            faction, roster, at_iso=at.isoformat(), paused_refs=paused,
        )
        writes[faction_path(fid)] = compact_faction_state(faction)
        writes[roster_path(fid)] = compact_roster_state(roster, faction=faction)


def _clear_dead_current_state(
    *, view: Callable[[str], Any], writes: dict[str, Any], dead: set[str],
    involved_factions: Sequence[str], at: datetime,
) -> None:
    """Close every current authority invalidated by strategic combat death.

    Strategic warfare is an exact death frontier. Family authority, personal
    estate, offices, custody, succession, finite activity ownership and faction
    existence all close in this transaction rather than waiting for a monthly or
    annual lifecycle pass.
    """
    dead = {str(x) for x in dead if isinstance(x, str) and x}
    if not dead:
        return

    faction_refs = current_faction_refs(view)
    family = copy.deepcopy(dict(view(_FAMILY)))
    try:
        social = copy.deepcopy(dict(view(_SOCIAL)))
    except FileNotFoundError:
        social = {"courtships": {}, "relationships": {}}
    try:
        custody = copy.deepcopy(dict(view(_CUSTODY)))
    except FileNotFoundError:
        custody = {"schema": "jianghu-custody-state-1.0", "records": []}

    person_index = exact_person_index(read_json=view, writes={}, faction_refs=faction_refs)
    living_people = {
        ref: copy.deepcopy(route["person"])
        for ref, route in person_index.items()
        if ref not in dead and isinstance(route.get("person"), Mapping) and is_living(route["person"])
    }
    family = close_family_authorities(family, dead_refs=sorted(dead), living_people=living_people)
    writes[_FAMILY] = family

    # Personal captivity may disqualify someone from receiving a new standing
    # office, while ordinary deployment/travel does not: an office is authority,
    # not a time reservation. Clean death-invalidated custody/social state before
    # selecting a non-hereditary successor so a newly released prisoner is not
    # treated as still unavailable.
    social, custody, _released = clean_social_and_custody_for_deaths(
        social, custody, dead_refs=sorted(dead),
    )
    writes[_SOCIAL] = social
    writes[_CUSTODY] = custody
    custody_unavailable = {
        str(row.get("person_ref"))
        for row in (custody.get("records", []) if isinstance(custody.get("records"), list) else [])
        if isinstance(row, Mapping)
        and str(row.get("person_ref") or "")
        and str(row.get("status") or "") not in {"released", "escaped", "rescued", "executed"}
    }
    try:
        meta = view(_META)
        player_ref = str(meta.get("player_id") or "") if isinstance(meta, Mapping) else ""
    except FileNotFoundError:
        player_ref = ""

    by_faction: dict[str, set[str]] = {}
    for ref in sorted(dead):
        route = person_index.get(ref)
        if not isinstance(route, Mapping):
            continue
        if route.get("owner_kind") == "faction":
            fid = str(route.get("owner_ref") or "")
            if fid:
                by_faction.setdefault(fid, set()).add(ref)
        path = str(route.get("path") or "")
        if not path:
            continue
        owner = copy.deepcopy(view(path))
        rows = owner.get("people", []) if isinstance(owner, Mapping) else []
        if not isinstance(rows, list):
            raise ValueError("jianghu exact person owner invalid")
        ordinal = next((i for i, row in enumerate(rows) if isinstance(row, Mapping) and row.get("person_id") == ref), -1)
        if ordinal < 0:
            raise ValueError(f"jianghu exact person route stale: {ref}")
        person = copy.deepcopy(dict(rows[ordinal]))
        person["standing_offices"] = []
        rows[ordinal] = person
        owner["people"] = rows
        writes[path] = owner

    # Hereditary succession is immediate and consumes the globally closed family
    # state, so battlefield death cannot leave a dead leader in current office.
    post_office_view = _View(view, writes)
    for fid in sorted(by_faction):
        faction = _load_faction(post_office_view, fid)
        roster = _load_roster(post_office_view, fid, faction)
        people = roster.get("people", []) if isinstance(roster.get("people"), list) else []
        succession = apply_recognized_succession(
            family, faction_ref=fid, roster_people=[p for p in people if isinstance(p, Mapping)], year=at.year,
        )
        roster["people"] = succession["people_after"]
        # Hereditary claims have first priority. If none yields a living leader,
        # fill the vacancy immediately through the same ordinary office selector
        # used by monthly institutional progression. A battle may not leave a
        # still-living institution leaderless until the next monthly wake.
        office_result = settle_institutional_offices(
            faction, roster, year=at.year, social=social, player_ref=player_ref or None,
            unavailable_refs=sorted(custody_unavailable),
        )
        roster = office_result["roster"]
        faction = reconcile_faction_population(faction, roster)
        writes[faction_path(fid)] = compact_faction_state(faction)
        writes[roster_path(fid)] = compact_roster_state(roster, faction=faction)

    # Personal estates settle before extinction. Cross-owner spouse/child
    # inheritance is global; only genuinely unclaimed faction cash falls to the
    # institution treasury and remains part of its dormant estate.
    geography = view(_GEOGRAPHY)
    places = geography.get("places", {}) if isinstance(geography, Mapping) else {}
    place_region = {
        str(pid): str(row.get("climate_profile") or "")
        for pid, row in places.items()
        if isinstance(pid, str) and isinstance(row, Mapping) and row.get("climate_profile")
    } if isinstance(places, Mapping) else {}
    settle_exact_death_estates(
        read_json=view, writes=writes, faction_refs=faction_refs, family=family,
        dead_refs=sorted(dead), place_region=place_region, site_rows=_site_rows(view),
    )

    # Exact availability is derived from durable owners. Remove the dead from
    # projects, standing retinues/deployments, routes and warrants here. Route
    # cargo/provisions stay on a terminal movement for domain-owned salvage.
    prune_dead_from_durable_activities(
        read_json=view, writes=writes, dead_refs=sorted(dead), faction_refs=faction_refs,
    )

    # Reconcile all faction rosters touched directly or by cross-owner
    # inheritance, then retire zero-living-member institutions last.
    current_view = _View(view, writes)
    for path, record in list(writes.items()):
        if not isinstance(record, Mapping) or not path.startswith("state/martial-world/people/"):
            continue
        fid = str(record.get("faction_ref") or "")
        if not fid or fid not in faction_refs:
            continue
        faction = _load_faction(current_view, fid)
        # Estate settlement writes exact purses through the universal owner
        # router.  Strategic warfare is an extracted frontier and therefore
        # cannot rely on the shared frontier bridge to compact those roster
        # after-images later.  Rehydrate once here, reconcile population from
        # logical person state, then persist the canonical sparse roster.
        roster = hydrate_roster_state(record, faction=faction)
        faction = reconcile_faction_population(faction, roster)
        writes[faction_path(fid)] = compact_faction_state(faction)
        writes[path] = compact_roster_state(roster, faction=faction)

    current_view = _View(view, writes)
    try:
        relations = copy.deepcopy(dict(current_view(_RELATIONS)))
    except FileNotFoundError:
        relations = {"edges": []}

    def load_current_faction(fid: str) -> tuple[str, dict[str, Any]]:
        fpath = faction_path(fid)
        return fpath, hydrate_faction_state(current_view(fpath))

    extinction = settle_extinctions_from_touched_rosters(
        read_json=current_view, writes=writes, relations_state=relations,
        load_faction=load_current_faction, relations_path=_RELATIONS,
    )
    if extinction.get("extinct_refs"):
        writes[_RELATIONS] = extinction["relations"]
        custody_after, extinct_releases = release_custody_held_by_extinct_factions(
            writes.get(_CUSTODY, custody), extinct_refs=extinction["extinct_refs"],
        )
        if extinct_releases:
            writes[_CUSTODY] = custody_after
            _settle_released_training(
                view=current_view, writes=writes,
                released_refs=[row["person_ref"] for row in extinct_releases], at=at,
            )
    if _released:
        _settle_released_training(view=current_view, writes=writes, released_refs=sorted(_released), at=at)



def settle_faction_operation_departures(
    *, read_json: Callable[[str], Any], writes: dict[str, Any],
    events: Sequence[Mapping[str, Any]], at: datetime, schedule_after: Mapping[str, Any],
) -> dict[str, Any]:
    """Put a purpose-owned deployment onto the one physical route system.

    Deployments reserve people and explain why they are moving. Once departure
    occurs, ``route-operations`` alone owns their current physical road position.
    This reducer is shared by raids, rescues, formal missions, war strikes and
    tournament parties.
    """
    due = [row for row in events if isinstance(row, Mapping) and row.get("kind") == "faction_operation_departure"]
    if not due:
        return {"writes": {}, "reviews": [], "handoffs": [], "schedule_after": copy.deepcopy(dict(schedule_after))}
    view = _View(read_json, writes)
    deployments = copy.deepcopy(dict(view(_DEPLOYMENTS)))
    rows = deployments.setdefault("deployments", {})
    if not isinstance(rows, dict):
        raise ValueError("jianghu deployments invalid")
    try:
        route_state = copy.deepcopy(dict(view(_ROUTE_OPERATIONS)))
    except FileNotFoundError:
        route_state = {"schema": "jianghu-route-operations-state-1.0", "movements": {}, "contacts": {}}
    try:
        meta = view(_META)
    except FileNotFoundError:
        meta = {}
    world_seed = str(meta.get("world_seed", "jianghu-world")) if isinstance(meta, Mapping) else "jianghu-world"
    schedule = copy.deepcopy(dict(schedule_after))
    try:
        equipment_ledger = copy.deepcopy(dict(view(_EQUIPMENT)))
    except FileNotFoundError:
        equipment_ledger = {}
    reviews: list[dict[str, Any]] = []
    changed = False

    for event in sorted(due, key=lambda row: (str(row.get("owner_ref") or ""), str(row.get("event_id") or ""))):
        op_ref = str(event.get("owner_ref") or "")
        raw = rows.get(op_ref)
        if not isinstance(raw, Mapping):
            reviews.append({"kind": "faction_operation_departure", "event_id": event.get("event_id"), "result": "operation_missing"})
            continue
        op = copy.deepcopy(dict(raw))
        direction = str(event.get("direction") or op.get("pending_travel_direction") or "outbound")
        expected = {"mobilizing", "traveling_outbound"} if direction == "outbound" else {"return_preparing", "traveling_return", "holding_defense"}
        if str(op.get("status") or "") not in expected:
            reviews.append({"kind": "faction_operation_departure", "event_id": event.get("event_id"), "operation_ref": op_ref, "result": "operation_not_ready"})
            continue
        source = str(op.get("source_place_ref") or "")
        target = str(op.get("target_place_ref") or "")
        start, end = (source, target) if direction == "outbound" else (target, source)
        participants = [str(x) for x in op.get("participant_refs", []) if isinstance(x, str) and x]
        if not start or not end or not participants:
            reviews.append({"kind": "faction_operation_departure", "event_id": event.get("event_id"), "operation_ref": op_ref, "result": "operation_route_invalid"})
            continue
        faction_ref = str(op.get("faction_ref") or "")
        if str(op.get("operation_kind") or "") == "captive_repatriation":
            try:
                exact = exact_person_index(read_json=view, writes={}, faction_refs=current_faction_refs(view))
                person = exact.get(participants[0], {}).get("person")
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                person = None
            if not isinstance(person, Mapping) or not escort_can_resume_field_travel([person]):
                retry = copy.deepcopy(dict(event))
                retry["due_at"] = (at + timedelta(days=1)).isoformat()
                retry["event_id"] = f"operation_departure_recovery:{op_ref}:{(at + timedelta(days=1)).date().isoformat()}"
                schedule = upsert_one_off_event(schedule, retry)
                reviews.append({
                    "kind": "faction_operation_departure", "event_id": event.get("event_id"),
                    "operation_ref": op_ref, "direction": direction, "result": "awaiting_repatriation_recovery",
                })
                continue

        def _issue_if_needed(
            *, inventory: Mapping[str, Any], operation: Mapping[str, Any],
        ) -> tuple[dict[str, Any], dict[str, Any], int]:
            nonlocal equipment_ledger
            if direction != "outbound" or str(operation.get("operation_kind") or "") not in {
                "faction_raid", "faction_war_strike", "custody_rescue", "faction_reconnaissance", "allied_defense_reinforcement",
            }:
                return copy.deepcopy(dict(operation)), copy.deepcopy(dict(inventory)), 0
            faction = _load_faction(view, faction_ref)
            roster = _load_roster(view, faction_ref, faction)
            people = {
                str(person.get("person_id")): person
                for person in roster.get("people", [])
                if isinstance(person, Mapping) and isinstance(person.get("person_id"), str)
            }
            issued = issue_operation_equipment(
                operation=operation, faction_ref=faction_ref, participant_refs=participants,
                people_by_ref=people, inventory=inventory, equipment_ledger=equipment_ledger,
            )
            equipment_ledger = copy.deepcopy(dict(issued["equipment_ledger_after"]))
            return (
                copy.deepcopy(dict(issued["operation_after"])),
                copy.deepcopy(dict(issued["inventory_after"])),
                max(0, int(issued.get("issued_person_count", 0))),
            )
        if start == end:
            # Same-settlement movement has no road edge. Leave it to the local
            # arrival/contact lifecycle instead of manufacturing a phantom road.
            arrival_kind = str(event.get("arrival_event_kind") or op.get("arrival_event_kind") or "faction_operation_arrival")
            schedule = upsert_one_off_event(schedule, {
                "event_id": f"operation_arrival:{op_ref}", "kind": arrival_kind,
                "due_at": (at + timedelta(hours=2)).isoformat(), "owner_ref": op_ref,
                "requires_player_decision": False,
            })
            issued_count = 0
            if direction == "outbound" and faction_ref:
                ipath = inventory_path(faction_ref)
                current_inventory = _load_inventory(view, faction_ref)
                op, current_inventory, issued_count = _issue_if_needed(
                    inventory=current_inventory, operation=op,
                )
                writes[ipath] = compact_inventory_state(current_inventory)
                if issued_count:
                    writes[_EQUIPMENT] = compact_equipment_ledger(equipment_ledger)
            op["status"] = "traveling_outbound" if direction == "outbound" else "traveling_return"
            op["arrival_at"] = (at + timedelta(hours=2)).isoformat()
            if direction == "return" and isinstance(op.get("captive_refs"), list):
                try:
                    custody = copy.deepcopy(dict(view(_CUSTODY)))
                except FileNotFoundError:
                    custody = {"schema": "jianghu-custody-state-1.0", "records": []}
                captive_set = {str(x) for x in op.get("captive_refs", []) if isinstance(x, str) and x}
                custody_changed = False
                for record in custody.get("records", []):
                    if (
                        isinstance(record, dict)
                        and str(record.get("person_ref") or "") in captive_set
                        and record.get("status") not in {"released", "escaped", "rescued", "executed"}
                    ):
                        # Same-settlement returns have no route owner. The live
                        # deployment itself is the finite physical transit owner
                        # until the local arrival frontier restores a real site.
                        record["location_ref"] = op_ref
                        custody_changed = True
                if custody_changed:
                    writes[_CUSTODY] = custody
            rows[op_ref] = op; changed = True
            dossier_ref = str(op.get("institutional_operation_ref") or "")
            if direction == "outbound" and dossier_ref:
                stage_institutional_phase(read_json=read_json, writes=writes, operation_ref=dossier_ref, phase="in_field", at_iso=at.isoformat(), details={"physical_operation_ref": op_ref})
            if direction == "outbound" and str(op.get("operation_kind") or "") in {"faction_raid", "faction_war_strike"}:
                deployments["deployments"] = rows; writes[_DEPLOYMENTS] = deployments
                deployments, schedule, aid_reviews = stage_defensive_calls_to_arms(
                    read_json=read_json, writes=writes, deployments=deployments, schedule=schedule,
                    attack_ref=op_ref, attack=op, at=at, world_seed=world_seed,
                )
                rows = deployments.setdefault("deployments", rows); reviews.extend(aid_reviews)
            reviews.append({
                "kind": "faction_operation_departure", "event_id": event.get("event_id"),
                "operation_ref": op_ref, "issued_person_count": issued_count,
                "result": "local_movement_started",
            })
            continue
        try:
            plan = travel_plan(world_seed=world_seed, start_at=at, start=start, end=end, mode="foot")
        except (KeyError, ValueError):
            reviews.append({"kind": "faction_operation_departure", "event_id": event.get("event_id"), "operation_ref": op_ref, "result": "route_unavailable"})
            continue
        movement_ref = f"route_operation:{op_ref}:{direction}"
        arrival_kind = str(event.get("arrival_event_kind") or op.get("arrival_event_kind") or "faction_operation_arrival")
        destination_site = ""
        if direction == "outbound":
            destination_site = str(op.get("target_site_ref") or "")
        else:
            try:
                home = _load_faction(view, str(op.get("faction_ref") or ""))
            except (FileNotFoundError, KeyError, ValueError):
                home = {}
            destination_site = str(home.get("local_site_ref") or "") if isinstance(home, Mapping) else ""
        try:
            provision_ipath = inventory_path(faction_ref)
            provision_inventory = _load_inventory(view, faction_ref)
            provision_inventory, provision_reservation = reserve_faction_rations(
                provision_inventory, faction_ref=faction_ref, participant_count=len(participants),
                travel_seconds=provisioning_journey_seconds(plan),
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            retry = copy.deepcopy(dict(event))
            retry["due_at"] = (at + timedelta(days=1)).isoformat()
            retry["event_id"] = f"operation_departure_retry:{op_ref}:{direction}:{(at + timedelta(days=1)).date().isoformat()}"
            schedule = upsert_one_off_event(schedule, retry)
            reviews.append({
                "kind": "faction_operation_departure", "event_id": event.get("event_id"),
                "operation_ref": op_ref, "direction": direction,
                "result": "awaiting_travel_provisions",
            })
            continue
        raid_payload = direction == "return" and str(op.get("operation_kind") or "") == "faction_raid" and any((
            max(0, int(op.get("seized_cash", 0))) > 0,
            max(0, int(op.get("seized_quantity", 0))) > 0,
            bool(op.get("captive_refs")),
        ))
        movement_kind = "raid_return" if raid_payload else "faction_operation_travel"
        extra = {
            "faction_ref": str(op.get("faction_ref") or ""),
            "operation_kind": str(op.get("operation_kind") or ""),
            "operation_ref": op_ref,
            "journey_phase": direction,
            "arrival_event_kind": arrival_kind,
            "targeting_intent": strategic_operation_targeting_intent(op),
            "provision_reservation": provision_reservation,
        }
        if raid_payload:
            escort_refs = [str(x) for x in op.get("return_escort_refs", []) if isinstance(x, str) and x]
            captive_refs = [str(x) for x in op.get("captive_refs", []) if isinstance(x, str) and x]
            extra.update({
                "escort_refs": escort_refs, "raider_refs": escort_refs,
                "protected_person_refs": captive_refs, "captive_refs": captive_refs,
                "item_ref": str(op.get("seized_item_ref") or ""),
                "quantity": max(0, int(op.get("seized_quantity", 0))),
                "cash_quantity": max(0, int(op.get("seized_cash", 0))),
            })
        elif direction == "return" and str(op.get("operation_kind") or "") == "custody_rescue":
            rescued_ref = str(op.get("captive_ref") or "")
            if rescued_ref and rescued_ref in participants:
                escort_refs = [ref for ref in participants if ref != rescued_ref]
                extra.update({
                    "protected_person_refs": [rescued_ref],
                    "rescued_refs": [rescued_ref],
                })
                if escort_refs:
                    extra["escort_refs"] = escort_refs
        movement = build_route_journey(
            movement_ref=movement_ref, movement_kind=movement_kind, purpose_ref=op_ref,
            plan=plan, participants=participants, leader_ref=(extra.get("escort_refs") or participants)[0],
            beneficiary_ref=str(op.get("faction_ref") or ""), started_at=at, mode="foot",
            destination_site_ref=destination_site, extra=extra,
        )
        try:
            route_state, schedule = stage_route_journey(
                route_state=route_state, schedule=schedule, movement_ref=movement_ref, movement=movement, now=at,
            )
        except ValueError:
            reviews.append({"kind": "faction_operation_departure", "event_id": event.get("event_id"), "operation_ref": op_ref, "result": "movement_already_active"})
            continue
        op, provision_inventory, issued_count = _issue_if_needed(
            inventory=provision_inventory, operation=op,
        )
        writes[provision_ipath] = compact_inventory_state(provision_inventory)
        if issued_count:
            writes[_EQUIPMENT] = compact_equipment_ledger(equipment_ledger)
        op["participant_refs"] = participants
        op["status"] = "traveling_outbound" if direction == "outbound" else "traveling_return"
        op["physical_movement_ref"] = movement_ref
        op["route_refs"] = list(plan.get("edges", []))
        op["travel_hours"] = float(plan.get("travel_hours", 0.0))
        op["arrival_at" if direction == "outbound" else "return_arrival_at"] = str(plan.get("arrival_at") or "")
        op.pop("pending_travel_direction", None)
        if direction == "return" and isinstance(op.get("captive_refs"), list):
            try:
                custody = copy.deepcopy(dict(view(_CUSTODY)))
            except FileNotFoundError:
                custody = {"schema": "jianghu-custody-state-1.0", "records": []}
            captive_set = {str(x) for x in op.get("captive_refs", []) if isinstance(x, str) and x}
            custody_changed = False
            for record in custody.get("records", []):
                if isinstance(record, dict) and str(record.get("person_ref") or "") in captive_set and record.get("status") not in {"released", "escaped", "rescued", "executed"}:
                    record["location_ref"] = movement_ref; custody_changed = True
            if custody_changed:
                writes[_CUSTODY] = custody
        if movement_kind == "raid_return":
            # The route movement is now the sole physical owner of seized value
            # and captives. Keep purpose/participants on the deployment, but do
            # not duplicate cargo/cash/custody payload there where another
            # reducer could later mistake it for independently settleable value.
            for key in (
                "seized_cash", "seized_item_ref", "seized_quantity", "seized_cargo_bucket",
                "captive_refs", "return_escort_refs",
            ):
                op.pop(key, None)
        rows[op_ref] = op; changed = True
        dossier_ref = str(op.get("institutional_operation_ref") or "")
        if direction == "outbound" and dossier_ref:
            stage_institutional_phase(read_json=read_json, writes=writes, operation_ref=dossier_ref, phase="in_field", at_iso=at.isoformat(), details={"physical_operation_ref": op_ref})
        if direction == "outbound" and str(op.get("operation_kind") or "") in {"faction_raid", "faction_war_strike"}:
            deployments["deployments"] = rows; writes[_DEPLOYMENTS] = deployments
            deployments, schedule, aid_reviews = stage_defensive_calls_to_arms(
                read_json=read_json, writes=writes, deployments=deployments, schedule=schedule,
                attack_ref=op_ref, attack=op, at=at, world_seed=world_seed,
            )
            rows = deployments.setdefault("deployments", rows); reviews.extend(aid_reviews)
        reviews.append({
            "kind": "faction_operation_departure", "event_id": event.get("event_id"),
            "operation_ref": op_ref, "movement_ref": movement_ref, "direction": direction,
            "participant_count": len(participants), "route_count": len(plan.get("edges", [])),
            "issued_person_count": issued_count,
            "result": "physical_route_started",
        })
    if changed:
        deployments["deployments"] = rows
        writes[_DEPLOYMENTS] = deployments
        writes[_ROUTE_OPERATIONS] = route_state
        writes[_SCHEDULER] = copy.deepcopy(schedule)
    return {"writes": writes, "reviews": reviews, "handoffs": [], "schedule_after": schedule}

def settle_faction_operation_arrivals(
    *, read_json: Callable[[str], Any], writes: dict[str, Any],
    events: Sequence[Mapping[str, Any]], at: datetime, schedule_after: Mapping[str, Any],
) -> dict[str, Any]:
    due = [row for row in events if isinstance(row, Mapping) and row.get("kind") == "faction_operation_arrival"]
    if not due:
        return {"writes":{},"reviews":[],"handoffs":[],"schedule_after":copy.deepcopy(dict(schedule_after))}
    view = _View(read_json, writes)
    deployments = copy.deepcopy(dict(view(_DEPLOYMENTS))); rows = deployments.setdefault("deployments", {})
    commitments = derived_commitment_state(view)
    physically_unavailable = physical_unavailable_person_refs(view)
    equipment = hydrate_equipment_ledger(view(_EQUIPMENT))
    try: relations = copy.deepcopy(dict(view(_RELATIONS)))
    except FileNotFoundError: relations = {"edges":[]}
    try: social = copy.deepcopy(dict(view(_SOCIAL)))
    except FileNotFoundError: social = {"schema":"jianghu-social-state-1.0"}
    try: family = copy.deepcopy(dict(view(_FAMILY)))
    except FileNotFoundError: family = {"schema":"jianghu-family-state-1.0","marriages":{},"parentage":{},"households":{},"succession_claims":{}}
    sites = _site_rows(view)
    try:
        meta = view(_META); player_ref = str(meta.get("player_id") or "") if isinstance(meta, Mapping) else ""
    except FileNotFoundError:
        meta = {}
        player_ref = ""
    world_seed = str(meta.get("world_seed") or "jianghu-world") if isinstance(meta, Mapping) else "jianghu-world"
    schedule = copy.deepcopy(dict(schedule_after))
    reviews: list[dict[str, Any]] = []; handoffs: list[dict[str, Any]] = []
    frontier_used: set[str] = set()

    for event in sorted(due, key=lambda row: (str(row.get("owner_ref") or ""), str(row.get("event_id") or ""))):
        op_ref = str(event.get("owner_ref") or ""); op = rows.get(op_ref) if isinstance(rows, Mapping) else None
        if not isinstance(op, Mapping) or op.get("status") not in {"traveling_outbound", "arrived_pending"}:
            reviews.append({"kind":"faction_operation_arrival","event_id":event.get("event_id"),"result":"operation_not_active"}); continue
        op = copy.deepcopy(dict(op))
        fid = str(op.get("faction_ref") or ""); target_fid = str(op.get("target_faction_ref") or ""); kind = str(op.get("operation_kind") or "")
        if not fid or not target_fid or kind not in {"formal_challenge","faction_raid","faction_war_strike","custody_rescue","faction_reconnaissance","allied_defense_reinforcement"}:
            reviews.append({"kind":"faction_operation_arrival","event_id":event.get("event_id"),"result":"operation_invalid"}); continue
        source_faction = _load_faction(view, fid)
        source_roster = _load_roster(view, fid, source_faction)
        recorded_target_faction = _load_faction(view, target_fid)
        target_place = str(op.get("target_place_ref") or recorded_target_faction.get("headquarters") or "")
        # A strategic faction operation targets the institution's actual current
        # compound by default. Public-site fallback is only for institutions
        # without a usable local site; otherwise a raid on a sect/school/house
        # could silently resolve at an unrelated inn or apothecary in the same
        # settlement while still fighting the faction's defenders.
        faction_site = str(recorded_target_faction.get("local_site_ref") or "")
        target_site = str(op.get("target_site_ref") or "") or faction_site or _arrival_site(
            sites, target_place, target_place
        )
        if target_site not in sites and faction_site and faction_site in sites:
            target_site = faction_site
        site = sites.get(target_site) if isinstance(sites, Mapping) else None
        if isinstance(site, Mapping) and str(site.get("parent_place_ref") or ""):
            target_place = str(site.get("parent_place_ref"))

        if kind == "allied_defense_reinforcement":
            participants = [str(x) for x in op.get("participant_refs", []) if isinstance(x, str) and x]
            source_people = [copy.deepcopy(dict(p)) for p in source_roster.get("people", []) if isinstance(p, Mapping)]
            participant_set = set(participants)
            for i, person in enumerate(source_people):
                if str(person.get("person_id") or "") in participant_set:
                    moved = copy.deepcopy(dict(person)); moved["location_ref"] = target_site or target_place; source_people[i] = moved
            source_roster["people"] = source_people
            source_faction = reconcile_faction_population(source_faction, source_roster)
            writes[faction_path(fid)] = compact_faction_state(source_faction)
            writes[roster_path(fid)] = compact_roster_state(source_roster, faction=source_faction)
            hold_until = str(op.get("hold_until") or (at + timedelta(days=2)).isoformat())
            current = copy.deepcopy(dict(op)); current["status"] = "holding_defense"; current["arrived_at"] = at.isoformat(); current.pop("physical_movement_ref", None)
            rows[op_ref] = current
            schedule = upsert_one_off_event(schedule, {
                "event_id": f"operation_departure:return:{op_ref}", "kind": "faction_operation_departure",
                "due_at": hold_until, "owner_ref": op_ref, "direction": "return",
                "arrival_event_kind": "faction_operation_return", "requires_player_decision": False,
            })
            reviews.append({"kind":"allied_defense_arrival","operation_ref":op_ref,"faction_ref":fid,"support_target_faction_ref":target_fid,"participant_count":len(participants),"result":"holding_defense"})
            frontier_used.update(participants)
            continue

        if kind in {"faction_raid", "faction_war_strike"} and treaty_forbids_hostilities(relations, fid, target_fid):
            participants = [str(x) for x in op.get("participant_refs", []) if isinstance(x, str) and x]
            if participants:
                current = copy.deepcopy(dict(op)); current["status"] = "return_preparing"; current["pending_travel_direction"] = "return"; current.pop("physical_movement_ref", None); rows[op_ref] = current
                schedule = upsert_one_off_event(schedule, {"event_id":f"operation_departure:return:{op_ref}","kind":"faction_operation_departure","due_at":(at+timedelta(seconds=1)).isoformat(),"owner_ref":op_ref,"direction":"return","arrival_event_kind":"faction_operation_return","requires_player_decision":False})
            reviews.append({"kind":"faction_operation_arrival","operation_ref":op_ref,"operation_kind":kind,"faction_ref":fid,"target_faction_ref":target_fid,"result":"returning_under_treaty","battle_outcome":"hostilities_suspended"})
            continue

        # Land can change hands while an operation is physically marching. The
        # old target ID is intent/provenance, not present-tense site authority.
        # Never fight a dead former owner at a site now held by someone else. A
        # raid/war strike may continue only when the source is already at war
        # with the new controller; challenges/rescues and non-war arrivals turn
        # around instead of manufacturing a new aggression.
        current_controller = active_site_controller(view, target_site) if target_site else None
        recorded_target_fid = target_fid
        if current_controller and current_controller != recorded_target_fid:
            can_retarget = (
                current_controller != fid
                and kind in {"faction_raid", "faction_war_strike"}
                and conflict_stage({"hostility": _relation_hostility(relations, fid, current_controller)}) == "war"
            )
            if can_retarget:
                target_fid = current_controller
                op = copy.deepcopy(dict(op)); op["target_faction_ref"] = target_fid; rows[op_ref] = op
            else:
                participant_refs = [str(x) for x in op.get("participant_refs", []) if isinstance(x, str)]
                source_people = [copy.deepcopy(dict(p)) for p in source_roster.get("people", []) if isinstance(p, Mapping)]
                source_map = {str(p.get("person_id")): p for p in source_people if isinstance(p.get("person_id"), str)}
                surviving_attackers = [ref for ref in participant_refs if ref in source_map and _living(source_map[ref])]
                if surviving_attackers:
                    current = copy.deepcopy(dict(op)); current["participant_refs"] = surviving_attackers
                    current["status"] = "return_preparing"; current["pending_travel_direction"] = "return"
                    current.pop("physical_movement_ref", None); rows[op_ref] = current
                    schedule = upsert_one_off_event(schedule, {
                        "event_id": f"operation_departure:return:{op_ref}", "kind": "faction_operation_departure",
                        "due_at": (at + timedelta(seconds=1)).isoformat(), "owner_ref": op_ref, "direction": "return",
                        "arrival_event_kind": "faction_operation_return", "requires_player_decision": False,
                    })
                else:
                    commitments = release_resources(commitments, activity_ref=op_ref); rows.pop(op_ref, None)
                reviews.append({
                    "kind": "faction_operation_arrival", "event_id": event.get("event_id"), "operation_ref": op_ref,
                    "operation_kind": kind, "faction_ref": fid, "target_faction_ref": recorded_target_fid,
                    "current_controller_ref": current_controller, "attacker_count": len(surviving_attackers),
                    "defender_force_count": 0, "engaged_count": 0, "contact_count": 0, "exchanges": 0,
                    "deaths": 0, "winner_side": None,
                    "result": "returning" if surviving_attackers else "closed",
                    "battle_outcome": "target_control_changed",
                })
                frontier_used.update(surviving_attackers)
                continue

        target_faction = _load_faction(view, target_fid)
        target_roster = _load_roster(view, target_fid, target_faction)
        # Defenders earned their training up to the exact attack frontier. Close
        # that epoch before selecting them or applying combat consequences.
        target_busy = sorted({str(ref) for ref in commitments.get("person_index", {}) if isinstance(ref, str)} | physically_unavailable)
        target_paused = institutional_training_pause_refs(
            target_faction, [p for p in target_roster.get("people", []) if isinstance(p, Mapping)],
            unavailable_refs=target_busy,
        )
        target_faction, target_roster, _ = settle_and_reset_faction_training_cycle(
            target_faction, target_roster, at_iso=at.isoformat(), paused_refs=target_paused,
        )
        if kind == "faction_reconnaissance":
            participant_refs = [str(x) for x in op.get("participant_refs", []) if isinstance(x, str) and x]
            source_people = [copy.deepcopy(dict(p)) for p in source_roster.get("people", []) if isinstance(p, Mapping)]
            source_map = {str(p.get("person_id")): p for p in source_people if isinstance(p.get("person_id"), str)}
            scouts = [source_map[ref] for ref in participant_refs if ref in source_map and _living(source_map[ref])]
            target_people = [copy.deepcopy(dict(p)) for p in target_roster.get("people", []) if isinstance(p, Mapping)]
            defender_count = len([p for p in target_people if _combat_active(p, year=at.year) and str(p.get("person_id") or "") not in physically_unavailable and _person_place(p, target_faction, sites) == target_place])
            scout_values = []
            for person in scouts:
                skills = person.get("martial_skills", {}) if isinstance(person.get("martial_skills"), Mapping) else {}
                scout_values.append(max(0, int(skills.get("stealth_scouting", 0))))
            scout_score = sum(scout_values)//max(1,len(scout_values))
            walls = max(0, int((target_faction.get("buildings", {}) or {}).get("walls_gate", 0))) if isinstance(target_faction.get("buildings"), Mapping) else 0
            detected = stable_permille("institutional-recon-detection", op_ref, target_fid, at.isoformat()) < max(80, min(920, 520 + walls*35 - scout_score*5))
            if not detected:
                lo = max(0, (defender_count//5)*5)
                hi = max(lo+5, ((defender_count+4)//5)*5 + 5)
                report = {"observed_at":at.isoformat(),"subject_faction_ref":target_fid,"subject_site_ref":target_site,"confidence":"moderate" if scout_score < 70 else "high","estimated_combat_ready_range":[lo,hi],"walls_gate_level":walls,"source_kind":"physical_reconnaissance"}
                dossier_ref = str(op.get("institutional_operation_ref") or "")
                if dossier_ref:
                    stage_institutional_phase(read_json=read_json,writes=writes,operation_ref=dossier_ref,phase="returning",at_iso=at.isoformat(),details={"intelligence_report":report})
                current=copy.deepcopy(dict(op)); current["participant_refs"]=[str(p.get("person_id")) for p in scouts if isinstance(p.get("person_id"),str)]; current["status"]="return_preparing"; current["pending_travel_direction"]="return"; current["intelligence_report"]=report; current.pop("physical_movement_ref",None); rows[op_ref]=current
                schedule=upsert_one_off_event(schedule,{"event_id":f"operation_departure:return:{op_ref}","kind":"faction_operation_departure","due_at":(at+timedelta(hours=1)).isoformat(),"owner_ref":op_ref,"direction":"return","arrival_event_kind":"faction_operation_return","requires_player_decision":False})
                reviews.append({"kind":"faction_reconnaissance","operation_ref":op_ref,"faction_ref":fid,"target_faction_ref":target_fid,"scout_count":len(scouts),"result":"report_secured"})
                frontier_used.update(participant_refs)
                continue
            op["recon_detected"] = True
        try:
            geography = view(_GEOGRAPHY)
        except FileNotFoundError:
            geography = {}
        place_rows = geography.get("places", {}) if isinstance(geography, Mapping) else {}
        place = place_rows.get(target_place, {}) if isinstance(place_rows, Mapping) else {}
        terrain = site_combat_terrain(site if isinstance(site, Mapping) else None, place if isinstance(place, Mapping) else None)
        try:
            weather = weather_snapshot(world_seed=world_seed, at=at, place_id=target_place) if target_place else {}
        except (KeyError, TypeError, ValueError):
            weather = {}
        battlefield_environment = combat_environment(
            terrain=terrain, zone_ref=target_site, seed_ref=f"{op_ref}|{target_site}|{at.date().isoformat()}",
            weather=weather, frontage_m=local_frontage_count(site if isinstance(site, Mapping) else None),
        )
        participant_refs = [str(x) for x in op.get("participant_refs", []) if isinstance(x, str)]
        source_people = [copy.deepcopy(dict(p)) for p in source_roster.get("people", []) if isinstance(p, Mapping)]
        target_people = [copy.deepcopy(dict(p)) for p in target_roster.get("people", []) if isinstance(p, Mapping)]
        source_map = {str(p.get("person_id")):p for p in source_people if isinstance(p.get("person_id"),str)}
        target_map = {str(p.get("person_id")):p for p in target_people if isinstance(p.get("person_id"),str)}
        attacker_refs = [ref for ref in participant_refs if ref in source_map and _living(source_map[ref])]
        for ref in attacker_refs: source_map[ref]["location_ref"] = target_site
        index = commitments.get("person_index", {}) if isinstance(commitments.get("person_index"), Mapping) else {}
        blocked = {str(x) for x in index if isinstance(x,str)} | physically_unavailable | frontier_used | set(attacker_refs)
        if player_ref: blocked.add(player_ref)
        defenders = combat_ready_members(target_people, year=at.year, unavailable_refs=blocked, minimum_age=16)
        defenders = [p for p in defenders if _person_place(p,target_faction,sites)==target_place]
        defender_refs = [str(p.get("person_id")) for p in defenders if isinstance(p.get("person_id"),str)]
        allied_support: dict[str, dict[str, Any]] = {}
        for support_ref, support_raw in sorted(rows.items()):
            if not isinstance(support_raw, Mapping) or str(support_raw.get("operation_kind") or "") != "allied_defense_reinforcement":
                continue
            if str(support_raw.get("status") or "") != "holding_defense" or str(support_raw.get("support_target_faction_ref") or support_raw.get("target_faction_ref") or "") != target_fid:
                continue
            if str(support_raw.get("target_place_ref") or "") != target_place:
                continue
            ally_fid = str(support_raw.get("faction_ref") or "")
            if not ally_fid or ally_fid in {fid, target_fid}:
                continue
            try:
                ally_faction = _load_faction(_View(read_json, writes), ally_fid)
                ally_roster = _load_roster(_View(read_json, writes), ally_fid, ally_faction)
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                continue
            ally_people = [copy.deepcopy(dict(p)) for p in ally_roster.get("people", []) if isinstance(p, Mapping)]
            ally_map = {str(p.get("person_id")): p for p in ally_people if isinstance(p.get("person_id"), str)}
            refs = [str(x) for x in support_raw.get("participant_refs", []) if isinstance(x, str) and x]
            refs = [ref for ref in refs if ref in ally_map and ref not in physically_unavailable and _combat_active(ally_map[ref], year=at.year) and _person_place(ally_map[ref], ally_faction, sites) == target_place]
            if not refs:
                continue
            entry = allied_support.setdefault(ally_fid, {"faction": ally_faction, "roster": ally_roster, "people": ally_people, "map": ally_map, "refs": [], "operation_refs": []})
            entry["refs"].extend(ref for ref in refs if ref not in entry["refs"]); entry["operation_refs"].append(support_ref)
            defender_refs.extend(ref for ref in refs if ref not in defender_refs)
        master: dict[str, dict[str, Any]] = {ref:copy.deepcopy(person) for ref,person in source_map.items() if ref in attacker_refs}
        master.update({ref:copy.deepcopy(target_map[ref]) for ref in defender_refs if ref in target_map})
        for ally_data in allied_support.values():
            master.update({ref: copy.deepcopy(ally_data["map"][ref]) for ref in ally_data["refs"] if ref in ally_data["map"]})
        frontage = 1 if kind=="formal_challenge" else local_frontage_count(site if isinstance(site,Mapping) else None)
        doctrines = {
            fid: source_faction.get("doctrine",{}) if isinstance(source_faction.get("doctrine"),Mapping) else {},
            target_fid: target_faction.get("doctrine",{}) if isinstance(target_faction.get("doctrine"),Mapping) else {},
        }
        for ally_fid, ally_data in allied_support.items():
            ally_faction = ally_data["faction"]
            doctrines[ally_fid] = ally_faction.get("doctrine", {}) if isinstance(ally_faction.get("doctrine"), Mapping) else {}
        engaged: set[str] = set(); newly_dead: set[str] = set(); exchanges=0; contacts=0; outcome="uncontested" if not defender_refs else "contact"
        aggregate_winner: str|None = "side_a" if attacker_refs and not defender_refs else None
        while True:
            active_a=[ref for ref in attacker_refs if ref in master and _combat_active(master[ref],year=at.year)]
            active_b=[ref for ref in defender_refs if ref in master and _combat_active(master[ref],year=at.year)]
            active_a.sort(key=lambda ref:(-combat_readiness_score(master[ref],year=at.year),ref)); active_b.sort(key=lambda ref:(-combat_readiness_score(master[ref],year=at.year),ref))
            if not active_a: aggregate_winner="side_b" if active_b else aggregate_winner; outcome="attack_broken"; break
            if not active_b: aggregate_winner="side_a"; outcome="defense_broken"; break
            wave_a=active_a[:frontage]; wave_b=active_b[:frontage]; contacts+=1; engaged.update(wave_a); engaged.update(wave_b)
            before_dead={ref for ref,p in master.items() if not _living(p)}
            result=simulate_exact_combat(
                combat_ref=f"combat:{op_ref}:contact:{contacts}",side_a_refs=wave_a,side_b_refs=wave_b,
                people={ref:copy.deepcopy(master[ref]) for ref in wave_a+wave_b},equipment_ledger=equipment,doctrines=doctrines,
                zone_ref=target_site,started_at=at.isoformat(),
                objective={"kind":kind,"source_faction_ref":fid,"target_faction_ref":target_fid,"operation_ref":op_ref,"contact_index":contacts},
                targeting_intent=strategic_operation_targeting_intent(op),
                max_exchanges=160 if kind=="formal_challenge" else (128 if kind in {"faction_war_strike","custody_rescue"} else 96),
                environment=battlefield_environment, social_state=social,
            )
            social=copy.deepcopy(dict(result.get("social_state_after") or social))
            equipment=copy.deepcopy(dict(result["equipment_ledger_after"])); exchanges+=max(0,int(result.get("exchanges",0)))
            for ref,person in result.get("people_after",{}).items():
                if isinstance(ref,str) and isinstance(person,Mapping): master[ref]=copy.deepcopy(dict(person))
            wave_dead={ref for ref,p in master.items() if not _living(p) and ref not in before_dead}
            # Mutual-awareness exact combat can create one current vengeance
            # duty for a close relative who was physically in this contact.
            # The relation is not inherited to an entire lineage or logged as
            # history. If the killer also dies, universal death cleanup removes
            # the now-actionless obligation below.
            for dead_ref in sorted(wave_dead):
                killer_ref=""
                for ev in result.get("last_events",[]):
                    if not isinstance(ev,Mapping) or str(ev.get("actual_ref") or "")!=dead_ref:continue
                    if str(ev.get("result") or "") not in {"contact","physical_contact_no_wound"}:continue
                    actor=str(ev.get("actor_ref") or "")
                    if actor and actor!=dead_ref:killer_ref=actor
                if not killer_ref:continue
                for relative_ref in sorted(close_family_refs(family,dead_ref)):
                    if relative_ref not in set(wave_a+wave_b) or relative_ref==killer_ref:continue
                    if relative_ref not in master or not _living(master[relative_ref]):continue
                    added=add_personal_obligation(
                        social,actor_ref=relative_ref,counterparty_ref=killer_ref,kind="vengeance",
                        strength=85,created_at=at.isoformat(),
                    )
                    social=added["state_after"]
            newly_dead.update(wave_dead)
            winner=result.get("winner_side")
            if not bool(result.get("resolved")):
                aggregate_winner=None; outcome="local_stalemate"; break
            if winner=="side_b": aggregate_winner="side_b"; outcome="attack_broken"; break
            if kind in {"formal_challenge","faction_raid","faction_reconnaissance"}:
                aggregate_winner=winner if isinstance(winner,str) else None; outcome="contact_complete"; break
            # War and custody rescue continue through successive local frontages
            # while combat-ready defenders still bar the objective. Winning one
            # doorway/contact does not teleport rescuers past the rest of a base.
            if winner!="side_a": aggregate_winner=None; outcome="local_stalemate"; break

        for ref in attacker_refs:
            if ref in master: source_map[ref]=master[ref]
        for ref in defender_refs:
            if ref in master and ref in target_map: target_map[ref]=master[ref]
        source_roster["people"]=[source_map.get(str(p.get("person_id")),p) if isinstance(p,Mapping) else p for p in source_people]
        target_roster["people"]=[target_map.get(str(p.get("person_id")),p) if isinstance(p,Mapping) else p for p in target_people]
        source_faction=reconcile_faction_population(source_faction,source_roster); target_faction=reconcile_faction_population(target_faction,target_roster)
        writes[faction_path(fid)]=compact_faction_state(source_faction); writes[roster_path(fid)]=compact_roster_state(source_roster,faction=source_faction)
        writes[faction_path(target_fid)]=compact_faction_state(target_faction); writes[roster_path(target_fid)]=compact_roster_state(target_roster,faction=target_faction)
        for ally_fid, ally_data in allied_support.items():
            ally_map = ally_data["map"]
            for ref in ally_data["refs"]:
                if ref in master: ally_map[ref] = master[ref]
            ally_roster = ally_data["roster"]
            ally_people = ally_data["people"]
            ally_roster["people"] = [ally_map.get(str(p.get("person_id")), p) if isinstance(p, Mapping) else p for p in ally_people]
            ally_faction = reconcile_faction_population(ally_data["faction"], ally_roster)
            ally_data["faction"] = ally_faction; ally_data["roster"] = ally_roster
            writes[faction_path(ally_fid)] = compact_faction_state(ally_faction)
            writes[roster_path(ally_fid)] = compact_roster_state(ally_roster, faction=ally_faction)
        if newly_dead:
            commitments=remove_people_from_commitments(commitments,person_refs=sorted(newly_dead))
        raid_objective = None
        if kind == "faction_raid" and aggregate_winner == "side_a":
            op, raid_objective, commitments = _apply_strategic_raid_objective(
                view=_View(read_json, writes), writes=writes, operation_ref=op_ref, operation=op,
                target_faction=target_faction, target_fid=target_fid, attacker_refs=attacker_refs,
                defender_refs=defender_refs, people_after=master, commitments=commitments, target_site=target_site, at=at,
            )
            writes[faction_path(target_fid)] = compact_faction_state(target_faction)
        relation_event="war_battle" if kind=="faction_war_strike" else ("armed_raid" if kind in {"faction_raid","custody_rescue","faction_reconnaissance"} else "tournament_sportsmanship")
        relations=_apply_relation(relations,fid,target_fid,relation_event); relations=_apply_relation(relations,target_fid,fid,relation_event)
        for ally_fid in sorted(allied_support):
            relations=_apply_relation(relations,fid,ally_fid,relation_event); relations=_apply_relation(relations,ally_fid,fid,relation_event)
        if newly_dead:
            if any(ref in newly_dead for ref in attacker_refs): relations=_apply_relation(relations,fid,target_fid,"member_killed")
            if any(ref in newly_dead for ref in defender_refs if ref in target_map): relations=_apply_relation(relations,target_fid,fid,"member_killed")
            for ally_fid, ally_data in allied_support.items():
                if any(ref in newly_dead for ref in ally_data["refs"]): relations=_apply_relation(relations,ally_fid,fid,"member_killed")
            # Publish battle diplomacy before death closure. If a last-member
            # casualty extinguishes either institution, extinction removes these
            # current edges instead of a stale local relation copy restoring them.
            writes[_RELATIONS]=copy.deepcopy(relations)
            writes[_SOCIAL]=copy.deepcopy(social)
            # Stage the current deployment owner before pruning dead durable
            # references, then reload it from the universal closer's after-image.
            deployments["deployments"]=rows
            writes[_DEPLOYMENTS]=copy.deepcopy(deployments)
            post_view=_View(read_json,writes)
            _clear_dead_current_state(view=post_view,writes=writes,dead=newly_dead,involved_factions=tuple([fid,target_fid]+sorted(allied_support)),at=at)
            if isinstance(writes.get(_RELATIONS), Mapping):
                relations=copy.deepcopy(dict(writes[_RELATIONS]))
            if isinstance(writes.get(_DEPLOYMENTS), Mapping):
                deployments=copy.deepcopy(dict(writes[_DEPLOYMENTS]))
                rows=deployments.setdefault("deployments", {})
                if not isinstance(rows, dict):
                    raise ValueError("jianghu deployments invalid after death closure")
            if isinstance(writes.get(_SOCIAL), Mapping):
                social=copy.deepcopy(dict(writes[_SOCIAL]))
            if isinstance(writes.get(_FAMILY), Mapping):
                family=copy.deepcopy(dict(writes[_FAMILY]))
        rescue_success = False
        rescued_captive_ref = ""
        rescued_owner_fid = ""
        rescued_returns_with_party = False
        if kind == "custody_rescue":
            try:
                custody = copy.deepcopy(dict(view(_CUSTODY)))
            except FileNotFoundError:
                custody = {"schema":"jianghu-custody-state-1.0","records":[]}
            if isinstance(writes.get(_CUSTODY), Mapping):
                custody = copy.deepcopy(dict(writes[_CUSTODY]))
            custody_rows = custody.get("records", []) if isinstance(custody, Mapping) else []
            custody_id = str(op.get("custody_id") or "")
            captive_ref = str(op.get("captive_ref") or "")
            record_index = next((
                i for i, row in enumerate(custody_rows)
                if isinstance(row, Mapping)
                and str(row.get("custody_id") or "") == custody_id
                and str(row.get("person_ref") or "") == captive_ref
                and str(row.get("holder_faction_ref") or "") == target_fid
                and row.get("status") not in {"released","escaped","rescued","executed"}
            ), None) if isinstance(custody_rows, list) else None
            location_matches = False
            if record_index is not None:
                record = custody_rows[record_index]
                custody_location = str(record.get("location_ref") or "")
                custody_site = sites.get(custody_location) if isinstance(sites, Mapping) else None
                custody_place = str(custody_site.get("parent_place_ref") or "") if isinstance(custody_site, Mapping) else custody_location
                location_matches = custody_location == target_site or custody_place == target_place
            rescue_won = bool(attacker_refs) and (not defender_refs or aggregate_winner == "side_a")
            if record_index is not None and location_matches and rescue_won:
                try:
                    rescued_owner_fid = _person_owner_faction(
                        _View(read_json, writes), captive_ref, preferred=(fid, target_fid, "house_tang")
                    )
                    next_commitments = release_resources(commitments, activity_ref=custody_id)
                    if captive_ref != player_ref:
                        next_commitments = extend_commitment_resources(
                            next_commitments, activity_ref=op_ref,
                            resources=[("person", captive_ref, rescued_owner_fid or fid)],
                        )
                    custody_transition(
                        custody_rows[record_index], action="rescue", at=at.isoformat(), actor_ref=attacker_refs[0],
                    )
                except (KeyError, ValueError):
                    rescue_success = False
                else:
                    commitments = next_commitments
                    custody_rows.pop(record_index)
                    custody["records"] = custody_rows
                    writes[_CUSTODY] = custody
                    rescue_success = True
                    rescued_captive_ref = captive_ref
                    rescued_returns_with_party = captive_ref != player_ref
                    if rescued_returns_with_party and rescued_owner_fid and rescued_owner_fid != fid:
                        op["repatriate_after_return"] = {
                            "person_ref": captive_ref,
                            "owner_faction_ref": rescued_owner_fid,
                            "cause_ref": custody_id,
                        }
                        relations = _apply_relation(relations, rescued_owner_fid, fid, "rescued_members")
                    rescuer_ref=attacker_refs[0] if attacker_refs else ""
                    if rescuer_ref and rescued_captive_ref:
                        social=apply_relationship_event(
                            social,observer_ref=rescued_captive_ref,subject_ref=rescuer_ref,
                            event_kind="rescue",observer_knows=True,severity_milli=1200,
                            protected_player_ref=player_ref or "pc_wei_tang",
                        )["state_after"]
                        repayable=[
                            row for row in obligations_for_actor(social,rescuer_ref)
                            if str(row.get("counterparty_ref") or "")==rescued_captive_ref
                            and str(row.get("kind") or "") in {"life_debt","promise_aid","promise_protect"}
                        ]
                        if repayable:
                            repayable.sort(key=lambda row:(-int(row.get("strength",0)),str(row.get("kind") or "")))
                            row=repayable[0]
                            ref=personal_obligation_ref(rescuer_ref,rescued_captive_ref,str(row.get("kind") or ""))
                            social=resolve_personal_obligation(social,obligation_ref_value=ref)["state_after"]
                        else:
                            social=add_personal_obligation(
                                social,actor_ref=rescued_captive_ref,counterparty_ref=rescuer_ref,
                                kind="life_debt",strength=80,created_at=at.isoformat(),
                            )["state_after"]
                        writes[_SOCIAL]=copy.deepcopy(social)

        allied_defender_count = sum(len(data["refs"]) for data in allied_support.values())
        for ally_data in allied_support.values():
            for support_ref in ally_data["operation_refs"]:
                support_raw = rows.get(support_ref)
                if not isinstance(support_raw, Mapping):
                    continue
                support = copy.deepcopy(dict(support_raw))
                survivors = [ref for ref in support.get("participant_refs", []) if isinstance(ref, str) and ref in master and _living(master[ref])]
                if survivors:
                    support["participant_refs"] = survivors; support["status"] = "return_preparing"; support["pending_travel_direction"] = "return"
                    support.pop("physical_movement_ref", None); rows[support_ref] = support
                    schedule = upsert_one_off_event(schedule, {"event_id": f"operation_departure:return:{support_ref}", "kind": "faction_operation_departure", "due_at": (at + timedelta(seconds=1)).isoformat(), "owner_ref": support_ref, "direction": "return", "arrival_event_kind": "faction_operation_return", "requires_player_decision": False})
                else:
                    commitments = release_resources(commitments, activity_ref=support_ref); rows.pop(support_ref, None)
        surviving_attackers=[ref for ref in attacker_refs if ref not in newly_dead]
        return_participants=list(surviving_attackers)
        if kind == "faction_raid":
            # Captives are physical carried participants, not combat controllers.
            # The raid-objective helper adds them after combat; do not erase that
            # custody link when rebuilding the post-battle return party.
            for captive_ref in [str(x) for x in op.get("captive_refs", []) if isinstance(x, str) and x]:
                if captive_ref not in return_participants:
                    return_participants.append(captive_ref)
        if rescue_success and rescued_returns_with_party and rescued_captive_ref and rescued_captive_ref not in return_participants:
            return_participants.append(rescued_captive_ref)
        if surviving_attackers:
            current=copy.deepcopy(dict(op)); current["participant_refs"]=return_participants
            current["status"]="return_preparing"; current["pending_travel_direction"]="return"
            current.pop("physical_movement_ref", None)
            current["battle_force_count"]=len(attacker_refs); current["battle_defender_force_count"]=len(defender_refs); current["allied_defender_count"]=allied_defender_count; current["local_frontage_count"]=frontage
            current["battle_outcome"] = outcome; current["battle_winner_side"] = aggregate_winner
            current["casualty_refs"] = sorted(ref for ref in newly_dead if ref in attacker_refs)
            if kind == "custody_rescue": current["rescue_success"] = rescue_success; current["rescued_captive_ref"] = rescued_captive_ref
            rows[op_ref]=current
            schedule=upsert_one_off_event(schedule,{
                "event_id":f"operation_departure:return:{op_ref}","kind":"faction_operation_departure",
                "due_at":(at+timedelta(seconds=1)).isoformat(),"owner_ref":op_ref,"direction":"return",
                "arrival_event_kind":"faction_operation_return","requires_player_decision":False,
            })
        else:
            failed_op = copy.deepcopy(dict(op)); failed_op["battle_outcome"] = outcome; failed_op["battle_winner_side"] = aggregate_winner
            failed_op["allied_defender_count"] = allied_defender_count; failed_op["casualty_refs"] = sorted(ref for ref in newly_dead if ref in attacker_refs)
            if kind == "custody_rescue": failed_op["rescue_success"] = rescue_success; failed_op["rescued_captive_ref"] = rescued_captive_ref
            dossier_ref = str(failed_op.get("institutional_operation_ref") or "")
            if dossier_ref:
                close_institutional_operation(read_json=read_json, writes=writes, operation_ref=dossier_ref, at_iso=at.isoformat(), success=False, closure_reason="force_lost_in_field", physical_operation=failed_op, returned_refs=[], casualties=failed_op["casualty_refs"])
            commitments=release_resources(commitments,activity_ref=op_ref); rows.pop(op_ref,None)
        frontier_used.update(attacker_refs); frontier_used.update(defender_refs)
        review={"kind":"faction_operation_arrival","event_id":event.get("event_id"),"operation_ref":op_ref,"operation_kind":kind,"faction_ref":fid,"target_faction_ref":target_fid,"attacker_count":len(attacker_refs),"defender_force_count":len(defender_refs),"allied_defender_count":allied_defender_count,"engaged_count":len(engaged),"local_frontage_count":frontage,"contact_count":contacts,"exchanges":exchanges,"winner_side":aggregate_winner,"deaths":len(newly_dead),"result":"returning" if surviving_attackers else "closed","battle_outcome":outcome}
        if kind == "custody_rescue":
            review["captive_ref"] = str(op.get("captive_ref") or "")
            review["rescue_success"] = rescue_success
            review["rescued_owner_faction_ref"] = rescued_owner_fid
            if rescue_success and rescued_captive_ref == player_ref:
                notice = {
                    "kind": "player_rescued_travel_decision",
                    "person_ref": rescued_captive_ref, "location_ref": target_site or target_place,
                    "rescuer_faction_ref": fid, "delivered_to_player": True, "requires_player_decision": True,
                }
                handoffs.append({**notice, "handoff": classify_handoff(notice)})
        if isinstance(raid_objective, Mapping):
            review["raid_objective"] = copy.deepcopy(dict(raid_objective))
        reviews.append(review)
        if target_fid=="house_tang":
            notice={**review,"kind":"faction_war_result" if kind=="faction_war_strike" else ("faction_attack_result" if kind in {"faction_raid","custody_rescue"} else "faction_challenge_result"),"delivered_to_player":True,"requires_player_decision":False}
            handoffs.append({**notice,"handoff":classify_handoff(notice)})

    deployments["deployments"]=rows
    writes[_DEPLOYMENTS]=deployments; writes[_EQUIPMENT]=compact_equipment_ledger(equipment); writes[_RELATIONS]=relations; writes[_SOCIAL]=social; writes[_SCHEDULER]=schedule
    return {"writes":writes,"reviews":reviews,"handoffs":handoffs,"schedule_after":schedule}


def settle_faction_operation_returns(
    *, read_json: Callable[[str], Any], writes: dict[str, Any],
    events: Sequence[Mapping[str, Any]], at: datetime, schedule_after: Mapping[str, Any],
) -> dict[str, Any]:
    """Close same-settlement/local operation returns without a phantom road.

    Nonlocal returns close in the shared physical route lifecycle. This reducer
    handles the one legitimate no-road case: source and target are the same
    settlement, so the deployment itself remains the physical purpose owner for
    a short local return before exact people are released at their home site.
    """
    due = [row for row in events if isinstance(row, Mapping) and row.get("kind") == "faction_operation_return"]
    if not due:
        return {"writes": {}, "reviews": [], "handoffs": [], "schedule_after": copy.deepcopy(dict(schedule_after))}
    view = _View(read_json, writes)
    deployments = copy.deepcopy(dict(view(_DEPLOYMENTS)))
    rows = deployments.setdefault("deployments", {})
    if not isinstance(rows, dict):
        raise ValueError("jianghu deployments invalid")
    sites = _site_rows(view)
    equipment_ledger = copy.deepcopy(dict(view(_EQUIPMENT)))
    schedule = copy.deepcopy(dict(schedule_after))
    reviews: list[dict[str, Any]] = []

    for event in sorted(due, key=lambda row: (str(row.get("owner_ref") or ""), str(row.get("event_id") or ""))):
        op_ref = str(event.get("owner_ref") or "")
        raw = rows.get(op_ref)
        if not isinstance(raw, Mapping) or str(raw.get("status") or "") not in {"traveling_return", "arrived_pending"}:
            reviews.append({
                "kind": "faction_operation_return", "event_id": event.get("event_id"),
                "operation_ref": op_ref, "result": "operation_not_returning",
            })
            continue
        op = copy.deepcopy(dict(raw))
        fid = str(op.get("faction_ref") or "")
        participants = [str(x) for x in op.get("participant_refs", []) if isinstance(x, str) and x]
        try:
            faction = _load_faction(view, fid) if fid else {}
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            faction = {}
        home_place = str(op.get("source_place_ref") or faction.get("headquarters") or "")
        home_site = str(op.get("source_site_ref") or faction.get("local_site_ref") or "")
        if not home_site:
            home_site = _arrival_site(sites, home_place, home_place)

        # Update each conserved roster owner once. A rescued captive may belong
        # to a different faction from the rescuing deployment, so the operation
        # never assumes all participants share one owner.
        roster_updates: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        returned = 0
        for person_ref in participants:
            owner_fid = _person_owner_faction(view, person_ref, preferred=(fid,))
            if not owner_fid:
                continue
            if owner_fid not in roster_updates:
                owner_faction = _load_faction(view, owner_fid)
                owner_roster = _load_roster(view, owner_fid, owner_faction)
                roster_updates[owner_fid] = (owner_faction, owner_roster)
            owner_faction, owner_roster = roster_updates[owner_fid]
            people = owner_roster.get("people", []) if isinstance(owner_roster.get("people"), list) else []
            for ordinal, person in enumerate(people):
                if isinstance(person, Mapping) and str(person.get("person_id") or "") == person_ref:
                    moved = copy.deepcopy(dict(person)); moved["location_ref"] = home_site or home_place
                    people[ordinal] = moved; returned += 1; break
            owner_roster["people"] = people

        for owner_fid, (owner_faction, owner_roster) in roster_updates.items():
            owner_faction = reconcile_faction_population(owner_faction, owner_roster)
            writes[faction_path(owner_fid)] = compact_faction_state(owner_faction)
            writes[roster_path(owner_fid)] = compact_roster_state(owner_roster, faction=owner_faction)

        secured_cash = max(0, int(op.get("seized_cash", 0)))
        secured_item_ref = str(op.get("seized_item_ref") or "")
        secured_quantity = max(0, int(op.get("seized_quantity", 0)))
        if fid and secured_cash > 0:
            faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + secured_cash
            writes[faction_path(fid)] = compact_faction_state(faction)
        if fid and secured_item_ref and secured_quantity > 0:
            inventory = _load_inventory(view, fid)
            credit_cargo_to_inventory(inventory, item_ref=secured_item_ref, quantity=secured_quantity)
            writes[inventory_path(fid)] = compact_inventory_state(inventory)
        captive_refs = {str(x) for x in op.get("captive_refs", []) if isinstance(x, str) and x}
        if captive_refs:
            try:
                custody = copy.deepcopy(dict(view(_CUSTODY)))
            except FileNotFoundError:
                custody = {"schema": "jianghu-custody-state-1.0", "records": []}
            custody_changed = False
            for record in custody.get("records", []):
                if isinstance(record, dict) and str(record.get("person_ref") or "") in captive_refs and record.get("status") not in {"released", "escaped", "rescued", "executed"}:
                    record["location_ref"] = home_site or home_place; record["holder_faction_ref"] = fid or str(record.get("holder_faction_ref") or ""); custody_changed = True
            if custody_changed:
                writes[_CUSTODY] = custody

        recovered: dict[str, int] = {}
        lost_or_consumed: dict[str, int] = {}
        if fid and isinstance(op.get("issued_equipment"), Mapping):
            inventory = _load_inventory(view, fid)
            settled_issue = reclaim_operation_equipment(
                operation=op, inventory=inventory, equipment_ledger=equipment_ledger,
            )
            op = copy.deepcopy(dict(settled_issue["operation_after"]))
            equipment_ledger = copy.deepcopy(dict(settled_issue["equipment_ledger_after"]))
            recovered = copy.deepcopy(dict(settled_issue.get("recovered", {})))
            lost_or_consumed = copy.deepcopy(dict(settled_issue.get("lost_or_consumed", {})))
            writes[inventory_path(fid)] = compact_inventory_state(settled_issue["inventory_after"])
            writes[_EQUIPMENT] = compact_equipment_ledger(equipment_ledger)

        followup = op.get("repatriate_after_return") if isinstance(op.get("repatriate_after_return"), Mapping) else None
        followup_ref = ""
        if isinstance(followup, Mapping):
            person_ref = str(followup.get("person_ref") or "")
            owner_fid = str(followup.get("owner_faction_ref") or "")
            try:
                owner_faction = _load_faction(_View(read_json, writes), owner_fid) if owner_fid else {}
                owner_home = str(owner_faction.get("headquarters") or "") if isinstance(owner_faction, Mapping) else ""
                followup_ref, followup_op, followup_event = build_repatriation_operation(
                    person_ref=person_ref, owner_faction_ref=owner_fid, origin_place_ref=home_place,
                    home_place_ref=owner_home, at=at, cause_ref=str(followup.get("cause_ref") or op_ref),
                    counterparty_faction_ref=fid,
                )
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                followup_ref = ""
            if followup_ref and followup_ref not in rows:
                rows[followup_ref] = followup_op
                schedule = upsert_one_off_event(schedule, followup_event)
        dossier_ref = str(op.get("institutional_operation_ref") or "")
        if dossier_ref:
            operation_kind = str(op.get("operation_kind") or "")
            success = (
                bool(op.get("intelligence_report")) if operation_kind == "faction_reconnaissance" else
                bool(op.get("rescue_success")) if operation_kind == "custody_rescue" else
                str(op.get("battle_winner_side") or "") == "side_a" if operation_kind in {"faction_raid", "faction_war_strike"} else
                True
            )
            close_institutional_operation(
                read_json=read_json, writes=writes, operation_ref=dossier_ref, at_iso=at.isoformat(),
                success=success, closure_reason="returned_and_reported" if success else "returned_after_failure",
                physical_operation=op, returned_refs=participants, casualties=op.get("casualty_refs", []),
                equipment_recovered=recovered, equipment_lost_or_consumed=lost_or_consumed,
            )
        rows.pop(op_ref, None)
        reviews.append({
            "kind": "faction_operation_return", "event_id": event.get("event_id"),
            "operation_ref": op_ref, "faction_ref": fid, "returned_count": returned,
            "equipment_recovered": recovered,
            "equipment_lost_or_consumed": lost_or_consumed,
            "cash_secured": secured_cash,
            "cargo_secured": secured_quantity,
            "captive_count": len(captive_refs),
            "repatriation_operation_ref": followup_ref,
            "result": "completed",
        })

    deployments["deployments"] = rows
    writes[_DEPLOYMENTS] = deployments
    if schedule != schedule_after:
        writes[_SCHEDULER] = copy.deepcopy(schedule)
    return {"writes": writes, "reviews": reviews, "handoffs": [], "schedule_after": schedule}


__all__=["expand_new_strategic_mobilizations","local_frontage_count","settle_faction_operation_departures","settle_faction_operation_arrivals","settle_faction_operation_returns"]
