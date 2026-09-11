from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_failure_discovery_lab_runs_real_black_lance_and_handoff_chains_before_broad_release():
    lab = runpy.run_path(str(ROOT / "tools/playability_lab.py"))
    selectors = set(lab["HIGH_VALUE_SELECTORS"])
    assert {
        "tests/current/test_combat_command_wrapper.py::test_live_combat_start_and_bare_attack_exchange_preview_and_plan_end_to_end",
        "tests/current/test_combat_gameplay_certification.py::test_real_black_lance_12v18_replay_is_contested_not_a_medic_or_short_reach_exploit",
        "tests/current/test_live_combat_ally_support.py::test_canonical_active_combat_accepts_compound_player_attack_and_medic_treat_order",
        "tests/current/test_combat_parley_transaction.py::test_combat_side_parley_and_reply_are_transaction_valid_and_legacy_compatible",
        "tests/current/test_transition_handoff_projection.py::test_matching_hostile_contact_handoff_is_superseded_by_exact_active_combat",
        "tests/current/test_main_branch_bootstrap.py::test_replacement_release_baseline_can_reset_a_newer_stale_persistent_volume",
        "tests/current/test_main_branch_bootstrap.py::test_fast_forward_release_baseline_change_is_verified_and_retires_old_receipts",
    }.issubset(selectors)


def test_playability_lab_does_not_run_duplicate_high_value_scenarios():
    lab = runpy.run_path(str(ROOT / "tools/playability_lab.py"))
    selectors = tuple(lab["HIGH_VALUE_SELECTORS"])
    assert len(selectors) == len(set(selectors))


def test_playability_lab_has_hard_timeouts_and_disables_plugin_noise():
    source = (ROOT / "tools/playability_lab.py").read_text(encoding="utf-8")
    assert "except subprocess.TimeoutExpired" in source
    assert "start_new_session=True" in source
    assert "os.killpg(proc.pid, signal.SIGKILL)" in source
    assert "for selector in HIGH_VALUE_SELECTORS:" in source
    assert 'env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"' in source
    assert 'env["PYTHONDONTWRITEBYTECODE"] = "1"' in source
    assert '[sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets]' in source
    assert "pytest_exit_runner.py" not in source
    assert not (ROOT / "tools/pytest_exit_runner.py").exists()


def test_changed_path_router_runs_lab_scope_contract_for_lab_edits():
    changed = runpy.run_path(str(ROOT / "tools/test_changed.py"))
    selected = set(changed["select"](["tools/playability_lab.py"]))
    assert "tests/playability/test_playability_lab_scope.py" in selected
