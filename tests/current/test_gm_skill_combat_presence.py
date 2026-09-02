from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMBAT = ROOT / "plugins/shinobi-rpg/skill/shinobi-game-master/references/combat.md"


def test_gm_uses_arrived_combat_cast_not_registered_future_reinforcements():
    text = COMBAT.read_text(encoding="utf-8")

    assert "scene.combat_present_person_ids" in text
    assert "exact player-safe friendly cast" in text
    assert "route membership" in text
    assert "registered future reinforcement" in text
    assert "not co-present" in text
    assert "exact reinforcement clock arrives" in text
