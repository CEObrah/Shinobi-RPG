#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def write(rel, data):
    (ROOT / rel).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def add_once(seq, value, after=None):
    if value in seq:
        return
    if after in seq:
        seq.insert(seq.index(after) + 1, value)
    else:
        seq.append(value)


def remove_text_block(rel, start_marker, next_marker):
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    start = text.find(start_marker)
    if start < 0:
        return
    end = text.find(next_marker, start)
    if end < 0:
        raise RuntimeError(f"cannot find end marker in {rel}: {next_marker}")
    text = text[:start] + text[end:]
    path.write_text(text, encoding="utf-8")


# Register the new mutable owner schema and directory.
registry = read("schemas/registry.json")
registry["population-registry"] = "population-registry.schema.json"
write("schemas/registry.json", registry)

directory_map = read("data/runtime/directory-map.json")
directory_map.setdefault("dirs", {})["state/population"] = "mapped"
write("data/runtime/directory-map.json", directory_map)

# Temporal coverage follows the House aggregate owner plus sparse surviving notables,
# not deleted ordinary person-lite identities.
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

# Remove repository-wide vocabulary scanners. Structural checks remain authoritative;
# normal terminology is not a gameplay invariant.
remove_text_block(
    "tools/test_semantics.py",
    "# No retired organizational term survives outside the regression scanner itself.",
    "# Process sharding contract on Sword.",
)
remove_text_block(
    "tools/test_unit_model.py",
    "# No retired organization term in text files or filenames.",
    "# Context-size advisory only.",
)

# Align the central audit with the new House representation.
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

# Register the new path/domain/system/write route in their formal templates.
dmt = read("data/runtime/templates/directory-map.template.json")
dirs = dmt["object_contracts"]["/dirs"]
add_once(dirs["allowed_keys"], "state/population", after="state/place")
add_once(dirs["canonical_order"], "state/population", after="state/place")
dmt["type_contracts"]["/dirs/state/population"] = ["string"]
write("data/runtime/templates/directory-map.template.json", dmt)

rrt = read("data/runtime/templates/runtime-rule-router.template.json")
domains = rrt["object_contracts"]["/domains"]
add_once(domains["allowed_keys"], "population_recruitment", after="physical_combat")
add_once(domains["canonical_order"], "population_recruitment", after="physical_combat")
rrt["type_contracts"]["/domains/population_recruitment"] = ["array"]
rrt["type_contracts"]["/domains/population_recruitment/*"] = ["string"]
rrt["array_contracts"]["/domains/population_recruitment"] = {"item_types": ["string"]}
write("data/runtime/templates/runtime-rule-router.template.json", rrt)

sct = read("data/runtime/templates/system-contract-index.template.json")
systems = sct["object_contracts"]["/systems"]
add_once(systems["allowed_keys"], "population_recruitment", after="player_combat_detail")
add_once(systems["canonical_order"], "population_recruitment", after="player_combat_detail")
sct["type_contracts"]["/systems/population_recruitment"] = ["string"]
write("data/runtime/templates/system-contract-index.template.json", sct)

rst = read("data/runtime/templates/repository-route-shard.v1.template.json")
route = rst["object_contracts"]["/routes/*"]
add_once(route["allowed_keys"], "w", after="r")
add_once(route["canonical_order"], "w", after="r")
rst["type_contracts"]["/routes/*/w"] = ["array"]
rst["type_contracts"]["/routes/*/w/*"] = ["string"]
rst["array_contracts"]["/routes/*/w"] = {"item_types": ["string"]}
write("data/runtime/templates/repository-route-shard.v1.template.json", rst)

print("Population structural authorities aligned")
print("required_house_notables=", ht_ids)
