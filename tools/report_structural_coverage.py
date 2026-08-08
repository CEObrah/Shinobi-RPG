#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def load_templates():
    idx = read_json("data/runtime/template-index.json")
    out = {}
    for shard_rel in idx.get("shards", {}).values():
        out.update(read_json(shard_rel).get("templates", {}))
    return out


def load_system_contracts():
    idx = read_json("data/runtime/system-contract-index.json")
    out = {}
    for system_id, rel in idx.get("systems", {}).items():
        out[system_id] = read_json(rel)
    return out


def normalize_path(value: str) -> str:
    return value.rstrip("/")


def overlaps(owner_dir: str, authority_path: str) -> bool:
    owner = normalize_path(owner_dir)
    authority = normalize_path(authority_path)
    return owner == authority or owner.startswith(authority + "/") or authority.startswith(owner + "/")


templates = load_templates()
systems = load_system_contracts()
coverage = defaultdict(list)
for system_id, contract in systems.items():
    for schema_id in contract.get("owner_templates", []):
        coverage[schema_id].append(system_id)

mutable = []
for schema_id, entry in sorted(templates.items()):
    template = read_json(entry["path"])
    if template.get("scope") != "mutable_state":
        continue
    dirs = template.get("current_directories", [])
    candidates = []
    evidence = {}
    for system_id, contract in systems.items():
        matches = []
        for owner_dir in dirs:
            for authority_path in contract.get("authority_paths", []):
                if isinstance(owner_dir, str) and isinstance(authority_path, str) and overlaps(owner_dir, authority_path):
                    matches.append([owner_dir, authority_path])
        if matches:
            candidates.append(system_id)
            evidence[system_id] = matches
    mutable.append({
        "schema": schema_id,
        "template_path": entry["path"],
        "current_directories": dirs,
        "system_contracts": sorted(coverage.get(schema_id, [])),
        "path_authority_candidates": sorted(candidates),
        "path_authority_evidence": evidence,
    })

state_schemas = defaultdict(list)
for path in sorted((ROOT / "state").rglob("*.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("schema"), str):
        state_schemas[data["schema"]].append(path.relative_to(ROOT).as_posix())

missing = [x for x in mutable if not x["system_contracts"]]
report = {
    "mutable_template_count": len(mutable),
    "instantiated_state_schema_count": len(state_schemas),
    "mutable_templates": mutable,
    "mutable_without_system_contract": [x["schema"] for x in missing],
    "missing_with_unique_path_authority": {
        x["schema"]: x["path_authority_candidates"][0]
        for x in missing if len(x["path_authority_candidates"]) == 1
    },
    "missing_with_ambiguous_path_authority": {
        x["schema"]: x["path_authority_candidates"]
        for x in missing if len(x["path_authority_candidates"]) > 1
    },
    "missing_without_path_authority": [
        x["schema"] for x in missing if not x["path_authority_candidates"]
    ],
    "state_schema_without_mutable_template": sorted(
        schema for schema in state_schemas
        if schema not in templates or read_json(templates[schema]["path"]).get("scope") != "mutable_state"
    ),
    "mutable_template_not_instantiated": sorted(
        x["schema"] for x in mutable if x["schema"] not in state_schemas
    ),
}

out = ROOT / "maintenance" / "structural-coverage.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps({
    "mutable_template_count": report["mutable_template_count"],
    "instantiated_state_schema_count": report["instantiated_state_schema_count"],
    "mutable_without_system_contract": report["mutable_without_system_contract"],
    "missing_with_unique_path_authority": report["missing_with_unique_path_authority"],
    "missing_with_ambiguous_path_authority": report["missing_with_ambiguous_path_authority"],
    "missing_without_path_authority": report["missing_without_path_authority"],
    "state_schema_without_mutable_template": report["state_schema_without_mutable_template"],
    "mutable_template_not_instantiated": report["mutable_template_not_instantiated"],
}, indent=2))
