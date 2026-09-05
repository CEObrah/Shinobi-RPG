from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from shinobi_runtime.api.command_time import command_submitted_at
from shinobi_runtime.commands.campaign_planner import CampaignCommandPlanner
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.martial_world.physical_presence import active_combat_for_person
from shinobi_runtime.store import RepositoryStore


ROOT = Path(__file__).resolve().parents[2]
MEDIC_REF = "mw.person.house_tang.1032"
CASUALTY_REF = "mw.person.house_tang.1020"


def _active_combat_ref(repo: RepositoryStore, player_ref: str) -> str:
    active = active_combat_for_person(repo.read_json, player_ref)
    if active is None:
        pytest.skip("canonical campaign currently has no active player combat")
    ref = str(active[0]) if isinstance(active, tuple) else str(active.get("combat_ref") or active.get("combat_id") or "")
    assert ref
    return ref


def test_canonical_active_combat_accepts_compound_player_attack_and_medic_treat_order():
    """Reproduce the rev64 Black Lance handoff through the production planner."""
    repo = RepositoryStore(ROOT)
    planner = CampaignCommandPlanner(repo)
    meta = repo.read_json("state/meta.json")
    player_ref = str(meta["player_id"])
    combat_ref = _active_combat_ref(repo, player_ref)

    deployments = repo.read_json("state/martial-world/deployments.json")
    standing = (deployments.get("deployments") or {}).get("retinue.wei.permanent_travel_team")
    if not isinstance(standing, dict) or MEDIC_REF not in standing.get("member_refs", []):
        pytest.skip("canonical campaign no longer has Han Chaohong in Wei's active standing retinue")

    combats = repo.read_json("state/martial-world/combats.json")
    combat_before = copy.deepcopy((combats.get("combats") or {})[combat_ref])
    if CASUALTY_REF not in {ref for refs in combat_before.get("sides", {}).values() for ref in refs}:
        pytest.skip("canonical casualty is no longer in this exact combat")

    command = CommandEnvelope(
        campaign_id=str(meta["campaign_id"]),
        request_id="test.live-combat-ally-support.preview",
        actor_id=player_ref,
        command_type="jianghu_combat_resolution",
        expected_revision=int(meta["revision"]),
        submitted_at=command_submitted_at(meta["time"]),
        payload={
            "action": "exchange",
            "combat_ref": combat_ref,
            "until_resolution": True,
            "ally_orders": [
                {"actor_ref": MEDIC_REF, "task": "treat", "target_ref": CASUALTY_REF},
            ],
        },
        mode="gameplay",
    )

    preview = planner.preview(command)
    assert preview.status == "ready", (preview.status, preview.code)
    assert preview.code == "jianghu_combat_exchange_resolved"
    assert repo.read_json("state/martial-world/combats.json")["combats"][combat_ref] == combat_before

    plan = planner.plan(command)
    assert plan.result["combat_ref"] == combat_ref
    planned_combats = json.loads(plan.writes["state/martial-world/combats.json"].decode("utf-8"))
    combat_after = planned_combats["combats"][combat_ref]
    support = combat_after["combatants"][MEDIC_REF].get("support_task")
    if isinstance(support, dict) and support.get("status") == "active":
        assert support["task"] == "treat"
        assert support["target_ref"] == CASUALTY_REF
        assert support["issued_by_ref"] == player_ref
    assert any(
        row.get("actor_ref") == MEDIC_REF
        and str(row.get("result") or "").startswith("support_")
        for row in plan.result.get("events", [])
    )
