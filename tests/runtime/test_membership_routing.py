from __future__ import annotations

from pathlib import Path

from shinobi_runtime.membership_routes import (
    house_refs_for_member,
    stage_team_change,
    team_refs_for_assignment,
    team_refs_for_member,
    team_refs_for_parent,
    team_refs_for_service,
)
from shinobi_runtime.store import RepositoryStore


ROOT = Path(__file__).resolve().parents[2]


class RecordingRepository:
    def __init__(self, base: RepositoryStore) -> None:
        self.base = base
        self.reads: list[str] = []

    def read_optional_bytes(self, path: str):
        self.reads.append(path)
        if path == "state/team/registry.json":
            raise AssertionError("membership routing must not scan the global team registry")
        return self.base.read_optional_bytes(path)

    def read_json(self, path: str):
        self.reads.append(path)
        if path == "state/team/registry.json":
            raise AssertionError("membership routing must not scan the global team registry")
        return self.base.read_json(path)


def test_current_campaign_membership_routes_cover_player_team_house_parent_and_service() -> None:
    repo = RepositoryStore(ROOT)
    assert set(team_refs_for_member(repo, "pc_wei_tang")) == {
        "team.blackhound",
        "team.konoha.fujin",
    }
    assert house_refs_for_member(repo, "pc_wei_tang") == ("house.tang",)
    assert "team.blackhound" in team_refs_for_parent(repo, "institution.konoha.hokage_administration")
    assert "team.konoha.fujin" in team_refs_for_service(repo, "konoha")


def test_exact_membership_lookup_reads_only_root_and_hashed_shard() -> None:
    repo = RecordingRepository(RepositoryStore(ROOT))
    refs = team_refs_for_member(repo, "pc_wei_tang")
    assert "team.blackhound" in refs
    assert "state/team/registry.json" not in repo.reads
    assert repo.reads[0] == "state/reg/membership-routes.json"
    assert any(path.startswith("state/reg/membership-routes/") for path in repo.reads[1:])


def test_assignment_routes_are_derived_and_updated_atomically_with_team_state() -> None:
    repo = RepositoryStore(ROOT)
    writes: dict[str, dict] = {}
    touched = stage_team_change(
        repo,
        writes,
        team_ref="team.blackhound",
        before_members=("pc_wei_tang",),
        after_members=("pc_wei_tang",),
        before_assignment=None,
        after_assignment="formation.test.001",
    )
    assignment_records = [
        writes[path]
        for path in touched
        if "formation.test.001" in writes[path].get("team_assignment_routes", {})
    ]
    assert len(assignment_records) == 1
    assert assignment_records[0]["team_assignment_routes"]["formation.test.001"] == ["team.blackhound"]


def test_unassigned_key_has_empty_direct_route_without_scanning() -> None:
    repo = RecordingRepository(RepositoryStore(ROOT))
    assert team_refs_for_assignment(repo, "formation.not-present") == ()
    assert "state/team/registry.json" not in repo.reads
