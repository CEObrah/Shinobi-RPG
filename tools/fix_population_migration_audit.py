#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def write(rel, data):
    (ROOT / rel).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# Register the new mutable owner schema in the formal schema registry.
registry = read("schemas/registry.json")
registry["population-registry"] = "population-registry.schema.json"
write("schemas/registry.json", registry)

# Temporal coverage now follows the House aggregate owner plus sparse surviving notables,
# not the deleted ordinary person-lite identities.
coverage = read("data/runtime/coverage-requirements.json")
required = [x for x in coverage.get("required_owner_ids", []) if not str(x).startswith("ht.m")]
ht_ids = []
ht_dir = ROOT / "state/person/ht"
if ht_dir.exists():
    for path in sorted(ht_dir.glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        if d.get("id"):
            ht_ids.append(d["id"])
for owner_id in ["house.tang"] + ht_ids:
    if owner_id not in required:
        required.append(owner_id)
coverage["required_owner_ids"] = required
write("data/runtime/coverage-requirements.json", coverage)

# The central audit still encoded the former all-person-lite House model and its
# redundant projection. Point those checks at the canonical House owner and the new
# representation-neutral cohort class.
audit_path = ROOT / "tools/audit.py"
text = audit_path.read_text(encoding="utf-8")
text = text.replace(
    "if house.get('personal_force_model')!='all_members_individual_lite_or_exact':err('house_resolution_policy')",
    "if house.get('personal_force_model')!='aggregate_cohorts_with_sparse_sword_manor_notables':err('house_resolution_policy')",
)
text = text.replace(
    "if set(_eff.keys())!=set(('exact','individual_lite','unit')):err('development_representation_classes')",
    "if set(_eff.keys())!=set(('exact','individual_lite','unit','house_cohort')):err('development_representation_classes')",
)
text = text.replace(
    "_hu=rj(ROOT/'state/house/units.json') or {}",
    "_hu=rj(ROOT/'state/house/tang.json') or {}",
)
audit_path.write_text(text, encoding="utf-8")

print("Population migration audit assumptions updated")
print("required_house_notables=", ht_ids)
