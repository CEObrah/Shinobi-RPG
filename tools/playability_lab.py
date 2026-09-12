#!/usr/bin/env python3
"""Run failure-discovery gameplay tests before broad Shinobi verification."""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HIGH_VALUE_SELECTORS = (
    "tests/current/test_combat_command_wrapper.py::test_live_combat_start_and_bare_attack_exchange_preview_and_plan_end_to_end",
    "tests/current/test_combat_command_wrapper.py::test_until_resolution_scope_finishes_or_returns_explicit_continuation_frontier",
    "tests/current/test_combat_gameplay_certification.py::test_real_black_lance_12v18_replay_is_contested_not_a_medic_or_short_reach_exploit",
    "tests/current/test_live_combat_ally_support.py::test_canonical_active_combat_accepts_compound_player_attack_and_medic_treat_order",
    "tests/current/test_combat_friendly_line_safety.py::test_autonomous_melee_is_withheld_if_friendly_crosses_lane_before_resolution",
    "tests/current/test_combat_parley_transaction.py::test_combat_side_parley_and_reply_are_transaction_valid_and_legacy_compatible",
    "tests/current/test_semantic_wait_and_time_flow.py::test_wait_policy_keeps_one_reason_precise_and_supports_distinct_any_of_reasons",
    "tests/current/test_transition_handoff_projection.py::test_matching_hostile_contact_handoff_is_superseded_by_exact_active_combat",
    "tests/current/test_main_branch_bootstrap.py::test_replacement_release_baseline_can_reset_a_newer_stale_persistent_volume",
    "tests/current/test_main_branch_bootstrap.py::test_fast_forward_release_baseline_change_is_verified_and_retires_old_receipts",
)


def _run_pytest(env: dict[str, str], targets: list[str], *, timeout: int = 300) -> int:
    proc = subprocess.Popen(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets],
        cwd=ROOT,
        env=env,
        start_new_session=True,
    )
    try:
        return int(proc.wait(timeout=timeout))
    except subprocess.TimeoutExpired:
        print(f"PLAYABILITY LAB TIMEOUT: {' '.join(targets)}", flush=True)
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        return 124


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-mutations", action="store_true")
    parser.add_argument("--no-integration", action="store_true")
    args = parser.parse_args()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "runtime")
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    code = _run_pytest(env, ["tests/playability"])
    if code:
        return code
    if not args.no_integration:
        for selector in HIGH_VALUE_SELECTORS:
            print(f"PLAYABILITY SCENARIO: {selector}", flush=True)
            code = _run_pytest(env, [selector], timeout=120)
            if code:
                return code
    if not args.no_mutations:
        second = subprocess.run([sys.executable, "tools/mutation_audit.py"], cwd=ROOT, env=env, check=False)
        if second.returncode:
            return int(second.returncode)
    print("PLAYABILITY LAB PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
