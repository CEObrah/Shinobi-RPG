from shinobi_runtime.commands.promotion_exam_scheduler import next_cycle_phase
from shinobi_runtime.sim.events import CampaignTime

PROFILE={"id":"promotion_exam.konoha.chunin","cycle_start_months":[1,7],"phases":["registration","qualification","field_evaluation","finals","promotion_review","closed"]}


def pipeline(*rows):
    return {"schema":"shinobi-career-pipeline","version":1,"history":list(rows)}


def test_exam_opens_only_in_configured_month():
    assert next_cycle_phase(PROFILE,pipeline(),CampaignTime.parse("SE-0061-06-11T09:00:00")) is None
    cycle,phase=next_cycle_phase(PROFILE,pipeline(),CampaignTime.parse("SE-0061-07-01T09:00:00"))
    assert cycle.endswith("0061-07") and phase=="registration"


def test_exam_advances_one_phase_per_review():
    row={"kind":"promotion_exam_cycle_phase","cycle_id":"promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07","profile_ref":"promotion_exam.konoha.chunin","phase":"registration"}
    assert next_cycle_phase(PROFILE,pipeline(row),CampaignTime.parse("SE-0061-07-08T09:00:00"))==(row["cycle_id"],"qualification")


def test_closed_exam_does_not_reopen_same_month():
    row={"kind":"promotion_exam_cycle_phase","cycle_id":"promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07","profile_ref":"promotion_exam.konoha.chunin","phase":"closed"}
    assert next_cycle_phase(PROFILE,pipeline(row),CampaignTime.parse("SE-0061-07-30T09:00:00")) is None
