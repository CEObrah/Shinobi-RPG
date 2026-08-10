#!/usr/bin/env python3
"""Seed bounded Shinobi population pools from explicit simulation census estimates.

The exact village census is not canon.  The estimates below are campaign tuning
inputs.  Konoha is anchored at 80,000, a commonly cited fan-scale estimate,
while the relative scale of the other great villages follows the databook
population ratings (Konoha 5, Iwa 4, Kumo 3, Suna 2, Kiri 2).  Existing active
shinobi force totals are treated as an already-existing partition of each
estimated settlement population, not as newly created people.

This tool is deterministic and idempotent for an empty registry.  It refuses to
overwrite a non-empty population registry so migration remains explicit.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "state/population/registry.json"
CAMPAIGN_TIME = "SE-0061-02-06T21:15:00"

VILLAGES = {
    "konoha": {"owner_ref": "faction_konoha", "rating": 5, "estimate": 80000, "force": "state/force/konoha-shinobi.json"},
    "iwa": {"owner_ref": "faction_iwa", "rating": 4, "estimate": 64000, "force": "state/force/iwa-shinobi.json"},
    "kumo": {"owner_ref": "faction_kumo", "rating": 3, "estimate": 48000, "force": "state/force/kumo-shinobi.json"},
    "suna": {"owner_ref": "faction_suna", "rating": 2, "estimate": 32000, "force": "state/force/suna-shinobi.json"},
    "kiri": {"owner_ref": "faction_kiri", "rating": 2, "estimate": 32000, "force": "state/force/kiri-shinobi.json"},
}

CATEGORIES = ("child", "academy_age", "working_age", "older_adult")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def demographic_counts(total: int) -> Dict[str, int]:
    # Simulation demographics, not a canon claim. Largest category receives the
    # integer rounding remainder so the partition always conserves exactly.
    child = total * 16 // 100
    academy = total * 12 // 100
    older = total * 10 // 100
    working = total - child - academy - older
    return {
        "child": child,
        "academy_age": academy,
        "working_age": working,
        "older_adult": older,
    }


def pool(owner_ref: str, category: str, count: int, category_counts: dict, provenance: str, tags: list[str]) -> dict:
    if sum(category_counts.values()) != count:
        raise ValueError(f"pool partition mismatch for {owner_ref}:{category}")
    return {
        "owner_ref": owner_ref,
        "category": category,
        "count": count,
        "status": "active" if count else "exhausted",
        "provenance": provenance,
        "profile": {
            "numeric_distributions": {},
            "category_counts": category_counts,
            "tags": tags,
        },
        "last_changed_at": CAMPAIGN_TIME,
    }


def main() -> int:
    registry = read_json(REGISTRY)
    if registry.get("pools") or registry.get("transfers"):
        raise SystemExit("population registry is not empty; refusing implicit reseed")

    seeded = {}
    for slug, spec in VILLAGES.items():
        force = read_json(ROOT / spec["force"])
        service = int(force["total"])
        estimate = int(spec["estimate"])
        if service > estimate:
            raise ValueError(f"{slug} force exceeds census estimate")
        total_demographics = demographic_counts(estimate)
        if service > total_demographics["working_age"]:
            raise ValueError(f"{slug} force exceeds working-age simulation partition")

        civilian_categories = dict(total_demographics)
        civilian_categories["working_age"] -= service
        civilian = estimate - service
        zero = {key: 0 for key in CATEGORIES}
        service_categories = dict(zero)
        service_categories["working_age"] = service

        provenance = (
            f"simulation_estimate_not_canon: settlement census={estimate}; "
            f"canon-relative population rating={spec['rating']}/5; active shinobi partition "
            f"initialized from {spec['force']} total={service}; exact census is not established by canon"
        )
        common_tags = [
            "simulation_census_estimate",
            "not_canon_exact_census",
            f"population_rating_{spec['rating']}_of_5",
            slug,
        ]
        seeded[f"pool.{slug}.civilian_residents"] = pool(
            spec["owner_ref"], "civilian_residents", civilian, civilian_categories,
            provenance, common_tags + ["civilian_source"],
        )
        seeded[f"pool.{slug}.academy_candidates"] = pool(
            spec["owner_ref"], "academy_candidates", 0, dict(zero),
            provenance, common_tags + ["training_pipeline"],
        )
        seeded[f"pool.{slug}.shinobi_service"] = pool(
            spec["owner_ref"], "shinobi_service", service, service_categories,
            provenance, common_tags + ["institutional_service", force["id"]],
        )

    registry["pools"] = dict(sorted(seeded.items()))
    write_json(REGISTRY, registry)
    print(f"seeded {len(seeded)} pools across {len(VILLAGES)} great villages")
    print("represented_population", sum(item["count"] for item in seeded.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
