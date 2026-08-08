#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(message)


def read_json(rel: str):
    path = ROOT / rel
    if not path.exists():
        fail(f"missing file: {rel}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"invalid json: {rel}: {exc}")


def load_templates() -> dict[str, dict]:
    index = read_json("data/runtime/template-index.json")
    entries: dict[str, dict] = {}
    for shard_rel in index.get("shards", {}).values():
        shard = read_json(shard_rel)
        entries.update(shard.get("templates", {}))
    return entries


def decode_pointer(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        fail(f"invalid template pointer: {pointer}")
    return [segment.replace("~1", "/").replace("~0", "~") for segment in pointer[1:].split("/") if segment != ""]


def placeholder(type_spec):
    types = type_spec if isinstance(type_spec, list) else [type_spec]
    if "object" in types:
        return {}
    if "array" in types:
        return []
    return None


def expected_blank(template: dict) -> dict:
    field_types = template.get("field_types")
    if not isinstance(field_types, dict):
        fail(f"template {template.get('target_schema')} missing field_types")
    root: dict = {}
    ordered = sorted(field_types.items(), key=lambda item: (item[0].count("/"), item[0]))
    for pointer, type_spec in ordered:
        segments = decode_pointer(pointer)
        if not segments or "*" in segments:
            continue
        current = root
        blocked = False
        for idx, segment in enumerate(segments):
            last = idx == len(segments) - 1
            if not isinstance(current, dict):
                blocked = True
                break
            if last:
                current.setdefault(segment, placeholder(type_spec))
                continue
            if segment not in current:
                current[segment] = {}
            if isinstance(current[segment], list):
                blocked = True
                break
            if current[segment] is None:
                current[segment] = {}
            if not isinstance(current[segment], dict):
                fail(f"template {template.get('target_schema')} has incompatible nested pointer {pointer}")
            current = current[segment]
        if blocked:
            continue
    return root


def assert_blank_values(value, path: str) -> None:
    if value is None:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            assert_blank_values(child, f"{path}/{key}")
        return
    if isinstance(value, list):
        if value:
            fail(f"blank skeleton array must be empty: {path}")
        return
    fail(f"blank skeleton contains campaign/default value at {path}: {value!r}")


def main() -> None:
    template_entries = load_templates()
    mutable: dict[str, dict] = {}
    for schema_id, entry in template_entries.items():
        template = read_json(entry["path"])
        if template.get("scope") != "mutable_state":
            continue
        if template.get("target_schema") != schema_id:
            fail(f"template-index target mismatch: {schema_id}")
        if template.get("unknown_key_policy") != "reject":
            fail(f"mutable template must reject unknown keys: {schema_id}")
        mutable[schema_id] = template

    blank_index = read_json("data/runtime/blank-owner-index.json")
    owners = blank_index.get("owners")
    if not isinstance(owners, dict):
        fail("blank-owner-index owners must be an object")
    if set(owners) != set(mutable):
        missing = sorted(set(mutable) - set(owners))
        extra = sorted(set(owners) - set(mutable))
        fail(f"blank-owner-index coverage mismatch missing={missing} extra={extra}")

    for schema_id, rel in sorted(owners.items()):
        expected_path = f"data/runtime/blank-owners/{schema_id}.blank.json"
        if rel != expected_path:
            fail(f"blank skeleton path must be semantic and deterministic: {schema_id}: {rel}")
        actual = read_json(rel)
        expected = expected_blank(mutable[schema_id])
        if actual != expected:
            fail(f"blank skeleton drift: {schema_id}")
        assert_blank_values(actual, rel)

    system_index = read_json("data/runtime/system-contract-index.json")
    coverage: defaultdict[str, list[str]] = defaultdict(list)
    for system_id, rel in system_index.get("systems", {}).items():
        contract = read_json(rel)
        if contract.get("system_id") != system_id:
            fail(f"system contract id mismatch: {system_id}: {rel}")
        owners_field = contract.get("owner_templates")
        if not isinstance(owners_field, list):
            fail(f"system contract owner_templates must be array: {system_id}")
        for schema_id in owners_field:
            if schema_id in mutable:
                coverage[schema_id].append(system_id)

    uncovered = sorted(schema_id for schema_id in mutable if not coverage.get(schema_id))
    if uncovered:
        fail(f"mutable schemas without registered system update contract: {uncovered}")

    print(
        "STRUCTURAL DETERMINISM OK",
        json.dumps({
            "mutable_owner_types": len(mutable),
            "blank_skeletons": len(owners),
            "contract_covered_owner_types": len(coverage),
        }, sort_keys=True),
    )


if __name__ == "__main__":
    main()
