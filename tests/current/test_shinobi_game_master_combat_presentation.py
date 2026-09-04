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

def test_player_facing_combat_prose_translates_resolver_primitives_into_lived_action():
    text = COMBAT_REFERENCE.read_text(encoding="utf-8")

    assert "Player-facing combat prose must not name resolver primitives" in text
    assert "attack line" in text
    assert "movement lane" in text
    assert "contact geometry" in text
    assert "Translate mechanics into embodied cause and effect" in text
    assert "continuity of an action scene in a strong novel or film" in text
    assert "The receipt is evidence for the GM, not dialogue for the player" in text



def test_skill_consumes_committed_combat_narrative_projection_before_returning_control():
    text = COMBAT_REFERENCE.read_text(encoding="utf-8")
    assert "When a committed exact-combat result exposes `narrative_projection`" in text
    assert "must_narrate_before_next_decision: true" in text
    assert "A coarse `contact_zone: neck` is not permission" in text
    assert "before summarizing the enemy as retreating or routing" in text
