#!/usr/bin/env python3
from __future__ import annotations
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"(?i)(?:[._-])v[0-9]+\b|\bv[0-9]+\b")

def tracked():
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [ROOT / p.decode("utf-8") for p in raw.split(b"\0") if p]

def walk(value, pointer=""):
    out = []
    if isinstance(value, dict):
        for key, item in value.items():
            out.extend(walk(item, f"{pointer}/{key}"))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            out.extend(walk(item, f"{pointer}/{idx}"))
    elif isinstance(value, str) and VERSION.search(value):
        out.append({"pointer": pointer or "/", "value": value})
    return out

hits = []
path_hits = []
for path in tracked():
    if not path.exists():
        continue
    rel = path.relative_to(ROOT).as_posix()
    gameplay = path.suffix.lower() == ".json" and (
        rel.startswith("state/") or (rel.startswith("data/") and not rel.startswith("data/runtime/"))
    )
    if not gameplay:
        continue
    if VERSION.search(rel):
        path_hits.append(rel)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        continue
    for hit in walk(data):
        hits.append({"path": rel, **hit})

template_hits = []
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
            template_hits.append({"path": path.relative_to(ROOT).as_posix(), "field": field, "value": value})

counts = Counter(hit["value"] for hit in hits)
report = {
    "gameplay_value_hit_count": len(hits),
    "gameplay_path_hit_count": len(path_hits),
    "template_hit_count": len(template_hits),
    "distinct_gameplay_values": [{"value": k, "count": v} for k, v in counts.most_common()],
    "gameplay_path_hits": sorted(path_hits),
    "template_hits": template_hits,
    "gameplay_hits": hits,
}
out = ROOT / "maintenance" / "gameplay-version-identifiers.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps({k: report[k] for k in ("gameplay_value_hit_count", "gameplay_path_hit_count", "template_hit_count")}, indent=2))
