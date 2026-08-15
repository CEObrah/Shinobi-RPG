from __future__ import annotations

from pathlib import Path


def test_named_social_requests_stage_actual_conversation() -> None:
    social = Path("runtime/contracts/narration/social-village.md").read_text(encoding="utf-8")
    family = Path("runtime/contracts/narration/family-politics.md").read_text(encoding="utf-8")

    assert "stage the conversation" in social
    assert "Do not answer with a paraphrase" in social
    assert "let the decision-relevant NPCs answer in their own voices" in social

    assert "do not replace the requested scene" in family
    assert "render actual NPC speech and visible reactions" in family
    assert "Never invent Wei's exact words" in family
