from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.mission_owner import MissionOwner
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.reducers import Mission, MissionObjective, SettlementTerm
from shinobi_runtime.sim import CampaignTime
from shinobi_runtime.sim.events import EventQueue
from shinobi_runtime.sim.hosts import HostState
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry, SchedulerHost, one_shot_event, recurring_event
from shinobi_runtime.sim.scheduler_store import legacy_to_shards
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.tx import (
    GitStager,
    ReceiptStore,
    TransactionCoordinator,
    WriteAheadLog,
)
from shinobi_runtime.tx.errors import StaleRevisionError


CURRENT = "SE-0061-02-06T21:15:00"


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _safe_before_test(due: CampaignTime, current: CampaignTime) -> CampaignTime:
    candidate = due.add_seconds(-1)
    return candidate if candidate >= current else current


def causal_scheduler_fixture(processes: list[dict], scene_boundary: tuple[str, str] | None) -> dict:
    current = CampaignTime.parse(CURRENT)
    hosts = {}
    events = []
    for process in processes:
        if process["id"] != "process_test_pressure":
            continue
        due = CampaignTime.parse(process["next_due"])
        pressure_id = process["coverage"][0]
        host_id = "host.canon_pressure." + pressure_id
        recurrence = dict(process["recurrence"])
        hosts[host_id] = SchedulerHost(
            state=HostState(
                host_id=host_id, kind="canon_pressure", resolved_through=current,
                safe_through=_safe_before_test(due, current),
                handler_ref="causal.scheduler.1", rng_namespace=pressure_id, next_due=due,
            ),
            authority_kind="canon_pressure", owner_ref="state/canon/pressures.json",
            metadata={"pressure_id": pressure_id, "status": "active"},
        )
        events.append(recurring_event(
            kind="canon_pressure.periodic_review", identity=pressure_id,
            host_id=host_id, due_at=due, recurrence=recurrence,
            payload={"pressure_id": pressure_id, "owner_ref": "state/canon/pressures.json"},
            priority=80,
        ))
    if scene_boundary is not None:
        due = CampaignTime.parse(scene_boundary[0])
        host_id = "host.scene.current"
        hosts[host_id] = SchedulerHost(
            state=HostState(
                host_id=host_id, kind="scene", resolved_through=current,
                safe_through=_safe_before_test(due, current),
                handler_ref="causal.scheduler.1", rng_namespace="scene", next_due=due,
            ),
            authority_kind="scene_boundary_index", owner_ref="state/scene.json",
            metadata={"scene_id": "scene.test"},
        )
        events.append(one_shot_event(
            kind="scene.player_boundary", identity=scene_boundary[1],
            source_host=host_id, target_host=host_id, due_at=due,
            payload={"scene_event_id": scene_boundary[1]}, priority=10,
            visibility="player_known", requires_player=True,
        ))
    registry = CausalSchedulerRegistry(
        world_time=current, hosts=hosts, queue=EventQueue(events), seeded_at=current,
        bootstrap_source="synthetic_fixture",
        metrics={
            "host_count": len(hosts), "pending_event_count": len(events),
            "global_person_scans": 0, "global_faction_directory_scans": 0,
        },
    )
    return dict(registry.to_record())


def campaign_files(
    *,
    canon_due: str | None = None,
    scene_boundary: tuple[str, str] | None = None,
    include_objective_evidence: bool = True,
    loaded_owner_ids: list[str] | None = None,
) -> dict[str, dict]:
    processes = [
        {
            "id": "process_test_continuous",
            "status": "active",
            "settlement_mode": "batchable",
            "settled_through": CURRENT,
            "next_due": "SE-0061-02-08T00:00:00",
            "source": "team_training",
            "recurrence": {
                "kind": "fixed_interval",
                "accrual_mode": "continuous",
                "interval_seconds": 604800,
            },
            "coverage": ["team.test"],
        }
    ]
    pressures = {
        "schema": "canon-pressure-registry",
        "owner_id": "world_world_pressures",
        "owner_type": "canon_pressure_registry",
        "authority": True,
        "canon_rule": "canon is trajectory, not forced result",
        "pressures": {},
    }
    if canon_due is not None:
        process_id = "process_test_pressure"
        pressure_id = "pressure_test_front"
        processes.append(
            {
                "id": process_id,
                "status": "active",
                "settlement_mode": "batchable",
                "settled_through": CURRENT,
                "next_due": canon_due,
                "source": "scheduler",
                "recurrence": {
                    "kind": "fixed_interval",
                    "accrual_mode": "boundary_only",
                    "interval_seconds": 604800,
                },
                "coverage": [pressure_id],
            }
        )
        pressures["pressures"][pressure_id] = {
            "id": pressure_id,
            "title": "Unresolved test pressure",
            "host_ref": "faction.test",
            "status": "active",
            "goal": None,
            "stakes": ["change", "stasis"],
            "actors": [],
            "resources": [],
            "constraints": {"canon_forcing": False, "refs": []},
            "opposition": [],
            "current_step": None,
            "next_boundary": {
                "host_ref": "host.canon_pressure." + pressure_id,
                "settled_through": CURRENT,
                "due_at": canon_due,
            },
            "reducer_ref": "runtime/contracts/system-contracts/canon_pressures.json",
            "source_refs": [],
            "evidence_refs": [],
            "visibility": {"classification": None, "basis_refs": []},
            "knowledge": {"player_refs": [], "npc_refs": []},
            "chronology": [],
        }
    files = {
        "state/meta.json": {
            "schema": "meta",
            "campaign_id": "planner-test",
            "game": "shinobi",
            "time": CURRENT,
            "revision": 1,
            "player_id": "pc_wei_tang",
        },
        "state/scene.json": {
            "schema": "scene",
            "scene_id": "scene.test",
            "world_time": CURRENT,
            "location_id": "place.test",
            "active_combat": False,
            "time_passage_allowed": True,
            "freeform_actions_allowed": True,
            "loaded_owner_ids": (
                ["pc_wei_tang"]
                if loaded_owner_ids is None
                else list(loaded_owner_ids)
            ),
            "known_clock_boundaries": (
                []
                if scene_boundary is None
                else [
                    {
                        "due_at": scene_boundary[0],
                        "event_id": scene_boundary[1],
                        "visibility": "player_known",
                    }
                ]
            ),
            "scene_summary": "Test scene.",
            "decision_required": "Choose an action.",
            "narrative": {},
            "observable_pressures": [],
        },
        "state/reg/combat-zoom.json": {
            "schema": "combat-zoom-registry",
            "pending_by_actor": {},
        },
        "state/inventory/registry.json": {
            "schema": "inventory-registry",
            "authority": True,
            "holders": {
                "faction.test": {"currency.ryo": 1000},
                "account.test": {},
                "escrow.mission.test": {"currency.ryo": 10},
            },
        },
        "state/canon/pressures.json": pressures,
        "state/index/owners.json": {
            "schema": "owner-index",
            "prefix_index": {
                "authority": "state/index/owners/authority.json",
                "faction": "state/index/owners/faction.json",
                "pc": "state/index/owners/pc.json",
                "team": "state/index/owners/team.json",
            },
        },
        "state/index/owners/authority.json": {
            "schema": "owner-index-shard",
            "prefix": "authority",
            "authority": False,
            "owners": {"authority.test": "state/authority/test.json"},
        },
        "state/index/owners/faction.json": {
            "schema": "owner-index-shard",
            "prefix": "faction",
            "authority": False,
            "owners": {"faction.test": "state/faction/test.json"},
        },
        "state/index/owners/pc.json": {
            "schema": "owner-index-shard",
            "prefix": "pc",
            "authority": False,
            "owners": {"pc_wei_tang": "state/player.json"},
        },
        "state/index/owners/team.json": {
            "schema": "owner-index-shard",
            "prefix": "team",
            "authority": False,
            "owners": {"team.test": "state/team/test.json"},
        },
        "state/team/test.json": {
            "schema": "synthetic-team-owner",
            "id": "team.test",
        },
        "state/authority/test.json": {
            "schema": "synthetic-authority-owner",
            "id": "authority.test",
        },
        "state/faction/test.json": {
            "schema": "synthetic-faction-owner",
            "id": "faction.test",
        },
        "state/player.json": {
            "schema": "synthetic-player-owner",
            "owner_id": "pc_wei_tang",
        },
        "game/rules/autonomy/policies.json": {
            "schema": "autonomy-policies",
            "profiles": {},
            "faction_assignments": {},
            "team_profiles": {"default": {}},
            "institution_assignments": {},
        },
        "state/reg/world-events.json": {
            "schema": "world-event-registry",
            "owner_id": "registry.world_events",
            "owner_type": "world_event_registry",
            "segment_limit": 128,
            "archived_event_count": 0,
            "archive_refs": [],
            "next_archive_seq": 1,
            "events": ([
                {
                    "id": "event.mission_test_obj_first_succeeded",
                    "kind": "information_claim_created",
                    "status": "resolved",
                    "timing": {
                        "scheduled_for": None,
                        "occurred_at": CURRENT,
                        "started_at": CURRENT,
                        "ended_at": CURRENT,
                    },
                    "host_refs": ["faction.test"],
                    "actor_refs": ["team.test"],
                    "place_refs": [],
                    "causal_refs": ["mission.test", "operation.test"],
                    "affected_owner_refs": ["mission.test"],
                    "material_consequence_refs": [
                        "mission-objective:mission.test:obj.first:succeeded:1000"
                    ],
                    "visibility": {
                        "classification": "restricted",
                        "witness_refs": [],
                        "audience_refs": ["pc_wei_tang"],
                        "knowledge_refs": [],
                        "route_refs": [],
                    },
                    "provenance": {
                        "source_kind": "resolved_operation",
                        "source_refs": ["operation.test"],
                        "archetype_ref": None,
                        "recorded_at": CURRENT,
                    },
                    "execution": {
                        "reducer_ref": "test.mission-evidence",
                        "reducer_version": "1",
                        "transaction_ref": "tx.evidence.test",
                        "receipt_refs": ["receipt.evidence.test"],
                    },
                    "supersedes_ref": None,
                    "superseded_by_ref": None,
                }
            ] if include_objective_evidence else []),
            "archetype_catalog_ref": "game/data/content/world-event-archetypes.json",
        },
    }
    scheduler_files = legacy_to_shards(causal_scheduler_fixture(processes, scene_boundary))
    files.update({path: json.loads(raw.decode("utf-8")) for path, raw in scheduler_files.items()})
    return files


def offered_owner(*, with_terms: bool = False) -> MissionOwner:
    terms = ()
    if with_terms:
        terms = (
            SettlementTerm(
                term_id="term.reward",
                direction="reward",
                account_ref="account.test",
                asset_ref="currency.ryo",
                quantity=10,
                applies_on=("succeeded",),
            ),
        )
    return MissionOwner(
        mission=Mission(
            mission_id="mission.test",
            state="offered",
            participant_refs=("pc_wei_tang", "team.test"),
            objectives=(
                MissionObjective(
                    objective_id="obj.first",
                    kind="identify",
                    required=True,
                ),
            ),
            settlement_terms=terms,
        ),
        issuer_ref="faction.test",
        authority_ref="authority.test",
        mission_rank="D",
        funding_holder_ref="faction.test",
        escrow_holder_ref=("escrow.mission.test" if with_terms else None),
        opened_at=CampaignTime.parse(CURRENT),
        authorized_at=CampaignTime.parse(CURRENT),
        starts_at=None,
        deadline_at=CampaignTime.parse("SE-0061-02-09T00:00:00"),
        next_due_at=CampaignTime.parse("SE-0061-02-08T00:00:00"),
        operation_ref=None,
        closed_at=None,
    )


def make_campaign(
    tmp_path: Path,
    *,
    canon_due: str | None = None,
    scene_boundary: tuple[str, str] | None = None,
    include_objective_evidence: bool = True,
    loaded_owner_ids: list[str] | None = None,
    mission: MissionOwner | None = None,
    house_roster: dict | None = None,
):
    root = tmp_path / "campaign"
    runtime = tmp_path / "runtime"
    for relative, value in campaign_files(
        canon_due=canon_due,
        scene_boundary=scene_boundary,
        include_objective_evidence=include_objective_evidence,
        loaded_owner_ids=loaded_owner_ids,
    ).items():
        write_json(root / relative, value)
    unrelated = b'{\n  "ratio": 1.25,\n  "status": "untouched"\n}\n'
    (root / "state" / "unrelated.json").write_bytes(unrelated)
    if mission is not None:
        write_json(root / "state/mission/mission.test.json", dict(mission.to_record()))
    if house_roster is not None:
        write_json(root / "state/person-core/house-tang.json", house_roster)
    git(root.parent, "init", "-q", str(root))
    git(root, "config", "user.email", "runtime@example.invalid")
    git(root, "config", "user.name", "Runtime Test")
    git(root, "add", "state", "game")
    git(root, "commit", "-qm", "baseline")
    repository = RepositoryStore(root)
    stager = GitStager(root)
    coordinator = TransactionCoordinator(
        repository,
        stager,
        WriteAheadLog(runtime / "wal"),
        ReceiptStore(runtime / "receipts"),
        lock_path=runtime / "writer.lock",
    )
    return root, repository, stager, coordinator, unrelated


def command(
    command_type: str,
    revision: int,
    payload: dict,
    *,
    request_id: str,
    actor_id: str = "pc_wei_tang",
) -> CommandEnvelope:
    return CommandEnvelope(
        campaign_id="planner-test",
        request_id=request_id,
        actor_id=actor_id,
        command_type=command_type,
        expected_revision=revision,
        submitted_at="2026-08-09T12:00:00Z",
        payload=payload,
        mode="gameplay",
    )


def execute(
    planner: RepositoryCommandPlanner,
    coordinator: TransactionCoordinator,
    envelope: CommandEnvelope,
):
    plan = planner.plan(envelope)
    return coordinator.execute(
        envelope,
        transaction_id=plan.transaction_id,
        created_at=plan.created_at,
        writes=plan.writes,
        result=plan.result,
        validator=plan.validator,
    )


def test_advance_time_is_deterministic_atomic_and_preserves_unrelated_bytes(
    tmp_path: Path,
) -> None:
    root, repository, stager, coordinator, unrelated = make_campaign(tmp_path)
    planner = RepositoryCommandPlanner(repository)
    envelope = command(
        "advance_time",
        1,
        {"target_time": "SE-0061-02-06T21:16:00"},
        request_id="advance-001",
    )
    first = planner.plan(envelope)
    second = planner.plan(envelope)
    assert first.transaction_id == second.transaction_id == "tx.gameplay." + envelope.digest
    assert first.created_at == second.created_at == envelope.submitted_at
    assert dict(first.writes) == dict(second.writes)
    assert set(first.writes) == {
        "state/meta.json",
        "state/scene.json",
        "state/time/causal-scheduler.json",
    }

    execution = execute(planner, coordinator, envelope)
    assert execution.status == "committed"
    assert repository.read_json("state/meta.json")["revision"] == 2
    assert repository.read_json("state/meta.json")["time"] == "SE-0061-02-06T21:16:00"
    assert repository.read_json("state/scene.json")["world_time"] == "SE-0061-02-06T21:16:00"
    scheduler = repository.read_json("state/time/causal-scheduler.json")
    assert scheduler["world_time"] == "SE-0061-02-06T21:16:00"
    assert scheduler["metrics"]["global_person_scans"] == 0
    assert repository.read_bytes("state/unrelated.json") == unrelated
    assert set(stager.get_commit(execution.commit_hash).paths) == set(first.writes)
    stager.assert_pristine()


def test_advance_time_commits_only_to_visible_boundary_without_choosing_for_player(
    tmp_path: Path,
) -> None:
    due = "SE-0061-02-06T21:16:00"
    event_id = "team_fujin_report"
    root, repository, stager, coordinator, unrelated = make_campaign(
        tmp_path,
        scene_boundary=(due, event_id),
    )
    original_scene = repository.read_json("state/scene.json")
    original_narrative = original_scene["narrative"]
    planner = RepositoryCommandPlanner(repository)
    envelope = command(
        "advance_time",
        1,
        {"target_time": "SE-0061-02-06T21:20:00"},
        request_id="advance-interrupt-001",
    )

    preview = planner.preview(envelope)
    assert preview.status == "ready"
    assert preview.code == "advance_time_interrupt_ready"
    plan = planner.plan(envelope)
    assert plan.result["world_time"] == due
    assert plan.result["requested_time"] == "SE-0061-02-06T21:20:00"
    assert plan.result["interrupted"] is True
    assert plan.result["interrupt_event_id"] == event_id

    execution = execute(planner, coordinator, envelope)
    assert execution.status == "committed"
    assert repository.read_json("state/meta.json")["time"] == due
    scene = repository.read_json("state/scene.json")
    assert scene["world_time"] == due
    assert scene["location_id"] == original_scene["location_id"]
    assert scene["narrative"] == original_narrative
    assert scene["time_passage_allowed"] is False
    assert scene["known_clock_boundaries"] == original_scene["known_clock_boundaries"]
    assert event_id in scene["decision_required"]
    assert "explicit player response" in scene["decision_required"]
    assert "no player response" in scene["scene_summary"]

    blocked = planner.preview(
        command(
            "advance_time",
            2,
            {"target_time": "SE-0061-02-06T21:17:00"},
            request_id="preview-past-unresolved-interrupt",
        )
    )
    assert blocked.status == "needs_clarification"
    assert blocked.code == "scene_boundary_requires_player_decision"
    with pytest.raises(
        CommandRejectedError,
        match="scene_boundary_requires_player_decision",
    ):
        planner.plan(
            command(
                "advance_time",
                2,
                {"target_time": "SE-0061-02-06T21:17:00"},
                request_id="advance-past-unresolved-interrupt",
            )
        )
    assert repository.current_revision() == 2
    assert repository.read_bytes("state/unrelated.json") == unrelated
    stager.assert_pristine()


def test_advance_time_settles_only_fact_free_canon_pressure_boundary(tmp_path: Path) -> None:
    root, repository, stager, coordinator, unrelated = make_campaign(
        tmp_path,
        canon_due="SE-0061-02-06T21:16:00",
    )
    planner = RepositoryCommandPlanner(repository)
    envelope = command(
        "advance_time",
        1,
        {"target_time": "SE-0061-02-06T21:17:00"},
        request_id="advance-front-001",
    )
    plan = planner.plan(envelope)
    assert "state/canon/pressures.json" in plan.writes
    assert plan.result["canon_pressure_reviews"] == (
        "pressure_test_front@SE-0061-02-06T21:16:00x1",
    )
    execute(planner, coordinator, envelope)
    pressure = repository.read_json("state/canon/pressures.json")["pressures"]["pressure_test_front"]
    assert pressure["chronology"] == []
    assert pressure["goal"] is None
    assert pressure["next_boundary"]["due_at"] == "SE-0061-02-13T21:16:00"


def test_advance_time_rejects_noop_stale_unknown_and_unsupported_boundary(
    tmp_path: Path,
) -> None:
    root, repository, stager, coordinator, unrelated = make_campaign(tmp_path)
    planner = RepositoryCommandPlanner(repository)
    with pytest.raises(CommandRejectedError, match="command_no_op"):
        planner.plan(
            command(
                "advance_time",
                1,
                {"target_time": CURRENT},
                request_id="noop",
            )
        )
    with pytest.raises(CommandRejectedError, match="advance_time_payload_fields_invalid"):
        planner.plan(
            command(
                "advance_time",
                1,
                {"target_time": "SE-0061-02-06T21:16:00", "path": "state/meta.json"},
                request_id="path-injection",
            )
        )
    with pytest.raises(StaleRevisionError):
        planner.plan(
            command(
                "advance_time",
                0,
                {"target_time": "SE-0061-02-06T21:16:00"},
                request_id="stale",
            )
        )
    boundary_command = command(
        "advance_time",
        1,
        {"target_time": "SE-0061-02-08T00:00:00"},
        request_id="causal-boundary",
    )
    preview = planner.preview(boundary_command)
    assert preview.status == "ready"
    assert preview.code == "advance_time_ready"
    plan = planner.plan(boundary_command)
    assert plan.result["world_time"] == "SE-0061-02-08T00:00:00"
    assert plan.result["processed_causal_events"] == ()
    assert plan.result["scheduler_metrics"]["global_person_scans"] == 0
    assert repository.current_revision() == 1
    stager.assert_pristine()


def test_mission_scene_rebuilds_only_current_mission_refs_and_keeps_context(
    tmp_path: Path,
) -> None:
    non_mission_refs = ["pc_wei_tang", "place.test", "team.test"]
    historical_missions = [f"mission.historical.{index:03d}" for index in range(200)]
    root, repository, stager, coordinator, unrelated = make_campaign(
        tmp_path,
        mission=offered_owner(),
        loaded_owner_ids=non_mission_refs + historical_missions,
    )
    planner = RepositoryCommandPlanner(repository)
    plan = planner.plan(
        command(
            "mission_transition",
            1,
            {"mission_id": "mission.test", "target_state": "accepted"},
            request_id="bounded-mission-scene",
        )
    )
    scene = json.loads(plan.writes["state/scene.json"])
    assert scene["loaded_owner_ids"] == sorted(non_mission_refs + ["mission.test"])
    assert len(
        [
            owner_id
            for owner_id in scene["loaded_owner_ids"]
            if owner_id.startswith("mission.")
        ]
    ) == 1
    assert repository.current_revision() == 1
    stager.assert_pristine()


def test_mission_scene_fails_closed_instead_of_dropping_non_mission_context(
    tmp_path: Path,
) -> None:
    non_mission_refs = [f"context.owner.{index:03d}" for index in range(64)]
    root, repository, stager, coordinator, unrelated = make_campaign(
        tmp_path,
        mission=offered_owner(),
        loaded_owner_ids=non_mission_refs + ["mission.historical"],
    )
    planner = RepositoryCommandPlanner(repository)
    with pytest.raises(
        CommandRejectedError,
        match="campaign_scene_context_budget_exceeded",
    ):
        planner.plan(
            command(
                "mission_transition",
                1,
                {"mission_id": "mission.test", "target_state": "accepted"},
                request_id="mission-scene-context-overflow",
            )
        )
    assert repository.read_json("state/scene.json")["loaded_owner_ids"] == (
        non_mission_refs + ["mission.historical"]
    )
    assert repository.current_revision() == 1
    stager.assert_pristine()


def test_complete_persisted_mission_lifecycle_uses_only_typed_existing_owner(
    tmp_path: Path,
) -> None:
    root, repository, stager, coordinator, unrelated = make_campaign(
        tmp_path,
        mission=offered_owner(),
    )
    planner = RepositoryCommandPlanner(repository)
    steps = (
        command(
            "mission_transition",
            1,
            {"mission_id": "mission.test", "target_state": "accepted"},
            request_id="mission-accept",
        ),
        command(
            "mission_transition",
            2,
            {"mission_id": "mission.test", "target_state": "active"},
            request_id="mission-activate",
        ),
        command(
            "mission_objective_update",
            3,
            {
                "mission_id": "mission.test",
                "objective_id": "obj.first",
                "target_status": "succeeded",
                "progress_milli": 1000,
                "evidence_event_id": "event.mission_test_obj_first_succeeded",
            },
            request_id="mission-objective",
        ),
        command(
            "mission_transition",
            4,
            {"mission_id": "mission.test", "target_state": "resolving"},
            request_id="mission-resolving",
        ),
        command(
            "mission_derive_and_settle",
            5,
            {"mission_id": "mission.test"},
            request_id="mission-settle",
        ),
    )
    for envelope in steps:
        execution = execute(planner, coordinator, envelope)
        assert execution.status == "committed"

    owner = MissionOwner.from_record(repository.read_json("state/mission/mission.test.json"))
    assert owner.mission.state == "succeeded"
    assert owner.mission.objective_by_id["obj.first"].resolution_ref == (
        "event.mission_test_obj_first_succeeded"
    )
    assert owner.closed_at == CampaignTime.parse(CURRENT)
    assert owner.next_due_at is None
    assert owner.mission.settlement is not None
    assert owner.mission.settlement.settlement_token == "settle." + steps[-1].digest
    assert repository.current_revision() == 6
    assert repository.read_bytes("state/unrelated.json") == unrelated
    assert "mission.test" in repository.read_json("state/scene.json")["loaded_owner_ids"]
    for commit_hash in git(root, "rev-list", "--max-count=5", "HEAD").splitlines():
        paths = set(stager.get_commit(commit_hash).paths)
        assert {
            "state/meta.json",
            "state/scene.json",
            "state/mission/mission.test.json",
            "state/reg/world-events.json",
        } <= paths
        assert all(
            path in {
                "state/meta.json",
                "state/scene.json",
                "state/mission/mission.test.json",
                "state/mission/context-index.json",
                "state/reg/world-events.json",
                "state/time/causal-scheduler.json",
            }
            or path.startswith("state/time/causal-scheduler/")
            for path in paths
        )


def test_terminal_mission_settlement_records_house_field_evidence_for_later_consolidation(
    tmp_path: Path,
) -> None:
    house_ref = "person.house_test"
    owner = offered_owner()
    owner = replace(
        owner,
        mission=replace(owner.mission, participant_refs=("pc_wei_tang", "team.test", house_ref)),
    )
    roster = {
        "profiles": {
            house_ref: {
                "institutional_progression": {
                    "development_residual_units": {},
                    "service_history": [],
                }
            }
        }
    }
    root, repository, stager, coordinator, unrelated = make_campaign(
        tmp_path, mission=owner, house_roster=roster
    )
    planner = RepositoryCommandPlanner(repository)
    steps = (
        command("mission_transition", 1, {"mission_id": "mission.test", "target_state": "accepted"}, request_id="house-mission-accept"),
        command("mission_transition", 2, {"mission_id": "mission.test", "target_state": "active"}, request_id="house-mission-active"),
        command(
            "mission_objective_update", 3,
            {
                "mission_id": "mission.test",
                "objective_id": "obj.first",
                "target_status": "succeeded",
                "progress_milli": 1000,
                "evidence_event_id": "event.mission_test_obj_first_succeeded",
            },
            request_id="house-mission-objective",
        ),
        command("mission_transition", 4, {"mission_id": "mission.test", "target_state": "resolving"}, request_id="house-mission-resolving"),
        command("mission_derive_and_settle", 5, {"mission_id": "mission.test"}, request_id="house-mission-settle"),
    )
    for envelope in steps:
        assert execute(planner, coordinator, envelope).status == "committed"
    profile = repository.read_json("state/person-core/house-tang.json")["profiles"][house_ref]
    institutional = profile["institutional_progression"]
    assert institutional["development_residual_units"]["field.mission_events"] == 2.0
    rows = [row for row in institutional["service_history"] if row.get("mission_ref") == "mission.test"]
    assert len(rows) == 1
    assert rows[0]["kind"] == "mission"
    assert rows[0]["domains"] == ["recon", "teamwork"]
    assert rows[0]["outcome"] == "succeeded"
    assert rows[0]["evidence_units"] == 2.0
    assert repository.read_bytes("state/unrelated.json") == unrelated
    stager.assert_pristine()


def test_mission_commands_fail_closed_on_creation_dependency_and_settle_funded_terms(
    tmp_path: Path,
) -> None:
    root, repository, stager, coordinator, unrelated = make_campaign(
        tmp_path,
        mission=offered_owner(with_terms=True),
    )
    planner = RepositoryCommandPlanner(repository)
    with pytest.raises(CommandRejectedError, match="mission_owner_not_found"):
        planner.plan(
            command(
                "mission_transition",
                1,
                {"mission_id": "mission.missing", "target_state": "accepted"},
                request_id="missing-mission",
            )
        )
    # Authorization is checked before mission membership, so a caller cannot use
    # mission-specific errors to probe participants in another player's campaign.
    with pytest.raises(CommandRejectedError, match="actor_not_campaign_player"):
        planner.plan(
            command(
                "mission_transition",
                1,
                {"mission_id": "mission.test", "target_state": "accepted"},
                request_id="wrong-actor",
                actor_id="pc_other",
            )
        )
    accepted = command(
        "mission_transition",
        1,
        {"mission_id": "mission.test", "target_state": "accepted"},
        request_id="accept-with-terms",
    )
    execute(planner, coordinator, accepted)
    active = command(
        "mission_transition",
        2,
        {"mission_id": "mission.test", "target_state": "active"},
        request_id="active-with-terms",
    )
    execute(planner, coordinator, active)
    objective = command(
        "mission_objective_update",
        3,
        {
            "mission_id": "mission.test",
            "objective_id": "obj.first",
            "target_status": "succeeded",
            "progress_milli": 1000,
            "evidence_event_id": "event.mission_test_obj_first_succeeded",
        },
        request_id="objective-with-terms",
    )
    execute(planner, coordinator, objective)
    resolving = command(
        "mission_transition",
        4,
        {"mission_id": "mission.test", "target_state": "resolving"},
        request_id="resolving-with-terms",
    )
    execute(planner, coordinator, resolving)
    settle = command(
        "mission_derive_and_settle",
        5,
        {"mission_id": "mission.test"},
        request_id="funded-settlement",
    )
    preview = planner.preview(settle)
    assert preview.status == "ready"
    assert preview.code == "mission_derive_and_settle_ready"
    execute(planner, coordinator, settle)
    owner = MissionOwner.from_record(repository.read_json("state/mission/mission.test.json"))
    assert owner.mission.state == "succeeded"
    inventory = repository.read_json("state/inventory/registry.json")
    assert inventory["holders"]["account.test"]["currency.ryo"] == 10
    assert "escrow.mission.test" not in inventory["holders"]
    assert repository.current_revision() == 6
    stager.assert_pristine()


def test_terminal_objective_requires_exact_persisted_causal_evidence(
    tmp_path: Path,
) -> None:
    root, repository, stager, coordinator, unrelated = make_campaign(
        tmp_path,
        mission=offered_owner(),
        include_objective_evidence=False,
    )
    planner = RepositoryCommandPlanner(repository)
    execute(
        planner,
        coordinator,
        command(
            "mission_transition",
            1,
            {"mission_id": "mission.test", "target_state": "accepted"},
            request_id="accept-without-evidence",
        ),
    )
    execute(
        planner,
        coordinator,
        command(
            "mission_transition",
            2,
            {"mission_id": "mission.test", "target_state": "active"},
            request_id="activate-without-evidence",
        ),
    )
    terminal = command(
        "mission_objective_update",
        3,
        {
            "mission_id": "mission.test",
            "objective_id": "obj.first",
            "target_status": "succeeded",
            "progress_milli": 1000,
            "evidence_event_id": "event.mission_test_obj_first_succeeded",
        },
        request_id="unsupported-objective-claim",
    )
    with pytest.raises(CommandRejectedError, match="mission_objective_evidence_required"):
        planner.plan(terminal)
    assert repository.current_revision() == 3
    owner = MissionOwner.from_record(repository.read_json("state/mission/mission.test.json"))
    assert owner.mission.objective_by_id["obj.first"].status == "pending"
    assert owner.mission.objective_by_id["obj.first"].resolution_ref is None
    assert repository.read_bytes("state/unrelated.json") == unrelated
    stager.assert_pristine()


def test_in_progress_objective_fails_closed_when_shape_cannot_preserve_evidence(
    tmp_path: Path,
) -> None:
    root, repository, stager, coordinator, unrelated = make_campaign(
        tmp_path,
        mission=offered_owner(),
    )
    planner = RepositoryCommandPlanner(repository)
    with pytest.raises(
        CommandRejectedError,
        match="mission_objective_progress_evidence_unsupported",
    ):
        planner.plan(
            command(
                "mission_objective_update",
                1,
                {
                    "mission_id": "mission.test",
                    "objective_id": "obj.first",
                    "target_status": "in_progress",
                    "progress_milli": 500,
                    "evidence_event_id": "event.mission_test_obj_first_succeeded",
                },
                request_id="unpersistable-progress-evidence",
            )
        )
    assert repository.current_revision() == 1
    stager.assert_pristine()


def test_only_exact_mission_authority_can_abort_shared_mission(tmp_path: Path) -> None:
    root, repository, stager, coordinator, unrelated = make_campaign(
        tmp_path / "unauthorized",
        mission=offered_owner(),
    )
    planner = RepositoryCommandPlanner(repository)
    execute(
        planner,
        coordinator,
        command(
            "mission_transition",
            1,
            {"mission_id": "mission.test", "target_state": "accepted"},
            request_id="accept-before-unauthorized-abort",
        ),
    )
    with pytest.raises(CommandRejectedError, match="actor_not_mission_authority"):
        planner.plan(
            command(
                "mission_transition",
                2,
                {"mission_id": "mission.test", "target_state": "aborted"},
                request_id="unauthorized-global-abort",
            )
        )
    assert repository.current_revision() == 2
    stager.assert_pristine()

    authorized_owner = replace(offered_owner(), authority_ref="pc_wei_tang")
    root, repository, stager, coordinator, unrelated = make_campaign(
        tmp_path / "authorized",
        mission=authorized_owner,
    )
    planner = RepositoryCommandPlanner(repository)
    execute(
        planner,
        coordinator,
        command(
            "mission_transition",
            1,
            {"mission_id": "mission.test", "target_state": "accepted"},
            request_id="accept-before-authorized-abort",
        ),
    )
    execution = execute(
        planner,
        coordinator,
        command(
            "mission_transition",
            2,
            {"mission_id": "mission.test", "target_state": "aborted"},
            request_id="authorized-global-abort",
        ),
    )
    assert execution.status == "committed"
    persisted = MissionOwner.from_record(
        repository.read_json("state/mission/mission.test.json")
    )
    assert persisted.mission.state == "aborted"
    assert repository.current_revision() == 3
    stager.assert_pristine()


def test_environment_app_uses_repository_planner_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from shinobi_runtime.api.app import create_app_from_env

    root, repository, stager, coordinator, unrelated = make_campaign(tmp_path)
    runtime = tmp_path / "service-runtime"
    monkeypatch.setenv("SHINOBI_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("SHINOBI_RUNTIME_ROOT", str(runtime))
    monkeypatch.setenv(
        "SHINOBI_API_TOKEN",
        "planner-test-token-with-at-least-thirty-two-characters",
    )
    client = TestClient(create_app_from_env())
    body = {
        "campaign_id": "planner-test",
        "request_id": "api-advance-001",
        "actor_id": "pc_wei_tang",
        "command_type": "advance_time",
        "expected_revision": 1,
        "submitted_at": "2026-08-09T12:00:00Z",
        "payload": {"target_time": "SE-0061-02-06T21:16:00"},
        "mode": "gameplay",
    }
    headers = {
        "Authorization": "Bearer planner-test-token-with-at-least-thirty-two-characters"
    }
    preview = client.post("/v1/commands/preview", headers=headers, json=body)
    assert preview.status_code == 200
    assert preview.json()["code"] == "advance_time_ready"
    response = client.post("/v1/commands/execute", headers=headers, json=body)
    assert response.status_code == 200
    assert response.json()["status"] == "committed"
    duplicate = client.post("/v1/commands/execute", headers=headers, json=body)
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"
    assert duplicate.json()["transaction_id"] == response.json()["transaction_id"]
    assert RepositoryStore(root).current_revision() == 2
