#!/usr/bin/env python3
from __future__ import annotations

import hashlib
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


def is_gameplay_target(template: dict) -> bool:
    dirs = template.get("current_directories", []) if isinstance(template, dict) else []
    return isinstance(dirs, list) and any(
        isinstance(d, str) and (d.startswith("state/") or (d.startswith("data/") and not d.startswith("data/runtime/")))
        for d in dirs
    )


# 1. Derive the live migration set from both instantiated gameplay owners and
# registered gameplay-target templates. Versioned values in gameplay JSON are
# permitted only in /schema fields; any other use fails closed.
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
            source_ids.add(value)

template_paths: list[Path] = []
all_template_targets: set[str] = set()
for path in sorted((ROOT / "data/runtime/templates").glob("*.json")):
    data = load_no_dupes(path)
    if not is_gameplay_target(data):
        continue
    template_paths.append(path)
    target_schema = data.get("target_schema")
    if isinstance(target_schema, str):
        all_template_targets.add(target_schema)
        if VERSION.search(target_schema):
            source_ids.add(target_schema)

if not source_ids:
    print("no versioned gameplay schema IDs or gameplay-target schemas remain")
    raise SystemExit(0)

by_target: defaultdict[str, list[str]] = defaultdict(list)
for source in sorted(source_ids):
    target = stable(source)
    if target == source:
        raise SystemExit(f"unable to normalize gameplay schema:{source}")
    by_target[target].append(source)
collisions = {target: values for target, values in by_target.items() if len(values) > 1}
if collisions:
    raise SystemExit(f"gameplay schema normalization collisions:{collisions}")

mapping = {source: stable(source) for source in sorted(source_ids, key=len, reverse=True)}
for source, target in mapping.items():
    if target in all_schema_ids and target not in source_ids:
        raise SystemExit(f"stable gameplay schema target already instantiated:{source}->{target}")
    if target in all_template_targets and target not in source_ids:
        raise SystemExit(f"stable gameplay target schema already registered:{source}->{target}")

# 2. Ensure the schema registry can expose stable live aliases without
# overwriting an unrelated contract. Older unused generation keys may remain
# tooling-only.
registry_path = ROOT / "schemas/registry.json"
registry = load_no_dupes(registry_path) if registry_path.exists() else {}
if isinstance(registry, dict):
    for source, target in mapping.items():
        if source not in registry:
            continue
        existing = registry.get(target)
        if existing is not None and existing != registry[source]:
            raise SystemExit(f"schema registry stable-key collision:{source}->{target}:{existing}!={registry[source]}")

# 3. Rename gameplay-target template files to stable semantic filenames.
renames: dict[str, str] = {}
for path in template_paths:
    data = load_no_dupes(path)
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

# 4. Replace exact live gameplay schema IDs everywhere they are referenced.
# This rewires data, template-index keys, schema registry keys, schema consts,
# validators, routers, and documentation without renaming unrelated tooling
# generations.
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

# 4b. Unit battle kernels are derived caches. Their source hash is the exact
# byte hash of each unit's authoritative stats_ref capability document. The
# schema rewrite changes those bytes, so refresh only that derived hash after
# proving the kernel still belongs to the same unit.
refreshed_kernels = 0
for unit_path in sorted((ROOT / "state/unit").glob("*.json")):
    unit = load_no_dupes(unit_path)
    stats_ref = unit.get("stats_ref")
    kernel_ref = unit.get("battle_kernel_ref")
    if not isinstance(stats_ref, str) or not isinstance(kernel_ref, str):
        continue
    stats_path = ROOT / stats_ref
    kernel_path = ROOT / kernel_ref
    if not stats_path.exists() or not kernel_path.exists():
        continue
    kernel = load_no_dupes(kernel_path)
    if kernel.get("unit_id") != unit.get("id"):
        raise SystemExit(
            f"unit kernel owner mismatch during refresh:{unit_path.relative_to(ROOT)}:{kernel_ref}"
        )
    digest = hashlib.sha256(stats_path.read_bytes()).hexdigest()
    if kernel.get("source_sha256") != digest:
        kernel["source_sha256"] = digest
        kernel_path.write_text(
            json.dumps(kernel, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        refreshed_kernels += 1

# 5. Normalize gameplay-target template_id independently. This catches a
# versioned template identity even if its target schema was already stable.
for path in sorted((ROOT / "data/runtime/templates").glob("*.json")):
    data = load_no_dupes(path)
    if not is_gameplay_target(data):
        continue
    template_id = data.get("template_id")
    if isinstance(template_id, str) and VERSION.search(template_id):
        data["template_id"] = stable(template_id)
        path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")

# 6. Verify every JSON file still parses with unique keys and gameplay JSON now
# contains no version identifiers in any string value.
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

# 7. Gameplay-target template identity must also be stable.
for path in sorted((ROOT / "data/runtime/templates").glob("*.json")):
    data = load_no_dupes(path)
    if not is_gameplay_target(data):
        continue
    for field in ("target_schema", "template_id"):
        value = data.get(field)
        if isinstance(value, str) and VERSION.search(value):
            raise SystemExit(f"versioned gameplay-template identity remains:{path.relative_to(ROOT)}:{field}:{value}")

print(json.dumps({
    "migrated_schema_ids": len(mapping),
    "renamed_gameplay_templates": len(renames),
    "refreshed_unit_kernels": refreshed_kernels,
    "sample_mapping": dict(list(mapping.items())[:20]),
}, indent=2))
