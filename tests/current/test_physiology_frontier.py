

def person():
    return {
        "person_id": "p", "body_mass_kg": 70,
        "attributes": {"endurance": 60, "willpower": 60},
        "qi": 60, "qi_control": 60,
        "health": {"status": "ready", "injuries": [], "blood_lost_ml": 0, "shock": 0, "consciousness": 100},
    }


def test_background_exact_combat_never_advances_person_clock_past_frontier():
    from shinobi_runtime.martial_world.combat_simulation import simulate_exact_combat

    def fighter(ref):
        p = person()
        p["person_id"] = ref
        p["martial_skills"] = {
            "unarmed": 80, "sword": 0, "spear": 0, "bow": 0, "hidden_weapons": 0,
        }
        p["attributes"].update({"strength": 80, "speed": 80, "dexterity": 80, "perception": 80})
        p["medicine_state"] = {
            "last_settled_at": "0061-01-01T00:00:00",
            "category_saturation_milli": {},
            "toxicity_milli": 0,
            "active_effects": [{
                "recipe_ref": "wound_salve",
                "category": "wound",
                "started_at": "0061-01-01T00:00:00",
                "expires_at": "0061-01-02T00:00:00",
                "modifiers": {"external_wound_healing_rate_milli": 1250},
            }],
        }
        return p

    frontier_at = "0061-01-01T00:00:00"
    result = simulate_exact_combat(
        combat_ref="background-time-boundary",
        side_a_refs=["a"], side_b_refs=["b"],
        people={"a": fighter("a"), "b": fighter("b")},
        equipment_ledger={"person_loadouts": {"a": {"items": {}}, "b": {"items": {}}}},
        doctrines={}, zone_ref="test", started_at=frontier_at,
        objective={"kind": "background_contact"}, max_exchanges=2,
    )
    assert result["combat_elapsed_ms"] > 0
    for after in result["people_after"].values():
        assert after["medicine_state"]["last_settled_at"] == frontier_at
        for wound in after.get("health", {}).get("injuries", []):
            assert wound.get("created_at") == frontier_at
