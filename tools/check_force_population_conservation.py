#!/usr/bin/env python3
"""Validate force headcount, availability, troop pools, and physical population ancestry."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POP_PATH = ROOT / "state/population/registry.json"
FORCE_DIR = ROOT / "state/force"


def fail(message: str) -> None:
    print(f"FORCE POPULATION CONSERVATION FAIL: {message}")
    raise SystemExit(1)


def is_required_population_force(force_id: str) -> bool:
    return force_id.startswith("force.civil.") or force_id == "force.iron.samurai"


def main() -> int:
    population = json.loads(POP_PATH.read_text(encoding="utf-8"))
    pools = population.get("pools") if isinstance(population, dict) else None
    if not isinstance(pools, dict):
        fail("population_registry_invalid")

    checked = 0
    linked = 0
    for path in sorted(FORCE_DIR.glob("*.json")):
        force = json.loads(path.read_text(encoding="utf-8"))
        if force.get("schema") != "force":
            continue
        force_id = force.get("id")
        total = force.get("total")
        if not isinstance(force_id, str) or isinstance(total, bool) or not isinstance(total, int) or total < 0:
            fail(f"force_shape:{path.name}")

        troop_pools = force.get("troop_pools")
        if not isinstance(troop_pools, list) or any(not isinstance(row, dict) for row in troop_pools):
            fail(f"troop_pools_shape:{force_id}")
        if any(isinstance(row.get("count"), bool) or not isinstance(row.get("count"), int) or row.get("count") < 0 for row in troop_pools):
            fail(f"troop_pool_count:{force_id}")

        availability = force.get("availability")
        if not isinstance(availability, dict):
            fail(f"availability_shape:{force_id}")
        values = list(availability.values())
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            fail(f"availability_value:{force_id}")
        availability_sum = sum(values)
        if availability_sum != total:
            fail(f"availability_total:{force_id}:{availability_sum}!={total}")

        pool_id = force.get("population_pool_id")
        if is_required_population_force(force_id) and not isinstance(pool_id, str):
            fail(f"required_population_ancestry_missing:{force_id}")
        if isinstance(pool_id, str):
            pool = pools.get(pool_id)
            if not isinstance(pool, dict):
                fail(f"population_pool_missing:{force_id}:{pool_id}")
            if pool.get("linked_force_ref") != force_id:
                fail(f"population_force_backlink:{force_id}:{pool_id}")
            if pool.get("count") != total:
                fail(f"population_force_total:{force_id}:{pool.get('count')}!={total}")
            representation = pool.get("representation")
            if not isinstance(representation, dict):
                fail(f"population_representation:{pool_id}")
            anonymous = representation.get("anonymous_count")
            rostered = representation.get("rostered_count")
            if (
                isinstance(anonymous, bool) or not isinstance(anonymous, int)
                or isinstance(rostered, bool) or not isinstance(rostered, int)
                or anonymous + rostered != total
            ):
                fail(f"population_representation_total:{pool_id}")
            linked += 1
        checked += 1

    print(f"FORCE POPULATION CONSERVATION OK: {checked} forces; {linked} population-linked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
