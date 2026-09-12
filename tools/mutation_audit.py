#!/usr/bin/env python3
"""Prove critical Shinobi regressions can detect intentionally restored bugs.

Each mutant is applied only inside a disposable full repository copy. The
canonical tree and campaign state are never edited. A mutation is considered
killed only when its baseline selector passes in the real candidate, the mutated
source still compiles, and the same selector then fails.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    name: str
    relpath: str
    replacements: tuple[tuple[str, str], ...]
    selector: str


MUTATIONS = (
    Mutation(
        "playability-lab-critical-chain-omission",
        "tools/playability_lab.py",
        (('    "tests/current/test_combat_gameplay_certification.py::test_real_black_lance_12v18_replay_is_contested_not_a_medic_or_short_reach_exploit",\n', ''),),
        'tests/playability/test_playability_lab_scope.py::test_failure_discovery_lab_runs_real_black_lance_and_handoff_chains_before_broad_release',
    ),
    Mutation(
        "deployment-doc-start-command-drift",
        "docs/RUNTIME_SERVICE_DEPLOYMENT.md",
        ((
            "SHINOBI_GIT_BRANCH=main PYTHONPATH=/app/runtime python -m shinobi_runtime.bootstrap",
            "SHINOBI_GIT_BRANCH=main PYTHONPATH=/app/runtime python -m shinobi_runtime.branch_bootstrap",
        ),),
        "tests/playability/test_delivery_chain_contract.py::test_deployment_docs_cannot_drift_from_railway_start_command_again",
    ),
    Mutation(
        "release-checkpoint-deployment-doc-fingerprint-bypass",
        "tools/run_pytest_shards.py",
        ((
            '    "docs/RUNTIME_SERVICE_DEPLOYMENT.md",\n',
            '',
        ),),
        "tests/playability/test_delivery_chain_contract.py::test_resumable_release_evidence_is_invalidated_by_deployment_runbook_changes",
    ),
    Mutation(
        "package-state-exclusion",
        "tools/package_release.py",
        ((
            "return rel.as_posix() not in TRANSIENT_EXACT_PATHS",
            'return rel.as_posix() not in TRANSIENT_EXACT_PATHS and rel.parts[0] != "state"',
        ),),
        "tests/playability/test_release_packaging_contract.py::test_packager_keeps_every_campaign_state_file_and_required_release_surface",
    ),
    Mutation(
        "destroyed-throat-left-alive",
        "runtime/shinobi_runtime/martial_world/health.py",
        ((
            "if structure=='throat' and sd>=int(_data()['structures']['throat']['destruction_threshold']) and not bool(w.get('stabilized')):return 'dying'",
            "if structure=='throat' and sd>=int(_data()['structures']['throat']['destruction_threshold']) and not bool(w.get('stabilized')):return 'alive'",
        ),),
        "tests/playability/test_combat_physics_torture.py::test_neck_anatomy_matrix_distinguishes_generic_contact_from_destroyed_exact_structures",
    ),
    Mutation(
        "exact-trachea-airway-bypass",
        "runtime/shinobi_runtime/martial_world/health.py",
        ((
            "if structure=='trachea' and sd>=int(l['catastrophic_airway_damage']) and not bool(w.get('stabilized')):return 'dying'",
            "if structure=='trachea' and sd>=int(l['catastrophic_airway_damage']) and not bool(w.get('stabilized')):return 'alive'",
        ),),
        "tests/playability/test_combat_physics_torture.py::test_neck_anatomy_matrix_distinguishes_generic_contact_from_destroyed_exact_structures",
    ),
    Mutation(
        "spinal-cord-lethality-bypass",
        "runtime/shinobi_runtime/martial_world/health.py",
        ((
            "if structure in {'spinal_cord'} and sd>=int(l['catastrophic_spinal_cord_damage']):return 'dead'",
            "if structure in {'spinal_cord'} and sd>=int(l['catastrophic_spinal_cord_damage']):return 'alive'",
        ),),
        "tests/playability/test_combat_physics_torture.py::test_neck_anatomy_matrix_distinguishes_generic_contact_from_destroyed_exact_structures",
    ),
    Mutation(
        "cervical-spine-emergency-bypass",
        "runtime/shinobi_runtime/martial_world/health.py",
        ((
            "if structure=='cervical_spine' and sd>=int(_data()['structures']['cervical_spine']['destruction_threshold']) and int(w.get('nerve_damage',0))>=80:return 'dying'",
            "if structure=='cervical_spine' and sd>=int(_data()['structures']['cervical_spine']['destruction_threshold']) and int(w.get('nerve_damage',0))>=80:return 'alive'",
        ),),
        "tests/playability/test_combat_physics_torture.py::test_neck_anatomy_matrix_distinguishes_generic_contact_from_destroyed_exact_structures",
    ),
    Mutation(
        "major-neck-vessel-lethality",
        "runtime/shinobi_runtime/martial_world/health.py",
        ((
            "if structure in {'carotid_artery','jugular_vein'} and sd>=int(l['catastrophic_major_neck_vessel_damage']):return 'dead'",
            "if structure in {'carotid_artery','jugular_vein'} and sd>=int(l['catastrophic_major_neck_vessel_damage']):return 'alive'",
        ),),
        "tests/playability/test_combat_physics_torture.py::test_neck_anatomy_matrix_distinguishes_generic_contact_from_destroyed_exact_structures",
    ),
    Mutation(
        "friendly-lane-schedule-bypass",
        "runtime/shinobi_runtime/martial_world/exact_combat.py",
        ((
            'if blocker is not None:\n            raise ValueError("friendly_attack_lane_blocked")',
            'if blocker is not None:\n            pass',
        ),),
        "tests/playability/test_combat_physics_torture.py::test_autonomous_melee_never_schedules_through_a_same_side_body",
    ),
    Mutation(
        "friendly-lane-dynamic-recheck-bypass",
        "runtime/shinobi_runtime/martial_world/exact_combat.py",
        ((
            'if blocker is not None:\n            return {\n                **event_base,\n                "result": "autonomous_attack_withheld_friendly_lane",\n                "friendly_blocker_ref": blocker,\n                "safety_phase": "pre_resolution_dynamic_lane_recheck",\n            }',
            'if False and blocker is not None:\n            return {\n                **event_base,\n                "result": "autonomous_attack_withheld_friendly_lane",\n                "friendly_blocker_ref": blocker,\n                "safety_phase": "pre_resolution_dynamic_lane_recheck",\n            }',
        ),),
        "tests/current/test_combat_friendly_line_safety.py::test_autonomous_melee_is_withheld_if_friendly_crosses_lane_before_resolution",
    ),
    Mutation(
        "projectile-dynamic-friendly-lane-recheck-bypass",
        "runtime/shinobi_runtime/martial_world/exact_combat.py",
        ((
            '    if str(action.decision_origin) != "player":\n        blocker = _prospective_friendly_blocker(\n            combat=combat, action=action, people=people, equipment_ledger=equipment_ledger,\n        )\n        if blocker is not None:\n            return {\n                **event_base,\n                "result": "autonomous_attack_withheld_friendly_lane",',
            '    if False and str(action.decision_origin) != "player":\n        blocker = _prospective_friendly_blocker(\n            combat=combat, action=action, people=people, equipment_ledger=equipment_ledger,\n        )\n        if blocker is not None:\n            return {\n                **event_base,\n                "result": "autonomous_attack_withheld_friendly_lane",',
        ),),
        "tests/playability/test_combat_physics_torture.py::test_autonomous_projectile_is_withheld_when_ally_crosses_lane_after_declaration",
    ),
    Mutation(
        "replacement-release-baseline-reset-bypass",
        "runtime/shinobi_runtime/bootstrap.py",
        ((
            "if remote_baseline is not None and remote_baseline != local_baseline:",
            "if False and remote_baseline is not None and remote_baseline != local_baseline:",
        ),),
        "tests/current/test_main_branch_bootstrap.py::test_replacement_release_baseline_can_reset_a_newer_stale_persistent_volume",
    ),
    Mutation(
        "fast-forward-release-baseline-reset-bypass",
        "runtime/shinobi_runtime/bootstrap.py",
        ((
            "if remote_baseline is not None and remote_baseline != local_baseline:",
            "if False and remote_baseline is not None and remote_baseline != local_baseline:",
        ),),
        "tests/current/test_main_branch_bootstrap.py::test_fast_forward_release_baseline_change_is_verified_and_retires_old_receipts",
    ),
    Mutation(
        "release-baseline-recovery-store-reuse",
        "runtime/shinobi_runtime/bootstrap.py",
        ((
            '            _retire_recovery_store(\n                settings,\n                retired_head=local_head,\n                new_baseline_id=remote_baseline,\n            )',
            "            pass",
        ),),
        "tests/current/test_main_branch_bootstrap.py::test_fast_forward_release_baseline_change_is_verified_and_retires_old_receipts",
    ),
    Mutation(
        "release-revision-pin-bypass",
        "tools/verify_release_state.py",
        ((
            '        ("player_id", "player_id"),\n        ("revision", "revision"),\n        ("time", "world_time"),',
            '        ("player_id", "player_id"),\n        ("time", "world_time"),',
        ),),
        "tests/current/test_release_campaign_state_contract.py::test_release_contract_rejects_revision_106_style_stale_snapshot",
    ),
    Mutation(
        "gm-skill-handshake-bypass",
        "runtime/shinobi_runtime/gm_skill_contract.py",
        (("and secrets.compare_digest(value, GM_SKILL_CONTRACT_TOKEN)", "and True"),),
        "tests/playability/test_delivery_chain_contract.py::test_get_play_context_fails_closed_for_missing_or_stale_skill_token",
    ),
    Mutation(
        "get-play-context-release-lineage-proof-omission",
        "runtime/shinobi_runtime/api/mcp.py",
        ((
            '        "release_baseline_id": baseline_id,\n',
            '',
        ),),
        "tests/playability/test_delivery_chain_contract.py::test_get_play_context_fails_closed_for_missing_or_stale_skill_token",
    ),
    Mutation(
        "get-play-context-deployment-guard-bypass",
        "runtime/shinobi_runtime/api/mcp.py",
        ((
            "deployment = assert_deployment_freshness(operations.repository.root)",
            'deployment = type("BypassedFreshness", (), {"status": "bypassed"})()',
        ),),
        "tests/playability/test_delivery_chain_contract.py::test_get_play_context_fails_closed_for_missing_or_stale_skill_token",
    ),
    Mutation(
        "app-startup-freshness-guard-bypass",
        "runtime/shinobi_runtime/api/app.py",
        ((
            "    assert_deployment_freshness(repository.root)",
            "    inspect_deployment_freshness(repository.root)",
        ),),
        "tests/playability/test_delivery_chain_contract.py::test_app_entrypoint_calls_freshness_guard_before_recovery",
    ),
    Mutation(
        "bootstrap-startup-freshness-guard-bypass",
        "runtime/shinobi_runtime/bootstrap.py",
        ((
            "        assert_deployment_freshness(settings.campaign_root)",
            "        pass",
        ),),
        "tests/playability/test_delivery_chain_contract.py::test_bootstrap_checks_deployment_freshness_before_exec",
    ),
    Mutation(
        "production-source-freshness-bypass",
        "runtime/shinobi_runtime/deployment_freshness.py",
        (("if not freshness.healthy:\n        raise DeploymentFreshnessError(freshness.reason)", "if False:\n        raise DeploymentFreshnessError(freshness.reason)"),),
        "tests/playability/test_delivery_chain_contract.py::test_production_startup_freshness_guard_rejects_stale_source",
    ),
    Mutation(
        "shard-checkpoint-source-fingerprint-bypass",
        "tools/run_pytest_shards.py",
        ((
            'if data.get("source_fingerprint") != fingerprint or data.get("files") != files:',
            'if data.get("files") != files:',
        ),),
        "tests/current/test_pytest_shard_runner.py::test_shard_runner_checkpoint_resumes_only_same_fingerprint",
    ),
)


def _env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "runtime")
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(root: Path, selector: str, *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", selector],
        cwd=root,
        env=_env(root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def _copy_candidate(target: Path) -> None:
    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in {".git", ".pytest_cache", "__pycache__", "artifacts"}
            or name.endswith(".pyc")
        }

    shutil.copytree(ROOT, target, ignore=ignore)


def _apply(root: Path, mutation: Mutation) -> None:
    path = root / mutation.relpath
    text = path.read_text(encoding="utf-8")
    for old, new in mutation.replacements:
        count = text.count(old)
        if count != 1:
            raise RuntimeError(
                f"{mutation.name}: expected one replacement target in {mutation.relpath}, found {count}"
            )
        text = text.replace(old, new, 1)
    if path.suffix == ".py":
        compile(text, str(path), "exec")
    path.write_text(text, encoding="utf-8")


def _exercise_mutation(mutation: Mutation) -> tuple[str, str]:
    with tempfile.TemporaryDirectory(prefix=f"shinobi-mut-{mutation.name}-") as tmp:
        candidate = Path(tmp) / "repo"
        _copy_candidate(candidate)
        _apply(candidate, mutation)
        result = _run(candidate, mutation.selector)
    output = result.stdout
    if result.returncode == 0:
        return "survived", output
    if "ERROR collecting" in output or "ImportError" in output:
        return "invalid_test_failure", output
    # A non-zero process exit is not automatically a useful mutation kill.
    # Require pytest to report at least one actual failed test so collection,
    # environment, signal, or harness failures cannot inflate the kill count.
    if re.search(r"\b[1-9][0-9]* failed\b", output.lower()) is None:
        return "invalid_non_assertion_failure", output
    return "killed", output


def run(selected: tuple[Mutation, ...]) -> int:
    unique_selectors = tuple(dict.fromkeys(mutation.selector for mutation in selected))
    requested_baseline_workers = int(os.environ.get("SHINOBI_MUTATION_BASELINE_WORKERS", "6"))
    baseline_workers = max(1, min(requested_baseline_workers, len(unique_selectors)))
    with ThreadPoolExecutor(max_workers=baseline_workers) as pool:
        futures = {selector: pool.submit(_run, ROOT, selector) for selector in unique_selectors}
        baseline_cache = {selector: futures[selector].result() for selector in unique_selectors}
    for selector in unique_selectors:
        result = baseline_cache[selector]
        if result.returncode != 0:
            print(f"MUTATION AUDIT BASELINE FAIL: {selector}")
            print(result.stdout[-8000:])
            return 1

    requested_workers = int(os.environ.get("SHINOBI_MUTATION_WORKERS", "8"))
    workers = max(1, min(requested_workers, len(selected)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {mutation: pool.submit(_exercise_mutation, mutation) for mutation in selected}
        results = {mutation: futures[mutation].result() for mutation in selected}

    killed = 0
    for mutation in selected:
        status, output = results[mutation]
        if status == "killed":
            killed += 1
            print(f"MUTATION KILLED: {mutation.name}")
            continue
        if status == "survived":
            label = "MUTATION SURVIVED"
        elif status == "invalid_test_failure":
            label = "MUTATION INVALID TEST FAILURE"
        else:
            label = "MUTATION INVALID NON-ASSERTION FAILURE"
        print(f"{label}: {mutation.name}")
        print(output[-8000:])
        return 1
    print(f"MUTATION AUDIT PASS: killed={killed}/{len(selected)} workers={workers}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation", action="append", default=[])
    args = parser.parse_args()
    if args.mutation:
        wanted = set(args.mutation)
        selected = tuple(m for m in MUTATIONS if m.name in wanted)
        missing = wanted - {m.name for m in selected}
        if missing:
            print("unknown mutation(s): " + ", ".join(sorted(missing)))
            return 2
    else:
        selected = MUTATIONS
    return run(selected)


if __name__ == "__main__":
    raise SystemExit(main())
