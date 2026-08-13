from __future__ import annotations

from types import SimpleNamespace

import pytest

from shinobi_runtime.combat import PersonnelState
from shinobi_runtime.reducers.health import (
    HealthResolutionError,
    apply_personnel_effect,
    settle_recovery,
)


POLICY = {
    "health_per_day_milli_capacity": 100,
    "chakra_per_hour_milli_capacity": 100,
    "fatigue_per_hour_milli_capacity": 100,
    "strain_per_hour_milli_capacity": 100,
    "incapacitated_health_multiplier_milli": 500,
    "minimum_wound_clear_hours": 24,
}


_MISSING = object()


def shinobi_person(*, injuries_marker=object()):
    condition = {"health": "healthy", "fatigue": 0, "readiness": "ready"}
    if injuries_marker is not _MISSING:
        condition["injuries"] = injuries_marker
    return {
        "life_status": "alive",
        "resources": {
            "health": {"capacity": 40, "current": 40},
            "chakra": {"capacity": 100, "current": 100},
            "fatigue": {"capacity": 100, "current": 0},
            "strain": {"safe_capacity": 40, "current": 0},
        },
        "condition": condition,
    }


def compact_person() -> dict:
    return {
        "schema": "person",
        "stats": {
            "resources": {
                "health": {"capacity": 60, "current": 60},
                "chakra": {"capacity": 80, "current": 80},
                "fatigue": {"capacity": 100, "current": 0},
                "strain": {"safe_capacity": 50, "current": 0},
            }
        },
        "health": {"status": "healthy", "fatigue": 0},
    }


def effect(*, wounded=0, incapacitated=0, killed=0, fatigue=20):
    return SimpleNamespace(
        after_resources=(SimpleNamespace(resource_ref="fatigue", current=fatigue),),
        after_personnel=PersonnelState(
            total=1,
            active=0 if (wounded or incapacitated or killed) else 1,
            wounded=wounded,
            incapacitated=incapacitated,
            killed=killed,
        ),
    )


def test_missing_exact_person_injury_ledger_normalizes_to_empty() -> None:
    record = shinobi_person(injuries_marker=_MISSING)
    result = settle_recovery(record, elapsed_seconds=3600, policy=POLICY)
    assert record["condition"]["injuries"] == []
    assert result["before"]["injuries"] == []
    assert result["after"]["injuries"] == []


def test_explicit_malformed_exact_person_injury_ledger_still_fails_closed() -> None:
    record = shinobi_person(injuries_marker="not-a-list")
    with pytest.raises(HealthResolutionError, match="person injuries are invalid"):
        settle_recovery(record, elapsed_seconds=3600, policy=POLICY)


def test_compact_person_can_receive_wound_without_second_injury_ledger() -> None:
    record = compact_person()
    apply_personnel_effect(
        record,
        effect=effect(wounded=1, fatigue=25),
        event_marker="mission.test@SE-0061-02-12T07:00:00",
    )
    assert record["health"] == {"status": "wounded", "fatigue": 25}
    assert record["stats"]["resources"]["health"]["current"] == 45
    assert "condition" not in record
    assert "life_status" not in record


def test_compact_person_incapacitation_recovers_through_shared_health_domain() -> None:
    record = compact_person()
    apply_personnel_effect(
        record,
        effect=effect(incapacitated=1, fatigue=30),
        event_marker="mission.test@SE-0061-02-12T07:00:00",
    )
    assert record["health"]["status"] == "incapacitated"
    assert record["stats"]["resources"]["health"]["current"] == 20
    result = settle_recovery(record, elapsed_seconds=24 * 60 * 60, policy=POLICY)
    assert result["before"]["readiness"] == "incapacitated"
    assert record["health"]["fatigue"] == record["stats"]["resources"]["fatigue"]["current"]
    assert result["after"]["readiness"] in {"incapacitated", "injured"}


def test_compact_person_death_uses_existing_health_status_authority() -> None:
    record = compact_person()
    apply_personnel_effect(
        record,
        effect=effect(killed=1),
        event_marker="mission.test@SE-0061-02-12T07:00:00",
    )
    assert record["health"]["status"] == "dead"
    assert record["stats"]["resources"]["health"]["current"] == 0
    assert "life_status" not in record
