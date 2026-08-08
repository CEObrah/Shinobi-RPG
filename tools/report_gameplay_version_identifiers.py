#!/usr/bin/env python3
from __future__ import annotations
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"(?i)(?:[._-])v([0-9]+)\b")
WHOLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

def tracked():
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [ROOT / p.decode("utf-8") for p in raw.split(b"\0") if p]

def stable(value: str) -> str:
    return VERSION.sub("", value)

def walk(value, pointer=""):
    out = []
    if isinstance(value, dict):
        for key, item in value.items():
            out.extend(walk(item, f"{pointer}/{key}"))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            out.extend(walk(item, f"{pointer}/{idx}"))
    elif isinstance(value, str) and VERSION.search(value):
        out.append((pointer or "/", value))
    return out

hits = []
pointer_tail_counts = Counter()
value_counts = Counter()
value_paths: defaultdict[str, set[str]] = defaultdict(set)
value_pointer_tails: defaultdict[str, Counter] = defaultdict(Counter)

for path in tracked():
    if not path.exists() or path.suffix.lower() != ".json":
        continue
    rel = path.relative_to(ROOT).as_posix()
    gameplay = rel.startswith("state/") or (rel.startswith("data/") and not rel.startswith("data/runtime/"))
    if not gameplay:
        continue
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        continue
    for pointer, value in walk(data):
        tail = pointer.rsplit("/", 1)[-1] if "/" in pointer else pointer
        hits.append((rel, pointer, value))
        pointer_tail_counts[tail] += 1
        value_counts[value] += 1
        value_paths[value].add(rel)
        value_pointer_tails[value][tail] += 1

normalized_groups: defaultdict[str, set[str]] = defaultdict(set)
for value in value_counts:
    if WHOLE_ID.fullmatch(value):
        normalized_groups[stable(value)].add(value)

collisions = []
for target, sources in sorted(normalized_groups.items()):
    if len(sources) > 1:
        collisions.append({
            "stable": target,
            "sources": [
                {
                    "value": source,
                    "count": value_counts[source],
                    "pointer_tails": dict(value_pointer_tails[source].most_common()),
                    "sample_paths": sorted(value_paths[source])[:8],
                }
                for source in sorted(sources)
            ],
        })

single_generation = []
for target, sources in sorted(normalized_groups.items()):
    if len(sources) != 1:
        continue
    source = next(iter(sources))
    single_generation.append({
        "source": source,
        "stable": target,
        "count": value_counts[source],
        "pointer_tails": dict(value_pointer_tails[source].most_common()),
        "sample_paths": sorted(value_paths[source])[:5],
    })
single_generation.sort(key=lambda x: (-x["count"], x["source"]))

template_rows = []
template_groups: defaultdict[str, set[str]] = defaultdict(set)
for path in sorted((ROOT / "data/runtime/templates").glob("*.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    dirs = data.get("current_directories", []) if isinstance(data, dict) else []
    gameplay = isinstance(dirs, list) and any(
        isinstance(d, str) and (d.startswith("state/") or (d.startswith("data/") and not d.startswith("data/runtime/")))
        for d in dirs
    )
    if not gameplay:
        continue
    for field in ("target_schema", "template_id", "source_schema"):
        value = data.get(field)
        if isinstance(value, str) and VERSION.search(value):
            row = {"path": path.relative_to(ROOT).as_posix(), "field": field, "value": value, "stable": stable(value)}
            template_rows.append(row)
            if field == "target_schema" and WHOLE_ID.fullmatch(value):
                template_groups[stable(value)].add(value)

template_collisions = [
    {"stable": target, "sources": sorted(sources)}
    for target, sources in sorted(template_groups.items()) if len(sources) > 1
]

registry_path = ROOT / "schemas/registry.json"
registry_collisions = []
registry_versioned = []
if registry_path.exists():
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    groups: defaultdict[str, list[dict]] = defaultdict(list)
    for key, filename in registry.items():
        if isinstance(key, str) and VERSION.search(key):
            row = {"key": key, "filename": filename, "stable_key": stable(key), "stable_filename": stable(str(filename))}
            registry_versioned.append(row)
            groups[row["stable_key"]].append(row)
    registry_collisions = [
        {"stable": target, "entries": rows}
        for target, rows in sorted(groups.items()) if len(rows) > 1
    ]

schema_field_values = Counter(value for _, pointer, value in hits if pointer.endswith("/schema") or pointer == "/schema")
non_schema_values = Counter(value for _, pointer, value in hits if not (pointer.endswith("/schema") or pointer == "/schema"))

summary = {
    "gameplay_value_hit_count": len(hits),
    "distinct_value_count": len(value_counts),
    "whole_identifier_distinct_count": len(normalized_groups),
    "single_generation_identifier_count": len(single_generation),
    "collision_group_count": len(collisions),
    "template_collision_group_count": len(template_collisions),
    "registry_collision_group_count": len(registry_collisions),
    "pointer_tail_counts": dict(pointer_tail_counts.most_common()),
    "top_schema_values": [{"value": k, "count": v} for k, v in schema_field_values.most_common(100)],
    "top_non_schema_values": [{"value": k, "count": v} for k, v in non_schema_values.most_common(100)],
    "collisions": collisions,
    "template_collisions": template_collisions,
    "registry_collisions": registry_collisions,
    "registry_versioned": registry_versioned,
    "single_generation_top": single_generation[:300],
    "template_hits": template_rows,
}
out = ROOT / "maintenance" / "gameplay-version-classification.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps({
    "gameplay_value_hit_count": len(hits),
    "distinct_value_count": len(value_counts),
    "collision_group_count": len(collisions),
    "template_collision_group_count": len(template_collisions),
    "registry_collision_group_count": len(registry_collisions),
}, indent=2))
