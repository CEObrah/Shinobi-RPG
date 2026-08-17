from shinobi_runtime.commands import promotion_exam_integrity as integrity


CYCLE_ID = "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07"
PROFILE = {
    "id": "promotion_exam.konoha.chunin",
    "finals_format": {"model": "single_elimination"},
}


def pipeline(*rows):
    return {"schema": "shinobi-career-pipeline", "version": 1, "history": list(rows)}


def registration(team_ref, instructor_ref, *candidate_refs):
    return {
        "kind": "promotion_exam_registration",
        "cycle_id": CYCLE_ID,
        "team_ref": team_ref,
        "instructor_ref": instructor_ref,
        "candidate_refs": list(candidate_refs),
    }


def field_result(candidate_ref):
    return {
        "kind": "promotion_exam_evaluation",
        "cycle_id": CYCLE_ID,
        "phase": "field_evaluation",
        "candidate_ref": candidate_ref,
        "score": 90,
        "threshold": 82,
        "outcome": "pass",
    }


def test_same_team_finalists_are_never_paired():
    state = pipeline(
        registration("team.konoha.fujin", "pc_wei_tang", "char.kai", "char.mei", "char.riku"),
        field_result("char.kai"),
        field_result("char.mei"),
        field_result("char.riku"),
    )
    finals = integrity.team_safe_finals_state(state, PROFILE, CYCLE_ID)
    assert finals["complete"] is True
    assert finals["open_bouts"] == []
    assert finals["champion_ref"] is None
    assert set(finals["co_finalist_refs"]) == {"char.kai", "char.mei", "char.riku"}


def test_mixed_teams_pair_only_cross_team():
    state = pipeline(
        registration("team.a", "leader.a", "char.a1", "char.a2", "char.a3"),
        registration("team.b", "leader.b", "char.b1", "char.b2", "char.b3"),
        *(field_result(ref) for ref in ("char.a1", "char.a2", "char.a3", "char.b1", "char.b2", "char.b3")),
    )
    finals = integrity.team_safe_finals_state(state, PROFILE, CYCLE_ID)
    assert finals["complete"] is False
    assert len(finals["open_bouts"]) == 3
    team = {"char.a1": "a", "char.a2": "a", "char.a3": "a", "char.b1": "b", "char.b2": "b", "char.b3": "b"}
    for bout in finals["open_bouts"]:
        left, right = bout["candidate_refs"]
        assert team[left] != team[right]


def test_legacy_active_life_status_is_eligible():
    profile = {"source_rank": "Genin", "service_village": "konoha"}
    person = {
        "schema": "shinobi_character",
        "life_status": "active",
        "official_rank_or_status": "genin",
        "village_or_affiliation": "Konoha",
        "career_state": {"promotion_eligible": True},
    }
    assert integrity._person_matches_profile(person, profile) is True
