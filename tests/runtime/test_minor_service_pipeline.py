from __future__ import annotations

import copy
from pathlib import Path

from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


def _command(repo: RepositoryStore, institution_id: str, actor_id: str) -> CommandEnvelope:
    meta = repo.read_json("state/meta.json")
    return CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="minor-service-" + institution_id.replace(".", "-"),
        actor_id=actor_id,
        command_type="advance_time",
        expected_revision=meta["revision"],
        submitted_at=meta["time"],
        payload={"target_time": "SE-0061-07-01T07:00:00"},
        mode="autonomous",
    )


def test_minor_service_pipelines_convert_existing_training_manpower_without_growth() -> None:
    repo = RepositoryStore(ROOT)
    planner = CampaignCommandPlanner(repo)
    policy = planner._autonomy_policy_book()
    bundle = repo.read_json("state/world/institutions-minor-and-civil.json")
    institutions = {
        row["id"]: row
        for row in bundle["payload"]["institutions"]
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    force_index = repo.read_json("state/index/owners/force.json")["owners"]
    target_ids = (
        "institution.ame.central_authority",
        "institution.kusa.village_administration",
        "institution.taki.village_administration",
        "institution.oto.hideout_network",
        "institution.iron.samurai_command",
        "faction_yuga",
    )

    for institution_id in target_ids:
        institution = copy.deepcopy(institutions[institution_id])
        assignment = policy.institution_assignment(institution_id)
        force_ref = assignment["force_ref"]
        force_path = force_index[force_ref]
        before = repo.read_json(force_path)
        world_events = copy.deepcopy(repo.read_json("state/reg/world-events.json"))
        writes: dict[str, dict] = {}

        result = planner._apply_institution_autonomy_review(
            institution=institution,
            at=CampaignTime.parse("SE-0061-07-01T07:00:00"),
            compacted=1,
            command=_command(repo, institution_id, institution.get("leader_id") or institution_id),
            policy_book=policy,
            world_events=world_events,
            record_writes=writes,
        )
        after = writes[force_path]
        service = result["service_training"]

        assert service["completed"] > 0
        assert service["net_force_growth"] == 0
        assert after["total"] == before["total"]
        assert after["availability"]["training_or_instruction"] == (
            before["availability"]["training_or_instruction"] - service["completed"]
        )
        assert after["availability"]["ready_24h"] == (
            before["availability"]["ready_24h"] + service["completed"]
        )
        assert sum(after["availability"].values()) == after["total"]
        reserve_count = sum(row["count"] for row in after["reserve_capability"].values())
        assert reserve_count + after["availability"]["deployed"] == after["total"]
        before_training_pool = next(row for row in before["troop_pools"] if row["role"] == "training_instruction")
        after_training_pool = next(row for row in after["troop_pools"] if row["role"] == "training_instruction")
        before_field_pool = next(row for row in before["troop_pools"] if row["role"] == "field_ready")
        after_field_pool = next(row for row in after["troop_pools"] if row["role"] == "field_ready")
        assert after_training_pool["count"] == before_training_pool["count"] - service["completed"]
        assert after_field_pool["count"] == before_field_pool["count"] + service["completed"]
