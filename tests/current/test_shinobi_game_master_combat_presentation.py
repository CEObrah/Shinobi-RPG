from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
COMBAT_REFERENCE = (
    REPO_ROOT
    / "plugins"
    / "shinobi-rpg"
    / "skill"
    / "shinobi-game-master"
    / "references"
    / "combat.md"
)


def test_observed_hostile_count_is_not_presented_as_current_strength_census():
    text = COMBAT_REFERENCE.read_text(encoding="utf-8")

    assert "`confirmed_observed_hostile_count` is cumulative encounter observation" in text
    assert "It is not a live census" in text
    assert "Current-strength narration must come from fresh player-lawful battlefield evidence" in text
    assert "Never back-calculate an IC live enemy count" in text
    assert "fresh lawful perception outranks stale mission reports" in text
    assert (
        "`confirmed_observed_hostile_count` means exactly what that observer has detected "
        "among the current hostile combatants"
    ) not in text
