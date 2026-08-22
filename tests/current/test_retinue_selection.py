from shinobi_runtime.martial_world.retinues import (
    permanent_team_age_bounds,
    permanent_team_member_eligible,
    select_mission_escort_reinforcements,
    select_retinue_members,
)


def _person(
    ref: str,
    *,
    birth_year: int = 30,
    sword: int = 50,
    scouting: int = 30,
    command: int = 20,
    medicine: int = 10,
    grade: str = "full",
    offices=(),
):
    return {
        "person_id": ref,
        "birth_year": birth_year,
        "faction_ref": "house_tang",
        "membership_grade": grade,
        "standing_offices": list(offices),
        "attributes": {
            "strength": 60,
            "speed": 60,
            "dexterity": 60,
            "endurance": 60,
            "perception": 60,
            "intelligence": 60,
            "willpower": 60,
        },
        "martial_skills": {
            "sword": sword,
            "spear": 0,
            "bow": 0,
            "hidden_weapons": 0,
            "unarmed": 30,
            "stealth_scouting": scouting,
            "command": command,
        },
        "professional_skills": {
            "medicine": medicine,
            "administration": 10,
            "commerce": 10,
            "crafting": 10,
            "instruction": 10,
        },
        "health": {
            "status": "ready",
            "injuries": [],
            "toxicity_milli": 0,
            "blood_lost_ml": 0,
            "shock": 0,
            "consciousness": 100,
        },
    }


def _leader():
    row = _person("pc_wei_tang", birth_year=44, sword=115, scouting=65, command=55, medicine=15, grade="elite")
    row["professional_skills"]["medicine"] = 15
    return row


def test_delegated_retinue_is_exactly_three_complementary_permanent_members():
    people = [
        _leader(),
        _person("medic", birth_year=39, medicine=90),
        _person("guard", birth_year=35, sword=90),
        _person("scout", birth_year=41, scouting=80),
        _person("deputy", birth_year=34, command=70),
    ]
    refs, roles = select_retinue_members(
        people[0], people, requested_count=0, year=61,
    )
    assert len(refs) == 3
    assert len(set(refs)) == len(refs)
    assert "pc_wei_tang" not in refs
    assert set(roles.values()) == {"field_medic", "protective_guard", "scout"}


def test_permanent_team_for_seventeen_year_old_excludes_multi_generation_candidates_even_if_stronger():
    leader = _leader()
    people = [
        leader,
        _person("old_medic", birth_year=-8, medicine=150, grade="senior"),   # 69
        _person("old_guard", birth_year=3, sword=150, grade="senior"),      # 58
        _person("old_scout", birth_year=10, scouting=150, grade="senior"),  # 51
        _person("young_medic", birth_year=38, medicine=75),                  # 23
        _person("young_guard", birth_year=34, sword=80),                     # 27
        _person("young_scout", birth_year=40, scouting=72),                  # 21
    ]
    assert permanent_team_age_bounds(leader, year=61) == (16, 35)
    refs, roles = select_retinue_members(leader, people, requested_count=3, year=61)
    assert refs == ["young_medic", "young_guard", "young_scout"]
    assert set(roles.values()) == {"field_medic", "protective_guard", "scout"}
    assert not {"old_medic", "old_guard", "old_scout"} & set(refs)


def test_permanent_team_excludes_probationary_recruits_even_when_they_are_role_best():
    leader = _leader()
    probationary = _person("probationary_medic", birth_year=40, medicine=150, grade="probationary")
    trusted = _person("trusted_medic", birth_year=39, medicine=70, grade="full")
    assert not permanent_team_member_eligible(leader, probationary, year=61)
    refs, _roles = select_retinue_members(
        leader,
        [leader, probationary, trusted, _person("guard", birth_year=38, sword=70), _person("scout", birth_year=37, scouting=70)],
        requested_count=3,
        year=61,
    )
    assert "probationary_medic" not in refs
    assert "trusted_medic" in refs


def test_retinue_excludes_critical_house_officers_minors_and_unavailable_people():
    people = [
        _leader(),
        _person("chief_medic", birth_year=35, medicine=140, offices=("chief_physician",)),
        _person("young_genius", birth_year=50, medicine=150, scouting=150, sword=150),
        _person("busy_scout", birth_year=40, scouting=120),
        _person("medic", birth_year=39, medicine=80),
        _person("guard", birth_year=38, sword=85),
        _person("scout", birth_year=37, scouting=75),
    ]
    refs, _roles = select_retinue_members(
        people[0], people, requested_count=3, year=61,
        unavailable_refs=["busy_scout"],
    )
    assert len(refs) == 3
    assert "chief_medic" not in refs
    assert "young_genius" not in refs
    assert "busy_scout" not in refs


def test_legacy_exact_two_member_selection_remains_readable_but_is_not_live_default():
    people = [
        _leader(),
        _person("medic", birth_year=39, medicine=90),
        _person("guard", birth_year=38, sword=90),
        _person("scout", birth_year=37, scouting=90),
    ]
    refs, roles = select_retinue_members(
        people[0], people, requested_count=2, year=61,
    )
    assert len(refs) == 2
    assert len(roles) == 2


def test_lawful_cohort_generalist_can_fill_third_slot_when_house_has_no_young_master_specialist():
    people = [
        _leader(),
        _person("medic", birth_year=39, medicine=90),
        _person("guard", birth_year=38, sword=90),
        _person("generalist", birth_year=40, sword=5, scouting=5, command=5, medicine=5),
    ]
    generalist = people[-1]
    generalist["attributes"] = {key: 10 for key in generalist["attributes"]}
    refs, roles = select_retinue_members(
        people[0], people, requested_count=0, year=61,
    )
    assert len(refs) == 3
    assert len(roles) == 3
    assert "generalist" in refs
    assert roles["generalist"] == "scout"


def test_mission_reinforcements_are_separate_from_permanent_team_and_can_use_older_veterans():
    leader = _leader()
    permanent = ["medic", "scout", "guard.core"]
    people = [
        leader,
        _person("medic", birth_year=39, medicine=95),
        _person("scout", birth_year=40, scouting=92),
        _person("guard.core", birth_year=38, sword=95),
        _person("guard.temp.old", birth_year=-8, sword=130, grade="senior"),
        _person("guard.temp.1", birth_year=20, sword=110),
        _person("guard.temp.2", birth_year=18, sword=105),
        _person("chief", birth_year=20, sword=180, offices=("chief_instructor",)),
    ]
    refs = select_mission_escort_reinforcements(
        leader,
        people,
        needed_count=2,
        year=61,
        exclude_refs=[leader["person_id"], *permanent],
    )
    assert refs == ["guard.temp.old", "guard.temp.1"]
    assert not set(refs) & set(permanent)
    assert "chief" not in refs
