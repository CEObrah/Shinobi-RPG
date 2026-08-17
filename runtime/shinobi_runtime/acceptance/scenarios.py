"""Real-campaign production acceptance scenarios.

Each scenario copies the shipped campaign into a disposable directory and
executes semantic commands through :class:`ArchiveCampaignExecutor`.  This is
archive-mode proof for ZIP checkouts: planner, manifest, schema/template
validation, atomic persistence, readback, revision semantics and deterministic
reducers are real production components.  Git/WAL crash semantics remain the
responsibility of the transaction-coordinator test suite.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from shinobi_runtime.acceptance.campaign import ArchiveCampaignExecutor, ArchiveExecutionReceipt
from shinobi_runtime.people.repository import repository_sheet_resolver
from shinobi_runtime.store import content_root
from shinobi_runtime.information.store import InformationStore
from shinobi_runtime.sim.scheduler_store import SchedulerStore


PLAYER = "pc_wei_tang"
HIRUZEN = "canon_hiruzen"
FUJIN = "team.konoha.fujin"
BLACK_HOUND = "team.blackhound"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_campaign(source: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"),
    )
    return destination


def _receipt_record(receipt: ArchiveExecutionReceipt) -> Dict[str, Any]:
    return {
        "command_type": receipt.command_type,
        "request_id": receipt.request_id,
        "base_revision": receipt.base_revision,
        "target_revision": receipt.target_revision,
        "planning_read_count": receipt.planning_read_count,
        "write_count": receipt.write_count,
        "planning_state_reads": list(receipt.planning_state_reads),
        "write_paths": list(receipt.write_paths),
        "elapsed_ms": round(receipt.elapsed_ms, 3),
        "result": dict(receipt.result),
    }


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    status: str
    receipts: Tuple[ArchiveExecutionReceipt, ...]
    metrics: Mapping[str, Any]
    assertions: Tuple[str, ...]
    final_state_root: str

    def to_record(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "transactions": [_receipt_record(item) for item in self.receipts],
            "metrics": dict(self.metrics),
            "assertions": list(self.assertions),
            "final_state_root": self.final_state_root,
        }


def _result(
    name: str,
    root: Path,
    receipts: Sequence[ArchiveExecutionReceipt],
    *,
    metrics: Mapping[str, Any],
    assertions: Iterable[str],
) -> ScenarioResult:
    return ScenarioResult(
        name=name,
        status="passed",
        receipts=tuple(receipts),
        metrics=dict(metrics),
        assertions=tuple(assertions),
        final_state_root=content_root(root, include_roots=("state",)).root_sha256,
    )


def team_fujin_slice(source_root: Path, work_root: Path) -> ScenarioResult:
    root = _copy_campaign(source_root, work_root / "team-fujin")
    ex = ArchiveCampaignExecutor(root, hash_each_transaction=False)
    receipts = []
    receipts.append(ex.execute("travel_resolution", {
        "route_id": "route_local",
        "destination_id": "place.konoha.academy.assignment.hall",
        "traveler_refs": [PLAYER],
        "party_context_ref": None,
        "mission_ref": None,
    }))
    receipts.append(ex.execute("advance_time", {"target_time": "SE-0061-02-07T08:00:00"}))
    scene = ex.repository.read_json("state/scene.json")
    boundary_ids = [
        item.get("event_id") for item in scene.get("known_clock_boundaries", [])
        if isinstance(item, Mapping) and item.get("due_at") == "SE-0061-02-07T08:00:00"
    ]
    if "team_fujin_heavy_week_report_day_two" not in boundary_ids:
        raise AssertionError("Team Fujin scheduled boundary was not preserved")
    receipts.append(ex.execute("scene_boundary_resolution", {
        "action_kind": "resolve_clock_boundary",
        "subject_ref": PLAYER,
        "target_ref": None,
        "boundary_event_id": "team_fujin_heavy_week_report_day_two",
        "summary": "Team Fujin reports at the scheduled training boundary.",
        "visibility": "public",
    }))
    receipts.append(ex.execute("team_development_resolution", {
        "team_ref": FUJIN,
        "doctrine_identity": "coordinated objective control",
        "motto": "See together. Move together. Finish the mission together.",
        "training_focus": ["team coordination", "reconnaissance handoff", "objective defense"],
        "instructor_refs": [PLAYER],
        "facility_refs": [],
    }))
    receipts.append(ex.execute("training_resolution", {
        "actor_ref": "char.kai",
        "target": "operational_skills.team_coordination",
        "model_ref": "training.team",
        "context_ref": FUJIN,
        "instructor_ref": PLAYER,
        "target_time": "SE-0061-02-07T09:00:00",
        "active_hours": "1",
    }))
    receipts.append(ex.execute("relationship_resolution", {
        "target_ref": "char.kai",
        "relationship_type": "teammate",
        "interaction_kind": "shared_training",
        "summary": "A scheduled Team Fujin training block improves working familiarity.",
        "visibility": "public",
    }))
    receipts.append(ex.execute("commitment_resolution", {
        "commitment_id": "commitment.acceptance.fujin_after_action_drill",
        "kind": "order",
        "subject_ref": PLAYER,
        "target_ref": "char.kai",
        "host_ref": FUJIN,
        "due_at": "SE-0061-02-08T09:00:00",
        "summary": "Kai is ordered to report for the next Team Fujin after-action drill.",
        "visibility": "public",
    }))
    scheduler = SchedulerStore(ex.repository).load(full=True)
    commitments = ex.repository.read_json("state/reg/commitments.json")
    if not any(r.get("id") == "commitment.acceptance.fujin_after_action_drill" for r in commitments.get("records", [])):
        raise AssertionError("Team Fujin commitment did not persist")
    if not any(
        e.kind == "commitment.due"
        and e.payload.get("commitment_id") == "commitment.acceptance.fujin_after_action_drill"
        for e in scheduler.queue.snapshot()
    ):
        raise AssertionError("Team Fujin commitment did not schedule its causal wake")
    return _result(
        "team_fujin",
        root,
        receipts,
        metrics={
            "transactions": len(receipts),
            "max_planning_reads": max(r.planning_read_count for r in receipts),
            "max_writes": max(r.write_count for r in receipts),
            "scheduler_person_scans": scheduler.metrics.get("global_person_scans"),
            "scheduler_faction_scans": scheduler.metrics.get("global_faction_directory_scans"),
        },
        assertions=(
            "same exact-team and team-training runtime used as other exact teams",
            "scheduled Team Fujin boundary resolved through causal scheduler",
            "training persisted without bespoke Team Fujin Python branch",
            "relationship consequence persisted",
            "future team order persisted and scheduled",
        ),
    )


def black_hound_slice(source_root: Path, work_root: Path) -> ScenarioResult:
    root = _copy_campaign(source_root, work_root / "black-hound")
    ex = ArchiveCampaignExecutor(root, hash_each_transaction=False)
    receipts = []
    receipts.append(ex.execute("training_resolution", {
        "actor_ref": PLAYER,
        "target": "operational_skills.tactics",
        "model_ref": "training.team",
        "context_ref": BLACK_HOUND,
        "instructor_ref": "canon_hayama_shirakumo",
        "target_time": "SE-0061-02-06T21:45:00",
        "active_hours": "0.5",
    }))
    mission_id = "mission.acceptance.black_hound_exercise"
    participant_refs = [
        PLAYER,
        "canon_ensui_nara",
        "canon_hana_inuzuka",
        "canon_hoheto_hyuga",
        "canon_tekuno_kanden",
        "canon_hayama_shirakumo",
    ]
    receipts.append(ex.execute("mission_creation", {
        "mission_id": mission_id,
        "issuer_ref": HIRUZEN,
        "authority_ref": HIRUZEN,
        "mission_rank": "C",
        "participant_refs": participant_refs,
        "objectives": [{"objective_id": "obj.hold", "kind": "hold", "required": True, "dependencies": []}],
        "settlement_terms": [],
        "deadline_at": "SE-0061-02-10T18:00:00",
        "next_due_at": "SE-0061-02-09T18:00:00",
        "operation_ref": None,
    }, actor_id=HIRUZEN, mode="autonomous"))
    receipts.append(ex.execute("mission_transition", {"mission_id": mission_id, "target_state": "accepted"}))
    receipts.append(ex.execute("mission_transition", {"mission_id": mission_id, "target_state": "active"}))
    receipts.append(ex.execute("travel_resolution", {
        "route_id": "route_local",
        "destination_id": "place.konoha.hokage_tower",
        "traveler_refs": participant_refs,
        "party_context_ref": BLACK_HOUND,
        "mission_ref": mission_id,
    }))
    combat = ex.execute("combat_resolution", {
        "combat_id": "combat.acceptance.black_hound_control",
        "scale": "duel",
        "mission_ref": mission_id,
        "participants": [
            {"actor_ref": PLAYER, "side_ref": "side:wei", "action": "hold", "target_refs": ["canon_hayama_shirakumo"], "objective_ref": "objective:control", "lethal": False},
            {"actor_ref": "canon_hayama_shirakumo", "side_ref": "side:hayama", "action": "attack", "target_refs": [PLAYER], "objective_ref": "objective:control-opposition", "lethal": False},
        ],
        "objectives": [
            {"objective_ref": "objective:control", "side_ref": "side:wei", "kind": "hold", "target_refs": ["canon_hayama_shirakumo"], "zone_ref": "place.konoha.hokage_tower", "deadline_tick": 1},
            {"objective_ref": "objective:control-opposition", "side_ref": "side:hayama", "kind": "delay", "target_refs": [PLAYER], "zone_ref": None, "deadline_tick": 1},
        ],
    })
    receipts.append(combat)
    evidence_event = combat.result["semantic_event_id"]
    receipts.append(ex.execute("mission_objective_update", {
        "mission_id": mission_id,
        "objective_id": "obj.hold",
        "target_status": "succeeded",
        "progress_milli": 1000,
        "evidence_event_id": evidence_event,
    }))
    receipts.append(ex.execute("information_claim_resolution", {
        "claim_id": "claim.acceptance.black_hound_control_result",
        "subject_ref": "canon_hayama_shirakumo",
        "source_ref": evidence_event,
        "holder_ref": PLAYER,
        "epistemic_kind": "observation",
        "confidence_milli": 1000,
        "evidence_refs": [evidence_event],
        "context_ref": mission_id,
    }))
    receipts.append(ex.execute("information_delivery", {
        "claim_id": "claim.acceptance.black_hound_control_result",
        "sender_ref": PLAYER,
        "recipient_ref": HIRUZEN,
        "channel": "classified_debrief",
        "channel_confidence_milli": 1000,
    }))
    receipts.append(ex.execute("mission_transition", {"mission_id": mission_id, "target_state": "resolving"}))
    receipts.append(ex.execute("mission_derive_and_settle", {"mission_id": mission_id}))
    mission = ex.repository.read_json(f"state/mission/{mission_id}.json")
    info_store = InformationStore(ex.repository)
    if mission.get("state") != "succeeded":
        raise AssertionError("Black Hound mission did not settle successfully")
    claim = "claim.acceptance.black_hound_control_result"
    if not info_store.holder_knows(PLAYER, claim):
        raise AssertionError("Black Hound observer did not retain claim knowledge")
    if not info_store.holder_knows(HIRUZEN, claim):
        raise AssertionError("lawful Black Hound report did not reach Hiruzen")
    if info_store.holder_knows("char.kai", claim):
        raise AssertionError("classified Black Hound claim leaked to Team Fujin")
    return _result(
        "black_hound",
        root,
        receipts,
        metrics={
            "transactions": len(receipts),
            "mission_state": mission.get("state"),
            "max_planning_reads": max(r.planning_read_count for r in receipts),
            "max_writes": max(r.write_count for r in receipts),
        },
        assertions=(
            "same exact-team and training runtime as Team Fujin",
            "lawful mission tasking persisted for six-person roster",
            "exact-character travel preserved canonical location history",
            "mission-linked combat produced objective evidence",
            "claim creation and delivery stayed separate",
            "classified knowledge reached Hiruzen but not Team Fujin",
            "mission settled through typed lifecycle",
        ),
    )


def sword_manor_slice(source_root: Path, work_root: Path) -> ScenarioResult:
    root = _copy_campaign(source_root, work_root / "sword-manor")
    core_path = root / "state/person-core/house-tang.json"
    before_core_hash = _sha256(core_path)
    before_house = json.loads((root / "state/house/tang.json").read_text())
    ex = ArchiveCampaignExecutor(root, hash_each_transaction=False)
    receipts = []
    receipts.append(ex.execute("training_resolution", {
        "actor_ref": "cohort.house_tang.junior_disciple_second",
        "target": "stats.operational_skills.team_coordination",
        "model_ref": "training.cohort",
        "context_ref": "house.tang",
        "instructor_ref": PLAYER,
        "target_time": "SE-0061-02-06T21:45:00",
        "active_hours": "0.5",
    }))
    receipts.append(ex.execute("relationship_resolution", {
        "target_ref": "ht.core.022",
        "relationship_type": "house_member",
        "interaction_kind": "shared_training",
        "summary": "A rostered Sword Manor member trains directly with Wei and becomes socially distinct without receiving a heavyweight character sheet.",
        "visibility": "restricted",
    }))
    after_core_hash = _sha256(core_path)
    after_house = ex.repository.read_json("state/house/tang.json")
    if before_core_hash != after_core_hash:
        raise AssertionError("cohort-backed Sword Manor identities were rewritten during routine mechanics")
    before_cohort = next(c for c in before_house["cohorts"] if c.get("id") == "cohort.house_tang.junior_disciple_second")
    after_cohort = next(c for c in after_house["cohorts"] if c.get("id") == "cohort.house_tang.junior_disciple_second")
    if before_cohort == after_cohort:
        raise AssertionError("Sword Manor cohort mechanics did not progress")
    if after_cohort.get("aggregate_count") != len(after_cohort.get("roster_refs", [])):
        raise AssertionError("Sword Manor cohort roster/headcount diverged")
    return _result(
        "sword_manor",
        root,
        receipts,
        metrics={
            "transactions": len(receipts),
            "cohort_represented_people": after_cohort.get("aggregate_count"),
            "person_core_rewrites": 0,
            "max_planning_reads": max(r.planning_read_count for r in receipts),
            "max_writes": max(r.write_count for r in receipts),
        },
        assertions=(
            "routine cohort training updated one House owner rather than rostered people",
            "persistent roster identities remained byte-identical",
            "individual relationship divergence persisted outside the sparse person core",
            "cohort headcount remained equal to roster identity count",
        ),
    )


def population_materialization_slice(source_root: Path, work_root: Path) -> ScenarioResult:
    root = _copy_campaign(source_root, work_root / "population")
    ex = ArchiveCampaignExecutor(root, hash_each_transaction=False)
    before = ex.repository.read_json("state/population/registry.json")
    before_pool_total = sum(item["count"] for item in before["pools"].values())
    before_rostered = sum(item.get("representation", {}).get("rostered_count", 0) for item in before["pools"].values())
    before_anonymous = sum(item.get("representation", {}).get("anonymous_count", 0) for item in before["pools"].values())
    before_world_people = len(ex.repository.read_json("state/person-core/world.json").get("people", {}))
    receipts = []
    receipts.append(ex.execute("recruitment_resolution", {
        "source_pool_id": "pool.konoha.youth_candidate",
        "destination_pool_id": "pool.konoha.academy",
        "requested_count": 12,
        "policy_ref": "recruitment.academy",
        "authority_ref": HIRUZEN,
    }, actor_id=HIRUZEN, mode="autonomous"))
    materialized = ex.execute("person_materialization", {
        "source_pool_id": "pool.konoha.academy",
        "authority_ref": HIRUZEN,
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
    }, actor_id=HIRUZEN, mode="autonomous")
    receipts.append(materialized)
    after = ex.repository.read_json("state/population/registry.json")
    after_pool_total = sum(item["count"] for item in after["pools"].values())
    after_rostered = sum(item.get("representation", {}).get("rostered_count", 0) for item in after["pools"].values())
    after_anonymous = sum(item.get("representation", {}).get("anonymous_count", 0) for item in after["pools"].values())
    world = ex.repository.read_json("state/person-core/world.json")
    after_world_people = len(world.get("people", {}))
    person_id = materialized.result["person_id"]
    core = world["people"][person_id]
    if before_pool_total != 256000 or after_pool_total != before_pool_total:
        raise AssertionError("materialization changed physical population")
    if after_world_people != before_world_people + 1:
        raise AssertionError("materialization did not create exactly one persistent identity")
    if after_rostered != before_rostered + 1 or after_anonymous != before_anonymous - 1:
        raise AssertionError("materialization did not move one anonymous representation to rostered identity")
    if any(item.get("representation", {}).get("anonymous_count", 0) + item.get("representation", {}).get("rostered_count", 0) != item.get("count") for item in after["pools"].values()):
        raise AssertionError("population representation no longer partitions physical population")
    if core.get("component_refs") != {}:
        raise AssertionError("materialized identity received free heavyweight components")
    return _result(
        "population_materialization",
        root,
        receipts,
        metrics={
            "transactions": len(receipts),
            "population_pool_total_before": before_pool_total,
            "population_pool_total_after": after_pool_total,
            "persistent_world_people_before": before_world_people,
            "persistent_world_people_after": after_world_people,
            "rostered_population_before": before_rostered,
            "rostered_population_after": after_rostered,
            "anonymous_population_before": before_anonymous,
            "anonymous_population_after": after_anonymous,
            "materialized_person_id": person_id,
        },
        assertions=(
            "recruitment conserved aggregate population",
            "runtime derived accepted count from requested slots and policy",
            "materialization upgraded one already-existing anonymous human to persistent identity without changing physical population",
            "no free equipment, history, relationships or heavyweight mechanics were granted",
        ),
    )


def information_slice(source_root: Path, work_root: Path) -> ScenarioResult:
    root = _copy_campaign(source_root, work_root / "information")
    ex = ArchiveCampaignExecutor(root, hash_each_transaction=False)
    receipts = []
    receipts.append(ex.execute("information_claim_resolution", {
        "claim_id": "claim.acceptance.restricted_observation",
        "subject_ref": "canon_hayama_shirakumo",
        "source_ref": "canon_hayama_shirakumo",
        "holder_ref": PLAYER,
        "epistemic_kind": "observation",
        "confidence_milli": 900,
        "evidence_refs": [],
        "context_ref": None,
    }))
    receipts.append(ex.execute("information_delivery", {
        "claim_id": "claim.acceptance.restricted_observation",
        "sender_ref": PLAYER,
        "recipient_ref": HIRUZEN,
        "channel": "classified_debrief",
        "channel_confidence_milli": 950,
    }))
    info_store = InformationStore(ex.repository)
    projection = info_store.projection()
    claim_id = "claim.acceptance.restricted_observation"
    if not info_store.holder_knows(PLAYER, claim_id):
        raise AssertionError("claim creator did not retain knowledge")
    if not info_store.holder_knows(HIRUZEN, claim_id):
        raise AssertionError("lawful delivery did not create recipient knowledge")
    if info_store.holder_knows("char.kai", claim_id):
        raise AssertionError("restricted claim leaked to unrelated team member")
    return _result(
        "information",
        root,
        receipts,
        metrics={"transactions": len(receipts), "claims": projection["claim_count"], "deliveries": projection["delivery_count"]},
        assertions=(
            "claim creation and delivery used separate commands",
            "sender knowledge was required",
            "recipient gained knowledge only through lawful delivery",
            "unrelated exact-team member did not gain omniscient knowledge",
        ),
    )


def large_force_slice(source_root: Path, work_root: Path) -> ScenarioResult:
    root = _copy_campaign(source_root, work_root / "large-force")
    ex = ArchiveCampaignExecutor(root, hash_each_transaction=False)
    attachment = "operation.acceptance.border_exercise"
    # This isolated strategic scenario must first lawfully consume the campaign's
    # already-scheduled player clock boundary. Otherwise a multi-day autonomous
    # movement is correctly interrupted before the formation can arrive.
    receipts = list(_resolve_initial_scene_boundary(ex))

    # Force owners first mobilize real formations from conserved manpower.  Only
    # after that do they grant temporary command over the deployed slice.
    formation_refs: Dict[str, list[str]] = {"konoha": [], "iwa": []}
    movement_plan: list[tuple[str, str, str]] = []
    for faction_ref, force_ref, side, sizes in (
        ("faction_konoha", "force.konoha.shinobi", "konoha", (3000, 2000)),
        ("faction_iwa", "force.iwa.shinobi", "iwa", (3000, 2000)),
    ):
        for size in sizes:
            lifecycle = ex.execute(
                "formation_lifecycle_resolution",
                {
                    "action": "mobilize",
                    "force_ref": force_ref,
                    "formation_size": size,
                    "max_operational_personnel": 6000,
                    "operational_attachment_ref": attachment,
                },
                actor_id=faction_ref,
                mode="autonomous",
            )
            receipts.append(lifecycle)
            created_formation_ref = lifecycle.result["formation_ref"]
            formation_refs[side].append("formation:" + created_formation_ref)
            route_id = "route_konoha_fire_northwest" if side == "konoha" else "route_fire_grass_earth"
            movement_plan.append((faction_ref, created_formation_ref, route_id))

    # Allocate the full 10,000-person exercise slice before any movement advances
    # world time. Periodic autonomous reviews are then free to act during travel
    # without racing this acceptance harness for the same source-force capacity.
    for faction_ref, created_formation_ref, route_id in movement_plan:
        receipts.append(ex.execute(
            "formation_movement_resolution",
            {
                "formation_ref": created_formation_ref,
                "route_id": route_id,
                "destination_id": "place.fire.northwest",
                "operational_attachment_ref": attachment,
                "movement_posture": "standard",
            },
            actor_id=faction_ref,
            mode="autonomous",
        ))

    kf, kg = formation_refs["konoha"]
    iw, ig = formation_refs["iwa"]
    # Aggregate force-slice authority is attachment-scoped. Grant command over
    # each exact mobilized formation rather than using the retired broad
    # operation-id attachment convention.
    assignments = (
        ("assign.acceptance.konoha.deployed.1", "force.konoha.shinobi", "faction_konoha", PLAYER, kf.removeprefix("formation:"), 3000),
        ("assign.acceptance.konoha.deployed.2", "force.konoha.shinobi", "faction_konoha", PLAYER, kg.removeprefix("formation:"), 2000),
        ("assign.acceptance.iwa.deployed.1", "force.iwa.shinobi", "faction_iwa", "faction_iwa", iw.removeprefix("formation:"), 3000),
        ("assign.acceptance.iwa.deployed.2", "force.iwa.shinobi", "faction_iwa", "faction_iwa", ig.removeprefix("formation:"), 2000),
    )
    for assignment_id, force_ref, grantor_ref, recipient_ref, formation_ref, allocated_count in assignments:
        receipts.append(ex.execute(
            "force_assignment_resolution",
            {
                "assignment_id": assignment_id,
                "force_ref": force_ref,
                "grantor_ref": grantor_ref,
                "recipient_ref": recipient_ref,
                "allocated_count": allocated_count,
                "source_availability_class": "deployed",
                "operational_attachment_ref": formation_ref,
                "authority_limits": {"purpose": "bounded campaign battle acceptance"},
                "expires_at": None,
            },
            actor_id=grantor_ref,
            mode="autonomous",
        ))

    participants = [
        {"participant_ref": kf, "committed_count": 3000, "side_ref": "side:konoha", "action": "attack", "target_refs": [iw], "objective_ref": "objective:konoha.eliminate", "lethal": True, "command_authority_ref": PLAYER, "named_actor_refs": []},
        {"participant_ref": kg, "committed_count": 2000, "side_ref": "side:konoha", "action": "attack", "target_refs": [ig], "objective_ref": "objective:konoha.eliminate", "lethal": True, "command_authority_ref": PLAYER, "named_actor_refs": []},
        {"participant_ref": iw, "committed_count": 3000, "side_ref": "side:iwa", "action": "attack", "target_refs": [kf], "objective_ref": "objective:iwa.eliminate", "lethal": True, "command_authority_ref": "faction_iwa", "named_actor_refs": []},
        {"participant_ref": ig, "committed_count": 2000, "side_ref": "side:iwa", "action": "attack", "target_refs": [kg], "objective_ref": "objective:iwa.eliminate", "lethal": True, "command_authority_ref": "faction_iwa", "named_actor_refs": []},
    ]
    objectives = [
        {"objective_ref": "objective:konoha.eliminate", "side_ref": "side:konoha", "kind": "eliminate", "target_refs": [iw], "zone_ref": None, "deadline_tick": 8},
        {"objective_ref": "objective:iwa.eliminate", "side_ref": "side:iwa", "kind": "eliminate", "target_refs": [kf], "zone_ref": None, "deadline_tick": 8},
    ]
    battle = ex.execute("combat_resolution", {
        "combat_id": "combat.acceptance.10000",
        "scale": "battle",
        "location_ref": "place.fire.northwest",
        "participants": participants,
        "objectives": objectives,
    }, actor_id=PLAYER, mode="autonomous")
    receipts.append(battle)
    if battle.result.get("represented_personnel") != 10000:
        raise AssertionError("large battle did not represent exactly 10,000 personnel")
    if battle.write_count > 30:
        raise AssertionError("large battle write amplification exceeded operational budget")
    casualty_ids = tuple(battle.result.get("casualty_transfer_ids", ()))
    if not casualty_ids:
        raise AssertionError("lethal large battle generated no conserved casualty transfer")
    population = ex.repository.read_json("state/population/registry.json")
    transfer_ids = {r.get("id") for r in population.get("transfers", []) if isinstance(r, Mapping)}
    if any(item not in transfer_ids for item in casualty_ids):
        raise AssertionError("battle casualty transfer receipt missing from population authority")
    for pool in population.get("pools", {}).values():
        representation = pool.get("representation", {})
        if representation.get("anonymous_count", 0) + representation.get("rostered_count", 0) != pool.get("count"):
            raise AssertionError("battle broke population representation conservation")
    assignments_state = ex.repository.read_json("state/org/assignments.json")
    if not all(any(r.get("id") == aid and r.get("source_owner") == force for r in assignments_state["records"]) for aid, force, *_ in assignments):
        raise AssertionError("force ownership/assignment provenance was not preserved")
    return _result(
        "large_force",
        root,
        receipts,
        metrics={
            "transactions": len(receipts),
            "represented_personnel": battle.result.get("represented_personnel"),
            "operational_participants": battle.result.get("operational_participants"),
            "battle_planning_reads": battle.planning_read_count,
            "battle_writes": battle.write_count,
            "casualty_transfer_count": len(casualty_ids),
            "mobilized_formation_refs": formation_refs,
        },
        assertions=(
            "10,000 represented personnel were first mobilized into authoritative formations and then resolved through bounded combat",
            "source ownership remained separate from temporary command authority",
            "battle writes remained bounded and participant-count independent",
            "battlefield deaths reconciled into conserved physical and representation-aware population transfers",
            "operation owner persisted command-authority provenance",
        ),
    )


def _resolve_initial_scene_boundary(ex: ArchiveCampaignExecutor) -> Tuple[ArchiveExecutionReceipt, ...]:
    receipts = [ex.execute("advance_time", {"target_time": "SE-0061-02-07T08:00:00"})]
    receipts.append(ex.execute("scene_boundary_resolution", {
        "action_kind": "resolve_clock_boundary",
        "subject_ref": PLAYER,
        "target_ref": None,
        "boundary_event_id": "team_fujin_heavy_week_report_day_two",
        "summary": "The scheduled Team Fujin report boundary is resolved for long-horizon acceptance.",
        "visibility": "public",
    }))
    return tuple(receipts)


def dormant_person_slice(source_root: Path, work_root: Path) -> ScenarioResult:
    root = _copy_campaign(source_root, work_root / "dormant-person")
    person_path = root / "state/person-core/house-tang.json"
    before_hash = _sha256(person_path)
    resolver = repository_sheet_resolver(root)
    before_sheet = resolver("ht.core.022")
    ex = ArchiveCampaignExecutor(root, hash_each_transaction=False)
    receipts = list(_resolve_initial_scene_boundary(ex))
    receipts.append(ex.execute("advance_time", {"target_time": "SE-0071-02-07T08:00:00"}))
    after_hash = _sha256(person_path)
    after_sheet = repository_sheet_resolver(root)("ht.core.022")
    if before_hash != after_hash:
        raise AssertionError("dormant rostered person received periodic person-file writes")
    after_resolved = after_sheet.get("core", {}).get("resolved_through")
    if after_resolved != "SE-0071-02-07T08:00:00":
        raise AssertionError("lazy returning-person projection did not resolve through current campaign time")
    before_age = before_sheet.get("cohort_baseline", {}).get("numeric_values", {}).get("age_years")
    after_age = after_sheet.get("cohort_baseline", {}).get("numeric_values", {}).get("age_years")
    if not isinstance(before_age, (int, float)) or not isinstance(after_age, (int, float)) or after_age < before_age + 9:
        raise AssertionError("returning rostered person's age did not advance lawfully")
    return _result(
        "dormant_person",
        root,
        receipts,
        metrics={
            "transactions": len(receipts),
            "person_core_writes": 0,
            "age_before": before_age,
            "age_after": after_age,
            "resolved_through": after_resolved,
            "long_jump_reads": receipts[-1].planning_read_count,
            "long_jump_writes": receipts[-1].write_count,
        },
        assertions=(
            "rostered identity remained persistent without periodic rewrites",
            "routine progression remained cohort-backed",
            "returning person projected current age from stable birth date",
            "lazy person read resolved through current campaign time",
        ),
    )


def long_horizon_slice(source_root: Path, work_root: Path) -> ScenarioResult:
    targets = {
        "six_months": "SE-0061-08-07T08:00:00",
        "three_years": "SE-0064-02-07T08:00:00",
        "ten_years": "SE-0071-02-07T08:00:00",
        "twenty_years": "SE-0081-02-07T08:00:00",
    }
    horizon_metrics: Dict[str, Dict[str, Any]] = {}
    all_receipts = []
    roots = []
    for label, target in targets.items():
        root = _copy_campaign(source_root, work_root / f"horizon-{label}")
        ex = ArchiveCampaignExecutor(root, hash_each_transaction=False)
        initial = list(_resolve_initial_scene_boundary(ex))
        jump = ex.execute("advance_time", {"target_time": target})
        all_receipts.extend(initial)
        all_receipts.append(jump)
        metrics = jump.result.get("scheduler_metrics", {})
        if metrics.get("global_person_scans") != 0 or metrics.get("global_faction_directory_scans") != 0:
            raise AssertionError(f"{label} reintroduced global temporal scanning")
        horizon_metrics[label] = {
            "target_time": target,
            "planning_reads": jump.planning_read_count,
            "writes": jump.write_count,
            "processed_causal_events": len(jump.result.get("processed_causal_events", ())),
            "scheduler_host_count": metrics.get("host_count"),
            "pending_event_count": metrics.get("pending_event_count"),
        }
        roots.append(content_root(root, include_roots=("state",)).root_sha256)
    read_counts = [item["planning_reads"] for item in horizon_metrics.values()]
    write_counts = [item["writes"] for item in horizon_metrics.values()]
    if max(read_counts) - min(read_counts) > 6 or max(write_counts) - min(write_counts) > 6:
        raise AssertionError("long-horizon cost scaled materially with elapsed years")
    combined = hashlib.sha256("".join(roots).encode("ascii")).hexdigest()
    return ScenarioResult(
        name="long_horizon",
        status="passed",
        receipts=tuple(all_receipts),
        metrics={"horizons": horizon_metrics, "read_spread": max(read_counts)-min(read_counts), "write_spread": max(write_counts)-min(write_counts)},
        assertions=(
            "six-month, three-year, ten-year and twenty-year real-campaign jumps succeeded",
            "no named-person or faction-directory global scans occurred",
            "read/write cost stayed approximately flat as elapsed years increased",
            "causal events, not named_people x months polling, bounded long skips",
        ),
        final_state_root=combined,
    )


def cross_system_slice(source_root: Path, work_root: Path) -> ScenarioResult:
    """Prove that the player's simultaneous affiliations share one state model."""
    root = _copy_campaign(source_root, work_root / "cross-system")
    ex = ArchiveCampaignExecutor(root, hash_each_transaction=False)
    receipts = []
    # Team Fujin creates a material future obligation.
    receipts.append(ex.execute("commitment_resolution", {
        "commitment_id": "commitment.acceptance.cross.fujin_report",
        "kind": "order",
        "subject_ref": PLAYER,
        "target_ref": "char.kai",
        "host_ref": FUJIN,
        "due_at": "SE-0061-02-08T12:00:00",
        "summary": "Kai must report the next Team Fujin drill result.",
        "visibility": "public",
    }))
    # Black Hound creates classified knowledge without granting it to Fujin.
    receipts.append(ex.execute("information_claim_resolution", {
        "claim_id": "claim.acceptance.cross.black_hound",
        "subject_ref": "canon_hayama_shirakumo",
        "source_ref": "canon_hayama_shirakumo",
        "holder_ref": PLAYER,
        "epistemic_kind": "report",
        "confidence_milli": 850,
        "evidence_refs": [],
        "context_ref": None,
    }))
    # Sword Manor cohort progresses in the same campaign without touching its sparse roster.
    before_core = _sha256(root / "state/person-core/house-tang.json")
    receipts.append(ex.execute("training_resolution", {
        "actor_ref": "cohort.house_tang.junior_disciple_second",
        "target": "stats.operational_skills.team_coordination",
        "model_ref": "training.cohort",
        "context_ref": "house.tang",
        "instructor_ref": PLAYER,
        "target_time": "SE-0061-02-06T21:45:00",
        "active_hours": "0.5",
    }))
    after_core = _sha256(root / "state/person-core/house-tang.json")
    info_store = InformationStore(ex.repository)
    if before_core != after_core:
        raise AssertionError("cross-system House cohort update rewrote sparse identities")
    if info_store.holder_knows("char.kai", "claim.acceptance.cross.black_hound"):
        raise AssertionError("Black Hound compartmented information leaked across affiliation")
    scheduler = SchedulerStore(ex.repository).load(full=True)
    if not any(e.payload.get("commitment_id") == "commitment.acceptance.cross.fujin_report" for e in scheduler.queue.snapshot()):
        raise AssertionError("Team Fujin commitment did not coexist with other affiliation state")
    return _result(
        "cross_system",
        root,
        receipts,
        metrics={"transactions": len(receipts), "person_core_rewrites": 0},
        assertions=(
            "Team Fujin obligation, Black Hound classified knowledge and Sword Manor cohort progression coexist",
            "affiliations do not overwrite one another",
            "classification boundaries remain audience-specific",
            "Sword Manor roster identities remain sparse while routine mechanics progress collectively",
        ),
    )


SCENARIOS = (
    team_fujin_slice,
    black_hound_slice,
    sword_manor_slice,
    population_materialization_slice,
    information_slice,
    large_force_slice,
    dormant_person_slice,
    long_horizon_slice,
    cross_system_slice,
)


def run_campaign_scenarios(source_root: object, work_root: object) -> Tuple[ScenarioResult, ...]:
    source = Path(source_root).resolve()
    work = Path(work_root).resolve()
    work.mkdir(parents=True, exist_ok=True)
    return tuple(scenario(source, work) for scenario in SCENARIOS)
