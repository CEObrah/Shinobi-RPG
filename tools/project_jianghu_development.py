#!/usr/bin/env python3
"""Deterministic 1/5/10-year institutional development projections.

This is a release diagnostic, not a campaign mutation. It holds each selected
person's current faction curriculum/facility/instructor environment constant so
we can inspect the progression curve itself without pretending to forecast
future political or staffing changes.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from shinobi_runtime.martial_world.training import advance_faction_training_epoch, apply_institutional_training
from shinobi_runtime.martial_world.faction_state import hydrate_faction_state
from shinobi_runtime.martial_world.family_simulation import advance_annual_life_course


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_world() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    factions: dict[str, dict[str, Any]] = {}
    rosters: dict[str, dict[str, Any]] = {}
    for path in sorted((ROOT / "state/martial-world/factions").glob("*.json")):
        row = read(path)
        fid = row.get("faction_id")
        if isinstance(fid, str):
            factions[fid] = hydrate_faction_state(row)
    for path in sorted((ROOT / "state/martial-world/people").glob("*.json")):
        row = read(path)
        fid = row.get("faction_ref")
        if isinstance(fid, str):
            rosters[fid] = row
    return factions, rosters


def primary_skill(person: Mapping[str, Any]) -> int:
    martial = person.get("martial_skills", {}) if isinstance(person.get("martial_skills"), Mapping) else {}
    return max([int(v) for k, v in martial.items() if k in {"sword", "spear", "bow", "hidden_weapons", "unarmed"} and isinstance(v, int)] or [0])


def avg_aptitude(person: Mapping[str, Any]) -> float:
    a = person.get("aptitudes", {}) if isinstance(person.get("aptitudes"), Mapping) else {}
    vals = [int(v) for v in a.values() if isinstance(v, int)]
    return sum(vals) / max(1, len(vals))


def choose_representatives(rosters: Mapping[str, Mapping[str, Any]], current_year: int) -> dict[str, tuple[str, dict[str, Any]]]:
    all_people: list[tuple[str, dict[str, Any]]] = []
    by_name: dict[str, tuple[str, dict[str, Any]]] = {}
    for fid, roster in rosters.items():
        for p in roster.get("people", []):
            if not isinstance(p, dict):
                continue
            all_people.append((fid, p))
            if isinstance(p.get("name"), str):
                by_name[p["name"]] = (fid, p)
    selected = {
        "wei": by_name["Tang Wei"],
        "zhu": by_name["Tang Zhu"],
        "ling": by_name["Tang Ling"],
        "kai": by_name["Tang Kai"],
    }
    pool = [
        (fid, p) for fid, p in all_people
        if fid != "house_tang" and 18 <= current_year - int(p.get("birth_year", current_year)) <= 50
    ]
    ordinary = min(
        pool,
        key=lambda fp: (
            abs(avg_aptitude(fp[1]) - 100.0) * 2 + abs(primary_skill(fp[1]) - 55),
            str(fp[1].get("person_id", "")),
        ),
    )
    talented_pool = [fp for fp in pool if 120 <= avg_aptitude(fp[1]) <= 175 and 50 <= primary_skill(fp[1]) <= 95]
    if not talented_pool:
        talented_pool = pool
    talented = min(
        talented_pool,
        key=lambda fp: (
            abs(avg_aptitude(fp[1]) - 150.0) * 2 + abs(primary_skill(fp[1]) - 75),
            str(fp[1].get("person_id", "")),
        ),
    )
    master_pool = [fp for fp in pool if 95 <= primary_skill(fp[1]) <= 135]
    if not master_pool:
        master_pool = pool
    elite = min(
        master_pool,
        key=lambda fp: (
            abs(primary_skill(fp[1]) - 110) + abs(avg_aptitude(fp[1]) - 140.0) / 4,
            str(fp[1].get("person_id", "")),
        ),
    )
    selected.update({"ordinary": ordinary, "talented_ordinary": talented, "elite_non_tang_master": elite})
    return selected


def future_iso(anchor: datetime, years: int) -> str:
    try:
        return anchor.replace(year=anchor.year + years).isoformat()
    except ValueError:
        # Leap-day defensive fallback.
        return anchor.replace(year=anchor.year + years, day=28).isoformat()


def snapshot(person: Mapping[str, Any], year: int) -> dict[str, Any]:
    return {
        "age": max(0, year - int(person.get("birth_year", year))),
        "body_mass_kg": int(person.get("body_mass_kg", 0)),
        "attributes": dict(person.get("attributes", {})) if isinstance(person.get("attributes"), Mapping) else {},
        "martial_skills": dict(person.get("martial_skills", {})) if isinstance(person.get("martial_skills"), Mapping) else {},
        "professional_skills": dict(person.get("professional_skills", {})) if isinstance(person.get("professional_skills"), Mapping) else {},
        "qi": int(person.get("qi", 0)),
        "qi_control": int(person.get("qi_control", 0)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="artifacts/jianghu-development-projections.json")
    args = ap.parse_args()

    factions, rosters = load_world()
    scheduler = read(ROOT / "state/martial-world/scheduler.json")
    anchor = datetime.fromisoformat(str(scheduler["settled_through"]))
    selected = choose_representatives(rosters, anchor.year)
    projections: dict[str, Any] = {}

    for label, (fid, raw_person) in selected.items():
        faction = factions[fid]
        roster = rosters[fid]
        entry: dict[str, Any] = {
            "person_id": raw_person.get("person_id"),
            "name": raw_person.get("name"),
            "faction_ref": fid,
            "average_aptitude": round(avg_aptitude(raw_person), 2),
            "current": snapshot(raw_person, anchor.year),
            "projections": {},
        }
        for years in (1, 5, 10):
            projected_faction = copy.deepcopy(faction)
            projected = copy.deepcopy(raw_person)
            # Step through each campaign year so the diagnostic includes the same
            # bounded natural child/body maturation used by the annual life-course
            # scheduler as well as sparse institutional training.  This remains a
            # disposable projection and excludes natural death so the requested
            # capability curve is not replaced by a mortality forecast.
            for step in range(1, years + 1):
                projected_faction, _ = advance_faction_training_epoch(
                    projected_faction,
                    roster,
                    at_iso=future_iso(anchor, step),
                    refresh_environment=True,
                )
                projected = apply_institutional_training(
                    projected, faction=projected_faction, roster_people=roster.get("people", [])
                )
                life = advance_annual_life_course(
                    [projected],
                    year=anchor.year + step,
                    player_ref=None,
                    exclude_death_refs=[str(projected.get("person_id") or "")],
                )
                projected = life["people_after"][0]
            entry["projections"][str(years)] = snapshot(projected, anchor.year + years)
        projections[label] = entry

    result = {
        "status": "PASS",
        "method": "current institutional environment held constant; sparse training plus annual body maturation advance by calendar year; natural death excluded for capability projection",
        "anchor": anchor.isoformat(),
        "projections": projections,
    }
    out = Path(args.json)
    if not out.is_absolute():
        out = ROOT / out
    write(out, result)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
