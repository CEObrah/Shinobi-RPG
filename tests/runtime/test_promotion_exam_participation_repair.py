import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.campaign_promotion_exam_participation_repair import _repair_phase_times
from shinobi_runtime.sim.events import CampaignTime


SCHEDULE = {
    "registration": "SE-0061-07-01T07:00:00",
    "qualification": "SE-0061-07-11T07:00:00",
    "field_evaluation": "SE-0061-07-13T07:00:00",
    "finals": "SE-0061-07-22T07:00:00",
    "promotion_review": "SE-0061-07-23T07:00:00",
    "closed": "SE-0061-07-24T07:00:00",
}


def test_current_cycle_repair_uses_original_phase_times():
    parsed = _repair_phase_times(
        SCHEDULE,
        current_time=CampaignTime.parse("SE-0061-07-22T07:29:58"),
    )
    assert str(parsed["registration"]) == SCHEDULE["registration"]
    assert str(parsed["qualification"]) == SCHEDULE["qualification"]
    assert str(parsed["field_evaluation"]) == SCHEDULE["field_evaluation"]
    assert str(parsed["finals"]) == SCHEDULE["finals"]


def test_current_cycle_repair_rejects_nonchronological_or_future_finals():
    broken = dict(SCHEDULE)
    broken["qualification"] = broken["registration"]
    with pytest.raises(CommandRejectedError):
        _repair_phase_times(
            broken,
            current_time=CampaignTime.parse("SE-0061-07-22T07:29:58"),
        )

    with pytest.raises(CommandRejectedError):
        _repair_phase_times(
            SCHEDULE,
            current_time=CampaignTime.parse("SE-0061-07-21T07:00:00"),
        )
