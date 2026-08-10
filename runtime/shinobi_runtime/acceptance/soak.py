"""Mixed real-campaign production soak for archive checkouts.

The soak deliberately exercises the production command planner, manifest
planner, registered validators and atomic persister against disposable copies
of the shipped campaign.  It is not a Git/WAL replacement; transaction crash
semantics remain covered by the transaction-coordinator suite.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
from statistics import mean
from time import perf_counter
from typing import Any, Dict, Iterable, Mapping, Sequence

from shinobi_runtime.acceptance.campaign import ArchiveCampaignExecutor
from shinobi_runtime.sim import CampaignTime
from shinobi_runtime.store import content_root


PLAYER = "pc_wei_tang"
HIRUZEN = "canon_hiruzen"
HAYAMA = "canon_hayama_shirakumo"
KAI = "char.kai"
FUJIN = "team.konoha.fujin"
BLACK_HOUND = "team.blackhound"


def _copy_campaign(source: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"),
    )
    return destination


def _percentile(values: Sequence[int | float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def _world_event_count(root: Path) -> int:
    path = root / "state/reg/world-events.json"
    if not path.exists():
        return 0
    data = json.loads(path.read_text())
    events = data.get("events", []) if isinstance(data, Mapping) else []
    archived = data.get("archived_event_count", 0) if isinstance(data, Mapping) else 0
    if isinstance(archived, bool) or not isinstance(archived, int):
        archived = 0
    return archived + (len(events) if isinstance(events, list) else 0)


def _scheduler_metrics(root: Path) -> Dict[str, int]:
    data = json.loads((root / "state/time/causal-scheduler.json").read_text())
    hosts = data.get("hosts", {}) if isinstance(data, Mapping) else {}
    events = data.get("events", []) if isinstance(data, Mapping) else []
    metrics = data.get("metrics", {}) if isinstance(data, Mapping) else {}
    return {
        "host_count": len(hosts) if isinstance(hosts, Mapping) else 0,
        "pending_event_count": len(events) if isinstance(events, list) else 0,
        "global_person_scans": int(metrics.get("global_person_scans", 0)) if isinstance(metrics, Mapping) else 0,
        "global_faction_directory_scans": int(metrics.get("global_faction_directory_scans", 0)) if isinstance(metrics, Mapping) else 0,
    }


def _campaign_time(executor: ArchiveCampaignExecutor) -> CampaignTime:
    meta = executor.repository.read_json("state/meta.json")
    return CampaignTime.parse(meta["time"])


def _execute(
    executor: ArchiveCampaignExecutor,
    records: list,
    command_type: str,
    payload: Mapping[str, Any],
    *,
    actor_id: str | None = None,
    mode: str = "gameplay",
) -> Mapping[str, Any]:
    receipt = executor.execute(command_type, payload, actor_id=actor_id, mode=mode)
    records.append(receipt)
    return receipt.result


def _base_pair(executor: ArchiveCampaignExecutor, records: list, serial: int) -> None:
    _execute(executor, records, "relationship_resolution", {
        "target_ref": KAI if serial % 2 == 0 else HAYAMA,
        "relationship_type": "teammate",
        "interaction_kind": "shared_training",
        "summary": f"Routine mixed-soak interaction {serial} reinforces working familiarity.",
        "visibility": "restricted",
    })
    claim_id = f"claim.soak.{serial:04d}"
    _execute(executor, records, "information_claim_resolution", {
        "claim_id": claim_id,
        "subject_ref": HAYAMA,
        "source_ref": HAYAMA,
        "holder_ref": PLAYER,
        "epistemic_kind": "observation",
        "confidence_milli": 900,
        "evidence_refs": [],
        "context_ref": None,
    })
    _execute(executor, records, "information_delivery", {
        "claim_id": claim_id,
        "sender_ref": PLAYER,
        "recipient_ref": HIRUZEN,
        "channel": "direct_report",
        "channel_confidence_milli": 950,
    })
    commitment_id = f"commitment.soak.{serial:04d}"
    _execute(executor, records, "commitment_resolution", {
        "commitment_id": commitment_id,
        "kind": "promise",
        "subject_ref": PLAYER,
        "target_ref": KAI,
        "host_ref": FUJIN,
        "due_at": None,
        "summary": f"Routine mixed-soak promise {serial} is recorded explicitly.",
        "visibility": "restricted",
    })
    _execute(executor, records, "commitment_transition", {
        "commitment_id": commitment_id,
        "target_status": "completed",
        "summary": f"Routine mixed-soak promise {serial} is fulfilled.",
    })


def _training(executor: ArchiveCampaignExecutor, records: list, serial: int) -> None:
    now = _campaign_time(executor)
    target = now.add_seconds(120)
    _execute(executor, records, "training_resolution", {
        "actor_ref": PLAYER,
        "target": "operational_skills.tactics",
        "model_ref": "training.self_directed",
        "context_ref": None,
        "instructor_ref": None,
        "target_time": str(target),
        "active_hours": "0.0333333333",
    })


def _recruit_and_materialize(executor: ArchiveCampaignExecutor, records: list, serial: int) -> None:
    _execute(executor, records, "recruitment_resolution", {
        "source_pool_id": "pool.konoha.youth_candidate",
        "destination_pool_id": "pool.konoha.academy",
        "requested_count": 1,
        "policy_ref": "recruitment.academy",
        "authority_ref": HIRUZEN,
    }, actor_id=HIRUZEN, mode="autonomous")
    _execute(executor, records, "person_materialization", {
        "source_pool_id": "pool.konoha.academy",
        "authority_ref": HIRUZEN,
        "name": f"Soak Candidate {serial:03d}",
        "aliases": [],
        "pronouns": "they/them",
        "birth_date": "SE-0049-01-12",
        "origin": "Konohagakure / Land of Fire",
        "location_ref": "place.konoha",
        "role_profile_ref": "role.population.rostered",
        "identity_cues": {
            "appearance": "A rostered Academy-age candidate represented sparsely for acceptance testing.",
            "temperament": "No individualized temperament has yet been established.",
            "doctrine_expression": "No individualized doctrine has yet been established.",
        },
    }, actor_id=HIRUZEN, mode="autonomous")


def _mission_package(executor: ArchiveCampaignExecutor, records: list, serial: int) -> None:
    now = _campaign_time(executor)
    mission_id = f"mission.soak.{serial:03d}"
    objective_id = "obj.control"
    _execute(executor, records, "mission_creation", {
        "mission_id": mission_id,
        "issuer_ref": HIRUZEN,
        "authority_ref": HIRUZEN,
        "mission_rank": "D",
        "participant_refs": [PLAYER, HAYAMA],
        "objectives": [{"objective_id": objective_id, "kind": "hold", "required": True, "dependencies": []}],
        "settlement_terms": [],
        "deadline_at": str(now.add_seconds(30 * 86400)),
        "next_due_at": str(now.add_seconds(15 * 86400)),
        "operation_ref": None,
    }, actor_id=HIRUZEN, mode="autonomous")
    _execute(executor, records, "mission_transition", {"mission_id": mission_id, "target_state": "accepted"})
    _execute(executor, records, "mission_transition", {"mission_id": mission_id, "target_state": "active"})
    combat = _execute(executor, records, "combat_resolution", {
        "combat_id": f"combat.soak.{serial:03d}",
        "scale": "duel",
        "mission_ref": mission_id,
        "participants": [
            {"actor_ref": PLAYER, "side_ref": "side:wei", "action": "hold", "target_refs": [HAYAMA], "objective_ref": "objective:control", "lethal": False},
            {"actor_ref": HAYAMA, "side_ref": "side:hayama", "action": "delay", "target_refs": [PLAYER], "objective_ref": "objective:delay", "lethal": False},
        ],
        "objectives": [
            {"objective_ref": "objective:control", "side_ref": "side:wei", "kind": "hold", "target_refs": [HAYAMA], "zone_ref": "place.sword_manor", "deadline_tick": 1},
            {"objective_ref": "objective:delay", "side_ref": "side:hayama", "kind": "delay", "target_refs": [PLAYER], "zone_ref": None, "deadline_tick": 1},
        ],
    })
    _execute(executor, records, "mission_objective_update", {
        "mission_id": mission_id,
        "objective_id": objective_id,
        "target_status": "succeeded",
        "progress_milli": 1000,
        "evidence_event_id": combat["semantic_event_id"],
    })
    _execute(executor, records, "mission_transition", {"mission_id": mission_id, "target_state": "resolving"})
    _execute(executor, records, "mission_derive_and_settle", {"mission_id": mission_id})


def _travel_pair(executor: ArchiveCampaignExecutor, records: list) -> None:
    _execute(executor, records, "travel_resolution", {
        "route_id": "route_local",
        "destination_id": "place.konoha.academy.assignment.hall",
        "traveler_refs": [PLAYER],
        "party_context_ref": None,
        "mission_ref": None,
    })
    _execute(executor, records, "travel_resolution", {
        "route_id": "route_local",
        "destination_id": "place.sword_manor",
        "traveler_refs": [PLAYER],
        "party_context_ref": None,
        "mission_ref": None,
    })


def _prelude(executor: ArchiveCampaignExecutor, records: list) -> None:
    # Ten transactions.  This deliberately crosses and resolves the shipped
    # Team Fujin clock boundary so the soak includes actual causal time/event
    # processing rather than only same-timestamp bookkeeping.
    _execute(executor, records, "travel_resolution", {
        "route_id": "route_local",
        "destination_id": "place.konoha.academy.assignment.hall",
        "traveler_refs": [PLAYER],
        "party_context_ref": None,
        "mission_ref": None,
    })
    _execute(executor, records, "advance_time", {"target_time": "SE-0061-02-07T08:00:00"})
    _execute(executor, records, "scene_boundary_resolution", {
        "action_kind": "resolve_clock_boundary",
        "subject_ref": PLAYER,
        "target_ref": None,
        "boundary_event_id": "team_fujin_heavy_week_report_day_two",
        "summary": "Team Fujin reports at the scheduled boundary during mixed campaign soak.",
        "visibility": "public",
    })
    _execute(executor, records, "training_resolution", {
        "actor_ref": KAI,
        "target": "operational_skills.team_coordination",
        "model_ref": "training.team",
        "context_ref": FUJIN,
        "instructor_ref": PLAYER,
        "target_time": "SE-0061-02-07T08:30:00",
        "active_hours": "0.5",
    })
    _execute(executor, records, "relationship_resolution", {
        "target_ref": KAI,
        "relationship_type": "teammate",
        "interaction_kind": "shared_training",
        "summary": "Team Fujin completes the opening mixed-soak training block.",
        "visibility": "public",
    })
    claim_id = "claim.soak.prelude"
    _execute(executor, records, "information_claim_resolution", {
        "claim_id": claim_id,
        "subject_ref": KAI,
        "source_ref": KAI,
        "holder_ref": PLAYER,
        "epistemic_kind": "observation",
        "confidence_milli": 1000,
        "evidence_refs": [],
        "context_ref": None,
    })
    _execute(executor, records, "information_delivery", {
        "claim_id": claim_id,
        "sender_ref": PLAYER,
        "recipient_ref": HIRUZEN,
        "channel": "routine_team_report",
        "channel_confidence_milli": 950,
    })
    commitment_id = "commitment.soak.prelude"
    _execute(executor, records, "commitment_resolution", {
        "commitment_id": commitment_id,
        "kind": "promise",
        "subject_ref": PLAYER,
        "target_ref": KAI,
        "host_ref": FUJIN,
        "due_at": None,
        "summary": "Wei records a routine Team Fujin follow-up promise during the soak.",
        "visibility": "public",
    })
    _execute(executor, records, "commitment_transition", {
        "commitment_id": commitment_id,
        "target_status": "completed",
        "summary": "The Team Fujin follow-up promise is completed during the soak.",
    })
    _execute(executor, records, "travel_resolution", {
        "route_id": "route_local",
        "destination_id": "place.sword_manor",
        "traveler_refs": [PLAYER],
        "party_context_ref": None,
        "mission_ref": None,
    })


@dataclass(frozen=True)
class SoakRunResult:
    name: str
    transactions: int
    final_revision: int
    final_time: str
    final_state_root: str
    command_counts: Mapping[str, int]
    planning_reads: Mapping[str, float]
    writes: Mapping[str, float]
    latency_ms: Mapping[str, float]
    world_events_before: int
    world_events_after: int
    state_files_before: int
    state_files_after: int
    scheduler_before: Mapping[str, int]
    scheduler_after: Mapping[str, int]
    elapsed_ms: float

    def to_record(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "transactions": self.transactions,
            "final_revision": self.final_revision,
            "final_time": self.final_time,
            "final_state_root": self.final_state_root,
            "command_counts": dict(self.command_counts),
            "planning_reads": dict(self.planning_reads),
            "writes": dict(self.writes),
            "latency_ms": dict(self.latency_ms),
            "world_events_before": self.world_events_before,
            "world_events_after": self.world_events_after,
            "history_growth": self.world_events_after - self.world_events_before,
            "state_files_before": self.state_files_before,
            "state_files_after": self.state_files_after,
            "scheduler_before": dict(self.scheduler_before),
            "scheduler_after": dict(self.scheduler_after),
            "elapsed_ms": round(self.elapsed_ms, 3),
        }


def run_mixed_soak(source_root: Path, work_root: Path, *, name: str) -> SoakRunResult:
    root = _copy_campaign(source_root, work_root / name)
    ex = ArchiveCampaignExecutor(root, hash_each_transaction=False, validate_contracts=False)
    records = []
    before_events = _world_event_count(root)
    before_files = sum(1 for p in (root / "state").rglob("*") if p.is_file())
    before_scheduler = _scheduler_metrics(root)
    initial_meta = ex.repository.read_json("state/meta.json")
    initial_revision = int(initial_meta["revision"])
    started = perf_counter()

    _prelude(ex, records)
    # 99 rounds x 10 = 990 plus 10 prelude = exactly 1,000 successful
    # production semantic transactions.
    for round_index in range(1, 100):
        serial = round_index
        if round_index % 20 == 0:
            _travel_pair(ex, records)  # 2
            _base_pair(ex, records, serial * 10)  # 5
            # three additional relationship consequences = 10 total.
            for extra in range(3):
                _execute(ex, records, "relationship_resolution", {
                    "target_ref": HAYAMA if extra % 2 == 0 else KAI,
                    "relationship_type": "teammate",
                    "interaction_kind": "shared_training",
                    "summary": f"Travel-round interaction {serial}.{extra} is persisted.",
                    "visibility": "restricted",
                })
        elif round_index % 10 == 0:
            _mission_package(ex, records, serial)  # 7
            _training(ex, records, serial)  # 1
            claim_id = f"claim.soak.mission.{serial:03d}"
            _execute(ex, records, "information_claim_resolution", {
                "claim_id": claim_id,
                "subject_ref": HAYAMA,
                "source_ref": HAYAMA,
                "holder_ref": PLAYER,
                "epistemic_kind": "observation",
                "confidence_milli": 900,
                "evidence_refs": [],
                "context_ref": None,
            })
            _execute(ex, records, "information_delivery", {
                "claim_id": claim_id,
                "sender_ref": PLAYER,
                "recipient_ref": HIRUZEN,
                "channel": "mission_report",
                "channel_confidence_milli": 950,
            })
        elif round_index % 5 == 0:
            _recruit_and_materialize(ex, records, serial)  # 2
            _training(ex, records, serial)  # 1
            _base_pair(ex, records, serial * 10)  # 5
            for extra in range(2):
                _execute(ex, records, "relationship_resolution", {
                    "target_ref": KAI,
                    "relationship_type": "teammate",
                    "interaction_kind": "shared_training",
                    "summary": f"Population-round interaction {serial}.{extra} is persisted.",
                    "visibility": "restricted",
                })
        else:
            _base_pair(ex, records, serial * 10)  # 5
            _base_pair(ex, records, serial * 10 + 1)  # 5

    if len(records) != 1000:
        raise AssertionError(f"mixed soak produced {len(records)} transactions instead of 1000")

    meta = ex.repository.read_json("state/meta.json")
    after_events = _world_event_count(root)
    after_files = sum(1 for p in (root / "state").rglob("*") if p.is_file())
    after_scheduler = _scheduler_metrics(root)
    if after_scheduler["global_person_scans"] != 0 or after_scheduler["global_faction_directory_scans"] != 0:
        raise AssertionError("mixed soak reintroduced global scheduler scans")
    if int(meta["revision"]) != initial_revision + 1000:
        raise AssertionError("mixed soak revision count is not exactly initial revision + 1000")

    reads = [r.planning_read_count for r in records]
    writes = [r.write_count for r in records]
    latencies = [r.elapsed_ms for r in records]
    counts = Counter(r.command_type for r in records)
    return SoakRunResult(
        name=name,
        transactions=len(records),
        final_revision=int(meta["revision"]),
        final_time=str(meta["time"]),
        final_state_root=content_root(root, include_roots=("state",)).root_sha256,
        command_counts=dict(sorted(counts.items())),
        planning_reads={"mean": round(mean(reads), 3), "p95": round(_percentile(reads, .95), 3), "max": max(reads)},
        writes={"mean": round(mean(writes), 3), "p95": round(_percentile(writes, .95), 3), "max": max(writes)},
        latency_ms={"mean": round(mean(latencies), 3), "p95": round(_percentile(latencies, .95), 3), "max": round(max(latencies), 3)},
        world_events_before=before_events,
        world_events_after=after_events,
        state_files_before=before_files,
        state_files_after=after_files,
        scheduler_before=before_scheduler,
        scheduler_after=after_scheduler,
        elapsed_ms=(perf_counter() - started) * 1000.0,
    )


def run_deterministic_mixed_soak(source_root: Path, work_root: Path) -> Dict[str, Any]:
    first = run_mixed_soak(source_root, work_root, name="run-a")
    second = run_mixed_soak(source_root, work_root, name="run-b")
    if first.final_state_root != second.final_state_root:
        raise AssertionError("mixed soak deterministic replay roots differ")
    if first.command_counts != second.command_counts:
        raise AssertionError("mixed soak command mixes differ")
    return {
        "status": "passed",
        "deterministic_replay": True,
        "root_equal": True,
        "final_state_root": first.final_state_root,
        "run_a": first.to_record(),
        "run_b": second.to_record(),
    }
