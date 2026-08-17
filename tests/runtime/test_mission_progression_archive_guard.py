from __future__ import annotations

from shinobi_runtime.commands.mission_progression import _evidence_guard


class _Repository:
    def __init__(self):
        self.registry = {
            "events": [],
            "archive_refs": ["state/history/events/archive-0001.json"],
        }
        self.archive = {
            "events": [
                {
                    "id": "event.archived-evidence",
                    "kind": "travel_completed",
                }
            ]
        }

    def read_json(self, path):
        if path == "state/reg/world-events.json":
            return self.registry
        if path == "state/history/events/archive-0001.json":
            return self.archive
        raise FileNotFoundError(path)

    def digest(self, path):
        return {
            "state/reg/world-events.json": "digest-hot",
            "state/history/events/archive-0001.json": "digest-archive",
        }[path]


def test_archived_evidence_guards_the_exact_archive_not_hot_registry() -> None:
    assert _evidence_guard(
        _Repository(),
        "event.archived-evidence",
    ) == {"state/history/events/archive-0001.json": "digest-archive"}
