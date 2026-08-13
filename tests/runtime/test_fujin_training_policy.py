from __future__ import annotations

import json
from pathlib import Path


def test_fujin_training_policy_uses_sword_manor_house_supervision() -> None:
    root = json.loads(
        Path("game/rules/training/autonomy-participation.json").read_text(encoding="utf-8")
    )
    policy = root["policies"]["team.konoha.fujin"]

    assert policy["enabled"] is True
    assert policy["assembly_location_ref"] == "place.sword_manor"
    assert policy["assemble_nonplayer_members"] is True
    assert policy["active_hours_per_week"] == 34
    assert policy["full_training_day_hours"] * 5 + policy["taper_day_hours"] == 34
    assert policy["recovery_days_per_cycle"] == 1
    assert policy["instructor_strategy"] == "replace_team_instructors"
    assert policy["instructor_refs"] == ["char.zhu", "char.linh"]
    assert policy["target_strategy"] == "weakness_strength_balanced"
