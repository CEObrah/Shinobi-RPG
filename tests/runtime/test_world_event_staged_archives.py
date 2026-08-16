import json
from types import SimpleNamespace

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.semantic_event_integrity import (
    _rehydrate_pending_archives_from_plan,
    _world_event_by_id_staging_safe,
    _world_event_record_and_digest_staging_safe,
)


class _Repository:
    def __init__(self, records=None):
        self.records = dict(records or {})
        self.reads = []
        self.digests = []

    def read_json(self, path):
        self.reads.append(path)
        if path not in self.records:
            raise FileNotFoundError(path)
        return self.records[path]

    def digest(self, path):
        self.digests.append(path)
        if path not in self.records and path != "state/reg/world-events.json":
            return None
        return "digest:" + path


class _Planner:
    def __init__(self, repository):
        self.repository = repository


def _event(event_id):
    return {"id": event_id, "kind": "test", "status": "resolved"}


def test_pending_archive_is_read_from_staged_after_image_not_repository():
    persisted_path = "state/history/events/segment-000001.json"
    pending_path = "state/history/events/segment-000002.json"
    persisted = _event("event.persisted")
    pending = _event("event.pending")
    repository = _Repository({persisted_path: {"events": [persisted]}})
    planner = _Planner(repository)
    registry = {
        "events": [],
        "archive_refs": [persisted_path, pending_path],
        "__pending_archive_writes__": {
            pending_path: {"events": [pending]},
        },
    }

    assert _world_event_by_id_staging_safe(
        planner, "event.pending", registry=registry
    ) == pending
    assert repository.reads == []

    event, digest = _world_event_record_and_digest_staging_safe(
        planner, "event.pending", registry=registry
    )
    assert event == pending
    assert digest is None
    assert repository.reads == []
    assert pending_path not in repository.digests

    assert _world_event_by_id_staging_safe(
        planner, "event.persisted", registry=registry
    ) == persisted
    assert repository.reads == [persisted_path]


def test_persisted_archive_failure_still_fails_closed_for_exact_history_lookup():
    missing_path = "state/history/events/segment-missing.json"
    planner = _Planner(_Repository())
    registry = {
        "events": [],
        "archive_refs": [missing_path],
    }

    with pytest.raises(CommandRejectedError) as excinfo:
        _world_event_by_id_staging_safe(planner, "event.needed", registry=registry)

    assert excinfo.value.code == "world_event_archive_invalid"


def test_nested_plan_archive_writes_are_rehydrated_before_more_events_are_appended():
    pending_path = "state/history/events/segment-000010.json"
    pending = {"schema": "world-event-archive", "events": [_event("event.nested")]}
    hot_registry = {
        "schema": "world-event-registry",
        "events": [],
        "archive_refs": [pending_path],
    }
    base = SimpleNamespace(
        writes={
            "state/reg/world-events.json": json.dumps(hot_registry).encode("utf-8"),
            pending_path: json.dumps(pending).encode("utf-8"),
        }
    )

    restored = _rehydrate_pending_archives_from_plan(hot_registry, base)

    assert restored["__pending_archive_writes__"][pending_path] == pending
    assert "__pending_archive_writes__" not in hot_registry
