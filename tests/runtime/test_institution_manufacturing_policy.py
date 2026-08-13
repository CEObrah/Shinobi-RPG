from __future__ import annotations

import json
from pathlib import Path

from shinobi_runtime.commands import campaign_manufacturing  # noqa: F401
from shinobi_runtime.commands.specs import COMMAND_SPECS

_WEEK_SECONDS = 7 * 24 * 60 * 60


def _settle_chunks(chunks: list[int], *, weekly_hours: int, lines: int, batch_hours: int) -> tuple[int, int]:
    required = batch_hours * _WEEK_SECONDS
    progress = 0
    batches = 0
    for elapsed in chunks:
        total = progress + elapsed * weekly_hours * lines
        batches += total // required
        progress = total % required
    return batches, progress


def test_sword_manor_manufacturing_recipes_are_conserved_and_discoverable() -> None:
    mechanics = json.loads(
        Path("game/data/mechanics/institution-projects.json").read_text(encoding="utf-8")
    )
    stock = json.loads(Path("state/house/stock.json").read_text(encoding="utf-8"))
    recipes = mechanics["manufacturing_recipes"]
    schedule = mechanics["manufacturing_schedule"]

    assert 0 < schedule["standing_weekly_active_hours"] <= schedule["max_weekly_active_hours"] <= 48
    assert "institution_manufacturing_resolution" in COMMAND_SPECS
    assert set(recipes) == {
        "manufacturing.sword_manor.house_court_armor",
        "manufacturing.sword_manor.katana",
    }
    for recipe in recipes.values():
        assert recipe["required_module_kind"] == "production"
        assert recipe["output_item_ref"] in stock["items"]
        assert recipe["output_quantity_per_batch"] > 0
        assert recipe["active_hours_per_batch"] > 0
        assert recipe["procurement_cost_ryo_per_batch"] > 0
        assert recipe["authority_scope_ref"] == "project:workshop_expansion"


def test_standing_manufacturing_work_is_time_partition_invariant() -> None:
    weekly_hours = 40
    whole = _settle_chunks(
        [_WEEK_SECONDS], weekly_hours=weekly_hours, lines=1, batch_hours=24
    )
    split = _settle_chunks(
        [_WEEK_SECONDS // 3, _WEEK_SECONDS // 3, _WEEK_SECONDS - 2 * (_WEEK_SECONDS // 3)],
        weekly_hours=weekly_hours,
        lines=1,
        batch_hours=24,
    )
    assert whole == split
    assert whole[0] == 1
