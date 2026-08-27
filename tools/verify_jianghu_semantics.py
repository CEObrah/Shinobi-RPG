#!/usr/bin/env python3
"""Authoritative semantic release gate for the current Jianghu campaign.

This validator checks gameplay meaning that structural JSON/schema validation cannot
establish. It reads exact IDs and current canonical authorities rather than display
names or derived projections.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
from shinobi_runtime.martial_world.commitments import derived_commitment_state
from shinobi_runtime.martial_world.route_activity import route_controlling_refs, route_potential_controller_refs
from shinobi_runtime.martial_world.equipment_state import effective_person_loadout
from shinobi_runtime.martial_world.faction_state import (
    faction_admission_policy, hydrate_faction_state, resolved_faction_type,
)
from shinobi_runtime.martial_world.faction_politics import conflict_stage
CANONICAL_GRADES = {"probationary", "junior", "full", "senior", "elite", "elder"}
MARTIAL_GRADES = CANONICAL_GRADES
EXPECTED_TYPES = {
    "martial_house": 24,
    "sect": 60,
    "martial_school": 35,
    "escort_agency": 30,
    "brotherhood_society": 16,
    "outlaw_faction": 70,
    "contract_hall": 5,
}
REQUIRED_IDENTITY_FIELDS = {
    "name", "faction_type", "headquarters", "admission_policy",
    "membership_ladder", "office_structure_ref", "leader_title",
    "martial_specializations", "martial_neglects", "qi_emphasis",
    "weapons", "training_curriculum", "doctrine", "economic_niches",
    "public_reputation", "allies", "rivals",
    "operating_routes", "display_titles",
}
FORBIDDEN_EQUIPMENT_TOKENS = (
    "shield", "armor", "armour", "lamellar", "brigandine", "mail_armor",
    "padded_armor", "rawhide_armor", "great_war_bow", "war_arrow",
)
FORBIDDEN_ONTOLOGY_RE = re.compile(
    r"(?<![a-z0-9])(?:shield|armor|armour|lamellar|brigandine|mail_armor|"
    r"padded_armor|rawhide_armor|great_war_bow|war_arrow)(?![a-z0-9])"
)
BANNED_HOT_KEYS = {
    "schema_version", "gameplay_version", "rules_version", "migration_version",
    "baseline_version", "state_version", "migration_reason", "repair_proof",
    "cleanup_receipt", "old_baseline", "previous_bug_description",
    "version_history", "revision_history", "migration_history",
    "repair_history", "training_review_count", "scheduler_runs",
    "autonomous_action_attempts", "decision_history", "mutation_history",
}
STATIC_FACTION_KEYS = {
    "admission_policy", "membership_ladder", "office_structure_ref",
    "leader_title", "martial_specializations", "martial_neglects",
    "qi_emphasis", "weapons", "training_curriculum", "doctrine",
    "economic_niches", "public_reputation", "allies",
    "rivals", "operating_routes", "display_titles", "apothecary_policy",
}
TANG_IDENTITY_TARGETS = {
    "pc_wei_tang": {"name": "Tang Wei", "birth_year": 44},
    "char.zhu": {"name": "Tang Zhu", "birth_year": 24},
    "char.ling": {"name": "Tang Ling", "birth_year": 27},
    "char.kai": {"name": "Tang Kai", "birth_year": 55},
}


def tang_identity_errors(person_id: str, person: Mapping[str, Any] | None) -> list[str]:
    """Validate only permanent authored identity, never mutable progression.

    The exact revision-85 Tang profiles are separately regression-tested against
    the canonical save.  A semantic verifier must also accept lawful future
    states where training, cultivation, attributes, offices, health, faction
    membership, and age have changed through gameplay.
    """
    target = TANG_IDENTITY_TARGETS[person_id]
    if not isinstance(person, Mapping):
        return [f"missing authored Tang person {person_id}"]
    errors: list[str] = []
    if person.get("name") != target["name"]:
        errors.append(f"{person_id}: authored name changed to {person.get('name')!r}")
    if int(person.get("birth_year", -10**9)) != int(target["birth_year"]):
        errors.append(f"{person_id}: authored birth year changed")
    aptitudes = person.get("aptitudes") or {}
    if not isinstance(aptitudes, Mapping) or set(aptitudes.values()) != {200}:
        errors.append(f"{person_id}: extraordinary 200 aptitudes were not preserved")
    if "shield" in json.dumps(person, ensure_ascii=False).lower():
        errors.append(f"{person_id}: deleted shield identity survives")
    return errors



def load(rel: str) -> Any:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def walk(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, path + (str(index),))


def parse_year(timestamp: str) -> int:
    match = re.search(r"(\d{4})-", str(timestamp))
    if not match:
        raise ValueError(f"cannot parse year from {timestamp!r}")
    return int(match.group(1))


def equipment_authority_errors(
    *, ledger: Mapping[str, Any], loadout_policies: Mapping[str, Any],
    exact_people: Mapping[str, Mapping[str, Any]], living_exact_people: set[str],
    active_faction_refs: set[str], dormant_faction_refs: set[str],
    people_faction: Mapping[str, str] | None = None,
) -> tuple[list[str], dict[str, int]]:
    """Validate physical custody separately from current legal title."""
    errors: list[str] = []
    current_institutions = set(active_faction_refs) | set(dormant_faction_refs)
    assignments = ledger.get("policy_assignments", {}) if isinstance(ledger, Mapping) else {}
    if not isinstance(assignments, Mapping):
        return ["equipment ledger policy_assignments is not an object"], {}
    policy_holder_count = 0
    for policy_ref, raw_refs in assignments.items():
        policy = loadout_policies.get(str(policy_ref)) if isinstance(loadout_policies, Mapping) else None
        if not isinstance(policy, Mapping):
            errors.append(f"equipment policy assignment references unknown policy {policy_ref}"); continue
        owner_ref = str(policy.get("faction_ref") or "")
        if owner_ref not in current_institutions:
            errors.append(f"equipment policy {policy_ref}: legal faction owner is not active/dormant: {owner_ref!r}")
        if not isinstance(raw_refs, list):
            errors.append(f"equipment policy {policy_ref}: holder list is not an array"); continue
        for holder_ref in raw_refs:
            ref = str(holder_ref or ""); policy_holder_count += 1
            if ref not in exact_people:
                errors.append(f"equipment policy {policy_ref}: unknown physical holder {ref!r}"); continue
            if ref not in living_exact_people:
                errors.append(f"equipment policy {policy_ref}: dead exact person remains current policy holder {ref!r}")
            if people_faction is not None and str(people_faction.get(ref) or "") != owner_ref:
                errors.append(f"equipment policy {policy_ref}: holder {ref!r} is not a current member of issuing faction {owner_ref!r}")
    loadouts = ledger.get("person_loadouts", {}) if isinstance(ledger, Mapping) else {}
    if not isinstance(loadouts, Mapping):
        errors.append("equipment ledger person_loadouts is not an object"); loadouts = {}
    for holder_ref in loadouts:
        if str(holder_ref) not in exact_people:
            errors.append(f"equipment loadout has unknown physical holder {holder_ref!r}")
    provenance = ledger.get("provenance_exceptions", {}) if isinstance(ledger, Mapping) else {}
    if not isinstance(provenance, Mapping):
        errors.append("equipment ledger provenance_exceptions is not an object"); provenance = {}
    claim_count = 0
    for holder_ref, raw_items in provenance.items():
        holder = str(holder_ref or "")
        if holder not in exact_people:
            errors.append(f"equipment provenance has unknown physical holder {holder!r}")
        if not isinstance(raw_items, Mapping):
            errors.append(f"equipment provenance holder {holder!r} is not an object"); continue
        for item_ref, raw_claim in raw_items.items():
            claim_count += 1
            if not isinstance(raw_claim, Mapping):
                errors.append(f"equipment provenance {holder}/{item_ref} is not an object"); continue
            owner_ref = str(raw_claim.get("owner_ref") or ""); quantity = int(raw_claim.get("quantity", 0))
            if quantity <= 0:
                errors.append(f"equipment provenance {holder}/{item_ref} has nonpositive quantity")
            try:
                held_quantity = max(0, int(
                    effective_person_loadout(ledger, holder).get("items", {}).get(str(item_ref), 0)
                ))
            except (TypeError, ValueError):
                held_quantity = 0
            if quantity > held_quantity:
                errors.append(
                    f"equipment provenance {holder}/{item_ref}: legal claim quantity {quantity} "
                    f"exceeds physical held quantity {held_quantity}"
                )
            if owner_ref in exact_people:
                if owner_ref not in living_exact_people:
                    errors.append(f"equipment provenance {holder}/{item_ref}: dead exact person remains legal owner {owner_ref}")
            elif owner_ref not in current_institutions:
                errors.append(f"equipment provenance {holder}/{item_ref}: legal owner is unknown/retired {owner_ref!r}")
    demands = ledger.get("recovery_demands", {}) if isinstance(ledger, Mapping) else {}
    if not isinstance(demands, Mapping):
        errors.append("equipment ledger recovery_demands is not an object"); demands = {}
    demand_count = 0
    for demand_ref, raw_demand in demands.items():
        demand_count += 1
        if not isinstance(raw_demand, Mapping):
            errors.append(f"equipment recovery demand {demand_ref} is not an object"); continue
        owner_ref = str(raw_demand.get("owner_ref") or ""); holder_ref = str(raw_demand.get("holder_ref") or ""); item_ref = str(raw_demand.get("item_ref") or ""); quantity = int(raw_demand.get("quantity", 0))
        if holder_ref not in exact_people:
            errors.append(f"equipment recovery demand {demand_ref}: unknown physical holder {holder_ref!r}")
        if owner_ref in exact_people:
            if owner_ref not in living_exact_people:
                errors.append(f"equipment recovery demand {demand_ref}: dead exact person remains legal owner {owner_ref}")
        elif owner_ref not in current_institutions:
            errors.append(f"equipment recovery demand {demand_ref}: legal owner is unknown/retired {owner_ref!r}")
        if quantity <= 0:
            errors.append(f"equipment recovery demand {demand_ref}: nonpositive quantity")
        claim_rows = provenance.get(holder_ref, {}) if isinstance(provenance, Mapping) else {}
        claim = claim_rows.get(item_ref) if isinstance(claim_rows, Mapping) else None
        if not isinstance(claim, Mapping):
            errors.append(f"equipment recovery demand {demand_ref}: no current provenance claim")
        else:
            claim_owner = str(claim.get("owner_ref") or ""); claim_quantity = int(claim.get("quantity", 0))
            if claim_owner != owner_ref or claim_quantity < quantity:
                errors.append(f"equipment recovery demand {demand_ref}: demand exceeds or disagrees with current provenance claim")
    return errors, {
        "policy_holders": policy_holder_count, "personal_loadout_holders": len(loadouts),
        "provenance_claims": claim_count, "recovery_demands": demand_count,
    }



def deployment_equipment_authority_errors(
    *, deployments: Mapping[str, Any], exact_people: set[str], living_exact_people: set[str],
    route_operations: Mapping[str, Any] | None = None,
) -> list[str]:
    """Validate finite operation gear against the current physical operation owner.

    ``issued_equipment`` is a return obligation, not permanent title. A live
    issue holder must therefore still be a living exact participant of that same
    operation. Death/capture/separation must first materialize physical custody
    and legal provenance, then detach the finite return obligation.
    """
    errors: list[str] = []
    terminal = {"completed", "returned", "failed", "cancelled", "disbanded", "closed"}
    rows = deployments.get("deployments", {}) if isinstance(deployments, Mapping) else {}
    if not isinstance(rows, Mapping):
        return ["deployments owner is not an object"]
    movements = route_operations.get("movements", {}) if isinstance(route_operations, Mapping) else {}
    if not isinstance(movements, Mapping):
        movements = {}
    for dref, raw in rows.items():
        if not isinstance(raw, Mapping):
            continue
        issued = raw.get("issued_equipment", {})
        if issued in (None, {}):
            continue
        if not isinstance(issued, Mapping):
            errors.append(f"{dref}: issued_equipment is not an object"); continue
        participants = {
            str(ref) for ref in raw.get("participant_refs", [])
            if isinstance(ref, str) and ref
        } if isinstance(raw.get("participant_refs"), list) else set()
        live_owner = str(raw.get("status") or "active") not in terminal
        issue_refs = {str(ref) for ref in issued if isinstance(ref, str) and ref}
        for ref in sorted(issue_refs):
            if ref not in exact_people:
                errors.append(f"{dref}: operation issue references missing exact person {ref}")
            elif live_owner and ref not in living_exact_people:
                errors.append(f"{dref}: live operation issue references dead exact person {ref}")
            if live_owner and ref not in participants:
                errors.append(f"{dref}: live operation issue holder is not a current participant {ref}")
        for baseline_key in ("issued_equipment_baseline", "issued_equipment_claim_baseline"):
            baseline = raw.get(baseline_key, {})
            if baseline in (None, {}):
                continue
            if not isinstance(baseline, Mapping):
                errors.append(f"{dref}: {baseline_key} is not an object"); continue
            baseline_refs = {str(ref) for ref in baseline if isinstance(ref, str) and ref}
            extras = sorted(baseline_refs - issue_refs)
            if extras:
                errors.append(f"{dref}: {baseline_key} has holders outside issued_equipment: {extras}")
        movement_ref = str(raw.get("physical_movement_ref") or "")
        if live_owner and movement_ref:
            movement = movements.get(movement_ref)
            if not isinstance(movement, Mapping):
                errors.append(f"{dref}: live operation issue owner references missing physical movement {movement_ref}")
            else:
                movement_participants = {
                    str(ref) for ref in movement.get("participant_refs", [])
                    if isinstance(ref, str) and ref
                } if isinstance(movement.get("participant_refs"), list) else set()
                for ref in sorted(issue_refs - movement_participants):
                    errors.append(
                        f"{dref}: live operation issue holder is not on linked physical movement {ref}"
                    )
    return errors



def route_controller_authority_errors(*, route_operations: Mapping[str, Any]) -> list[str]:
    """Reject live movements whose physical participants cannot lawfully control them.

    Carried people are physical occupants, never automatic controllers. A route
    with surviving non-carried potential controllers may wait for recovery or
    logistics, but a route containing only captives/rescued/protected people must
    already be terminal/party-extinguished so purpose-specific salvage or
    repatriation can resolve it.
    """
    errors: list[str] = []
    movements = route_operations.get("movements", {}) if isinstance(route_operations, Mapping) else {}
    if not isinstance(movements, Mapping):
        return ["route operations movements owner is not an object"]
    terminal = {"completed", "closed", "failed", "settled", "cancelled", "party_extinguished"}
    for movement_ref, raw in movements.items():
        if not isinstance(raw, Mapping):
            continue
        status = str(raw.get("status") or "active")
        if status in terminal:
            continue
        participants = {
            str(ref) for ref in raw.get("participant_refs", []) if isinstance(ref, str) and ref
        } if isinstance(raw.get("participant_refs"), list) else set()
        if not participants:
            continue
        carried = {
            str(ref)
            for key in ("protected_person_refs", "captive_refs", "rescued_refs")
            for ref in (raw.get(key, []) if isinstance(raw.get(key), list) else [])
            if isinstance(ref, str) and ref
        }
        potential = route_potential_controller_refs(raw)
        controllers = route_controlling_refs(raw)
        if carried.intersection(participants) and not potential:
            errors.append(f"{movement_ref}: live carried-only movement has no potential controller")
            continue
        if status != "awaiting_return_logistics" and carried.intersection(participants) and not controllers:
            errors.append(f"{movement_ref}: live carried movement has no current controller")
        if status == "awaiting_return_logistics" and not potential:
            errors.append(f"{movement_ref}: awaiting-return movement has no potential controller")
    return errors

def strategic_operation_intent_errors(
    *, deployments: Mapping[str, Any], factions: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """Reject live strategic operations whose saved objective contradicts combat intent."""
    errors: list[str] = []
    nonlethal = {"robbery", "kidnapping", "cargo_seizure", "extortion", "cargo_diversion"}
    terminal = {"completed", "returned", "failed", "cancelled", "disbanded", "closed"}
    rows = deployments.get("deployments", {}) if isinstance(deployments, Mapping) else {}
    if not isinstance(rows, Mapping):
        return ["deployments owner is not an object"]
    for op_ref, raw in rows.items():
        if not isinstance(raw, Mapping) or str(raw.get("status") or "active") in terminal:
            continue
        kind = str(raw.get("operation_kind") or "")
        intent = str(raw.get("operation_intent") or "")
        targeting = str(raw.get("targeting_intent") or "")
        if intent in nonlethal and targeting == "lethal":
            errors.append(f"{op_ref}: nonlethal operation intent {intent} explicitly targets lethal combat")
        source_ref = str(raw.get("faction_ref") or "")
        source = factions.get(source_ref) if isinstance(factions, Mapping) else None
        if kind == "faction_raid" and isinstance(source, Mapping) and resolved_faction_type(source) == "outlaw_faction":
            if intent not in nonlethal:
                errors.append(f"{op_ref}: live outlaw faction_raid lacks a lawful nonlethal raid objective")
    return errors


def institutional_membership_obligation_errors(
    *, contracts: Mapping[str, Any], tournaments: Mapping[str, Any],
    living_exact_people: set[str], people_faction: Mapping[str, str],
    faction_refs: set[str],
) -> list[str]:
    """Reject live institutional obligations whose exact people changed affiliation."""
    errors: list[str] = []

    active_contracts = contracts.get("active", {}) if isinstance(contracts, Mapping) else {}
    if isinstance(active_contracts, Mapping):
        for cref, raw in active_contracts.items():
            if not isinstance(raw, Mapping):
                continue
            status = str(raw.get("status") or "")
            if status not in {"accepted", "in_progress", "objective_resolved"}:
                continue
            beneficiary = str(raw.get("beneficiary_ref") or "")
            if beneficiary not in faction_refs:
                continue
            participants = raw.get("participants", [])
            if not isinstance(participants, list):
                continue
            for ref in [str(x) for x in participants if isinstance(x, str) and x]:
                if ref in living_exact_people and str(people_faction.get(ref) or "") != beneficiary:
                    errors.append(
                        f"{cref}: live contract principal {ref} is no longer a member of beneficiary {beneficiary}"
                    )

    tournament_rows = tournaments.get("tournaments", {}) if isinstance(tournaments, Mapping) else {}
    if isinstance(tournament_rows, Mapping):
        for tref, tournament in tournament_rows.items():
            if not isinstance(tournament, Mapping) or str(tournament.get("status") or "") == "completed":
                continue
            registrations = tournament.get("registrations", [])
            if isinstance(registrations, list):
                for registration in registrations:
                    if not isinstance(registration, Mapping):
                        continue
                    ref = str(registration.get("entrant_ref") or "")
                    sponsor = str(registration.get("faction_ref") or "")
                    if ref and sponsor and ref in living_exact_people and str(people_faction.get(ref) or "") != sponsor:
                        errors.append(
                            f"{tref}: living tournament entrant {ref} is no longer a member of sponsor {sponsor}"
                        )
            delegations = tournament.get("delegations", {})
            if isinstance(delegations, Mapping):
                for key, delegation in delegations.items():
                    if not isinstance(delegation, Mapping):
                        continue
                    sponsor = str(delegation.get("faction_ref") or key or "")
                    if not sponsor:
                        continue
                    for role_key in ("entrant_refs", "spectator_refs", "leader_refs", "senior_refs"):
                        values = delegation.get(role_key, [])
                        if not isinstance(values, list):
                            continue
                        for ref in [str(x) for x in values if isinstance(x, str) and x]:
                            if ref in living_exact_people and str(people_faction.get(ref) or "") != sponsor:
                                errors.append(
                                    f"{tref}: living tournament delegate {ref} in {role_key} is no longer a member of sponsor {sponsor}"
                                )
    return errors

def social_causality_errors(
    *, social: Mapping[str, Any], exact_people: Mapping[str, Mapping[str, Any]],
    living_exact_people: set[str], current_faction_refs: set[str],
) -> tuple[list[str], dict[str, int]]:
    """Validate sparse current Jianghu social authorities and their bounds."""
    errors: list[str] = []
    metrics = {"obligations": 0, "beliefs": 0, "vows": 0, "martial_familiarity": 0}

    obligations = social.get("obligations", {}) if isinstance(social, Mapping) else {}
    if obligations not in (None, {}) and not isinstance(obligations, Mapping):
        errors.append("social obligations owner is not an object")
        obligations = {}
    per_actor: collections.Counter[str] = collections.Counter()
    for ref, raw in obligations.items() if isinstance(obligations, Mapping) else ():
        metrics["obligations"] += 1
        if not isinstance(raw, Mapping):
            errors.append(f"{ref}: social obligation row is not an object"); continue
        actor = str(raw.get("actor_ref") or ""); counterparty = str(raw.get("counterparty_ref") or "")
        if actor not in exact_people or counterparty not in exact_people or actor == counterparty:
            errors.append(f"{ref}: social obligation has unresolved/distinct-person refs")
        if actor and actor not in living_exact_people:
            errors.append(f"{ref}: dead actor retains current personal obligation")
        if counterparty and counterparty not in living_exact_people:
            errors.append(f"{ref}: dead counterparty retains current personal obligation")
        per_actor[actor] += 1
    for actor, count in per_actor.items():
        if actor and count > 32:
            errors.append(f"{actor}: current personal obligations exceed sparse bound ({count} > 32)")

    beliefs = social.get("beliefs", {}) if isinstance(social, Mapping) else {}
    if beliefs not in (None, {}) and not isinstance(beliefs, Mapping):
        errors.append("social beliefs owner is not an object")
        beliefs = {}
    per_observer: collections.Counter[str] = collections.Counter()
    for ref, raw in beliefs.items() if isinstance(beliefs, Mapping) else ():
        metrics["beliefs"] += 1
        if not isinstance(raw, Mapping):
            errors.append(f"{ref}: social belief row is not an object"); continue
        observer = str(raw.get("observer_ref") or ""); subject = str(raw.get("subject_ref") or "")
        if observer not in living_exact_people:
            errors.append(f"{ref}: current belief observer is missing/dead")
        if str(raw.get("claim_kind") or "") == "property_crime_responsibility" and subject not in exact_people:
            errors.append(f"{ref}: property-crime belief subject is not an exact person")
        per_observer[observer] += 1
    for observer, count in per_observer.items():
        if observer and count > 64:
            errors.append(f"{observer}: current beliefs exceed sparse bound ({count} > 64)")

    vows = social.get("vows", {}) if isinstance(social, Mapping) else {}
    if vows not in (None, {}) and not isinstance(vows, Mapping):
        errors.append("social vows owner is not an object")
        vows = {}
    per_vower: collections.Counter[str] = collections.Counter()
    for ref, raw in vows.items() if isinstance(vows, Mapping) else ():
        metrics["vows"] += 1
        if not isinstance(raw, Mapping):
            errors.append(f"{ref}: social vow row is not an object"); continue
        person = str(raw.get("person_ref") or ""); subject = str(raw.get("subject_ref") or ""); faction = str(raw.get("faction_ref") or "")
        if person not in living_exact_people:
            errors.append(f"{ref}: current vow owner is missing/dead")
        if subject and subject not in living_exact_people:
            errors.append(f"{ref}: current protect-vow subject is missing/dead")
        if faction and faction not in current_faction_refs:
            errors.append(f"{ref}: current faction vow references retired faction {faction}")
        per_vower[person] += 1
    for person, count in per_vower.items():
        if person and count > 12:
            errors.append(f"{person}: current vows exceed sparse bound ({count} > 12)")

    familiarity = social.get("martial_familiarity", {}) if isinstance(social, Mapping) else {}
    if familiarity not in (None, {}) and not isinstance(familiarity, Mapping):
        errors.append("social martial familiarity owner is not an object")
        familiarity = {}
    per_fighter: collections.Counter[str] = collections.Counter()
    for ref, raw in familiarity.items() if isinstance(familiarity, Mapping) else ():
        metrics["martial_familiarity"] += 1
        if not isinstance(raw, Mapping):
            errors.append(f"{ref}: martial familiarity row is not an object"); continue
        observer = str(raw.get("observer_ref") or ""); opponent = str(raw.get("opponent_ref") or "")
        if observer not in living_exact_people or opponent not in living_exact_people or observer == opponent:
            errors.append(f"{ref}: martial familiarity has unresolved/distinct living refs")
        per_fighter[observer] += 1
    for observer, count in per_fighter.items():
        if observer and count > 8:
            errors.append(f"{observer}: familiar opponents exceed sparse bound ({count} > 8)")
    return errors, metrics


def coalition_causality_errors(
    *, relations: Mapping[str, Any], current_faction_refs: set[str],
) -> tuple[list[str], dict[str, int]]:
    """Validate sparse current shared-war coalitions against their real relation edges."""
    errors: list[str] = []
    metrics = {"coalitions": 0, "coalition_memberships": 0}
    edges = relations.get("edges", []) if isinstance(relations, Mapping) else []
    if not isinstance(edges, list):
        return ["faction relation edges owner is not an array"], metrics
    directed = {
        (str(row.get("from_faction") or ""), str(row.get("to_faction") or "")): row
        for row in edges if isinstance(row, Mapping)
    }

    def compatible(a: str, b: str) -> bool:
        ab = directed.get((a, b), {})
        ba = directed.get((b, a), {})
        hostility = max(int(ab.get("hostility", 0)), int(ba.get("hostility", 0)))
        trust = int(ab.get("trust", 0)) + int(ba.get("trust", 0))
        return hostility <= 12 and trust >= 30

    coalitions = relations.get("coalitions", {}) if isinstance(relations, Mapping) else {}
    if coalitions not in (None, {}) and not isinstance(coalitions, Mapping):
        return ["faction coalitions owner is not an object"], metrics
    for ref, raw in coalitions.items() if isinstance(coalitions, Mapping) else ():
        metrics["coalitions"] += 1
        if not isinstance(raw, Mapping):
            errors.append(f"{ref}: coalition row is not an object")
            continue
        members_raw = raw.get("member_faction_refs", [])
        if not isinstance(members_raw, list):
            errors.append(f"{ref}: coalition member_faction_refs is not an array")
            continue
        members = [str(value) for value in members_raw if isinstance(value, str) and value]
        metrics["coalition_memberships"] += len(members)
        if len(members) < 2 or len(set(members)) != len(members):
            errors.append(f"{ref}: coalition must contain at least two unique member factions")
        target = str(raw.get("target_faction_ref") or "")
        if target not in current_faction_refs:
            errors.append(f"{ref}: coalition target is not a current faction: {target!r}")
        if target and target in set(members):
            errors.append(f"{ref}: coalition target is also a coalition member")
        if str(raw.get("purpose") or "") != "mutual_war_pressure":
            errors.append(f"{ref}: coalition has unsupported current purpose {raw.get('purpose')!r}")
        for member in members:
            if member not in current_faction_refs:
                errors.append(f"{ref}: coalition member is not a current faction: {member}")
                continue
            war_edge = directed.get((member, target), {})
            if not target or conflict_stage(war_edge) != "war":
                errors.append(f"{ref}: coalition member {member} is not currently at war with {target}")
        for index, left in enumerate(members):
            for right in members[index + 1:]:
                if left in current_faction_refs and right in current_faction_refs and not compatible(left, right):
                    errors.append(f"{ref}: mutually incompatible coalition members survive: {left}, {right}")
    return errors, metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="json_path", help="write machine-readable result")
    args = parser.parse_args()

    errors: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, Any] = {}

    identities_doc = load("game/data/martial-world/faction-identities.json")
    identities = identities_doc.get("identities", {})
    world_seed = load("game/data/martial-world/world-seed.json")
    static_factions = world_seed.get("martial_factions", {}) if isinstance(world_seed, dict) else {}
    geography = load("game/data/martial-world/geography.json")
    route_rows = geography.get("routes", []) if isinstance(geography, dict) else []
    route_by_id = {
        str(row.get("id")): row for row in route_rows
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    faction_paths = sorted((ROOT / "state/martial-world/factions").glob("*.json"))
    roster_paths = sorted((ROOT / "state/martial-world/people").glob("*.json"))
    factions: dict[str, dict] = {}
    people: dict[str, dict] = {}
    people_faction: dict[str, str] = {}
    roster_people: dict[str, list[dict]] = {}
    authored_type_counts: collections.Counter[str] = collections.Counter()
    active_type_counts: collections.Counter[str] = collections.Counter()
    grade_counts: collections.Counter[str] = collections.Counter()
    names: collections.Counter[str] = collections.Counter()

    scheduler = load("state/martial-world/scheduler.json")
    registry = load("state/martial-world/faction-registry.json")
    active_refs = {
        str(value) for value in registry.get("faction_refs", []) if isinstance(value, str)
    } if isinstance(registry, dict) else set()
    dormant_refs = {
        str(value) for value in registry.get("dormant_estate_refs", []) if isinstance(value, str)
    } if isinstance(registry, dict) else set()
    current_year = parse_year(scheduler.get("settled_through", ""))
    metrics["current_year"] = current_year

    if len(identities) != 240:
        errors.append(f"expected 240 explicit faction identities, found {len(identities)}")
    if len(faction_paths) < 240:
        errors.append(f"authored faction owner loss: expected at least 240 durable owners, found {len(faction_paths)}")

    for path in faction_paths:
        stored_faction = json.loads(path.read_text(encoding="utf-8"))
        fid = stored_faction.get("faction_id")
        if not isinstance(fid, str) or not fid:
            errors.append(f"{path.name}: missing stable faction_id")
            continue
        try:
            faction = hydrate_faction_state(stored_faction)
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"{fid}: faction hydration failed: {exc}")
            continue
        factions[fid] = faction
        identity = identities.get(fid)
        if isinstance(identity, dict):
            missing = REQUIRED_IDENTITY_FIELDS - set(identity)
            if missing:
                errors.append(f"{fid}: identity missing fields {sorted(missing)}")
            stored_type = stored_faction.get("type")
            if stored_type is not None and identity.get("faction_type") != stored_type:
                errors.append(f"{fid}: mutable faction type disagrees with static identity")
            authored_type_counts[str(identity.get("faction_type"))] += 1
            if faction.get("headquarters") != identity.get("headquarters"):
                errors.append(f"{fid}: headquarters differs from authored identity")
            copied_static = STATIC_FACTION_KEYS & set(stored_faction)
            if copied_static:
                errors.append(f"{fid}: static identity copied into hot faction state: {sorted(copied_static)}")
            static_profile = static_factions.get(fid, {}) if isinstance(static_factions, dict) else {}
            static_training = static_profile.get("training", {}) if isinstance(static_profile, dict) else {}
            authored_training = identity.get("training_curriculum", {})
            if static_training != authored_training:
                errors.append(f"{fid}: world-seed training differs from authored identity curriculum")
        else:
            # Materialized factions are current campaign facts, not authored
            # bootstrap identities. Their durable owner must therefore carry
            # the institutional policy that static factions hydrate by ID.
            if fid in static_factions:
                errors.append(f"{fid}: authored faction is missing its explicit identity row")
            ftype = resolved_faction_type(faction)
            if not ftype:
                errors.append(f"{fid}: dynamic faction lacks a resolved faction type")
            for key in ("training", "recruitment_policy", "admission_policy", "autonomy_policy"):
                if not isinstance(faction.get(key), dict) or not faction.get(key):
                    errors.append(f"{fid}: dynamic faction lacks durable {key}")
            if not str(faction.get("headquarters") or "") or not str(faction.get("local_site_ref") or ""):
                errors.append(f"{fid}: dynamic faction lacks a physical headquarters")
        if fid in active_refs:
            active_type_counts[resolved_faction_type(faction)] += 1
        epoch = faction.get("training_epoch")
        if isinstance(epoch, dict):
            forbidden_epoch = {"curriculum_ref", "history", "current_environment"} & set(epoch)
            if forbidden_epoch:
                errors.append(f"{fid}: derived training snapshot fields survive in hot state: {sorted(forbidden_epoch)}")
            allowed_epoch = {"started_at", "settled_through", "elapsed_training_days", "intensity_milli"}
            extra_epoch = set(epoch) - allowed_epoch
            if extra_epoch:
                errors.append(f"{fid}: unexplained training epoch fields survive: {sorted(extra_epoch)}")

    if dict(authored_type_counts) != EXPECTED_TYPES:
        errors.append(f"faction type distribution differs from authored world: {dict(authored_type_counts)}")

    durable_refs = set(factions)
    for fid in sorted(active_refs | dormant_refs):
        if fid not in durable_refs:
            errors.append(f"{fid}: registry references missing durable faction owner")
    unknown_registry_state = durable_refs - set(identities) - active_refs - dormant_refs
    if unknown_registry_state:
        errors.append(f"dynamic faction owners are neither active nor dormant: {sorted(unknown_registry_state)}")

    local_sites_doc = load("game/data/martial-world/local-sites.json")
    local_sites = local_sites_doc.get("sites", {}) if isinstance(local_sites_doc, dict) else {}
    site_claims: dict[str, list[str]] = collections.defaultdict(list)
    for fid in sorted(active_refs):
        faction = factions.get(fid)
        if not isinstance(faction, dict):
            continue
        claims: list[str] = []
        primary = str(faction.get("local_site_ref") or "")
        if primary:
            claims.append(primary)
        controlled = faction.get("controlled_estates", {}) if isinstance(faction.get("controlled_estates"), dict) else {}
        claims.extend(str(ref) for ref in controlled if isinstance(ref, str) and ref)
        for site_ref in claims:
            if isinstance(local_sites, dict) and site_ref not in local_sites:
                errors.append(f"{fid}: controls unknown local site {site_ref}")
            site_claims[site_ref].append(fid)
    collisions = {
        site_ref: sorted(set(owners)) for site_ref, owners in site_claims.items()
        if len(set(owners)) > 1
    }
    for site_ref, owners in sorted(collisions.items()):
        errors.append(f"active local site has multiple faction controllers: {site_ref}: {owners}")
    metrics["active_controlled_local_sites"] = len(site_claims)
    metrics["active_site_control_collisions"] = len(collisions)

    outlaw_route_coverage: collections.Counter[str] = collections.Counter()
    for fid, profile in static_factions.items() if isinstance(static_factions, dict) else []:
        if not isinstance(profile, dict) or profile.get("type") != "outlaw_faction":
            continue
        headquarters = str(profile.get("headquarters") or "")
        operating_routes = profile.get("operating_routes", [])
        if not isinstance(operating_routes, list) or not operating_routes:
            errors.append(f"{fid}: outlaw faction has no operating route")
            continue
        for route_ref in operating_routes:
            route = route_by_id.get(str(route_ref))
            if not isinstance(route, dict):
                errors.append(f"{fid}: unknown outlaw operating route {route_ref!r}")
                continue
            if headquarters not in {str(route.get("from") or ""), str(route.get("to") or "")}:
                errors.append(f"{fid}: outlaw operating route is nonlocal to headquarters: {route_ref}")
            outlaw_route_coverage[str(route_ref)] += 1
    # Dynamic outlaws do not have an authored identity row. Their current
    # operational identity must still be mechanically usable and local to the
    # physical headquarters they actually occupy.
    for fid in sorted(active_refs - set(identities)):
        faction = factions.get(fid)
        if not isinstance(faction, dict) or resolved_faction_type(faction) != "outlaw_faction":
            continue
        subtype = str(faction.get("outlaw_subtype") or "")
        routes = faction.get("operating_routes", [])
        policy = faction.get("outlaw_policy")
        if not subtype:
            errors.append(f"{fid}: dynamic outlaw faction lacks outlaw_subtype")
        if not isinstance(routes, list) or not routes:
            errors.append(f"{fid}: dynamic outlaw faction lacks operating routes")
            routes = []
        if not isinstance(policy, dict) or not policy:
            errors.append(f"{fid}: dynamic outlaw faction lacks outlaw policy")
        headquarters = str(faction.get("headquarters") or "")
        for route_ref in routes:
            route = route_by_id.get(str(route_ref))
            if not isinstance(route, dict):
                errors.append(f"{fid}: unknown dynamic outlaw operating route {route_ref!r}")
            elif headquarters not in {str(route.get("from") or ""), str(route.get("to") or "")}:
                errors.append(f"{fid}: dynamic outlaw operating route is nonlocal to headquarters: {route_ref}")
    for route_ref in route_by_id:
        if route_ref == "route.luoyang.rural_estates":
            continue
        if outlaw_route_coverage[route_ref] <= 0:
            errors.append(f"regional route lacks local outlaw operating pressure: {route_ref}")

    for path in roster_paths:
        roster = json.loads(path.read_text(encoding="utf-8"))
        fid = roster.get("faction_ref")
        rows = roster.get("people", [])
        if not isinstance(fid, str) or fid not in factions:
            errors.append(f"{path.name}: unknown faction_ref {fid!r}")
            continue
        roster_people[fid] = rows
        policy = faction_admission_policy(fid, factions[fid])
        allowed_sexes = set(policy.get("allowed_sexes") or [])
        min_age = int(policy.get("minimum_entry_age", 0))
        leader_count = 0
        heir_count = 0
        for person in rows:
            pid = person.get("person_id")
            if not isinstance(pid, str) or not pid:
                errors.append(f"{fid}: person without stable ID")
                continue
            if pid in people:
                errors.append(f"duplicate person identity {pid}")
                continue
            people[pid] = person
            people_faction[pid] = fid
            name = person.get("name")
            if not isinstance(name, str) or not name.strip() or re.match(r"^Recruit\s+[0-9a-f]+$", name, re.I):
                errors.append(f"{pid}: invalid generated display name {name!r}")
            else:
                names[name] += 1
            grade = person.get("membership_grade")
            grade_counts[str(grade)] += 1
            if grade not in CANONICAL_GRADES:
                errors.append(f"{pid}: noncanonical membership grade {grade!r}")
            if allowed_sexes and person.get("sex") not in allowed_sexes:
                errors.append(f"{pid}: sex violates {fid} admission policy")
            age = current_year - int(person.get("birth_year", current_year))
            if age < 0:
                errors.append(f"{pid}: birth_year lies in the future")
            offices = person.get("standing_offices") or []
            if "leader" in offices:
                leader_count += 1
            if "heir" in offices:
                heir_count += 1
            for node_path, node in walk(person):
                if node_path and node_path[-1].lower() == "shield":
                    errors.append(f"{pid}: deleted Shield field survives at {'.'.join(node_path)}")
        if leader_count != 1:
            errors.append(f"{fid}: expected exactly one living leader, found {leader_count}")

    independent_doc = load("state/martial-world/independent-people.json")
    independent_rows = independent_doc.get("people", []) if isinstance(independent_doc, dict) else []
    independent_people: dict[str, dict] = {}
    if not isinstance(independent_rows, list):
        errors.append("independent people owner has invalid people array")
        independent_rows = []
    for person in independent_rows:
        if not isinstance(person, dict):
            errors.append("independent people owner contains a non-object person")
            continue
        pid = person.get("person_id")
        if not isinstance(pid, str) or not pid:
            errors.append("independent person without stable ID")
            continue
        if pid in people or pid in independent_people:
            errors.append(f"duplicate persistent martial identity {pid}")
            continue
        if person.get("membership_grade") is not None or person.get("faction_ref") is not None:
            errors.append(f"{pid}: independent person still carries active faction membership")
        independent_people[pid] = person
        name = person.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{pid}: independent person has invalid display name {name!r}")
        else:
            names[name] += 1

    civic_doc = load("state/martial-world/civic-people.json")
    civic_rows = civic_doc.get("people", []) if isinstance(civic_doc, dict) else []
    civic_people: dict[str, dict] = {}
    if not isinstance(civic_rows, list):
        errors.append("civic people owner has invalid people array")
        civic_rows = []
    for person in civic_rows:
        if not isinstance(person, dict):
            errors.append("civic people owner contains a non-object person")
            continue
        pid = person.get("person_id")
        if not isinstance(pid, str) or not pid:
            errors.append("civic person without stable ID")
            continue
        if pid in people or pid in independent_people or pid in civic_people:
            errors.append(f"duplicate exact person identity {pid}")
            continue
        civic_people[pid] = person

    persistent_people = {**people, **independent_people}
    exact_people = {**persistent_people, **civic_people}
    living_exact_people = {
        ref for ref, person in exact_people.items()
        if not (isinstance(person.get("health"), dict) and person.get("health", {}).get("status") == "dead")
    }
    try:
        social_doc = load("state/martial-world/social.json")
        social_errors, social_metrics = social_causality_errors(
            social=social_doc, exact_people=exact_people, living_exact_people=living_exact_people,
            current_faction_refs=set(active_refs) | set(dormant_refs),
        )
        errors.extend(social_errors)
        metrics["social_causality"] = social_metrics
    except FileNotFoundError:
        pass

    try:
        relation_doc = load("state/martial-world/faction-relations.json")
        coalition_errors, coalition_metrics = coalition_causality_errors(
            relations=relation_doc,
            # Dormant estates remain durable ownership facts, but they are not
            # living institutions capable of coordinating a current war.
            current_faction_refs=set(active_refs),
        )
        errors.extend(coalition_errors)
        metrics["war_coalitions"] = coalition_metrics
    except FileNotFoundError:
        pass

    try:
        equipment_ledger = load("state/martial-world/equipment-ledger.json")
        loadout_doc = load("game/data/martial-world/equipment-loadouts.json")
        loadout_policies = loadout_doc.get("policies", {}) if isinstance(loadout_doc, Mapping) else {}
        property_errors, property_metrics = equipment_authority_errors(
            ledger=equipment_ledger, loadout_policies=loadout_policies, exact_people=exact_people,
            living_exact_people=living_exact_people, active_faction_refs=active_refs, dormant_faction_refs=dormant_refs,
            people_faction=people_faction,
        )
        errors.extend(property_errors)
        metrics["equipment_authority"] = property_metrics
    except FileNotFoundError:
        pass

    metrics.update({
        "factions": len(factions),
        "faction_people": len(people),
        "independent_people": len(independent_people),
        "civic_people": len(civic_people),
        "people": len(persistent_people),
        "martial_people": len(persistent_people),
        "exact_people": len(exact_people),
        "unique_names": len(names),
        "maximum_name_collision": max(names.values(), default=0),
        "grade_counts": dict(sorted(grade_counts.items())),
        "faction_type_counts": dict(sorted(authored_type_counts.items())),
        "active_faction_type_counts": dict(sorted(active_type_counts.items())),
    })
    if len(persistent_people) < 11691:
        errors.append(f"persistent martial identity loss: bootstrap=11691 current={len(persistent_people)}")
    rostered_martial = sum(count for grade, count in grade_counts.items() if grade in MARTIAL_GRADES)
    if rostered_martial != len(people):
        errors.append(f"every rostered faction person must be a martial member, found {rostered_martial} of {len(people)}")
    if len(names) < 9000 or metrics["maximum_name_collision"] > 8:
        errors.append(f"generated name diversity remains too low: unique={len(names)} max_collision={metrics['maximum_name_collision']}")

    child_bands = {
        "0_3": {"ages": range(0, 4), "mass": 20, "physical_attribute": 24, "martial": 10, "professional": 0},
        "4_7": {"ages": range(4, 8), "mass": 36, "physical_attribute": 55, "martial": 16, "professional": 3},
        "8_12": {"ages": range(8, 13), "mass": 56, "physical_attribute": 50, "martial": 18, "professional": 6},
    }
    child_metrics: dict[str, dict[str, int]] = {}
    authored_tang = set(TANG_IDENTITY_TARGETS)
    physical_keys = ("strength", "speed", "dexterity", "endurance")
    for label, band in child_bands.items():
        rows = [
            p for p in persistent_people.values()
            if current_year - int(p.get("birth_year", current_year)) in band["ages"]
            and str(p.get("person_id", "")) not in authored_tang
        ]
        summary = {
            "count": len(rows),
            "max_mass": max((int(p.get("body_mass_kg", 0)) for p in rows), default=0),
            "max_physical_attribute": max((max((int((p.get("attributes") or {}).get(k, 0)) for k in physical_keys), default=0) for p in rows), default=0),
            "max_martial": max((max((p.get("martial_skills") or {}).values(), default=0) for p in rows), default=0),
            "max_professional": max((max((p.get("professional_skills") or {}).values(), default=0) for p in rows), default=0),
        }
        child_metrics[label] = summary
        for key in ("mass", "physical_attribute", "martial", "professional"):
            actual_key = "max_" + key
            if summary[actual_key] > band[key]:
                errors.append(f"child band {label}: {actual_key}={summary[actual_key]} exceeds age-aware ceiling {band[key]}")
    metrics["child_bands"] = child_metrics

    # The canonical revision-85 Tang profiles are exact baseline fixtures, but
    # progression and institutional affiliation are live gameplay facts.  This
    # future-state verifier therefore protects only permanent authored identity.
    for pid in TANG_IDENTITY_TARGETS:
        errors.extend(tang_identity_errors(pid, exact_people.get(pid)))

    keyword_focus = (("sword", "sword"), ("bow", "bow"), ("spear", "spear"), ("boxing", "unarmed"), ("palm", "unarmed"))
    for fid, identity in identities.items():
        lower_name = str(identity.get("name", "")).lower()
        curriculum = identity.get("training_curriculum") or {}
        martial = {k: int(curriculum.get(k, 0)) for k in ("sword", "spear", "bow", "unarmed", "hidden_weapons")}
        for token, discipline in keyword_focus:
            if token in lower_name and martial[discipline] < max(martial.values(), default=0):
                errors.append(f"{fid}: name says {token} but {discipline} is not a top martial priority")

    equipment = load("game/data/martial-world/equipment.json")
    expected_weapons = {
        "weapon_jian", "weapon_dao", "weapon_long_dao", "weapon_short_sword", "weapon_dagger",
        "weapon_spear", "weapon_short_spear", "weapon_staff", "weapon_glaive",
        "weapon_bow", "weapon_throwing_knife", "weapon_needle",
    }
    expected_clothing = {
        "clothing_traditional_martial", "clothing_faction_martial",
        "concealment_cloth_face_wrap", "concealment_veiled_bamboo_hat", "concealment_plain_full_mask",
    }
    if set(equipment.get("weapon_catalog", {})) != expected_weapons:
        errors.append("weapon catalog differs from settled Jianghu equipment ontology")
    if set(equipment.get("clothing_catalog", {})) != expected_clothing:
        errors.append("clothing catalog differs from settled Jianghu clothing ontology")
    if set(equipment.get("ammunition_catalog", {})) != {"item_arrow"}:
        errors.append("Arrow must be the sole canonical ammunition entry")
    active_json_roots = [ROOT / "game/data", ROOT / "state", ROOT / "runtime/contracts"]
    forbidden_ontology_hits: list[str] = []
    for base in active_json_roots:
        for path in base.rglob("*.json"):
            doc = json.loads(path.read_text(encoding="utf-8"))
            for node_path, node in walk(doc):
                if node_path:
                    key = node_path[-1].lower()
                    if key in BANNED_HOT_KEYS and str(path).startswith(str(ROOT / "state")):
                        errors.append(f"{path.relative_to(ROOT)}: banned hot-state key {key}")
                    if key in {"techniques", "named_techniques", "jutsu", "technique_tree", "skill_tree", "item_rarity", "rarity"}:
                        errors.append(f"{path.relative_to(ROOT)}: prohibited gameplay ontology key {key}")
                    if FORBIDDEN_ONTOLOGY_RE.search(key):
                        forbidden_ontology_hits.append(
                            f"{path.relative_to(ROOT)}: deleted ontology survives in key {'.'.join(node_path)}"
                        )
                if isinstance(node, str) and FORBIDDEN_ONTOLOGY_RE.search(node.lower()):
                    forbidden_ontology_hits.append(
                        f"{path.relative_to(ROOT)}: deleted ontology survives at {'.'.join(node_path)}: {node!r}"
                    )

    runtime_root = ROOT / "runtime/shinobi_runtime"
    for path in runtime_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        match = FORBIDDEN_ONTOLOGY_RE.search(text)
        if match:
            line = text.count("\n", 0, match.start()) + 1
            forbidden_ontology_hits.append(
                f"{path.relative_to(ROOT)}:{line}: deleted ontology token {match.group(0)!r} survives in runtime source"
            )
    if forbidden_ontology_hits:
        errors.extend(forbidden_ontology_hits)
    metrics["forbidden_ontology_hits"] = len(forbidden_ontology_hits)

    family = load("state/martial-world/family.json")
    marriages = family.get("marriages", {})
    parentage = family.get("parentage", {})
    households = family.get("households", {})
    succession = family.get("succession_claims", {})
    house_ids = {fid for fid, identity in identities.items() if identity.get("faction_type") == "martial_house"}
    for fid in house_ids:
        if not any(row.get("faction_ref") == fid for row in households.values()):
            errors.append(f"{fid}: no family household")
        if not any(row.get("faction_ref") == fid for row in succession.values()):
            errors.append(f"{fid}: no succession claim")
    metrics["family"] = {"marriages": len(marriages), "parentage": len(parentage), "households": len(households), "succession_claims": len(succession)}

    recurring = scheduler.get("recurring", {})
    for cadence, rows in recurring.items():
        if isinstance(rows, dict):
            for owner_ref in rows:
                if owner_ref in persistent_people or str(owner_ref).startswith("mw.person."):
                    errors.append(f"per-person scheduler host survives: {cadence}/{owner_ref}")
    metrics["scheduler_recurring_domains"] = sorted(recurring)

    # Availability is derived from real activity owners. Validate the projection
    # rather than requiring a second mutable commitment registry.
    if (ROOT / "state/martial-world/commitments.json").exists():
        errors.append("persistent commitment registry survived derived-state cleanup")
    try:
        commitment_state = derived_commitment_state(load)
    except Exception as exc:
        errors.append(f"derived activity occupancy failed: {exc}")
        commitment_state = {"commitments": {}, "person_index": {}}
    commitments = commitment_state.get("commitments", {}) if isinstance(commitment_state, dict) else {}
    person_index = commitment_state.get("person_index", {}) if isinstance(commitment_state, dict) else {}
    resource_owner: dict[tuple[str, str], str] = {}
    for cid, row in commitments.items() if isinstance(commitments, dict) else ():
        if not isinstance(row, dict):
            errors.append(f"{cid}: derived activity row is not an object")
            continue
        for resource_row in row.get("resources", []):
            if not isinstance(resource_row, dict):
                continue
            kind, ref = resource_row.get("kind"), resource_row.get("ref")
            if not isinstance(kind, str) or not isinstance(ref, str):
                errors.append(f"{cid}: malformed derived resource")
                continue
            resource = (kind, ref)
            previous = resource_owner.get(resource)
            if previous is not None and previous != cid:
                errors.append(f"resource double-booked by activity owners {previous} and {cid}: {resource}")
            resource_owner[resource] = cid
            if kind == "person":
                if ref not in exact_people:
                    errors.append(f"{cid}: derived occupancy references missing exact person {ref}")
                elif ref not in living_exact_people:
                    errors.append(f"{cid}: derived occupancy references dead exact person {ref}")
    if isinstance(person_index, dict):
        for person_ref, cid in person_index.items():
            if person_ref not in exact_people:
                errors.append(f"derived occupancy index references missing exact person {person_ref}")
            elif person_ref not in living_exact_people:
                errors.append(f"derived occupancy index references dead exact person {person_ref}")
            if not isinstance(commitments, dict) or cid not in commitments:
                errors.append(f"derived occupancy index references missing activity {cid}")
    metrics["active_derived_activities"] = len(commitments) if isinstance(commitments, dict) else 0
    metrics["derived_occupied_resources"] = len(resource_owner)

    # Exact-person references that are durable but not always time-reserving
    # still need direct identity/liveness certification.  Derived commitments
    # intentionally omit standing retinues and pre-departure accepted contracts.
    deployments_doc = load("state/martial-world/deployments.json")
    deployment_rows = deployments_doc.get("deployments", {}) if isinstance(deployments_doc, dict) else {}
    terminal_deployments = {"completed", "returned", "failed", "cancelled", "disbanded", "closed"}
    if isinstance(deployment_rows, dict):
        for dref, row in deployment_rows.items():
            if not isinstance(row, dict):
                continue
            refs: set[str] = set()
            for key in ("participant_refs", "member_refs", "chooser_refs"):
                values = row.get(key, [])
                if isinstance(values, list):
                    refs.update(str(x) for x in values if isinstance(x, str) and x)
            for key in ("leader_ref", "commander_ref", "deputy_ref"):
                value = row.get(key)
                if isinstance(value, str) and value:
                    refs.add(value)
            structure = row.get("structure", {}) if isinstance(row.get("structure"), dict) else {}
            values = structure.get("member_refs", []) if isinstance(structure, dict) else []
            if isinstance(values, list):
                refs.update(str(x) for x in values if isinstance(x, str) and x)
            for key in ("commander_ref", "deputy_ref"):
                value = structure.get(key) if isinstance(structure, dict) else None
                if isinstance(value, str) and value:
                    refs.add(value)
            live_owner = str(row.get("status") or "active") not in terminal_deployments
            for ref in sorted(refs):
                if ref not in exact_people:
                    errors.append(f"{dref}: deployment references missing exact person {ref}")
                elif live_owner and ref not in living_exact_people:
                    errors.append(f"{dref}: live deployment/retinue references dead exact person {ref}")

    route_ops_for_equipment = load("state/martial-world/route-operations.json")
    errors.extend(deployment_equipment_authority_errors(
        deployments=deployments_doc, exact_people=set(exact_people),
        living_exact_people=living_exact_people, route_operations=route_ops_for_equipment,
    ))
    errors.extend(strategic_operation_intent_errors(
        deployments=deployments_doc, factions=factions,
    ))

    contract_doc = load("state/martial-world/contracts/index.json")
    contract_rows = contract_doc.get("active", {}) if isinstance(contract_doc, dict) else {}
    if isinstance(contract_rows, dict):
        for cref, row in contract_rows.items():
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "")
            participants = row.get("participants", [])
            participant_refs = [str(x) for x in participants if isinstance(x, str) and x] if isinstance(participants, list) else []
            for ref in participant_refs:
                if ref not in exact_people:
                    errors.append(f"{cref}: contract references missing exact principal {ref}")
                elif status in {"accepted", "in_progress", "objective_resolved"} and ref not in living_exact_people:
                    errors.append(f"{cref}: live contract references dead principal {ref}")
            if status == "accepted" and not participant_refs:
                errors.append(f"{cref}: accepted contract has no living principal path")
            objective = row.get("objective", {}) if isinstance(row.get("objective"), dict) else {}
            protected = objective.get("protected_person_refs", []) if isinstance(objective, dict) else []
            if isinstance(protected, list):
                for ref in [str(x) for x in protected if isinstance(x, str) and x]:
                    if ref not in exact_people:
                        errors.append(f"{cref}: contract protects missing exact person {ref}")
                    elif status in {"offered", "accepted"} and ref not in living_exact_people:
                        errors.append(f"{cref}: predeparture contract protects dead exact person {ref}")

    tournament_doc = load("state/martial-world/tournaments.json")
    errors.extend(institutional_membership_obligation_errors(
        contracts=contract_doc, tournaments=tournament_doc,
        living_exact_people=living_exact_people, people_faction=people_faction,
        faction_refs=set(factions),
    ))
    tournament_rows = tournament_doc.get("tournaments", {}) if isinstance(tournament_doc, dict) else {}
    if isinstance(tournament_rows, dict):
        for tref, tournament in tournament_rows.items():
            if not isinstance(tournament, dict):
                continue
            registrations = tournament.get("registrations", [])
            if isinstance(registrations, list):
                for registration in registrations:
                    if not isinstance(registration, dict):
                        continue
                    ref = registration.get("entrant_ref")
                    if isinstance(ref, str) and ref and ref not in exact_people:
                        errors.append(f"{tref}: tournament registration references missing entrant {ref}")
            delegations = tournament.get("delegations", {})
            if isinstance(delegations, dict):
                for delegation in delegations.values():
                    if not isinstance(delegation, dict):
                        continue
                    for key in ("entrant_refs", "spectator_refs", "leader_refs", "senior_refs"):
                        values = delegation.get(key, [])
                        if isinstance(values, list):
                            for ref in [str(x) for x in values if isinstance(x, str) and x]:
                                if ref not in exact_people:
                                    errors.append(f"{tref}: tournament delegation references missing exact person {ref}")

    custody_doc = load("state/martial-world/custody.json")
    custody_rows = custody_doc.get("records", []) if isinstance(custody_doc, dict) else []
    if isinstance(custody_rows, list):
        for row in custody_rows:
            if not isinstance(row, dict) or str(row.get("status") or "") in {"released", "escaped", "rescued", "executed"}:
                continue
            prisoner = str(row.get("person_ref") or "")
            captor = str(row.get("captor_ref") or "")
            if prisoner and prisoner not in exact_people:
                errors.append(f"custody references missing exact prisoner {prisoner}")
            elif prisoner and prisoner not in living_exact_people:
                errors.append(f"live custody references dead exact prisoner {prisoner}")
            if captor:
                holder_kind = str(row.get("holder_kind") or "")
                if holder_kind == "government":
                    jurisdiction = str(row.get("jurisdiction_ref") or "")
                    expected_captor = f"government:{jurisdiction}" if jurisdiction else ""
                    if not jurisdiction or captor != expected_captor:
                        errors.append(f"government custody has invalid institutional captor {captor}")
                    if int(row.get("guard_strength", 0)) <= 0:
                        errors.append(f"government custody lacks positive guard strength for {prisoner}")
                    if not str(row.get("sentence_release_at") or ""):
                        errors.append(f"government custody lacks sentence release frontier for {prisoner}")
                elif captor not in exact_people:
                    errors.append(f"custody references missing exact captor {captor}")
                elif captor not in living_exact_people:
                    errors.append(f"live custody references dead exact captor {captor}")

    government = load("state/martial-world/government.json")
    attention = government.get("attention", {})
    for warrant_ref, warrant in government.get("warrants", {}).items():
        evidence_ref = warrant.get("evidence_ref")
        subject_ref = warrant.get("subject_ref")
        if not isinstance(evidence_ref, str) or not evidence_ref:
            errors.append(f"{warrant_ref}: warrant lacks current evidence authority")
        if isinstance(subject_ref, str) and subject_ref not in attention:
            errors.append(f"{warrant_ref}: warrant subject lacks current government attention")

    route_ops = load("state/martial-world/route-operations.json")
    errors.extend(route_controller_authority_errors(route_operations=route_ops))
    for collection in ("movements",):
        for op_ref, operation in (route_ops.get(collection) or {}).items():
            refs = operation.get("participant_refs") or operation.get("escort_refs") or []
            for pid in refs:
                if pid not in exact_people:
                    errors.append(f"{op_ref}: route operation references missing exact person {pid}")
                elif str(operation.get("status") or "active") not in {"completed", "closed", "failed", "settled", "cancelled"} and pid not in living_exact_people:
                    errors.append(f"{op_ref}: live route operation references dead exact person {pid}")
            if operation.get("commitment_refs"):
                errors.append(f"{op_ref}: route operation persists obsolete commitment refs")
    for contact_ref, contact in (route_ops.get("contacts") or {}).items():
        for pid in list(contact.get("outlaw_refs") or []) + list(contact.get("escort_refs") or []):
            if pid not in exact_people:
                errors.append(f"{contact_ref}: contact references missing exact participant {pid}")

    # Finite scheduler obligations must resolve to a current domain owner.  The
    # scheduler is causal state, not a history log: terminal/missing owners must
    # not retain future wakeups, while live finite owners that require a wakeup
    # must not silently disappear from the causal frontier.
    one_off = scheduler.get("one_off", {}) if isinstance(scheduler, dict) else {}
    if not isinstance(one_off, dict):
        errors.append("scheduler one_off owner is not an object")
        one_off = {}
    one_off_by_kind: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for event_id, event in one_off.items():
        if not isinstance(event, dict):
            errors.append(f"scheduler one-off {event_id}: event is not an object")
            continue
        kind = str(event.get("kind") or "")
        owner_ref = str(event.get("owner_ref") or "")
        one_off_by_kind[kind].append(event)
        if kind == "contract_expiry_due":
            contract = contract_rows.get(owner_ref) if isinstance(contract_rows, dict) else None
            if not isinstance(contract, dict):
                errors.append(f"{event_id}: contract expiry references missing live contract {owner_ref}")
            elif str(event.get("due_at") or "") != str(contract.get("expires_at") or ""):
                errors.append(f"{event_id}: contract expiry time differs from current contract {owner_ref}")
        elif kind == "autonomous_project_due":
            project_doc = load("state/martial-world/projects.json")
            project_rows = project_doc.get("projects", {}) if isinstance(project_doc, dict) else {}
            project = project_rows.get(owner_ref) if isinstance(project_rows, dict) else None
            if not isinstance(project, dict):
                errors.append(f"{event_id}: project wake references missing project {owner_ref}")
            elif str(project.get("status") or "") in {"completed", "cancelled", "abandoned", "closed"}:
                errors.append(f"{event_id}: terminal project retains scheduler wake {owner_ref}")
        elif kind in {"faction_operation_departure", "faction_operation_arrival", "faction_operation_return"}:
            operation = deployment_rows.get(owner_ref) if isinstance(deployment_rows, dict) else None
            if not isinstance(operation, dict):
                errors.append(f"{event_id}: operation wake references missing deployment {owner_ref}")
            elif str(operation.get("status") or "") in terminal_deployments:
                errors.append(f"{event_id}: terminal operation retains scheduler wake {owner_ref}")
        elif kind == "route_activity_cycle":
            movement_ref = str(event.get("movement_ref") or "")
            movement = (route_ops.get("movements") or {}).get(movement_ref) if isinstance(route_ops, dict) else None
            if not isinstance(movement, dict):
                errors.append(f"{event_id}: route wake references missing movement {movement_ref}")
            elif str(movement.get("status") or "active") in {"completed", "closed", "failed", "settled", "cancelled"}:
                errors.append(f"{event_id}: terminal route movement retains scheduler wake {movement_ref}")
        elif kind == "government_custody_release_due":
            custody_id = owner_ref
            current = next(
                (row for row in custody_rows if isinstance(row, dict) and str(row.get("custody_id") or "") == custody_id),
                None,
            ) if isinstance(custody_rows, list) else None
            if not isinstance(current, dict):
                errors.append(f"{event_id}: government release references missing custody {custody_id}")
            elif str(current.get("holder_kind") or "") != "government":
                errors.append(f"{event_id}: government release references non-government custody {custody_id}")
            elif str(event.get("due_at") or "") != str(current.get("sentence_release_at") or ""):
                errors.append(f"{event_id}: government release time differs from current sentence {custody_id}")
        elif kind == "family_birth_due":
            marriage_ref = str(event.get("marriage_ref") or "")
            child_ref = str(event.get("child_ref") or "")
            marriage = marriages.get(marriage_ref) if isinstance(marriages, dict) else None
            pregnancy = marriage.get("pregnancy") if isinstance(marriage, dict) and isinstance(marriage.get("pregnancy"), dict) else None
            if not isinstance(pregnancy, dict):
                errors.append(f"{event_id}: birth wake references marriage without current pregnancy {marriage_ref}")
            elif str(pregnancy.get("child_ref") or "") != child_ref:
                errors.append(f"{event_id}: birth wake child differs from current pregnancy {marriage_ref}")

    project_doc = load("state/martial-world/projects.json")
    project_rows = project_doc.get("projects", {}) if isinstance(project_doc, dict) else {}
    contract_expiry_owners = {str(row.get("owner_ref") or "") for row in one_off_by_kind.get("contract_expiry_due", [])}
    for contract_ref, contract in contract_rows.items() if isinstance(contract_rows, dict) else ():
        if not isinstance(contract, dict):
            continue
        if str(contract.get("status") or "") in {"offered", "accepted"} and str(contract.get("expires_at") or "") and contract_ref not in contract_expiry_owners:
            errors.append(f"{contract_ref}: live expiring contract lacks scheduler expiry wake")
    project_wake_owners = {str(row.get("owner_ref") or "") for row in one_off_by_kind.get("autonomous_project_due", [])}
    for project_ref, project in project_rows.items() if isinstance(project_rows, dict) else ():
        if isinstance(project, dict) and str(project.get("status") or "") == "active" and project_ref not in project_wake_owners:
            errors.append(f"{project_ref}: active project lacks scheduler progress wake")
    custody_release_owners = {str(row.get("owner_ref") or "") for row in one_off_by_kind.get("government_custody_release_due", [])}
    for row in custody_rows if isinstance(custody_rows, list) else ():
        if not isinstance(row, dict):
            continue
        custody_id = str(row.get("custody_id") or "")
        if (
            str(row.get("holder_kind") or "") == "government"
            and str(row.get("status") or "held") not in {"released", "escaped", "rescued", "executed"}
            and str(row.get("sentence_release_at") or "")
            and custody_id not in custody_release_owners
        ):
            errors.append(f"{custody_id}: government sentence lacks scheduler release wake")
    birth_wakes = {
        (str(row.get("marriage_ref") or ""), str(row.get("child_ref") or ""))
        for row in one_off_by_kind.get("family_birth_due", [])
    }
    for marriage_ref, marriage in marriages.items() if isinstance(marriages, dict) else ():
        pregnancy = marriage.get("pregnancy") if isinstance(marriage, dict) and isinstance(marriage.get("pregnancy"), dict) else None
        if isinstance(pregnancy, dict):
            child_ref = str(pregnancy.get("child_ref") or "")
            if child_ref and (str(marriage_ref), child_ref) not in birth_wakes:
                errors.append(f"{marriage_ref}: current pregnancy lacks scheduler birth wake")

    operation_wakes: dict[str, set[str]] = collections.defaultdict(set)
    for kind in ("faction_operation_departure", "faction_operation_arrival", "faction_operation_return"):
        for event in one_off_by_kind.get(kind, []):
            operation_wakes[str(event.get("owner_ref") or "")].add(kind)
    route_carried_operations: set[str] = set()
    for movement_ref, movement in (route_ops.get("movements") or {}).items() if isinstance(route_ops, dict) else ():
        if not isinstance(movement, dict) or str(movement.get("status") or "") in {"completed", "closed", "failed", "settled", "cancelled"}:
            continue
        if not str(movement_ref).startswith("route_operation:"):
            continue
        body = str(movement_ref)[len("route_operation:"):]
        for suffix in (":outbound", ":return"):
            if body.endswith(suffix):
                route_carried_operations.add(body[:-len(suffix)])
                break
    for operation_ref, operation in deployment_rows.items() if isinstance(deployment_rows, dict) else ():
        if not isinstance(operation, dict) or not str(operation_ref).startswith("operation:"):
            continue
        status = str(operation.get("status") or "")
        wakes = operation_wakes.get(str(operation_ref), set())
        if status in {"mobilizing", "return_preparing"} and "faction_operation_departure" not in wakes:
            errors.append(f"{operation_ref}: {status} operation lacks departure wake")
        elif status == "traveling_outbound" and "faction_operation_arrival" not in wakes and operation_ref not in route_carried_operations:
            errors.append(f"{operation_ref}: outbound operation lacks direct or route-carried arrival wake")
        elif status == "traveling_return" and "faction_operation_return" not in wakes and operation_ref not in route_carried_operations:
            errors.append(f"{operation_ref}: returning operation lacks direct or route-carried return wake")
    metrics["scheduler_one_off_events"] = len(one_off)
    metrics["scheduler_one_off_kinds"] = dict(sorted((kind, len(rows)) for kind, rows in one_off_by_kind.items()))

    schema_version_hits = []
    for path in (ROOT / "state").rglob("*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for node_path, _node in walk(doc):
            if node_path and node_path[-1] in BANNED_HOT_KEYS:
                schema_version_hits.append(f"{path.relative_to(ROOT)}:{'.'.join(node_path)}")
    if schema_version_hits:
        errors.append(f"hot-state archaeology remains ({len(schema_version_hits)} hits), first={schema_version_hits[0]}")

    metrics["errors"] = len(errors)
    metrics["warnings"] = len(warnings)
    result = {"status": "PASS" if not errors else "FAIL", "metrics": metrics, "errors": errors, "warnings": warnings}
    if args.json_path:
        out = Path(args.json_path)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"JIANGHU SEMANTIC CHECK {result['status']}")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    for message in errors[:200]:
        print("ERROR:", message)
    if len(errors) > 200:
        print(f"ERROR: ... {len(errors) - 200} more")
    for message in warnings:
        print("WARNING:", message)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
