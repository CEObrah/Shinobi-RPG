from __future__ import annotations

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.campaign_player_training_order_repair import (
    _NEW_ORDER,
    _OLD_ORDER,
    _repair_order,
)


def _player(orders):
    return {
        "schema": "shinobi_character",
        "owner_id": "pc_wei_tang",
        "goal_state": {"current_orders": list(orders)},
    }


def test_repair_replaces_only_exact_superseded_house_order() -> None:
    other = "Keep Team Fujin coverage under Zhu or Linh when Wei is unavailable."
    repaired = _repair_order(_player([other, _OLD_ORDER]))

    assert repaired["goal_state"]["current_orders"] == [other, _NEW_ORDER]


def test_repair_rejects_nonexact_player_intent_instead_of_guessing() -> None:
    with pytest.raises(CommandRejectedError) as exc:
        _repair_order(_player(["Some other House training order."]))
    assert exc.value.code == "campaign_player_training_order_repair_source_not_exact"


def test_repair_rejects_second_application() -> None:
    with pytest.raises(CommandRejectedError) as exc:
        _repair_order(_player([_NEW_ORDER]))
    assert exc.value.code == "campaign_player_training_order_repair_already_applied"
