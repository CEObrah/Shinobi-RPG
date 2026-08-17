from pathlib import Path
import copy
import json
import shutil

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.combat import CapabilityProfile
from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.store import RegisteredSchemaValidator, RegisteredTemplateValidator, RepositoryStore
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionPlanner

ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_ID = "shinobi-wei-main"
SUBMITTED = "2026-08-14T00:00:00Z"


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "campaign"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc"))
    return root


def _cmd(root: Path, payload: dict, suffix: str) -> CommandEnvelope:
    meta = RepositoryStore(root).read_json("state/meta.json")
    return CommandEnvelope(
        campaign_id=CAMPAIGN_ID,
        request_id=f"ocular-{suffix}-{meta['revision']}",
        actor_id="canon_tobi",
        command_type="ocular_stockpile_resolution",
        expected_revision=meta["revision"],
        submitted_at=SUBMITTED,
        payload=payload,
        mode="autonomous",
    )


def _plan(root: Path, payload: dict, suffix: str):
    repo = RepositoryStore(root)
    env = _cmd(root, payload, suffix)
    plan = RepositoryCommandPlanner(repo).plan(env)
    manifest = TransactionPlanner(repo).plan(env, transaction_id=plan.transaction_id, created_at=plan.created_at, writes=plan.writes)
    overlay = StagedOverlay(repo, manifest)
    plan.validator(overlay, manifest)
    RegisteredSchemaValidator(repo).validate_overlay(overlay, manifest.paths)
    RegisteredTemplateValidator(repo).validate_overlay(overlay, manifest.paths)
    return plan, overlay


def _commit(root: Path, plan) -> None:
    for relative, payload in plan.writes.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def test_obito_batch_materializes_lowest_exact_eye_without_reroll_or_regeneration(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    payload = {"action": "materialize", "stockpile_ref": "obito_stockpile"}
    first_plan, first_overlay = _plan(root, payload, "first")
    repeated_plan, _ = _plan(root, payload, "repeat-preview")
    assert first_plan.result["eye_ref"] == repeated_plan.result["eye_ref"] == "eye.obito_store.001"
    assert first_plan.result["remaining_count"] == 76
    batch = first_overlay.read_json("state/medical/ocular-storage/obito.json")
    assert batch["unique_asset_count"] == 76
    assert batch["available_ordinal_ranges"] == [[2, 77]]
    shard = first_overlay.read_json("state/medical/ocular/owners/canon_tobi.json")
    eyes = [row for row in shard["eyes"] if row["eye_id"] == "eye.obito_store.001"]
    assert len(eyes) == 1
    assert eyes[0]["hardware_stage"] == "3_tomoe"
    assert eyes[0]["condition"] == "preserved_functional"
    assert eyes[0]["original_owner_id"] == "unknown_uchiha_store_001"
    _commit(root, first_plan)

    second_plan, second_overlay = _plan(root, payload, "second")
    assert second_plan.result["eye_ref"] == "eye.obito_store.002"
    assert second_overlay.read_json("state/medical/ocular-storage/obito.json")["unique_asset_count"] == 75

    planner = RepositoryCommandPlanner(RepositoryStore(root))
    with pytest.raises(CommandRejectedError, match="ocular_stockpile_eye_already_materialized|ocular_stockpile_eye_unavailable"):
        planner.plan(_cmd(root, {"action": "materialize", "stockpile_ref": "obito_stockpile", "eye_ref": "eye.obito_store.001"}, "reuse"))


def _add_second_kakashi_sharingan(root: Path) -> None:
    path = root / "state/medical/ocular/owners/canon_kakashi.json"
    shard = json.loads(path.read_text())
    left = copy.deepcopy(shard["eyes"][0])
    left.update({
        "eye_id": "eye.kakashi.right.test",
        "side": "right",
        "current_location": "canon_kakashi:right_eye_socket",
        "original_owner_id": "unknown_uchiha_test_donor",
        "mangekyo_identity": None,
        "unique_ability_refs": [],
    })
    shard["eyes"].append(left)
    path.write_text(json.dumps(shard, indent=2) + "\n")


def _neutral_capability() -> CapabilityProfile:
    return CapabilityProfile(
        offense=50,
        defense=50,
        mobility=50,
        perception=50,
        control=50,
        stealth=50,
        capture=50,
        escape=50,
        protection=50,
    )


def test_two_functional_sharingan_are_not_averaged_down_to_one_eye(tmp_path: Path) -> None:
    one_root = _copy(tmp_path / "one")
    one_planner = RepositoryCommandPlanner(RepositoryStore(one_root))
    one_record = copy.deepcopy(RepositoryStore(one_root).read_json("state/char/kakashi.json"))
    one_cap, one_init, _one_cost, one_meta = one_planner._ocular_combat_projection(
        actor_ref="canon_kakashi",
        record=one_record,
        capability=_neutral_capability(),
        method="sword",
        technique=None,
        requested_eye_refs=None,
        autonomous=True,
        duration_seconds=6,
    )

    two_root = _copy(tmp_path / "two")
    _add_second_kakashi_sharingan(two_root)
    two_planner = RepositoryCommandPlanner(RepositoryStore(two_root))
    two_record = copy.deepcopy(RepositoryStore(two_root).read_json("state/char/kakashi.json"))
    two_cap, two_init, _two_cost, two_meta = two_planner._ocular_combat_projection(
        actor_ref="canon_kakashi",
        record=two_record,
        capability=_neutral_capability(),
        method="sword",
        technique=None,
        requested_eye_refs=None,
        autonomous=True,
        duration_seconds=6,
    )

    assert len(one_meta["active_eye_refs"]) == 1
    assert len(two_meta["active_eye_refs"]) == 2
    assert two_cap.perception > one_cap.perception
    assert two_cap.defense > one_cap.defense
    assert two_init > one_init
    assert two_meta["pair_integration"] is not None
    assert two_meta["passive_drain_milli_accrued"] == 2 * one_meta["passive_drain_milli_accrued"]


def test_pair_required_ocular_action_requires_both_functional_eye_sockets(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    planner = RepositoryCommandPlanner(RepositoryStore(root))
    record = copy.deepcopy(RepositoryStore(root).read_json("state/char/kakashi.json"))
    pair_technique = {"method_id": "test_pair_dojutsu", "physical_profile": {"requires_ocular_pair": True}}
    with pytest.raises(CommandRejectedError, match="combat_dojutsu_pair_required"):
        planner._ocular_combat_projection(
            actor_ref="canon_kakashi",
            record=record,
            capability=_neutral_capability(),
            method="sword",
            technique=pair_technique,
            requested_eye_refs=None,
            autonomous=True,
            duration_seconds=6,
        )

    _add_second_kakashi_sharingan(root)
    planner = RepositoryCommandPlanner(RepositoryStore(root))
    record = copy.deepcopy(RepositoryStore(root).read_json("state/char/kakashi.json"))
    _cap, _initiative, _cost, meta = planner._ocular_combat_projection(
        actor_ref="canon_kakashi",
        record=record,
        capability=_neutral_capability(),
        method="sword",
        technique=pair_technique,
        requested_eye_refs=None,
        autonomous=True,
        duration_seconds=6,
    )
    assert meta["pair_integration"] == 115


def _autonomous_kakashi_combat(root: Path) -> CommandEnvelope:
    scene_path = root / "state/scene.json"
    scene = json.loads(scene_path.read_text())
    scene["location_id"] = "place.konoha.residential"
    scene_path.write_text(json.dumps(scene, indent=2) + "\n")
    neji_path = root / "state/char/neji.json"
    neji = json.loads(neji_path.read_text())
    neji["current_location_id"] = "place.konoha.residential"
    neji_path.write_text(json.dumps(neji, indent=2) + "\n")
    kakashi_path = root / "state/char/kakashi.json"
    kakashi = json.loads(kakashi_path.read_text())
    kakashi["dojutsu_state"]["passive_drain_milli_accumulator"] = 900
    kakashi_path.write_text(json.dumps(kakashi, indent=2) + "\n")
    repo = RepositoryStore(root)
    meta = repo.read_json("state/meta.json")
    return CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="ocular-kakashi-combat",
        actor_id="canon_kakashi",
        command_type="combat_resolution",
        expected_revision=meta["revision"],
        submitted_at=SUBMITTED,
        payload={
            "combat_id": "combat.test.ocular.persistence",
            "scale": "duel",
            "range_band": 0,
            "participants": [
                {
                    "actor_ref": "canon_kakashi",
                    "side_ref": "side:kakashi",
                    "action": "attack",
                    "target_refs": ["canon_neji"],
                    "objective_ref": "objective:kakashi",
                    "lethal": False,
                },
                {
                    "actor_ref": "canon_neji",
                    "side_ref": "side:neji",
                    "action": "hold",
                    "target_refs": [],
                    "objective_ref": None,
                    "lethal": False,
                },
            ],
            "objectives": [
                {
                    "objective_ref": "objective:kakashi",
                    "side_ref": "side:kakashi",
                    "kind": "delay",
                    "target_refs": ["canon_neji"],
                    "zone_ref": None,
                    "deadline_tick": 1,
                }
            ],
        },
        mode="autonomous",
    )


def test_transplanted_sharingan_passive_drain_persists_through_exact_combat(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    envelope = _autonomous_kakashi_combat(root)
    repo = RepositoryStore(root)
    before = repo.read_json("state/char/kakashi.json")["resources"]["chakra"]["current"]
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
    used = plan.result["methods_used"]["canon_kakashi"]
    assert used["dojutsu"]["passive_chakra_cost"] == 1
    after = overlay.read_json("state/char/kakashi.json")
    assert after["resources"]["chakra"]["current"] == before - used["chakra_cost"]
    assert after["dojutsu_state"]["passive_drain_milli_accumulator"] == 65


def _planner(root: Path) -> RepositoryCommandPlanner:
    return RepositoryCommandPlanner(RepositoryStore(root))


def test_two_identical_sharingan_eyes_contribute_two_exact_visual_channels_without_speed_bonus(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    planner = _planner(root)
    record = RepositoryStore(root).read_json("state/char/kakashi.json")
    base = planner._combat_capability(record)
    one_cap, one_init, one_cost, one_meta = planner._ocular_combat_projection(
        actor_ref="canon_kakashi",
        record=record,
        capability=base,
        method="unarmed",
        technique=None,
        requested_eye_refs=None,
        autonomous=False,
        duration_seconds=120,
    )
    assert one_meta["active_eye_refs"] == ["eye.kakashi.left"]
    assert one_cap.mobility == base.mobility
    assert one_cap.perception > base.perception
    assert one_cap.defense > base.defense
    assert one_init > 0
    assert one_meta["passive_drain_milli_accrued"] > 0

    shard_path = root / "state/medical/ocular/owners/canon_kakashi.json"
    shard = __import__("json").loads(shard_path.read_text())
    duplicate = dict(shard["eyes"][0])
    duplicate.update({
        "eye_id": "eye.kakashi.right.test",
        "side": "right",
        "current_location": "canon_kakashi:right_eye_socket",
    })
    shard["eyes"].append(duplicate)
    shard_path.write_text(__import__("json").dumps(shard, indent=2) + "\n")
    registry_path = root / "state/medical/ocular-registry.json"
    registry = __import__("json").loads(registry_path.read_text())
    registry["eye_index"][duplicate["eye_id"]] = "state/medical/ocular/owners/canon_kakashi.json"
    registry_path.write_text(__import__("json").dumps(registry, indent=2) + "\n")

    planner = _planner(root)
    two_record = RepositoryStore(root).read_json("state/char/kakashi.json")
    two_cap, two_init, two_cost, two_meta = planner._ocular_combat_projection(
        actor_ref="canon_kakashi",
        record=two_record,
        capability=base,
        method="unarmed",
        technique=None,
        requested_eye_refs=None,
        autonomous=False,
        duration_seconds=120,
    )
    assert set(two_meta["active_eye_refs"]) == {"eye.kakashi.left", "eye.kakashi.right.test"}
    assert two_cap.perception > one_cap.perception
    assert two_cap.defense > one_cap.defense
    assert two_init > one_init
    assert two_cap.mobility == one_cap.mobility == base.mobility
    assert two_meta["passive_drain_milli_accrued"] == 2 * one_meta["passive_drain_milli_accrued"]
    assert two_cost >= one_cost


def test_byakugan_eyes_use_registered_resolution_and_tenketsu_formulas(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    planner = _planner(root)
    record = RepositoryStore(root).read_json("state/char/neji.json")
    base = planner._combat_capability(record)
    two_cap, two_init, _cost, meta = planner._ocular_combat_projection(
        actor_ref="canon_neji",
        record=record,
        capability=base,
        method="unarmed",
        technique=None,
        requested_eye_refs=None,
        autonomous=True,
        duration_seconds=12,
    )
    assert len(meta["active_eye_refs"]) == 2
    assert two_cap.perception > base.perception
    assert two_cap.control > base.control
    assert two_cap.capture > base.capture
    assert two_cap.mobility == base.mobility
    assert two_init == 0


def test_pair_integration_uses_registered_harmonic_formula(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    planner = _planner(root)
    record = RepositoryStore(root).read_json("state/char/tobi.json")
    base = planner._combat_capability(record)
    _cap, _init, _cost, meta = planner._ocular_combat_projection(
        actor_ref="canon_tobi",
        record=record,
        capability=base,
        method="unarmed",
        technique=None,
        requested_eye_refs=None,
        autonomous=True,
        duration_seconds=12,
    )
    expected = (2 * 200 * 185 + (200 + 185) // 2) // (200 + 185)
    assert meta["pair_integration"] == expected
    assert set(meta["active_eye_refs"]) == {"eye.obito.right", "eye.obito.left_replacement"}


def test_transplanted_sharingan_passive_drain_reaches_exact_zero_at_full_integration_and_control(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    shard_path = root / "state/medical/ocular/owners/canon_kakashi.json"
    shard = __import__("json").loads(shard_path.read_text())
    shard["eyes"][0]["integration"] = 200
    shard_path.write_text(__import__("json").dumps(shard, indent=2) + "\n")
    char_path = root / "state/char/kakashi.json"
    record = __import__("json").loads(char_path.read_text())
    record["dojutsu_state"]["ocular_control"] = 200
    char_path.write_text(__import__("json").dumps(record, indent=2) + "\n")
    planner = _planner(root)
    record = RepositoryStore(root).read_json("state/char/kakashi.json")
    base = planner._combat_capability(record)
    _cap, _init, cost, meta = planner._ocular_combat_projection(
        actor_ref="canon_kakashi",
        record=record,
        capability=base,
        method="unarmed",
        technique=None,
        requested_eye_refs=None,
        autonomous=False,
        duration_seconds=600,
    )
    assert cost == 0
    assert meta["passive_drain_milli_accrued"] == 0
    assert meta["passive_chakra_cost"] == 0


def test_latent_eye_cannot_be_forced_active_by_combat_payload(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    planner = _planner(root)
    record = RepositoryStore(root).read_json("state/char/sasuke.json")
    base = planner._combat_capability(record)
    with pytest.raises(CommandRejectedError, match="combat_active_eye_not_field_usable"):
        planner._ocular_combat_projection(
            actor_ref="canon_sasuke",
            record=record,
            capability=base,
            method="unarmed",
            technique=None,
            requested_eye_refs=["eye.sasuke.left"],
            autonomous=False,
            duration_seconds=12,
        )


def test_eye_specific_transplant_active_cost_uses_existing_efficiency_formula(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    planner = _planner(root)
    record = RepositoryStore(root).read_json("state/char/kakashi.json")
    technique = planner._combat_technique_record("sharingan_left_eye")
    base_cost = planner._technique_chakra_cost(record, technique)
    base = planner._combat_capability(record)
    _cap, _init, extra_cost, meta = planner._ocular_combat_projection(
        actor_ref="canon_kakashi",
        record=record,
        capability=base,
        method="sensory",
        technique=technique,
        requested_eye_refs=["eye.kakashi.left"],
        autonomous=False,
        duration_seconds=12,
        technique_chakra_cost=base_cost,
    )
    assert meta["active_transplant_cost_eye_ref"] == "eye.kakashi.left"
    assert meta["adjusted_active_chakra_cost"] > meta["registered_active_chakra_cost"]
    assert extra_cost >= meta["adjusted_active_chakra_cost"] - base_cost
