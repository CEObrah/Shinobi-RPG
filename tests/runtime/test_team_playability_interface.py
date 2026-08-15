from __future__ import annotations

from pathlib import Path

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.campaign_planner import CampaignCommandPlanner
from shinobi_runtime.domain.equipment import (
    actor_team_policy_roles,
    assignment_refit_policies,
    loadout_refit_policy,
)
from shinobi_runtime.store import RepositoryStore


ROOT = Path(__file__).resolve().parents[2]
TEAM_REF = "team.konoha.fujin"
PLAYER_REF = "pc_wei_tang"


def _repository() -> RepositoryStore:
    return RepositoryStore(ROOT)


def _team(repository: RepositoryStore) -> dict:
    return repository.read_json("state/team/fujin.json")


def _refit_command(repository: RepositoryStore, *, holder_ref: str, loadout_ref: str, stock_ref: str) -> CommandEnvelope:
    meta = repository.read_json("state/meta.json")
    return CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id=f"test.refit.{holder_ref}",
        actor_id=PLAYER_REF,
        command_type="inventory_resolution",
        expected_revision=meta["revision"],
        submitted_at="2026-08-11T00:00:00Z",
        payload={
            "action": "refit",
            "stock_ref": stock_ref,
            "holder_ref": holder_ref,
            "loadout_ref": loadout_ref,
            "summary": "Regression preview for Team Fujin permanent fitting.",
            "visibility": "restricted",
        },
    )


def test_team_fujin_refit_policies_are_holder_scoped_and_leader_authorized():
    repository = _repository()
    team = _team(repository)
    policies = dict(assignment_refit_policies(repository, TEAM_REF))

    expected = {
        "load.team_fujin.kai": "char.kai",
        "load.team_fujin.riku": "char.riku_hyuga",
        "load.team_fujin.mei": "char.mei_arakawa",
        "load.team_fujin.wei": PLAYER_REF,
    }
    assert {loadout_ref: policy["holder_ref"] for loadout_ref, policy in policies.items()} == expected

    for loadout_ref, holder_ref in expected.items():
        policy = loadout_refit_policy(repository, loadout_ref)
        assert policy is not None
        assert policy["assignment_ref"] == TEAM_REF
        assert policy["holder_ref"] == holder_ref
        assert policy["supply_stock_refs"] == [
            "stock.force_konoha_shinobi",
            "stock.house.tang",
        ]
        roles = actor_team_policy_roles(team, actor_ref=PLAYER_REF, holder_ref=holder_ref)
        assert "leader" in roles
        assert set(policy["authorized_team_roles"]).intersection(roles)

    outsider_roles = actor_team_policy_roles(team, actor_ref="char.outsider", holder_ref="char.kai")
    assert outsider_roles == frozenset()


def test_registered_team_refits_preview_and_plan_without_mutating_campaign():
    repository = _repository()
    planner = CampaignCommandPlanner(repository)
    before_meta = repository.read_json("state/meta.json")
    policies = dict(assignment_refit_policies(repository, TEAM_REF))

    for loadout_ref in (
        "load.team_fujin.kai",
        "load.team_fujin.riku",
        "load.team_fujin.mei",
    ):
        policy = policies[loadout_ref]
        command = _refit_command(
            repository,
            holder_ref=policy["holder_ref"],
            loadout_ref=loadout_ref,
            stock_ref=policy["supply_stock_refs"][0],
        )
        preview = planner.preview(command)
        assert preview.status == "ready"
        plan = planner.plan(command)
        assert plan.result["action"] == "refit"
        assert plan.result["assignment_ref"] == TEAM_REF
        assert plan.result["loadout_ref"] == loadout_ref
        assert list(plan.result["supply_stock_refs"]) == policy["supply_stock_refs"]
        assert plan.result["authority_basis"].startswith("team_refit_policy:")

    assert repository.read_json("state/meta.json") == before_meta
