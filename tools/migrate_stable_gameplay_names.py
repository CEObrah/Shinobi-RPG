#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".json", ".md", ".py", ".yml", ".yaml", ".txt", ".toml", ".ini", ".cfg"}
VERSION_KEYS = {"version", "schema_version", "game_version", "rules_version", "data_version"}
RELEASE_ATTACHED = re.compile(r"(?i)([._-])v3[0-9]\b")
RELEASE_STANDALONE = re.compile(r"(?i)\bv3[0-9]\b")


def tracked() -> list[Path]:
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [ROOT / p.decode("utf-8") for p in raw.split(b"\0") if p]


def stable_path(rel: str) -> str:
    rel = re.sub(r"(?i)\.v3[0-9](?=\.)", "", rel)
    rel = re.sub(r"(?i)-v3[0-9](?=\.)", "", rel)
    rel = re.sub(r"(?i)_v3[0-9](?=\.)", "", rel)
    return rel


def strip_version_keys(value):
    changed = False
    if isinstance(value, dict):
        for key in list(value):
            if str(key).lower() in VERSION_KEYS:
                del value[key]
                changed = True
                continue
            item, item_changed = strip_version_keys(value[key])
            if item_changed:
                value[key] = item
                changed = True
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            new_item, item_changed = strip_version_keys(item)
            if item_changed:
                value[idx] = new_item
                changed = True
    return value, changed


def strip_contract_version_fields(template: dict) -> bool:
    changed = False
    required = template.get("required_top_level_keys")
    if isinstance(required, list):
        new = [x for x in required if str(x).lower() not in VERSION_KEYS]
        if new != required:
            template["required_top_level_keys"] = new
            changed = True
    contracts = template.get("object_contracts")
    if isinstance(contracts, dict):
        for contract in contracts.values():
            if not isinstance(contract, dict):
                continue
            for field in ("allowed_keys", "canonical_order"):
                vals = contract.get(field)
                if isinstance(vals, list):
                    new = [x for x in vals if str(x).lower() not in VERSION_KEYS]
                    if new != vals:
                        contract[field] = new
                        changed = True
    for field in ("type_contracts", "array_contracts"):
        contracts = template.get(field)
        if not isinstance(contracts, dict):
            continue
        for pointer in list(contracts):
            parts = [p.lower() for p in str(pointer).split("/") if p]
            if any(p in VERSION_KEYS for p in parts):
                del contracts[pointer]
                changed = True
    return changed


def strip_schema_version_fields(value) -> bool:
    changed = False
    if isinstance(value, dict):
        req = value.get("required")
        if isinstance(req, list):
            new = [x for x in req if str(x).lower() not in VERSION_KEYS]
            if new != req:
                value["required"] = new
                changed = True
        props = value.get("properties")
        if isinstance(props, dict):
            for key in list(props):
                if str(key).lower() in VERSION_KEYS:
                    del props[key]
                    changed = True
                elif strip_schema_version_fields(props[key]):
                    changed = True
        for key, item in list(value.items()):
            if key in {"required", "properties"}:
                continue
            if strip_schema_version_fields(item):
                changed = True
    elif isinstance(value, list):
        for item in value:
            if strip_schema_version_fields(item):
                changed = True
    return changed


def gameplay_template(data: dict) -> bool:
    dirs = data.get("current_directories", [])
    if not isinstance(dirs, list):
        return False
    for entry in dirs:
        if not isinstance(entry, str):
            continue
        if entry.startswith("state/") or entry == "state":
            return True
        if entry.startswith("data/") and not entry.startswith("data/runtime/"):
            return True
    return False


def dump_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def loads_no_dupes(text: str, rel: str):
    def hook(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"duplicate JSON key after stable-name rewrite: {rel}: {key}")
            out[key] = value
        return out
    return json.loads(text, object_pairs_hook=hook)


# The old tech-index generation is an empty structural stub. Keep the live
# static-data contract and remove the stub before both names collapse.
t_index = ROOT / "data/runtime/template-index-shards/t.json"
if t_index.exists():
    data = json.loads(t_index.read_text(encoding="utf-8"))
    templates = data.get("templates", {})
    if isinstance(templates, dict) and "tech-index.v38" in templates:
        templates.pop("tech-index.v38")
        dump_json(t_index, data)
for rel in (
    "data/runtime/templates/tech-index.v38.template.json",
    "schemas/tech-index-v38.schema.json",
):
    path = ROOT / rel
    if path.exists():
        subprocess.check_call(["git", "rm", rel], cwd=ROOT)

# Gameplay data stores current facts and rules, not release counters.
for path in tracked():
    if not path.exists() or path.suffix.lower() != ".json":
        continue
    rel = path.relative_to(ROOT).as_posix()
    if not (rel.startswith("state/") or (rel.startswith("data/") and not rel.startswith("data/runtime/"))):
        continue
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        continue
    data, changed = strip_version_keys(data)
    if changed:
        dump_json(path, data)

# Simplify structural contracts for gameplay authorities that formerly exposed
# release counters and collect their source schemas for the same cleanup.
gameplay_source_schemas: set[str] = set()
for path in sorted((ROOT / "data/runtime/templates").glob("*.json")):
    if not path.exists():
        continue
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        continue
    if not isinstance(data, dict) or not gameplay_template(data):
        continue
    source = data.get("source_schema")
    if isinstance(source, str):
        gameplay_source_schemas.add(source)
    if strip_contract_version_fields(data):
        dump_json(path, data)

# Rename every release-generation gameplay schema/template filename to a stable
# semantic path. The audit established that tech-index was the only collision.
for path in list(tracked()):
    if not path.exists():
        continue
    rel = path.relative_to(ROOT).as_posix()
    new_rel = stable_path(rel)
    if new_rel == rel:
        continue
    target = ROOT / new_rel
    if target.exists():
        raise SystemExit(f"stable-name collision: {rel} -> {new_rel}")
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(["git", "mv", rel, new_rel], cwd=ROOT)

# Remove version properties from schemas serving gameplay authorities.
for source in sorted(gameplay_source_schemas):
    rel = stable_path(source)
    path = ROOT / rel
    if not path.exists():
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    if strip_schema_version_fields(data):
        dump_json(path, data)

# Rewrite attached release-generation identifiers and paths across the tree.
# Bare release tokens in non-JSON prose/code become "current" rather than
# preserving release-history vocabulary.
for path in tracked():
    if not path.exists() or path.suffix.lower() not in TEXT_SUFFIXES:
        continue
    rel = path.relative_to(ROOT).as_posix()
    text = path.read_text(encoding="utf-8")
    new = RELEASE_ATTACHED.sub("", text)
    if path.suffix.lower() != ".json":
        new = RELEASE_STANDALONE.sub("current", new)
    if new != text:
        if path.suffix.lower() == ".json":
            loads_no_dupes(new, rel)
        path.write_text(new, encoding="utf-8")

# Operative runtime rule: state the invariant without release-history prose.
runtime = ROOT / "RUNTIME.md"
text = runtime.read_text(encoding="utf-8")
old = (
    "Rules and reusable gameplay data state the current rule directly. Release history, migration notes, and superseded behavior belong only in maintenance documentation when they are actually needed. "
    "Gameplay entity IDs, process IDs, and state paths are semantic and version-neutral; technical version tags belong only in schema/template/validator metadata when required. Do not propagate legacy version labels into new campaign concepts."
)
new = (
    "Rules and reusable gameplay data contain only operative rules. Gameplay-facing IDs, filenames, schema IDs, template IDs, process IDs, state paths, and data records use stable semantic names without release numbers. "
    "Implementation protocol generations are confined to maintenance/tooling formats and never become campaign facts or gameplay concepts."
)
if old in text:
    runtime.write_text(text.replace(old, new), encoding="utf-8")

# Permanent guardrail.
naming_test = ROOT / "tools/test_gameplay_naming.py"
naming_test.write_text(r'''#!/usr/bin/env python3
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
RULE_HISTORY = re.compile(r"(?i)\b(legacy|migration|superseded behavior|previous version|release history)\b")
errors: list[str] = []

def tracked() -> list[Path]:
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [ROOT / p.decode("utf-8") for p in raw.split(b"\0") if p]

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
        if (rel in {"RUNTIME.md", "VOICE.md"} or rel.startswith("rules/")) and RULE_HISTORY.search(text):
            errors.append(f"release_history_language_in_rules:{rel}")
    if path.suffix.lower() == ".json" and (rel.startswith("state/") or (rel.startswith("data/") and not rel.startswith("data/runtime/"))):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for pointer in version_key_pointers(data):
            errors.append(f"gameplay_version_counter:{rel}:{pointer}")

for path in sorted((ROOT / "data/runtime/templates").glob("*.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    dirs = data.get("current_directories", []) if isinstance(data, dict) else []
    gameplay = isinstance(dirs, list) and any(
        isinstance(d, str) and (d.startswith("state/") or (d.startswith("data/") and not d.startswith("data/runtime/")))
        for d in dirs
    )
    if not gameplay:
        continue
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
    for error in errors[:250]:
        print("-", error)
    if len(errors) > 250:
        print(f"- ... {len(errors) - 250} more")
    sys.exit(1)
print("GAMEPLAY NAMING OK")
''', encoding="utf-8")

# Add the naming guard to the repository's actual CI stack. Routing/templates
# are already present and remain mandatory.
audit = ROOT / ".github/workflows/audit.yml"
audit_text = audit.read_text(encoding="utf-8")
anchor = "      - run: python tools/audit.py\n"
extra = "      - run: python tools/test_gameplay_naming.py\n"
if "python tools/test_gameplay_naming.py" not in audit_text:
    if anchor not in audit_text:
        raise SystemExit("audit workflow insertion anchor missing")
    audit.write_text(audit_text.replace(anchor, anchor + extra), encoding="utf-8")

for path in tracked():
    if path.exists() and path.suffix.lower() == ".json":
        loads_no_dupes(path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix())

print("stable gameplay-name migration prepared")
