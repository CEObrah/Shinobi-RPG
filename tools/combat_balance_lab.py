#!/usr/bin/env python3
"""Deterministic gameplay-level combat certification report.

This is intentionally different from a unit-test runner. It exercises the real
combat formulas and a short exact duel at representative stat bands, then emits
human-readable/JSON metrics that make balance regressions visible during release
review.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from shinobi_runtime.api.operations import _player_equipment_projection
from shinobi_runtime.martial_world.combat_exertion import fatigue_performance_milli
from shinobi_runtime.martial_world.equipment import weapon_contact_profile
from shinobi_runtime.martial_world.exact_combat import capability_from_person, initialize_combat, resolve_exchange

BASE = json.loads((ROOT / "state/martial-world/people/house_tang.json").read_text())["people"][0]


def fighter(ref: str, *, stat: int = 70, discipline: str = "sword", skill: int = 70, fatigue: int = 0) -> dict[str, Any]:
    row = copy.deepcopy(BASE)
    row["person_id"] = ref
    row["name"] = ref
    row["faction_ref"] = f"faction.{ref}"
    row["fatigue_milli"] = fatigue
    row["health"] = {"status": "ready", "injuries": [], "blood_lost_ml": 0, "shock": 0, "consciousness": 100}
    row["poison_burdens"] = {}
    row["pending_poison_burdens"] = {}
    row["qi"] = 0
    row["current_qi_milli"] = 0
    row["qi_control"] = stat
    row["attributes"] = {key: stat for key in ("strength", "speed", "dexterity", "endurance", "perception", "intelligence", "willpower")}
    row["martial_skills"] = {key: 0 for key in ("sword", "spear", "bow", "hidden_weapons", "unarmed", "stealth_scouting", "command")}
    row["martial_skills"][discipline] = skill
    row.pop("combat_doctrine_ref", None)
    return row


def ledger(*rows: tuple[str, str]) -> dict[str, Any]:
    return {
        "schema": "jianghu-equipment-ledger-1.0",
        "policy_assignments": {},
        "person_loadouts": {ref: {"items": {weapon: 1}} for ref, weapon in rows},
    }


def skill_sweep(discipline: str) -> list[dict[str, int]]:
    out = []
    for skill in (40, 60, 80, 100, 120):
        cap = capability_from_person(fighter(f"{discipline}.{skill}", discipline=discipline, skill=skill), action_skill=discipline)
        out.append({"skill": skill, "offense": cap.offense, "control": cap.control, "defense": cap.defense})
    return out


def equal_duel() -> dict[str, Any]:
    people = {"a": fighter("a", stat=70, skill=70), "b": fighter("b", stat=70, skill=70)}
    gear = ledger(("a", "weapon_jian"), ("b", "weapon_jian"))
    combat = initialize_combat(
        combat_ref="balance.equal.jian", side_a_refs=("a",), side_b_refs=("b",), people=people,
        zone_ref="z", started_at="x", objective={"kind": "eliminate", "target_refs": ["b"]},
        initial_range_band=0, equipment_ledger=gear,
        initial_ready_weapons={"a": "weapon_jian", "b": "weapon_jian"},
    )
    attempts = {"a": 0, "b": 0}
    contacts = {"a": 0, "b": 0}
    defended = {"a": 0, "b": 0}
    for _ in range(3):
        if combat.get("status") != "active":
            break
        result = resolve_exchange(
            combat=combat, people=people, equipment_ledger=gear, doctrines={}, player_ref="a",
            player_action_kind="thrust", player_target_ref="b", player_weapon_ref="weapon_jian",
            player_hit_zone="chest", player_targeting_intent="disable", compact_equipment_result=False,
        )
        for event in result["events"]:
            actor = event.get("actor_ref")
            if actor in attempts and event.get("action_kind") in {"thrust", "cut"}:
                attempts[actor] += 1
            if actor in contacts and event.get("result") == "contact":
                contacts[actor] += 1
            target = event.get("intended_ref")
            defense = event.get("defense") if isinstance(event.get("defense"), dict) else {}
            if target in defended and defense.get("response") not in {None, "none"}:
                defended[target] += 1
        combat, people, gear = result["combat_after"], result["people_after"], result["equipment_ledger_after"]
    return {"attempts": attempts, "contacts": contacts, "active_defenses": defended, "status": combat.get("status")}



def black_lance_group_replay() -> dict[str, Any]:
    """Replay the immutable fresh 12-v-18 encounter used by live certification."""
    fixture = json.loads((ROOT / "tests/fixtures/black_lance_fresh_group_certification.json").read_text())
    combat = copy.deepcopy(fixture["combat"])
    people = copy.deepcopy(fixture["people"])
    gear = copy.deepcopy(fixture["equipment_ledger"])
    doctrines = copy.deepcopy(fixture.get("doctrines", {}))
    player_ref = str(fixture["player_ref"])
    player_target_ref = str(fixture["player_target_ref"])
    retinue = copy.deepcopy(fixture["retinue_context"])
    a_refs = set(combat["sides"]["side_a"])
    b_refs = set(combat["sides"]["side_b"])
    medic_ref = next(ref for ref, role in retinue.get("member_roles", {}).items() if role == "field_medic")
    contacts = {"tang": 0, "black_lance": 0}
    medical_holds = 0
    medic_offense = 0
    same_side_contacts = 0
    target_families: dict[str, int] = {}
    initial_plan = {"tang_attack": 0, "tang_hold": 0, "black_lance_attack": 0, "black_lance_hold": 0}
    for exchange_index in range(8):
        if combat.get("status") != "active":
            break
        if people[player_target_ref].get("health", {}).get("status") in {"dead", "incapacitated"}:
            player_target_ref = next(
                ref for ref in combat["sides"]["side_b"]
                if people[ref].get("health", {}).get("status") not in {"dead", "incapacitated"}
            )
        result = resolve_exchange(
            combat=combat, people=people, equipment_ledger=gear, doctrines=doctrines,
            player_ref=player_ref, player_action_kind="thrust", player_target_ref=player_target_ref,
            player_weapon_ref="weapon_jian", player_hit_zone="auto", player_targeting_intent="disable",
            player_retinue_context=retinue, compact_equipment_result=False,
        )
        if exchange_index == 0:
            plans = result["combat_after"].get("team_plans", {})
            for side, prefix in (("side_a", "tang"), ("side_b", "black_lance")):
                assignments = plans.get(side, {}).get("assignments", {})
                actions = [str(row.get("preferred_action") or "") for row in assignments.values() if isinstance(row, dict)]
                initial_plan[f"{prefix}_attack"] = actions.count("attack")
                initial_plan[f"{prefix}_hold"] = actions.count("hold")
        for event in result["events"]:
            actor = event.get("actor_ref")
            actual = event.get("actual_ref")
            if event.get("result") == "holding_medical_support_position":
                medical_holds += 1
            if actor == medic_ref and event.get("action_kind") in {"thrust", "cut", "hidden_weapon_throw", "unarmed_strike"}:
                medic_offense += 1
            if event.get("result") == "contact":
                if actor in a_refs:
                    contacts["tang"] += 1
                elif actor in b_refs:
                    contacts["black_lance"] += 1
                if actor in a_refs | b_refs and actual in a_refs | b_refs and ((actor in a_refs) == (actual in a_refs)):
                    same_side_contacts += 1
            if actor in b_refs and isinstance(event.get("target_structure_ref"), str):
                structure = str(event["target_structure_ref"])
                family = next((name for name in ("wrist", "elbow", "knee", "ankle") if name in structure), None)
                if family:
                    target_families[family] = target_families.get(family, 0) + 1
        combat, people, gear = result["combat_after"], result["people_after"], result["equipment_ledger_after"]
    return {
        "contacts": contacts,
        "initial_plan": initial_plan,
        "medical_support_holds": medical_holds,
        "medic_offensive_actions": medic_offense,
        "same_side_contacts": same_side_contacts,
        "target_families": target_families,
        "temporary_companion_count": len(retinue.get("temporary_member_refs", [])),
    }

def build_report() -> dict[str, Any]:
    equipment = json.loads((ROOT / "game/data/martial-world/equipment.json").read_text())["weapon_catalog"]
    jian = equipment["weapon_jian"]
    spear = equipment["weapon_spear"]
    range_rows = []
    for distance in (0.7, 1.0, 2.0, 3.0):
        jp = weapon_contact_profile(jian, skill=80, strength=80, dexterity=80, range_m=distance)
        sp = weapon_contact_profile(spear, skill=80, strength=80, dexterity=80, range_m=distance)
        range_rows.append({
            "distance_m": distance,
            "jian_in_reach": bool(jp["in_reach"]), "jian_range_milli": int(jp["range_factor_milli"]),
            "spear_in_reach": bool(sp["in_reach"]), "spear_range_milli": int(sp["range_factor_milli"]),
        })

    meta = json.loads((ROOT / "state/meta.json").read_text())
    eq_ledger = json.loads((ROOT / "state/martial-world/equipment-ledger.json").read_text())
    player_id = str(meta["player_id"])
    player = next(
        row for path in (ROOT / "state/martial-world/people").glob("*.json")
        for row in json.loads(path.read_text()).get("people", [])
        if isinstance(row, dict) and row.get("person_id") == player_id
    )
    player_cap = capability_from_person(player, action_skill="sword")
    fatigue_rows = []
    for fatigue in (0, 1500, 3000):
        cap = capability_from_person(fighter(f"fatigue.{fatigue}", stat=80, discipline="sword", skill=80, fatigue=fatigue), action_skill="sword")
        fatigue_rows.append({"fatigue_milli": fatigue, "performance_milli": fatigue_performance_milli(fatigue), "offense": cap.offense, "mobility": cap.mobility})
    sword = skill_sweep("sword")
    spear_skill = skill_sweep("spear")
    duel = equal_duel()
    black_lance = black_lance_group_replay()

    contacts_12v18 = black_lance["contacts"]
    target_counts_12v18 = list(black_lance["target_families"].values())
    checks = {
        "sword_skill_monotonic": all(a["offense"] < b["offense"] and a["control"] < b["control"] for a, b in zip(sword, sword[1:])),
        "spear_skill_monotonic": all(a["offense"] < b["offense"] and a["control"] < b["control"] for a, b in zip(spear_skill, spear_skill[1:])),
        "spear_has_far_reach_advantage": range_rows[2]["spear_in_reach"] and not range_rows[2]["jian_in_reach"],
        "spear_loses_ideal_range_inside": range_rows[0]["spear_range_milli"] < range_rows[0]["jian_range_milli"],
        "equal_duel_both_sides_act": duel["attempts"]["a"] >= 1 and duel["attempts"]["b"] >= 1,
        "equal_duel_cadence_bounded": max(duel["attempts"].values()) <= max(1, min(duel["attempts"].values())) * 2,
        "equal_duel_contacts_symmetric": abs(duel["contacts"]["a"] - duel["contacts"]["b"]) <= 1,
        "equal_duel_has_real_defense": duel["active_defenses"]["a"] >= 1 and duel["active_defenses"]["b"] >= 1,
        "master_skill_materially_exceeds_ordinary": (sword[-1]["offense"] - sword[0]["offense"] >= 30 and sword[-1]["control"] - sword[0]["control"] >= 20),
        "fatigue_degrades_output_monotonically": all(a["performance_milli"] > b["performance_milli"] and a["offense"] > b["offense"] and a["mobility"] > b["mobility"] for a, b in zip(fatigue_rows, fatigue_rows[1:])),
        "black_lance_no_profession_locked_medic": black_lance["medical_support_holds"] == 0 and black_lance["medic_offensive_actions"] >= 1,
        "black_lance_no_autonomous_same_side_contacts": black_lance["same_side_contacts"] == 0,
        "black_lance_tang_frontage_not_collapsed": black_lance["initial_plan"]["tang_attack"] >= 7,
        "black_lance_group_contact_ratio_bounded": min(contacts_12v18.values()) >= 10 and max(contacts_12v18.values()) <= min(contacts_12v18.values()) * 3,
        "black_lance_disable_targeting_not_single_family": len(target_counts_12v18) >= 3 and max(target_counts_12v18) * 4 < sum(target_counts_12v18) * 3,
        "black_lance_legacy_party_membership_recovered": black_lance["temporary_companion_count"] == 8,
    }
    report = {
        "schema": "shinobi-combat-balance-lab-1.0",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "skill_sweeps": {"sword": sword, "spear": spear_skill},
        "weapon_range": range_rows,
        "fatigue_sweep": fatigue_rows,
        "equal_jian_duel": duel,
        "black_lance_12v18": black_lance,
        "live_player": {
            "player_id": player_id,
            "fatigue_milli": int(player.get("fatigue_milli", 0) or 0),
            "fatigue_performance_milli": fatigue_performance_milli(int(player.get("fatigue_milli", 0) or 0)),
            "sword_capability": {
                "offense": player_cap.offense, "defense": player_cap.defense, "control": player_cap.control,
                "mobility": player_cap.mobility, "reaction": player_cap.reaction,
            },
            "equipment_loadout": _player_equipment_projection(eq_ledger, player_id),
        },
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    report = build_report()
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"COMBAT BALANCE LAB {report['status']}")
    print("Checks:")
    for key, value in report["checks"].items():
        print(f"  {'PASS' if value else 'FAIL'} {key}")
    print("Sword skill sweep (skill/offense/control):", [(r["skill"], r["offense"], r["control"]) for r in report["skill_sweeps"]["sword"]])
    print("Spear skill sweep (skill/offense/control):", [(r["skill"], r["offense"], r["control"]) for r in report["skill_sweeps"]["spear"]])
    print("Equal jian duel:", report["equal_jian_duel"])
    print("Black Lance 12v18:", report["black_lance_12v18"])
    print("Fatigue sweep:", report["fatigue_sweep"])
    live = report["live_player"]
    print("Live player fatigue/performance:", live["fatigue_milli"], live["fatigue_performance_milli"])
    print("Live player sword capability:", live["sword_capability"])
    print("Live player loadout:", live["equipment_loadout"])
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
