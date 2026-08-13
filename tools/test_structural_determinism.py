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
    index = read_json("runtime/contracts/template-index.json")
    entries: dict[str, dict] = {}
    for shard_rel in index.get("shards", {}).values():
        shard = read_json(shard_rel)
        entries.update(shard.get("templates", {}))
    return entries


def decode_pointer(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        fail(f"invalid template pointer: {pointer}")
    return [segment.replace("~1", "/").replace("~0", "~") for segment in pointer[1:].split("/")]


def encode_segment(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def placeholder(type_spec):
    types = type_spec if isinstance(type_spec, list) else [type_spec]
    if "object" in types:
        return {}
    if "array" in types:
        return []
    return None


def put(root: dict, pointer: str, value) -> None:
    segments = decode_pointer(pointer)
    if not segments:
        if not isinstance(value, dict):
            fail("root blank skeleton must be object")
        return
    if "*" in segments:
        return
    current = root
    for idx, segment in enumerate(segments):
        last = idx == len(segments) - 1
        if not isinstance(current, dict):
            return
        if last:
            if segment not in current:
                current[segment] = value
            return
        if segment not in current:
            current[segment] = {}
        if isinstance(current[segment], list):
            return
        if current[segment] is None:
            current[segment] = {}
        if not isinstance(current[segment], dict):
            return
        current = current[segment]


def expected_blank(template: dict) -> dict:
    type_contracts = template.get("type_contracts")
    object_contracts = template.get("object_contracts")
    array_contracts = template.get("array_contracts")
    if not isinstance(type_contracts, dict) or not isinstance(object_contracts, dict) or not isinstance(array_contracts, dict):
        fail(f"template {template.get('target_schema')} missing structural contracts")

    root: dict = {}
    for pointer, type_spec in sorted(type_contracts.items(), key=lambda item: (item[0].count("/"), item[0])):
        if pointer == "" or "*" in decode_pointer(pointer):
            continue
        put(root, pointer, placeholder(type_spec))

    for pointer in sorted(array_contracts, key=lambda p: (p.count("/"), p)):
        if "*" not in decode_pointer(pointer):
            put(root, pointer, [])

    for pointer, contract in sorted(object_contracts.items(), key=lambda item: (item[0].count("/"), item[0])):
        if "*" in decode_pointer(pointer):
            continue
        if pointer:
            put(root, pointer, {})
        if not isinstance(contract, dict):
            fail(f"invalid object contract: {template.get('target_schema')}:{pointer}")
        for key in contract.get("allowed_keys", []):
            child_pointer = f"{pointer}/{encode_segment(key)}" if pointer else f"/{encode_segment(key)}"
            child_type = type_contracts.get(child_pointer)
            if child_type is not None:
                value = placeholder(child_type)
            elif child_pointer in array_contracts:
                value = []
            elif child_pointer in object_contracts:
                value = {}
            else:
                value = None
            put(root, child_pointer, value)

    return root


def first_difference(actual, expected, path=""):
    if type(actual) is not type(expected):
        return f"type mismatch at {path or '/'} actual={type(actual).__name__} expected={type(expected).__name__}"
    if isinstance(expected, dict):
        actual_keys = set(actual)
        expected_keys = set(expected)
        missing = sorted(expected_keys - actual_keys)
        if missing:
            return f"missing key at {path or '/'}: {missing[0]}"
        extra = sorted(actual_keys - expected_keys)
        if extra:
            return f"extra key at {path or '/'}: {extra[0]}"
        for key in sorted(expected):
            child = first_difference(actual[key], expected[key], f"{path}/{encode_segment(key)}")
            if child:
                return child
        return None
    if isinstance(expected, list):
        if actual != expected:
            return f"array mismatch at {path or '/'} actual={actual!r} expected={expected!r}"
        return None
    if actual != expected:
        return f"value mismatch at {path or '/'} actual={actual!r} expected={expected!r}"
    return None


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

    blank_index = read_json("runtime/contracts/blank-owner-index.json")
    owners = blank_index.get("owners")
    if not isinstance(owners, dict):
        fail("blank-owner-index owners must be an object")
    if set(owners) != set(mutable):
        missing = sorted(set(mutable) - set(owners))
        extra = sorted(set(owners) - set(mutable))
        fail(f"blank-owner-index coverage mismatch missing={missing} extra={extra}")

    for schema_id, rel in sorted(owners.items()):
        expected_path = f"runtime/contracts/blank-owners/{schema_id}.blank.json"
        if rel != expected_path:
            fail(f"blank skeleton path must be semantic and deterministic: {schema_id}: {rel}")
        actual = read_json(rel)
        expected = expected_blank(mutable[schema_id])
        if actual != expected:
            detail = first_difference(actual, expected) or "unknown difference"
            fail(f"blank skeleton drift: {schema_id}: {detail}")
        assert_blank_values(actual, rel)

    system_index = read_json("runtime/contracts/system-contract-index.json")
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
