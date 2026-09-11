from shinobi_runtime.martial_world.combat_simulation import finalize_autonomous_lethality


def _person(*, severity: int, organ_trauma: int, status: str = "incapacitated"):
    return {
        "person_id": "p",
        "body_mass_kg": 70,
        "attributes": {"endurance": 80, "willpower": 80},
        "health": {
            "status": status,
            "consciousness": 0 if status == "incapacitated" else 100,
            "blood_lost_ml": 0,
            "shock": 0,
            "injuries": [{
                "zone": "chest",
                "severity": severity,
                "organ_trauma": organ_trauma,
                "structure_damage": 0,
                "bleeding_ml_per_min": 0,
                "pain": severity,
            }],
        },
    }


def test_autonomous_lethal_combat_closes_unattended_dying_casualty_as_death():
    after = finalize_autonomous_lethality({"p": _person(severity=200, organ_trauma=200)}, targeting_intent="lethal")
    assert after["p"]["health"]["status"] == "dead"
    assert after["p"]["health"]["consciousness"] == 0


def test_disable_intent_does_not_override_physically_fatal_autonomous_trauma():
    after = finalize_autonomous_lethality({"p": _person(severity=200, organ_trauma=200)}, targeting_intent="disable")
    assert after["p"]["health"]["status"] == "dead"
    assert after["p"]["health"]["consciousness"] == 0


def test_autonomous_lethal_combat_does_not_kill_nonfatal_injury():
    after = finalize_autonomous_lethality({"p": _person(severity=90, organ_trauma=90, status="injured")}, targeting_intent="lethal")
    assert after["p"]["health"]["status"] == "injured"


def test_autonomous_timestamp_rebase_keeps_world_frontier_authoritative():
    from datetime import datetime, timedelta
    from shinobi_runtime.martial_world.combat_simulation import _rebase_autonomous_person_timestamps

    frontier = "0061-10-19T21:15:00"
    base = datetime.fromisoformat(frontier)
    before = {
        "person_id": "p",
        "medicine_state": {
            "last_settled_at": frontier,
            "category_saturation_milli": {"bone": 40000},
            "toxicity_milli": 0,
            "active_effects": [{
                "recipe_ref": "bone_medicine", "category": "bone",
                "started_at": "0061-10-13T21:15:00", "expires_at": "0061-10-20T21:15:00",
                "modifiers": {"fracture_healing_rate_milli": 1250},
            }],
        },
        "pending_poison_burdens": {"paralytic": {"burden": 5, "activates_at": "0061-10-19T21:20:00"}},
        "health": {"status": "injured", "injuries": [{"zone": "elbow", "created_at": "0061-10-18T10:00:00"}]},
    }
    after = {
        **before,
        "medicine_state": {
            **before["medicine_state"],
            "last_settled_at": (base + timedelta(seconds=106)).isoformat(),
            "category_saturation_milli": {"bone": 39999},
        },
        "pending_poison_burdens": {
            "paralytic": {"burden": 5, "activates_at": "0061-10-19T21:20:00"},
            "paralytic#2": {
                "poison_ref": "paralytic", "burden": 4,
                "activates_at": (base + timedelta(seconds=136)).isoformat(),
                "peaks_at": (base + timedelta(seconds=256)).isoformat(), "stage": "onset",
            },
        },
        "health": {
            "status": "incapacitated",
            "dying_since": (base + timedelta(seconds=37)).isoformat(),
            "injuries": [
                {"zone": "elbow", "created_at": "0061-10-18T10:00:00"},
                {"zone": "chest", "created_at": "106000"},
            ],
        },
    }
    rebased = _rebase_autonomous_person_timestamps(after, frontier_at=frontier, before=before)
    assert rebased["medicine_state"] == before["medicine_state"]
    assert rebased["health"]["dying_since"] == frontier
    assert rebased["health"]["injuries"][0]["created_at"] == "0061-10-18T10:00:00"
    assert rebased["health"]["injuries"][1]["created_at"] == frontier
    # The pre-existing exposure keeps its world clock; the newly delivered dose
    # gets its own registered fast combat clock instead of merging timelines.
    assert rebased["pending_poison_burdens"]["paralytic"]["activates_at"] == "0061-10-19T21:20:00"
    assert rebased["pending_poison_burdens"]["paralytic#2"]["activates_at"] == "0061-10-19T21:15:30"
    assert rebased["pending_poison_burdens"]["paralytic#2"]["peaks_at"] == "0061-10-19T21:17:30"
