import json
from pathlib import Path

from shinobi_runtime.martial_world.duties import (
    duty_staffing_requirements,
    duty_training_availability_milli,
    reassign_standing_duties,
)
from shinobi_runtime.martial_world.manpower import combat_ready_count, combat_ready_members

ROOT = Path(__file__).resolve().parents[2]


def load(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def person(ref: str, *, grade: str = "junior", birth_year: int = 40, sword: int = 30, medicine: int = 0):
    return {
        "person_id": ref,
        "birth_year": birth_year,
        "membership_grade": grade,
        "attributes": {
            "strength": 50, "speed": 50, "dexterity": 50,
            "endurance": 50, "perception": 50, "intelligence": 50,
        },
        "martial_skills": {"sword": sword},
        "professional_skills": {"medicine": medicine},
        "health": {"status": "ready", "consciousness": 100},
    }


def test_every_persistent_faction_person_is_on_the_martial_membership_ladder():
    grades = {"probationary", "junior", "full", "senior", "elite", "elder"}
    total = 0
    for path in sorted((ROOT / "state/martial-world/people").glob("*.json")):
        roster = json.loads(path.read_text(encoding="utf-8"))
        for row in roster.get("people", []):
            total += 1
            assert row.get("membership_grade") in grades
            assert "martial_member" not in row
            assert row.get("membership_grade") != "support"
    assert total == 11691
    for path in sorted((ROOT / "state/martial-world/factions").glob("*.json")):
        faction = json.loads(path.read_text(encoding="utf-8"))
        assert "martial_members" not in faction
        assert "household_support_members" not in faction


def test_mundane_duties_rotate_and_strong_elite_is_not_left_as_permanent_cook():
    faction = {
        "faction_id": "test_rotation_house",
        "population": 40,
        "buildings": {"residential_compound": 1},
        "enterprises": {},
        "infrastructure": {},
    }
    elite = person("elite", grade="elite", sword=140)
    elite["standing_duty_ref"] = "kitchen_service"
    people = [elite] + [person(f"j{i:02}", grade="junior", sword=30 + i % 5) for i in range(12)]

    q1 = reassign_standing_duties(faction, people, year=61, month=1, protected_refs=[])
    q2 = reassign_standing_duties(faction, people, year=61, month=4, protected_refs=[])
    q1_assignments = {
        row["person_id"]: row.get("standing_duty_ref")
        for row in q1["people_after"] if row.get("standing_duty_ref")
    }
    q2_assignments = {
        row["person_id"]: row.get("standing_duty_ref")
        for row in q2["people_after"] if row.get("standing_duty_ref")
    }
    assert "elite" not in q1_assignments
    assert "elite" not in q2_assignments
    assert q1_assignments != q2_assignments
    assert set(q1_assignments.values()) == {"kitchen_service", "general_household_service"}
    assert set(q2_assignments.values()) == {"kitchen_service", "general_household_service"}


def test_specialist_duty_follows_real_skill_and_current_duty_consumes_training_time():
    tang = load("state/martial-world/factions/house_tang.json")
    physician = person("physician", grade="senior", sword=80, medicine=120)
    ordinary = [person(f"ordinary.{i}", medicine=12 + i) for i in range(8)]
    result = reassign_standing_duties(tang, [physician, *ordinary], year=61, month=8, protected_refs=[])
    physician_after = next(row for row in result["people_after"] if row["person_id"] == "physician")
    assert physician_after.get("standing_duty_ref") == "infirmary_service"
    assert result["requirements"]["infirmary_service"] > 0
    assert duty_training_availability_milli({"standing_duty_ref": "infirmary_service"}) == 560
    assert duty_training_availability_milli({"standing_duty_ref": "kitchen_service"}) == 700
    assert duty_training_availability_milli({}) == 1000


def test_combat_manpower_is_derived_from_real_current_people_not_membership_headcount():
    ready = person("ready", grade="senior", birth_year=30, sword=90)
    child = person("child", grade="probationary", birth_year=55, sword=50)
    weak = person("weak", grade="full", birth_year=30, sword=10)
    unavailable = person("away", grade="elite", birth_year=25, sword=130)
    rows = [ready, child, weak, unavailable]
    assert combat_ready_count(rows, year=61, unavailable_refs={"away"}, minimum_age=14, minimum_combat_skill=20) == 1
    assert [row["person_id"] for row in combat_ready_members(rows, year=61, unavailable_refs={"away"})] == ["ready"]


def test_current_world_duty_requirements_are_scale_derived_not_rank_counts():
    tang = load("state/martial-world/factions/house_tang.json")
    requirements = duty_staffing_requirements(tang)
    assert requirements["kitchen_service"] > 0
    assert requirements["records_administration"] > 0
    assert requirements["workshop_service"] > 0
    assert requirements["infirmary_service"] > 0
    assert "martial_members" not in tang
