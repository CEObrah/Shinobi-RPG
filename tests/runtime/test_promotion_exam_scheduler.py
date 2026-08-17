from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.promotion_exam_scheduler import (
    _candidate_refs,
    active_promotion_exam_cycles,
    next_cycle_phase,
    registered_candidate_refs,
)
from shinobi_runtime.sim.events import CampaignTime

PROFILE = {
    "id": "promotion_exam.konoha.chunin",
    "cycle_start_months": [1, 7],
    "phases": [
        "registration",
        "qualification",
        "field_evaluation",
        "finals",
        "promotion_review",
        "closed",
    ],
}


def pipeline(*rows):
    return {"schema": "shinobi-career-pipeline", "version": 1, "history": list(rows)}


def phase_row(phase="registration"):
    return {
        "kind": "promotion_exam_cycle_phase",
        "cycle_id": "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07",
        "profile_ref": "promotion_exam.konoha.chunin",
        "phase": phase,
    }


def test_exam_opens_only_in_configured_month():
    assert next_cycle_phase(
        PROFILE,
        pipeline(),
        CampaignTime.parse("SE-0061-06-11T09:00:00"),
    ) is None
    cycle, phase = next_cycle_phase(
        PROFILE,
        pipeline(),
        CampaignTime.parse("SE-0061-07-01T09:00:00"),
    )
    assert cycle.endswith("0061-07") and phase == "registration"


def test_exam_advances_one_phase_per_review():
    row = phase_row()
    assert next_cycle_phase(
        PROFILE,
        pipeline(row),
        CampaignTime.parse("SE-0061-07-08T09:00:00"),
    ) == (row["cycle_id"], "qualification")


def test_closed_exam_does_not_reopen_same_month():
    row = phase_row("closed")
    assert next_cycle_phase(
        PROFILE,
        pipeline(row),
        CampaignTime.parse("SE-0061-07-30T09:00:00"),
    ) is None


def test_active_cycle_rehydrates_from_durable_phase_history():
    row = phase_row()
    assert active_promotion_exam_cycles(pipeline(row), (PROFILE,)) == (row,)
    assert active_promotion_exam_cycles(pipeline(phase_row("closed")), (PROFILE,)) == ()


def test_registered_candidate_refs_accumulates_authorized_registration_rows():
    cycle_id = phase_row()["cycle_id"]
    record = pipeline(
        phase_row(),
        {
            "kind": "promotion_exam_registration",
            "at": "SE-0061-07-01T07:00:00",
            "cycle_id": cycle_id,
            "profile_ref": PROFILE["id"],
            "team_ref": "team.konoha.fujin",
            "instructor_ref": "pc_wei_tang",
            "candidate_refs": ["char.kai", "char.mei_arakawa"],
            "canon_status": "campaign_institutional_not_future_canon",
        },
        {
            "kind": "promotion_exam_registration",
            "at": "SE-0061-07-01T07:01:00",
            "cycle_id": cycle_id,
            "profile_ref": PROFILE["id"],
            "team_ref": "team.konoha.fujin",
            "instructor_ref": "pc_wei_tang",
            "candidate_refs": ["char.riku_hyuga"],
            "canon_status": "campaign_institutional_not_future_canon",
        },
    )
    assert registered_candidate_refs(record, cycle_id) == (
        "char.kai",
        "char.mei_arakawa",
        "char.riku_hyuga",
    )


def test_candidate_refs_accept_command_envelope_frozen_json_sequence():
    command = CommandEnvelope(
        campaign_id="test-campaign",
        request_id="req-1",
        actor_id="pc_wei_tang",
        command_type="promotion_exam_registration_resolution",
        expected_revision=1,
        submitted_at="2026-08-16T00:00:00Z",
        payload={
            "candidate_refs": [
                "char.riku_hyuga",
                "char.kai",
                "char.mei_arakawa",
            ]
        },
    )
    frozen = command.payload["candidate_refs"]
    assert isinstance(frozen, tuple)
    assert _candidate_refs(frozen, actor_id=command.actor_id) == (
        "char.kai",
        "char.mei_arakawa",
        "char.riku_hyuga",
    )
