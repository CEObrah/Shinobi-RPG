from shinobi_runtime.api.player_promotion_exam_participation_projection import _cycle_registration_counts


CYCLE_ID = "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07"


def test_cycle_registration_counts_cover_all_exact_teams_without_exposing_identity_details():
    pipeline = {
        "schema": "shinobi-career-pipeline",
        "version": 1,
        "history": [
            {
                "kind": "promotion_exam_registration",
                "cycle_id": CYCLE_ID,
                "team_ref": "team.konoha.fujin",
                "candidate_refs": ["char.kai", "char.mei", "char.riku"],
            },
            {
                "kind": "promotion_exam_registration",
                "cycle_id": CYCLE_ID,
                "team_ref": "team.konoha.guy",
                "candidate_refs": ["canon_lee", "canon_neji", "canon_tenten"],
            },
            {
                "kind": "promotion_exam_registration",
                "cycle_id": "promotion_exam_cycle.other",
                "team_ref": "team.other",
                "candidate_refs": ["char.other"],
            },
        ],
    }

    assert _cycle_registration_counts(pipeline, CYCLE_ID) == (2, 6)
