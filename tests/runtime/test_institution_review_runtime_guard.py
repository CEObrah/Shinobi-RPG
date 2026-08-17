from __future__ import annotations

from shinobi_runtime.commands import institution_review_runtime_guard as guard_module
from shinobi_runtime.commands.domains.autonomy import AutonomyCommandsMixin


def review_event(
    event_id: str = "event.institution.review",
    *,
    refs: list[str] | None = None,
    kind: str = "institution_autonomy_reviewed",
) -> dict:
    return {
        "id": event_id,
        "kind": kind,
        "status": "resolved",
        "material_consequence_refs": [] if refs is None else list(refs),
    }


def test_final_reconcile_attributes_concrete_pipeline_consequence() -> None:
    world_events = {"events": [review_event()]}
    result = {
        "event_id": "event.institution.review",
        "population_pipeline": {
            "intake_transfer_id": "autonomy.intake.test",
            "graduation_transfer_id": None,
        },
    }

    repaired = guard_module._reconcile_final_event(result, world_events)

    assert repaired["event_id"] == "event.institution.review"
    assert world_events["events"][0]["material_consequence_refs"] == [
        "autonomy.intake.test"
    ]


def test_final_reconcile_suppresses_material_free_aggregate_review() -> None:
    world_events = {"events": [review_event()]}

    repaired = guard_module._reconcile_final_event(
        {"event_id": "event.institution.review"},
        world_events,
    )

    assert repaired["event_id"] is None
    assert world_events["events"] == []


def test_serialization_guard_suppresses_only_empty_hot_aggregate_review() -> None:
    concrete = review_event("event.institution.material", refs=["autonomy.intake.real"])
    other = review_event(
        "event.other.empty",
        kind="autonomous_mission_wake_required",
    )
    source = {
        "schema": "world-event-registry",
        "archived_event_count": 4,
        "archive_refs": [],
        "events": [review_event(), concrete, other],
    }

    cleaned = guard_module._strip_empty_aggregate_reviews(source)

    assert [event["id"] for event in cleaned["events"]] == [
        "event.institution.material",
        "event.other.empty",
    ]
    assert cleaned["events"][0]["material_consequence_refs"] == [
        "autonomy.intake.real"
    ]
    assert source["events"][0]["id"] == "event.institution.review"
    assert cleaned["archived_event_count"] == 4


def test_serialization_guard_repairs_pending_archive_counts() -> None:
    source = {
        "schema": "world-event-registry",
        "archived_event_count": 130,
        "archive_refs": ["state/history/events/segment-000008.json"],
        "events": [],
        "__pending_archive_writes__": {
            "state/history/events/segment-000008.json": {
                "schema": "world-event-archive",
                "event_count": 2,
                "events": [
                    review_event(),
                    review_event(
                        "event.institution.material",
                        refs=["formation:force.test"],
                    ),
                ],
            }
        },
    }

    cleaned = guard_module._strip_empty_aggregate_reviews(source)
    archive = cleaned["__pending_archive_writes__"][
        "state/history/events/segment-000008.json"
    ]

    assert archive["event_count"] == 1
    assert [event["id"] for event in archive["events"]] == [
        "event.institution.material"
    ]
    assert cleaned["archived_event_count"] == 129
    assert cleaned["archive_refs"] == [
        "state/history/events/segment-000008.json"
    ]


def test_serialization_guard_removes_empty_pending_archive_without_touching_other_refs() -> None:
    source = {
        "schema": "world-event-registry",
        "archived_event_count": 1,
        "archive_refs": [
            "state/history/events/segment-000007.json",
            "state/history/events/segment-000008.json",
        ],
        "events": [],
        "__pending_archive_writes__": {
            "state/history/events/segment-000008.json": {
                "schema": "world-event-archive",
                "event_count": 1,
                "events": [review_event()],
            }
        },
    }

    cleaned = guard_module._strip_empty_aggregate_reviews(source)

    assert cleaned["__pending_archive_writes__"] == {}
    assert cleaned["archive_refs"] == [
        "state/history/events/segment-000007.json"
    ]
    assert cleaned["archived_event_count"] == 0


def test_runtime_guard_strips_known_routing_hint_from_legacy_chain(monkeypatch) -> None:
    calls = []

    def legacy(
        self,
        *,
        institution,
        at,
        compacted,
        command,
        policy_book,
        world_events,
        record_writes,
    ):
        calls.append(institution["id"])
        world_events["events"].append(review_event())
        return {"event_id": "event.institution.review", "institution_id": institution["id"]}

    monkeypatch.setattr(
        AutonomyCommandsMixin,
        "_apply_institution_autonomy_review",
        legacy,
    )
    monkeypatch.setattr(guard_module, "_INSTALLED", False)
    guard_module.install_institution_review_runtime_guard()

    world_events = {"events": []}
    result = AutonomyCommandsMixin()._apply_institution_autonomy_review(
        institution={"id": "institution.test"},
        at="SE-0061-07-01T07:00:00",
        compacted=1,
        command=object(),
        policy_book=object(),
        institution_owner_ref="state/world/institutions-test.json",
        world_events=world_events,
        record_writes={},
    )

    assert calls == ["institution.test"]
    assert result["event_id"] is None
    assert world_events["events"] == []
