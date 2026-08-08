#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_TOKEN = re.compile(r"(?i)(?:[._-])v(3[0-9])\b|\bv(3[0-9])\b")
ANY_VERSION_TOKEN = re.compile(r"(?i)(?:[._-])v([0-9]+)\b|\bv([0-9]+)\b")
RULE_HISTORY = re.compile(r"(?i)\b(v[0-9]+|version(?:ed|ing)?|legacy|migration|migrat(?:e|ed|ion|ing)|backward(?:s)? compatibility|previous schema|old schema|release history|superseded behavior)\b")
TEXT_SUFFIXES = {".json", ".md", ".py", ".yml", ".yaml", ".txt", ".toml", ".ini", ".cfg"}
GAMEPLAY_ROOTS = ("state/", "data/", "rules/")
VERSION_KEYS = {"version", "schema_version", "game_version", "rules_version", "data_version"}

def tracked_files() -> list[Path]:
    out = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [ROOT / p.decode("utf-8") for p in out.split(b"\0") if p]

def stable_name(path: str) -> str:
    s = re.sub(r"(?i)\.v3[0-9](?=\.)", "", path)
    s = re.sub(r"(?i)-v3[0-9](?=\.)", "", s)
    s = re.sub(r"(?i)_v3[0-9](?=\.)", "", s)
    return s

def walk_version_keys(value, pointer=""):
    hits = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{pointer}/{key}"
            if str(key).lower() in VERSION_KEYS:
                hits.append(child)
            hits.extend(walk_version_keys(item, child))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            hits.extend(walk_version_keys(item, f"{pointer}/{idx}"))
    return hits

paths = tracked_files()
path_candidates: list[dict] = []
content_hits: list[dict] = []
schema_values: Counter[str] = Counter()
template_targets: Counter[str] = Counter()
rule_history_hits: list[dict] = []
rename_targets: defaultdict[str, list[str]] = defaultdict(list)
all_gameplay_versioned_paths: list[str] = []
gameplay_version_keys: list[dict] = []

for path in paths:
    rel = path.relative_to(ROOT).as_posix()
    renamed = stable_name(rel)
    if renamed != rel:
        path_candidates.append({"path": rel, "stable_path": renamed})
        rename_targets[renamed].append(rel)
    if (rel in {"RUNTIME.md", "VOICE.md"} or rel.startswith(GAMEPLAY_ROOTS)) and ANY_VERSION_TOKEN.search(rel):
        all_gameplay_versioned_paths.append(rel)
    if path.suffix.lower() not in TEXT_SUFFIXES:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    hits = list(RELEASE_TOKEN.finditer(text))
    if hits:
        samples = []
        for lineno, line in enumerate(text.splitlines(), 1):
            if RELEASE_TOKEN.search(line):
                samples.append({"line": lineno, "text": line[:500]})
                if len(samples) >= 8:
                    break
        content_hits.append({"path": rel, "count": len(hits), "samples": samples})
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            schema = data.get("schema")
            if isinstance(schema, str) and RELEASE_TOKEN.search(schema):
                schema_values[schema] += 1
            target = data.get("target_schema")
            if isinstance(target, str) and RELEASE_TOKEN.search(target):
                template_targets[target] += 1
            if rel.startswith(("state/", "data/")):
                key_hits = walk_version_keys(data)
                if key_hits:
                    gameplay_version_keys.append({"path": rel, "pointers": key_hits})
    if rel == "RUNTIME.md" or rel == "VOICE.md" or rel.startswith("rules/"):
        for lineno, line in enumerate(text.splitlines(), 1):
            if RULE_HISTORY.search(line):
                rule_history_hits.append({"path": rel, "line": lineno, "text": line[:700]})

collisions = [
    {"stable_path": target, "sources": sorted(sources)}
    for target, sources in sorted(rename_targets.items())
    if len(sources) > 1 or (ROOT / target).exists()
]

report = {
    "release_token_scope": "v30-v39 release generations",
    "tracked_file_count": len(paths),
    "versioned_path_count": len(path_candidates),
    "versioned_content_file_count": len(content_hits),
    "all_gameplay_versioned_path_count": len(all_gameplay_versioned_paths),
    "all_gameplay_versioned_paths": sorted(all_gameplay_versioned_paths),
    "gameplay_version_key_file_count": len(gameplay_version_keys),
    "gameplay_version_keys": gameplay_version_keys,
    "schema_values": dict(sorted(schema_values.items())),
    "template_target_values": dict(sorted(template_targets.items())),
    "rename_collisions": collisions,
    "path_candidates": path_candidates,
    "content_hits": content_hits,
    "rule_history_hits": rule_history_hits,
}

out = ROOT / "maintenance" / "gameplay-version-audit.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps({
    "release_versioned_paths": len(path_candidates),
    "release_content_files": len(content_hits),
    "all_gameplay_versioned_paths": len(all_gameplay_versioned_paths),
    "gameplay_version_key_files": len(gameplay_version_keys),
    "schema_ids": len(schema_values),
    "collisions": len(collisions),
    "rule_history_hits": len(rule_history_hits),
}, indent=2))
