from __future__ import annotations

from shinobi_runtime.api.combat_pressure_integrity import interruption_aware_defense_record


def test_brace_does_not_cancel_pending_offense() -> None:
    captured: list[dict] = []

    def base_recorder(combat, **kwargs):
        captured.append(dict(kwargs))

    interruption_aware_defense_record(
        base_recorder,
        combat={"_pending_actions": {}},
        defender_ref="wei",
        attacker_ref="spear",
        response="brace",
        response_start_ms=700,
        response_contact_ms=900,
    )

    assert captured == []


def test_parry_still_routes_through_offensive_interruption() -> None:
    captured: list[dict] = []

    def base_recorder(combat, **kwargs):
        captured.append(dict(kwargs))

    interruption_aware_defense_record(
        base_recorder,
        combat={"_pending_actions": {}},
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
