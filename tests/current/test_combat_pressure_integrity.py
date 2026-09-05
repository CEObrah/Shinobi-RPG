from __future__ import annotations

from shinobi_runtime.api.combat_pressure_integrity import interruption_aware_defense_record


def test_brace_does_not_cancel_offense_that_already_started() -> None:
    captured: list[dict] = []

    def base_recorder(combat, **kwargs):
        captured.append(dict(kwargs))

    interruption_aware_defense_record(
        base_recorder,
        combat={
            "_pending_actions": {
                "wei": {
                    "start_at_ms": 620,
                    "commit_at_ms": 760,
                    "release_at_ms": 980,
                }
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
                "wei": {
                    "start_at_ms": 760,
                    "commit_at_ms": 860,
                    "release_at_ms": 1080,
                }
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
                "wei": {
                    "start_at_ms": 620,
                    "commit_at_ms": 760,
                    "release_at_ms": 980,
                }
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
