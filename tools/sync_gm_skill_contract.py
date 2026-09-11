#!/usr/bin/env python3
"""Synchronize the runtime/Skill compatibility fingerprint.

The fingerprint covers every packaged GM Skill file. The token marker itself is
normalized before hashing, avoiding a self-referential digest. Any Skill edit
therefore requires a new token and a runtime release before certification can
pass.
"""
from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "plugins/shinobi-rpg/skill/shinobi-game-master"
SKILL_ENTRY = SKILL_ROOT / "SKILL.md"
RUNTIME_CONTRACT = ROOT / "runtime/shinobi_runtime/gm_skill_contract.py"
SKILL_MARKER = re.compile(r"(?m)^GM_SKILL_CONTRACT_TOKEN: [0-9a-f]{64}$")
RUNTIME_MARKER = re.compile(r'(?m)^GM_SKILL_CONTRACT_TOKEN = "[0-9a-f]{64}"$')
NORMALIZED_SKILL_MARKER = b"GM_SKILL_CONTRACT_TOKEN: <normalized>"


def _normalized_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if path == SKILL_ENTRY:
        text = data.decode("utf-8")
        text, count = SKILL_MARKER.subn(
            NORMALIZED_SKILL_MARKER.decode("ascii"), text
        )
        if count != 1:
            raise RuntimeError("Skill must contain exactly one GM skill contract marker")
        return text.encode("utf-8")
    return data


def skill_tree_token() -> str:
    digest = hashlib.sha256()
    files = sorted(
        (path for path in SKILL_ROOT.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(SKILL_ROOT).as_posix(),
    )
    if not files:
        raise RuntimeError("GM Skill tree is empty")
    for path in files:
        rel = path.relative_to(SKILL_ROOT).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_normalized_bytes(path))
        digest.update(b"\0")
    return digest.hexdigest()


def _current_values() -> tuple[str, str]:
    skill_text = SKILL_ENTRY.read_text(encoding="utf-8")
    runtime_text = RUNTIME_CONTRACT.read_text(encoding="utf-8")
    skill_match = SKILL_MARKER.search(skill_text)
    runtime_match = RUNTIME_MARKER.search(runtime_text)
    if skill_match is None or runtime_match is None:
        raise RuntimeError("GM skill contract markers are missing")
    return skill_match.group(0).split(": ", 1)[1], runtime_match.group(0).split('"', 2)[1]


def synchronize(*, check: bool) -> str:
    token = skill_tree_token()
    current_skill, current_runtime = _current_values()
    if check:
        if current_skill != token or current_runtime != token:
            raise RuntimeError(
                "GM Skill contract is stale; run tools/sync_gm_skill_contract.py"
            )
        return token

    skill_text = SKILL_ENTRY.read_text(encoding="utf-8")
    runtime_text = RUNTIME_CONTRACT.read_text(encoding="utf-8")
    skill_text, skill_count = SKILL_MARKER.subn(
        f"GM_SKILL_CONTRACT_TOKEN: {token}", skill_text
    )
    runtime_text, runtime_count = RUNTIME_MARKER.subn(
        f'GM_SKILL_CONTRACT_TOKEN = "{token}"', runtime_text
    )
    if skill_count != 1 or runtime_count != 1:
        raise RuntimeError("GM Skill contract markers are not unique")
    SKILL_ENTRY.write_text(skill_text, encoding="utf-8")
    RUNTIME_CONTRACT.write_text(runtime_text, encoding="utf-8")
    return token


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        token = synchronize(check=args.check)
    except RuntimeError as exc:
        print(f"GM SKILL CONTRACT FAILED: {exc}")
        return 1
    print(f"GM SKILL CONTRACT {'OK' if args.check else 'SYNCED'}: {token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
