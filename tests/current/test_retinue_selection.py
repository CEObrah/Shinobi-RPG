from shinobi_runtime.martial_world.retinues import select_retinue_members


def _person(
    ref: str,
    *,
    birth_year: int = 30,
    sword: int = 50,
    scouting: int = 30,
    command: int = 20,
    medicine: int = 10,
    offices=(),
):
    return {
        "person_id": ref,
        "birth_year": birth_year,
        "faction_ref": "house_tang",
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
    row = _person("pc_wei_tang", sword=115, scouting=65, command=55, medicine=15)
    row["professional_skills"]["medicine"] = 15
    return row


def test_delegated_retinue_prefers_complementary_roles_and_may_choose_three():
    people = [
        _leader(),
        _person("medic", medicine=90),
        _person("guard", sword=90),
        _person("scout", scouting=80),
        _person("deputy", command=70),
    ]
    refs, roles = select_retinue_members(
        people[0], people, requested_count=0, year=61,
    )
    assert len(refs) == 3
    assert len(set(refs)) == len(refs)
    assert "pc_wei_tang" not in refs
    assert set(roles.values()) == {"field_medic", "protective_guard", "scout"}


def test_retinue_excludes_critical_house_officers_minors_and_unavailable_people():
    people = [
        _leader(),
        _person("chief_medic", medicine=140, offices=("chief_physician",)),
        _person("young_genius", birth_year=50, medicine=150, scouting=150, sword=150),
        _person("busy_scout", scouting=120),
        _person("medic", medicine=80),
        _person("guard", sword=85),
        _person("scout", scouting=75),
    ]
    refs, _roles = select_retinue_members(
        people[0], people, requested_count=3, year=61,
        unavailable_refs=["busy_scout"],
    )
    assert len(refs) == 3
    assert "chief_medic" not in refs
    assert "young_genius" not in refs
    assert "busy_scout" not in refs


def test_exact_two_member_request_stays_two():
    people = [
        _leader(),
        _person("medic", medicine=90),
        _person("guard", sword=90),
        _person("scout", scouting=90),
    ]
    refs, roles = select_retinue_members(
        people[0], people, requested_count=2, year=61,
    )
    assert len(refs) == 2
    assert len(roles) == 2


def test_discretionary_request_stops_at_two_when_third_role_is_too_weak():
    people = [
        _leader(),
        _person("medic", medicine=90),
        _person("guard", sword=90),
        _person("weak_third", sword=5, scouting=5, command=5, medicine=5),
    ]
    weak = people[-1]
    weak["attributes"] = {key: 10 for key in weak["attributes"]}
    refs, _roles = select_retinue_members(
        people[0], people, requested_count=0, year=61,
    )
    assert len(refs) == 2


def test_exact_mission_sized_retinue_can_exceed_old_three_person_policy():
    people = [
        _leader(),
        _person("medic", medicine=95),
        _person("scout", scouting=92),
        _person("deputy", command=88),
        _person("guard.1", sword=100),
        _person("guard.2", sword=95),
        _person("guard.3", sword=90),
    ]
    refs, roles = select_retinue_members(
        people[0], people, requested_count=5, year=61,
    )
    assert len(refs) == 5
    assert len(set(refs)) == 5
    assert set(refs).issubset({p["person_id"] for p in people[1:]})
    assert list(roles.values()).count("protective_guard") >= 2
