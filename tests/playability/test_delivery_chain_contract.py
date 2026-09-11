from __future__ import annotations

import re
import runpy
import subprocess
import sys
from pathlib import Path
import pytest

from shinobi_runtime.deployment_freshness import (
    DeploymentFreshnessError,
    assert_deployment_freshness,
)
from shinobi_runtime.gm_skill_contract import (
    GM_SKILL_CONTRACT_TOKEN,
    verified_delivery_integrity,
    verify_gm_skill_contract_token,
)
from tools.verify_release_state import verify_release_state

ROOT = Path(__file__).resolve().parents[2]


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return completed.stdout.strip()


def _freshness_repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    (root / "runtime").mkdir(parents=True)
    (root / "state").mkdir(parents=True)
    _git(root, "init")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Shinobi Playability")
    (root / "runtime/engine.py").write_text("BUILD = 1\n", encoding="utf-8")
    (root / "state/meta.json").write_text('{"revision":1}\n', encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")
    return root, _git(root, "rev-parse", "HEAD")


def test_deployment_docs_cannot_drift_from_railway_start_command_again():
    railway = (ROOT / "railway.toml").read_text(encoding="utf-8")
    match = re.search(r'^startCommand = "([^"]+)"$', railway, flags=re.MULTILINE)
    assert match is not None
    command = match.group(1)
    assert command == "SHINOBI_GIT_BRANCH=main PYTHONPATH=/app/runtime python -m shinobi_runtime.bootstrap"
    deployment_doc = (ROOT / "docs/RUNTIME_SERVICE_DEPLOYMENT.md").read_text(encoding="utf-8")
    assert command in deployment_doc
    assert "shinobi_runtime.branch_bootstrap" not in deployment_doc


def test_packaged_skill_fingerprint_is_synchronized():
    completed = subprocess.run(
        [sys.executable, "tools/sync_gm_skill_contract.py", "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_get_play_context_fails_closed_for_missing_or_stale_skill_token():
    assert verify_gm_skill_contract_token(None) is False
    assert verify_gm_skill_contract_token("f" * 64) is False
    assert verify_gm_skill_contract_token(GM_SKILL_CONTRACT_TOKEN) is True
    integrity = verified_delivery_integrity()
    assert integrity["gm_skill_contract_verified"] is True
    assert "gm_skill_contract_token" not in repr(integrity)

    source = (ROOT / "runtime/shinobi_runtime/api/mcp.py").read_text(encoding="utf-8")
    assert "def get_play_context(skill_contract_token: Optional[str] = None)" in source
    assert "verify_gm_skill_contract_token(skill_contract_token)" in source
    assert 'OperationError(409, "gm_skill_contract_mismatch")' in source
    assert 'deployment = assert_deployment_freshness(operations.repository.root)' in source
    assert 'OperationError(503, "deployment_source_stale")' in source
    assert '"deployment_compatible": True' in source
    assert '"deployment_source_sync": deployment.status' in source
    assert "load_release_contract(operations.repository.root)" in source
    assert 'OperationError(503, "release_lineage_unavailable")' in source
    assert 'OperationError(503, "release_lineage_mismatch")' in source
    assert '"release_baseline_id": baseline_id' in source
    assert '"release_baseline_revision": baseline_revision' in source
    assert '"release_baseline_world_time": baseline_world_time' in source
    assert 'result["delivery_integrity"] = integrity' in source


def test_production_startup_freshness_guard_rejects_stale_source(tmp_path: Path):
    root, source_sha = _freshness_repo(tmp_path)
    (root / "runtime/engine.py").write_text("BUILD = 2\n", encoding="utf-8")
    _git(root, "add", "runtime/engine.py")
    _git(root, "commit", "-m", "source ahead")
    env = {
        "RAILWAY_PROJECT_ID": "project-test",
        "RAILWAY_GIT_COMMIT_SHA": source_sha,
    }
    with pytest.raises(DeploymentFreshnessError):
        assert_deployment_freshness(root, environ=env)

    # Local development without Railway metadata remains usable.
    assert assert_deployment_freshness(root, environ={}).healthy is True


def test_app_entrypoint_calls_freshness_guard_before_recovery():
    source = (ROOT / "runtime/shinobi_runtime/api/app.py").read_text(encoding="utf-8")
    guard = source.index("assert_deployment_freshness(repository.root)")
    recovery = source.index("coordinator.recover()")
    assert guard < recovery


def test_release_candidate_still_matches_its_pinned_fullfresh_baseline():
    assert verify_release_state(ROOT) == []


def test_changed_path_router_sends_deployment_runbook_to_delivery_regressions():
    policy = runpy.run_path(str(ROOT / "tools/test_changed.py"))
    selected = set(policy["select"](["docs/RUNTIME_SERVICE_DEPLOYMENT.md"]))
    assert "tests/playability/test_delivery_chain_contract.py" in selected
    assert "tests/current/test_deployment_write_safety.py" in selected


def test_resumable_release_evidence_is_invalidated_by_deployment_runbook_changes():
    policy = runpy.run_path(str(ROOT / "tools/run_pytest_shards.py"))
    certified = set(policy["CERTIFIED_TOP_LEVEL_FILES"])
    assert "docs/RUNTIME_SERVICE_DEPLOYMENT.md" in certified


def test_changed_path_router_covers_release_lineage_owners():
    policy = runpy.run_path(str(ROOT / "tools/test_changed.py"))
    for changed in (
        "runtime/shinobi_runtime/release_baseline.py",
        "runtime/contracts/release-campaign-state.json",
    ):
        selected = set(policy["select"]([changed]))
        assert "tests/current/test_main_branch_bootstrap.py" in selected
        assert "tests/playability/test_delivery_chain_contract.py" in selected


def test_release_runner_discovers_playability_and_runs_mutation_audit():
    shard = (ROOT / "tools/run_pytest_shards.py").read_text(encoding="utf-8")
    verify = (ROOT / "tools/verify_release.sh").read_text(encoding="utf-8")
    assert 'ROOT / "tests/playability"' in shard
    assert "tools/sync_gm_skill_contract.py --check" in verify
    assert "tools/playability_lab.py" in verify
    lab = (ROOT / "tools/playability_lab.py").read_text(encoding="utf-8")
    assert "tools/mutation_audit.py" in lab


def test_bootstrap_checks_deployment_freshness_before_exec():
    source = (ROOT / "runtime/shinobi_runtime/bootstrap.py").read_text(encoding="utf-8")
    checkout = source.index("ensure_checkout(settings)")
    freshness = source.index("assert_deployment_freshness(settings.campaign_root)")
    exec_call = source.index("os.execvp(")
    assert checkout < freshness < exec_call
