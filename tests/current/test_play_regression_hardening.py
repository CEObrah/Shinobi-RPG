from __future__ import annotations

import json
import shutil
import pytest
from pathlib import Path

from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


def _copy_current_save(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(
        ROOT,
        root,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".git"),
    )
    return root


def test_actual_revision4_compound_attack_and_medic_order_survive_same_resolution(tmp_path):
    """Regression for the played 'keep fighting; send Han to Yao' failure.

    This deliberately starts from the supplied live revision-4 combat instead of
    constructing a clean synthetic fight. Both halves of Wei's declared action
    must be present in the same planned result, and the already-incapacitated
    casualty must carry fallen collision geometry.
    """
    root = _copy_current_save(tmp_path)
    repo = RepositoryStore(root)
    planner = RepositoryCommandPlanner(repo)
    meta = repo.read_json("state/meta.json")
    if meta.get("revision") != 4 or meta.get("time") != "SE-0061-09-27T21:16:12":
        pytest.skip("historical revision-4 combat snapshot is not the supplied current save")

    player_ref = meta["player_id"]
    han_ref = "mw.person.house_tang.1032"
    yao_ref = "mw.person.house_tang.1045"
    roles = planner._standing_retinue_member_roles(player_ref)
    assert roles[han_ref] == "field_medic"

    combats = repo.read_json("state/martial-world/combats.json")["combats"]
    active = [
        (ref, row)
        for ref, row in combats.items()
        if isinstance(row, dict)
        and row.get("status") == "active"
        and player_ref in {
            member
            for members in row.get("sides", {}).values()
            if isinstance(members, list)
            for member in members
        }
    ]
    assert len(active) == 1
    combat_ref, before_combat = active[0]
    assert any(han_ref in members for members in before_combat["sides"].values())
    assert any(yao_ref in members for members in before_combat["sides"].values())

    command = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="regression.actual-rev4.compound-medic",
        actor_id=player_ref,
        command_type="jianghu_combat_resolution",
        expected_revision=meta["revision"],
        submitted_at="2026-09-03T06:45:00Z",
        payload={
            "action": "exchange",
            "combat_ref": combat_ref,
            "duration_seconds": 1,
            "ally_orders": [
                {"actor_ref": han_ref, "task": "reach", "target_ref": yao_ref}
            ],
        },
        mode="gameplay",
    )
    preview = planner.preview(command)
    assert preview.status == "ready"
    plan = planner.plan(command)
    result = plan.result

    support = [
        dict(event)
        for event in result["events"]
        if event.get("actor_ref") == han_ref
        and event.get("action_kind") == "ally_support"
        and event.get("intended_ref") == yao_ref
    ]
    assert support, "Han's support order disappeared from the compound combat action"
    assert all(event.get("decision_origin") == "player_ally_order" for event in support)
    assert support[0]["result"] in {
        "support_approach", "support_reached", "support_protecting",
        "support_extraction_secured", "support_extract_target_mobile",
    }
    assert any(event.get("actor_ref") == player_ref for event in result["events"]), (
        "Wei's personal combat half disappeared while resolving the ally order"
    )

    staged = json.loads(plan.writes["state/martial-world/combats.json"].decode("utf-8"))
    after_combat = staged["combats"][combat_ref]
    yao_position = after_combat["positions"][yao_ref]
    assert yao_position["stance"] == "fallen"
    assert int(yao_position["body_radius_mm"]) <= 140
    han_position = after_combat["positions"][han_ref]
    assert han_position["stance"] in {"moving_to_ally", "screening_ally", "extracting_ally"}

    info = dict(result["combat_information"])
    assert info["scale"] == "exact_people"
    assert info["observed_hostiles_cumulative"] >= info["observed_active_engaged"]
    assert info["observed_active_engaged"] + info["observed_withdrawing"] >= 1
    assert info["observed_combat_capable_remaining"] == info["observed_active_engaged"] + info["observed_withdrawing"]
    assert info["personal_tally_status"] == "may_be_partial_for_legacy_active_fight"

    # Planning is read-only. The supplied save itself must remain untouched.
    assert repo.read_json("state/meta.json")["revision"] == 4


def test_skill_requires_scene_first_scale_appropriate_combat_accounting():
    text = (ROOT / "plugins/shinobi-rpg/skill/shinobi-game-master/references/combat.md").read_text()
    assert "When a committed exact-combat result exposes `combat_information`" in text
    assert "personal_tally_status" in text
    assert "misleading zero" in text
