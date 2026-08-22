#!/usr/bin/env python3
"""Verify the current Jianghu release structure and conservation invariants."""
from __future__ import annotations

import glob
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from shinobi_runtime.store import RepositoryStore, RegisteredSchemaValidator, RegisteredTemplateValidator
from shinobi_runtime.martial_world.faction_state import (
    compact_faction_state, hydrate_faction_state, inventory_path, roster_path, faction_profile,
)
from shinobi_runtime.martial_world.inventory_state import compact_inventory_state
from shinobi_runtime.martial_world.independent_people import compact_independent_person
from shinobi_runtime.martial_world.person_state import (
    compact_roster_state, hydrate_roster_state, martial_member_from_grade,
)


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def main() -> int:
    errors: list[str] = []
    repo = RepositoryStore(ROOT)

    # Current release has one mutable authority root only.
    allowed_top = {"state/meta.json", "state/scene.json"}
    state_paths = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "state").rglob("*.json"))
    for rel in state_paths:
        if rel not in allowed_top and not rel.startswith("state/martial-world/"):
            fail(errors, f"obsolete mutable authority outside martial-world: {rel}")

    # Formal schemas and closed structural templates validate every mutable owner.
    try:
        schema_validator = RegisteredSchemaValidator(repo)
        template_validator = RegisteredTemplateValidator(repo)
    except Exception as exc:
        fail(errors, f"validator bootstrap failed: {exc}")
        schema_validator = template_validator = None

    if schema_validator and template_validator:
        for rel in state_paths:
            try:
                doc = repo.read_json(rel)
                if not isinstance(doc, dict):
                    raise ValueError("root is not an object")
                sid = doc.get("schema")
                if sid not in schema_validator.validators:
                    raise ValueError(f"unregistered schema {sid!r}")
                schema_validator.validators[sid].validate(doc)
                template = template_validator.templates.get(sid)
                if template is None or template_validator.scopes.get(sid) != "mutable_state":
                    raise ValueError(f"no mutable template for {sid!r}")
                RegisteredTemplateValidator._validate_document(doc, template, label=rel)
            except Exception as exc:
                fail(errors, f"{rel}: {exc}")

    # Registry/template/blank owner parity.
    registry = load("game/schemas/registry.json")
    blank_index = load("runtime/contracts/blank-owner-index.json").get("owners", {})
    template_ids = set(template_validator.templates) if template_validator else set()
    if set(registry) != template_ids:
        fail(errors, f"schema/template ID mismatch: schemas-only={sorted(set(registry)-template_ids)} templates-only={sorted(template_ids-set(registry))}")
    if set(blank_index) != template_ids:
        fail(errors, f"blank/template ID mismatch: missing={sorted(template_ids-set(blank_index))} extra={sorted(set(blank_index)-template_ids)}")
    for sid, rel in blank_index.items():
        p = ROOT / rel
        if not p.is_file():
            fail(errors, f"missing blank owner for {sid}: {rel}")
            continue
        try:
            blank = json.loads(p.read_text(encoding="utf-8"))
            if blank.get("schema") != sid:
                fail(errors, f"blank owner schema mismatch for {sid}")
            if template_validator:
                template = template_validator.templates[sid]
                root_contract = template["object_contracts"][""]
                extra = set(blank) - set(root_contract.get("allowed_keys", []))
                missing = set(template.get("required_top_level_keys", [])) - set(blank)
                if extra: fail(errors, f"blank owner {sid}: unregistered top-level keys {sorted(extra)}")
                if missing: fail(errors, f"blank owner {sid}: missing required skeleton keys {sorted(missing)}")
        except Exception as exc:
            fail(errors, f"blank owner invalid for {sid}: {exc}")

    # Faction/roster/inventory/person conservation. Paths are derived from IDs;
    # no duplicate people index, roster_ref, or inventory_ref exists in hot state.
    faction_files = sorted((ROOT / "state/martial-world/factions").glob("*.json"))
    if len(faction_files) != 240:
        fail(errors, f"expected 240 factions, found owners={len(faction_files)}")

    people_by_id: dict[str, tuple[str, int, dict, str]] = {}
    total_people = total_martial = 0
    faction_types: dict[str, int] = {}
    for path in faction_files:
        raw_faction = json.loads(path.read_text(encoding="utf-8"))
        fid = raw_faction.get("faction_id")
        if not isinstance(fid, str) or not fid:
            fail(errors, f"{path.name}: invalid faction_id")
            continue
        if path.name != f"{fid}.json":
            fail(errors, f"{fid}: faction path is not exact-ID canonical: {path.name}")
        try:
            faction = hydrate_faction_state(raw_faction)
        except Exception as exc:
            fail(errors, f"{fid}: faction hydration failed: {exc}")
            continue
        if compact_faction_state(faction) != raw_faction:
            fail(errors, f"{fid}: faction owner is not canonical sparse state")
        faction_types[str(faction.get("type", "?"))] = faction_types.get(str(faction.get("type", "?")), 0) + 1

        roster_rel = roster_path(fid)
        try:
            roster_raw = load(roster_rel)
        except Exception as exc:
            fail(errors, f"{fid}: cannot load roster: {exc}")
            continue
        if roster_raw.get("faction_ref") != fid:
            fail(errors, f"{fid}: roster owner mismatch")
        try:
            roster_logical = hydrate_roster_state(roster_raw, faction=faction)
        except Exception as exc:
            fail(errors, f"{fid}: roster hydration failed: {exc}")
            continue
        if compact_roster_state(roster_logical, faction=faction) != roster_raw:
            fail(errors, f"{fid}: roster is not canonical sparse state")
        people = roster_raw.get("people")
        if not isinstance(people, list):
            fail(errors, f"{fid}: roster people is not an array")
            continue
        members = 0
        living = 0
        for person in people:
            if not isinstance(person, dict):
                continue
            if martial_member_from_grade(person.get("membership_grade")) is True:
                members += 1
            health = person.get("health", {}) if isinstance(person.get("health"), dict) else {}
            if health.get("status") != "dead":
                living += 1
        if members != len(people):
            fail(errors, f"{fid}: every rostered faction person must have a martial membership grade ({members}/{len(people)})")
        if living != faction.get("population"):
            fail(errors, f"{fid}: living population mismatch faction={faction.get('population')} roster={living}")
        if "martial_members" in faction or "household_support_members" in faction:
            fail(errors, f"{fid}: obsolete martial/support population split survived")
        total_people += len(people); total_martial += members

        inv_rel = inventory_path(fid)
        try:
            inv = load(inv_rel)
            if inv.get("faction_ref") != fid:
                fail(errors, f"{fid}: inventory owner mismatch")
            if compact_inventory_state(inv) != inv:
                fail(errors, f"{fid}: inventory is not canonical sparse state")
        except Exception as exc:
            fail(errors, f"{fid}: inventory missing/invalid: {exc}")

        for ordinal, person in enumerate(people):
            if not isinstance(person, dict):
                fail(errors, f"{fid}: person ordinal {ordinal} is not an object"); continue
            pid = person.get("person_id")
            if not isinstance(pid, str) or not pid:
                fail(errors, f"{fid}: person ordinal {ordinal} has invalid ID"); continue
            if pid in people_by_id:
                fail(errors, f"duplicate person identity {pid}")
            if person.get("faction_ref") is not None:
                fail(errors, f"{pid}: roster-owned faction_ref must be omitted")
            cash = person.get("personal_cash", 0)
            if isinstance(cash, bool) or not isinstance(cash, int) or cash < 0:
                fail(errors, f"{pid}: invalid personal_cash")
            for forbidden_default in ("headquarters", "representation", "training_assignment", "combat_targeting_doctrine"):
                if forbidden_default in person:
                    fail(errors, f"{pid}: redundant per-person field survived: {forbidden_default}")
            if martial_member_from_grade(person.get("membership_grade")) is not None and "martial_member" in person:
                fail(errors, f"{pid}: derivable martial_member must be omitted")
            if person.get("current_qi") == person.get("qi", 0):
                fail(errors, f"{pid}: full current_qi must be omitted")
            if person.get("fatigue_milli") == 0:
                fail(errors, f"{pid}: zero fatigue must be omitted")
            if person.get("qi") == 0 or person.get("qi_control") == 0:
                fail(errors, f"{pid}: zero Qi/Qi Control defaults must be omitted")
            for section in ("martial_skills", "professional_skills"):
                values = person.get(section, {})
                if isinstance(values, dict) and any(v == 0 for v in values.values()):
                    fail(errors, f"{pid}: zero-valued {section} entry must be omitted")
            people_by_id[pid] = (roster_rel, ordinal, person, fid)

    # Faction exits conserve exact martial identities in the sparse independent
    # owner. Recruitment may later increase the persistent martial population,
    # so the bootstrap count is a floor, not a forever-fixed faction-roster count.
    independent_owner = load("state/martial-world/independent-people.json")
    independent_people = independent_owner.get("people", []) if isinstance(independent_owner, dict) else []
    independent_ids: set[str] = set()
    if not isinstance(independent_people, list):
        fail(errors, "independent people owner has invalid people array")
        independent_people = []
    for ordinal, person in enumerate(independent_people):
        if not isinstance(person, dict):
            fail(errors, f"independent person ordinal {ordinal} is not an object")
            continue
        pid = person.get("person_id")
        if not isinstance(pid, str) or not pid:
            fail(errors, f"independent person ordinal {ordinal} has invalid ID")
            continue
        if pid in people_by_id or pid in independent_ids:
            fail(errors, f"duplicate persistent martial identity {pid}")
        independent_ids.add(pid)
        if person.get("faction_ref") is not None or person.get("membership_grade") is not None:
            fail(errors, f"{pid}: independent person still carries active faction membership")
        if compact_independent_person(person) != person:
            fail(errors, f"{pid}: independent person is not canonical sparse state")
        former = person.get("former_faction_ref")
        since = person.get("independent_since")
        if not isinstance(former, str) or not former:
            fail(errors, f"{pid}: independent person missing former_faction_ref provenance")
        if not isinstance(since, str) or not since:
            fail(errors, f"{pid}: independent person missing independent_since provenance")

    total_persistent_martial = total_people + len(independent_ids)
    if total_persistent_martial < 11691:
        fail(errors, f"persistent martial identity loss: bootstrap=11691 current={total_persistent_martial}")
    if total_martial != total_people:
        fail(errors, f"every rostered faction person must be a martial member: members={total_martial} people={total_people}")
    expected_types = {
        "martial_house": 24, "sect": 60, "martial_school": 35,
        "escort_agency": 30, "brotherhood_society": 16,
        "outlaw_faction": 70, "contract_hall": 5,
    }
    if faction_types != expected_types:
        fail(errors, f"faction type distribution mismatch: {faction_types}")
    if any("mercenary" in str(x).lower() for x in faction_types):
        fail(errors, "mercenary faction type survived cleanup")

    # Direct route shards store only current faction people as [faction_ref,
    # ordinal]. Independent/civic identities intentionally fall back to their
    # sparse owners rather than acquiring a second mutable routing authority.
    route_index = load("state/martial-world/person-routes.json")
    if set(route_index) != {"schema", "person_count"}:
        fail(errors, "person route root contains derivable routing policy fields")
    if route_index.get("person_count") != len(people_by_id):
        fail(errors, f"person route root count mismatch {route_index.get('person_count')} != {len(people_by_id)}")
    routed: set[str] = set()
    for shard_path in sorted((ROOT / "state/martial-world/person-routes").glob("*.json")):
        bucket = shard_path.stem
        shard = json.loads(shard_path.read_text(encoding="utf-8"))
        for pid, row in (shard.get("people") or {}).items():
            if hashlib.sha256(pid.encode("utf-8")).hexdigest()[:2] != bucket:
                fail(errors, f"{pid}: routed to wrong hash bucket {bucket}")
            if pid in routed:
                fail(errors, f"{pid}: duplicate route")
            routed.add(pid)
            truth = people_by_id.get(pid)
            if truth is None:
                fail(errors, f"{pid}: route points to nonexistent faction person"); continue
            _roster_rel, ordinal, _person, fid = truth
            if not isinstance(row, list) or len(row) != 2 or row[0] != fid or row[1] != ordinal:
                fail(errors, f"{pid}: stale/noncompact direct route")
    if routed != set(people_by_id):
        fail(errors, f"person route coverage mismatch missing={len(set(people_by_id)-routed)} extra={len(routed-set(people_by_id))}")

    # Sword Manor is House Tang's faction headquarters, not a generator default for unrelated factions.
    for path in faction_files:
        faction = json.loads(path.read_text(encoding="utf-8"))
        if faction.get("faction_id") != "house_tang" and faction.get("headquarters") == "sword_manor":
            fail(errors, f"{faction.get('faction_id')}: unrelated faction incorrectly headquartered at Sword Manor")
    shaolin = load("state/martial-world/people/shaolin.json")
    if any(isinstance(p, dict) and p.get("sex") != "male" for p in shaolin.get("people", [])):
        fail(errors, "Shaolin monastic roster contains non-male identity")

    # House Tang bootstrap configuration. Population may lawfully grow through
    # conserved recruitment, so 100 is a floor rather than a frozen save value.
    ht = load("state/martial-world/factions/house_tang.json")
    if int(ht.get("population", 0)) < 100:
        fail(errors, f"House Tang population fell below bootstrap floor: {ht.get('population')}")
    if set(ht.get("buildings", {}).values()) != {5} or set(ht.get("enterprises", {}).values()) != {5}:
        fail(errors, "House Tang buildings and enterprises must all be Level 5")

    # Civilian aggregate conservation.
    civ = load("state/martial-world/civilian-populations.json")
    places = civ.get("places", {})
    civ_sum = sum(int(v.get("current_population", 0)) + int(v.get("reserved_for_recruitment", 0)) for v in places.values())
    if civ_sum < 0:
        fail(errors, "civilian population aggregate cannot be negative")
    if "population_total" in civ or "rule" in civ:
        fail(errors, "civilian state stores derived/explanatory top-level fields")
    for place_ref, pool in places.items():
        if pool.get("place_ref") is not None or pool.get("last_demographic_review_at") is not None:
            fail(errors, f"{place_ref}: civilian pool stores derived/history-only fields")
        if int(pool.get("reserved_for_recruitment", 0)) == 0 and "reserved_for_recruitment" in pool:
            fail(errors, f"{place_ref}: zero recruitment reservation should be sparse")
        if int(pool.get("recruitment_ordinal_cursor", 0)) == 0 and "recruitment_ordinal_cursor" in pool:
            fail(errors, f"{place_ref}: zero recruitment cursor should be sparse")

    # Geography and local-site integrity.
    geo = load("game/data/martial-world/geography.json")
    strategic_places = set((geo.get("places") or {}).keys())
    routes = geo.get("routes") or []
    for route in routes:
        a = route.get("from") or route.get("from_place_ref") or route.get("a")
        b = route.get("to") or route.get("to_place_ref") or route.get("b")
        if a not in strategic_places or b not in strategic_places:
            fail(errors, f"strategic route references unknown place: {route}")
        if int(route.get("distance_km", 0)) <= 0:
            fail(errors, f"strategic route has nonpositive distance: {route}")
    local = load("game/data/martial-world/local-sites.json").get("sites", {})
    # Local-site count is content-driven. New authored sites are legitimate;
    # validate referential closure instead of pinning a historical generated count.
    if len(local) < len(faction_files):
        fail(errors, f"local-site catalog unexpectedly smaller than faction count: {len(local)}")
    faction_site_refs = {str(load(p.relative_to(ROOT).as_posix()).get("local_site_ref") or "") for p in faction_files}
    missing_faction_sites = sorted(ref for ref in faction_site_refs if ref and ref not in local)
    if missing_faction_sites:
        fail(errors, f"faction local sites missing from catalog: {missing_faction_sites[:8]}")
    for site_ref, site in local.items():
        if site.get("site_ref") != site_ref:
            fail(errors, f"local site key/ref mismatch: {site_ref}")
        if site.get("parent_place_ref") not in strategic_places:
            fail(errors, f"{site_ref}: unknown strategic parent {site.get('parent_place_ref')}")
        for key in ("x_m", "y_m", "quality_level", "capacity", "price_multiplier_milli"):
            if isinstance(site.get(key), bool) or not isinstance(site.get(key), int):
                fail(errors, f"{site_ref}: invalid {key}")

    # No direct Qi pills and no obsolete faction/enterprise type.
    medicine = load("game/data/martial-world/medicine.json")
    med_text = json.dumps(medicine, ensure_ascii=False).lower()
    for forbidden in ("qi recovery pill", "qi_recovery_pill", "qi cultivation pill", "qi_cultivation_pill"):
        if forbidden in med_text:
            fail(errors, f"direct Qi pill survived: {forbidden}")
    faction_types_data = json.dumps(load("game/data/martial-world/faction-types.json")).lower()
    enterprises_data = json.dumps(load("game/data/martial-world/enterprises.json")).lower()
    if "mercenary_company" in faction_types_data or "mercenary_contracts" in enterprises_data:
        fail(errors, "invalid mercenary_company faction type present")

    if errors:
        print("STRUCTURE CHECK FAILED")
        for error in errors[:200]: print(" -", error)
        if len(errors) > 200: print(f" ... {len(errors)-200} more")
        return 1
    print(
        f"STRUCTURE OK: {len(state_paths)} state owners, {len(faction_files)} factions, "
        f"{total_people} faction people, {len(independent_ids)} independent people, "
        f"{total_persistent_martial} persistent martial identities, {len(local)} local sites"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
