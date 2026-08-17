from __future__ import annotations

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.runtime_stability import (
    RuntimeStabilityMixin,
    _event_by_id,
)


class _BaseInstitutionReview:
    def _append_internal_event(self, registry, *args, **kwargs):
        event_id = "event.institution.test"
        registry["events"].append(
            {
                "id": event_id,
                "kind": kwargs["kind"],
                "status": "resolved",
                "host_refs": list(kwargs.get("host_refs", ())),
                "actor_refs": [],
                "place_refs": [],
                "affected_owner_refs": list(kwargs.get("affected_owner_refs", ())),
                "material_consequence_refs": list(
                    kwargs.get("material_consequence_refs", ())
                ),
            }
        )
        return event_id

    def _apply_institution_autonomy_review(self, *args, **kwargs):
        registry = kwargs["world_events"]
        event_id = self._append_internal_event(
            registry,
            kind="institution_autonomy_reviewed",
            host_refs=("institution.test",),
            affected_owner_refs=(),
            material_consequence_refs=(),
        )
        mode = kwargs.get("test_mode")
        event = _event_by_id(registry, event_id)
        if mode == "attribute":
            event["material_consequence_refs"] = ["autonomy.intake.test"]
        elif mode == "suppress":
            registry["events"].remove(event)
            event_id = None
        return {"event_id": event_id, "institution_id": "institution.test"}


class _Subject(RuntimeStabilityMixin, _BaseInstitutionReview):
    pressures_path = "state/canon/pressures.json"
    scheduler_path = "state/time/causal-scheduler.json"


class _ImmediateBase:
    def _append_internal_event(self, registry, *args, **kwargs):
        event_id = "event.other.test"
        registry["events"].append(
            {
                "id": event_id,
                "kind": kwargs["kind"],
                "status": "resolved",
                "host_refs": ["host.test"],
                "actor_refs": [],
                "place_refs": [],
                "affected_owner_refs": ["state/test.json"],
                "material_consequence_refs": [],
            }
        )
        return event_id


class _ImmediateSubject(RuntimeStabilityMixin, _ImmediateBase):
    pressures_path = "state/canon/pressures.json"
    scheduler_path = "state/time/causal-scheduler.json"


def test_institution_review_is_validated_after_final_attribution() -> None:
    registry = {"events": []}

    result = _Subject()._apply_institution_autonomy_review(
        institution_owner_ref="state/world/institutions.json",
        world_events=registry,
        test_mode="attribute",
    )

    assert result["event_id"] == "event.institution.test"
    event = _event_by_id(registry, result["event_id"])
    assert event["affected_owner_refs"] == ["state/world/institutions.json"]
    assert event["material_consequence_refs"] == ["autonomy.intake.test"]


def test_material_free_final_institution_review_still_fails_closed() -> None:
    registry = {"events": []}

    with pytest.raises(
        CommandRejectedError,
        match="world_event_missing_material_consequence__institution_autonomy_reviewed",
    ):
        _Subject()._apply_institution_autonomy_review(
            institution_owner_ref="state/world/institutions.json",
            world_events=registry,
            test_mode="leave-empty",
        )


def test_suppressed_noop_institution_review_needs_no_terminal_event() -> None:
    registry = {"events": []}

    result = _Subject()._apply_institution_autonomy_review(
        institution_owner_ref="state/world/institutions.json",
        world_events=registry,
        test_mode="suppress",
    )

    assert result["event_id"] is None
    assert registry["events"] == []


def test_other_terminal_events_remain_immediately_validated() -> None:
    registry = {"events": []}

    with pytest.raises(
        CommandRejectedError,
        match="world_event_missing_material_consequence__other_terminal_event",
    ):
        _ImmediateSubject()._append_internal_event(
            registry,
            kind="other_terminal_event",
            affected_owner_refs=("state/test.json",),
            host_refs=("host.test",),
        )


def test_new_event_lookup_includes_pending_archive_segments() -> None:
    event = {
        "id": "event.archived.test",
        "kind": "institution_autonomy_reviewed",
        "status": "resolved",
        "host_refs": ["institution.test"],
        "affected_owner_refs": ["state/world/institutions.json"],
        "material_consequence_refs": ["priority.test"],
    }
    registry = {
        "events": [],
        "__pending_archive_writes__": {
            "state/history/events/segment-000008.json": {
                "events": [event],
            }
        },
    }

    assert _event_by_id(registry, "event.archived.test") is event
