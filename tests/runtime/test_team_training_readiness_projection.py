from shinobi_runtime.api.campaign_route_discovery import project_team_training_readiness
from shinobi_runtime.sim.events import CampaignTime


def _model():
    return {
        "schedule_limits": {
            "cycle_length_days": 7,
            "maximum_hours_per_member_per_week": 48,
            "minimum_recovery_hours": 0,
            "recent_session_limit": 128,
        }
    }


def test_team_training_readiness_exposes_schedule_and_colocation_without_places():
    team = {
        "member_refs": ["member.a", "member.b", "member.c"],
        "training": {
            "facility_refs": ["place.secret.alpha"],
            "recent_sessions": [
                {
                    "started_at": "SE-0061-02-06T21:15:00",
                    "ended_at": "SE-0061-02-06T22:15:00",
                    "active_hours": "1",
                    "member_refs": ["member.a", "member.b"],
                    "instructor_ref": "member.a",
                    "targets": {
                        "member.a": "operational_skills.leadership",
                        "member.b": "operational_skills.team_coordination",
                    },
                }
            ],
        },
    }
    members = {
        "member.a": {"current_location_id": "place.secret.alpha"},
        "member.b": {"current_location_id": "place.secret.alpha"},
        "member.c": {"current_location_id": "place.secret.beta"},
    }
    instructors = {"member.a": members["member.a"]}
    now = CampaignTime.parse("SE-0061-02-07T14:18:21")

    result = project_team_training_readiness(
        team,
        members,
        instructors,
        _model(),
        current_time=now,
    )

    assert result["schedule_limits"]["maximum_hours_per_member_per_week"] == "48"
    assert result["schedule_limits"]["minimum_recovery_hours"] == 0
    assert result["member_recovery"]["member.a"] == {
        "last_session_ended_at": "SE-0061-02-06T22:15:00",
        "recovery_ready_at": "SE-0061-02-06T22:15:00",
        "recovery_ready_now": True,
    }
    assert result["member_recovery"]["member.c"]["recovery_ready_now"] is True
    assert result["next_recovery_eligible_at_for_all_members"] == str(now)
    assert result["all_members_colocated_now"] is False
    assert result["full_team_at_registered_facility_now"] is False
    assert result["can_start_full_team_session_now"] is False
    assert result["target_specific_facility_requirements_require_preview"] is True
    assert result["colocated_member_groups"] == [
        {
            "member_refs": ["member.a", "member.b"],
            "authorized_instructor_refs_present": ["member.a"],
        },
        {
            "member_refs": ["member.c"],
            "authorized_instructor_refs_present": [],
        },
    ]
    assert "place.secret.alpha" not in repr(result)
    assert "place.secret.beta" not in repr(result)


def test_team_training_readiness_marks_full_team_ready_when_constraints_match():
    team = {
        "member_refs": ["member.a", "member.b"],
        "training": {
            "facility_refs": ["place.shared"],
            "recent_sessions": [],
        },
    }
    members = {
        "member.a": {"current_location_id": "place.shared"},
        "member.b": {"current_location_id": "place.shared"},
    }
    result = project_team_training_readiness(
        team,
        members,
        {"member.a": members["member.a"]},
        _model(),
        current_time=CampaignTime.parse("SE-0061-02-07T14:18:21"),
    )

    assert result["all_members_recovery_ready_now"] is True
    assert result["all_members_colocated_now"] is True
    assert result["full_team_authorized_instructor_colocated_now"] is True
    assert result["full_team_at_registered_facility_now"] is True
    assert result["can_start_full_team_session_now"] is True
    assert result["latest_resolved_session"] is None


def test_team_training_readiness_rejects_colocated_wrong_facility_false_positive():
    team = {
        "member_refs": ["member.a", "member.b"],
        "training": {
            "facility_refs": ["place.sword_manor"],
            "recent_sessions": [],
        },
    }
    members = {
        "member.a": {"current_location_id": "place.mission_desk"},
        "member.b": {"current_location_id": "place.mission_desk"},
    }

    result = project_team_training_readiness(
        team,
        members,
        {"member.a": members["member.a"]},
        _model(),
        current_time=CampaignTime.parse("SE-0061-06-20T07:00:00"),
    )

    assert result["all_members_recovery_ready_now"] is True
    assert result["all_members_colocated_now"] is True
    assert result["full_team_authorized_instructor_colocated_now"] is True
    assert result["full_team_at_registered_facility_now"] is False
    assert result["can_start_full_team_session_now"] is False
    assert "place.mission_desk" not in repr(result)
