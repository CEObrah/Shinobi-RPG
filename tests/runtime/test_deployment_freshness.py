from __future__ import annotations

import subprocess
from pathlib import Path

from shinobi_runtime.deployment_freshness import inspect_deployment_freshness


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "freshness@example.invalid")
    git(root, "config", "user.name", "Freshness Test")
    (root / "runtime").mkdir()
    (root / "state").mkdir()
    (root / "runtime" / "code.py").write_text("VERSION = 1\n", encoding="utf-8")
    (root / "state" / "meta.json").write_text('{"revision":1}\n', encoding="utf-8")
    git(root, "add", "runtime/code.py", "state/meta.json")
    git(root, "commit", "-qm", "baseline")
    return root, git(root, "rev-parse", "HEAD")


def railway_env(source_revision: str | None) -> dict[str, str]:
    env = {"RAILWAY_PROJECT_ID": "project.test"}
    if source_revision is not None:
        env["RAILWAY_GIT_COMMIT_SHA"] = source_revision
    return env


def test_state_only_checkout_descendant_remains_fresh(tmp_path: Path) -> None:
    root, build = repository(tmp_path)
    (root / "state" / "meta.json").write_text('{"revision":2}\n', encoding="utf-8")
    git(root, "add", "state/meta.json")
    git(root, "commit", "-qm", "gameplay state")

    freshness = inspect_deployment_freshness(root, environ=railway_env(build))

    assert freshness.status == "fresh"
    assert freshness.healthy is True
    assert freshness.production is True
    assert freshness.source_revision == build
    assert freshness.checkout_revision == git(root, "rev-parse", "HEAD")
    assert freshness.non_state_paths == ()


def test_non_state_checkout_descendant_marks_running_image_stale(tmp_path: Path) -> None:
    root, build = repository(tmp_path)
    (root / "runtime" / "code.py").write_text("VERSION = 2\n", encoding="utf-8")
    git(root, "add", "runtime/code.py")
    git(root, "commit", "-qm", "runtime source")

    freshness = inspect_deployment_freshness(root, environ=railway_env(build))

    assert freshness.status == "stale"
    assert freshness.healthy is False
    assert freshness.reason == "non_state_source_ahead_of_running_image"
    assert freshness.non_state_paths == ("runtime/code.py",)


def test_railway_process_without_valid_build_revision_is_unhealthy(tmp_path: Path) -> None:
    root, _build = repository(tmp_path)

    missing = inspect_deployment_freshness(root, environ=railway_env(None))
    invalid = inspect_deployment_freshness(root, environ=railway_env("not-a-sha"))

    assert missing.status == "unverified"
    assert missing.healthy is False
    assert missing.reason == "build_revision_unavailable"
    assert invalid.status == "unverified"
    assert invalid.healthy is False
    assert invalid.reason == "build_revision_unavailable"


def test_local_process_may_be_unverified_without_railway_metadata(tmp_path: Path) -> None:
    root, _build = repository(tmp_path)

    freshness = inspect_deployment_freshness(root, environ={})

    assert freshness.status == "unverified"
    assert freshness.production is False
    assert freshness.healthy is True


def test_unknown_or_divergent_build_revision_fails_closed(tmp_path: Path) -> None:
    root, _build = repository(tmp_path)
    unknown = "1" * 40

    freshness = inspect_deployment_freshness(root, environ=railway_env(unknown))

    assert freshness.status == "stale"
    assert freshness.healthy is False
    assert freshness.reason == "build_revision_not_in_checkout_history"
