from copy import deepcopy

from shinobi_runtime.martial_world.training import (
    advance_faction_training_epoch,
    apply_institutional_training,
    settle_and_reset_faction_training_cycle,
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
        "training_epoch": {"started_at": "0061-01-01T00:00:00", "settled_through": "0061-01-01T00:00:00", "elapsed_training_days": 0, "intensity_milli": intensity},
    }


def test_lazy_training_is_independent_of_when_person_is_read():
    student = _person("student", 20)
    weak = _person("teacher", 40, 50)
    roster = {"faction_ref": "test_school", "people": [student, weak]}
    faction = _faction()
    faction, _ = advance_faction_training_epoch(faction, roster, at_iso="0061-01-31T00:00:00")

    # Path A materializes at the midpoint.
    midpoint = apply_institutional_training(student, faction=faction, roster_people=roster["people"])
    strong = _person("teacher", 180, 180)
    roster_a = {"faction_ref": "test_school", "people": [midpoint, strong]}
    faction_a = deepcopy(faction)
    faction_a, _ = advance_faction_training_epoch(faction_a, roster_a, at_iso="0061-03-02T00:00:00")
    final_a = apply_institutional_training(midpoint, faction=faction_a, roster_people=roster_a["people"])

    # Path B is never materialized until the same final frontier.
    roster_b = {"faction_ref": "test_school", "people": [student, strong]}
    faction_b = deepcopy(faction)
    faction_b, _ = advance_faction_training_epoch(faction_b, roster_b, at_iso="0061-03-02T00:00:00")
    final_b = apply_institutional_training(student, faction=faction_b, roster_people=roster_b["people"])

    assert final_a["martial_skills"]["sword"] == final_b["martial_skills"]["sword"]
    assert faction_b["training_epoch"]["history"], "environment change must close an immutable segment"


def test_intensity_milli_is_mechanical_not_dead_state():
    roster = {"faction_ref": "test_school", "people": [_person("student", 20), _person("teacher", 120, 120)]}
    low = _faction(500); high = _faction(1500)
    low, _ = advance_faction_training_epoch(low, roster, at_iso="0061-02-01T00:00:00")
    high, _ = advance_faction_training_epoch(high, roster, at_iso="0061-02-01T00:00:00")
    low_p = apply_institutional_training(roster["people"][0], faction=low, roster_people=roster["people"])
    high_p = apply_institutional_training(roster["people"][0], faction=high, roster_people=roster["people"])
    assert high_p["martial_skills"]["sword"] >= low_p["martial_skills"]["sword"]
    assert high_p["training_state"]["residual_milli"].get("sword", 0) != low_p["training_state"]["residual_milli"].get("sword", 0) or high_p["martial_skills"]["sword"] > low_p["martial_skills"]["sword"]


def test_paused_commitment_advances_anchor_without_training_gain():
    student = _person("student", 20)
    student["training_state"] = {"institutional_paused": True}
    roster = {"faction_ref": "test_school", "people": [student, _person("teacher", 120, 120)]}
    faction = _faction()
    faction, _ = advance_faction_training_epoch(faction, roster, at_iso="0061-02-01T00:00:00")
    after = apply_institutional_training(student, faction=faction, roster_people=roster["people"])
    assert after["martial_skills"]["sword"] == 20
    assert after["training_state"]["institutional_days_applied"] == faction["training_epoch"]["elapsed_training_days"]
    assert after["training_state"]["institutional_paused"] is True


def test_training_environment_uses_institutional_instructors_without_personal_master_bonds():
    student = _person("student", 20)
    instructor = _person("instructor", 160, 140)
    roster = {"faction_ref": "test_school", "people": [student, instructor]}
    faction, _ = advance_faction_training_epoch(deepcopy(_faction()), roster, at_iso="0061-02-15T00:00:00")
    env = faction["training_epoch"]["current_environment"]
    assert "direct_teaching" not in env
    assert env["instruction_profiles"]["sword"]["instructor_skill"] >= 100


def test_training_environment_snapshot_is_aggregate_not_per_student_bloat():
    people = [_person(f"student.{i:04d}", 20) for i in range(1000)] + [_person("teacher", 180, 180)]
    roster = {"faction_ref": "test_school", "people": people}
    faction, _ = advance_faction_training_epoch(_faction(), roster, at_iso="0061-02-01T00:00:00")
    env = faction["training_epoch"]["current_environment"]
    assert env["eligible_student_count"] == 1001
    assert "eligible_student_refs" not in env
    assert len(str(env)) < 25_000


def test_child_development_gate_advances_across_long_immutable_epoch():
    from datetime import datetime

    child = _person("child", 20)
    child["birth_year"] = 55  # age six at the epoch anchor
    child["aptitudes"] = {"martial": 200, "physical": 200, "cognitive": 200, "qi": 200, "leadership": 200}
    teacher = _person("teacher", 180, 180)
    roster = {"faction_ref": "test_school", "people": [child, teacher]}

    long_faction = _faction()
    long_faction, _ = advance_faction_training_epoch(long_faction, roster, at_iso="0071-01-01T00:00:00")
    long_result = apply_institutional_training(child, faction=long_faction, roster_people=roster["people"])

    # Build the same immutable environment as explicit annual slices.  A long
    # sparse epoch must produce the same age-gated development without storing
    # those annual slices in campaign state.
    source = dict(long_faction["training_epoch"]["current_environment"])
    history = []
    absolute_day = 0
    for year in range(61, 71):
        started = datetime(year, 1, 1)
        ended = datetime(year + 1, 1, 1)
        days = (ended - started).days
        seg = dict(source)
        seg["segment_id"] = f"manual.{year}"
        seg["start_day"] = absolute_day
        seg["end_day"] = absolute_day + days
        seg["started_at"] = started.isoformat()
        seg["settled_through"] = ended.isoformat()
        history.append(seg)
        absolute_day += days
    annual_faction = deepcopy(long_faction)
    annual_faction["training_epoch"]["history"] = history
    annual_faction["training_epoch"]["current_environment"] = None
    annual_faction["training_epoch"]["elapsed_training_days"] = absolute_day
    annual_result = apply_institutional_training(child, faction=annual_faction, roster_people=roster["people"])

    assert long_result["martial_skills"]["sword"] == annual_result["martial_skills"]["sword"]
    assert long_result["attributes"]["strength"] == annual_result["attributes"]["strength"]
    assert long_result["training_state"]["institutional_days_applied"] == absolute_day


def test_professional_curriculum_does_not_leak_into_martial_skills():
    student = _person("student", 20)
    segment = {
        "started_at": "0061-01-01T00:00:00",
        "facilities": {"training_hall": 5, "training_grounds": 5, "qi_hall": 5, "infirmary_apothecary": 5, "library_records": 5, "armory_workshop": 5, "main_hall": 5},
        "curriculum": {"sword": 100, "unarmed": 60, "qi": 80, "qi_control": 80, "medicine": 80, "administration": 80, "commerce": 80, "crafting": 80, "instruction": 80},
        "intensity_milli": 1000,
    }
    from shinobi_runtime.martial_world.training import _apply_segment
    after = deepcopy(student); state = {}
    _apply_segment(after, state, segment, days=365)
    for invalid in ("medicine", "administration", "commerce", "crafting", "instruction"):
        assert invalid not in after["martial_skills"]
    assert after["professional_skills"] == student["professional_skills"]
    assert after["martial_skills"]["sword"] > student["martial_skills"]["sword"]


def test_professional_training_requires_matching_current_duty_or_office():
    student = _person("student", 20)
    student["standing_duty_ref"] = "infirmary_service"
    student["professional_skills"]["medicine"] = 20
    segment = {
        "started_at": "0061-01-01T00:00:00",
        "facilities": {"training_hall": 5, "training_grounds": 5, "infirmary_apothecary": 5},
        "curriculum": {"sword": 100, "medicine": 100},
        "intensity_milli": 1000,
    }
    from shinobi_runtime.martial_world.training import _apply_segment
    after = deepcopy(student); state = {}
    _apply_segment(after, state, segment, days=3650)
    assert after["professional_skills"]["medicine"] > 20
    assert "medicine" not in after["martial_skills"]


def test_monthly_training_cycle_materializes_people_and_prunes_consumed_environment_history():
    student = _person("student", 20)
    teacher = _person("teacher", 140, 140)
    roster = {"faction_ref": "test_school", "people": [student, teacher]}
    faction = _faction()
    prior_sword = student["martial_skills"]["sword"]
    for month, at_iso in enumerate((
        "0061-02-01T00:00:00",
        "0061-03-03T00:00:00",
        "0061-04-02T00:00:00",
    ), start=1):
        # Force a real within-epoch environment rotation before the monthly
        # closure.  The lazy segments are required until everybody is caught
        # up, then must disappear because they have no future consumer.
        if month == 2:
            roster["people"][1]["martial_skills"]["sword"] = 180
            faction, _ = advance_faction_training_epoch(
                faction, roster, at_iso="0061-02-15T00:00:00", refresh_environment=True,
            )
        faction, roster, summary = settle_and_reset_faction_training_cycle(
            faction, roster, at_iso=at_iso,
        )
        epoch = faction["training_epoch"]
        assert epoch["elapsed_training_days"] == 0
        assert epoch["history"] == []
        assert epoch["current_environment"]["start_day"] == 0
        assert epoch["current_environment"]["end_day"] == 0
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
