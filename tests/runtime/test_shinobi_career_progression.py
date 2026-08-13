from __future__ import annotations

from shinobi_runtime.commands.shinobi_career_progression import (
    _add_academy_graduates,
    _monthly_boundaries,
    _normalize_scheduler_record,
    _settle_one_month,
)
from shinobi_runtime.sim.events import CampaignTime


def _pipeline(*, genin=1000, chunin=100, jonin=10):
    return {
        "schema": "shinobi-career-pipeline",
        "version": 1,
        "last_review_at": "SE-0061-02-06T21:15:00",
        "villages": {
            "konoha": {
                "service_pool_ref": "pool.konoha.shinobi_service",
                "force_ref": "force.konoha.shinobi",
                "rank_counts": {"genin": genin, "chunin": chunin, "jonin": jonin},
                "promotion_credit_ppm": {"genin_to_chunin": 0, "chunin_to_jonin": 0},
            }
        },
        "history": [],
    }


def _rules():
    return {
        "schema": "shinobi-career-progression-rules",
        "aggregate_promotion_rates_ppm_per_review": {
            "genin_to_chunin": 1000,
            "chunin_to_jonin": 300,
        },
    }


def _legacy_scheduler():
    return {
        "schema": "causal-scheduler-registry",
        "owner_id": "runtime.causal_scheduler",
        "owner_type": "causal_scheduler",
        "authority": True,
        "world_time": "SE-0061-06-04T07:00:01",
        "seeded_at": "SE-0061-02-06T21:15:00",
        "bootstrap_source": "test",
        "hosts": {
            "host.person_continuity": {
                "state": {
                    "host_id": "host.person_continuity",
                    "kind": "person_continuity",
                    "resolved_through": "SE-0061-02-06T21:15:00",
                    "safe_through": "SE-0062-01-01T06:59:59",
                    "handler_ref": "causal.scheduler",
                    "rng_namespace": "person_continuity",
                    "next_due": "SE-0062-01-01T07:00:00",
                },
                "authority_kind": "person_continuity",
                "owner_ref": "state/reg/person-continuity.json",
                "metadata": {},
            }
        },
        "events": [
            {
                "event_id": "evt.legacy",
                "kind": "person_continuity.periodic_review",
                "due_at": "SE-0062-01-01T07:00:00",
                "priority": 96,
                "source_host": "host.person_continuity",
                "target_host": "host.person_continuity",
                "payload": {
                    "identity": "person_continuity",
                    "owner_ref": "state/reg/person-continuity.json",
                    "recurrence": {
                        "kind": "calendar_year_start",
                        "clock": "07:00:00",
                        "accrual_mode": "boundary_only",
                    },
                },
                "dedupe_key": "person_continuity.periodic_review:person_continuity",
                "visibility": "hidden",
                "requires_player": False,
                "causation_id": None,
                "correlation_id": None,
            }
        ],
        "metrics": {},
    }


def test_legacy_person_continuity_migrates_to_first_missed_calendar_month():
    migrated = _normalize_scheduler_record(_legacy_scheduler())
    host = migrated["hosts"]["host.person_continuity"]["state"]
    event = migrated["events"][0]
    assert host["next_due"] == "SE-0061-03-01T07:00:00"
    assert host["safe_through"] == "SE-0061-03-01T06:59:59"
    assert event["due_at"] == "SE-0061-03-01T07:00:00"
    assert event["payload"]["recurrence"]["kind"] == "calendar_month_start"
    assert event["payload"]["recurrence"]["accrual_mode"] == "boundary_only"


def test_monthly_boundaries_use_calendar_months_not_thirty_day_intervals():
    record = _pipeline()
    assert [str(value) for value in _monthly_boundaries(record, CampaignTime.parse("SE-0061-06-01T07:00:00"))] == [
        "SE-0061-03-01T07:00:00",
        "SE-0061-04-01T07:00:00",
        "SE-0061-05-01T07:00:00",
        "SE-0061-06-01T07:00:00",
    ]


def test_monthly_promotion_conserves_headcount_and_does_not_rank_skip():
    record = _pipeline(genin=1000, chunin=0, jonin=0)
    result = _settle_one_month(record, rules=_rules(), at=CampaignTime.parse("SE-0061-03-01T07:00:00"))
    counts = record["villages"]["konoha"]["rank_counts"]
    assert counts == {"genin": 999, "chunin": 1, "jonin": 0}
    assert result["headcount_before"] == result["headcount_after"] == 1000


def test_fractional_carry_eventually_allows_rare_jonin_promotion():
    record = _pipeline(genin=0, chunin=1000, jonin=0)
    rules = _rules()
    at = CampaignTime.parse("SE-0061-03-01T07:00:00")
    for _ in range(4):
        _settle_one_month(record, rules=rules, at=at)
        at = at.next_month_start(7, 0, 0)
    counts = record["villages"]["konoha"]["rank_counts"]
    assert counts["jonin"] == 1
    assert counts["chunin"] == 999


def test_academy_intake_is_deduplicated_by_transfer_identity():
    record = _pipeline(genin=10, chunin=2, jonin=1)
    at = CampaignTime.parse("SE-0061-06-01T07:00:00")
    first = _add_academy_graduates(
        record,
        service_pool_ref="pool.konoha.shinobi_service",
        graduates=7,
        at=at,
        transfer_id="population-transfer.graduation.konoha.0061-06",
    )
    second = _add_academy_graduates(
        record,
        service_pool_ref="pool.konoha.shinobi_service",
        graduates=7,
        at=at,
        transfer_id="population-transfer.graduation.konoha.0061-06",
    )
    assert first is not None
    assert second is None
    assert record["villages"]["konoha"]["rank_counts"]["genin"] == 17


def test_jonin_rate_is_strictly_lower_than_chunin_rate_over_same_snapshot():
    record = _pipeline(genin=10000, chunin=10000, jonin=0)
    result = _settle_one_month(record, rules=_rules(), at=CampaignTime.parse("SE-0061-03-01T07:00:00"))
    promoted = result["promotions"]["konoha"]
    assert promoted["genin_to_chunin"] == 10
    assert promoted["chunin_to_jonin"] == 3
