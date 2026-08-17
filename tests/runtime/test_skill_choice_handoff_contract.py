from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_root_skill_allows_choices_at_meaningful_event_handoffs():
    text = (
        ROOT / "plugins/shinobi-rpg/skills/shinobi-game-master/SKILL.md"
    ).read_text()
    assert "meaningful player-facing event handoff" in text
    assert "decision_required: null` is not by itself a reason to suppress a useful menu" in text
    assert "If the player already declared a clear action, resolve it instead" in text
