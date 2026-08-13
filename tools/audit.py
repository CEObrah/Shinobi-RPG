#!/usr/bin/env python3
"""Repository-wide production audit for the current three-authority architecture.

This audit intentionally contains no compatibility logic for retired frontier,
coverage, micro-unit, tactical-team, or per-owner polling systems.  It composes
the focused production validators and then checks cross-system invariants that
span runtime/, game/, and state/.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def fail(message: str) -> None:
    ERRORS.append(message)


def load(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def run_validators() -> None:
    validators = sorted((ROOT / "tools").glob("test_*.py"))
    for path in validators:
        proc = subprocess.run(
            [sys.executable, str(path)], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        if proc.returncode:
            fail(f"validator_failed:{path.name}\n{proc.stdout.strip()}")


def architecture_invariants() -> dict[str, int]:
    for required in ("runtime", "game", "state"):
        if not (ROOT / required).is_dir():
            fail(f"authority_root_missing:{required}")
    for retired in (
        "src", "data", "schemas", "rules",
        "state/time/frontier.json", "state/time/coverage", "state/runtime.json",
        "state/unit", "state/unit-capability", "state/unit-kernel", "state/tactical-team",
    ):
        if (ROOT / retired).exists():
            fail(f"retired_authority_present:{retired}")

    runtime_files = [
        path for path in (ROOT / "runtime" / "shinobi_runtime").rglob("*.py")
        if "acceptance" not in path.relative_to(ROOT / "runtime" / "shinobi_runtime").parts
    ]
    forbidden_runtime_literals = (
        "pc_wei_tang", "team.blackhound", "black_hound", "team_fujin",
        "house_tang", "loc_hokage_tower", "LegacyFrontier", "legacy_process_shadow",
    )
    for path in runtime_files:
        text = path.read_text(encoding="utf-8")
        for literal in forbidden_runtime_literals:
            if literal in text:
                fail(f"campaign_literal_in_runtime:{path.relative_to(ROOT)}:{literal}")

    scheduler = load("state/time/causal-scheduler.json")
    metrics = scheduler.get("metrics", {})
    for key in (
        "global_person_scans", "named_persons_scanned_per_advance",
        "global_faction_directory_scans", "faction_directory_scans_per_advance",
    ):
        if metrics.get(key, 0) not in (0, None):
            fail(f"scheduler_global_scan:{key}:{metrics.get(key)}")

    teams = []
    for path in sorted((ROOT / "state/team").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("schema") == "exact-team":
            teams.append(record)
    if len(teams) < 3:
        fail(f"exact_team_coverage_too_small:{len(teams)}")
    for team in teams:
        for key in ("member_refs", "leader_ref", "assignment_authority_ref"):
            if key not in team:
                fail(f"exact_team_contract_missing:{team.get('id')}:{key}")

    house = load("state/house/tang.json")
    if "permanent_units" in house or "unit_policy" in house:
        fail("house_legacy_unit_vocabulary")
    if len(house.get("member_ids", [])) != 32:
        fail("house_member_count_not_32")
    cores = load("state/person-core/house-tang.json").get("people", {})
    if len(cores) != 27:
        fail(f"house_sparse_core_count:{len(cores)}")
    for core_id, core in cores.items():
        if str(core.get("cohort_ref", "")).startswith("unit."):
            fail(f"house_core_legacy_unit_ref:{core_id}")

    population = load("state/population/registry.json")
    represented = sum(
        pool.get("count", 0) for pool in population.get("pools", {}).values()
        if isinstance(pool, dict) and isinstance(pool.get("count"), int)
    )
    if represented != 256000:
        fail(f"population_total_drift:{represented}")

    return {
        "validators": len(list((ROOT / "tools").glob("test_*.py"))),
        "runtime_python_files": len(runtime_files),
        "exact_teams": len(teams),
        "scheduler_hosts": len(scheduler.get("hosts", {})),
        "queued_events": len(scheduler.get("events", [])),
        "population_pools": len(population.get("pools", {})),
        "represented_population": represented,
        "forces": len(list((ROOT / "state/force").glob("*.json"))),
        "formation_owners": len(list((ROOT / "state/formation").glob("*.json"))),
        "legacy_micro_unit_files": sum(
            len(list((ROOT / rel).glob("*.json"))) if (ROOT / rel).exists() else 0
            for rel in ("state/unit", "state/unit-capability", "state/unit-kernel", "state/tactical-team")
        ),
    }


def main() -> int:
    run_validators()
    metrics = architecture_invariants()
    if ERRORS:
        print(f"PRODUCTION AUDIT FAILED {len(ERRORS)}")
        for error in ERRORS:
            print("-", error)
        return 1
    print("PRODUCTION AUDIT OK")
    print(json.dumps(metrics, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
