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
    assert set(policy["instructor_refs"]) == {"char.zhu", "char.linh"}
    assert policy["basis"] == "persisted Team Fujin training and Sword Manor supervision orders"
