"""Helpers for testing repository Skill contracts across split reference files."""
from __future__ import annotations

from pathlib import Path


def combat_contract_text(root: Path) -> str:
    refs = root / "plugins/shinobi-rpg/skill/shinobi-game-master/references"
    entry = (refs / "combat.md").read_text(encoding="utf-8")
    base = (refs / "combat-base.md").read_text(encoding="utf-8")
    return entry + "\n\n" + base
