"""Bounded causal formation, merger, split and dissolution of current factions.

This reducer is intentionally conservative. It consumes only already-existing
exact people/assets and executes at most one transition on the final annual
faction chunk. It is not a random faction spawner.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .commitments import derived_commitment_state
from .faction_existence import mark_faction_extinct, register_materialized_faction_bundle
from .faction_politics import faction_camp
from .faction_registry import REGISTRY_PATH, unregister_faction
from .faction_state import (
    compact_faction_state, faction_admission_policy, faction_path, hydrate_faction_state, inventory_path,
    roster_path, resolved_faction_type,
)
from .faction_transitions import (
    primary_estate_projection, reconcile_family_transition, retire_faction_relations,
    retire_organizational_scale, transfer_holdings, transfer_inventory,
)
from .frontier_support import chunk_contains_final_owner
from .independent_people import compact_independent_person, hydrate_independent_person
from .inventory_state import compact_inventory_state, hydrate_inventory_state
from .institutional_obligations import faction_retirement_blockers, member_transition_blockers
from .person_state import compact_roster_state, hydrate_roster_state, reconcile_faction_population
from .site_control import active_site_controller
from .property import detach_faction_policy_holders, transfer_faction_property_authority

_ROOT = Path(__file__).resolve().parents[3]
_RULES_PATH = _ROOT / "game" / "data" / "martial-world" / "institutional-evolution.json"
_INDEPENDENTS = "state/martial-world/independent-people.json"
_RELATIONS = "state/martial-world/faction-relations.json"
_FAMILY = "state/martial-world/family.json"
_SOCIAL = "state/martial-world/social.json"
_EQUIPMENT = "state/martial-world/equipment-ledger.json"


def _rules() -> Mapping[str, Any]:
    raw = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("jianghu institutional evolution rules invalid")
    return raw


def _living(person: Mapping[str, Any]) -> bool:
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    return health.get("status") != "dead"


def _leader_ref(people: Sequence[Mapping[str, Any]]) -> str:
    for row in people:
        if not _living(row):
            continue
        offices = {str(x).split(":", 1)[0] for x in row.get("standing_offices", []) if isinstance(x, str)}
        if "leader" in offices and isinstance(row.get("person_id"), str):
            return str(row["person_id"])
    return ""


def _social_trust(social: Mapping[str, Any], source: str, target: str) -> int:
    rels = social.get("relationships", {}) if isinstance(social, Mapping) else {}
    row = rels.get(f"{source}|{target}") if isinstance(rels, Mapping) else None
    return int(row.get("trust", 0)) if isinstance(row, Mapping) else 0


def _faction_trust(relations: Mapping[str, Any], source: str, target: str) -> int:
    rows = relations.get("edges", []) if isinstance(relations, Mapping) else []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, Mapping) and row.get("from_faction") == source and row.get("to_faction") == target:
            return int(row.get("trust", 0))
    return 0


def _institutional_people(rows: Sequence[Mapping[str, Any]], *, leader_ref: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in rows:
        person = copy.deepcopy(dict(raw))
        person.pop("former_faction_ref", None)
        person.pop("independent_since", None)
        person.pop("faction_ref", None)
        person["membership_grade"] = str(person.get("membership_grade") or "full")
        person["standing_offices"] = ["leader"] if person.get("person_id") == leader_ref else []
        out.append(person)
    return out


def founder_curriculum(people: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Derive a new institution's teaching emphasis from its actual founders.

    Curriculum values are relative institutional weights, not free skill levels.
    Actual teaching quality remains bounded by the real instructors in the
    roster, so this records what the founders know well enough to organize
    around without granting a named-faction bonus.
    """
    rows = [row for row in people if isinstance(row, Mapping) and _living(row)]
    if not rows:
        raise ValueError("dynamic faction requires living founders")
    totals: dict[str, int] = {}
    domains = {
        "sword", "spear", "bow", "hidden_weapons", "unarmed",
        "stealth_scouting", "command", "medicine", "administration",
        "commerce", "crafting", "instruction", "qi", "qi_control",
    }
    for row in rows:
        martial = row.get("martial_skills", {}) if isinstance(row.get("martial_skills"), Mapping) else {}
        professional = row.get("professional_skills", {}) if isinstance(row.get("professional_skills"), Mapping) else {}
        for key in domains:
            if key == "qi":
                value = max(0, int(row.get("qi", 0)))
            elif key == "qi_control":
                value = max(0, int(row.get("qi_control", 0)))
            elif key in martial:
                value = max(0, int(martial.get(key, 0)))
            else:
                value = max(0, int(professional.get(key, 0)))
            totals[key] = totals.get(key, 0) + value
    averages = {key: value // len(rows) for key, value in totals.items() if value > 0}
    peak = max(averages.values(), default=0)
    # A lawful brotherhood/society can be founded by ordinary people who do not
    # yet have a teachable martial or professional curriculum.  Empty training
    # is an honest current institutional fact, not a reason to invent skill or
    # reject the institution.  Real instruction can appear later only through
    # actual members/instructors and the normal training lifecycle.
    if peak <= 0:
        return {}
    floor = max(1, peak // 5)
    return {
        key: max(1, min(100, value * 100 // peak))
        for key, value in sorted(averages.items()) if value >= floor
    }


def founder_recruitment_policy(people: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Derive conservative recruiting standards from the founder cohort."""
    rows = [row for row in people if isinstance(row, Mapping) and _living(row)]
    if not rows:
        raise ValueError("dynamic faction requires living founders")
    martial = []
    qi = []
    for row in rows:
        apt = row.get("aptitudes", {}) if isinstance(row.get("aptitudes"), Mapping) else {}
        martial.append(max(0, int(apt.get("martial", 100))))
        qi.append(max(0, int(apt.get("qi", 100))))
    avg_martial = sum(martial) // len(martial)
    avg_qi = sum(qi) // len(qi)
    return {
        "minimum_martial_aptitude": max(60, min(140, avg_martial * 2 // 3)),
        "minimum_qi_aptitude": max(50, min(130, avg_qi * 2 // 3)),
        "maximum_intake_per_season": max(2, min(12, len(rows) * 2)),
        "target_membership": max(12, len(rows) * 8),
    }


def default_founder_admission_policy() -> dict[str, Any]:
    return {"model": "open", "allowed_sexes": ["female", "male"], "minimum_entry_age": 8}


def default_founder_autonomy_policy() -> dict[str, int]:
    return {
        "recruitment_priority": 60, "training_priority": 70,
        "financial_caution": 65, "external_aggression": 35,
        "risk_tolerance": 45, "reserve_cash_months": 4,
    }


def default_dynamic_outlaw_profile(*, place_ref: str, site_type: str = "") -> dict[str, Any]:
    """Build an outlaw's operational identity from its real headquarters."""
    geography = json.loads((_ROOT / "game" / "data" / "martial-world" / "geography.json").read_text(encoding="utf-8"))
    routes = geography.get("routes", []) if isinstance(geography, Mapping) else []
    adjacent = sorted(
        str(row.get("id")) for row in routes
        if isinstance(row, Mapping) and isinstance(row.get("id"), str)
        and place_ref in {str(row.get("from") or ""), str(row.get("to") or "")}
    ) if isinstance(routes, list) else []
    subtype = "urban_gang" if str(site_type) == "guild_hall" else "road_band"
    return {
        "outlaw_subtype": subtype,
        "operating_routes": adjacent,
        "outlaw_policy": {
            "minimum_attack_advantage_milli": 1000,
            "loot_need_threshold": 55,
            "retreat_loss_threshold_pct": 30,
        },
    }


def _new_ref(kind: str, *, year: int, anchors: Sequence[str]) -> str:
    digest = hashlib.sha256((kind + "|" + str(year) + "|" + "|".join(sorted(anchors))).encode("utf-8")).hexdigest()[:12]
    return f"faction.dynamic_{kind}_{year}_{digest}"


def _proportional_request(inventory: Mapping[str, Any], *, numerator: int, denominator: int) -> dict[str, Any]:
    request: dict[str, Any] = {}
    for bucket in ("equipment", "raw_materials", "herbs", "medicines", "transport_capacity"):
        source = inventory.get(bucket, {}) if isinstance(inventory.get(bucket), Mapping) else {}
        moved = {
            str(key): max(0, int(value)) * numerator // max(1, denominator)
            for key, value in source.items()
            if max(0, int(value)) * numerator // max(1, denominator) > 0
        }
        if moved:
            request[bucket] = moved
    return request


def settle_autonomous_institutional_evolution(
    *, read_json: Callable[[str], Mapping[str, Any]], writes: dict[str, Any],
    schedule: Mapping[str, Any], events: Sequence[Mapping[str, Any]], year: int, at_iso: str,
    player_ref: str, site_rows: Mapping[str, Any], relations_state: Mapping[str, Any],
    family_state: Mapping[str, Any], independent_state: Mapping[str, Any], social_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply at most one conserved institutional transition on the annual boundary."""
    if not chunk_contains_final_owner(schedule, events, class_id="faction_annual"):
        return {
            "registry": copy.deepcopy(dict(writes.get(REGISTRY_PATH) or read_json(REGISTRY_PATH))),
            "relations": copy.deepcopy(dict(relations_state)),
            "family": copy.deepcopy(dict(family_state)),
            "independents": copy.deepcopy(dict(independent_state)),
            "reviews": [],
        }
    rules = _rules()
    if max(0, int(rules.get("annual_max_transitions", 1))) <= 0:
        return {
            "registry": copy.deepcopy(dict(writes.get(REGISTRY_PATH) or read_json(REGISTRY_PATH))),
            "relations": copy.deepcopy(dict(relations_state)), "family": copy.deepcopy(dict(family_state)),
            "independents": copy.deepcopy(dict(independent_state)), "reviews": [],
        }

    def get(path: str) -> Mapping[str, Any]:
        staged = writes.get(path)
        if isinstance(staged, Mapping):
            return staged
        return read_json(path)

    registry = copy.deepcopy(dict(get(REGISTRY_PATH)))
    relations = copy.deepcopy(dict(writes.get(_RELATIONS) or relations_state))
    family = copy.deepcopy(dict(writes.get(_FAMILY) or family_state))
    independents = copy.deepcopy(dict(writes.get(_INDEPENDENTS) or independent_state))
    commitments = derived_commitment_state(get)
    person_index = commitments.get("person_index", {}) if isinstance(commitments, Mapping) else {}
    blocked = {str(ref) for ref in person_index if isinstance(ref, str)}
    active = sorted(str(x) for x in registry.get("faction_refs", []) if isinstance(x, str))

    def bundle(fid: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        faction = hydrate_faction_state(get(faction_path(fid)))
        roster = hydrate_roster_state(get(roster_path(fid)), faction=faction)
        inventory = hydrate_inventory_state(get(inventory_path(fid)))
        return faction, roster, inventory

    def has_player_or_blocked(people: Sequence[Mapping[str, Any]]) -> bool:
        refs = {str(p.get("person_id")) for p in people if isinstance(p.get("person_id"), str) and _living(p)}
        return bool((player_ref and player_ref in refs) or refs & blocked)

    # 1. A genuinely insolvent one-person institution may dissolve. Property
    # remains with the dormant estate; only the living person becomes independent.
    dr = rules.get("dissolution", {}) if isinstance(rules.get("dissolution"), Mapping) else {}
    for fid in active:
        faction, roster, inventory = bundle(fid)
        people = [copy.deepcopy(dict(p)) for p in roster.get("people", []) if isinstance(p, Mapping)]
        living = [p for p in people if _living(p)]
        if has_player_or_blocked(people):
            continue
        if len(living) > max(0, int(dr.get("maximum_living_members", 1))):
            continue
        if not living:
            continue
        if max(0, int(faction.get("treasury_cash", 0))) > max(0, int(dr.get("maximum_treasury_cash", 0))):
            continue
        if max(0, int(inventory.get("food_ration_days", 0))) > max(0, int(dr.get("maximum_food_ration_days", 0))):
            continue
        if bool(dr.get("requires_no_enterprise", True)) and any(
            isinstance(v, Mapping) and int(v.get("level", 0) or 0) > 0
            for v in (faction.get("enterprises", {}) if isinstance(faction.get("enterprises"), Mapping) else {}).values()
        ):
            continue
        dissolution_refs = [str(p.get("person_id") or "") for p in living if isinstance(p.get("person_id"), str)]
        if faction_retirement_blockers(get, fid) or member_transition_blockers(
            get, dissolution_refs, source_faction_ref=fid,
        ):
            continue
        existing = independents.setdefault("people", [])
        if not isinstance(existing, list):
            raise ValueError("jianghu independent people invalid")
        existing_refs = {str(p.get("person_id")) for p in existing if isinstance(p, Mapping)}
        moved_refs: list[str] = []
        for person in living:
            ref = str(person.get("person_id") or "")
            if not ref or ref in existing_refs:
                continue
            out = copy.deepcopy(person)
            out.pop("membership_grade", None); out["standing_offices"] = []
            out["former_faction_ref"] = fid; out["independent_since"] = at_iso
            existing.append(compact_independent_person(out)); moved_refs.append(ref)
        if not moved_refs:
            continue
        moved = set(moved_refs)
        roster["people"] = [p for p in people if str(p.get("person_id") or "") not in moved]
        faction = mark_faction_extinct(reconcile_faction_population(faction, roster))
        registry = unregister_faction(registry, fid)
        relations = retire_faction_relations(relations, fid)
        family = reconcile_family_transition(family, moved_refs=moved_refs, source_faction_ref=fid, target_faction_ref=None)
        try:
            equipment_source = get(_EQUIPMENT)
        except FileNotFoundError:
            equipment_source = {"schema":"jianghu-equipment-ledger-1.0"}
        equipment_transition = detach_faction_policy_holders(
            equipment_source, source_faction_ref=fid, holder_refs=moved_refs,
        )
        writes[_EQUIPMENT] = equipment_transition["equipment_ledger_after"]
        writes[faction_path(fid)] = compact_faction_state(faction)
        writes[roster_path(fid)] = compact_roster_state(roster, faction=faction)
        writes[_INDEPENDENTS] = independents; writes[REGISTRY_PATH] = registry; writes[_RELATIONS] = relations; writes[_FAMILY] = family
        return {"registry":registry,"relations":relations,"family":family,"independents":independents,"reviews":[{"kind":"autonomous_faction_dissolution","faction_ref":fid,"member_refs":sorted(moved_refs),"equipment_policy_detached_count":equipment_transition["detached_policy_holder_count"]}]}

    # 2. Deeply trusted institutions can merge. The smaller institution is
    # absorbed into the larger one; exact people and every portable/estate asset move.
    mr = rules.get("merge", {}) if isinstance(rules.get("merge"), Mapping) else {}
    mutual_min = int(mr.get("mutual_faction_trust_minimum", 80))
    for i, a in enumerate(active):
        for b in active[i + 1:]:
            if min(_faction_trust(relations, a, b), _faction_trust(relations, b, a)) < mutual_min:
                continue
            af, ar, ai = bundle(a); bf, br, bi = bundle(b)
            ap = [copy.deepcopy(dict(p)) for p in ar.get("people", []) if isinstance(p, Mapping)]
            bp = [copy.deepcopy(dict(p)) for p in br.get("people", []) if isinstance(p, Mapping)]
            if has_player_or_blocked(ap) or has_player_or_blocked(bp):
                continue
            if faction_camp(a, af) and faction_camp(b, bf) and faction_camp(a, af) != faction_camp(b, bf):
                continue
            al = sum(1 for p in ap if _living(p)); bl = sum(1 for p in bp if _living(p))
            source_ref, target_ref = (a, b) if al <= bl else (b, a)
            source, source_roster, source_inv = (af, ar, ai) if source_ref == a else (bf, br, bi)
            target, target_roster, target_inv = (bf, br, bi) if source_ref == a else (af, ar, ai)
            source_people = [copy.deepcopy(dict(p)) for p in source_roster.get("people", []) if isinstance(p, Mapping)]
            moved = [p for p in source_people if _living(p)]
            moved_refs = [str(p.get("person_id")) for p in moved if isinstance(p.get("person_id"), str)]
            target_rows = [copy.deepcopy(dict(p)) for p in target_roster.get("people", []) if isinstance(p, Mapping)]
            existing_refs = {str(p.get("person_id")) for p in target_rows if isinstance(p.get("person_id"), str)}
            if any(ref in existing_refs for ref in moved_refs):
                continue
            if faction_retirement_blockers(get, source_ref) or member_transition_blockers(
                get, moved_refs, source_faction_ref=source_ref,
            ):
                continue
            source_inv, target_inv, moved_inventory = transfer_inventory(source_inv, target_inv, transfer_all=True)
            target_roster["people"] = target_rows + _institutional_people(moved, leader_ref="")
            moved_set = set(moved_refs)
            source_roster["people"] = [p for p in source_people if str(p.get("person_id") or "") not in moved_set]
            target["treasury_cash"] = max(0, int(target.get("treasury_cash", 0))) + max(0, int(source.get("treasury_cash", 0)))
            source["treasury_cash"] = 0
            source, target, moved_holdings = transfer_holdings(source, target)
            source = retire_organizational_scale(source)
            controlled = target.setdefault("controlled_estates", {})
            if not isinstance(controlled, dict):
                continue
            estates_to_move: dict[str, dict[str, Any]] = {}
            estate = primary_estate_projection(source, acquired_at=at_iso)
            if estate:
                site_ref, row = estate
                estates_to_move[site_ref] = row
            source_controlled = source.get("controlled_estates", {}) if isinstance(source.get("controlled_estates"), Mapping) else {}
            for site_ref, row in source_controlled.items():
                if isinstance(site_ref, str) and isinstance(row, Mapping):
                    estates_to_move[site_ref] = copy.deepcopy(dict(row))
            target_primary = str(target.get("local_site_ref") or "")
            if any(site_ref == target_primary or site_ref in controlled for site_ref in estates_to_move):
                continue
            for site_ref in sorted(estates_to_move):
                controlled[site_ref] = estates_to_move[site_ref]
            source_conditions = source.get("site_conditions", {}) if isinstance(source.get("site_conditions"), Mapping) else {}
            if source_conditions:
                target_conditions = target.setdefault("site_conditions", {})
                if not isinstance(target_conditions, dict) or any(str(site_ref) in target_conditions for site_ref in source_conditions):
                    continue
                for site_ref, condition in source_conditions.items():
                    if isinstance(site_ref, str) and isinstance(condition, Mapping):
                        target_conditions[site_ref] = copy.deepcopy(dict(condition))
            source["buildings"] = {}; source["infrastructure"] = {}; source["enterprises"] = {}; source.pop("controlled_estates", None); source.pop("site_conditions", None)
            source = mark_faction_extinct(reconcile_faction_population(source, source_roster))
            target = reconcile_faction_population(target, target_roster)
            try:
                equipment_source = get(_EQUIPMENT)
            except FileNotFoundError:
                equipment_source = {"schema":"jianghu-equipment-ledger-1.0"}
            try:
                property_transfer = transfer_faction_property_authority(
                    equipment_source, source_faction_ref=source_ref, target_faction_ref=target_ref,
                )
            except ValueError:
                continue
            registry = unregister_faction(registry, source_ref)
            registry["dormant_estate_refs"] = sorted(ref for ref in registry.get("dormant_estate_refs", []) if ref != source_ref)
            relations = retire_faction_relations(relations, source_ref)
            family = reconcile_family_transition(family, moved_refs=moved_refs, source_faction_ref=source_ref, target_faction_ref=target_ref)
            writes[faction_path(source_ref)] = compact_faction_state(source); writes[roster_path(source_ref)] = compact_roster_state(source_roster, faction=source); writes[inventory_path(source_ref)] = compact_inventory_state(source_inv)
            writes[faction_path(target_ref)] = compact_faction_state(target); writes[roster_path(target_ref)] = compact_roster_state(target_roster, faction=target); writes[inventory_path(target_ref)] = compact_inventory_state(target_inv)
            writes[_EQUIPMENT] = property_transfer["equipment_ledger_after"]
            writes[REGISTRY_PATH] = registry; writes[_RELATIONS] = relations; writes[_FAMILY] = family
            return {"registry":registry,"relations":relations,"family":family,"independents":independents,"reviews":[{"kind":"autonomous_faction_merger","source_faction_ref":source_ref,"target_faction_ref":target_ref,"moved_member_count":len(moved_refs),"inventory_transfer":moved_inventory,"holdings_transfer":moved_holdings}]}

    # 3. A real dissident clique may split only if it already occupies one of
    # the parent institution's secondary controlled estates. The estate itself
    # becomes the new faction's headquarters, so no land/site is invented.
    sr = rules.get("split", {}) if isinstance(rules.get("split"), Mapping) else {}
    for fid in active:
        faction, roster, inventory = bundle(fid)
        people = [copy.deepcopy(dict(p)) for p in roster.get("people", []) if isinstance(p, Mapping)]
        living = [p for p in people if _living(p)]
        if len(living) < max(2, int(sr.get("minimum_living_members", 6))) or has_player_or_blocked(people):
            continue
        leader = _leader_ref(living)
        controlled = faction.get("controlled_estates", {}) if isinstance(faction.get("controlled_estates"), Mapping) else {}
        if not leader or not controlled:
            continue
        by_ref = {str(p.get("person_id")): p for p in living if isinstance(p.get("person_id"), str)}
        max_leader_trust = int(sr.get("splinter_leader_trust_to_current_leader_maximum", -50))
        follower_min = int(sr.get("follower_trust_to_splinter_leader_minimum", 40))
        follower_leader_max = int(sr.get("follower_trust_to_current_leader_maximum", -30))
        min_departing = max(2, int(sr.get("minimum_departing_members", 2)))
        for site_ref in sorted(str(x) for x in controlled):
            estate = controlled.get(site_ref)
            if not isinstance(estate, Mapping):
                continue
            onsite = [p for p in living if str(p.get("location_ref") or "") == site_ref and str(p.get("person_id") or "") != leader]
            for candidate in sorted(onsite, key=lambda p: str(p.get("person_id") or "")):
                candidate_ref = str(candidate.get("person_id") or "")
                if _social_trust(social_state, candidate_ref, leader) > max_leader_trust:
                    continue
                clique = [candidate]
                for p in onsite:
                    ref = str(p.get("person_id") or "")
                    if ref == candidate_ref:
                        continue
                    if _social_trust(social_state, ref, candidate_ref) >= follower_min and _social_trust(social_state, ref, leader) <= follower_leader_max:
                        clique.append(p)
                if len(clique) < min_departing or len(living) - len(clique) < 2:
                    continue
                moved_refs = sorted(str(p.get("person_id")) for p in clique if isinstance(p.get("person_id"), str))
                new_ref = _new_ref("split", year=year, anchors=[fid, candidate_ref, site_ref])
                if new_ref in active:
                    continue
                if member_transition_blockers(
                    get, moved_refs, source_faction_ref=fid, moving_site_refs=[site_ref],
                ):
                    continue
                place_ref = str(estate.get("headquarters_place_ref") or (site_rows.get(site_ref) or {}).get("parent_place_ref") or "")
                if not place_ref:
                    continue
                numerator, denominator = len(clique), len(living)
                cash = max(0, int(faction.get("treasury_cash", 0))) * numerator // denominator
                food = max(0, int(inventory.get("food_ration_days", 0))) * numerator // denominator
                new_inventory = {"schema":"jianghu-faction-inventory-1.0","faction_ref":new_ref,"food_ration_days":0}
                inventory, new_inventory, _moved = transfer_inventory(
                    inventory, new_inventory, food_ration_days=food,
                    requested=_proportional_request(inventory, numerator=numerator, denominator=denominator),
                )
                faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) - cash
                new_faction = {
                    "schema":"jianghu-faction-state-1.0","faction_id":new_ref,
                    "name":f"{str(candidate.get('name') or candidate_ref).split()[0]} Fellowship",
                    "type":resolved_faction_type(faction) or "society", "headquarters":place_ref,
                    "local_site_ref":site_ref,"treasury_cash":cash,
                    "buildings":copy.deepcopy(dict(estate.get("buildings", {}))) if isinstance(estate.get("buildings"), Mapping) else {},
                    "infrastructure":copy.deepcopy(dict(estate.get("infrastructure", {}))) if isinstance(estate.get("infrastructure"), Mapping) else {},
                    "enterprises":copy.deepcopy(dict(estate.get("enterprises", {}))) if isinstance(estate.get("enterprises"), Mapping) else {},
                    "training_epoch":{"started_at":at_iso,"settled_through":at_iso,"intensity_milli":1000},
                }
                for inherited_key in ("training", "doctrine", "recruitment_policy", "autonomy_policy"):
                    inherited = faction.get(inherited_key)
                    if isinstance(inherited, Mapping) and inherited:
                        new_faction[inherited_key] = copy.deepcopy(dict(inherited))
                new_faction["admission_policy"] = faction_admission_policy(fid, faction)
                if resolved_faction_type(new_faction) == "outlaw_faction":
                    profile = default_dynamic_outlaw_profile(
                        place_ref=place_ref,
                        site_type=str((site_rows.get(site_ref) or {}).get("site_type") or "") if isinstance(site_rows, Mapping) else "",
                    )
                    if isinstance(faction.get("outlaw_subtype"), str) and faction.get("outlaw_subtype"):
                        profile["outlaw_subtype"] = str(faction["outlaw_subtype"])
                    if isinstance(faction.get("outlaw_policy"), Mapping):
                        profile["outlaw_policy"] = copy.deepcopy(dict(faction["outlaw_policy"]))
                    new_faction.update(profile)
                camp = faction_camp(fid, faction)
                if camp: new_faction["jianghu_camp"] = camp
                tenure = faction.get("membership_tenure")
                if isinstance(tenure, str) and tenure: new_faction["membership_tenure"] = tenure
                moved_set = set(moved_refs)
                new_roster = {"schema":"jianghu-person-lite-roster-1.0","faction_ref":new_ref,"people":_institutional_people(clique, leader_ref=candidate_ref)}
                roster["people"] = [p for p in people if str(p.get("person_id") or "") not in moved_set]
                source_controlled = copy.deepcopy(dict(controlled)); source_controlled.pop(site_ref, None)
                if source_controlled: faction["controlled_estates"] = source_controlled
                else: faction.pop("controlled_estates", None)
                faction = reconcile_faction_population(faction, roster); new_faction = reconcile_faction_population(new_faction, new_roster)
                registry = register_materialized_faction_bundle(registry=registry, faction=new_faction, roster=new_roster, inventory=new_inventory)
                family = reconcile_family_transition(family, moved_refs=moved_refs, source_faction_ref=fid, target_faction_ref=new_ref)
                try:
                    equipment_source = get(_EQUIPMENT)
                except FileNotFoundError:
                    equipment_source = {"schema":"jianghu-equipment-ledger-1.0"}
                equipment_transition = detach_faction_policy_holders(
                    equipment_source, source_faction_ref=fid, holder_refs=moved_refs,
                )
                writes[_EQUIPMENT] = equipment_transition["equipment_ledger_after"]
                writes[faction_path(fid)] = compact_faction_state(faction); writes[roster_path(fid)] = compact_roster_state(roster, faction=faction); writes[inventory_path(fid)] = compact_inventory_state(inventory)
                writes[faction_path(new_ref)] = compact_faction_state(new_faction); writes[roster_path(new_ref)] = compact_roster_state(new_roster, faction=new_faction); writes[inventory_path(new_ref)] = compact_inventory_state(new_inventory)
                writes[REGISTRY_PATH] = registry; writes[_FAMILY] = family
                return {"registry":registry,"relations":relations,"family":family,"independents":independents,"reviews":[{"kind":"autonomous_faction_split","source_faction_ref":fid,"new_faction_ref":new_ref,"member_refs":moved_refs,"estate_site_ref":site_ref,"equipment_policy_detached_count":equipment_transition["detached_policy_holder_count"]}]}

    # 4. Independent exact people can found a small institution only when a
    # co-located clique already exists at a rentable public guild/caravan site.
    fr = rules.get("foundation", {}) if isinstance(rules.get("foundation"), Mapping) else {}
    rows = independents.get("people", []) if isinstance(independents.get("people"), list) else []
    hydrated = [hydrate_independent_person(p) for p in rows if isinstance(p, Mapping)]
    eligible = [p for p in hydrated if _living(p) and str(p.get("person_id") or "") not in blocked and str(p.get("person_id") or "") != player_ref]
    by_site: dict[str, list[dict[str, Any]]] = {}
    allowed_types = {str(x) for x in fr.get("eligible_site_types", []) if isinstance(x, str)}
    active_primary_sites: set[str] = set()
    for fid in active:
        try:
            active_primary_sites.add(str(bundle(fid)[0].get("local_site_ref") or ""))
        except (FileNotFoundError, ValueError):
            continue
    for p in eligible:
        site_ref = str(p.get("location_ref") or "")
        site = site_rows.get(site_ref) if isinstance(site_rows, Mapping) else None
        if (
            not isinstance(site, Mapping)
            or str(site.get("site_type") or "") not in allowed_types
            or str(site.get("public_access") or "public") != "public"
            or site_ref in active_primary_sites
        ):
            continue
        try:
            if active_site_controller(get, site_ref):
                continue
        except ValueError:
            continue
        by_site.setdefault(site_ref, []).append(p)
    min_members = max(2, int(fr.get("minimum_members", 2))); trust_min = int(fr.get("mutual_trust_minimum", 60)); cash_per = max(1, int(fr.get("startup_cash_per_member", 500)))
    for site_ref in sorted(by_site):
        group = by_site[site_ref]
        leaders = sorted(group, key=lambda p: (-int((p.get("aptitudes", {}) if isinstance(p.get("aptitudes"), Mapping) else {}).get("leadership", 0)), str(p.get("person_id") or "")))
        for leader in leaders:
            leader_ref = str(leader.get("person_id") or "")
            clique = [leader]
            for p in group:
                ref = str(p.get("person_id") or "")
                if ref == leader_ref: continue
                if _social_trust(social_state, ref, leader_ref) >= trust_min and _social_trust(social_state, leader_ref, ref) >= trust_min:
                    clique.append(p)
            if len(clique) < min_members:
                continue
            clique = sorted(clique, key=lambda p: str(p.get("person_id") or ""))
            total_cash = sum(max(0, int(p.get("personal_cash", 0))) for p in clique)
            startup = cash_per * len(clique)
            if total_cash < startup:
                continue
            member_refs = [str(p.get("person_id")) for p in clique if isinstance(p.get("person_id"), str)]
            new_ref = _new_ref("founded", year=year, anchors=[site_ref, *member_refs])
            if new_ref in active:
                continue
            site = site_rows.get(site_ref, {}) if isinstance(site_rows, Mapping) else {}
            place_ref = str(site.get("parent_place_ref") or "") if isinstance(site, Mapping) else ""
            if not place_ref:
                continue

            # Do not mutate the shared hydrated independent-person projection until
            # every destination and identity needed by the foundation has been
            # validated. A rejected candidate must cost its prospective founders
            # exactly zero silver.
            funded_clique = [copy.deepcopy(dict(p)) for p in clique]
            remaining = startup
            for p in funded_clique:
                take = min(max(0, int(p.get("personal_cash", 0))), remaining)
                p["personal_cash"] = max(0, int(p.get("personal_cash", 0))) - take
                remaining -= take
                if remaining <= 0:
                    break
            if remaining:
                continue
            new_faction = {
                "schema":"jianghu-faction-state-1.0","faction_id":new_ref,
                "name":f"{str(leader.get('name') or leader_ref).split()[0]} Fellowship",
                "type":"brotherhood_society" if str(fr.get("faction_type") or "society") == "society" else str(fr.get("faction_type")),"headquarters":place_ref,
                "local_site_ref":site_ref,"treasury_cash":startup,"buildings":{},"enterprises":{},
                "training":founder_curriculum(funded_clique),
                "recruitment_policy":founder_recruitment_policy(funded_clique),
                "admission_policy":default_founder_admission_policy(),
                "autonomy_policy":default_founder_autonomy_policy(),
                "training_epoch":{"started_at":at_iso,"settled_through":at_iso,"intensity_milli":1000},
                "jianghu_camp":str(fr.get("jianghu_camp") or "independent"),
            }
            if resolved_faction_type(new_faction) == "outlaw_faction":
                new_faction.update(default_dynamic_outlaw_profile(place_ref=place_ref, site_type=str(site.get("site_type") or "")))
            new_roster = {"schema":"jianghu-person-lite-roster-1.0","faction_ref":new_ref,"people":_institutional_people(funded_clique, leader_ref=leader_ref)}
            new_faction = reconcile_faction_population(new_faction, new_roster)
            new_inventory = {"schema":"jianghu-faction-inventory-1.0","faction_ref":new_ref,"food_ration_days":0}
            registry = register_materialized_faction_bundle(registry=registry, faction=new_faction, roster=new_roster, inventory=new_inventory)
            moved_set = set(member_refs)
            independents["people"] = [
                compact_independent_person(p) for p in hydrated
                if str(p.get("person_id")) not in moved_set
            ]
            family = reconcile_family_transition(family, moved_refs=member_refs, source_faction_ref="", target_faction_ref=new_ref)
            writes[_INDEPENDENTS] = independents; writes[faction_path(new_ref)] = compact_faction_state(new_faction); writes[roster_path(new_ref)] = compact_roster_state(new_roster, faction=new_faction); writes[inventory_path(new_ref)] = compact_inventory_state(new_inventory)
            writes[REGISTRY_PATH] = registry; writes[_FAMILY] = family
            return {"registry":registry,"relations":relations,"family":family,"independents":independents,"reviews":[{"kind":"autonomous_faction_foundation","faction_ref":new_ref,"member_refs":sorted(member_refs),"startup_cash":startup,"headquarters_site_ref":site_ref}]}

    return {"registry":registry,"relations":relations,"family":family,"independents":independents,"reviews":[]}


__all__ = [
    "default_dynamic_outlaw_profile", "default_founder_admission_policy",
    "default_founder_autonomy_policy", "founder_curriculum",
    "founder_recruitment_policy", "settle_autonomous_institutional_evolution",
]
