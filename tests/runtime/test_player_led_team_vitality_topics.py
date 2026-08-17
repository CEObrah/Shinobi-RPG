from __future__ import annotations

from shinobi_runtime.commands.living_world_team_vitality import _leadership_topic_cues


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
