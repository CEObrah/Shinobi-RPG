from __future__ import annotations

import json
from pathlib import Path
import shutil

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.people.profiles import numeric_map, profile_entry_for
from shinobi_runtime.people.repository import RepositoryPersonSheetResolver
from shinobi_runtime.store import RegisteredSchemaValidator, RegisteredTemplateValidator, RepositoryStore
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionPlanner

ROOT = Path(__file__).resolve().parents[2]
SUBMITTED = "2026-08-14T03:00:00Z"


def _command(repo: RepositoryStore, kind: str, payload: dict, request: str, *, actor: str = "pc_wei_tang", mode: str = "gameplay") -> CommandEnvelope:
    meta = repo.read_json("state/meta.json")
    return CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id=request,
        actor_id=actor,
        command_type=kind,
        expected_revision=meta["revision"],
        submitted_at=SUBMITTED,
        payload=payload,
        mode=mode,
    )


def _validated(repo: RepositoryStore, envelope: CommandEnvelope):
    plan = RepositoryCommandPlanner(repo).plan(envelope)
    manifest = TransactionPlanner(repo).plan(
        envelope,
        transaction_id=plan.transaction_id,
        created_at=plan.created_at,
        writes=plan.writes,
    )
    overlay = StagedOverlay(repo, manifest)
    plan.validator(overlay, manifest)
    RegisteredSchemaValidator(repo).validate_overlay(overlay, manifest.paths)
    RegisteredTemplateValidator(repo).validate_overlay(overlay, manifest.paths)
    return plan, overlay


def _apply(root: Path, plan) -> None:
    for relative, payload in plan.writes.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def test_house_lite_to_exact_is_additive_identity_preserving_and_no_reroll(tmp_path: Path) -> None:
    work = tmp_path / "repo"
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    repo = RepositoryStore(work)
    before_population = repo.read_json("state/population/registry.json")
    registry_before = repo.read_json("state/person-core/house-tang.json")
    profile_before = profile_entry_for(registry_before, "ht.core.006")
    assert profile_before is not None
    numbers_before = numeric_map(registry_before, profile_before)

    command = _command(
        repo,
        "person_exactification",
        {
            "person_ref": "ht.core.006",
            "authority_ref": "pc_wei_tang",
            "reason": "important field combat",
        },
        "release-exactify-house-006",
    )
    plan, overlay = _validated(repo, command)
    assert plan.result["identity_preserved"] is True
    assert plan.result["population_delta"] == 0
    assert "state/population/registry.json" not in plan.writes

    registry_after = overlay.read_json("state/person-core/house-tang.json")
    profile_after = profile_entry_for(registry_after, "ht.core.006")
    assert profile_after is not None
    assert numeric_map(registry_after, profile_after) == numbers_before
    core = registry_after["people"]["ht.core.006"]
    assert core["id"] == "ht.core.006"
    assert core["component_refs"]["profile.exact"] == "state/char/ht.core.006.json"
    exact = overlay.read_json("state/char/ht.core.006.json")
    assert exact["owner_id"] == "ht.core.006"
    assert exact["attributes"]["agility"] == round(numbers_before["stats.attributes.agility"])
    assert exact["martial_skills"]["sword"] == round(numbers_before["stats.martial_skills.sword"])
    assert exact["resources"]["chakra"]["capacity"] == round(numbers_before["stats.resources.chakra.capacity"])
    assert repo.read_json("state/population/registry.json") == before_population

    _apply(work, plan)
    repo_after = RepositoryStore(work)
    sheet = RepositoryPersonSheetResolver(repo_after)("ht.core.006")
    assert sheet is not None
    assert sheet["core"]["person_id"] == "ht.core.006"
    assert sheet["components"]["profile.exact"]["owner_id"] == "ht.core.006"
    exact_path, exact_actor = RepositoryCommandPlanner(repo_after)._resolve_actor_for_write("ht.core.006")
    assert exact_path == "state/char/ht.core.006.json"
    assert exact_actor["owner_id"] == "ht.core.006"


def test_exactified_house_person_enters_exact_combat_without_second_identity(tmp_path: Path) -> None:
    work = tmp_path / "repo"
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    repo = RepositoryStore(work)
    exactify = _command(
        repo,
        "person_exactification",
        {"person_ref": "ht.core.006", "authority_ref": "pc_wei_tang", "reason": "combat"},
        "release-exactify-combat",
    )
    plan, _overlay = _validated(repo, exactify)
    _apply(work, plan)

    # Exact combat is scene-owned. Move only this disposable test fixture to the
    # established Sword Manor location shared by both exact actors.
    scene_path = work / "state/scene.json"
    scene = json.loads(scene_path.read_text())
    scene["location_id"] = "place.sword_manor"
    scene_path.write_text(json.dumps(scene, indent=2) + "\n")

    repo = RepositoryStore(work)
    combat = _command(
        repo,
        "combat_resolution",
        {
            "combat_id": "combat.release.house_exact_bridge",
            "scale": "duel",
            "participants": [
                {
                    "actor_ref": "ht.core.006",
                    "side_ref": "side:house",
                    "action": "attack",
                    "target_refs": ["char.zhu"],
                    "objective_ref": "objective:house-pressure",
                    "lethal": False,
                },
                {
                    "actor_ref": "char.zhu",
                    "side_ref": "side:zhu",
                    "action": "hold",
                    "target_refs": [],
                    "objective_ref": None,
                    "lethal": False,
                },
            ],
            "objectives": [
                {
                    "objective_ref": "objective:house-pressure",
                    "side_ref": "side:house",
                    "kind": "delay",
                    "target_refs": ["char.zhu"],
                    "zone_ref": None,
                    "deadline_tick": 1,
                }
            ],
        },
        "release-house-exact-combat",
        actor="ht.core.006",
        mode="autonomous",
    )
    combat_plan, combat_overlay = _validated(repo, combat)
    assert combat_plan.result["scale"] == "duel"
    assert "ht.core.006" in {row["participant_ref"] for row in combat_plan.result["participant_effects"]}
    assert not any(path.endswith("ht.core.006.v2.json") for path in combat_plan.writes)
    profile = combat_overlay.read_json("state/person-core/house-tang.json")["profiles"]["ht.core.006"]
    evidence = profile["institutional_progression"]["development_residual_units"]
    assert evidence["field.combat_events"] >= 1
    assert evidence["field.combat_exchanges"] >= 1
    assert any(row.get("evidence_ref") == "combat.release.house_exact_bridge" for row in profile["institutional_progression"]["service_history"])
