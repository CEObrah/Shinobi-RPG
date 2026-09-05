from __future__ import annotations

from types import SimpleNamespace

from shinobi_runtime.api.combat_pressure_integrity import (
    interruption_aware_defense_record,
    pending_action_record_with_start,
)


def _pending_record(*, start_at_ms: int, commit_at_ms: int, release_at_ms: int) -> dict:
    action = SimpleNamespace(
        start_at_ms=start_at_ms,
        commit_at_ms=commit_at_ms,
        release_at_ms=release_at_ms,
    )

    def production_shape(row):
        return {
            "commit_at_ms": int(row.commit_at_ms),
            "release_at_ms": int(row.release_at_ms),
        }

    return pending_action_record_with_start(production_shape, action)


def test_pending_action_record_carries_physical_start_time() -> None:
    assert _pending_record(start_at_ms=620, commit_at_ms=760, release_at_ms=980) == {
        "start_at_ms": 620,
        "commit_at_ms": 760,
        "release_at_ms": 980,
    }


def test_brace_does_not_cancel_offense_that_already_started() -> None:
    captured: list[dict] = []

    def base_recorder(combat, **kwargs):
        captured.append(dict(kwargs))

    interruption_aware_defense_record(
        base_recorder,
        combat={
            "_pending_actions": {
                "wei": _pending_record(start_at_ms=620, commit_at_ms=760, release_at_ms=980)
            }
        },
        defender_ref="wei",
        attacker_ref="spear",
        response="brace",
        response_start_ms=700,
        response_contact_ms=900,
    )

    assert captured == []


def test_brace_can_preempt_offense_that_has_not_started_yet() -> None:
    captured: list[dict] = []

    def base_recorder(combat, **kwargs):
        captured.append(dict(kwargs))

    interruption_aware_defense_record(
        base_recorder,
        combat={
            "_pending_actions": {
                "wei": _pending_record(start_at_ms=760, commit_at_ms=860, release_at_ms=1080)
            }
        },
        defender_ref="wei",
        attacker_ref="spear",
        response="brace",
        response_start_ms=700,
        response_contact_ms=900,
    )

    assert captured == [
        {
            "defender_ref": "wei",
            "attacker_ref": "spear",
            "response": "brace",
            "response_start_ms": 700,
            "response_contact_ms": 900,
        }
    ]


def test_parry_still_routes_through_offensive_interruption() -> None:
    captured: list[dict] = []

    def base_recorder(combat, **kwargs):
        captured.append(dict(kwargs))

    interruption_aware_defense_record(
        base_recorder,
        combat={
            "_pending_actions": {
                "wei": _pending_record(start_at_ms=620, commit_at_ms=760, release_at_ms=980)
            }
        },
        defender_ref="wei",
        attacker_ref="spear",
        response="parry",
        response_start_ms=700,
        response_contact_ms=900,
    )

    assert captured == [
        {
            "defender_ref": "wei",
            "attacker_ref": "spear",
            "response": "parry",
            "response_start_ms": 700,
            "response_contact_ms": 900,
        }
    ]
