"""Production-path regression tests against the shipped real campaign snapshot.

These tests intentionally exercise the production RepositoryCommandPlanner over
actual campaign owners.  They do not mutate the source checkout; manifests are
validated through an in-memory StagedOverlay.
"""
from pathlib import Path
import shutil

import pytest

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.store import (
    RegisteredSchemaValidator,
    RegisteredTemplateValidator,
    RepositoryStore,
)
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionPlanner

ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_ID = "shinobi-wei-main"
ACTOR = "pc_wei_tang"
REVISION = 18
SUBMITTED = "2026-08-09T12:00:00Z"


def command(
    kind: str, payload: dict, suffix: str, *, revision: int = REVISION,
    actor_id: str = ACTOR, mode: str = "gameplay",
) -> CommandEnvelope:
    return CommandEnvelope(
        campaign_id=CAMPAIGN_ID,
        request_id=f"real-{suffix}",
        actor_id=actor_id,
        command_type=kind,
        expected_revision=revision,
        submitted_at=SUBMITTED,
        payload=payload,
        mode=mode,
    )


def validated_plan(envelope: CommandEnvelope, *, root: Path = ROOT):
    repo = RepositoryStore(root)
    planner = RepositoryCommandPlanner(repo)
    plan = planner.plan(envelope)
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


def test_real_campaign_advance_time_is_bounded_and_uses_causal_scheduler() -> None:
    plan, overlay = validated_plan(
        command(
            "advance_time",
            {"target_time": "SE-0061-02-06T21:16:00"},
            "advance",
        )
    )
    scheduler = overlay.read_json("state/time/causal-scheduler.json")
    assert scheduler["metrics"]["global_person_scans"] == 0
    assert scheduler["metrics"]["global_faction_directory_scans"] == 0
    assert set(plan.writes) == {"state/meta.json", "state/scene.json", "state/time/causal-scheduler.json"}


def test_real_campaign_training_plans_through_same_time_authority() -> None:
    plan, overlay = validated_plan(
        command(
            "training_resolution",
            {
                "actor_ref": ACTOR,
                "target": "operational_skills.tactics",
                "model_ref": "training.self_directed",
                "context_ref": None,
                "instructor_ref": None,
                "target_time": "SE-0061-02-06T21:45:00",
                "active_hours": "0.5",
            },
            "training",
        )
    )
    assert plan.result["command_type"] == "training_resolution"
    assert plan.result["world_time"] == "SE-0061-02-06T21:45:00"
    assert overlay.read_json("state/development/banks.json")["entries"][ACTOR]["resolved_through"] == "SE-0061-02-06T21:45:00"


def test_real_campaign_mission_creation_registers_owner_scheduler_and_history() -> None:
    plan, overlay = validated_plan(
        command(
            "mission_creation",
            {
                "mission_id": "mission.acceptance_black_hound_probe",
                "issuer_ref": "canon_hiruzen",
                "authority_ref": "canon_hiruzen",
                "mission_rank": "C",
                "participant_refs": [ACTOR],
                "objectives": [
                    {
                        "objective_id": "obj.investigate",
                        "kind": "investigate",
                        "required": True,
                        "dependencies": [],
                    }
                ],
                "settlement_terms": [],
                "deadline_at": "SE-0061-02-14T21:15:00",
                "next_due_at": "SE-0061-02-07T07:30:00",
                "operation_ref": None,
            },
            "mission-create",
            actor_id="canon_hiruzen", mode="autonomous",
        )
    )
    mission_path = "state/mission/mission.acceptance_black_hound_probe.json"
    assert mission_path in plan.writes
    assert overlay.read_json(mission_path)["state"] == "offered"
    scheduler = overlay.read_json("state/time/causal-scheduler.json")
    assert "host.mission.acceptance_black_hound_probe" in scheduler["hosts"]
    assert any(event["kind"] == "mission_created" for event in overlay.read_json("state/reg/world-events.json")["events"])


def test_real_campaign_recruitment_conserves_seeded_population_and_demographic_marginals() -> None:
    plan, overlay = validated_plan(
        command(
            "recruitment_resolution",
            {
                "source_pool_id": "pool.konoha.youth_candidate",
                "destination_pool_id": "pool.konoha.academy",
                "requested_count": 12,
                "policy_ref": "recruitment.academy",
                "authority_ref": "canon_hiruzen",
            },
            "recruitment",
            actor_id="canon_hiruzen", mode="autonomous",
        )
    )
    before = RepositoryStore(ROOT).read_json("state/population/registry.json")
    after = overlay.read_json("state/population/registry.json")
    before_total = sum(pool["count"] for pool in before["pools"].values())
    after_total = sum(pool["count"] for pool in after["pools"].values())
    assert before_total == after_total == 256000
    assert plan.result["accepted"] == 12
    receipt = after["transfers"][-1]
    assert receipt["source_removed"] == receipt["destination_added"] == 12
    for values in receipt["accepted_profile"]["dimension_counts"].values():
        assert sum(values.values()) == 12


def test_real_campaign_recruitment_rejects_wrong_source_category() -> None:
    repo = RepositoryStore(ROOT)
    planner = RepositoryCommandPlanner(repo)
    with pytest.raises(CommandRejectedError, match="recruitment_eligibility_invalid"):
        planner.plan(
            command(
                "recruitment_resolution",
                {
                    "source_pool_id": "pool.konoha.civilian_general",
                    "destination_pool_id": "pool.konoha.academy",
                    "requested_count": 5,
                    "policy_ref": "recruitment.academy",
                    "authority_ref": "canon_hiruzen",
                },
                "bad-recruitment",
                actor_id="canon_hiruzen", mode="autonomous",
            )
        )


def test_real_campaign_information_delivery_requires_existing_sender_knowledge(tmp_path: Path) -> None:
    campaign = tmp_path / "campaign"
    shutil.copytree(ROOT, campaign, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc"))

    claim_plan, claim_overlay = validated_plan(
        command(
            "information_claim_resolution",
            {
                "claim_id": "claim.acceptance_probe",
                "subject_ref": "canon_hayama_shirakumo",
                "source_ref": "canon_hayama_shirakumo",
                "holder_ref": "canon_hayama_shirakumo",
                "epistemic_kind": "observation",
                "confidence_milli": 900,
                "evidence_refs": [],
                "context_ref": None,
            },
            "information-claim",
            actor_id="canon_hayama_shirakumo", mode="autonomous",
        ),
        root=campaign,
    )
    for relative, payload in claim_plan.writes.items():
        path = campaign / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    delivery_plan, delivery_overlay = validated_plan(
        command(
            "information_delivery",
            {
                "claim_id": "claim.acceptance_probe",
                "sender_ref": "canon_hayama_shirakumo",
                "recipient_ref": ACTOR,
                "channel": "direct_briefing",
                "channel_confidence_milli": 1000,
            },
            "information-delivery",
            revision=19, actor_id="canon_hayama_shirakumo", mode="autonomous",
        ),
        root=campaign,
    )
    registry = delivery_overlay.read_json("state/reg/information-deliveries.json")
    assert "claim.acceptance_probe" in registry["claims"]
    assert registry["deliveries"][-1]["recipient_ref"] == ACTOR
    assert "claim.acceptance_probe" in registry["knowledge"][ACTOR]
    assert delivery_plan.result["command_type"] == "information_delivery"


def test_real_campaign_material_promise_creates_commitment_and_semantic_history() -> None:
    plan, overlay = validated_plan(
        command(
            "commitment_resolution",
            {
                "commitment_id": "commitment.acceptance_after_action_review",
                "kind": "promise",
                "subject_ref": ACTOR,
                "target_ref": "canon_hayama_shirakumo",
                "host_ref": "team.blackhound",
                "due_at": "SE-0061-02-08T18:00:00",
                "summary": "A formal after-action review is promised after the operation.",
                "visibility": "restricted",
            },
            "promise",
        )
    )
    assert "state/reg/commitments.json" in plan.writes
    records = overlay.read_json("state/reg/commitments.json")["records"]
    assert records[-1]["id"] == "commitment.acceptance_after_action_review"
    assert any(event["kind"] == "commitment_promise" for event in overlay.read_json("state/reg/world-events.json")["events"])


def test_real_campaign_exact_combat_requires_colocation_and_plans_nonlethal_spar() -> None:
    plan, overlay = validated_plan(
        command(
            "combat_resolution",
            {
                "combat_id": "combat.acceptance_black_hound_spar",
                "scale": "duel",
                "participants": [
                    {
                        "actor_ref": ACTOR,
                        "side_ref": "side:wei",
                        "action": "attack",
                        "target_refs": ["canon_hayama_shirakumo"],
                        "objective_ref": "objective:pressure",
                        "lethal": False,
                    },
                    {
                        "actor_ref": "canon_hayama_shirakumo",
                        "side_ref": "side:hayama",
                        "action": "hold",
                        "target_refs": [],
                        "objective_ref": None,
                        "lethal": False,
                    },
                ],
                "objectives": [
                    {
                        "objective_ref": "objective:pressure",
                        "side_ref": "side:wei",
                        "kind": "delay",
                        "target_refs": ["canon_hayama_shirakumo"],
                        "zone_ref": None,
                        "deadline_tick": 1,
                    }
                ],
            },
            "combat",
        )
    )
    assert plan.result["command_type"] == "combat_resolution"
    assert plan.result["scale"] == "duel"
    assert overlay.read_json("state/player.json")["owner_id"] == ACTOR


def test_real_campaign_exact_combat_rejects_remote_participant() -> None:
    repo = RepositoryStore(ROOT)
    planner = RepositoryCommandPlanner(repo)
    with pytest.raises(CommandRejectedError, match="combat_participant_not_co_located"):
        planner.plan(
            command(
                "combat_resolution",
                {
                    "combat_id": "combat.acceptance_remote_reject",
                    "scale": "duel",
                    "participants": [
                        {
                            "actor_ref": ACTOR,
                            "side_ref": "side:wei",
                            "action": "attack",
                            "target_refs": ["canon_zabuza"],
                            "objective_ref": "objective:test",
                            "lethal": False,
                        },
                        {
                            "actor_ref": "canon_zabuza",
                            "side_ref": "side:zabuza",
                            "action": "hold",
                            "target_refs": [],
                            "objective_ref": None,
                            "lethal": False,
                        },
                    ],
                    "objectives": [
                        {
                            "objective_ref": "objective:test",
                            "side_ref": "side:wei",
                            "kind": "delay",
                            "target_refs": ["canon_zabuza"],
                            "zone_ref": None,
                            "deadline_tick": 1,
                        }
                    ],
                },
                "remote-combat",
            )
        )


def test_real_campaign_multiday_travel_fails_closed_at_known_player_boundary() -> None:
    repo = RepositoryStore(ROOT)
    planner = RepositoryCommandPlanner(repo)
    with pytest.raises(CommandRejectedError, match="time_boundary_requires_domain_settlement"):
        planner.plan(
            command(
                "travel_resolution",
                {
                    "route_id": "route_konoha_wave",
                    "destination_id": "place.waves.town",
                    "traveler_refs": [ACTOR],
                    "party_context_ref": None,
                    "mission_ref": None,
                },
                "travel-boundary",
            )
        )


def test_real_campaign_person_materialization_conserves_humans_and_creates_sparse_core() -> None:
    before = RepositoryStore(ROOT).read_json("state/population/registry.json")
    source_before = before["pools"]["pool.konoha.youth_candidate"]["count"]
    plan, overlay = validated_plan(
        command(
            "person_materialization",
            {
                "source_pool_id": "pool.konoha.youth_candidate",
                "authority_ref": "canon_hiruzen",
                "name": "Renji Sato",
                "aliases": [],
                "pronouns": "he/him",
                "birth_date": "SE-0049-01-12",
                "origin": "Konohagakure / Land of Fire",
                "location_ref": "place.konoha",
                "role_profile_ref": "role.population.rostered",
                "identity_cues": {
                    "appearance": "A compact Academy-age youth with carefully wrapped practice gloves.",
                    "temperament": "Attentive and reserved around senior shinobi.",
                    "doctrine_expression": "No individualized doctrine has been established.",
                },
            },
            "materialize",
            actor_id="canon_hiruzen", mode="autonomous",
        )
    )
    after = overlay.read_json("state/population/registry.json")
    person_id = plan.result["person_id"]
    source_after = after["pools"]["pool.konoha.youth_candidate"]
    assert source_after["count"] == source_before
    assert source_after["representation"]["anonymous_count"] == before["pools"]["pool.konoha.youth_candidate"]["representation"]["anonymous_count"] - 1
    assert source_after["representation"]["rostered_count"] == before["pools"]["pool.konoha.youth_candidate"]["representation"]["rostered_count"] + 1
    core = overlay.read_json("state/person-core/world.json")["people"][person_id]
    assert core["cohort_ref"] == "pool.konoha.youth_candidate"
    assert core["component_refs"] == {}
    assert core["provenance"]["source_kind"] == "population_materialization"
    assert person_id in source_after["representation"]["rostered_person_refs"]
    assert overlay.read_json("state/index/owners/person.json")["owners"][person_id] == "state/person-core/world.json"


def test_real_campaign_relationship_resolution_uses_generic_effects_and_persistent_edge() -> None:
    plan, overlay = validated_plan(
        command(
            "relationship_resolution",
            {
                "target_ref": "canon_hayama_shirakumo",
                "relationship_type": "teammate",
                "interaction_kind": "shared_training",
                "summary": "A disciplined joint training block improves working familiarity.",
                "visibility": "restricted",
            },
            "relationship",
        )
    )
    assert plan.result["interaction_kind"] == "shared_training"
    edge_id = plan.result["relationship_edge_id"]
    path = "state/reg/relationship-edges/canon_hayama_shirakumo.json"
    edge = overlay.read_json(path)["relationship_edges"][edge_id]
    assert edge["source_id"] == "canon_hayama_shirakumo"
    assert edge["target_id"] == ACTOR
    assert edge["trust"] == 52
    assert edge["respect"] == 53
    assert edge["affection"] == 51
    assert overlay.read_json("state/reg/relationship-edge-index.json")["edge_index"][edge_id] == path


def test_real_campaign_asset_transfer_rejects_item_not_controlled_by_actor() -> None:
    repo = RepositoryStore(ROOT)
    planner = RepositoryCommandPlanner(repo)
    with pytest.raises(CommandRejectedError, match="asset_transfer_not_authorized"):
        planner.plan(
            command(
                "asset_transfer_resolution",
                {
                    "item_ref": "item_samehada",
                    "from_holder_ref": "canon_kisame",
                    "to_holder_ref": ACTOR,
                    "transfer_kind": "custody_transfer",
                    "summary": "Attempted unauthorized transfer.",
                    "visibility": "restricted",
                },
                "bad-asset-transfer",
            )
        )


def test_asset_transfer_changes_named_item_custody_on_disposable_campaign(tmp_path: Path) -> None:
    campaign = tmp_path / "campaign"
    shutil.copytree(ROOT, campaign, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc"))
    named_path = campaign / "state/reg/named-items.json"
    registry = __import__("json").loads(named_path.read_text())
    item = next(entry for entry in registry["named_items"] if entry["id"] == "item_samehada")
    item["physical_holder_id"] = ACTOR
    item["legal_owner_claim"] = ACTOR
    named_path.write_text(__import__("json").dumps(registry, indent=2) + "\n")
    plan, overlay = validated_plan(
        command(
            "asset_transfer_resolution",
            {
                "item_ref": "item_samehada",
                "from_holder_ref": ACTOR,
                "to_holder_ref": "canon_hayama_shirakumo",
                "transfer_kind": "give",
                "summary": "A named item is voluntarily handed over.",
                "visibility": "restricted",
            },
            "asset-transfer",
        ),
        root=campaign,
    )
    item_after = next(entry for entry in overlay.read_json("state/reg/named-items.json")["named_items"] if entry["id"] == "item_samehada")
    assert item_after["physical_holder_id"] == "canon_hayama_shirakumo"
    assert plan.result["authority_basis"] == "holder_self"


def test_real_campaign_local_travel_uses_shared_route_anchor_without_hardcoded_place_logic() -> None:
    plan, overlay = validated_plan(
        command(
            "travel_resolution",
            {
                "route_id": "route_local",
                "destination_id": "place.konoha.academy.assignment.hall",
                "traveler_refs": [ACTOR],
                "party_context_ref": None,
                "mission_ref": None,
            },
            "local-travel",
        )
    )
    assert plan.result["origin_id"] == "place.sword_manor"
    assert plan.result["destination_id"] == "place.konoha.academy.assignment.hall"
    assert 0 < plan.result["travel_seconds"] < 7200
    assert overlay.read_json("state/player.json")["current_location_id"] == "place.konoha.academy.assignment.hall"
