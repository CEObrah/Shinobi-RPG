from __future__ import annotations

from shinobi_runtime.commands import institution_review_runtime_guard as guard_module
from shinobi_runtime.commands.domains.autonomy import AutonomyCommandsMixin


def review_event(event_id: str = "event.institution.review") -> dict:
    return {
        "id": event_id,
        "kind": "institution_autonomy_reviewed",
        "material_consequence_refs": [],
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
