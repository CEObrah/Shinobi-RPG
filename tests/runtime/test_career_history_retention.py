from shinobi_runtime.commands.career_history_retention import (
    active_exam_cycle_ids,
    compact_career_history,
)


def _phase(cycle_id: str, phase: str, serial: int) -> dict:
    return {
        "kind": "promotion_exam_cycle_phase",
        "at": f"SE-0061-07-{serial:02d}T07:00:00",
        "cycle_id": cycle_id,
        "profile_ref": "promotion_exam.konoha.chunin",
        "phase": phase,
    }


def test_retention_preserves_every_row_for_large_active_exam():
    cycle = "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07"
    history = [
        {"kind": "aggregate_rank_progression", "serial": index}
        for index in range(700)
    ]
    history.append(_phase(cycle, "registration", 1))
    history.extend(
        {
            "kind": "promotion_exam_evaluation",
            "cycle_id": cycle,
            "phase": "qualification",
            "candidate_ref": f"char.candidate.{index}",
            "outcome": "pass",
            "score": 100,
            "threshold": 78,
        }
        for index in range(900)
    )
    history.append(_phase(cycle, "finals", 22))

    removed = compact_career_history(history, non_active_limit=512)

    assert removed == 188
    assert active_exam_cycle_ids(history) == {cycle}
    assert sum(1 for row in history if row.get("cycle_id") == cycle) == 902
    old_rows = [row for row in history if row.get("kind") == "aggregate_rank_progression"]
    assert len(old_rows) == 512
    assert old_rows[0]["serial"] == 188


def test_closed_exam_rows_become_compactable_hot_history():
    cycle = "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07"
    history = [_phase(cycle, "registration", 1)]
    history.extend(
        {
            "kind": "promotion_exam_evaluation",
            "cycle_id": cycle,
            "phase": "qualification",
            "candidate_ref": f"char.candidate.{index}",
            "outcome": "pass",
            "score": 100,
            "threshold": 78,
        }
        for index in range(700)
    )
    history.append(_phase(cycle, "closed", 24))

    removed = compact_career_history(history, non_active_limit=512)

    assert active_exam_cycle_ids(history) == set()
    assert removed == 190
    assert len(history) == 512
    assert history[-1]["phase"] == "closed"


def test_multiple_active_cycles_are_all_protected():
    first = "promotion_exam_cycle.a.0061-07"
    second = "promotion_exam_cycle.b.0061-07"
    history = [_phase(first, "registration", 1), _phase(second, "registration", 2)]
    history.extend({"kind": "promotion_exam_registration", "cycle_id": first, "candidate_refs": [f"a{n}"]} for n in range(600))
    history.extend({"kind": "promotion_exam_registration", "cycle_id": second, "candidate_refs": [f"b{n}"]} for n in range(600))

    removed = compact_career_history(history, non_active_limit=1)

    assert removed == 0
    assert active_exam_cycle_ids(history) == {first, second}
