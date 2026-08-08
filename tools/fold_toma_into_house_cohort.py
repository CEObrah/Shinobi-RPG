#!/usr/bin/env python3
from __future__ import annotations

import json
import statistics
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET_ID = "ht.m023"
MIGRATION_SOURCE = "7590b5ddb2fc19d8f6772b13f1b3d1a0ad0c0212"
TARGET_UNIT_ID = "unit.house_tang.junior_disciple_second"


def read(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def write(rel: str, data):
    (ROOT / rel).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def old_json(rel: str):
    raw = subprocess.check_output(["git", "show", f"{MIGRATION_SOURCE}:{rel}"], cwd=ROOT, text=True)
    return json.loads(raw)


def flatten_numeric(value, prefix=""):
    out = {}
    if isinstance(value, dict):
        for key, child in value.items():
            p = f"{prefix}.{key}" if prefix else key
            out.update(flatten_numeric(child, p))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        out[prefix] = float(value)
    return out


def age_at(birth_date: str, world_time: str):
    by, bm, bd = map(int, birth_date.removeprefix("SE-").split("-"))
    date = world_time.split("T", 1)[0].removeprefix("SE-")
    wy, wm, wd = map(int, date.split("-"))
    return wy - by - (1 if (wm, wd) < (bm, bd) else 0)


def summary(values):
    vals = [float(v) for v in values]
    return {
        "count": len(vals),
        "mean": round(sum(vals) / len(vals), 6),
        "sd": round(statistics.pstdev(vals), 6) if len(vals) > 1 else 0.0,
        "min": round(min(vals), 6),
        "max": round(max(vals), 6),
    }


def profile_for(records, world_time):
    numeric = {}
    categories = {}

    def cat(key):
        if key:
            categories[key] = categories.get(key, 0) + 1

    for person in records:
        for key, val in flatten_numeric(person.get("stats", {}), "stats").items():
            numeric.setdefault(key, []).append(val)
        for key, val in flatten_numeric(person.get("aptitude", {}), "aptitude").items():
            numeric.setdefault(key, []).append(val)
        body = person.get("body") or {}
        for key in ("adult_height_cm", "current_weight_kg", "growth_end_age"):
            val = body.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                numeric.setdefault(f"body.{key}", []).append(float(val))
        app = person.get("appearance")
        if isinstance(app, (int, float)) and not isinstance(app, bool):
            numeric.setdefault("appearance", []).append(float(app))
        numeric.setdefault("age_years", []).append(float(age_at(person["birth_date"], world_time)))
        cat(f"rank:{person.get('rank')}")
        cat(f"frame:{body.get('frame')}")
        cat(f"health:{(person.get('health') or {}).get('status')}")
        cat(f"assignment:{person.get('assignment')}")
        cat(f"origin:{person.get('origin')}")
        for duty in person.get("duties") or []:
            cat(f"duty:{duty}")
        for qual in person.get("qualifications") or []:
            cat(f"qualification:{qual}")
        for pkg in (person.get("repertoire") or {}).get("packages") or []:
            cat(f"package:{pkg}")

    return {
        "representation": "house_cohort",
        "numeric_distributions": {k: summary(v) for k, v in sorted(numeric.items())},
        "category_counts": dict(sorted(categories.items())),
        "development": {
            "resolved_through": world_time,
            "credits": {},
            "model": "representation_neutral_house_cohort",
        },
        "provenance": ["authoritative_house_cohort_profile_at_current_frontier"],
    }


def normalized_profile(profile):
    out = json.loads(json.dumps(profile))
    out["provenance"] = ["authoritative_house_cohort_profile_at_current_frontier"]
    return out


def find_refs(needle: str):
    refs = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel in {"tools/fold_toma_into_house_cohort.py", ".github/workflows/fold-toma-into-house-cohort.yml"}:
            continue
        if path.suffix.lower() not in {".json", ".md", ".py", ".yml", ".yaml"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if needle in text:
            refs.append(rel)
    return sorted(refs)


allowed_refs = {
    "state/house/tang.json",
    "state/index/owners/ht.json",
    "state/person/ht/023.json",
    "state/time/coverage/process_house_tang_people_weekly.json",
}
refs = set(find_refs(TARGET_ID))
if refs != allowed_refs:
    raise SystemExit(f"Unexpected {TARGET_ID} references: {sorted(refs)}")

house = read("state/house/tang.json")
meta = read("state/meta.json")
world_time = meta["time"]
unit = next((u for u in house["permanent_units"] if u.get("id") == TARGET_UNIT_ID), None)
if not unit or unit.get("members") != [TARGET_ID]:
    raise SystemExit(f"Unexpected current target-unit members: {None if not unit else unit.get('members')}")

old_house = old_json("state/house/tang.json")
old_unit = next(u for u in old_house["permanent_units"] if u.get("id") == TARGET_UNIT_ID)
old_ids = list(old_unit["members"])
if TARGET_ID not in old_ids or len(old_ids) != 7:
    raise SystemExit(f"Unexpected migration-source unit members: {old_ids}")

def person_path(pid: str):
    suffix = pid.split(".m", 1)[1]
    return f"state/person/ht/{suffix}.json"

old_records = {pid: old_json(person_path(pid)) for pid in old_ids}
expected_existing_records = [old_records[pid] for pid in old_ids if pid != TARGET_ID]
expected_existing_profile = profile_for(expected_existing_records, world_time)
if normalized_profile(unit.get("cohort_profile")) != expected_existing_profile:
    raise SystemExit("Current six-person cohort profile does not match exact migration-source reconstruction")
if unit.get("aggregate_count") != 6:
    raise SystemExit(f"Unexpected current aggregate count: {unit.get('aggregate_count')}")

# Reconstruct the original seven-person section exactly from its saved source records.
unit["members"] = []
unit["aggregate_count"] = 7
unit["cohort_profile"] = profile_for([old_records[pid] for pid in old_ids], world_time)

house["member_ids"] = [pid for pid in house.get("member_ids", []) if pid != TARGET_ID]
house["aggregate_member_count"] = sum(int(u.get("aggregate_count") or 0) for u in house["permanent_units"])
for u in house["permanent_units"]:
    if isinstance(u.get("cohort_profile"), dict):
        u["cohort_profile"]["provenance"] = ["authoritative_house_cohort_profile_at_current_frontier"]
write("state/house/tang.json", house)

person_file = ROOT / "state/person/ht/023.json"
if not person_file.exists():
    raise SystemExit("Target person owner missing before deletion")
person_file.unlink()

ht_index_path = ROOT / "state/index/owners/ht.json"
ht_index = read("state/index/owners/ht.json")
ht_index.get("owners", {}).pop(TARGET_ID, None)
if ht_index.get("owners"):
    write("state/index/owners/ht.json", ht_index)
else:
    ht_index_path.unlink()

owners = read("state/index/owners.json")
if not ht_index_path.exists():
    owners.get("prefix_index", {}).pop("ht", None)
count = 0
for shard in sorted((ROOT / "state/index/owners").glob("*.json")):
    count += len(json.loads(shard.read_text(encoding="utf-8")).get("owners", {}))
owners["owner_count"] = count
write("state/index/owners.json", owners)

coverage = read("state/time/coverage/process_house_tang_people_weekly.json")
coverage["owner_ids"] = [pid for pid in coverage.get("owner_ids", []) if pid != TARGET_ID]
write("state/time/coverage/process_house_tang_people_weekly.json", coverage)

runtime = read("state/runtime.json")
receipt = runtime["completed_reviews"]["process_house_tang_people_weekly"]
receipt["coverage_count"] = len(coverage["owner_ids"])
receipt["result"] = "House Tang exact people and aggregate House cohorts are the authoritative settlement base at this frontier; current representation coverage is complete."
write("state/runtime.json", runtime)

# Prevent recurrence: routine internal duty labels are cohort state, not individual-notability evidence.
contract_path = "data/runtime/system-contracts/population_recruitment.json"
contract = read(contract_path)
invariant = "Routine internal duty labels, including medical, support, logistics, watch and maintenance functions, are cohort category state and do not by themselves justify person-lite materialization."
if invariant not in contract["invariants"]:
    contract["invariants"].append(invariant)
write(contract_path, contract)

rules_path = ROOT / "rules/population.md"
rules = rules_path.read_text(encoding="utf-8")
anchor = "There is no quota and no automatic materialization merely for being accepted."
addition = " Routine internal duty labels such as medical, pharmacy, armory, watch, logistics, records, cooking, grounds, or scheduling do not by themselves justify person-lite materialization; the individual must have durable state that cohort averaging would lose."
if addition.strip() not in rules:
    rules = rules.replace(anchor, anchor + addition)
rules_path.write_text(rules, encoding="utf-8")

# Make the invariant executable.
test_path = ROOT / "tools/test_population_model.py"
test = test_path.read_text(encoding="utf-8")
needle = '    if not str(person.get("materialization_reason") or "").strip():\n        err(f"Sword Manor person-lite lacks materialization_reason:{pid}")\n'
replacement = needle + '    if person.get("materialization_reason") == "consequential_individual_duty":\n        err(f"Sword Manor person-lite cannot be materialized from a routine duty label alone:{pid}")\n'
if 'cannot be materialized from a routine duty label alone' not in test:
    if needle not in test:
        raise SystemExit("Population validator insertion point missing")
    test = test.replace(needle, replacement)
test_path.write_text(test, encoding="utf-8")

# Current tree must no longer contain this individual identity outside this temporary script.
remaining = find_refs(TARGET_ID)
if remaining:
    raise SystemExit(f"Target ID survived correction: {remaining}")

print("TOMA COHORT CORRECTION PREPARED")
print("target_unit", TARGET_UNIT_ID)
print("aggregate_count", unit["aggregate_count"])
print("house_aggregate_member_count", house["aggregate_member_count"])
print("coverage_count", receipt["coverage_count"])
print("owner_count", owners["owner_count"])
