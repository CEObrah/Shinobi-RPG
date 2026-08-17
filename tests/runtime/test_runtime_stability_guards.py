"""Regression coverage for production stability guards."""

import json

from shinobi_runtime.commands.runtime_stability import RuntimeStabilityMixin
from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner


class _EventTerminalBase:
    def _append_internal_event(self, registry, *args, **kwargs):
        event_id = "event.fixture.canon-pressure"
        registry.setdefault("events", []).append(
            {
                "id": event_id,
                "kind": kwargs.get("kind"),
                "status": "resolved",
                "host_refs": list(kwargs.get("host_refs", ())),
                "actor_refs": list(kwargs.get("actor_refs", ())),
                "place_refs": list(kwargs.get("place_refs", ())),
                "affected_owner_refs": list(kwargs.get("affected_owner_refs", ())),
                "material_consequence_refs": list(kwargs.get("material_consequence_refs", ())),
            }
        )
        return event_id


class _EventFixturePlanner(RuntimeStabilityMixin, _EventTerminalBase):
    pressures_path = "state/canon/pressures.json"
    scheduler_path = "state/time/causal-scheduler.json"


def test_exact_character_condition_controls_training_recovery_factor() -> None:
    assert CampaignCommandPlanner._health_recovery_factor(
        {"condition": {"readiness": "ready", "injuries": []}}
    ) == ("1", "1")
    assert CampaignCommandPlanner._health_recovery_factor(
        {"condition": {"readiness": "injured", "injuries": ["injury.test"]}}
    ) == ("0.65", "0.75")
    assert CampaignCommandPlanner._health_recovery_factor(
        {"condition": {"readiness": "unknown", "injuries": []}}
    ) == ("0.85", "0.90")


def test_world_event_archive_staging_never_leaks_into_hot_registry() -> None:
    archive_path = "state/history/events/segment-000001.json"
    registry = {
        "schema": "world-event-registry",
        "owner_id": "registry.world_events",
        "owner_type": "world_event_registry",
        "segment_limit": 128,
        "archived_event_count": 128,
        "archive_refs": [archive_path],
        "next_archive_seq": 2,
        "events": [],
        "archetype_catalog_ref": "game/data/content/world-event-archetypes.json",
        "__pending_archive_writes__": {
            archive_path: {
                "schema": "world-event-archive",
                "owner_id": "history.events.000001",
                "owner_type": "world_event_archive",
                "segment_index": 1,
                "created_at": "SE-0061-02-12T07:00:00",
                "event_count": 128,
                "events": [],
            }
        },
    }
    writes = CampaignCommandPlanner._world_event_writes(registry)
    persisted = json.loads(writes["state/reg/world-events.json"].decode("utf-8"))
    archive = json.loads(writes[archive_path].decode("utf-8"))
    assert "__pending_archive_writes__" not in persisted
    assert persisted["archive_refs"] == [archive_path]
    assert archive["owner_id"] == "history.events.000001"
    assert "__pending_archive_writes__" in registry


def test_canon_pressure_event_attributes_changed_authorities_without_state_time_dependency() -> None:
    registry = {"events": []}
    planner = _EventFixturePlanner()
    event_id = planner._append_internal_event(
        registry,
        kind="canon_pressure_reviewed",
        host_refs=("host.canon_pressure.fixture",),
        actor_refs=(),
        place_refs=(),
        affected_owner_refs=(),
        material_consequence_refs=("conditional_pressure:fixture",),
    )
    event = next(row for row in registry["events"] if row["id"] == event_id)
    assert set(event["affected_owner_refs"]) == {
        "state/canon/pressures.json",
        "state/time/causal-scheduler.json",
    }
    assert event["material_consequence_refs"] == ["conditional_pressure:fixture"]
