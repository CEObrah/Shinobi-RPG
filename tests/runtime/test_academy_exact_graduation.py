"""State-independent regressions for exact identities inside Academy graduation."""
from __future__ import annotations

from shinobi_runtime.commands.academy_career_sync import (
    _age_years,
    _apply_graduation_career_state,
    _eligible_exact_graduates,
)
from shinobi_runtime.sim.events import CampaignTime


def _subject(ref: str, birth_date: str, *, status: str = "academy") -> dict:
    return {
        "schema": "shinobi_character",
        "owner_id": ref,
        "name": ref,
        "birth_date": birth_date,
        "life_status": "active",
        "condition": {"readiness": "ready"},
        "official_rank_or_status": status,
        "career_state": {"rank": status, "current_rank_or_status": status, "promotion_eligible": True},
        "life_course_state": {
            "rank_history": [],
            "status_history": [],
            "injury_events": [],
            "relationship_events": [],
            "location_history": [],
        },
    }


def test_academy_age_gate_uses_exact_calendar_birth_anchor() -> None:
    before = CampaignTime.parse("SE-0061-02-28T07:00:00")
    after = CampaignTime.parse("SE-0061-03-01T07:00:00")
    assert _age_years("SE-0049-03-01", before) == 11
    assert _age_years("SE-0049-03-01", after) == 12
    assert _age_years("unknown", after) is None


def test_exact_graduate_receives_normal_genin_career_fields_without_replacing_identity() -> None:
    subject = _subject("canon_test_student", "SE-0048-01-01")
    at = CampaignTime.parse("SE-0061-03-01T07:00:00")
    previous = _apply_graduation_career_state(
        subject,
        at=at,
        reason="Qualified through the Academy graduation cycle.",
    )
    assert previous == "academy"
    assert subject["owner_id"] == "canon_test_student"
    assert subject["official_rank_or_status"] == "Genin"
    assert subject["career_state"]["rank"] == "Genin"
    assert subject["career_state"]["current_rank_or_status"] == "Genin"
    assert subject["career_state"]["promotion_eligible"] is False
    assert subject["life_course_state"]["rank_history"][-1]["rank"] == "Genin"
    assert "graduate: Genin" in subject["life_course_state"]["status_history"][-1]


def test_exact_graduate_candidate_selection_is_deterministic_oldest_first_and_excludes_underage() -> None:
    records = {
        "canon_old": _subject("canon_old", "SE-0047-05-01"),
        "canon_same_a": _subject("canon_same_a", "SE-0049-01-01"),
        "canon_same_b": _subject("canon_same_b", "SE-0049-01-01"),
        "canon_underage": _subject("canon_underage", "SE-0052-01-01"),
    }

    class Planner:
        def _resolve_covered_owner_view(self, ref, cache):
            return f"state/char/{ref}.json", "digest", records[ref]

    selected = _eligible_exact_graduates(
        Planner(),
        rostered_refs=["canon_same_b", "canon_underage", "canon_old", "canon_same_a"],
        at=CampaignTime.parse("SE-0061-03-01T07:00:00"),
        minimum_age=12,
        record_writes={},
    )
    refs = [row[1] for row in selected]
    assert refs == ["canon_old", "canon_same_a", "canon_same_b"]
