#!/usr/bin/env python3
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors = []

def read(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))

def err(msg):
    errors.append(msg)

registry = read("state/population/registry.json")
if registry.get("schema") != "population-registry":
    err("population registry schema")
for pool_id, pool in registry.get("pools", {}).items():
    if not isinstance(pool.get("count"), int) or pool["count"] < 0:
        err(f"bad population pool count:{pool_id}")
for transfer in registry.get("transfers", []):
    applicants = transfer.get("applicants")
    accepted = transfer.get("accepted")
    rejected = transfer.get("rejected")
    if not all(isinstance(x, int) and x >= 0 for x in (applicants, accepted, rejected)):
        err(f"bad transfer counts:{transfer.get('id')}")
        continue
    if accepted + rejected != applicants:
        err(f"selection conservation:{transfer.get('id')}")
    if transfer.get("source_removed") != accepted or transfer.get("destination_added") != accepted:
        err(f"transfer headcount conservation:{transfer.get('id')}")
    mids = transfer.get("materialized_person_ids", [])
    if len(mids) > accepted:
        err(f"materialized subset exceeds accepted:{transfer.get('id')}")
    if mids and transfer.get("destination_ref") != "house.tang":
        err(f"mass recruitment illegally created person-lite:{transfer.get('id')}:{transfer.get('destination_ref')}")

house = read("state/house/tang.json")
if house.get("personal_force_model") != "aggregate_cohorts_with_sparse_sword_manor_notables":
    err("House Tang personal force model")
aggregate = 0
for unit in house.get("permanent_units", []):
    n = unit.get("aggregate_count")
    if not isinstance(n, int) or n < 0:
        err(f"bad House cohort count:{unit.get('id')}")
        continue
    aggregate += n
    profile = unit.get("cohort_profile")
    if n > 0:
        if not isinstance(profile, dict) or profile.get("representation") != "house_cohort":
            err(f"missing House cohort profile:{unit.get('id')}")
        else:
            dev = profile.get("development") or {}
            if dev.get("model") != "representation_neutral_house_cohort":
                err(f"bad House cohort development model:{unit.get('id')}")
            if not isinstance(dev.get("credits"), dict):
                err(f"bad House cohort credits:{unit.get('id')}")
    elif profile is not None:
        err(f"zero-count House unit has profile:{unit.get('id')}")
if aggregate != house.get("aggregate_member_count"):
    err(f"House aggregate total drift:{aggregate}:{house.get('aggregate_member_count')}")

if (ROOT / "state/house/units.json").exists():
    err("redundant state/house/units.json exists")

ht_files = sorted((ROOT / "state/person/ht").glob("*.json")) if (ROOT / "state/person/ht").exists() else []
ht_ids = set()
for path in ht_files:
    person = json.loads(path.read_text(encoding="utf-8"))
    pid = person.get("id")
    ht_ids.add(pid)
    if person.get("schema") != "person-lite" or person.get("resolution") != "individual_lite":
        err(f"bad Sword Manor notable owner:{path.name}")
    if not str(person.get("materialization_reason") or "").strip():
        err(f"Sword Manor person-lite lacks materialization_reason:{pid}")

ht_index = read("state/index/owners/ht.json").get("owners", {})
if set(ht_index) != ht_ids:
    err(f"Sword Manor owner index drift:index={sorted(ht_index)} files={sorted(ht_ids)}")
for pid, rel in ht_index.items():
    if not (ROOT / rel).exists():
        err(f"Sword Manor owner route missing:{pid}:{rel}")

coverage = read("state/time/coverage/process_house_tang_people_weekly.json").get("owner_ids", [])
if "house.tang" not in coverage:
    err("House cohort owner absent from House process coverage")
for pid in ht_ids:
    if pid not in coverage:
        err(f"Sword Manor notable absent from House process coverage:{pid}")

runtime = read("state/runtime.json")
receipt = runtime.get("completed_reviews", {}).get("process_house_tang_people_weekly", {})
if receipt.get("coverage_count") != len(coverage):
    err("House process receipt coverage drift")

rules = (ROOT / "rules/population.md").read_text(encoding="utf-8")
for phrase in ("Mass recruitment stays aggregate", "Sword Manor sparse-notable exception", "does not create one thousand person-lite"):
    if phrase not in rules:
        err(f"population rule missing:{phrase}")

if errors:
    print(f"POPULATION MODEL FAIL {len(errors)}")
    for e in errors:
        print("-", e)
    raise SystemExit(1)
print(f"POPULATION MODEL OK house_aggregate={aggregate} sword_manor_notables={len(ht_ids)} transfers={len(registry.get('transfers', []))}")
