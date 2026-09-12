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


def test_skill_requires_full_span_beginning_middle_end_recovery_before_combat_prose():
    text = COMBAT_REFERENCE.read_text(encoding="utf-8")
    assert "Sustained-fight recovery gate" in text
    assert "opening pressure" in text
    assert "material middle development" in text
    assert "terminal beat" in text
    assert "Never compose the fight from terminal health plus a tail-only event sample" in text
    assert "follow `transition:current` sequentially" in text
    assert "Compression removes repetition, not causality" in text


def test_skill_does_not_spend_finite_poison_from_omission():
    base = (
        REPO_ROOT
        / "plugins"
        / "shinobi-rpg"
        / "skill"
        / "shinobi-game-master"
        / "references"
        / "combat-base.md"
    ).read_text(encoding="utf-8")
    assert "Finite poison is different: omission means no poison" in base
    assert "Use explicit `poison_ref: auto` only when the player has actually delegated poison expenditure" in base


def test_skill_preserves_relentless_lethal_tempo_in_runtime_payload() -> None:
    skill = (
        REPO_ROOT
        / "plugins"
        / "shinobi-rpg"
        / "skill"
        / "shinobi-game-master"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "kill as many as possible as quickly as possible" in skill
    assert "encode the registered lethal `until_resolution` span" in skill
    assert "movement_intent: chase" in skill
    assert "Never reduce that declaration to `targeting_intent: lethal` plus an arbitrary exchange count" in skill
