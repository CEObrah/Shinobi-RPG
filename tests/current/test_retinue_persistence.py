import copy

from shinobi_runtime.martial_world.live_state import player_view_from_person
from shinobi_runtime.martial_world import time_progression
from shinobi_runtime.people.repository import RepositoryPersonSheetResolver
from shinobi_runtime.store import RepositoryStore


ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
_DEPLOYMENTS = "state/martial-world/deployments.json"


class _OverlayRepository:
    def __init__(self, base, records):
        self.base = base
        self.records = records

    def read_json(self, path):
        key = str(path)
        if key in self.records:
            return copy.deepcopy(self.records[key])
        return self.base.read_json(path)


def _person(ref, *, medicine=10, scout=20, sword=40):
    return {
        "person_id": ref,
        "birth_year": 30,
        "faction_ref": "house_tang",
        "membership_grade": "full",
        "standing_offices": [],
        "attributes": {
            "strength": 60, "speed": 60, "dexterity": 60, "endurance": 60,
            "perception": 60, "intelligence": 60, "willpower": 60,
        },
        "martial_skills": {
            "sword": sword, "spear": 0, "bow": 0, "hidden_weapons": 0,
            "unarmed": 30, "stealth_scouting": scout, "command": 20,
        },
        "professional_skills": {
            "medicine": medicine, "administration": 10, "commerce": 10,
            "crafting": 10, "instruction": 10,
        },
        "health": {"status": "ready", "consciousness": 100, "injuries": []},
    }


def test_player_sheet_and_play_view_keep_active_retinue_visible():
    base = RepositoryStore(ROOT)
    deployments = copy.deepcopy(base.read_json(_DEPLOYMENTS))
    deployments.setdefault("deployments", {})["retinue.test.visible"] = {
        "deployment_ref": "retinue.test.visible",
        "operation_kind": "standing_retinue",
        "faction_ref": "house_tang",
        "leader_ref": "pc_wei_tang",
        "chooser_refs": ["char.zhu", "char.ling"],
        "member_refs": ["char.test.guard", "char.test.scout", "char.test.medic"],
        "member_roles": {
            "char.test.guard": "protective_guard",
            "char.test.scout": "scout",
            "char.test.medic": "field_medic",
        },
        "status": "active",
        "requested_at": "0061-08-14T21:15:00",
        "assigned_at": "0061-08-15T09:15:00",
        "training_policy": "house_curriculum_idle_field_experience_active",
    }
    resolver = RepositoryPersonSheetResolver(_OverlayRepository(base, {_DEPLOYMENTS: deployments}))
    sheet = resolver("pc_wei_tang")
    row = next(x for x in sheet["standing_retinues"] if x["retinue_ref"] == "retinue.test.visible")
    assert row["status"] == "active"
    assert row["member_roles"]["char.test.scout"] == "scout"
    player = player_view_from_person(sheet)
    assert player["standing_retinues"] == sheet["standing_retinues"]


def test_assignment_review_cannot_reuse_member_from_another_active_retinue(monkeypatch):
    people = [
        _person("leader.two", sword=100),
        _person("reserved.guard", sword=120),
        _person("medic", medicine=90),
        _person("scout", scout=90),
        _person("guard.two", sword=85),
    ]
    roster = {"people": people}
    leader = people[0]

    def fake_roster_person(_view, ref):
        assert ref == "leader.two"
        return "state/martial-world/people/house_tang.json", roster, 0, leader

    monkeypatch.setattr(time_progression, "roster_person", fake_roster_person)
    state = {
        "schema": "jianghu-deployment-state-1.0",
        "deployments": {
            "retinue.one": {
                "deployment_ref": "retinue.one",
                "operation_kind": "standing_retinue",
                "leader_ref": "leader.one",
                "member_refs": ["reserved.guard"],
                "member_roles": {"reserved.guard": "protective_guard"},
                "status": "active",
            },
            "retinue.two": {
                "deployment_ref": "retinue.two",
                "operation_kind": "standing_retinue",
                "leader_ref": "leader.two",
                "chooser_refs": ["chooser.two"],
                "member_refs": [],
                "member_roles": {},
                "status": "assignment_pending",
            },
        },
    }
    records = {
        _DEPLOYMENTS: state,
    }

    def read_json(path):
        key = str(path)
        if key in records:
            return copy.deepcopy(records[key])
        raise FileNotFoundError(key)

    out = time_progression.augment_frontier_with_progression(
        read_json=read_json,
        frontier={"writes": {}, "handoffs": [], "reviews": []},
        events=[{
            "event_id": "retinue_assignment_review:retinue.two",
            "kind": "retinue_assignment_review",
            "retinue_ref": "retinue.two",
        }],
        at=__import__("datetime").datetime.fromisoformat("0061-08-15T09:15:00"),
    )
    assigned = out["writes"][_DEPLOYMENTS]["deployments"]["retinue.two"]
    assert assigned["status"] == "active"
    assert "reserved.guard" not in assigned["member_refs"]
    assert len(assigned["member_refs"]) == 3


def test_route_journey_preserves_temporary_retinue_provenance_across_segments():
    from datetime import datetime
    from shinobi_runtime.martial_world.physical_travel import build_route_journey, begin_next_segment

    plan = {
        'edges': ['route.a', 'route.b'],
        'nodes': ['place.a', 'place.b', 'place.c'],
        'segments': [
            {'hours': 1.0, 'edge_start_milli': 0, 'edge_end_milli': 1000},
            {'hours': 1.0, 'edge_start_milli': 0, 'edge_end_milli': 1000},
        ],
    }
    movement = build_route_journey(
        movement_ref='movement.retinue.provenance', movement_kind='escort_contract',
        purpose_ref='contract.test', plan=plan,
        participants=['pc_wei_tang', 'char.permanent', 'char.temporary'],
        leader_ref='pc_wei_tang', beneficiary_ref='house_tang',
        started_at=datetime.fromisoformat('0061-08-15T09:00:00'), mode='convoy',
        extra={
            'escort_refs': ['pc_wei_tang', 'char.permanent', 'char.temporary'],
            'core_escort_refs': ['pc_wei_tang', 'char.permanent'],
            'temporary_mission_escort_refs': ['char.temporary'],
        },
    )
    assert movement['temporary_mission_escort_refs'] == ['char.temporary']
    next_segment = begin_next_segment(movement, at=datetime.fromisoformat('0061-08-15T10:00:00'))
    assert next_segment is not None
    assert next_segment['temporary_mission_escort_refs'] == ['char.temporary']


def test_assignment_review_excludes_physically_unavailable_people(monkeypatch):
    people = [
        _person("leader.two", sword=100),
        _person("fighter.away", sword=140),
        _person("medic", medicine=90),
        _person("scout", scout=90),
        _person("guard.two", sword=85),
    ]
    roster = {"people": people}
    leader = people[0]

    def fake_roster_person(_view, ref):
        assert ref == "leader.two"
        return "state/martial-world/people/house_tang.json", roster, 0, leader

    captured = []

    def fake_select(_leader, _people, *, year, unavailable_refs, target_count):
        captured.append(set(unavailable_refs))
        return ["medic", "scout", "guard.two"], {
            "medic": "field_medic", "scout": "scout", "guard.two": "protective_guard",
        }

    monkeypatch.setattr(time_progression, "roster_person", fake_roster_person)
    monkeypatch.setattr(time_progression, "select_retinue_members", fake_select)
    state = {
        "schema": "jianghu-deployment-state-1.0",
        "deployments": {
            "retinue.two": {
                "deployment_ref": "retinue.two",
                "operation_kind": "standing_retinue",
                "leader_ref": "leader.two",
                "chooser_refs": [],
                "member_refs": [],
                "member_roles": {},
                "status": "assignment_pending",
                "requested_count": 3,
            },
        },
    }
    records = {
        _DEPLOYMENTS: state,
        "state/martial-world/custody.json": {"records": []},
        "state/martial-world/combats.json": {
            "combats": {"combat:away": {"status": "active", "combatants": {"fighter.away": {}}}}
        },
        "state/martial-world/route-operations.json": {"movements": {}},
    }

    def read_json(path):
        key = str(path)
        if key in records:
            return copy.deepcopy(records[key])
        raise FileNotFoundError(key)

    time_progression.augment_frontier_with_progression(
        read_json=read_json,
        frontier={"writes": {}, "handoffs": [], "reviews": []},
        events=[{
            "event_id": "retinue_assignment_review:retinue.two",
            "kind": "retinue_assignment_review",
            "retinue_ref": "retinue.two",
        }],
        at=__import__("datetime").datetime.fromisoformat("0061-08-15T09:15:00"),
    )
    assert captured
    assert "fighter.away" in captured[0]
