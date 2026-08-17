from contextvars import copy_context

import copy
import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
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


def _capability_row(count: int) -> dict:
    return {
        "fundamentals": {
            "combat": 64,
            "awareness": 68,
            "endurance": 64,
            "chakra_control": 68,
            "chakra_output": 63,
            "movement": 67,
            "tactics": 63,
            "team_coordination": 70,
        },
        "methods": {
            "sword": 57,
            "unarmed": 67,
            "thrown_tools": 65,
            "bow": 54,
            "polearm": 54,
            "heavy_weapon": 53,
            "ninjutsu": 78,
            "genjutsu": 68,
            "traps": 63,
            "sensory": 73,
            "medical": 61,
            "sealing": 63,
        },
        "experience": 58,
        "count": count,
        "spread": 17,
    }


def _force(*, available: int, reserve_count: int) -> dict:
    return {
        "schema": "force",
        "id": "force.konoha.shinobi",
        "availability": {"training_or_instruction": available},
        "reserve_capability": {
            "training_or_instruction": _capability_row(reserve_count),
        },
    }


def _pipeline_result(graduates: int) -> dict:
    return {
        "event_id": None,
        "population_pipeline": {
            "graduates": graduates,
            "force_ref": "force.konoha.shinobi",
            "intake_transfer_id": None,
            "graduation_transfer_id": (
                "autonomy.graduation.test" if graduates else None
            ),
        },
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


def test_academy_graduates_extend_matching_training_reserve_capability() -> None:
    force = _force(available=1448, reserve_count=1440)
    before = copy.deepcopy(force["reserve_capability"]["training_or_instruction"])

    fix.repair_academy_force_reserve_capability(
        AutonomyCommandsMixin(),
        _pipeline_result(8),
        {"state/force/konoha-shinobi.json": force},
    )

    row = force["reserve_capability"]["training_or_instruction"]
    assert row["count"] == 1448
    assert row["fundamentals"] == before["fundamentals"]
    assert row["methods"] == before["methods"]
    assert row["experience"] == before["experience"]
    assert row["spread"] == before["spread"]


def test_academy_reserve_repair_is_forward_compatible_when_base_is_already_synced() -> None:
    force = _force(available=1448, reserve_count=1448)
    before = copy.deepcopy(force)

    fix.repair_academy_force_reserve_capability(
        AutonomyCommandsMixin(),
        _pipeline_result(8),
        {"state/force/konoha-shinobi.json": force},
    )

    assert force == before


def test_academy_reserve_repair_refuses_unrelated_force_drift() -> None:
    force = _force(available=1450, reserve_count=1440)

    with pytest.raises(CommandRejectedError, match="force_reserve_capability_drift"):
        fix.repair_academy_force_reserve_capability(
            AutonomyCommandsMixin(),
            _pipeline_result(8),
            {"state/force/konoha-shinobi.json": force},
        )


def test_zero_graduates_do_not_require_or_mutate_force_staging() -> None:
    fix.repair_academy_force_reserve_capability(
        AutonomyCommandsMixin(),
        _pipeline_result(0),
        {},
    )


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


def test_campaign_installer_binds_all_academy_compatibility_before_wrappers() -> None:
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
    assert getattr(
        AutonomyCommandsMixin._apply_institution_autonomy_review,
        "_academy_force_reserve_capability",
        False,
    ) is True


def test_base_academy_pipeline_still_needs_compatibility_wrapper() -> None:
    # This sentinel keeps the compatibility layer honest. Once the base reducer
    # directly constructs transfer IDs, conserves every force capability
    # representation, and attributes/suppresses its aggregate institution review
    # event, remove this module and this test together.
    import inspect

    base = AutonomyCommandsMixin._apply_institution_autonomy_review
    while getattr(base, "__wrapped__", None) is not None:
        base = base.__wrapped__
    source = inspect.getsource(base)
    assert 'transfer_id=f"autonomy.intake.{suffix}"' in source
    assert 'transfer_id=f"autonomy.graduation.{suffix}"' in source
    assert 'availability["training_or_instruction"] = int(availability.get("training_or_instruction", 0)) + graduation' in source
    assert 'training_pool["count"] += graduation' in source
    assert 'kind="institution_autonomy_reviewed"' in source
