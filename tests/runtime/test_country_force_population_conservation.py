from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _json(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_conventional_country_forces_have_conserved_population_ancestry() -> None:
    population = _json("state/population/registry.json")
    pools = population["pools"]
    force_paths = sorted((ROOT / "state/force").glob("civil-*.json"))
    force_paths.append(ROOT / "state/force/iron-samurai.json")
    assert force_paths
    for path in force_paths:
        force = json.loads(path.read_text(encoding="utf-8"))
        pool_id = force["population_pool_id"]
        pool = pools[pool_id]
        assert pool["linked_force_ref"] == force["id"]
        assert pool["count"] == force["total"]
        assert pool["representation"]["anonymous_count"] + pool["representation"]["rostered_count"] == force["total"]
        assert all(isinstance(row["count"], int) and row["count"] >= 0 for row in force["troop_pools"])
        assert sum(force["availability"].values()) == force["total"]


def test_conventional_population_repair_did_not_create_rostered_people() -> None:
    population = _json("state/population/registry.json")
    for pool_id, pool in population["pools"].items():
        if "existing_force_ancestry" not in pool.get("profile", {}).get("tags", []):
            continue
        assert pool["representation"] == {
            "anonymous_count": pool["count"],
            "rostered_count": 0,
            "rostered_person_refs": [],
        }
        assert "no population created" in pool["provenance"]
