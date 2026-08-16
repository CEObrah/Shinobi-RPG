from shinobi_runtime.commands.promotion_exam_service_eligibility import service_eligibility_due
from shinobi_runtime.sim.events import CampaignTime


PROFILE = {
    "id": "promotion_exam.konoha.chunin",
    "service_village": "konoha",
    "source_rank": "Genin",
    "eligibility_review": {
        "minimum_source_rank_service_days": 60,
        "requires_active_standard_mission_team": True,
        "requires_ready_condition": True,
    },
}


def person(*, eligible=False, readiness="ready"):
    return {
        "schema": "shinobi_character",
        "life_status": "active",
        "official_rank_or_status": "Genin",
        "village_or_affiliation": "Konoha",
        "career_state": {"promotion_eligible": eligible},
        "condition": {"readiness": readiness},
        "life_course_state": {
            "rank_history": [
                {
                    "at": "SE-0061-03-01T07:00:00",
                    "rank": "Genin",
                    "reason": "Academy graduation",
                }
            ]
        },
    }


def test_source_rank_service_review_matures_only_after_authored_days():
    assert service_eligibility_due(
        person(), PROFILE, CampaignTime.parse("SE-0061-04-29T07:00:00")
    ) is False
    assert service_eligibility_due(
        person(), PROFILE, CampaignTime.parse("SE-0061-05-01T07:00:00")
    ) is True


def test_existing_eligibility_and_unready_state_are_not_rewritten():
    at = CampaignTime.parse("SE-0061-07-01T07:00:00")
    assert service_eligibility_due(person(eligible=True), PROFILE, at) is False
    assert service_eligibility_due(person(readiness="incapacitated"), PROFILE, at) is False
