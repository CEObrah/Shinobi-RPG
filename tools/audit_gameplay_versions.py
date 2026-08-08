#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_TOKEN = re.compile(r"(?i)(?:[._-])v(3[0-9])\b|\bv(3[0-9])\b")
RULE_HISTORY = re.compile(r"(?i)\b(v3[0-9]|version(?:ed|ing)?|legacy|migration|migrat(?:e|ed|ion|ing)|backward(?:s)? compatibility|previous schema|old schema|release history)\b")
TEXT_SUFFIXES = {".json", ".md", ".py", ".yml", ".yaml", ".txt", ".toml", ".ini", ".cfg"}

def tracked_files() -> list[Path]:
    out = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [ROOT / p.decode("utf-8") for p in out.split(b"\0") if p]

def stable_name(path: str) -> str:
    # Remove only campaign release-generation tags. Low-level v1/v2 protocol
    # generations are intentionally outside this audit.
    s = re.sub(r"(?i)\.v3[0-9](?=\.)", "", path)
    s = re.sub(r"(?i)-v3[0-9](?=\.)", "", s)
    s = re.sub(r"(?i)_v3[0-9](?=\.)", "", s)
    return s

paths = tracked_files()
path_candidates: list[dict] = []
content_hits: list[dict] = []
schema_values: Counter[str] = Counter()
template_targets: Counter[str] = Counter()
rule_history_hits: list[dict] = []
rename_targets: defaultdict[str, list[str]] = defaultdict(list)

for path in paths:
    rel = path.relative_to(ROOT).as_posix()
    renamed = stable_name(rel)
    if renamed != rel:
        path_candidates.append({"path": rel, "stable_path": renamed})
        rename_targets[renamed].append(rel)
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
    "release_token_scope": "v30-v39 only; v1/v2 infrastructure protocols excluded",
    "tracked_file_count": len(paths),
    "versioned_path_count": len(path_candidates),
    "versioned_content_file_count": len(content_hits),
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
    "versioned_paths": len(path_candidates),
    "content_files": len(content_hits),
    "schema_ids": len(schema_values),
    "collisions": len(collisions),
    "rule_history_hits": len(rule_history_hits),
}, indent=2))
