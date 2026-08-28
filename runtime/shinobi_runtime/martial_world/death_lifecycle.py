"""Shared exact-person death, family and estate lifecycle helpers.

A persistent person has one exact owner at a time: a faction roster, the
independent owner, or the civic owner. Death semantics must not depend on which
owner currently stores that identity. This module provides the universal lookup
and conserved estate transfer used by every mortality frontier.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Mapping, Sequence

from .faction_state import faction_path, roster_path
from .route_activity import compact_route_movement_roles, route_controlling_refs, route_potential_controller_refs
from .field_command import build_deployment_structure, validate_deployment_structure
from .equipment_state import compact_equipment_ledger, effective_person_loadout, hydrate_equipment_ledger
from .property import detach_faction_policy_holders, personally_owned_quantity, provenance_claim, recovery_demand_ref, set_nonholder_claim
from .government_finance import refund_bounty_escrow

_INDEPENDENTS_PATH = "state/martial-world/independent-people.json"
_CIVIC_PATH = "state/martial-world/civic-people.json"
_PROJECTS_PATH = "state/martial-world/projects.json"
_DEPLOYMENTS_PATH = "state/martial-world/deployments.json"
_ROUTE_OPERATIONS_PATH = "state/martial-world/route-operations.json"
_GOVERNMENT_PATH = "state/martial-world/government.json"
_CONTRACT_INDEX_PATH = "state/martial-world/contracts/index.json"
_SCHEDULER_PATH = "state/martial-world/scheduler.json"
_EQUIPMENT_PATH = "state/martial-world/equipment-ledger.json"
_TOURNAMENTS_PATH = "state/martial-world/tournaments.json"


def _record(read_json: Callable[[str], Any], writes: Mapping[str, Any], path: str) -> Any:
    if path in writes:
        return copy.deepcopy(writes[path])
    return copy.deepcopy(read_json(path))


def is_living(person: Mapping[str, Any]) -> bool:
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    return health.get("status") != "dead"


def exact_person_index(
    *, read_json: Callable[[str], Any], writes: Mapping[str, Any], faction_refs: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """Return one duplicate-checked current route for every exact person."""
    out: dict[str, dict[str, Any]] = {}

    def add_owner(path: str, owner_kind: str, owner_ref: str = "") -> None:
        try:
            owner = _record(read_json, writes, path)
        except FileNotFoundError:
            return
        rows = owner.get("people", []) if isinstance(owner, Mapping) else []
        if not isinstance(rows, list):
            raise ValueError(f"jianghu exact person owner invalid: {path}")
        for ordinal, raw in enumerate(rows):
            if not isinstance(raw, Mapping):
                continue
            ref = raw.get("person_id")
            if not isinstance(ref, str) or not ref:
                continue
            if ref in out:
                raise ValueError(f"duplicate jianghu exact person identity: {ref}")
            person = copy.deepcopy(dict(raw))
            if owner_kind == "faction":
                person["faction_ref"] = owner_ref
            else:
                person.pop("faction_ref", None)
            out[ref] = {
                "owner_kind": owner_kind,
                "owner_ref": owner_ref,
                "path": path,
                "ordinal": ordinal,
                "person": person,
            }

    for fid in sorted(set(str(x) for x in faction_refs if isinstance(x, str) and x)):
        add_owner(roster_path(fid), "faction", fid)
    add_owner(_INDEPENDENTS_PATH, "independent")
    add_owner(_CIVIC_PATH, "civic")
    return out


def close_family_authorities(
    family: Mapping[str, Any], *, dead_refs: Sequence[str], living_people: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Close current family authorities globally while retaining kinship facts."""
    out = copy.deepcopy(dict(family))
    dead = {str(x) for x in dead_refs if isinstance(x, str) and x}
    if not dead:
        return out

    marriages = out.get("marriages", {})
    if isinstance(marriages, dict):
        for key, raw in list(marriages.items()):
            if not isinstance(raw, Mapping):
                continue
            refs = [str(x) for x in raw.get("spouse_refs", []) if isinstance(x, str)]
            if any(ref in dead for ref in refs):
                row = copy.deepcopy(dict(raw))
                row["status"] = "widowed"
                row.pop("pregnancy", None)
                marriages[key] = row

    claims = out.get("succession_claims", {})
    if isinstance(claims, dict):
        for key, raw in list(claims.items()):
            if isinstance(raw, Mapping) and str(raw.get("person_ref") or "") in dead:
                claims.pop(key, None)

    households = out.get("households", {})
    if isinstance(households, dict):
        for key, raw in list(households.items()):
            if not isinstance(raw, Mapping):
                continue
            old_members = [str(x) for x in raw.get("member_refs", []) if isinstance(x, str)]
            if not any(ref in dead for ref in old_members):
                continue
            row = copy.deepcopy(dict(raw))
            members = [ref for ref in old_members if ref not in dead]
            if not members:
                households.pop(key, None)
                continue
            row["member_refs"] = members
            if str(row.get("head_ref") or "") not in members:
                viable = [ref for ref in members if ref in living_people and is_living(living_people[ref])]
                row["head_ref"] = min(
                    viable or members,
                    key=lambda ref: (int(living_people.get(ref, {}).get("birth_year", 10**9)), ref),
                )
            households[key] = row
    return out



def release_custody_held_by_extinct_factions(
    custody: Mapping[str, Any], *, extinct_refs: Sequence[str],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Release living detainees when their institutional holder no longer exists.

    Individual captor death does not dissolve institutional custody, but a faction
    with zero living members has no physical guard authority left. Current custody
    rows are removed and the released identities are returned so the caller can
    close any training/availability epoch at the same frontier.
    """
    out = copy.deepcopy(dict(custody))
    extinct = {str(ref) for ref in extinct_refs if isinstance(ref, str) and ref}
    released: list[dict[str, str]] = []
    if not extinct:
        return out, released
    rows = out.get("records", [])
    if not isinstance(rows, list):
        raise ValueError("jianghu custody records invalid")
    kept: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        row = copy.deepcopy(dict(raw))
        holder = str(row.get("holder_faction_ref") or "")
        if holder not in extinct or row.get("status") in {"released", "escaped", "rescued", "executed"}:
            kept.append(row)
            continue
        person_ref = str(row.get("person_ref") or "")
        if person_ref:
            released.append({
                "custody_id": str(row.get("custody_id") or f"custody:{person_ref}"),
                "person_ref": person_ref,
                "holder_faction_ref": holder,
            })
    out["records"] = kept
    return out, released


def clean_social_and_custody_for_deaths(
    social: Mapping[str, Any], custody: Mapping[str, Any], *, dead_refs: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any], set[str]]:
    """Close personal social ties and physical custody invalidated by death.

    A prisoner held by an institution remains in that institution's custody when
    one exact captor/guard dies; only the personal captor reference is cleared.
    Personal custody with no institutional holder ends immediately and returns
    the living prisoner reference so the caller can resume ordinary activity.
    """
    social_after = copy.deepcopy(dict(social))
    custody_after = copy.deepcopy(dict(custody))
    dead = {str(x) for x in dead_refs if isinstance(x, str) and x}
    released: set[str] = set()
    if not dead:
        return social_after, custody_after, released

    courtships = social_after.get("courtships", {})
    if isinstance(courtships, dict):
        for key, raw in list(courtships.items()):
            refs = raw.get("person_refs", []) if isinstance(raw, Mapping) else []
            if any(str(ref) in dead for ref in refs):
                courtships.pop(key, None)

    relationships = social_after.get("relationships", {})
    if isinstance(relationships, dict):
        for key in list(relationships):
            if any(part in dead for part in str(key).split("|", 1)):
                relationships.pop(key, None)

    # Sparse current social facts are invalid once either exact person needed
    # by the fact is dead.  We do not preserve dead-person obligation, vow, or
    # combat-familiarity archaeology in hot state.  Beliefs survive a dead
    # subject only when the subject is some other live object (for example a
    # route movement); a dead observer can no longer act on one.
    obligations = social_after.get("obligations", {})
    if isinstance(obligations, dict):
        for key, raw in list(obligations.items()):
            if not isinstance(raw, Mapping) or str(raw.get("actor_ref") or "") in dead or str(raw.get("counterparty_ref") or "") in dead:
                obligations.pop(key, None)
        if not obligations:
            social_after.pop("obligations", None)

    vows = social_after.get("vows", {})
    if isinstance(vows, dict):
        for key, raw in list(vows.items()):
            if not isinstance(raw, Mapping) or str(raw.get("person_ref") or "") in dead or str(raw.get("subject_ref") or "") in dead:
                vows.pop(key, None)
        if not vows:
            social_after.pop("vows", None)

    familiarity = social_after.get("martial_familiarity", {})
    if isinstance(familiarity, dict):
        for key, raw in list(familiarity.items()):
            if not isinstance(raw, Mapping) or str(raw.get("observer_ref") or "") in dead or str(raw.get("opponent_ref") or "") in dead:
                familiarity.pop(key, None)
        if not familiarity:
            social_after.pop("martial_familiarity", None)

    beliefs = social_after.get("beliefs", {})
    if isinstance(beliefs, dict):
        for key, raw in list(beliefs.items()):
            if not isinstance(raw, Mapping) or str(raw.get("observer_ref") or "") in dead:
                beliefs.pop(key, None)
        if not beliefs:
            social_after.pop("beliefs", None)

    rows = custody_after.get("records", [])
    if isinstance(rows, list):
        kept: list[dict[str, Any]] = []
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            prisoner = str(raw.get("person_ref") or "")
            captor = str(raw.get("captor_ref") or "")
            if prisoner in dead:
                continue
            if captor in dead:
                if str(raw.get("holder_faction_ref") or ""):
                    row = copy.deepcopy(dict(raw))
                    row["captor_ref"] = ""
                    kept.append(row)
                elif prisoner:
                    released.add(prisoner)
                continue
            kept.append(copy.deepcopy(dict(raw)))
        custody_after["records"] = kept
    return social_after, custody_after, released

def estate_heir_ref(
    family: Mapping[str, Any], *, dead_ref: str, living_people: Mapping[str, Mapping[str, Any]],
) -> str:
    """Return living spouse first, otherwise the stable first living child."""
    marriages = family.get("marriages", {}) if isinstance(family, Mapping) else {}
    if isinstance(marriages, Mapping):
        for raw in marriages.values():
            if not isinstance(raw, Mapping) or str(raw.get("status") or "married") not in {"married", "widowed"}:
                continue
            refs = [str(x) for x in raw.get("spouse_refs", []) if isinstance(x, str)]
            if dead_ref not in refs:
                continue
            for ref in refs:
                if ref != dead_ref and ref in living_people and is_living(living_people[ref]):
                    return ref
    parentage = family.get("parentage", {}) if isinstance(family, Mapping) else {}
    children: list[str] = []
    if isinstance(parentage, Mapping):
        for child_ref, raw in parentage.items():
            parents = raw.get("parent_refs", []) if isinstance(raw, Mapping) else []
            ref = str(child_ref)
            if dead_ref in parents and ref in living_people and is_living(living_people[ref]):
                children.append(ref)
    return sorted(children)[0] if children else ""


def person_place(person: Mapping[str, Any], site_rows: Mapping[str, Any]) -> str:
    place = str(person.get("home_place_ref") or person.get("location_ref") or "")
    site = site_rows.get(place) if isinstance(site_rows, Mapping) else None
    if isinstance(site, Mapping) and isinstance(site.get("parent_place_ref"), str):
        return str(site["parent_place_ref"])
    return place


def _change_person_cash(
    *, read_json: Callable[[str], Any], writes: dict[str, Any], route: Mapping[str, Any], delta: int | None = None, set_to: int | None = None,
) -> None:
    path = str(route["path"])
    owner = _record(read_json, writes, path)
    rows = owner.get("people", []) if isinstance(owner, Mapping) else []
    if not isinstance(rows, list):
        raise ValueError("jianghu exact person owner invalid")
    ref = str(route.get("person", {}).get("person_id") or "") if isinstance(route.get("person"), Mapping) else ""
    ordinal = int(route.get("ordinal", -1))
    if not (0 <= ordinal < len(rows) and isinstance(rows[ordinal], Mapping) and rows[ordinal].get("person_id") == ref):
        ordinal = next((i for i, row in enumerate(rows) if isinstance(row, Mapping) and row.get("person_id") == ref), -1)
    if ordinal < 0:
        raise ValueError(f"jianghu exact person route stale: {ref}")
    person = copy.deepcopy(dict(rows[ordinal]))
    current = max(0, int(person.get("personal_cash", 0)))
    person["personal_cash"] = max(0, int(set_to if set_to is not None else current + int(delta or 0)))
    rows[ordinal] = person
    owner["people"] = rows
    writes[path] = owner


def settle_exact_death_estates(
    *, read_json: Callable[[str], Any], writes: dict[str, Any], faction_refs: Sequence[str],
    family: Mapping[str, Any], dead_refs: Sequence[str], place_region: Mapping[str, str], site_rows: Mapping[str, Any],
) -> dict[str, Any]:
    """Conserve personal silver through universal family-first estate settlement.

    Faction members fall back to their institution only after no exact living
    spouse/child exists. Civic/independent estates fall back to regional
    circulating liquidity. Every destination is resolved before the purse is
    cleared, so a malformed region fails closed instead of burning silver.
    """
    index = exact_person_index(read_json=read_json, writes=writes, faction_refs=faction_refs)
    living_people = {
        ref: copy.deepcopy(route["person"])
        for ref, route in index.items()
        if isinstance(route.get("person"), Mapping) and is_living(route["person"])
    }
    settled = 0
    heir_cash = 0
    faction_cash = 0
    regional_cash = 0
    touched_faction_refs: set[str] = set()
    touched_market_regions: set[str] = set()
    touched_owner_paths: set[str] = set()

    for dead_ref in sorted(set(str(x) for x in dead_refs if isinstance(x, str) and x)):
        route = index.get(dead_ref)
        if not isinstance(route, Mapping):
            continue
        person = route.get("person", {}) if isinstance(route.get("person"), Mapping) else {}
        cash = max(0, int(person.get("personal_cash", 0)))
        if cash <= 0:
            continue
        heir = estate_heir_ref(family, dead_ref=dead_ref, living_people=living_people)
        if heir:
            heir_route = index.get(heir)
            if not isinstance(heir_route, Mapping):
                raise ValueError(f"jianghu estate heir route unresolved: {heir}")
            _change_person_cash(read_json=read_json, writes=writes, route=heir_route, delta=cash)
            _change_person_cash(read_json=read_json, writes=writes, route=route, set_to=0)
            touched_owner_paths.update((str(heir_route.get("path") or ""), str(route.get("path") or "")))
            if heir_route.get("owner_kind") == "faction":
                touched_faction_refs.add(str(heir_route.get("owner_ref") or ""))
            if route.get("owner_kind") == "faction":
                touched_faction_refs.add(str(route.get("owner_ref") or ""))
            living_people[heir]["personal_cash"] = max(0, int(living_people[heir].get("personal_cash", 0))) + cash
            heir_cash += cash
            settled += cash
            continue

        if route.get("owner_kind") == "faction":
            fid = str(route.get("owner_ref") or "")
            if not fid:
                raise ValueError("jianghu faction estate owner unresolved")
            fpath = faction_path(fid)
            faction = _record(read_json, writes, fpath)
            if not isinstance(faction, dict):
                raise ValueError("jianghu faction estate owner invalid")
            faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + cash
            writes[fpath] = faction
            _change_person_cash(read_json=read_json, writes=writes, route=route, set_to=0)
            touched_owner_paths.update((fpath, str(route.get("path") or "")))
            touched_faction_refs.add(fid)
            faction_cash += cash
            settled += cash
            continue

        place = person_place(person, site_rows)
        region = place_region.get(place)
        if not isinstance(region, str) or not region:
            raise ValueError(f"jianghu estate regional destination unresolved: {dead_ref}")
        mpath = f"state/martial-world/markets/{region}.json"
        market = _record(read_json, writes, mpath)
        if not isinstance(market, dict) or (market.get("region_id") not in (None, region)):
            raise ValueError(f"jianghu estate market invalid: {region}")
        market["cash_pool"] = max(0, int(market.get("cash_pool", 0))) + cash
        writes[mpath] = market
        _change_person_cash(read_json=read_json, writes=writes, route=route, set_to=0)
        touched_owner_paths.update((mpath, str(route.get("path") or "")))
        touched_market_regions.add(region)
        regional_cash += cash
        settled += cash

    property_claims_transferred = 0
    property_demands_transferred = 0
    property_demands_closed = 0
    policy_holders_detached = 0
    try:
        equipment_source = _record(read_json, writes, _EQUIPMENT_PATH)
        equipment = hydrate_equipment_ledger(equipment_source)
    except FileNotFoundError:
        equipment_source = None
        equipment = None
    if isinstance(equipment, dict):
        dead_set = set(str(x) for x in dead_refs if isinstance(x, str) and x)
        # Policy issue is current living-member custody. Death materializes the
        # physical gear on the corpse and preserves faction title explicitly.
        dead_by_faction: dict[str, set[str]] = {}
        for dead_ref in dead_set:
            route = index.get(dead_ref)
            if isinstance(route, Mapping) and route.get("owner_kind") == "faction":
                fid = str(route.get("owner_ref") or "")
                if fid:
                    dead_by_faction.setdefault(fid, set()).add(dead_ref)
        for fid in sorted(dead_by_faction):
            detached = detach_faction_policy_holders(
                equipment, source_faction_ref=fid, holder_refs=dead_by_faction[fid],
            )
            equipment = hydrate_equipment_ledger(detached["equipment_ledger_after"])
            policy_holders_detached += int(detached.get("detached_policy_holder_count", 0))

        successors: dict[str, str] = {}
        for dead_ref in sorted(dead_set):
            route = index.get(dead_ref)
            if not isinstance(route, Mapping):
                continue
            successor = estate_heir_ref(family, dead_ref=dead_ref, living_people=living_people)
            if not successor and route.get("owner_kind") == "faction":
                successor = str(route.get("owner_ref") or "")
            successors[dead_ref] = successor

        provenance = equipment.get("provenance_exceptions", {})
        if provenance not in (None, {}) and not isinstance(provenance, dict):
            raise ValueError("jianghu equipment provenance exceptions invalid")
        if isinstance(provenance, dict):
            for holder_ref, item_rows in list(provenance.items()):
                if not isinstance(item_rows, dict):
                    raise ValueError("jianghu equipment provenance holder invalid")
                for item_ref, raw_claim in list(item_rows.items()):
                    if not isinstance(raw_claim, Mapping):
                        continue
                    old_owner = str(raw_claim.get("owner_ref") or "")
                    if old_owner not in dead_set:
                        continue
                    successor = successors.get(old_owner, "")
                    if successor and successor != str(holder_ref):
                        row = copy.deepcopy(dict(raw_claim)); row["owner_ref"] = successor
                        item_rows[str(item_ref)] = row
                        property_claims_transferred += 1
                    else:
                        item_rows.pop(item_ref, None)
                if not item_rows:
                    provenance.pop(holder_ref, None)
            if not provenance:
                equipment.pop("provenance_exceptions", None)

        for dead_ref in sorted(dead_set):
            successor = successors.get(dead_ref, "")
            if not successor:
                continue
            loadout = effective_person_loadout(equipment, dead_ref)
            items = loadout.get("items", {}) if isinstance(loadout, Mapping) else {}
            if not isinstance(items, Mapping):
                continue
            for item_ref, raw_qty in sorted(items.items()):
                if max(0, int(raw_qty)) <= 0 or provenance_claim(equipment, dead_ref, str(item_ref)) is not None:
                    continue
                personal_qty = personally_owned_quantity(equipment, dead_ref, str(item_ref))
                if personal_qty <= 0:
                    continue
                equipment = set_nonholder_claim(
                    equipment, holder_ref=dead_ref, item_ref=str(item_ref),
                    owner_ref=successor, quantity=personal_qty, status="estate",
                )
                property_claims_transferred += 1

        raw_demands = equipment.get("recovery_demands", {})
        if raw_demands not in (None, {}) and not isinstance(raw_demands, Mapping):
            raise ValueError("jianghu property recovery demands invalid")
        rebuilt_demands: dict[str, dict[str, Any]] = {}
        if isinstance(raw_demands, Mapping):
            for demand_ref, raw in raw_demands.items():
                if not isinstance(raw, Mapping):
                    continue
                old_owner = str(raw.get("owner_ref") or "")
                if old_owner not in dead_set:
                    rebuilt_demands[str(demand_ref)] = copy.deepcopy(dict(raw)); continue
                holder_ref = str(raw.get("holder_ref") or "")
                item_ref = str(raw.get("item_ref") or "")
                owner_ref = successors.get(old_owner, "")
                claim = provenance_claim(equipment, holder_ref, item_ref) if holder_ref and item_ref else None
                if not owner_ref or owner_ref == holder_ref or not isinstance(claim, Mapping) or str(claim.get("owner_ref") or "") != owner_ref:
                    property_demands_closed += 1; continue
                qty = min(max(0, int(raw.get("quantity", 0))), max(0, int(claim.get("quantity", 0))))
                if qty <= 0:
                    property_demands_closed += 1; continue
                row = copy.deepcopy(dict(raw))
                row["owner_ref"] = owner_ref; row["holder_ref"] = holder_ref; row["item_ref"] = item_ref; row["quantity"] = qty
                ref = recovery_demand_ref(owner_ref=owner_ref, holder_ref=holder_ref, item_ref=item_ref)
                property_demands_transferred += 1
                rebuilt_demands[ref] = row
        if rebuilt_demands:
            equipment["recovery_demands"] = rebuilt_demands
        else:
            equipment.pop("recovery_demands", None)
        equipment_after = compact_equipment_ledger(equipment)
        if equipment_source != equipment_after:
            writes[_EQUIPMENT_PATH] = equipment_after
            touched_owner_paths.add(_EQUIPMENT_PATH)

    return {
        "settled_cash": settled,
        "heir_cash": heir_cash,
        "faction_cash": faction_cash,
        "regional_cash": regional_cash,
        "property_claims_transferred": property_claims_transferred,
        "property_demands_transferred": property_demands_transferred,
        "property_demands_closed": property_demands_closed,
        "equipment_policy_holders_detached": policy_holders_detached,
        "living_people": living_people,
        "touched_faction_refs": sorted(ref for ref in touched_faction_refs if ref),
        "touched_market_regions": sorted(touched_market_regions),
        "touched_owner_paths": sorted(path for path in touched_owner_paths if path),
    }


def prune_dead_from_durable_activities(
    *, read_json: Callable[[str], Any], writes: dict[str, Any], dead_refs: Sequence[str],
    faction_refs: Sequence[str],
) -> dict[str, Any]:
    """Remove dead exact people from every durable current-activity owner.

    Availability is derived from projects, deployments, routes, custody and
    tournament combat. A dead reference must therefore be removed from the
    authoritative owner, not merely from a derived availability projection.
    Carried route assets are deliberately left on a terminal movement so the
    route reducer can settle cargo/provisions/captives without destroying value.
    """
    dead = {str(x) for x in dead_refs if isinstance(x, str) and x}
    if not dead:
        return {
            "changed_paths": [], "retinue_member_loss_refs": [], "removed_deployment_refs": [],
            "closed_warrant_refs": [], "refunded_bounty_cash": 0,
            "refunded_deployment_cash": 0, "extinguished_route_refs": [],
            "closed_contract_refs": [], "pruned_contract_participant_refs": [],
            "refunded_contract_cash": 0,
        }

    changed: set[str] = set()
    retinue_member_losses: list[str] = []
    removed_deployments: list[str] = []
    closed_warrants: list[str] = []
    extinguished_routes: list[str] = []
    refunded_bounty_cash = 0
    refunded_deployment_cash = 0

    try:
        projects = _record(read_json, writes, _PROJECTS_PATH)
    except FileNotFoundError:
        projects = None
    if isinstance(projects, dict) and isinstance(projects.get("projects"), dict):
        touched = False
        for pref, raw in list(projects["projects"].items()):
            if not isinstance(raw, Mapping) or bool(raw.get("completed")):
                continue
            row = copy.deepcopy(dict(raw)); row_changed = False
            for key in ("skilled_worker_refs", "management_worker_refs", "general_worker_refs", "worker_refs"):
                refs = row.get(key)
                if not isinstance(refs, list):
                    continue
                before = [str(ref) for ref in refs if isinstance(ref, str) and str(ref)]
                after = [ref for ref in before if ref not in dead]
                if after != before:
                    row[key] = after; row_changed = True
            if row_changed:
                projects["projects"][pref] = row; touched = True
        if touched:
            writes[_PROJECTS_PATH] = projects; changed.add(_PROJECTS_PATH)

    people_index = exact_person_index(read_json=read_json, writes=writes, faction_refs=faction_refs)
    try:
        deployments = _record(read_json, writes, _DEPLOYMENTS_PATH)
    except FileNotFoundError:
        deployments = None
    if isinstance(deployments, dict) and isinstance(deployments.get("deployments"), dict):
        touched = False
        rows = deployments["deployments"]
        for dref, raw in list(rows.items()):
            if not isinstance(raw, Mapping):
                continue
            row = copy.deepcopy(dict(raw))
            if row.get("operation_kind") == "standing_retinue":
                if str(row.get("leader_ref") or "") in dead:
                    rows.pop(dref, None); removed_deployments.append(str(dref)); touched = True
                    continue
                choosers = [str(x) for x in row.get("chooser_refs", []) if isinstance(x, str)] if isinstance(row.get("chooser_refs"), list) else []
                members = [str(x) for x in row.get("member_refs", []) if isinstance(x, str)] if isinstance(row.get("member_refs"), list) else []
                after_choosers = [ref for ref in choosers if ref not in dead]
                after_members = [ref for ref in members if ref not in dead]
                roles = row.get("member_roles", {}) if isinstance(row.get("member_roles"), Mapping) else {}
                after_roles = {str(ref): role for ref, role in roles.items() if str(ref) in after_members}
                if after_choosers != choosers or after_members != members or after_roles != roles:
                    row["chooser_refs"] = after_choosers; row["member_refs"] = after_members; row["member_roles"] = after_roles
                    if str(row.get("status") or "") == "active" and after_members != members:
                        retinue_member_losses.append(str(dref))
                    rows[dref] = row; touched = True
                continue

            # Temporary operation-issued gear is a finite return obligation only
            # while the exact holder remains part of the returning operation. If
            # that holder dies, estate settlement has already left any still-held
            # item physically on the corpse and recorded the lawful successor
            # title as provenance. Remove the dead holder from the operation issue
            # maps here so later return settlement cannot teleport corpse-held gear
            # back into the source armory. Consumed/missing gear likewise stays
            # consumed instead of being reported a second time at operation close.
            for issue_key in (
                "issued_equipment", "issued_equipment_baseline",
                "issued_equipment_claim_baseline",
            ):
                issue_rows = row.get(issue_key)
                if not isinstance(issue_rows, Mapping):
                    continue
                after_issue = {
                    str(ref): copy.deepcopy(value)
                    for ref, value in issue_rows.items()
                    if str(ref) not in dead
                }
                if after_issue == dict(issue_rows):
                    continue
                if after_issue:
                    row[issue_key] = after_issue
                else:
                    row.pop(issue_key, None)
                touched = True

            participants = row.get("participant_refs")
            if isinstance(participants, list):
                before = [str(ref) for ref in participants if isinstance(ref, str) and str(ref)]
                after = [ref for ref in before if ref not in dead]
                if after != before:
                    row["participant_refs"] = after; touched = True
                if before and not after and row.get("structure") is None:
                    reserve_fields = ("entry_fee_reserved_cash", "host_spend_reserved_cash", "delegate_ticket_reserved_cash")
                    reserve_cash = sum(max(0, int(row.get(key, 0))) for key in reserve_fields)
                    if reserve_cash:
                        fid = str(row.get("faction_ref") or "")
                        if not fid:
                            raise ValueError(f"jianghu empty deployment cash owner unresolved: {dref}")
                        fpath = faction_path(fid)
                        faction = _record(read_json, writes, fpath)
                        if not isinstance(faction, dict):
                            raise ValueError(f"jianghu empty deployment faction invalid: {fid}")
                        faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + reserve_cash
                        for key in reserve_fields:
                            row[key] = 0
                        writes[fpath] = faction; changed.add(fpath); refunded_deployment_cash += reserve_cash
                    rows.pop(dref, None); removed_deployments.append(str(dref)); touched = True
                    continue
            structure = row.get("structure") if isinstance(row.get("structure"), Mapping) else None
            if structure is not None:
                member_refs = [str(ref) for ref in structure.get("member_refs", []) if isinstance(ref, str)]
                survivors = [
                    ref for ref in member_refs if ref not in dead and ref in people_index
                    and isinstance(people_index[ref].get("person"), Mapping) and is_living(people_index[ref]["person"])
                ]
                if survivors != member_refs:
                    if not survivors:
                        rows.pop(dref, None); removed_deployments.append(str(dref)); touched = True
                        continue
                    records = {ref: copy.deepcopy(people_index[ref]["person"]) for ref in survivors}
                    preferred = str(structure.get("commander_ref") or "")
                    deputy = str(structure.get("deputy_ref") or "")
                    rebuilt = build_deployment_structure(
                        member_refs=survivors, records=records,
                        preferred_commander_ref=preferred if preferred in survivors else None,
                        preferred_deputy_ref=deputy if deputy in survivors else None,
                    )
                    validate_deployment_structure(rebuilt)
                    row["structure"] = rebuilt; touched = True
            if dref in rows:
                rows[dref] = row
        if touched:
            writes[_DEPLOYMENTS_PATH] = deployments; changed.add(_DEPLOYMENTS_PATH)

    try:
        route_ops = _record(read_json, writes, _ROUTE_OPERATIONS_PATH)
    except FileNotFoundError:
        route_ops = None
    if isinstance(route_ops, dict):
        touched = False
        movements = route_ops.get("movements", {})
        if isinstance(movements, dict):
            for mref, raw in list(movements.items()):
                if not isinstance(raw, Mapping):
                    continue
                row = copy.deepcopy(dict(raw)); row_changed = False
                for key in ("participant_refs", "escort_refs", "raider_refs", "contact_attacker_refs"):
                    refs = row.get(key)
                    if not isinstance(refs, list):
                        continue
                    before = [str(ref) for ref in refs if isinstance(ref, str) and str(ref)]
                    after = [ref for ref in before if ref not in dead]
                    if after != before:
                        row[key] = after; row_changed = True
                if str(row.get("leader_ref") or "") in dead:
                    survivors = [str(x) for x in row.get("participant_refs", []) if isinstance(x, str)] if isinstance(row.get("participant_refs"), list) else []
                    if survivors:
                        row["leader_ref"] = survivors[0]
                    else:
                        row.pop("leader_ref", None)
                    row_changed = True

                movement_kind = str(row.get("movement_kind") or "")
                original_participants = [str(ref) for ref in raw.get("participant_refs", []) if isinstance(ref, str) and str(ref)]
                remaining_participants = [str(ref) for ref in row.get("participant_refs", []) if isinstance(ref, str) and str(ref)]
                extinguished = bool(original_participants and not remaining_participants)

                # Physical presence and route control are different authorities.
                # Captives, rescued people, clients and other protected travelers
                # may remain alive after every escort/raider controlling the party
                # dies. They must never inherit route control merely because they
                # remain in participant_refs. If a non-carried potential controller
                # survives but is not currently assigned/fit, park the party so the
                # route frontier can retry after recovery; if only carried people
                # remain, the party is extinguished and purpose-specific salvage or
                # repatriation resolves them at the next route service frontier.
                original_controllers = route_controlling_refs(raw)
                controller_refs = route_controlling_refs(row)
                carried_refs = {
                    str(ref)
                    for key in ("protected_person_refs", "captive_refs", "rescued_refs")
                    for ref in (row.get(key, []) if isinstance(row.get(key), list) else [])
                    if isinstance(ref, str) and ref
                }
                if original_controllers and not controller_refs and remaining_participants:
                    potential_controllers = route_potential_controller_refs(row)
                    if potential_controllers:
                        if str(row.get("status") or "") not in {"completed", "closed", "party_extinguished"}:
                            row["status"] = "awaiting_return_logistics"
                            row["controller_loss_by_death"] = True
                            row.pop("leader_ref", None)
                            row_changed = True
                    elif carried_refs.intersection(remaining_participants):
                        extinguished = True
                        row.pop("leader_ref", None)

                if extinguished and str(row.get("status") or "") not in {"completed", "closed"}:
                    row["status"] = "party_extinguished"
                    row["party_extinguished_by_death"] = True
                    row.pop("controller_loss_by_death", None)
                    extinguished_routes.append(str(mref)); row_changed = True
                if row_changed:
                    movements[mref] = compact_route_movement_roles(row); touched = True
        contacts = route_ops.get("contacts", {})
        if isinstance(contacts, dict):
            for cref, raw in list(contacts.items()):
                if not isinstance(raw, Mapping):
                    continue
                row = copy.deepcopy(dict(raw)); row_changed = False
                for key in ("attacker_refs", "escort_refs"):
                    refs = row.get(key)
                    if not isinstance(refs, list):
                        continue
                    before = [str(ref) for ref in refs if isinstance(ref, str) and str(ref)]
                    after = [ref for ref in before if ref not in dead]
                    if after != before:
                        row[key] = after; row_changed = True
                if row_changed:
                    contacts[cref] = row; touched = True
        if touched:
            writes[_ROUTE_OPERATIONS_PATH] = route_ops; changed.add(_ROUTE_OPERATIONS_PATH)

    # Tournament registration is a paid/bracket fact and may remain after
    # death, but delegation roles are claims of physical presence. Remove dead
    # people from every physical delegation role immediately so a dead leader
    # or spectator cannot keep attending later convergence days.
    try:
        tournament_state = _record(read_json, writes, _TOURNAMENTS_PATH)
    except FileNotFoundError:
        tournament_state = None
    if isinstance(tournament_state, dict):
        tournament_touched = False
        tournaments = tournament_state.get("tournaments", {})
        if isinstance(tournaments, dict):
            for tref, raw_tournament in list(tournaments.items()):
                if not isinstance(raw_tournament, Mapping):
                    continue
                tournament = copy.deepcopy(dict(raw_tournament))
                delegations = tournament.get("delegations", {})
                if not isinstance(delegations, dict):
                    continue
                delegation_touched = False
                for fid, raw_delegation in list(delegations.items()):
                    if not isinstance(raw_delegation, Mapping):
                        continue
                    row = copy.deepcopy(dict(raw_delegation)); row_changed = False
                    for key in ("entrant_refs", "spectator_refs", "leader_refs", "senior_refs"):
                        refs = row.get(key, [])
                        if not isinstance(refs, list):
                            continue
                        before = [str(ref) for ref in refs if isinstance(ref, str) and str(ref)]
                        after = [ref for ref in before if ref not in dead]
                        if after != before:
                            row[key] = after; row_changed = True
                    if not any(row.get(key) for key in ("entrant_refs", "spectator_refs", "leader_refs", "senior_refs")):
                        if row_changed:
                            delegations.pop(fid, None); delegation_touched = True
                        continue
                    present_count = len(set(row.get("entrant_refs", [])) | set(row.get("spectator_refs", [])))
                    if int(row.get("present_count", present_count)) != present_count:
                        row["present_count"] = present_count; row_changed = True
                    if row_changed:
                        delegations[fid] = row; delegation_touched = True
                if delegation_touched:
                    tournament["delegations"] = delegations
                    tournaments[tref] = tournament
                    tournament_touched = True
        if tournament_touched:
            writes[_TOURNAMENTS_PATH] = tournament_state; changed.add(_TOURNAMENTS_PATH)

    # Contract participation is a durable exact-person reference even before
    # departure.  Remove dead principals immediately.  If every accepted
    # principal dies before departure, or an exact protected client dies before
    # departure, the funded obligation is now impossible under the current
    # contract semantics and expires immediately with exact escrow refund.
    # In-progress contracts remain owned by their physical route reducer; only
    # their participant projection is compacted here so the route can settle
    # cargo, clients and payment without a dead escort lingering in the index.
    try:
        contract_index = _record(read_json, writes, _CONTRACT_INDEX_PATH)
    except FileNotFoundError:
        contract_index = None
    closed_contracts: list[str] = []
    pruned_contract_participants: list[str] = []
    refunded_contract_cash = 0
    if isinstance(contract_index, dict):
        active = contract_index.get("active", {})
        if not isinstance(active, dict):
            raise ValueError("jianghu contract index invalid")
        contract_touched = False
        for cref, raw in list(active.items()):
            if not isinstance(raw, Mapping):
                continue
            status = str(raw.get("status") or "")
            if status not in {"offered", "accepted", "in_progress", "objective_resolved"}:
                continue
            row = copy.deepcopy(dict(raw))
            before = [str(x) for x in row.get("participants", []) if isinstance(x, str)] if isinstance(row.get("participants"), list) else []
            after = [ref for ref in before if ref not in dead]
            participants_changed = after != before
            if participants_changed:
                row["participants"] = after
                pruned_contract_participants.append(str(cref))

            objective = row.get("objective", {}) if isinstance(row.get("objective"), Mapping) else {}
            protected = {str(x) for x in objective.get("protected_person_refs", []) if isinstance(x, str)} if isinstance(objective.get("protected_person_refs"), list) else set()
            protected_died = bool(protected & dead)
            all_accepted_principals_died = status == "accepted" and bool(before) and not after
            predeparture_impossible = status in {"offered", "accepted"} and (protected_died or all_accepted_principals_died)

            if predeparture_impossible:
                escrow = max(0, int(row.get("escrow_cash", 0)))
                issuer = str(row.get("issuer_ref") or "")
                if escrow:
                    if issuer.startswith("market:"):
                        region = issuer.split(":", 1)[1]
                        if not region:
                            raise ValueError(f"jianghu dead-contract market issuer invalid: {cref}")
                        mpath = f"state/martial-world/markets/{region}.json"
                        market = _record(read_json, writes, mpath)
                        if not isinstance(market, dict) or market.get("region_id") not in (None, region):
                            raise ValueError(f"jianghu dead-contract market invalid: {region}")
                        market["cash_pool"] = max(0, int(market.get("cash_pool", 0))) + escrow
                        writes[mpath] = market; changed.add(mpath)
                    elif issuer:
                        fpath = faction_path(issuer)
                        faction = _record(read_json, writes, fpath)
                        if not isinstance(faction, dict):
                            raise ValueError(f"jianghu dead-contract faction issuer invalid: {issuer}")
                        faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) + escrow
                        writes[fpath] = faction; changed.add(fpath)
                    else:
                        raise ValueError(f"jianghu dead-contract issuer unresolved: {cref}")
                    refunded_contract_cash += escrow
                active.pop(cref, None)
                closed_contracts.append(str(cref))
                contract_touched = True
                continue

            if participants_changed:
                active[cref] = row
                contract_touched = True

        if contract_touched:
            writes[_CONTRACT_INDEX_PATH] = contract_index; changed.add(_CONTRACT_INDEX_PATH)
            # Direct-command death cleanup does not pass through the frontier's
            # final scheduler compactor.  Remove closed expiry wakes here too.
            # The shared frontier may later rewrite the scheduler, but it also
            # prunes against the refreshed active-contract index.
            if closed_contracts:
                try:
                    scheduler = _record(read_json, writes, _SCHEDULER_PATH)
                except FileNotFoundError:
                    scheduler = None
                if isinstance(scheduler, dict):
                    one_off = scheduler.get("one_off", {})
                    if not isinstance(one_off, dict):
                        raise ValueError("jianghu scheduler one_off invalid")
                    closed_set = set(closed_contracts)
                    removed = False
                    for event_id, event in list(one_off.items()):
                        if not isinstance(event, Mapping) or str(event.get("kind") or "") != "contract_expiry_due":
                            continue
                        if str(event.get("owner_ref") or "") in closed_set:
                            one_off.pop(event_id, None); removed = True
                    if removed:
                        writes[_SCHEDULER_PATH] = scheduler; changed.add(_SCHEDULER_PATH)

    try:
        government = _record(read_json, writes, _GOVERNMENT_PATH)
    except FileNotFoundError:
        government = None
    if isinstance(government, dict):
        touched = False
        attention = government.get("attention", {})
        if isinstance(attention, dict):
            for ref in dead:
                if ref in attention:
                    attention.pop(ref, None); touched = True
        warrants = government.get("warrants", {})
        if isinstance(warrants, dict):
            for warrant_ref, raw in list(warrants.items()):
                if not isinstance(raw, Mapping) or str(raw.get("subject_ref") or "") not in dead:
                    continue
                escrow = max(0, int(raw.get("bounty_escrow_cash", 0)))
                if escrow:
                    region = str(raw.get("jurisdiction_ref") or "")
                    if not region:
                        raise ValueError(f"jianghu dead-subject bounty region unresolved: {warrant_ref}")
                    market_path = f"state/martial-world/markets/{region}.json"
                    market = _record(read_json, writes, market_path)
                    if not isinstance(market, dict) or market.get("region_id") not in (None, region):
                        raise ValueError(f"jianghu dead-subject bounty market invalid: {region}")
                    refund = refund_bounty_escrow(market, raw)
                    market = refund["market_after"]
                    writes[market_path] = market; changed.add(market_path); refunded_bounty_cash += int(refund["refunded_cash"])
                warrants.pop(warrant_ref, None); closed_warrants.append(str(warrant_ref)); touched = True
        if touched:
            writes[_GOVERNMENT_PATH] = government; changed.add(_GOVERNMENT_PATH)

    return {
        "changed_paths": sorted(changed),
        "retinue_member_loss_refs": sorted(set(retinue_member_losses)),
        "removed_deployment_refs": sorted(set(removed_deployments)),
        "closed_warrant_refs": sorted(set(closed_warrants)),
        "refunded_bounty_cash": refunded_bounty_cash,
        "refunded_deployment_cash": refunded_deployment_cash,
        "extinguished_route_refs": sorted(set(extinguished_routes)),
        "closed_contract_refs": sorted(set(closed_contracts)),
        "pruned_contract_participant_refs": sorted(set(pruned_contract_participants)),
        "refunded_contract_cash": refunded_contract_cash,
    }


__all__ = [
    "clean_social_and_custody_for_deaths", "close_family_authorities", "estate_heir_ref",
    "release_custody_held_by_extinct_factions",
    "exact_person_index", "is_living", "person_place", "prune_dead_from_durable_activities", "settle_exact_death_estates",
]
