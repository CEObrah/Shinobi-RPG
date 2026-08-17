from __future__ import annotations

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.campaign_player_attribute_correction import (
    _AFTER,
    _BEFORE,
    _correct_attributes,
)


def _player(attributes):
    return {
        "schema": "shinobi_character",
        "owner_id": "pc_wei_tang",
        "attributes": {
            "agility": 145,
            "awareness": attributes["awareness"],
            "coordination": attributes["coordination"],
            "intelligence": attributes["intelligence"],
            "strength": 120,
        },
        "goal_state": {"current_orders": ["unchanged"]},
    }


def test_correction_changes_only_three_exact_stale_attributes() -> None:
    original = _player(_BEFORE)
    repaired = _correct_attributes(original)

    assert {key: repaired["attributes"][key] for key in _AFTER} == _AFTER
    assert repaired["attributes"]["agility"] == 145
    assert repaired["attributes"]["strength"] == 120
    assert repaired["goal_state"] == original["goal_state"]
    assert original["attributes"] == {
        "agility": 145,
        "awareness": 145,
        "coordination": 150,
        "intelligence": 170,
        "strength": 120,
    }


def test_correction_rejects_drift_instead_of_guessing() -> None:
    drifted = dict(_BEFORE)
    drifted["awareness"] = 146
    with pytest.raises(CommandRejectedError) as exc:
        _correct_attributes(_player(drifted))
    assert exc.value.code == "campaign_player_attribute_correction_source_not_exact"


def test_correction_rejects_second_application() -> None:
    with pytest.raises(CommandRejectedError) as exc:
        _correct_attributes(_player(_AFTER))
    assert exc.value.code == "campaign_player_attribute_correction_already_applied"
