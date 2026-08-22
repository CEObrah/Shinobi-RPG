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
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
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
    "rivals", "operating_routes", "display_titles",
}
TANG_TARGETS = {
    "pc_wei_tang": {
        "name": "Tang Wei", "age": 17,
        "attributes": {"strength": 82, "speed": 90, "dexterity": 94, "endurance": 84, "perception": 96, "intelligence": 100, "willpower": 92},
        "martial_skills": {"sword": 115, "unarmed": 72, "stealth_scouting": 65, "command": 55},
        "professional_skills": {"medicine": 15, "administration": 40, "commerce": 25, "crafting": 35, "instruction": 25},
        "qi": 150, "qi_control": 78,
    },
    "char.zhu": {
        "name": "Tang Zhu", "age": 37,
        "attributes": {"strength": 87, "speed": 79, "dexterity": 86, "endurance": 89, "perception": 90, "intelligence": 100, "willpower": 96},
        "martial_skills": {"sword": 110, "unarmed": 72, "stealth_scouting": 52, "command": 90},
        "professional_skills": {"medicine": 25, "administration": 92, "commerce": 45, "crafting": 50, "instruction": 108},
        "qi": 145, "qi_control": 88,
    },
    "char.ling": {
        "name": "Tang Ling", "age": 34,
        "attributes": {"strength": 70, "speed": 82, "dexterity": 90, "endurance": 80, "perception": 98, "intelligence": 100, "willpower": 94},
        "martial_skills": {"sword": 102, "unarmed": 58, "stealth_scouting": 62, "command": 66},
        "professional_skills": {"medicine": 120, "administration": 70, "commerce": 40, "crafting": 60, "instruction": 82},
        "qi": 145, "qi_control": 92,
    },
    "char.kai": {
        "name": "Tang Kai", "age": 6,
        "attributes": {"strength": 22, "speed": 45, "dexterity": 48, "endurance": 30, "perception": 70, "intelligence": 100, "willpower": 68},
        "martial_skills": {"sword": 40, "unarmed": 25, "stealth_scouting": 25},
        "professional_skills": {"medicine": 3, "administration": 3, "commerce": 2, "crafting": 3},
        "qi": 125, "qi_control": 35,
    },
}


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
    faction_paths = sorted((ROOT / "state/martial-world/factions").glob("*.json"))
    roster_paths = sorted((ROOT / "state/martial-world/people").glob("*.json"))
    factions: dict[str, dict] = {}
    people: dict[str, dict] = {}
    people_faction: dict[str, str] = {}
    roster_people: dict[str, list[dict]] = {}
    type_counts: collections.Counter[str] = collections.Counter()
    grade_counts: collections.Counter[str] = collections.Counter()
    names: collections.Counter[str] = collections.Counter()

    scheduler = load("state/martial-world/scheduler.json")
    current_year = parse_year(scheduler.get("settled_through", ""))
    metrics["current_year"] = current_year

    if len(identities) != 240:
        errors.append(f"expected 240 explicit faction identities, found {len(identities)}")
    if len(faction_paths) != 240:
        errors.append(f"expected 240 faction owners, found {len(faction_paths)}")

    for path in faction_paths:
        faction = json.loads(path.read_text(encoding="utf-8"))
        fid = faction.get("faction_id")
        if not isinstance(fid, str) or not fid:
            errors.append(f"{path.name}: missing stable faction_id")
            continue
        factions[fid] = faction
        identity = identities.get(fid)
        if not isinstance(identity, dict):
            errors.append(f"{fid}: missing explicit authored identity")
            continue
        missing = REQUIRED_IDENTITY_FIELDS - set(identity)
        if missing:
            errors.append(f"{fid}: identity missing fields {sorted(missing)}")
        if identity.get("faction_type") != faction.get("type"):
            if faction.get("type") is not None:
                errors.append(f"{fid}: mutable faction type disagrees with static identity")
        type_counts[str(identity.get("faction_type"))] += 1
        if faction.get("headquarters") != identity.get("headquarters"):
            errors.append(f"{fid}: headquarters differs from authored identity")
        if STATIC_FACTION_KEYS & set(faction):
            errors.append(f"{fid}: static identity copied into hot faction state: {sorted(STATIC_FACTION_KEYS & set(faction))}")
        static_profile = static_factions.get(fid, {}) if isinstance(static_factions, dict) else {}
        static_training = static_profile.get("training", {}) if isinstance(static_profile, dict) else {}
        authored_training = identity.get("training_curriculum", {})
        if static_training != authored_training:
            errors.append(f"{fid}: world-seed training differs from authored identity curriculum")
        logical_training = dict(static_training) if isinstance(static_training, dict) else {}
        if isinstance(faction.get("training"), dict):
            logical_training.update(faction["training"])
        epoch = faction.get("training_epoch")
        if isinstance(epoch, dict):
            if "curriculum_ref" in epoch:
                errors.append(f"{fid}: obsolete curriculum_ref survives in hot training epoch")
            current_environment = epoch.get("current_environment")
            if isinstance(current_environment, dict) and current_environment.get("curriculum") != logical_training:
                errors.append(f"{fid}: active training environment curriculum is stale")

    if dict(type_counts) != EXPECTED_TYPES:
        errors.append(f"faction type distribution differs from authored world: {dict(type_counts)}")

    for path in roster_paths:
        roster = json.loads(path.read_text(encoding="utf-8"))
        fid = roster.get("faction_ref")
        rows = roster.get("people", [])
        if not isinstance(fid, str) or fid not in factions:
            errors.append(f"{path.name}: unknown faction_ref {fid!r}")
            continue
        roster_people[fid] = rows
        identity = identities[fid]
        policy = identity.get("admission_policy") or {}
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

    persistent_people = {**people, **independent_people}
    metrics.update({
        "factions": len(factions),
        "faction_people": len(people),
        "independent_people": len(independent_people),
        "people": len(persistent_people),
        "martial_people": len(persistent_people),
        "unique_names": len(names),
        "maximum_name_collision": max(names.values(), default=0),
        "grade_counts": dict(sorted(grade_counts.items())),
        "faction_type_counts": dict(sorted(type_counts.items())),
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
    authored_tang = set(TANG_TARGETS)
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

    for pid, target in TANG_TARGETS.items():
        person = people.get(pid)
        if person is None:
            errors.append(f"missing authored Tang person {pid}")
            continue
        if people_faction.get(pid) != "house_tang":
            errors.append(f"{pid}: not owned by House Tang")
        if person.get("name") != target["name"]:
            errors.append(f"{pid}: authored name changed to {person.get('name')!r}")
        if current_year - int(person.get("birth_year", current_year)) != target["age"]:
            errors.append(f"{pid}: age differs from authored target {target['age']}")
        for field in ("attributes", "martial_skills", "professional_skills"):
            if person.get(field) != target[field]:
                errors.append(f"{pid}: {field} differs from current authored target")
        if int(person.get("qi", -1)) != target["qi"] or int(person.get("qi_control", -1)) != target["qi_control"]:
            errors.append(f"{pid}: cultivation differs from current authored target")
        aptitudes = person.get("aptitudes") or {}
        if set(aptitudes.values()) != {200}:
            errors.append(f"{pid}: extraordinary 200 aptitudes were not preserved")
        if "shield" in json.dumps(person, ensure_ascii=False).lower():
            errors.append(f"{pid}: deleted shield identity survives")

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

    # Commitment uniqueness and exact resource conservation links. The current
    # owner stores a list of resources per activity; every resource must be
    # unique globally and the person index must exactly mirror person resources.
    commitment_state = load("state/martial-world/commitments.json")
    commitments = commitment_state.get("commitments", {}) if isinstance(commitment_state, dict) else {}
    person_index = commitment_state.get("person_index", {}) if isinstance(commitment_state, dict) else {}
    if not isinstance(commitments, dict):
        errors.append("commitment owner has invalid commitments map")
        commitments = {}
    if not isinstance(person_index, dict):
        errors.append("commitment owner has invalid person_index map")
        person_index = {}
    resource_owner: dict[tuple[str, str], str] = {}
    for cid, row in commitments.items():
        if not isinstance(row, dict):
            errors.append(f"{cid}: commitment row is not an object")
            continue
        if row.get("status", "active") != "active":
            errors.append(f"{cid}: inactive commitment persisted in current owner")
        if row.get("commitment_ref") != cid:
            errors.append(f"{cid}: commitment_ref disagrees with map key")
        resources = row.get("resources", [])
        person_refs = row.get("person_refs", [])
        if not isinstance(resources, list):
            errors.append(f"{cid}: resources is not an array")
            resources = []
        if not isinstance(person_refs, list) or any(not isinstance(ref, str) or not ref for ref in person_refs):
            errors.append(f"{cid}: person_refs is not a valid string array")
            person_refs = []
        row_resources: set[tuple[str, str]] = set()
        resource_people: set[str] = set()
        for index, resource_row in enumerate(resources):
            if not isinstance(resource_row, dict):
                errors.append(f"{cid}: resource[{index}] is not an object")
                continue
            kind = resource_row.get("kind")
            ref = resource_row.get("ref")
            owner_ref = resource_row.get("owner_ref")
            if not isinstance(kind, str) or not kind or not isinstance(ref, str) or not ref:
                errors.append(f"{cid}: resource[{index}] lacks exact kind/ref")
                continue
            if not isinstance(owner_ref, str) or not owner_ref:
                errors.append(f"{cid}: resource[{index}] lacks owner_ref")
            resource = (kind, ref)
            if resource in row_resources:
                errors.append(f"{cid}: duplicate resource inside commitment: {resource}")
            row_resources.add(resource)
            previous = resource_owner.get(resource)
            if previous is not None and previous != cid:
                errors.append(f"resource double-booked by commitments {previous} and {cid}: {resource}")
            else:
                resource_owner[resource] = cid
            if kind == "person":
                resource_people.add(ref)
                if ref not in persistent_people:
                    errors.append(f"{cid}: committed person resource does not exist: {ref}")
        declared_people = set(person_refs)
        if len(declared_people) != len(person_refs):
            errors.append(f"{cid}: duplicate person_refs")
        if declared_people != resource_people:
            errors.append(f"{cid}: person_refs disagree with person resources")
        for person_ref in declared_people:
            if person_index.get(person_ref) != cid:
                errors.append(f"{cid}: person_index disagrees for {person_ref}")
    for person_ref, cid in person_index.items():
        if not isinstance(person_ref, str) or person_ref not in persistent_people:
            errors.append(f"person_index references missing person {person_ref!r}")
            continue
        row = commitments.get(cid)
        if not isinstance(cid, str) or not isinstance(row, dict):
            errors.append(f"person_index references missing commitment {cid!r} for {person_ref}")
            continue
        refs = row.get("person_refs", [])
        if not isinstance(refs, list) or person_ref not in refs:
            errors.append(f"person_index entry {person_ref} is absent from {cid} person_refs")
    metrics["active_commitments"] = len(commitments)
    metrics["committed_resources"] = len(resource_owner)

    government = load("state/martial-world/government.json")
    attention = government.get("attention", {})
    for warrant_ref, warrant in government.get("warrants", {}).items():
        evidence_ref = warrant.get("evidence_ref")
        subject_ref = warrant.get("subject_ref")
        if not isinstance(evidence_ref, str) or not evidence_ref:
            errors.append(f"{warrant_ref}: warrant lacks current evidence authority")
        subject_attention = attention.get(subject_ref) if isinstance(subject_ref, str) else None
        if isinstance(subject_attention, dict):
            last_evidence_ref = subject_attention.get("last_evidence_ref")
            if isinstance(last_evidence_ref, str) and last_evidence_ref and last_evidence_ref != evidence_ref:
                errors.append(f"{warrant_ref}: warrant evidence disagrees with subject attention")

    route_ops = load("state/martial-world/route-operations.json")
    for collection in ("movements",):
        for op_ref, operation in (route_ops.get(collection) or {}).items():
            refs = operation.get("participant_refs") or operation.get("escort_refs") or []
            for pid in refs:
                if pid not in persistent_people:
                    errors.append(f"{op_ref}: route operation references missing person {pid}")
            for commitment_ref in operation.get("commitment_refs") or []:
                if commitment_ref not in commitments:
                    errors.append(f"{op_ref}: route operation lacks live reservation {commitment_ref}")
    for contact_ref, contact in (route_ops.get("contacts") or {}).items():
        for pid in list(contact.get("outlaw_refs") or []) + list(contact.get("escort_refs") or []):
            if pid not in persistent_people:
                errors.append(f"{contact_ref}: contact references missing participant {pid}")

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
