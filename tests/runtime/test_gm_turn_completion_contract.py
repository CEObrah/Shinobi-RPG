from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT / "plugins/shinobi-rpg/skills/shinobi-game-master"


def test_standing_wait_and_ooc_detour_are_not_false_turn_endings() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    contract = json.loads(
        (ROOT / "runtime/contracts/choice-presentation.json").read_text(encoding="utf-8")
    )

    required_skill_phrases = (
        "unfinished turn intent",
        "quiet maintenance/time chunk is never turn completion",
        "OOC DEV repair",
        "resume the same declared horizon automatically",
        "event-seeking",
    )
    for phrase in required_skill_phrases:
        assert phrase in skill

    assert "unfinished turn intent" in contract["standing_continuation_rule"]
    assert "does not consume or cancel" in contract["ooc_detour_rule"]
    assert "event-seeking command" in contract["event_seek_rule"]
    assert "arbitrary short advance_time chunks" in contract["event_seek_rule"]


def test_choice_menu_is_not_a_substitute_for_unfinished_continuation() -> None:
    choices = (SKILL_ROOT / "references/choices.md").read_text(encoding="utf-8")
    contract = json.loads(
        (ROOT / "runtime/contracts/choice-presentation.json").read_text(encoding="utf-8")
    )

    assert "Do not interrupt declared intent" in choices
    assert "Do not offer `keep waiting`" in choices
    assert "standing wait/declared continuation" in contract["handoff_rule"]
    assert "quiet maintenance/time chunk" in contract["standing_continuation_rule"]
