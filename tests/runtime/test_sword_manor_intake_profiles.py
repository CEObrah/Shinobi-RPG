from __future__ import annotations

from shinobi_runtime.commands.campaign_intake_profiles import _varied_distribution
from shinobi_runtime.people.cohorts import _moment_values


def test_sword_manor_character_lite_profiles_are_stable_and_individualized() -> None:
    summary = _varied_distribution(path="stats.attributes.agility", count=12, mean=62.0)
    assert summary["sd"] > 0
    assert summary["min"] < summary["mean"] < summary["max"]

    first = _moment_values(summary)
    second = _moment_values(summary)
    assert first == second
    assert len(first) == 12
    assert len(set(round(value, 8) for value in first)) > 1


def test_sword_manor_current_resource_fields_do_not_diverge_from_capacity_baseline() -> None:
    summary = _varied_distribution(
        path="stats.resources.health.current", count=12, mean=85.0
    )
    assert summary == {
        "count": 12,
        "mean": 85.0,
        "sd": 0.0,
        "min": 85.0,
        "max": 85.0,
    }
    assert _moment_values(summary) == tuple(85.0 for _ in range(12))


def test_one_person_intake_remains_exactly_representable() -> None:
    summary = _varied_distribution(path="stats.martial_skills.sword", count=1, mean=62.0)
    assert summary["sd"] == 0.0
    assert _moment_values(summary) == (62.0,)
