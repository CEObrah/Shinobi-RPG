from __future__ import annotations

from shinobi_runtime.commands import story_vitality


def test_house_promotion_is_story_event() -> None:
    result = {
        "house_rostered_promotion_reviews": [
            {
                "member_ref": "ht.core.001",
                "from": "junior_disciple",
                "to": "senior_disciple",
                "promoted": True,
            }
        ]
    }
    assert story_vitality._result_has_story_event(result) is True


def test_mature_outreach_is_story_event() -> None:
    result = {
        "commitment_reviews": [
            {
                "commitment_id": "commitment.outreach.abc.00",
                "status": "overdue",
            }
        ]
    }
    assert story_vitality._result_has_story_event(result) is True


def test_delegated_mission_report_is_story_event() -> None:
    result = {
        "autonomous_actions": [
            {
                "kind": "mission_advance",
                "delegated_mission_report": {
                    "mission_id": "mission.offer.test",
                    "recipient_ref": "pc_wei_tang",
                    "delegate_leader_ref": "canon_hayama_shirakumo",
                    "outcome": "succeeded",
                    "routine_consequences": {"casualty_count": 0},
                },
            }
        ]
    }
    reports = story_vitality._delegated_mission_reports(result)
    assert len(reports) == 1
    assert reports[0]["outcome"] == "succeeded"
    assert story_vitality._result_has_story_event(result) is True


def test_routine_house_training_progress_does_not_force_story_stop() -> None:
    result = {
        "house_rostered_individual_progression": [
            {
                "member_ref": "ht.core.001",
                "outcomes": {
                    "stats.martial_skills.sword": {
                        "points_gained": 2,
                    }
                },
            }
        ]
    }
    assert story_vitality._house_training_progressed(result) is True
    assert story_vitality._result_has_story_event(result) is False


def test_non_outreach_overdue_commitment_does_not_force_story_stop() -> None:
    result = {
        "commitment_reviews": [
            {
                "commitment_id": "commitment.some_other_promise",
                "status": "overdue",
            }
        ]
    }
    assert story_vitality._result_has_story_event(result) is False
