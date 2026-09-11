from shinobi_runtime.martial_world.escort import active_retinue_party
from shinobi_runtime.martial_world.retinues import select_mission_escort_reinforcements


def _person(
    ref: str,
    *,
    sword: int = 50,
    scouting: int = 30,
    command: int = 20,
    medicine: int = 10,
    offices=(),
):
    return {
        "person_id": ref,
        "birth_year": 30,
        "faction_ref": "house_tang",
        "standing_offices": list(offices),
        "attributes": {
            "strength": 60, "speed": 60, "dexterity": 60,
            "endurance": 60, "perception": 60, "intelligence": 60,
            "willpower": 60,
        },
        "martial_skills": {
            "sword": sword, "spear": 0, "bow": 0, "hidden_weapons": 0,
            "unarmed": 30, "stealth_scouting": scouting, "command": command,
        },
        "professional_skills": {
            "medicine": medicine, "administration": 10, "commerce": 10,
            "crafting": 10, "instruction": 10,
        },
        "health": {"status": "ready", "consciousness": 100},
    }


def test_huashan_six_escort_requirement_means_two_temporary_staff_beyond_wei_and_trio():
    deployments = {
        "deployments": {
            "retinue.wei": {
                "operation_kind": "standing_retinue",
                "status": "active",
                "leader_ref": "pc_wei_tang",
                "member_refs": ["team.medic", "team.scout", "team.guard"],
            }
        }
    }
    core = active_retinue_party(
        deployments,
        leader_ref="pc_wei_tang",
        principals=["pc_wei_tang"],
    )
    assert core == ["pc_wei_tang", "team.medic", "team.scout", "team.guard"]
    assert max(0, 6 - len(core)) == 2


def test_two_required_mission_reinforcements_do_not_become_retinue_members():
    leader = _person("pc_wei_tang", sword=115, scouting=65, command=55, medicine=15)
    permanent = ["team.medic", "team.scout", "team.guard"]
    people = [
        leader,
        _person("team.medic", medicine=95),
        _person("team.scout", scouting=92),
        _person("team.guard", sword=96),
        _person("mission.guard.1", sword=110),
        _person("mission.guard.2", sword=105),
        _person("mission.guard.3", sword=100),
        _person("house.chief", sword=180, offices=("chief_martial_instructor",)),
    ]
    temporary = select_mission_escort_reinforcements(
        leader,
        people,
        needed_count=2,
        year=61,
        exclude_refs=[leader["person_id"], *permanent],
    )
    assert temporary == ["mission.guard.1", "mission.guard.2"]
    assert not set(temporary) & set(permanent)
