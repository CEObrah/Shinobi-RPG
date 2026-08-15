from contextvars import copy_context

import pytest

from shinobi_runtime.commands import academy_pipeline_transfer_ids as fix
from shinobi_runtime.commands.domains import autonomy as autonomy_module
from shinobi_runtime.commands.domains.autonomy import AutonomyCommandsMixin
from shinobi_runtime.sim.events import CampaignTime


def _review_event(*, refs=None):
    return {
        "id": "event.review.test",
        "kind": "institution_autonomy_reviewed",
        "material_consequence_refs": list(refs or []),
    }


def test_academy_pipeline_transfer_suffix_is_stable_and_boundary_specific() -> None:
    first = fix.academy_pipeline_transfer_suffix(
        "institution.konoha.academy", CampaignTime.parse("SE-0061-07-01T07:00:00")
    )
    repeated = fix.academy_pipeline_transfer_suffix(
        "institution.konoha.academy", CampaignTime.parse("SE-0061-07-01T07:00:00")
    )
    later = fix.academy_pipeline_transfer_suffix(
        "institution.konoha.academy", CampaignTime.parse("SE-0061-08-01T07:00:00")
    )
    assert first == repeated
    assert first != later
    assert len(first) == 20


def test_suffix_proxy_is_context_local() -> None:
    proxy = fix._SuffixProxy()
    with pytest.raises(RuntimeError, match="academy_pipeline_transfer_suffix_unbound"):
        format(proxy)

    token = fix._SUFFIX.set("alpha")
    try:
        assert f"{proxy}" == "alpha"
        isolated = copy_context()
        assert isolated.run(lambda: f"{proxy}") == "alpha"
    finally:
        fix._SUFFIX.reset(token)

    with pytest.raises(RuntimeError, match="academy_pipeline_transfer_suffix_unbound"):
        format(proxy)


def test_academy_review_event_gains_transfer_material_consequences() -> None:
    event = _review_event()
    world_events = {"events": [event]}
    result = {
        "event_id": event["id"],
        "population_pipeline": {
            "intake_transfer_id": "autonomy.intake.abc",
            "graduation_transfer_id": "autonomy.graduation.def",
        },
        "service_training": None,
        "military_lifecycle": None,
    }

    repaired = fix.repair_institution_review_event(result, world_events)

    assert repaired["event_id"] == event["id"]
    assert event["material_consequence_refs"] == [
        "autonomy.intake.abc",
        "autonomy.graduation.def",
    ]


def test_review_event_preserves_existing_refs_and_attributes_other_material_work() -> None:
    event = _review_event(refs=["operation.institution.existing"])
    world_events = {"events": [event]}
    result = {
        "event_id": event["id"],
        "population_pipeline": None,
        "service_training": {
            "force_ref": "force.konoha.shinobi",
            "completed": 8,
            "event_ref": "event.service.training",
        },
        "military_lifecycle": {
            "force_ref": "force.konoha.shinobi",
            "formation_ref": "formation.konoha.01",
            "medical_recovered": 3,
        },
    }

    fix.repair_institution_review_event(result, world_events)

    assert event["material_consequence_refs"] == [
        "operation.institution.existing",
        "service_training:force.konoha.shinobi:8",
        "formation.konoha.01",
        "force.konoha.shinobi",
        "medical_recovery:force.konoha.shinobi:3",
    ]


def test_material_free_review_suppresses_only_invalid_aggregate_event() -> None:
    detailed = {
        "id": "event.detail.test",
        "kind": "institutional_operation_checked",
        "material_consequence_refs": ["operation.detail"],
    }
    aggregate = _review_event()
    world_events = {"events": [detailed, aggregate]}
    result = {
        "event_id": aggregate["id"],
        "population_pipeline": None,
        "service_training": None,
        "military_lifecycle": None,
    }

    repaired = fix.repair_institution_review_event(result, world_events)

    assert repaired["event_id"] is None
    assert world_events["events"] == [detailed]


def test_campaign_installer_binds_suffix_and_review_integrity_before_wrappers() -> None:
    fix.install_academy_pipeline_transfer_ids()
    assert isinstance(autonomy_module.suffix, fix._SuffixProxy)
    assert getattr(
        AutonomyCommandsMixin._apply_institution_autonomy_review,
        "_academy_pipeline_transfer_ids",
        False,
    ) is True
    assert getattr(
        AutonomyCommandsMixin._apply_institution_autonomy_review,
        "_institution_review_event_integrity",
        False,
    ) is True


def test_base_academy_pipeline_still_needs_compatibility_wrapper() -> None:
    # This sentinel keeps the compatibility layer honest. Once the base reducer
    # directly constructs transfer IDs and attributes/suppresses its aggregate
    # institution review event, remove this module and this test together.
    import inspect

    base = AutonomyCommandsMixin._apply_institution_autonomy_review
    while getattr(base, "__wrapped__", None) is not None:
        base = base.__wrapped__
    source = inspect.getsource(base)
    assert 'transfer_id=f"autonomy.intake.{suffix}"' in source
    assert 'transfer_id=f"autonomy.graduation.{suffix}"' in source
    assert 'kind="institution_autonomy_reviewed"' in source
