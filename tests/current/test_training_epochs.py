from copy import deepcopy

from shinobi_runtime.martial_world.training import (
    _apply_segment,
    _snapshot_segment,
    advance_faction_training_epoch,
    apply_institutional_training,
    settle_and_reset_faction_training_cycle,
    training_epoch_elapsed_days,
)


def _person(pid, sword, instruction=0):
    return {
        "person_id": pid,
        "membership_grade": "full",
        "birth_year": 30,
        "attributes": {"strength": 50, "speed": 50, "agility": 50, "endurance": 50, "perception": 50, "intelligence": 50, "willpower": 50},
        "martial_skills": {"sword": sword, "spear": 0, "bow": 0, "hidden_weapons": 0, "unarmed": 0, "stealth_scouting": 0, "command": 0},
        "professional_skills": {"instruction": instruction, "medicine": 0, "crafting": 0, "commerce": 0, "administration": 0},
        "aptitudes": {"martial": 100, "physical": 100, "cognitive": 100, "qi": 100, "leadership": 100},
        "health": {"status": "ready", "consciousness": 100, "injuries": []},
        "qi": 20,
        "qi_control": 20,
    }


def _faction(intensity=1000):
    return {
        "faction_id": "test_school",
        "training": {"sword": 100, "spear": 0, "bow": 0, "hidden_weapons": 0, "unarmed": 0, "stealth_scouting": 0, "command": 0, "qi": 0, "qi_control": 0},
        "buildings": {"training_hall": 2, "training_grounds": 2, "qi_hall": 0, "library_records": 1},
        "infrastructure": {"estate_area_m2": 6000, "facilities": {
            "training_hall": {"footprint_m2": 1000},
            "training_grounds": {"footprint_m2": 4000},
            "library_records": {"footprint_m2": 500},
        }},
        "training_epoch": {"started_at": "0061-01-01T00:00:00", "settled_through": "0061-01-01T00:00:00", "intensity_milli": intensity},
    }


def test_training_clock_has_no_persisted_environment_or_history():
    student = _person("student", 20)
    teacher = _person("teacher", 120, 120)
    roster = {"faction_ref": "test_school", "people": [student, teacher]}
    faction, summary = advance_faction_training_epoch(_faction(), roster, at_iso="0061-03-02T00:00:00")
    epoch = faction["training_epoch"]
    assert training_epoch_elapsed_days(faction) == 60
    assert summary["training_environment_segments"] == 0
    for derived in ("history", "current_environment", "elapsed_training_days", "curriculum_ref"):
        assert derived not in epoch
    after = apply_institutional_training(student, faction=faction, roster_people=roster["people"])
    assert after["martial_skills"]["sword"] >= student["martial_skills"]["sword"]


def test_intensity_milli_is_mechanical_not_dead_state():
    roster = {"faction_ref": "test_school", "people": [_person("student", 20), _person("teacher", 120, 120)]}
    low = _faction(500); high = _faction(1500)
    low, _ = advance_faction_training_epoch(low, roster, at_iso="0061-02-01T00:00:00")
    high, _ = advance_faction_training_epoch(high, roster, at_iso="0061-02-01T00:00:00")
    low_p = apply_institutional_training(roster["people"][0], faction=low, roster_people=roster["people"])
    high_p = apply_institutional_training(roster["people"][0], faction=high, roster_people=roster["people"])
    assert high_p["martial_skills"]["sword"] >= low_p["martial_skills"]["sword"]
    assert high_p["training_state"]["residual_milli"].get("sword", 0) != low_p["training_state"]["residual_milli"].get("sword", 0) or high_p["martial_skills"]["sword"] > low_p["martial_skills"]["sword"]


def test_paused_commitment_advances_person_anchor_without_training_gain():
    student = _person("student", 20)
    student["training_state"] = {"institutional_paused": True}
    roster = {"faction_ref": "test_school", "people": [student, _person("teacher", 120, 120)]}
    faction, _ = advance_faction_training_epoch(_faction(), roster, at_iso="0061-02-01T00:00:00")
    after = apply_institutional_training(student, faction=faction, roster_people=roster["people"])
    assert after["martial_skills"]["sword"] == 20
    assert after["training_state"]["institutional_days_applied"] == training_epoch_elapsed_days(faction)
    assert after["training_state"]["institutional_paused"] is True


def test_training_environment_is_derived_from_current_institution_and_instructors():
    student = _person("student", 20)
    instructor = _person("instructor", 160, 140)
    roster = {"faction_ref": "test_school", "people": [student, instructor]}
    faction, _ = advance_faction_training_epoch(deepcopy(_faction()), roster, at_iso="0061-02-15T00:00:00")
    segment = _snapshot_segment(faction, roster["people"], start_day=0, started_at="0061-01-01T00:00:00")
    assert "direct_teaching" not in segment
    assert segment["instruction_profiles"]["sword"]["instructor_skill"] >= 100
    assert "current_environment" not in faction["training_epoch"]


def test_derived_training_snapshot_is_aggregate_not_saved_student_bloat():
    people = [_person(f"student.{i:04d}", 20) for i in range(1000)] + [_person("teacher", 180, 180)]
    roster = {"faction_ref": "test_school", "people": people}
    faction, _ = advance_faction_training_epoch(_faction(), roster, at_iso="0061-02-01T00:00:00")
    segment = _snapshot_segment(faction, people, start_day=0, started_at="0061-01-01T00:00:00")
    assert segment["eligible_student_count"] == 1001
    assert "eligible_student_refs" not in segment
    assert "current_environment" not in faction["training_epoch"]


def test_child_development_gate_advances_across_long_sparse_epoch_without_history_rows():
    child = _person("child", 20)
    child["birth_year"] = 55
    child["aptitudes"] = {"martial": 200, "physical": 200, "cognitive": 200, "qi": 200, "leadership": 200}
    teacher = _person("teacher", 180, 180)
    roster = {"faction_ref": "test_school", "people": [child, teacher]}
    faction, _ = advance_faction_training_epoch(_faction(), roster, at_iso="0071-01-01T00:00:00")
    result = apply_institutional_training(child, faction=faction, roster_people=roster["people"])
    assert result["martial_skills"]["sword"] > child["martial_skills"]["sword"]
    assert result["attributes"]["strength"] >= child["attributes"]["strength"]
    assert result["training_state"]["institutional_days_applied"] == training_epoch_elapsed_days(faction)
    assert "history" not in faction["training_epoch"]


def test_professional_curriculum_requires_derived_specialist_assignment_or_office():
    student = _person("student", 20)
    student["professional_skills"]["medicine"] = 20
    segment = {
        "started_at": "0061-01-01T00:00:00",
        "facilities": {"training_hall": 5, "training_grounds": 5, "infirmary_apothecary": 5},
        "curriculum": {"sword": 100, "medicine": 100},
        "routine_duty_by_person": {"student": "infirmary_service"},
        "intensity_milli": 1000,
    }
    after = deepcopy(student); state = {}
    _apply_segment(after, state, segment, days=3650)
    assert after["professional_skills"]["medicine"] > 20
    assert "medicine" not in after["martial_skills"]
    assert "standing_duty_ref" not in after


def test_monthly_training_cycle_materializes_people_and_resets_to_small_clock():
    student = _person("student", 20)
    teacher = _person("teacher", 140, 140)
    roster = {"faction_ref": "test_school", "people": [student, teacher]}
    faction = _faction()
    prior_sword = student["martial_skills"]["sword"]
    faction, roster, summary = settle_and_reset_faction_training_cycle(
        faction, roster, at_iso="0061-02-01T00:00:00",
    )
    epoch = faction["training_epoch"]
    assert set(epoch) == {"started_at", "settled_through", "intensity_milli"}
    assert training_epoch_elapsed_days(faction) == 0
    assert summary["training_people_settled"] == 2
    for person in roster["people"]:
        assert "institutional_days_applied" not in person.get("training_state", {})
    assert roster["people"][0]["martial_skills"]["sword"] >= prior_sword


def test_training_residual_storage_roundtrip_uses_compact_carry():
    from shinobi_runtime.martial_world.person_state import compact_person_state, hydrate_person_state

    logical = _person("student", 20)
    logical["training_state"] = {
        "residual_milli": {
            "attribute:strength": 111,
            "attribute:endurance": 222,
            "sword": 333,
            "qi": 444,
            "qi_control": 555,
        }
    }
    stored = compact_person_state(logical, faction_ref="test_school", home_location="site.test")
    assert "training_state" not in stored
    assert stored["training_carry_milli"] == "111,0,0,222,0,0,0,333,0,0,0,0,0,0,444,555"
    hydrated = hydrate_person_state(stored, faction_ref="test_school", home_location="site.test")
    assert "training_carry_milli" not in hydrated
    assert hydrated["training_state"]["residual_milli"] == logical["training_state"]["residual_milli"]
    stored_again = compact_person_state(hydrated, faction_ref="test_school", home_location="site.test")
    assert stored_again == stored


def test_independent_recruit_hydrates_packed_training_carry_before_same_frontier_settlement():
    from shinobi_runtime.martial_world.autonomy_frontier import _hydrate_recruited_independent
    from shinobi_runtime.martial_world.person_state import compact_person_state

    prior_member = _person("transfer.student", 20)
    prior_member["training_state"] = {
        "residual_milli": {
            "attribute:strength": 145,
            "attribute:endurance": 88,
            "sword": 965,
            "qi": 832,
            "qi_control": 934,
        }
    }
    stored_independent = compact_person_state(
        prior_member, faction_ref="former_school", home_location="site.former",
    )
    stored_independent.pop("membership_grade", None)
    stored_independent["former_faction_ref"] = "former_school"
    stored_independent["independent_since"] = "0060-01-01T00:00:00"
    stored_independent["location_ref"] = "site.test"

    recruited = _hydrate_recruited_independent(
        stored_independent, faction_ref="test_school", home_site="site.test",
    )
    recruited["membership_grade"] = "probationary"
    recruited.pop("former_faction_ref", None)
    recruited.pop("independent_since", None)

    assert "training_carry_milli" not in recruited
    assert recruited["training_state"]["residual_milli"]["sword"] == 965

    teacher = _person("teacher", 140, 140)
    roster = {"faction_ref": "test_school", "people": [recruited, teacher]}
    faction, roster, _summary = settle_and_reset_faction_training_cycle(
        _faction(), roster, at_iso="0061-02-01T00:00:00",
    )
    settled = next(row for row in roster["people"] if row["person_id"] == "transfer.student")
    stored_after = compact_person_state(
        settled, faction_ref="test_school", home_location="site.test",
    )
    assert "residual_milli" not in stored_after.get("training_state", {})
    assert "training_carry_milli" in stored_after



def test_training_residual_vector_storage_is_not_a_current_person_contract():
    import pytest
    from shinobi_runtime.martial_world.person_state import compact_person_state, hydrate_person_state

    logical = _person("student", 20)
    logical["training_state"] = {"residual_vector": [111, 0, 0, 222]}
    with pytest.raises(ValueError, match="noncanonical|unknown|residual"):
        compact_person_state(logical, faction_ref="test_school", home_location="site.test")

    stored = _person("student", 20)
    stored["training_state"] = {"residual_vector": [111, 0, 0, 222]}
    with pytest.raises(ValueError, match="noncanonical|unknown|residual"):
        hydrate_person_state(stored, faction_ref="test_school", home_location="site.test")

def test_training_input_change_applies_only_after_exact_settlement_frontier():
    """A new instructor cannot improve training that happened before they joined."""
    student = _person("student", 20)
    weak = _person("weak_teacher", 45, 10)
    strong = _person("strong_teacher", 180, 180)

    split_faction = _faction()
    split_roster = {"faction_ref": "test_school", "people": [deepcopy(student), deepcopy(weak)]}
    split_faction, split_roster, _ = settle_and_reset_faction_training_cycle(
        split_faction, split_roster, at_iso="0061-01-16T00:00:00",
    )
    split_roster["people"].append(deepcopy(strong))
    split_faction, split_roster, _ = settle_and_reset_faction_training_cycle(
        split_faction, split_roster, at_iso="0061-01-31T00:00:00",
    )
    split_student = next(p for p in split_roster["people"] if p["person_id"] == "student")

    retro_faction = _faction()
    retro_roster = {"faction_ref": "test_school", "people": [deepcopy(student), deepcopy(weak), deepcopy(strong)]}
    retro_faction, retro_roster, _ = settle_and_reset_faction_training_cycle(
        retro_faction, retro_roster, at_iso="0061-01-31T00:00:00",
    )
    retro_student = next(p for p in retro_roster["people"] if p["person_id"] == "student")

    weak_faction = _faction()
    weak_roster = {"faction_ref": "test_school", "people": [deepcopy(student), deepcopy(weak)]}
    weak_faction, weak_roster, _ = settle_and_reset_faction_training_cycle(
        weak_faction, weak_roster, at_iso="0061-01-31T00:00:00",
    )
    weak_student = next(p for p in weak_roster["people"] if p["person_id"] == "student")

    split_score = split_student["martial_skills"]["sword"] * 1000 + split_student.get("training_state", {}).get("residual_milli", {}).get("sword", 0)
    retro_score = retro_student["martial_skills"]["sword"] * 1000 + retro_student.get("training_state", {}).get("residual_milli", {}).get("sword", 0)
    weak_score = weak_student["martial_skills"]["sword"] * 1000 + weak_student.get("training_state", {}).get("residual_milli", {}).get("sword", 0)

    assert weak_score <= split_score < retro_score
