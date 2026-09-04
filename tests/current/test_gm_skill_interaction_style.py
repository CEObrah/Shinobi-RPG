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


def test_llm_scene_director_contract_requires_forward_human_life_not_rephrased_filler():
    scene = read("references/scene-craft.md")
    narration = read("references/narration.md")
    main = read("SKILL.md")
    assert "## Scene progression, not prose motion" in scene
    assert "Present NPCs are agents, not answer boxes" in scene
    assert "## LLM scene-director obligation" in narration
    assert "Permission is not enough" in narration
    assert "not scene progression" in narration
    assert "Active-scene progression is an LLM responsibility" in main
    assert "compress or transition rather than pad" in main
    assert "gm_scene_context.scene_direction" in main
    assert "Run the director protocol internally before prose" in narration
    assert "Decide the next beat before composing sentences" in scene


def test_ai_native_scene_engine_contract_is_documented_across_scene_combat_and_world_flow():
    main = read("SKILL.md")
    scene = read("references/scene-contract.md")
    combat = read("references/combat.md")
    world = read("references/world-simulation.md")
    assert "Use the LLM as the scene engine, not as a formatter" in main
    assert "## Active-session motion and participant agency" in scene
    assert "continuity, not a turn lock" in scene
    assert "## AI scene direction inside combat" in combat
    assert "not telemetry with decorative adjectives" in combat
    assert "## Living-world delivery and local scene life" in world
    assert "ordinary local human life does not require an offscreen world event" in world


def test_serialized_saga_contract_covers_all_scenes_and_combat():
    main = read("SKILL.md")
    narration = read("references/narration.md")
    scene = read("references/scene-craft.md")
    combat = read("references/combat.md")
    assert "### Serialized saga standard" in main
    assert "combat as strongly as dialogue" in main
    assert "## Serialized-scene architecture" in narration
    assert "approach / anticipation -> immediate human or physical objective -> friction" in narration
    assert "## Build scenes with pressure, turn, and residue" in scene
    assert "## Combat buildup, reversals, and aftermath" in combat
