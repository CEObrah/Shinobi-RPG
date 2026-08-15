import json
from pathlib import Path

from shinobi_runtime.sim import CampaignTime, HostState
from shinobi_runtime.sim.scheduler import (
    CausalSchedulerRegistry,
    SchedulerHost,
    recurring_event,
    settle_scheduler,
)
from shinobi_runtime.sim.events import EventQueue
from shinobi_runtime.sim.scheduler_store import SchedulerStore, legacy_to_shards
from shinobi_runtime.store import RepositoryStore

ROOT_PATH = "state/time/causal-scheduler.json"


def t(value: str) -> CampaignTime:
    return CampaignTime.parse(value)


def _host(host_id: str, due: str) -> SchedulerHost:
    due_at = t(due)
    return SchedulerHost(
        state=HostState(
            host_id=host_id,
            kind="faction",
            resolved_through=t("SE-0061-01-01T00:00:00"),
            safe_through=due_at.add_seconds(-1),
            handler_ref="test.scheduler",
            rng_namespace=host_id,
            next_due=due_at,
        ),
        authority_kind="faction",
        owner_ref=host_id.replace("host.", "faction."),
        metadata={},
    )


def _legacy() -> CausalSchedulerRegistry:
    due_soon = t("SE-0061-01-02T00:00:00")
    due_late = t("SE-0099-01-01T00:00:00")
    hosts = {
        "host.soon": _host("host.soon", str(due_soon)),
        "host.late": _host("host.late", str(due_late)),
    }
    events = [
        recurring_event(
            kind="faction.periodic_review",
            identity="soon",
            host_id="host.soon",
            due_at=due_soon,
            recurrence={"kind": "fixed_interval", "interval_seconds": 86400},
            payload={},
        ),
        recurring_event(
            kind="faction.periodic_review",
            identity="late",
            host_id="host.late",
            due_at=due_late,
            recurrence={"kind": "fixed_interval", "interval_seconds": 86400},
            payload={},
        ),
    ]
    return CausalSchedulerRegistry(
        world_time=t("SE-0061-01-01T00:00:00"),
        hosts=hosts,
        queue=EventQueue(events),
        seeded_at=t("SE-0061-01-01T00:00:00"),
        bootstrap_source="test",
        metrics={
            "host_count": 2,
            "pending_event_count": 2,
            "global_person_scans": 0,
            "global_faction_directory_scans": 0,
        },
    )


def _write_sharded(tmp_path: Path) -> RepositoryStore:
    repo = RepositoryStore(tmp_path)
    for path, content in legacy_to_shards(_legacy().to_record()).items():
        repo.replace_image(path, content)
    return repo


class RecordingRepository(RepositoryStore):
    def __init__(self, root: object) -> None:
        super().__init__(root)
        self.read_paths: list[str] = []

    def read_optional_bytes(self, relative_path: object):
        self.read_paths.append(str(relative_path))
        return super().read_optional_bytes(relative_path)


def test_scheduler_migration_preserves_exact_logical_state(tmp_path: Path) -> None:
    original = _legacy().to_record()
    repo = _write_sharded(tmp_path)
    root = repo.read_json(ROOT_PATH)
    assert root["storage_version"] == 2
    assert "hosts" not in root and "events" not in root
    assert SchedulerStore(repo).load(full=True).to_record() == original


def test_scheduler_window_reads_due_work_not_unrelated_future_event_year(tmp_path: Path) -> None:
    _write_sharded(tmp_path)
    repo = RecordingRepository(tmp_path)
    scheduler = SchedulerStore(repo).load(target=t("SE-0061-01-02T00:00:00"))
    assert set(scheduler.hosts) == {"host.soon"}
    assert {event.target_host for event in scheduler.queue.snapshot()} == {"host.soon"}
    assert not any("/events/0099/" in path for path in repo.read_paths)
    assert "state/time/causal-scheduler/event-index/0099.json" not in repo.read_paths


def test_window_settlement_preserves_unloaded_future_work(tmp_path: Path) -> None:
    repo = _write_sharded(tmp_path)
    store = SchedulerStore(repo)
    scheduler = store.load(target=t("SE-0061-01-02T12:00:00"))
    result = settle_scheduler(scheduler, target=t("SE-0061-01-02T12:00:00"))
    assert result.reached_time == t("SE-0061-01-02T12:00:00")
    for path, content in store.write_images(scheduler).items():
        repo.replace_image(path, content)
    full = SchedulerStore(repo).load(full=True)
    assert "host.late" in full.hosts
    assert any(event.target_host == "host.late" for event in full.queue.snapshot())
    assert full.world_time == t("SE-0061-01-02T12:00:00")
