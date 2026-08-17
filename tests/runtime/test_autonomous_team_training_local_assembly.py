from __future__ import annotations

import copy
from pathlib import Path

from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.store import RepositoryStore


ROOT = Path(__file__).resolve().parents[2]
TEAM8_PATH = "state/team/team-konoha-generated-a2b320a362.json"
STUDENTS = ("canon_hinata", "canon_kiba", "canon_shino")


def _planner_and_team():
    repo = RepositoryStore(ROOT)
    return repo, CampaignCommandPlanner(repo), copy.deepcopy(repo.read_json(TEAM8_PATH))


def test_routine_team_training_can_assemble_across_same_village_route_anchor() -> None:
    _repo, planner, team = _planner_and_team()
    group = planner._eligible_autonomous_group(team=team, record_writes={})
    assert group is not None
    instructor_ref, _instructor, location_ref, member_rows, basis = group
    assert instructor_ref == "canon_kurenai"
    assert location_ref == "place.konoha"
    assert basis == "shared_route_anchor"
    assert set(STUDENTS).issubset({row[0] for row in member_rows})


def test_local_training_assembly_never_crosses_strategic_route_anchor() -> None:
    repo, planner, team = _planner_and_team()
    owners = repo.read_json("state/index/owners/canon.json")["owners"]
    writes = {}
    for ref in STUDENTS:
        path = owners[ref]
        person = copy.deepcopy(repo.read_json(path))
        person["current_location_id"] = "place.suna"
        writes[path] = person
    assert planner._eligible_autonomous_group(team=team, record_writes=writes) is None


def test_facility_specific_training_still_requires_exact_colocation() -> None:
    _repo, planner, team = _planner_and_team()
    team["training"]["facility_refs"] = ["place.konoha.academy"]
    assert planner._eligible_autonomous_group(team=team, record_writes={}) is None
