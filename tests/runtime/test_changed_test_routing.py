from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "shinobi_test_changed",
    ROOT / "tools" / "test_changed.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _selected(path: str) -> set[str]:
    return set(MODULE.select([path]))


def test_api_context_changes_route_to_budget_compaction_regressions() -> None:
    selected = _selected("runtime/shinobi_runtime/api/command_discovery.py")

    assert "tests/runtime/test_api_models.py" in selected
    assert "tests/runtime/test_command_discovery.py" in selected
    assert "tests/runtime/test_play_context_wire_contract.py" in selected
    assert "tests/runtime/test_player_activity_handoff_projection.py" in selected
    assert "tests/runtime/test_mcp_plugin.py" in selected
    assert "tests/runtime/test_mcp_reliability.py" in selected


def test_campaign_environment_changes_route_to_wire_and_activity_handoff_regressions() -> None:
    selected = _selected("runtime/shinobi_runtime/api/campaign_environment.py")

    assert "tests/runtime/test_play_context_wire_contract.py" in selected
    assert "tests/runtime/test_player_activity_handoff_projection.py" in selected
    assert "tests/runtime/test_environment.py" in selected


def test_player_led_team_vitality_changes_route_to_leadership_agenda_regression() -> None:
    selected = _selected(
        "runtime/shinobi_runtime/commands/living_world_team_vitality.py"
    )

    assert "tests/runtime/test_player_led_team_vitality_topics.py" in selected
    assert "tests/runtime/test_team_intelligence.py" in selected


def test_promotion_exam_result_read_changes_route_to_paged_result_regression() -> None:
    selected = _selected(
        "runtime/shinobi_runtime/api/player_promotion_exam_results_read.py"
    )

    assert "tests/runtime/test_promotion_exam_results_read.py" in selected
    assert "tests/runtime/test_promotion_exam_public_results.py" in selected


def test_mcp_change_routes_to_reliability_regression() -> None:
    selected = _selected("runtime/shinobi_runtime/api/mcp.py")

    assert "tests/runtime/test_mcp_reliability.py" in selected
    assert "tests/runtime/test_mcp_plugin.py" in selected


def test_procedure_time_policy_routes_to_causal_time_regressions() -> None:
    selected = _selected("game/data/mechanics/procedure-time.json")

    assert "tests/runtime/test_procedure_time_resolution.py" in selected
    assert "tests/runtime/test_advance_until_event.py" in selected


def test_mission_progress_changes_route_to_main_and_archive_regressions() -> None:
    selected = _selected("runtime/shinobi_runtime/commands/mission_progression.py")

    assert "tests/runtime/test_mission_progression.py" in selected
    assert "tests/runtime/test_mission_progression_archive_guard.py" in selected
    assert "tests/runtime/test_missions.py" in selected
