import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands import promotion_exam_evaluation as evaluation
from shinobi_runtime.commands import promotion_exam_finals as finals


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
            "components": [{"path": "attributes.awareness", "weight": 1}],
        },
    },
    "finals_format": {
        "model": "single_elimination",
        "venue_ref": "place.konoha.academy.assignment.hall",
    },
}


def pipeline(*rows):
    return {"schema": "shinobi-career-pipeline", "version": 1, "history": list(rows)}


def registration(*candidate_refs):
    return {
        "kind": "promotion_exam_registration",
        "cycle_id": CYCLE_ID,
        "candidate_refs": list(candidate_refs),
    }


def field_result(candidate_ref, outcome):
    return {
        "kind": "promotion_exam_evaluation",
        "cycle_id": CYCLE_ID,
        "phase": "field_evaluation",
        "candidate_ref": candidate_ref,
        "score": 90 if outcome == "pass" else 70,
        "threshold": 82,
        "outcome": outcome,
    }


def settled_bout(open_bout, winner_ref):
    candidates = list(open_bout["candidate_refs"])
    loser_ref = next(ref for ref in candidates if ref != winner_ref)
    return {
        "kind": "promotion_exam_bout",
        "cycle_id": CYCLE_ID,
        "bout_ref": open_bout["bout_ref"],
        "round_index": open_bout["round_index"],
        "match_index": open_bout["match_index"],
        "candidate_refs": candidates,
        "winner_ref": winner_ref,
        "loser_ref": loser_ref,
    }


def test_finals_use_field_passers_not_another_score_gate():
    state = pipeline(
        registration("char.a", "char.b", "char.c"),
        field_result("char.a", "pass"),
        field_result("char.b", "fail"),
        field_result("char.c", "pass"),
    )

    assert finals.promotion_exam_finals_candidate_refs(state, CYCLE_ID) == (
        "char.a",
        "char.c",
    )
    with pytest.raises(CommandRejectedError) as exc:
        evaluation._evaluation_config(PROFILE, "finals")
    assert exc.value.code == "promotion_exam_stage_not_evaluable"


def test_single_elimination_bracket_persists_rounds_byes_and_champion():
    state = pipeline(
        registration("char.a", "char.b", "char.c"),
        field_result("char.a", "pass"),
        field_result("char.b", "pass"),
        field_result("char.c", "pass"),
    )

    initial = finals.promotion_exam_finals_state(state, PROFILE, CYCLE_ID)
    repeated = finals.promotion_exam_finals_state(state, PROFILE, CYCLE_ID)
    assert initial == repeated
    assert initial["complete"] is False
    assert len(initial["open_bouts"]) == 1
    assert initial["open_bouts"][0]["round_index"] == 1

    semifinal = initial["open_bouts"][0]
    semifinal_winner = semifinal["candidate_refs"][0]
    state["history"].append(settled_bout(semifinal, semifinal_winner))

    after_semifinal = finals.promotion_exam_finals_state(state, PROFILE, CYCLE_ID)
    assert after_semifinal["complete"] is False
    assert len(after_semifinal["open_bouts"]) == 1
    championship = after_semifinal["open_bouts"][0]
    assert championship["round_index"] == 2
    assert semifinal_winner in championship["candidate_refs"]

    champion = championship["candidate_refs"][1]
    state["history"].append(settled_bout(championship, champion))

    completed = finals.promotion_exam_finals_state(state, PROFILE, CYCLE_ID)
    assert completed["complete"] is True
    assert completed["open_bouts"] == []
    assert completed["champion_ref"] == champion
    assert finals.promotion_exam_finals_complete(state, PROFILE, CYCLE_ID) is True


def test_tampered_persisted_pairing_is_rejected():
    state = pipeline(
        registration("char.a", "char.b"),
        field_result("char.a", "pass"),
        field_result("char.b", "pass"),
    )
    open_bout = finals.promotion_exam_finals_state(state, PROFILE, CYCLE_ID)["open_bouts"][0]
    row = settled_bout(open_bout, open_bout["candidate_refs"][0])
    row["candidate_refs"] = list(reversed(row["candidate_refs"]))
    state["history"].append(row)

    with pytest.raises(CommandRejectedError) as exc:
        finals.promotion_exam_finals_state(state, PROFILE, CYCLE_ID)
    assert exc.value.code == "promotion_exam_bout_bracket_conflict"
