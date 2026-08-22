#!/usr/bin/env python3
"""Run focused Jianghu regressions for changed repository owners.

The fast development gate intentionally excludes long-horizon soak tests.  Those
remain in the deliberate release/simulation tooling.  Every invocation still
runs the small core contract/invariant slice, then adds subsystem tests based on
changed paths.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CORE = {
    "tests/current/test_contract_discovery.py",
    "tests/current/test_preview_validation.py",
    "tests/current/test_runtime_invariants.py",
    "tests/current/test_world_invariants.py",
    "tests/current/test_release_surface.py",
}
COMBAT = {
    "tests/current/test_combat_geometry.py",
    "tests/current/test_health_targeting.py",
    "tests/current/test_mounted_combat.py",
}
TRAINING = {
    "tests/current/test_training_epochs.py",
    "tests/current/test_rest_practice.py",
    "tests/current/test_field_development.py",
}
WORLD = {
    "tests/current/test_living_world_closure.py",
    "tests/current/test_scheduler_economy.py",
    "tests/current/test_social_civic_estate.py",
    "tests/current/test_membership_duties.py",
    "tests/current/test_family_simulation.py",
}
TOURNAMENT = {
    "tests/current/test_player_tournament_economy.py",
}
RETINUE = {
    "tests/current/test_retinue_persistence.py",
    "tests/current/test_retinue_selection.py",
    "tests/current/test_membership_duties.py",
}
RELEASE = {
    "tests/current/test_release_semantics.py",
    "tests/current/test_jianghu_mandate_regressions.py",
}


def normalize(raw: str) -> str:
    path = Path(raw)
    try:
        path = path.resolve().relative_to(ROOT.resolve())
    except (OSError, ValueError):
        pass
    return path.as_posix().lstrip("./")


def select(paths: list[str]) -> list[str]:
    selected = set(CORE)
    for raw in paths:
        path = normalize(raw)
        low = path.lower()

        if path.startswith("tests/current/") and path.endswith(".py"):
            selected.add(path)

        if path.startswith("runtime/shinobi_runtime/api/") or path.startswith("runtime/shinobi_runtime/commands/") or path.startswith("runtime/shinobi_runtime/tx/") or path.startswith("runtime/shinobi_runtime/store/"):
            selected.update(RELEASE)

        if path.startswith("runtime/shinobi_runtime/combat/") or any(token in low for token in ("/exact_combat.py", "/combat.py", "/combat_simulation.py", "/health.py", "/targeting.py", "/mounts.py", "/medicine.py", "/poison.py")):
            selected.update(COMBAT)

        if any(token in low for token in ("training", "rest_practice", "field_development", "/qi.py", "life_course")):
            selected.update(TRAINING)

        if "tournament" in low:
            selected.update(TOURNAMENT)

        if any(token in low for token in ("retinue", "membership", "duties")):
            selected.update(RETINUE)

        if (
            path.startswith("runtime/shinobi_runtime/martial_world/")
            and any(token in low for token in (
                "scheduler", "time_", "econom", "production", "faction", "family", "social", "civic",
                "autonom", "strategic", "event", "government", "outlaw", "recruit", "manpower",
                "infrastructure", "institution", "relationship", "property", "services", "weather",
                "world_", "route_", "local_travel", "travel.py", "upkeep", "compensation", "commitment",
            ))
        ):
            selected.update(WORLD)

        if path.startswith("runtime/shinobi_runtime/sim/"):
            selected.update(WORLD)

        if path.startswith("game/") or path.startswith("state/"):
            selected.update(RELEASE)
            # Current world/state changes deserve the bounded living-world slice;
            # pure schema/reference edits remain covered by quick_check + core.
            if not path.startswith("game/schemas/"):
                selected.update(WORLD)

        if path.startswith("plugins/shinobi-rpg/skills/") or path in {"pyproject.toml", "requirements.txt"} or path.startswith("runtime/contracts/"):
            selected.update(RELEASE)

    return sorted(test for test in selected if (ROOT / test).is_file())


def main(argv: list[str]) -> int:
    tests = select(argv)
    print("CHANGED TESTS: " + " ".join(tests), flush=True)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(ROOT / "runtime")
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests]
    return subprocess.call(cmd, cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
