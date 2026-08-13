#!/usr/bin/env python3
from __future__ import annotations
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".json", ".md", ".py", ".yml", ".yaml", ".txt", ".toml", ".ini", ".cfg"}
VERSION_KEYS = {"version", "schema_version", "game_version", "rules_version", "data_version"}
RELEASE = re.compile(r"(?i)(?:[._-])v3[0-9]\b|\bv3[0-9]\b")
GAMEPLAY_VERSION = re.compile(r"(?i)(?:[._-])v[0-9]+\b|\bv[0-9]+\b")
RULE_HISTORY = re.compile(r"(?i)\b(legacy|migration|superseded behavior|previous version|release history)\b")
errors: list[str] = []

def tracked() -> list[Path]:
    """Return repository files in Git checkouts or unpacked release archives."""
    if (ROOT / ".git").exists():
        raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        return [ROOT / p.decode("utf-8") for p in raw.split(b"\0") if p]
    ignored = {".git", ".pytest_cache", "__pycache__"}
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file() and not any(part in ignored for part in path.relative_to(ROOT).parts)
    ]

def version_key_pointers(value, pointer=""):
    hits = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{pointer}/{key}"
            if str(key).lower() in VERSION_KEYS:
                hits.append(child)
            hits.extend(version_key_pointers(item, child))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            hits.extend(version_key_pointers(item, f"{pointer}/{idx}"))
    return hits

def version_string_pointers(value, pointer=""):
    hits = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{pointer}/{key}"
            hits.extend(version_string_pointers(item, child))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            hits.extend(version_string_pointers(item, f"{pointer}/{idx}"))
    elif isinstance(value, str) and GAMEPLAY_VERSION.search(value):
        hits.append((pointer or "/", value))
    return hits

for path in tracked():
    if not path.exists():
        continue
    rel = path.relative_to(ROOT).as_posix()
    if RELEASE.search(rel):
        errors.append(f"release_generation_in_path:{rel}")
    if path.suffix.lower() in TEXT_SUFFIXES:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = ""
        if RELEASE.search(text):
            errors.append(f"release_generation_in_content:{rel}")
        if (rel in {"RUNTIME.md", "VOICE.md"} or rel.startswith("game/rules/text/")) and RULE_HISTORY.search(text):
            errors.append(f"release_history_language_in_rules:{rel}")
    gameplay_json = path.suffix.lower() == ".json" and (
        rel.startswith("state/") or (rel.startswith("game/data/") and not rel.startswith("runtime/contracts/"))
    )
    if gameplay_json:
        if GAMEPLAY_VERSION.search(rel):
            errors.append(f"gameplay_version_in_path:{rel}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for pointer in version_key_pointers(data):
            errors.append(f"gameplay_version_counter:{rel}:{pointer}")
        for pointer, value in version_string_pointers(data):
            # A schema identifier is storage-contract metadata, not an in-world
            # gameplay generation. Versioned schema IDs remain legal while
            # gameplay owner IDs, rule/data counters, and user-facing names do not.
            if pointer == "/schema":
                continue
            errors.append(f"gameplay_version_identifier:{rel}:{pointer}:{value}")

for path in sorted((ROOT / "runtime/contracts/templates").glob("*.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    dirs = data.get("current_directories", []) if isinstance(data, dict) else []
    gameplay = isinstance(dirs, list) and any(
        isinstance(d, str) and (d.startswith("state/") or (d.startswith("game/data/") and not d.startswith("runtime/contracts/")))
        for d in dirs
    )
    if not gameplay:
        continue
    # template_id/target_schema are storage-contract metadata, not gameplay IDs.
    vals = data.get("required_top_level_keys", [])
    if isinstance(vals, list) and any(str(x).lower() in VERSION_KEYS for x in vals):
        errors.append(f"gameplay_template_version_counter:{path.relative_to(ROOT)}:required_top_level_keys")
    for contract in (data.get("object_contracts", {}) or {}).values():
        if not isinstance(contract, dict):
            continue
        for field in ("allowed_keys", "canonical_order"):
            vals = contract.get(field, [])
            if isinstance(vals, list) and any(str(x).lower() in VERSION_KEYS for x in vals):
                errors.append(f"gameplay_template_version_counter:{path.relative_to(ROOT)}:{field}")
    for field in ("type_contracts", "array_contracts"):
        for pointer in (data.get(field, {}) or {}):
            if any(part.lower() in VERSION_KEYS for part in str(pointer).split("/") if part):
                errors.append(f"gameplay_template_version_counter:{path.relative_to(ROOT)}:{pointer}")

if errors:
    print("GAMEPLAY NAMING FAILED")
    for error in errors[:500]:
        print("-", error)
    if len(errors) > 500:
        print(f"- ... {len(errors) - 500} more")
    sys.exit(1)
print("GAMEPLAY NAMING OK")
