#!/usr/bin/env python3
"""Fast release gate for the current Shinobi repository architecture."""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "runtime"))

ERRORS: list[str] = []
METRICS: dict[str, int] = {}


def fail(message: str) -> None:
    ERRORS.append(message)


def check_required_layout() -> None:
    for relative in (
        "runtime/shinobi_runtime",
        "runtime/contracts",
        "game",
        "state",
        "tests/runtime",
        "plugins/shinobi-rpg/skills/shinobi-game-master/SKILL.md",
    ):
        if not (ROOT / relative).exists():
            fail(f"missing:{relative}")


def check_json() -> None:
    count = 0
    for base in (ROOT / "runtime" / "contracts", ROOT / "game", ROOT / "state"):
        for path in base.rglob("*.json"):
            count += 1
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                fail(f"json:{path.relative_to(ROOT)}:{exc}")
    METRICS["json_files"] = count


def check_python_syntax() -> None:
    count = 0
    for base in (ROOT / "runtime" / "shinobi_runtime", ROOT / "tools", ROOT / "tests" / "runtime"):
        for path in base.rglob("*.py"):
            count += 1
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError as exc:
                fail(f"python:{path.relative_to(ROOT)}:{exc.msg}:{exc.lineno}")
    METRICS["python_files"] = count


def check_system_contract_validators() -> None:
    count = 0
    for path in sorted((ROOT / "runtime" / "contracts" / "system-contracts").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        validators = record.get("validators")
        if not isinstance(validators, list) or not validators:
            fail(f"validators_missing:{path.relative_to(ROOT)}")
            continue
        for relative in validators:
            if not isinstance(relative, str) or not relative:
                fail(f"validator_invalid:{path.relative_to(ROOT)}")
                continue
            if not (ROOT / relative).is_file():
                fail(f"validator_missing:{path.relative_to(ROOT)}:{relative}")
        count += 1
    METRICS["system_contracts"] = count


def check_repository_routes() -> None:
    record = json.loads((ROOT / "runtime/contracts/repository-map.json").read_text(encoding="utf-8"))
    shards = record.get("route_shards")
    if not isinstance(shards, dict):
        fail("repository_map:route_shards_missing")
        return
    for name, relative in shards.items():
        if not isinstance(relative, str) or not (ROOT / relative).is_file():
            fail(f"repository_map:route_shard_missing:{name}:{relative}")
    METRICS["route_shards"] = len(shards)


def check_skill_references() -> None:
    skill_root = ROOT / "plugins/shinobi-rpg/skills/shinobi-game-master"
    markdown = list(skill_root.rglob("*.md"))
    pattern = re.compile(r"(?<![A-Za-z0-9_.-])((?:references|assets)/[A-Za-z0-9_.-]+\.md)")
    checked: set[str] = set()
    for path in markdown:
        text = path.read_text(encoding="utf-8")
        for relative in pattern.findall(text):
            if relative in checked:
                continue
            checked.add(relative)
            if not (skill_root / relative).is_file():
                fail(f"skill_reference_missing:{relative}")
    METRICS["skill_references"] = len(checked)





def check_registered_structures() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/verify_structure.py"), str(ROOT)],
        cwd=ROOT,
        env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )
    if result.returncode != 0:
        fail("registered_structure_validation_failed")

def check_runtime_imports() -> None:
    try:
        from shinobi_runtime.api.app import create_app  # noqa: F401
        from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner  # noqa: F401
        from shinobi_runtime.commands.specs import COMMAND_SPECS
    except Exception as exc:
        fail(f"runtime_import:{type(exc).__name__}:{exc}")
        return
    METRICS["semantic_commands"] = len(COMMAND_SPECS)


def main() -> int:
    check_required_layout()
    check_json()
    check_python_syntax()
    check_system_contract_validators()
    check_repository_routes()
    check_skill_references()
    check_registered_structures()
    check_runtime_imports()
    if ERRORS:
        print(f"QUICK CHECK FAILED {len(ERRORS)}")
        for error in ERRORS:
            print("-", error)
        return 1
    print("QUICK CHECK OK")
    print(json.dumps(METRICS, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
