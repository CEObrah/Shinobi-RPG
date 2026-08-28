"""Monthly faction upkeep, compensation, training and household-life frontier."""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .compensation import settle_monthly_compensation
from .faction_state import allows_ordinary_membership_exit
from .family_simulation import (
    advance_npc_relationships, apply_recognized_succession, review_conceptions,
)
from .death_lifecycle import clean_social_and_custody_for_deaths, close_family_authorities, exact_person_index, is_living, prune_dead_from_durable_activities, settle_exact_death_estates
from .handoffs import classify_handoff
from .institutional_lifecycle import settle_institutional_offices
from .institutional_obligations import member_transition_bound_person_refs
from .person_state import compact_person_state, compact_roster_state, hydrate_roster_state, reconcile_faction_population
from .property import detach_faction_policy_holders
from .training import settle_and_reset_faction_training_cycle
from .upkeep import monthly_upkeep_quote
from .travel_provisions import apply_monthly_upkeep_credit
from .world_health import (
    annual_voluntary_departure_refs, institutional_stress_milli, living_member_count, training_intensity_for_stress,
)

_FAMILY_PATH = "state/martial-world/family.json"
_SOCIAL_PATH = "state/martial-world/social.json"
_CUSTODY_PATH = "state/martial-world/custody.json"
_INDEPENDENTS_PATH = "state/martial-world/independent-people.json"
_EQUIPMENT_PATH = "state/martial-world/equipment-ledger.json"


def settle_faction_cycle_frontier(
    *,
    events: Sequence[Mapping[str, Any]], at: datetime, player_ref: str,
    family_state: Mapping[str, Any], social_state: Mapping[str, Any], custody_state: Mapping[str, Any],
    independent_state: Mapping[str, Any], writes: dict[str, Any], reviews: list[dict[str, Any]],
    handoffs: list[dict[str, Any]], pending_training_resume_refs: set[str],
    pending_one_off_events: list[dict[str, Any]], place_region: Mapping[str, str], site_rows: Mapping[str, Any],
    faction_refs: Sequence[str], read_json: Callable[[str], Any],
    faction_cache: dict[str, tuple[str, dict[str, Any]]], inventory_cache: dict[str, tuple[str, dict[str, Any]]],
    market_cache: dict[str, tuple[str, dict[str, Any]]], roster_cache: dict[str, tuple[str, dict[str, Any]]],
    load_faction: Callable[[str], tuple[str, dict[str, Any]]],
    load_inventory: Callable[[str], tuple[str, dict[str, Any]]],
    load_market: Callable[[str], tuple[str, dict[str, Any]]],
    load_roster: Callable[[str], tuple[str, dict[str, Any]]],
    family_bound_refs: Callable[[str], set[str]], unavailable_person_refs: Callable[[], set[str]],
) -> dict[str, Any]:
    at_iso = at.isoformat()
    family = copy.deepcopy(dict(family_state))
    social = copy.deepcopy(dict(social_state))
    custody = copy.deepcopy(dict(custody_state))
    independents = copy.deepcopy(dict(independent_state))

    def _transition_read(path: str) -> Any:
        return copy.deepcopy(writes[path]) if path in writes else read_json(path)

    # Institutional binding is distinct from time occupancy. Contract
    # principals and tournament delegates may train/live normally, but cannot
    # silently leave the institution while the finite obligation still names it.
    transition_bound_refs = member_transition_bound_person_refs(_transition_read)

    # Cross-owner conception needs a current exact-person world view, but a
    # monthly shard can settle several factions at the same timestamp. Rebuilding
    # every durable roster for each faction was O(factions * world population).
    # Keep one frontier-local exact index and refresh only person owners that
    # actually change during this shard.
    world_people_index: dict[str, dict[str, Any]] | None = None
    world_owner_refs: dict[str, set[str]] = {}

    def _ensure_world_people_index() -> dict[str, dict[str, Any]]:
        nonlocal world_people_index
        if world_people_index is None:
            routes = exact_person_index(read_json=read_json, writes=writes, faction_refs=faction_refs)
            world_people_index = {}
            world_owner_refs.clear()
            for ref, route in routes.items():
                person = route.get("person")
                path = str(route.get("path") or "")
                if not isinstance(person, Mapping) or not path:
                    continue
                world_people_index[ref] = copy.deepcopy(dict(person))
                world_owner_refs.setdefault(path, set()).add(ref)
        return world_people_index

    def _refresh_world_people_owner(path: str, owner: Mapping[str, Any], *, faction_ref: str = "") -> None:
        index = _ensure_world_people_index()
        for old_ref in world_owner_refs.pop(path, set()):
            index.pop(old_ref, None)
        rows = owner.get("people", []) if isinstance(owner, Mapping) else []
        if not isinstance(rows, list):
            raise ValueError(f"jianghu exact person owner invalid: {path}")
        refs: set[str] = set()
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            ref = raw.get("person_id")
            if not isinstance(ref, str) or not ref:
                continue
            if ref in index:
                raise ValueError(f"duplicate jianghu exact person identity: {ref}")
            person = copy.deepcopy(dict(raw))
            if faction_ref:
                person["faction_ref"] = faction_ref
            else:
                person.pop("faction_ref", None)
            index[ref] = person
            refs.add(ref)
        world_owner_refs[path] = refs

    upheld_factions: set[str] = set()
    upkeep_pressure: dict[str, dict[str, int]] = {}
    for event in events:
        if event.get("kind") != "faction_upkeep":
            continue
        fid = event.get("owner_ref")
        if not isinstance(fid, str) or fid in upheld_factions:
            continue
        fpath, faction = load_faction(fid)
        ipath, inventory = load_inventory(fid)
        transport = inventory.get("transport_capacity", {})
        if not isinstance(transport, Mapping):
            transport = {}
        quote = monthly_upkeep_quote(
            faction, rider_capacity_slots=int(transport.get("rider_slots", 0)), freight_capacity_kg=int(transport.get("freight_capacity_kg", 0)),
        )
        food_before = max(0, int(inventory.get("food_ration_days", 0)))
        cash_before = max(0, int(faction.get("treasury_cash", 0)))
        gross_food_due = int(quote["food_ration_days"])
        inventory, food_due, travel_food_credit_used = apply_monthly_upkeep_credit(
            inventory, gross_food_due=gross_food_due,
        )
        cash_due = int(quote["total_cash"])
        food_paid = min(food_before, food_due)
        cash_paid = min(cash_before, cash_due)
        upkeep_market = None; mpath = ""; region = None
        if cash_paid > 0:
            region = place_region.get(str(faction.get("headquarters", "")))
            if not isinstance(region, str) or not region:
                raise ValueError(f"jianghu upkeep regional destination unresolved: {fid}")
            try:
                mpath, upkeep_market = load_market(region)
            except FileNotFoundError as exc:
                raise ValueError(f"jianghu upkeep market unresolved: {region}") from exc
            if not isinstance(upkeep_market, dict) or upkeep_market.get("region_id") not in (None, region):
                raise ValueError(f"jianghu upkeep market invalid: {region}")
        inventory["food_ration_days"] = food_before - food_paid
        faction["treasury_cash"] = cash_before - cash_paid
        if cash_paid > 0 and isinstance(upkeep_market, dict) and isinstance(region, str):
            upkeep_market["cash_pool"] = max(0, int(upkeep_market.get("cash_pool", 0))) + cash_paid
            market_cache[region] = (mpath, upkeep_market); writes[mpath] = upkeep_market
        pressure = institutional_stress_milli(food_due=food_due, food_paid=food_paid, cash_due=cash_due, cash_paid=cash_paid)
        upkeep_pressure[fid] = {
            "food_due": food_due, "food_paid": food_paid, "cash_due": cash_due,
            "cash_paid": cash_paid, "stress_milli": pressure,
        }
        # ``apply_monthly_upkeep_credit`` returns a new inventory after-image.
        # Publish it to the shared cache as well as ``writes`` so the autonomy
        # reducer later in this same frontier observes the post-upkeep reserve
        # rather than the stale pre-upkeep object cached by ``load_inventory``.
        inventory_cache[fid] = (ipath, inventory)
        faction_cache[fid] = (fpath, faction)
        writes[ipath] = inventory; writes[fpath] = faction
        upheld_factions.add(fid)
        reviews.append({
            "kind": "faction_upkeep", "event_id": event.get("event_id"), "faction_ref": fid,
            "gross_food_due": gross_food_due, "travel_food_credit_used": travel_food_credit_used,
            "food_due": food_due, "food_consumed": food_paid, "food_shortfall": food_due - food_paid,
            "cash_due": cash_due, "cash_paid": cash_paid, "cash_shortfall": cash_due - cash_paid,
        })

    member_cycled: set[str] = set()
    for event in events:
        if event.get("kind") != "faction_member_cycle":
            continue
        fid = event.get("owner_ref")
        if not isinstance(fid, str) or fid in member_cycled:
            continue
        fpath, faction = load_faction(fid); rpath, roster = load_roster(fid)
        paid = settle_monthly_compensation(faction, roster)
        faction = copy.deepcopy(dict(paid["faction"])); roster = copy.deepcopy(dict(paid["roster"]))
        pressure_row = upkeep_pressure.get(fid, {})
        current_stress = institutional_stress_milli(
            food_due=max(0, int(pressure_row.get("food_due", 0))), food_paid=max(0, int(pressure_row.get("food_paid", 0))),
            cash_due=max(0, int(pressure_row.get("cash_due", 0))), cash_paid=max(0, int(pressure_row.get("cash_paid", 0))),
            stipend_due=max(0, int(paid.get("due_cash", 0))), stipend_paid=max(0, int(paid.get("paid_cash", 0))),
        )
        desired_intensity = training_intensity_for_stress(current_stress)
        faction, roster, training_summary = settle_and_reset_faction_training_cycle(
            faction, roster, at_iso=at_iso, next_intensity_milli=desired_intensity,
            paused_refs=sorted(unavailable_person_refs()),
        )
        roster_for_cache = roster
        dead_refs = {
            str(p.get("person_id")) for p in roster_for_cache.get("people", [])
            if isinstance(p, Mapping) and isinstance(p.get("person_id"), str)
            and isinstance(p.get("health"), Mapping) and p.get("health", {}).get("status") == "dead"
        }
        if dead_refs:
            # Stage the current after-images first so universal death settlement
            # can see this faction together with every other exact-person owner.
            writes[fpath] = faction
            writes[rpath] = compact_roster_state(roster_for_cache, faction=faction)
            person_index = exact_person_index(read_json=read_json, writes=writes, faction_refs=faction_refs)
            living_people = {
                ref: route["person"] for ref, route in person_index.items()
                if isinstance(route.get("person"), Mapping) and is_living(route["person"])
            }
            family = close_family_authorities(family, dead_refs=sorted(dead_refs), living_people=living_people)
            writes[_FAMILY_PATH] = family
            social, custody, released = clean_social_and_custody_for_deaths(
                social, custody, dead_refs=sorted(dead_refs),
            )
            writes[_SOCIAL_PATH] = social
            writes[_CUSTODY_PATH] = custody
            pending_training_resume_refs.update(released)
            people_rows = roster_for_cache.get("people", [])
            if isinstance(people_rows, list):
                cleaned=[]
                for raw in people_rows:
                    if isinstance(raw, Mapping) and str(raw.get("person_id")) in dead_refs and raw.get("standing_offices"):
                        person=copy.deepcopy(dict(raw)); person["standing_offices"]=[]; cleaned.append(person)
                    else: cleaned.append(raw)
                roster_for_cache["people"] = cleaned
            estate_result = settle_exact_death_estates(
                read_json=read_json, writes=writes, faction_refs=faction_refs, family=family,
                dead_refs=sorted(dead_refs), place_region=place_region, site_rows=site_rows,
            )
            prune_dead_from_durable_activities(
                read_json=read_json, writes=writes, dead_refs=sorted(dead_refs), faction_refs=faction_refs,
            )
            # Cross-owner inheritance may have changed another faction that was
            # already cached earlier in this same monthly frontier. Invalidate
            # every touched institutional cache before continuing.
            for touched_fid in estate_result.get("touched_faction_refs", []):
                faction_cache.pop(str(touched_fid), None)
                roster_cache.pop(str(touched_fid), None)
            fpath, faction = load_faction(fid)
            rpath, roster_for_cache = load_roster(fid)
            estate_cash_settled = int(estate_result["settled_cash"])
        else:
            estate_cash_settled = 0
        monthly_succession_ref = None
        if dead_refs:
            succession = apply_recognized_succession(
                family, faction_ref=fid, roster_people=[p for p in roster_for_cache.get("people", []) if isinstance(p, Mapping)], year=at.year,
            )
            roster_for_cache["people"] = succession["people_after"]
            monthly_succession_ref = succession.get("successor_ref")
            custody_unavailable = {
                str(row.get("person_ref"))
                for row in (custody.get("records", []) if isinstance(custody.get("records"), list) else [])
                if isinstance(row, Mapping) and str(row.get("person_ref") or "")
                and str(row.get("status") or "") not in {"released", "escaped", "rescued", "executed"}
            }
            office_result = settle_institutional_offices(
                faction, roster_for_cache, year=at.year, social=social,
                player_ref=player_ref or None, unavailable_refs=sorted(custody_unavailable),
            )
            roster_for_cache = office_result["roster"]
            if monthly_succession_ref is None:
                monthly_succession_ref = next((
                    row["person_ref"] for row in office_result["appointments"]
                    if row.get("office") == "leader"
                ), None)
            if monthly_succession_ref:
                notice = {"kind": "succession_notice", "faction_ref": fid, "successor_ref": monthly_succession_ref, "delivered_to_player": True}
                handoff = classify_handoff(notice); handoffs.append({**notice, "handoff": handoff})

        departure_refs = annual_voluntary_departure_refs(
            [p for p in roster_for_cache.get("people", []) if isinstance(p, Mapping)],
            faction_ref=fid, year=at.year, hardship_milli=current_stress,
            protected_refs=sorted(family_bound_refs(fid) | unavailable_person_refs() | transition_bound_refs | ({player_ref} if player_ref else set())),
            maximum=(max(1, living_member_count([p for p in roster_for_cache.get("people", []) if isinstance(p, Mapping)]) // 80) if current_stress >= 400 else 0),
            period_key=f"{at.year:04d}-{at.month:02d}", allow_voluntary_departure=allows_ordinary_membership_exit(fid),
        ) if current_stress >= 400 else []
        departure_refs = [ref for ref in departure_refs if ref not in transition_bound_refs]
        if departure_refs:
            leaving = set(departure_refs); kept: list[Any] = []
            independent_rows = independents.setdefault("people", [])
            if not isinstance(independent_rows, list): raise ValueError("jianghu independent people invalid")
            for raw in roster_for_cache.get("people", []):
                if not isinstance(raw, Mapping) or str(raw.get("person_id")) not in leaving:
                    kept.append(raw); continue
                person = compact_person_state(raw, faction_ref=fid)
                person.pop("membership_grade", None); person["standing_offices"] = []
                person["location_ref"] = str(raw.get("location_ref") or faction.get("local_site_ref") or faction.get("headquarters") or "")
                person["former_faction_ref"] = fid; person["independent_since"] = at_iso; independent_rows.append(person)
            roster_for_cache["people"] = kept
            try:
                equipment_source = writes.get(_EQUIPMENT_PATH, read_json(_EQUIPMENT_PATH))
            except FileNotFoundError:
                equipment_source = {"schema":"jianghu-equipment-ledger-1.0"}
            equipment_transition = detach_faction_policy_holders(
                equipment_source, source_faction_ref=fid, holder_refs=sorted(leaving),
            )
            writes[_EQUIPMENT_PATH] = equipment_transition["equipment_ledger_after"]
            faction = reconcile_faction_population(faction, roster_for_cache); faction_cache[fid] = (fpath, faction)
            # A voluntary departure is one exact-person ownership transfer.
            # Publish both owner after-images before any global person read in
            # this same frontier.  Otherwise the independent owner contains the
            # departing identity while the still-staged faction roster still
            # contains its old copy, and exact_person_index correctly rejects
            # the transient duplicate before conception/relationship review.
            staged_roster = compact_roster_state(roster_for_cache, faction=faction)
            writes[fpath] = faction
            writes[rpath] = staged_roster
            roster_cache[fid] = (rpath, hydrate_roster_state(staged_roster, faction=faction))
            writes[_INDEPENDENTS_PATH] = independents

        relationship_review = advance_npc_relationships(
            family, social, faction_ref=fid, roster_people=[p for p in roster_for_cache.get("people", []) if isinstance(p, Mapping)],
            at_iso=at_iso, player_ref=player_ref or None,
            residence_ref=str(faction.get("local_site_ref") or faction.get("headquarters") or "") or None,
            exclude_refs=sorted(unavailable_person_refs()), site_rows=site_rows,
        )
        if relationship_review["courtships_started"] or relationship_review["marriages_created"]:
            family = relationship_review["family_after"]; social = relationship_review["social_after"]
            writes[_FAMILY_PATH] = family; writes[_SOCIAL_PATH] = social
        # Conception is a relationship between exact spouses, not between two
        # rows that happen to share one faction owner. Build the current world
        # view so cross-faction marriages remain biologically live when spouses
        # are physically co-located and otherwise eligible.
        _refresh_world_people_owner(rpath, roster_for_cache, faction_ref=fid)
        if departure_refs:
            _refresh_world_people_owner(_INDEPENDENTS_PATH, independents)
        family_review = review_conceptions(
            family, faction_ref=fid, roster_people=[p for p in roster_for_cache.get("people", []) if isinstance(p, Mapping)],
            world_people=_ensure_world_people_index(), at_iso=at_iso, player_ref=player_ref or None,
            exclude_refs=sorted(unavailable_person_refs()),
        )
        if family_review["conceived_refs"]:
            family = family_review["family_after"]; pending_one_off_events.extend(family_review["one_off_events"]); writes[_FAMILY_PATH] = family
        roster = compact_roster_state(roster_for_cache, faction=faction)
        writes[fpath] = faction; writes[rpath] = roster; faction_cache[fid] = (fpath, faction)
        roster_cache[fid] = (rpath, hydrate_roster_state(roster, faction=faction)); member_cycled.add(fid)
        reviews.append({
            "kind": "faction_member_cycle", "event_id": event.get("event_id"), "faction_ref": fid,
            "stipend_due_cash": int(paid["due_cash"]), "stipend_paid_cash": int(paid["paid_cash"]),
            "stipend_shortfall_cash": int(paid["shortfall_cash"]), "institutional_stress_milli": current_stress,
            "training_intensity_milli": desired_intensity, "departures": len(departure_refs),
            "courtships_started": len(relationship_review["courtships_started"]), "succession_ref": monthly_succession_ref,
            "marriages_created": len(relationship_review["marriages_created"]), "conceptions_created": len(family_review["conceived_refs"]),
            "estate_cash_settled": estate_cash_settled, **training_summary,
        })

    return {
        "upkeep_pressure": upkeep_pressure, "family_state": family, "social_state": social,
        "custody_state": custody, "independent_state": independents,
    }


__all__ = ["settle_faction_cycle_frontier"]
