from __future__ import annotations

import json
from pathlib import Path

from shinobi_runtime.commands.standing_training_participation import _registered_training_instructors


def test_blackhound_uses_full_sustainable_zhu_linh_development_envelope() -> None:
    root = json.loads(
        Path("game/rules/training/autonomy-participation.json").read_text(encoding="utf-8")
    )
    policy = root["policies"]["team.blackhound"]

    assert policy["enabled"] is True
    assert policy["participates_in_autonomous_training"] is False
    assert policy["active_hours_per_week"] == 48
    assert policy["shared_core_active_hours_per_week"] == 34
    assert policy["supplemental_individual_active_hours_per_week"] == 14
    assert policy["full_training_day_hours"] * 5 + policy["taper_day_hours"] == 34
    assert (
        policy["shared_core_active_hours_per_week"]
        + policy["supplemental_individual_active_hours_per_week"]
        == policy["active_hours_per_week"]
    )
    assert policy["recovery_days_per_cycle"] == 1
    assert policy["assembly_location_ref"] == "place.sword_manor"
    assert policy["instructor_strategy"] == "replace_team_instructors"
    assert policy["instructor_refs"] == ["char.zhu", "char.linh"]
    assert "canon_hayama_shirakumo" not in policy["instructor_refs"]
    assert policy["target_strategy"] == "weakness_strength_balanced"

    solo = {
        "attributes.strength",
        "attributes.toughness",
        "martial_skills.movement",
        "martial_skills.unarmed",
        "martial_skills.sword",
    }
    tactical = {
        "operational_skills.infiltration",
        "operational_skills.investigation",
        "operational_skills.tactics",
        "operational_skills.team_coordination",
        "operational_skills.tracking",
    }
    assert solo.issubset(set(policy["assessment_paths"]))
    assert tactical.issubset(set(policy["assessment_paths"]))

    assert policy["player_joint_active_hours_per_week"] == 48
    assert policy["player_joint_shared_core_active_hours_per_week"] == 34
    assert policy["player_supplemental_active_hours_per_week"] == 14
    assert (
        policy["player_joint_shared_core_active_hours_per_week"]
        + policy["player_supplemental_active_hours_per_week"]
        == policy["player_joint_active_hours_per_week"]
    )
    assert policy["player_joint_target_cycle"][:7] == [
        "operational_skills.leadership",
        "operational_skills.team_coordination",
        "operational_skills.investigation",
        "operational_skills.tracking",
        "operational_skills.traps",
        "chakra_dimensions.hand_seal_speed",
        "chakra_dimensions.sensing",
    ]


def test_blackhound_policy_replaces_saved_hayama_instruction() -> None:
    policy = {
        "instructor_strategy": "replace_team_instructors",
        "instructor_refs": ["char.zhu", "char.linh"],
    }
    assert _registered_training_instructors(
        policy, ["pc_wei_tang", "canon_hayama_shirakumo"]
    ) == ("char.zhu", "char.linh")
