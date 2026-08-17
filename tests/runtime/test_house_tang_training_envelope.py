from __future__ import annotations

import json
from pathlib import Path


def test_house_tang_uses_full_sustainable_week_with_recovery_day() -> None:
    root = json.loads(
        Path("game/data/house/training-policies.json").read_text(encoding="utf-8")
    )
    policy = root["policies"]["house.tang"]

    assert root["format_version"] == 3
    assert policy["weekly_active_hours_cap"] == 48
    assert policy["shared_core_active_hours_per_week"] == 34
    assert policy["supplemental_individual_active_hours_per_week"] == 14
    assert (
        policy["shared_core_active_hours_per_week"]
        + policy["supplemental_individual_active_hours_per_week"]
        == policy["weekly_active_hours_cap"]
    )
    assert sum(policy["daily_active_hours"]) == 48
    assert policy["daily_active_hours"][-1] == 0
    assert max(policy["daily_active_hours"]) == 8
