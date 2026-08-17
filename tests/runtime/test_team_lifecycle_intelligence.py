"""Regression coverage for exact-team purpose and protected autonomous rosters."""

from pathlib import Path

from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store import RepositoryStore


ROOT = Path(__file__).resolve().parents[2]


def test_new_exact_team_gets_compact_lifecycle_contract() -> None:
    repository = RepositoryStore(ROOT)
    planner = CampaignCommandPlanner(repository)
    at = CampaignTime.parse(repository.read_json("state/meta.json")["time"])
    scheduler = planner._load_scheduler(current_time=at, scene=planner._scene_base(at))
    writes = {}
    _path, team = planner._register_exact_team_state(
        team_id="team.test.lifecycle",
        name="Lifecycle Test Team",
        team_type="temporary_task_force",
        parent_institution_ref="institution.konoha.hokage_administration",
        assignment_authority_ref="canon_hiruzen",
        leader_ref="canon_yugao",
        member_refs=("canon_yugao", "canon_aoba", "canon_hayate"),
        roles={"canon_yugao": "field_lead", "canon_aoba": "recon", "canon_hayate": "assault"},
        classification="restricted",
        at=at,
        basis="test only",
        scheduler=scheduler,
        record_writes=writes,
    )
    lifecycle = team["lifecycle"]
    assert lifecycle == {
        "purpose_kind": "standing",
        "purpose_ref": None,
        "purpose_status": "active",
        "replacement_policy": "authority_review",
        "target_size": 3,
        "exclusive_active_membership": True,
        "autonomy_owner_ref": None,
    }


def test_autonomy_policy_cannot_enable_cross_team_borrowing() -> None:
    planner = CampaignCommandPlanner(RepositoryStore(ROOT))
    book = planner._autonomy_policy_book()
    for assignment in book.faction_assignments.values():
        spec = assignment.get("team_creation") if isinstance(assignment, dict) else None
        if isinstance(spec, dict):
            assert "allow_existing_team_members" not in spec
            assert "max_active_teams" not in spec
            assert spec["purpose_kind"] in ("standing", "mission_bound")


def test_dynamic_team_policy_has_no_fictional_team_count_ceiling() -> None:
    planner = CampaignCommandPlanner(RepositoryStore(ROOT))
    book = planner._autonomy_policy_book()
    kumo = book.faction_assignments["faction.kumo_military_intelligence"]
    spec = kumo["team_creation"]
    assert spec["mode"] == "dynamic"
    assert "max_active_teams" not in spec
    assert spec["replacement_policy"] == "maintain_strength"


def test_player_led_team_defaults_to_authority_review_replacements() -> None:
    planner = CampaignCommandPlanner(RepositoryStore(ROOT))
    lifecycle = planner._default_team_lifecycle({
        "leader_ref": "pc_wei_tang",
        "member_refs": ["pc_wei_tang", "char.kai", "char.riku_hyuga", "char.mei_arakawa"],
    })
    assert lifecycle["replacement_policy"] == "authority_review"
    assert lifecycle["exclusive_active_membership"] is True
    assert lifecycle["target_size"] == 4
