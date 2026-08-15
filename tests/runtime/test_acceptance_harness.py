from __future__ import annotations

import json

from shinobi_runtime.acceptance import OperationalBudgets, run_acceptance


EXPECTED_PHASES = (
    "contract_and_budget_preflight",
    "mixed_campaign_20_turns",
    "advance_1_year",
    "advance_5_years",
    "advance_10_years",
    "stale_rostered_person_catch_up",
    "inactive_region_and_front_progression",
    "large_scaled_battle",
    "thousand_sequential_turn_reductions",
    "replay_and_temp_git_root_acceptance",
)


def test_phase_zero_through_nine_runtime_acceptance(tmp_path) -> None:
    budgets = OperationalBudgets()
    summary = run_acceptance(tmp_path, budgets=budgets)
    record = summary.to_record()

    assert summary.passed
    assert tuple(phase.name for phase in summary.phases) == EXPECTED_PHASES
    assert tuple(phase.phase for phase in summary.phases) == tuple(range(10))
    assert summary.final_root_sha256 == summary.replay_root_sha256
    assert summary.elapsed_milliseconds < budgets.max_runtime_milliseconds

    mixed = summary.phases[1].metrics
    assert mixed["turns"] == 20
    assert mixed["final_revision"] == 20
    assert mixed["revision_delta_per_turn"] == 1
    assert mixed["max_touched_owners"] <= budgets.max_touched_owners_per_turn
    assert mixed["population_total"] == 120
    assert mixed["resource_total"] == 1100
    assert mixed["population_delta"] == mixed["resource_delta"] == 0
    assert not mixed["information_leakage_detected"]

    for phase in summary.phases[2:5]:
        assert phase.metrics["pending_overdue"] == 0
        assert phase.metrics["pending_future"] <= budgets.max_pending_events_after_closure
        assert phase.metrics["unsafe_hosts"] == 0
        assert phase.metrics["public_fact_count"] == 0
        assert (
            phase.metrics["max_touched_owners_per_event"]
            <= budgets.max_touched_owners_per_event
        )
        assert phase.metrics["processed_events"] <= budgets.long_horizon_event_budget

    stale = summary.phases[5].metrics
    assert stale["resolved_through_before"] < stale["resolved_through_after"]
    assert stale["pending_overdue"] == 0
    assert stale["pending_future"] <= budgets.max_pending_events_after_closure
    assert stale["processed_events"] <= budgets.stale_person_event_budget

    inactive = summary.phases[6].metrics
    assert inactive["hosts"] == 2
    assert inactive["pending_overdue"] == 0
    assert inactive["pending_future"] <= budgets.max_pending_events_after_closure
    assert inactive["public_fact_count"] == 0
    assert inactive["processed_events"] <= budgets.inactive_host_event_budget

    battle = summary.phases[7].metrics
    assert battle["resolution_mode"] == "kernel"
    assert battle["participants"] == budgets.large_battle_participants
    assert battle["engagements"] == budgets.large_battle_engagements
    assert battle["touched_owners"] <= budgets.large_battle_max_touched_owners
    assert battle["personnel_after"] == battle["personnel_before"]
    assert battle["resources_after"] <= battle["resources_before"]
    assert battle["objective_achieved"]

    stress = summary.phases[8].metrics
    assert stress["turns"] == 1000
    assert stress["final_revision"] == 1000
    assert stress["revision_delta_per_turn"] == 1
    assert stress["root_count"] == 1001
    assert stress["max_touched_owners"] <= budgets.max_touched_owners_per_turn
    assert stress["population_total"] == 120
    assert stress["resource_total"] == 1100
    assert stress["population_delta"] == stress["resource_delta"] == 0
    assert not stress["information_leakage_detected"]

    replay = summary.phases[9].metrics
    assert replay["turn_root_sequence_equal"]
    assert replay["owner_bytes_equal"]
    assert replay["ten_year_frontier_equal"]
    assert replay["disk_root_sha256"] == summary.final_root_sha256
    assert replay["commit_count"] == 2
    assert replay["repository_pristine"]

    encoded = json.dumps(record, sort_keys=True)
    assert json.loads(encoded) == record
    assert "secret:" not in encoded
    assert record["schema"] == "shinobi.runtime-acceptance-result"
    assert record["version"] == 1
