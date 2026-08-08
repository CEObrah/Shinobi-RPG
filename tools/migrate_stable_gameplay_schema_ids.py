#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"(?i)(?:[._-])v[0-9]+\b")
TEXT_SUFFIXES = {".json", ".md", ".py", ".yml", ".yaml", ".txt", ".toml", ".ini", ".cfg"}


def tracked() -> list[Path]:
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [ROOT / p.decode("utf-8") for p in raw.split(b"\0") if p]


def stable(value: str) -> str:
    return VERSION.sub("", value)


def load_no_dupes(path: Path):
    def hook(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"duplicate JSON key: {path.relative_to(ROOT)}:{key}")
            out[key] = value
        return out
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=hook)


def walk_strings(value, pointer=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk_strings(item, f"{pointer}/{key}")
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            yield from walk_strings(item, f"{pointer}/{idx}")
    elif isinstance(value, str):
        yield pointer or "/", value


# 1. Derive the migration set strictly from gameplay JSON. Versioned strings
# are permitted here only as schema identifiers; any other use fails closed.
source_ids: set[str] = set()
all_schema_ids: set[str] = set()
for path in tracked():
    if not path.exists() or path.suffix.lower() != ".json":
        continue
    rel = path.relative_to(ROOT).as_posix()
    gameplay = rel.startswith("state/") or (rel.startswith("data/") and not rel.startswith("data/runtime/"))
    if not gameplay:
        continue
    data = load_no_dupes(path)
    for pointer, value in walk_strings(data):
        if pointer.endswith("/schema") or pointer == "/schema":
            all_schema_ids.add(value)
        if VERSION.search(value):
            if not (pointer.endswith("/schema") or pointer == "/schema"):
                raise SystemExit(f"versioned gameplay value outside schema field:{rel}:{pointer}:{value}")
            if stable(value) == value:
                raise SystemExit(f"unable to normalize gameplay schema:{value}")
            source_ids.add(value)

if not source_ids:
    print("no versioned gameplay schema IDs remain")
    raise SystemExit(0)

by_target: defaultdict[str, list[str]] = defaultdict(list)
for source in sorted(source_ids):
    by_target[stable(source)].append(source)
collisions = {target: values for target, values in by_target.items() if len(values) > 1}
if collisions:
    raise SystemExit(f"gameplay schema normalization collisions:{collisions}")

mapping = {source: stable(source) for source in sorted(source_ids, key=len, reverse=True)}
for source, target in mapping.items():
    if target in all_schema_ids and target not in source_ids:
        raise SystemExit(f"stable gameplay schema target already in use:{source}->{target}")

# 2. Ensure schema registry aliases can be made stable without overwriting an
# unrelated live contract. Older unused generation keys may remain tooling-only.
registry_path = ROOT / "schemas/registry.json"
registry = load_no_dupes(registry_path) if registry_path.exists() else {}
if isinstance(registry, dict):
    for source, target in mapping.items():
        if source not in registry:
            continue
        existing = registry.get(target)
        if existing is not None and existing != registry[source]:
            raise SystemExit(f"schema registry stable-key collision:{source}->{target}:{existing}!={registry[source]}")

# 3. Rename gameplay-target template files to stable semantic filenames. Their
# source schema filenames may retain technical generations because those are
# implementation contracts, not gameplay-tree identity.
renames: dict[str, str] = {}
for path in sorted((ROOT / "data/runtime/templates").glob("*.json")):
    data = load_no_dupes(path)
    dirs = data.get("current_directories", []) if isinstance(data, dict) else []
    gameplay_target = isinstance(dirs, list) and any(
        isinstance(d, str) and (d.startswith("state/") or (d.startswith("data/") and not d.startswith("data/runtime/")))
        for d in dirs
    )
    if not gameplay_target:
        continue
    target_schema = data.get("target_schema")
    if not isinstance(target_schema, str) or target_schema not in mapping:
        continue
    rel = path.relative_to(ROOT).as_posix()
    new_name = VERSION.sub("", path.name)
    new_rel = path.with_name(new_name).relative_to(ROOT).as_posix()
    if new_rel != rel:
        if (ROOT / new_rel).exists():
            raise SystemExit(f"gameplay template filename collision:{rel}->{new_rel}")
        if new_rel in renames.values():
            raise SystemExit(f"duplicate gameplay template target path:{new_rel}")
        renames[rel] = new_rel

for old_rel, new_rel in renames.items():
    (ROOT / new_rel).parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(["git", "mv", old_rel, new_rel], cwd=ROOT)

# 4. Replace the exact live gameplay schema IDs everywhere they are referenced.
# This updates gameplay data, target templates, template-index keys, schema
# registry keys, schema consts, validators, routers, and documentation without
# touching unrelated infrastructure generations.
for path in tracked():
    if not path.exists() or path.suffix.lower() not in TEXT_SUFFIXES:
        continue
    text = path.read_text(encoding="utf-8")
    new = text
    for source, target in mapping.items():
        new = new.replace(source, target)
    for old_rel, new_rel in renames.items():
        new = new.replace(old_rel, new_rel)
        new = new.replace(Path(old_rel).name, Path(new_rel).name)
    if new != text:
        path.write_text(new, encoding="utf-8")

# 5. Verify every JSON file still parses with unique keys and that gameplay JSON
# now contains no version identifiers in any string value.
for path in tracked():
    if not path.exists() or path.suffix.lower() != ".json":
        continue
    data = load_no_dupes(path)
    rel = path.relative_to(ROOT).as_posix()
    gameplay = rel.startswith("state/") or (rel.startswith("data/") and not rel.startswith("data/runtime/"))
    if gameplay:
        for pointer, value in walk_strings(data):
            if VERSION.search(value):
                raise SystemExit(f"version identifier remains in gameplay tree:{rel}:{pointer}:{value}")

# 6. Gameplay-target template identity must also be stable.
for path in sorted((ROOT / "data/runtime/templates").glob("*.json")):
    data = load_no_dupes(path)
    dirs = data.get("current_directories", []) if isinstance(data, dict) else []
    gameplay_target = isinstance(dirs, list) and any(
        isinstance(d, str) and (d.startswith("state/") or (d.startswith("data/") and not d.startswith("data/runtime/")))
        for d in dirs
    )
    if not gameplay_target:
        continue
    for field in ("target_schema", "template_id"):
        value = data.get(field)
        if isinstance(value, str) and VERSION.search(value):
            raise SystemExit(f"versioned gameplay-template identity remains:{path.relative_to(ROOT)}:{field}:{value}")

print(json.dumps({
    "migrated_schema_ids": len(mapping),
    "renamed_gameplay_templates": len(renames),
    "sample_mapping": dict(list(mapping.items())[:20]),
}, indent=2))
