from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "plugins/shinobi-rpg/skill/shinobi-game-master"


def read(rel: str) -> str:
    return (SKILL / rel).read_text(encoding="utf-8")


def test_player_interface_recovers_current_revision_transition_after_interruption():
    interface = read("references/player-interface.md")
    review = read("references/live-play-review.md")

    assert "## Recovering an interrupted committed transition" in interface
    assert "transition:current" in interface
    assert "next_object_ref" in interface
    assert "command_recoverable: false" in interface
    assert "Do not replay a transition that has already been shown" in interface
    assert "terminal state or casualty totals" in review


def test_reentry_contract_does_not_turn_stale_handoff_into_repeat_menu():
    interface = read("references/player-interface.md")
    review = read("references/live-play-review.md")

    assert "A stale presentation handoff is not automatically a new protected decision" in interface
    assert "stale scene/activity handoff" in review
