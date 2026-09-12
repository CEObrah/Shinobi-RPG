"""Immutable scenario fixtures reconstructed from real gameplay failures.

These fixtures intentionally do not read ``state/``. They preserve the minimum
historical world facts required to reproduce a gameplay invariant while the
live campaign continues advancing independently.
"""
from __future__ import annotations

import shinobi_runtime.martial_world.exact_combat as exact

PLAYER = "fixture.player"
HAN = "fixture.medic"
YAO = "fixture.casualty"
ENEMY = "fixture.enemy"


def _person(ref: str, faction: str, *, medicine: int = 0) -> dict:
    return {
        "person_id": ref,
        "faction_ref": faction,
        "body_mass_kg": 70,
        "attributes": {
            "strength": 60,
            "speed": 60,
            "dexterity": 80,
            "endurance": 60,
            "perception": 80,
            "intelligence": 80,
            "willpower": 70,
        },
        "martial_skills": {
            "unarmed": 60,
            "sword": 0,
            "spear": 0,
            "bow": 0,
            "hidden_weapons": 0,
            "command": 30,
        },
        "professional_skills": {"medicine": medicine},
        "qi": 0,
        "qi_control": 0,
        "fatigue_milli": 0,
        "health": {
            "status": "ready",
            "injuries": [],
            "blood_lost_ml": 0,
            "shock": 0,
            "consciousness": 100,
        },
        "poison_burdens": {},
        "pending_poison_burdens": {},
    }


def played_medic_fixture() -> tuple[dict, dict, dict]:
    """Reconstruct the September 3 attack-plus-medic gameplay failure."""
    people = {
        PLAYER: _person(PLAYER, "house_tang"),
        HAN: _person(HAN, "house_tang", medicine=85),
        YAO: _person(YAO, "house_tang"),
        ENEMY: _person(ENEMY, "fixture_enemy"),
    }
    people[YAO]["health"] = {
        "status": "wounded",
        "blood_lost_ml": 100,
        "shock": 30,
        "consciousness": 90,
        "injuries": [{
            "wound_id": "fixture.wound.left_arm",
            "zone": "left_arm",
            "severity": 55,
            "bleeding_ml_per_min": 180,
            "pain": 40,
            "organ_trauma": 0,
            "structure_damage": 45,
            "stabilized": False,
            "function_loss_pct": 60,
            "cut": 60,
            "pierce": 0,
            "blunt": 0,
            "penetration": 0,
            "fracture": 0,
            "tendon_damage": 20,
            "nerve_damage": 0,
            "treated": False,
        }],
    }
    ledger = {
        "schema": "jianghu-equipment-ledger-1.0",
        "policy_assignments": {},
        "person_loadouts": {
            PLAYER: {"items": {}, "condition_milli": {}},
            HAN: {
                "items": {"tool_physicians_kit": 1, "supply_medical_bundle": 2},
                "condition_milli": {},
            },
            YAO: {"items": {}, "condition_milli": {}},
            ENEMY: {"items": {}, "condition_milli": {}},
        },
        "provenance_exceptions": {
            HAN: {
                "supply_medical_bundle": {
                    "owner_ref": "house_tang",
                    "quantity": 2,
                    "status": "retinue_role_issue",
                }
            }
        },
    }
    combat = exact.initialize_combat(
        combat_ref="fixture.played-medic-failure",
        side_a_refs=[PLAYER, HAN, YAO],
        side_b_refs=[ENEMY],
        people=people,
        zone_ref="fixture.road",
        started_at="SE-0061-09-27T21:16:12",
        objective={"kind": "eliminate", "target_refs": [ENEMY]},
        equipment_ledger=ledger,
    )
    combat["positions"][PLAYER].update(x_mm=0, y_mm=0)
    combat["positions"][HAN].update(x_mm=-1000, y_mm=1000)
    combat["positions"][YAO].update(x_mm=-500, y_mm=1000, stance="fallen", body_radius_mm=120)
    combat["positions"][ENEMY].update(x_mm=600, y_mm=0)
    combat["combatants"][YAO]["status_families"] = ["incapacitated"]
    return combat, people, ledger


def stage_treatment_fixture() -> tuple[dict, dict, dict, int]:
    combat, people, ledger = played_medic_fixture()
    combat["positions"][HAN]["x_mm"] = int(combat["positions"][YAO]["x_mm"]) - 500
    combat["positions"][HAN]["y_mm"] = int(combat["positions"][YAO]["y_mm"])
    start_ms = int(combat.get("elapsed_ms", 0))
    combat["combatants"][HAN]["support_task"] = {
        "task": "treat",
        "target_ref": YAO,
        "status": "active",
        "issued_by_ref": PLAYER,
        "issued_at_ms": start_ms,
    }
    return combat, people, ledger, start_ms
