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


def test_sword_manor_intake_freezes_new_members_as_persistent_individual_profiles() -> None:
    import json
    from pathlib import Path

    from shinobi_runtime.commands.campaign_intake_onboarding import CampaignCommandPlanner
    from shinobi_runtime.commands.envelope import CommandEnvelope
    from shinobi_runtime.people.profiles import numeric_map, profile_entry_for
    from shinobi_runtime.sim.events import CampaignTime
    from shinobi_runtime.store import RepositoryStore

    root = Path(__file__).resolve().parents[2]
    repo = RepositoryStore(root)
    meta = repo.read_json("state/meta.json")
    planner = CampaignCommandPlanner(repo)
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="release-intake-profile-regression",
        actor_id="char.zhu",
        command_type="institution_intake_resolution",
        expected_revision=meta["revision"],
        submitted_at="2026-08-14T00:00:00Z",
        payload={
            "institution_ref": "house.tang",
            "source_pool_id": "pool.konoha.civilian_general",
            "applicant_count": 4,
            "policy_ref": "recruitment.sword_manor_disciple",
            "summary": "Regression intake",
            "visibility": "restricted",
        },
    )
    plan = planner._institution_intake_resolution(
        command, meta, CampaignTime.parse(meta["time"])
    )
    assert plan.result["character_lite_profiles"] == "persistent_individual_profiles"
    assert plan.result["cohort_summary_authority"] == "derived_from_persistent_individuals"

    registry = json.loads(plan.writes["state/person-core/house-tang.json"])
    house = json.loads(plan.writes["state/house/tang.json"])
    refs = plan.result["new_member_refs"]
    assert refs
    for ref in refs:
        assert registry["people"][ref]["id"] == ref
        entry = profile_entry_for(registry, ref)
        assert entry["person_ref"] == ref
        assert entry["institutional_progression"]["standing"] == "junior_disciple"
        assert entry["institutional_progression"]["training_package_refs"] == ["PKG_HT_JUNIOR"]
        assert numeric_map(registry, entry)["stats.martial_skills.sword"] >= 0

    cohort = next(row for row in house["cohorts"] if row["id"] == plan.result["cohort_ref"])
    assert cohort["aggregate_count"] == len(refs)
    assert cohort["cohort_profile"]["development"]["model"] == "derived_from_persistent_individuals"
    assert "age_years" not in cohort["cohort_profile"]["numeric_distributions"]
