from pathlib import Path

import pytest

from shinobi_runtime.api.command_time import command_submitted_at
from shinobi_runtime.commands.campaign_planner import CampaignCommandPlanner
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.martial_world.physical_presence import active_combat_for_person
from shinobi_runtime.store import RepositoryStore


ROOT = Path(__file__).resolve().parents[2]


def test_canonical_active_combat_bare_exchange_matches_advertised_contract():
    repo = RepositoryStore(ROOT)
    planner = CampaignCommandPlanner(repo)
    meta = repo.read_json("state/meta.json")
    player_ref = str(meta["player_id"])
    active = active_combat_for_person(repo.read_json, player_ref)
    if active is None:
        pytest.skip("canonical campaign currently has no active player combat")
    combat_ref = str(active[0]) if isinstance(active, tuple) else str(active.get("combat_ref") or active.get("combat_id") or "")
    assert combat_ref

    command = CommandEnvelope(
        campaign_id=str(meta["campaign_id"]),
        request_id="test.live-active-combat.preview-contract",
        actor_id=player_ref,
        command_type="jianghu_combat_resolution",
        expected_revision=int(meta["revision"]),
        submitted_at=command_submitted_at(meta["time"]),
        payload={"action": "exchange", "combat_ref": combat_ref},
        mode="gameplay",
    )

    preview = planner.preview(command)
    assert preview.status == "ready"
    assert preview.code == "jianghu_combat_exchange_resolved"
