from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from shinobi_runtime.narration import select_narration_modules


ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT / "plugins/shinobi-rpg/skills/shinobi-game-master"


def load_json(relative_path: str) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def test_hot_voice_protects_lens_agency_knowledge_and_turn_shape() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    narration = (SKILL_ROOT / "references/narration.md").read_text(encoding="utf-8")
    combined = skill + "\n" + narration
    required = (
        "second-person present tense",
        "Never choose Wei's consequential voluntary",
        "Keep world truth and player knowledge separate",
        "Let mechanics determine what happens",
        "player decision",
        "causal pressure",
    )
    for phrase in required:
        assert phrase in combined


def test_skill_encodes_living_scene_handoffs_without_inventing_world_reaction() -> None:
    narration = (SKILL_ROOT / "references/narration.md").read_text(encoding="utf-8")
    choices = (SKILL_ROOT / "references/choices.md").read_text(encoding="utf-8")
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "Reversible scene-local interaction" in narration
    assert "Player action is not world reaction" in narration
    assert "ordinary reversible acknowledgements" in skill
    assert "carry the declared purpose" in choices
    assert "standing wait" in choices
    assert "software correction" in narration


def test_scene_flow_contract_covers_chronology_choice_integrity_and_bounded_delegation() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    narration = (SKILL_ROOT / "references/narration.md").read_text(encoding="utf-8")
    choices = (SKILL_ROOT / "references/choices.md").read_text(encoding="utf-8")
    review = (SKILL_ROOT / "references/live-play-review.md").read_text(encoding="utf-8")
    contract = load_json("runtime/contracts/choice-presentation.json")

    assert "Chronology and scene dateline" in narration
    assert "authoritative campaign date/time" in narration
    assert "must not remain frozen at one timestamp" in narration
    assert "unresolved_decision: null" in narration
    assert "Preserve premise parity" in choices
    assert "Render selected choices in-world" in choices
    assert "Bounded judgment delegation" in choices
    assert "one bounded answer" in choices
    assert "dead-end handoff" in review
    assert "premise" in contract["premise_parity_rule"]
    assert "explicit in-world Wei action" in contract["selection_render_rule"]
    assert "one bounded answer" in contract["bounded_delegation_rule"]
    assert "null unresolved decision" in contract["handoff_rule"]
    assert "campaign date/time" in skill


def test_narration_router_is_bounded_and_all_modules_are_cold() -> None:
    router = load_json("runtime/contracts/narration-router.json")
    modules = router["modules"]
    assert set(modules) == {
        "combat",
        "covert_intel_mission",
        "social_village_institution",
        "command_large_war",
        "training_recovery",
        "family_clan_politics",
    }
    assert len(set(modules.values())) == len(modules)
    assert "one primary" in router["rule"]
    assert "Never preload all modules" in router["rule"]
    assert router["default_primary"] == "social_village_institution"
    assert router["scene_type_primary"]["institutional_command"] == (
        "social_village_institution"
    )
    assert router["pressure_gated_modules"] == ["command_large_war"]
    assert "command_large_war" not in router["scene_type_primary"].values()
    assert set(router["pressure_primary_overrides"].values()) == {
        "command_large_war"
    }

    hot = set(load_json("runtime/contracts/repository-map.json")["hot"])
    for relative_path in modules.values():
        assert relative_path.startswith("runtime/contracts/narration/")
        assert relative_path not in hot
        module_text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert module_text.startswith("# ")
        assert "VOICE.md" not in module_text
        assert "RUNTIME.md" not in module_text


def test_institutional_command_routes_to_social_without_large_force_pressure() -> None:
    router = load_json("runtime/contracts/narration-router.json")
    selection = select_narration_modules(
        router,
        scene_type="institutional_command",
        pressures=("new_team_active_before_first_training_cycle",),
    )
    assert selection.primary_id == "social_village_institution"
    assert selection.primary_path.endswith("social-village.md")
    assert selection.secondary_id is None
    assert selection.matched_pressures == ()
    assert selection.scene_type_matched is True


def test_command_war_requires_an_exact_causal_large_force_pressure() -> None:
    router = load_json("runtime/contracts/narration-router.json")
    ordinary = select_narration_modules(
        router,
        scene_type="institutional command",
        pressures=("large force command training", "command paperwork"),
    )
    assert ordinary.primary_id == "social_village_institution"
    assert ordinary.secondary_id is None

    large_force = select_narration_modules(
        router,
        scene_type="Institutional Command",
        pressures=("Large-Force Command",),
    )
    assert large_force.primary_id == "command_large_war"
    assert large_force.primary_path.endswith("command-war.md")
    assert large_force.secondary_id == "social_village_institution"
    assert large_force.matched_pressures == ("large_force_command",)


def test_pressure_routing_is_order_independent_and_fails_closed_on_conflict() -> None:
    router = load_json("runtime/contracts/narration-router.json")
    first = select_narration_modules(
        router,
        scene_type="combat",
        pressures=("war_command", "mass_battle"),
    )
    replay = select_narration_modules(
        router,
        scene_type="combat",
        pressures=("mass-battle", "war command"),
    )
    assert first == replay
    assert first.primary_id == "command_large_war"
    assert first.secondary_id == "combat"

    conflicting = deepcopy(router)
    conflicting["pressure_primary_overrides"]["single_combat"] = "combat"
    with pytest.raises(ValueError, match="conflicting narration modules"):
        select_narration_modules(
            conflicting,
            scene_type="institutional_command",
            pressures=("mass_battle", "single_combat"),
        )


def test_pressure_gated_module_cannot_be_selected_by_scene_type() -> None:
    router = load_json("runtime/contracts/narration-router.json")
    invalid = deepcopy(router)
    invalid["scene_type_primary"]["institutional_command"] = "command_large_war"
    with pytest.raises(ValueError, match="reachable without a pressure"):
        select_narration_modules(
            invalid,
            scene_type="institutional_command",
        )

    nonstatic = deepcopy(router)
    nonstatic["authority"] = True
    with pytest.raises(ValueError, match="invalid narration router"):
        select_narration_modules(
            nonstatic,
            scene_type="institutional_command",
        )


def test_choice_menu_is_bounded_nonbinding_and_never_cached() -> None:
    contract = load_json("runtime/contracts/choice-presentation.json")
    counts = contract["suggested_choice_count"]
    assert 2 <= counts["minimum"] <= counts["maximum"] <= 5
    assert contract["default_visible_total"] == 6
    assert contract["default_horizon_mix"] == {
        "immediate": 3,
        "wider_horizon": 2,
        "free_action": 1,
    }
    assert contract["free_form_option_required"] is True
    assert contract["duration_required_for_every_suggested_choice"] is False
    assert "nonbinding" in contract["menu_rule"]
    assert "no default or recommended option" in contract["menu_rule"]
    assert "hidden" in contract["information_rule"]
    assert "Never cache" in contract["storage_rule"]

    scene = load_json("state/scene.json")
    forbidden_cached_menu_keys = {
        "decision_packages",
        "action_packages",
        "next_action",
        "choice_menu",
        "suggested_choices",
    }
    assert forbidden_cached_menu_keys.isdisjoint(scene)
    assert forbidden_cached_menu_keys.isdisjoint(scene.get("narrative", {}))


def test_scene_modules_encode_distinct_narrative_risks() -> None:
    expected_phrases = {
        "combat.md": ("spatial ledger", "genuine player decision"),
        "covert.md": ("bounded perception", "hidden complication"),
        "command-war.md": ("Attribute information to its channel", "imperfect information"),
        "social-village.md": ("ordinary life", "No NPC exists merely"),
        "training-recovery.md": ("Do not narrate every repetition", "not a dashboard"),
        "family-politics.md": ("Preserve Wei's intent", "durable consequence"),
    }
    base = ROOT / "runtime/contracts/narration"
    for filename, phrases in expected_phrases.items():
        text = (base / filename).read_text(encoding="utf-8")
        for phrase in phrases:
            assert phrase in text
