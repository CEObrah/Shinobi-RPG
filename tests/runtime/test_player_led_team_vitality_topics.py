from __future__ import annotations

from shinobi_runtime.commands.living_world_team_vitality import _leadership_topic_cues
from shinobi_runtime.commands.team_leadership_context import (
    relationship_contact_mode,
    topic_ownership_cues,
)


def test_active_assignment_prioritizes_real_leadership_and_doctrine_gaps() -> None:
    team = {
        "member_refs": ["pc_wei_tang", "char.kai", "char.mei_arakawa", "char.riku_hyuga"],
        "current_assignment_ref": "mission.test",
        "training": {
            "recent_sessions": [
                {
                    "targets": {
                        "char.kai": "operational_skills.tactics",
                        "char.mei_arakawa": "martial_skills.movement",
                        "char.riku_hyuga": "operational_skills.investigation",
                    }
                }
            ]
        },
    }
    profile = {"training_focus": ["formation timing", "tracking discipline"]}
    doctrine = {
        "familiarity": {
            "pc_wei_tang": 90,
            "char.kai": 42,
            "char.mei_arakawa": 78,
            "char.riku_hyuga": 61,
        },
        "training": {
            "role_focus": {
                "char.kai": "assault",
                "char.mei_arakawa": "control",
                "char.riku_hyuga": "reconnaissance",
            }
        },
    }

    topics = _leadership_topic_cues(team, profile, doctrine)

    assert topics == [
        "current assignment readiness, delegation, and contingencies",
        "uneven doctrine familiarity and where leadership attention is needed",
        "integrating recent individual training into team coordination",
    ]
    assert topic_ownership_cues(topics) == [
        "shared_boundary",
        "shared_boundary",
        "team_can_own_routine_preparation",
    ]


def test_recent_mission_becomes_after_action_leadership_agenda() -> None:
    team = {
        "member_refs": ["pc_wei_tang", "char.kai", "char.mei_arakawa"],
        "current_assignment_ref": None,
        "training": {"recent_sessions": []},
    }
    profile = {"training_focus": ["tracking discipline"]}
    history = {
        "last_mission_ref": "mission.test",
        "last_result_at": "SE-0061-08-01T10:00:00",
    }

    topics = _leadership_topic_cues(team, profile, None, history)

    assert topics[0] == "latest mission lessons, delegated ownership, and follow-through"
    assert topic_ownership_cues(topics)[0] == "team_can_own_follow_through"


def test_unassigned_team_surfaces_cross_coverage_before_generic_training() -> None:
    team = {
        "member_refs": ["pc_wei_tang", "char.kai", "char.mei_arakawa"],
        "current_assignment_ref": None,
        "training": {"recent_sessions": []},
    }
    profile = {"training_focus": ["tracking discipline"]}
    doctrine = {
        "familiarity": {
            "pc_wei_tang": 80,
            "char.kai": 75,
            "char.mei_arakawa": 72,
        },
        "training": {
            "role_focus": {
                "char.kai": "assault",
                "char.mei_arakawa": "control",
            }
        },
    }

    topics = _leadership_topic_cues(team, profile, doctrine)

    assert topics == [
        "role cross-coverage, deputy initiative, and succession under pressure",
        "tracking discipline",
        "next training block, readiness, and what the team can own without Wei",
    ]


def test_checkin_agenda_is_bounded_and_deduplicated() -> None:
    team = {
        "member_refs": ["pc_wei_tang", "char.kai"],
        "current_assignment_ref": None,
        "training": {"recent_sessions": []},
    }
    profile = {
        "training_focus": [
            "readiness",
            "readiness",
            "coordination",
            "tracking",
        ]
    }

    topics = _leadership_topic_cues(team, profile, None)

    assert topics == ["readiness", "coordination", "tracking"]
    assert len(topics) == 3


class _RelationshipRepository:
    def __init__(self, edge):
        self.edge = edge

    def read_json(self, path):
        assert path == "state/reg/relationship-edges/char.mei_arakawa.json"
        return {
            "schema": "relationship-edge-shard",
            "source_id": "char.mei_arakawa",
            "relationship_edges": {"rel.test": self.edge},
        }


def test_relationship_changes_observable_contact_mode_without_exposing_scores_or_axis_names() -> None:
    edge = {
        "id": "rel.test",
        "source_id": "char.mei_arakawa",
        "target_id": "pc_wei_tang",
        "trust": 72,
        "respect": 70,
        "current_tension": "none_saved",
    }
    mode = relationship_contact_mode(
        _RelationshipRepository(edge),
        "char.mei_arakawa",
        "pc_wei_tang",
    )
    assert mode == "direct_concise"
    assert "72" not in mode
    assert "70" not in mode
    assert "trust" not in mode
    assert "respect" not in mode


def test_saved_tension_changes_behavior_without_disclosing_the_tension_label() -> None:
    edge = {
        "id": "rel.test",
        "source_id": "char.mei_arakawa",
        "target_id": "pc_wei_tang",
        "trust": 90,
        "respect": 90,
        "current_tension": "unresolved_professional_disagreement",
    }
    mode = relationship_contact_mode(
        _RelationshipRepository(edge),
        "char.mei_arakawa",
        "pc_wei_tang",
    )
    assert mode == "careful_professional"
    assert "disagreement" not in mode
    assert "tension" not in mode
