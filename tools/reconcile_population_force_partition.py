#!/usr/bin/env python3
"""Reconcile estimated Great Village census pools with exact saved force totals.

Village census totals remain simulation estimates because canon provides relative
population ratings rather than exact censuses.  The active shinobi-service
partition, however, is not estimated here: it is aligned to each campaign
force owner's existing total.  The balancing delta is taken from the civilian
pool, preserving each village census and all headcount conservation.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "state/population/registry.json"
TIME = "SE-0061-02-06T21:15:00"
VILLAGES = {
    "konoha": "state/force/konoha-shinobi.json",
    "iwa": "state/force/iwa-shinobi.json",
    "kumo": "state/force/kumo-shinobi.json",
    "suna": "state/force/suna-shinobi.json",
    "kiri": "state/force/kiri-shinobi.json",
}


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def proportional(values: dict[str, int], new_total: int) -> dict[str, int]:
    old_total = sum(values.values())
    if old_total == 0:
        keys = sorted(values)
        result = {key: 0 for key in keys}
        if keys:
            result[keys[0]] = new_total
        return result
    floors = {key: values[key] * new_total // old_total for key in values}
    remain = new_total - sum(floors.values())
    order = sorted(values, key=lambda key: (-((values[key] * new_total) % old_total), key))
    for key in order[:remain]:
        floors[key] += 1
    return dict(sorted(floors.items()))


def resize_pool(pool: dict, new_count: int) -> None:
    old_count = int(pool["count"])
    profile = pool["profile"]
    pool["count"] = new_count
    pool["status"] = "active" if new_count else "exhausted"
    pool["last_changed_at"] = TIME
    profile["category_counts"] = {str(pool["category"]): new_count}
    for distribution in profile.get("numeric_distributions", {}).values():
        if isinstance(distribution, dict) and distribution.get("count") == old_count:
            distribution["count"] = new_count
    for name, values in list(profile.get("dimension_counts", {}).items()):
        profile["dimension_counts"][name] = proportional(dict(values), new_count)


def main() -> int:
    registry = read(REGISTRY)
    if registry.get("transfers"):
        raise SystemExit("refusing population baseline reconciliation after transfers exist")
    pools = registry["pools"]
    before_total = sum(int(p["count"]) for p in pools.values())
    report = []
    for village, force_path in VILLAGES.items():
        service_id = f"pool.{village}.shinobi_service"
        civilian_id = f"pool.{village}.civilian_general"
        service = pools[service_id]
        civilian = pools[civilian_id]
        force = read(ROOT / force_path)
        exact_service = int(force["total"])
        old_service = int(service["count"])
        old_civilian = int(civilian["count"])
        delta = exact_service - old_service
        new_civilian = old_civilian - delta
        if new_civilian < 0:
            raise ValueError(f"{village}: force total exceeds available census partition")
        resize_pool(service, exact_service)
        resize_pool(civilian, new_civilian)
        suffix = (
            f"; military_partition_reconciled_to_campaign_force={force['id']}:{exact_service}; "
            "census_total_remains_simulation_estimate_not_canon"
        )
        if "military_partition_reconciled_to_campaign_force=" not in service["provenance"]:
            service["provenance"] += suffix
        if "military_partition_reconciled_to_campaign_force=" not in civilian["provenance"]:
            civilian["provenance"] += suffix
        report.append((village, old_service, exact_service, old_civilian, new_civilian))

    after_total = sum(int(p["count"]) for p in pools.values())
    if before_total != after_total:
        raise ValueError(f"global population changed {before_total}->{after_total}")
    write(REGISTRY, registry)
    for row in report:
        print("%s service %d->%d civilian %d->%d" % row)
    print("represented_population", after_total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
