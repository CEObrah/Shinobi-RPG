from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import shinobi_runtime.api.transition_operations as transition_module
from shinobi_runtime.api.ooc import RepositoryOocAudit
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.api.parley_operations import ParleyAwareCampaignOperations
from shinobi_runtime.api.transition_operations import TransitionAwareCampaignOperations
from shinobi_runtime.deployment_freshness import inspect_deployment_freshness
from shinobi_runtime.store import RepositoryStore


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    (root / "runtime").mkdir(parents=True)
    (root / "state").mkdir(parents=True)
    _git(root, "init")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Shinobi Tests")
    (root / "runtime" / "runtime.py").write_text("BUILD = 1\n", encoding="utf-8")
    (root / "state" / "meta.json").write_text('{"revision": 1}\n', encoding="utf-8")
    _git(root, "add", "runtime/runtime.py", "state/meta.json")
    _git(root, "commit", "-m", "initial")
    return root, _git(root, "rev-parse", "HEAD")


def _railway_env(source_sha: str | None) -> dict[str, str]:
    env = {"RAILWAY_PROJECT_ID": "project-test"}
    if source_sha is not None:
        env["RAILWAY_GIT_COMMIT_SHA"] = source_sha
    return env


def test_deployment_freshness_accepts_state_only_commits_after_build(tmp_path: Path):
    root, source_sha = _init_repo(tmp_path)
    (root / "state" / "meta.json").write_text('{"revision": 2}\n', encoding="utf-8")
    _git(root, "add", "state/meta.json")
    _git(root, "commit", "-m", "state only")

    freshness = inspect_deployment_freshness(root, environ=_railway_env(source_sha))

    assert freshness.status == "fresh"
    assert freshness.healthy is True
    assert freshness.non_state_paths == ()


def test_deployment_freshness_rejects_source_change_after_build(tmp_path: Path):
    root, source_sha = _init_repo(tmp_path)
    (root / "runtime" / "runtime.py").write_text("BUILD = 2\n", encoding="utf-8")
    _git(root, "add", "runtime/runtime.py")
    _git(root, "commit", "-m", "source ahead")

    freshness = inspect_deployment_freshness(root, environ=_railway_env(source_sha))

    assert freshness.status == "stale"
    assert freshness.healthy is False
    assert freshness.reason == "non_state_source_ahead_of_running_image"
    assert freshness.non_state_paths == ("runtime/runtime.py",)


def test_railway_process_without_build_revision_is_unhealthy(tmp_path: Path):
    root, _source_sha = _init_repo(tmp_path)

    freshness = inspect_deployment_freshness(root, environ=_railway_env(None))

    assert freshness.status == "unverified"
    assert freshness.production is True
    assert freshness.healthy is False
    assert freshness.reason == "build_revision_unavailable"


def test_local_process_without_build_revision_remains_usable(tmp_path: Path):
    root, _source_sha = _init_repo(tmp_path)

    freshness = inspect_deployment_freshness(root, environ={})

    assert freshness.status == "unverified"
    assert freshness.production is False
    assert freshness.healthy is True


def test_production_operations_reject_preview_and_execute_when_deployment_is_stale(monkeypatch, tmp_path: Path):
    ops = object.__new__(TransitionAwareCampaignOperations)
    ops.repository = SimpleNamespace(root=tmp_path)
    reached = []

    monkeypatch.setattr(
        transition_module,
        "inspect_deployment_freshness",
        lambda _root: SimpleNamespace(healthy=False),
    )
    monkeypatch.setattr(
        ParleyAwareCampaignOperations,
        "preview_command",
        lambda self, command: reached.append(("preview", command)) or {"ok": True},
    )
    monkeypatch.setattr(
        ParleyAwareCampaignOperations,
        "execute_command",
        lambda self, command: reached.append(("execute", command)) or {"ok": True},
    )

    with pytest.raises(OperationError) as preview_exc:
        ops.preview_command("preview-command")
    with pytest.raises(OperationError) as execute_exc:
        ops.execute_command("execute-command")

    assert preview_exc.value.status_code == 503
    assert preview_exc.value.code == "deployment_source_stale"
    assert execute_exc.value.status_code == 503
    assert execute_exc.value.code == "deployment_source_stale"
    assert reached == []


def test_production_operations_delegate_when_deployment_is_fresh(monkeypatch, tmp_path: Path):
    ops = object.__new__(TransitionAwareCampaignOperations)
    ops.repository = SimpleNamespace(root=tmp_path)

    monkeypatch.setattr(
        transition_module,
        "inspect_deployment_freshness",
        lambda _root: SimpleNamespace(healthy=True),
    )
    monkeypatch.setattr(
        ParleyAwareCampaignOperations,
        "preview_command",
        lambda self, command: {"kind": "preview", "command": command},
    )
    monkeypatch.setattr(
        ParleyAwareCampaignOperations,
        "execute_command",
        lambda self, command: {"kind": "execute", "command": command},
    )

    assert ops.preview_command("a") == {"kind": "preview", "command": "a"}
    assert ops.execute_command("b") == {"kind": "execute", "command": "b"}


def test_ooc_audit_exposes_deployment_source_summary():
    root = Path(__file__).resolve().parents[2]
    result = RepositoryOocAudit(RepositoryStore(root))(None, ())

    assert any(
        row.startswith("deployment_source:summary ")
        for row in result.diagnostics
    )
