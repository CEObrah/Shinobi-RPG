import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_fujin_and_blackhound_use_broad_balanced_curricula():
    record = json.loads(
        (ROOT / "game/rules/training/autonomy-participation.json").read_text()
    )
    policies = record["policies"]
    for team_ref in ("team.konoha.fujin", "team.blackhound"):
        policy = policies[team_ref]
        assert policy["participates_in_autonomous_training"] is False
        assert policy["active_hours_per_week"] == 48
        assert policy["target_strategy"] == "weakness_strength_balanced"
        cycle = set(policy["team_target_cycle"])
        assert "attributes.awareness" in cycle
        assert "chakra_dimensions.control" in cycle
        assert "martial_skills.movement" in cycle
        assert "martial_skills.stealth" in cycle
        assert "operational_skills.tactics" in cycle
        assert "operational_skills.team_coordination" in cycle
        assert len(policy["assessment_paths"]) >= 15


def test_only_blackhound_owns_wei_joint_credit_and_it_spans_both_teams():
    record = json.loads(
        (ROOT / "game/rules/training/autonomy-participation.json").read_text()
    )
    fujin = record["policies"]["team.konoha.fujin"]
    blackhound = record["policies"]["team.blackhound"]
    assert fujin.get("joint_player_training_credit_owner") is not True
    assert blackhound["joint_player_training_credit_owner"] is True
    assert blackhound["joint_player_training_team_refs"] == [
        "team.konoha.fujin",
        "team.blackhound",
    ]
    assert blackhound["player_joint_active_hours_per_week"] == 48
    assert blackhound["player_joint_shared_core_active_hours_per_week"] == 34
    assert blackhound["player_supplemental_active_hours_per_week"] == 14
    cycle = set(blackhound["player_joint_target_cycle"])
    assert {
        "operational_skills.leadership",
        "operational_skills.team_coordination",
        "operational_skills.tactics",
        "martial_skills.sword",
        "domain_proficiencies.wind",
        "martial_skills.stealth",
        "operational_skills.investigation",
        "operational_skills.survival",
    }.issubset(cycle)
