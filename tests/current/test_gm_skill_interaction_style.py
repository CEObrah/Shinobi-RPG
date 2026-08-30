from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "plugins/shinobi-rpg/skill/shinobi-game-master"


def read(rel: str) -> str:
    return (SKILL / rel).read_text(encoding="utf-8")


def test_general_interaction_contract_rejects_round_robin_dialogue():
    scene = read("references/scene-craft.md")
    narration = read("references/narration.md")
    main = read("SKILL.md")
    assert "## Information follows the interaction" in scene
    assert "Never allocate turns as a round-robin" in scene
    assert "Avoid transcript cadence" in scene
    assert "Avoid transcript cadence" in narration
    assert "Do not allocate dialogue by attendee" in main


def test_performance_guidance_is_not_a_speaking_quota():
    contract = read("references/scene-contract.md")
    playbook = read("references/scene-playbook.md")
    assert "Performance guidance is not a speaking quota" in contract
    assert "An attendee list is not a dialogue queue" in playbook


def test_scene_contract_discourages_authorial_dialogue_commentary():
    scene = read("references/scene-craft.md")
    narration = read("references/narration.md")
    assert "## Do not narrate the narration" in scene
    assert "Do not announce that a discussion has reached a natural stopping point" in narration


def test_interaction_contract_separates_shared_premises_from_live_unknowns():
    scene = read("references/scene-craft.md")
    contract = read("references/scene-contract.md")
    narration = read("references/narration.md")
    review = read("references/live-play-review.md")
    main = read("SKILL.md")
    assert "## Shared premises vs. live unknowns" in scene
    assert "Do not make NPCs ask questions whose answers are already common ground" in scene
    assert "## Shared premises and live unknowns" in contract
    assert "do not manufacture ignorance to create exposition" in contract
    assert "Do not use obviously known premises as setup questions" in narration
    assert "shared premises" in review
    assert "distinguish **shared premises** from **live unknowns**" in main
