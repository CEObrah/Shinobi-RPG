from shinobi_runtime.commands import promotion_exam_evaluation as module


CYCLE_ID = "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07"
PROFILE = {
    "id": "promotion_exam.konoha.chunin",
    "phases": [
        "registration",
        "qualification",
        "field_evaluation",
        "finals",
        "promotion_review",
        "closed",
    ],
    "evaluation_stages": {
        "qualification": {
            "threshold": 78,
            "components": [{"path": "attributes.intelligence", "weight": 1}],
        },
        "field_evaluation": {
            "threshold": 82,
            "components": [
                {"path": "attributes.awareness", "weight": 2},
                {"path": "operational_skills.tactics", "weight": 1},
            ],
        },
        "finals": {
            "threshold": 86,
            "components": [{"path": "attributes.composure", "weight": 1}],
        },
    },
}


def pipeline(*rows):
    return {"schema": "shinobi-career-pipeline", "version": 1, "history": list(rows)}


def registration(*candidate_refs):
    return {
        "kind": "promotion_exam_registration",
        "at": "SE-0061-07-01T07:00:00",
        "cycle_id": CYCLE_ID,
        "profile_ref": PROFILE["id"],
        "team_ref": "team.konoha.fujin",
        "instructor_ref": "pc_wei_tang",
        "candidate_refs": list(candidate_refs),
        "canon_status": "campaign_institutional_not_future_canon",
    }


def evaluation(phase, candidate_ref, outcome, score=90, threshold=82):
    return {
        "kind": "promotion_exam_evaluation",
        "at": "SE-0061-07-13T07:00:00",
        "cycle_id": CYCLE_ID,
        "profile_ref": PROFILE["id"],
        "phase": phase,
        "team_ref": "team.konoha.fujin",
        "evaluator_ref": "canon_hiruzen",
        "candidate_ref": candidate_ref,
        "score": score,
        "threshold": threshold,
        "outcome": outcome,
        "canon_status": "campaign_institutional_not_future_canon",
    }


def test_legacy_missing_prior_evaluation_does_not_fabricate_results():
    state = pipeline(registration("char.kai", "char.mei_arakawa", "char.riku_hyuga"))
    assert module.promotion_exam_stage_candidate_refs(
        state, PROFILE, CYCLE_ID, "field_evaluation"
    ) == ("char.kai", "char.mei_arakawa", "char.riku_hyuga")
    assert module.promotion_exam_evaluation_rows(
        state, CYCLE_ID, phase="qualification"
    ) == ()


def test_prior_stage_results_filter_next_stage_entrants():
    state = pipeline(
        registration("char.kai", "char.mei_arakawa", "char.riku_hyuga"),
        evaluation("qualification", "char.kai", "pass", score=90, threshold=78),
        evaluation("qualification", "char.mei_arakawa", "fail", score=74, threshold=78),
        evaluation("qualification", "char.riku_hyuga", "pass", score=88, threshold=78),
    )
    assert module.promotion_exam_stage_candidate_refs(
        state, PROFILE, CYCLE_ID, "field_evaluation"
    ) == ("char.kai", "char.riku_hyuga")


def test_stage_completion_requires_every_stage_entrant():
    state = pipeline(
        registration("char.kai", "char.mei_arakawa"),
        evaluation("field_evaluation", "char.kai", "pass"),
    )
    assert module.promotion_exam_stage_complete(
        state, PROFILE, CYCLE_ID, "field_evaluation"
    ) is False
    state["history"].append(evaluation("field_evaluation", "char.mei_arakawa", "pass"))
    assert module.promotion_exam_stage_complete(
        state, PROFILE, CYCLE_ID, "field_evaluation"
    ) is True


def test_weighted_score_is_deterministic_integer_half_up():
    person = {
        "attributes": {"awareness": 90},
        "operational_skills": {"tactics": 89},
    }
    score, threshold, outcome = module._score_candidate(
        person, PROFILE["evaluation_stages"]["field_evaluation"]
    )
    assert (score, threshold, outcome) == (90, 82, "pass")


def test_install_registers_semantic_command_and_phase_gate(monkeypatch):
    from shinobi_runtime.commands import campaign_player_handoffs
    from shinobi_runtime.commands import promotion_exam_pacing

    module._INSTALLED = False
    original = promotion_exam_pacing._next_phase_due
    module.install_promotion_exam_evaluation()
    assert "promotion_exam_evaluation_resolution" in campaign_player_handoffs.CampaignCommandPlanner.COMMAND_TYPES
    assert getattr(promotion_exam_pacing._next_phase_due, "_promotion_exam_evaluation_gate", False) is True
    promotion_exam_pacing._next_phase_due = original
    module._INSTALLED = False
