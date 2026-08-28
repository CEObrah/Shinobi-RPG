import json
from pathlib import Path

from shinobi_runtime.martial_world.duties import (
    derive_duty_assignments,
    duty_staffing_requirements,
    duty_time_share_for_ref,
    routine_service_overhead_milli,
)
from shinobi_runtime.martial_world.manpower import combat_ready_count, combat_ready_members
from shinobi_runtime.martial_world.faction_state import (
    allows_independent_recruitment,
    allows_ordinary_membership_exit,
)
from shinobi_runtime.martial_world.world_health import annual_voluntary_departure_refs

ROOT = Path(__file__).resolve().parents[2]


def load(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def person(ref: str, *, grade: str = "junior", birth_year: int = 40, sword: int = 30, medicine: int = 0, commerce: int = 0, crafting: int = 0):
    return {
        "person_id": ref,
        "birth_year": birth_year,
        "membership_grade": grade,
        "attributes": {
            "strength": 50, "speed": 50, "dexterity": 50,
            "endurance": 50, "perception": 50, "intelligence": 50,
        },
        "martial_skills": {"sword": sword},
        "professional_skills": {"medicine": medicine, "commerce": commerce, "crafting": crafting},
        "health": {"status": "ready", "consciousness": 100},
    }


def test_every_persistent_faction_person_is_on_the_martial_membership_ladder_and_has_no_saved_duty():
    grades = {"probationary", "junior", "full", "senior", "elite", "elder"}
    total = 0
    for path in sorted((ROOT / "state/martial-world/people").glob("*.json")):
        roster = json.loads(path.read_text(encoding="utf-8"))
        for row in roster.get("people", []):
            total += 1
            assert row.get("membership_grade") in grades
            assert "martial_member" not in row
            assert row.get("membership_grade") != "support"
            assert "standing_duty_ref" not in row
    assert total > 0
    for path in sorted((ROOT / "state/martial-world/factions").glob("*.json")):
        faction = json.loads(path.read_text(encoding="utf-8"))
        assert "martial_members" not in faction
        assert "household_support_members" not in faction


def test_generic_household_work_is_shared_overhead_not_named_person_state():
    faction = {
        "faction_id": "test_rotation_house",
        "buildings": {"residential_compound": 1, "main_hall": 1, "storehouse": 1, "transport_yard": 1},
        "enterprises": {},
        "infrastructure": {},
    }
    people = [person(f"j{i:02}", sword=30 + i % 5) for i in range(40)]
    projection = derive_duty_assignments(faction, people, year=61, month=1, protected_refs=[])
    assert projection["assignments"] == {}
    assert projection["requirements"] == {}
    assert routine_service_overhead_milli(faction, living_population=40) > 0


def test_specialist_duty_is_derived_from_real_skill_and_consumes_training_time():
    tang = load("state/martial-world/factions/house_tang.json")
    physician = person("physician", grade="senior", sword=80, medicine=120)
    merchant = person("merchant", grade="senior", sword=70, commerce=120)
    smith = person("smith", grade="senior", sword=70, crafting=120)
    ordinary = [person(f"ordinary.{i}", medicine=12 + i, commerce=12 + i, crafting=12 + i) for i in range(16)]
    result = derive_duty_assignments(tang, [physician, merchant, smith, *ordinary], year=61, month=8, protected_refs=[])
    assert result["assignments"].get("physician") == "infirmary_service"
    assert result["requirements"]["infirmary_service"] > 0
    assert result["requirements"]["workshop_service"] > 0
    assert duty_time_share_for_ref("infirmary_service") == 440
    assert duty_time_share_for_ref(None) == 0


def test_house_tang_life_service_is_a_faction_rule_not_person_oath_bloat():
    world = load("game/data/martial-world/world-seed.json")["martial_factions"]
    tang_static = world["house_tang"]
    assert tang_static["membership_tenure"] == "life_service"
    candidates = [person(f"member.{i}", birth_year=20) for i in range(20)]
    assert annual_voluntary_departure_refs(
        candidates,
        faction_ref="house_tang",
        year=61,
        hardship_milli=1000,
        allow_voluntary_departure=False,
    ) == []
    assert not allows_ordinary_membership_exit("house_tang")
    assert allows_ordinary_membership_exit("faction.cloud_terrace_sect")


def test_life_service_member_cannot_be_recruited_away_through_independent_pool():
    former_tang = {
        "person_id": "former.tang",
        "former_faction_ref": "house_tang",
        "independent_since": "0060-01-01T00:00:00",
    }
    former_ordinary = {
        "person_id": "former.ordinary",
        "former_faction_ref": "faction.cloud_terrace_sect",
        "independent_since": "0060-01-01T00:00:00",
    }
    assert not allows_independent_recruitment(former_tang, target_faction_ref="faction.four_gates_escort_agency")
    assert not allows_independent_recruitment(former_tang, target_faction_ref="house_tang")
    assert allows_independent_recruitment(former_ordinary, target_faction_ref="house_tang")


def test_combat_manpower_is_derived_from_real_current_people_not_membership_headcount():
    ready = person("ready", grade="senior", birth_year=30, sword=90)
    child = person("child", grade="probationary", birth_year=55, sword=50)
    weak = person("weak", grade="full", birth_year=30, sword=10)
    unavailable = person("away", grade="elite", birth_year=25, sword=130)
    rows = [ready, child, weak, unavailable]
    assert combat_ready_count(rows, year=61, unavailable_refs={"away"}, minimum_age=14, minimum_combat_skill=20) == 1
    assert [row["person_id"] for row in combat_ready_members(rows, year=61, unavailable_refs={"away"})] == ["ready"]


def test_current_world_duty_requirements_only_name_output_gating_specialists():
    tang = load("state/martial-world/factions/house_tang.json")
    roster = load("state/martial-world/people/house_tang.json")
    living = sum(1 for p in roster["people"] if (p.get("health") or {}).get("status") != "dead")
    requirements = duty_staffing_requirements(tang, living_population=living)
    assert set(requirements) <= {"trade_service", "workshop_service", "infirmary_service"}
    assert requirements["workshop_service"] > 0
    assert requirements["infirmary_service"] > 0
    assert "kitchen_service" not in requirements
    assert "records_administration" not in requirements
    assert "population" not in tang
