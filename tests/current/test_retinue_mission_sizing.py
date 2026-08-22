from shinobi_runtime.commands.jianghu_retinue import _delegated_mission_guard_count
from shinobi_runtime.martial_world.retinues import select_retinue_members


def _person(ref: str, *, sword: int = 50, scouting: int = 30, command: int = 20, medicine: int = 10):
    return {
        "person_id": ref,
        "birth_year": 30,
        "faction_ref": "house_tang",
        "standing_offices": [],
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


def test_accepted_escort_sets_delegated_travel_detail_minimum():
    contracts = {
        "active": {
            "contract.test": {
                "contract_type": "escort",
                "status": "accepted",
                "beneficiary_ref": "house_tang",
                "participants": ["pc_wei_tang"],
                "objective": {"minimum_escort_count": 6},
            },
            "contract.other": {
                "contract_type": "escort",
                "status": "accepted",
                "beneficiary_ref": "other_house",
                "participants": ["pc_wei_tang"],
                "objective": {"minimum_escort_count": 50},
            },
        }
    }
    assert _delegated_mission_guard_count(
        contracts, actor_ref="pc_wei_tang", faction_ref="house_tang"
    ) == 5


def test_mission_sized_retinue_can_fill_five_real_members():
    leader = _person("pc_wei_tang", sword=115, scouting=65, command=55, medicine=15)
    people = [
        leader,
        _person("medic", medicine=95),
        _person("scout", scouting=92),
        _person("deputy", command=88),
        _person("escort.1", sword=100),
        _person("escort.2", sword=95),
        _person("escort.3", sword=90),
    ]
    refs, roles = select_retinue_members(leader, people, requested_count=5, year=61)
    assert len(refs) == 5
    assert len(set(refs)) == 5
    assert list(roles.values()).count("protective_guard") >= 2
