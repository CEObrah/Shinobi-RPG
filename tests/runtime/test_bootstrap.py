from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from shinobi_runtime.bootstrap import BootstrapError, CheckoutSettings, ensure_checkout


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root)] + list(arguments),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def source_and_remote(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    git(source, "config", "user.email", "bootstrap@example.invalid")
    git(source, "config", "user.name", "Bootstrap Test")
    (source / "campaign.txt").write_text("one\n", encoding="utf-8")
    git(source, "add", "campaign.txt")
    git(source, "commit", "-qm", "baseline")
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(source), str(remote)],
        check=True,
    )
    git(source, "remote", "add", "origin", str(remote))
    return source, remote


def settings(tmp_path: Path, remote: Path) -> CheckoutSettings:
    return CheckoutSettings(
        campaign_root=tmp_path / "volume" / "campaign",
        runtime_root=tmp_path / "volume" / "runtime",
        git_url=str(remote),
        branch="main",
    )


def test_bootstrap_clones_once_and_fast_forwards_clean_checkout(tmp_path: Path) -> None:
    source, remote = source_and_remote(tmp_path)
    configured = settings(tmp_path, remote)
    checkout = ensure_checkout(configured)
    assert (checkout / "campaign.txt").read_text(encoding="utf-8") == "one\n"

    (source / "campaign.txt").write_text("two\n", encoding="utf-8")
    git(source, "add", "campaign.txt")
    git(source, "commit", "-qm", "remote update")
    git(source, "push", "-q", "origin", "main")
    assert ensure_checkout(configured) == checkout
    assert (checkout / "campaign.txt").read_text(encoding="utf-8") == "two\n"


def test_bootstrap_preserves_clean_local_commit_ahead_for_wal_recovery(tmp_path: Path) -> None:
    source, remote = source_and_remote(tmp_path)
    configured = settings(tmp_path, remote)
    checkout = ensure_checkout(configured)
    git(checkout, "config", "user.email", "runtime@example.invalid")
    git(checkout, "config", "user.name", "Runtime Test")
    (checkout / "campaign.txt").write_text("local transaction\n", encoding="utf-8")
    git(checkout, "add", "campaign.txt")
    git(checkout, "commit", "-qm", "local transaction")
    local_head = git(checkout, "rev-parse", "HEAD")
    assert ensure_checkout(configured) == checkout
    assert git(checkout, "rev-parse", "HEAD") == local_head


def test_bootstrap_preserves_synchronized_dirty_checkout_for_wal_recovery_and_rejects_divergence(
    tmp_path: Path,
) -> None:
    source, remote = source_and_remote(tmp_path)
    configured = settings(tmp_path, remote)
    checkout = ensure_checkout(configured)
    (checkout / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    assert ensure_checkout(configured) == checkout
    assert (checkout / "untracked.txt").read_text(encoding="utf-8") == "dirty\n"
    (checkout / "untracked.txt").unlink()

    git(checkout, "config", "user.email", "runtime@example.invalid")
    git(checkout, "config", "user.name", "Runtime Test")
    (checkout / "campaign.txt").write_text("local\n", encoding="utf-8")
    git(checkout, "add", "campaign.txt")
    git(checkout, "commit", "-qm", "local")
    (source / "campaign.txt").write_text("remote\n", encoding="utf-8")
    git(source, "add", "campaign.txt")
    git(source, "commit", "-qm", "remote")
    git(source, "push", "-q", "origin", "main")
    with pytest.raises(BootstrapError, match="diverged"):
        ensure_checkout(configured)


def test_bootstrap_settings_reject_unsafe_layout_refs_and_token_transport(
    tmp_path: Path,
) -> None:
    with pytest.raises(BootstrapError, match="inside"):
        CheckoutSettings(
            campaign_root=tmp_path / "campaign",
            runtime_root=tmp_path / "campaign" / "runtime",
            git_url="https://github.com/example/campaign.git",
        )
    with pytest.raises(BootstrapError, match="safe Git ref"):
        CheckoutSettings(
            campaign_root=tmp_path / "campaign",
            runtime_root=tmp_path / "runtime",
            git_url="https://github.com/example/campaign.git",
            branch="--upload-pack=bad",
        )
    with pytest.raises(BootstrapError, match="HTTPS"):
        CheckoutSettings(
            campaign_root=tmp_path / "campaign",
            runtime_root=tmp_path / "runtime",
            git_url=str(tmp_path / "remote.git"),
            git_token="secret",
        )
    with pytest.raises(BootstrapError, match="embed credentials"):
        CheckoutSettings(
            campaign_root=tmp_path / "campaign",
            runtime_root=tmp_path / "runtime",
            git_url="https://token@github.com/example/campaign.git",
        )
