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
    (source / "state").mkdir()
    (source / "state" / "meta.json").write_text('{"revision":18}\n', encoding="utf-8")
    (source / "README.md").write_text("baseline\n", encoding="utf-8")
    git(source, "add", "state/meta.json", "README.md")
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


def test_bootstrap_rehomes_clean_divergent_history_when_campaign_authority_matches(
    tmp_path: Path,
) -> None:
    source, remote = source_and_remote(tmp_path)
    configured = settings(tmp_path, remote)
    checkout = ensure_checkout(configured)
    git(checkout, "config", "user.email", "runtime@example.invalid")
    git(checkout, "config", "user.name", "Runtime Test")

    (checkout / "README.md").write_text("local old-lineage docs\n", encoding="utf-8")
    git(checkout, "add", "README.md")
    git(checkout, "commit", "-qm", "local old lineage")

    (source / "README.md").write_text("remote replacement docs\n", encoding="utf-8")
    git(source, "add", "README.md")
    git(source, "commit", "-qm", "remote replacement lineage")
    git(source, "push", "-q", "origin", "main")
    remote_head = git(source, "rev-parse", "HEAD")

    assert ensure_checkout(configured) == checkout
    assert git(checkout, "rev-parse", "HEAD") == remote_head
    assert (checkout / "README.md").read_text(encoding="utf-8") == "remote replacement docs\n"
    assert (checkout / "state" / "meta.json").read_text(encoding="utf-8") == '{"revision":18}\n'


def test_bootstrap_still_fails_closed_when_divergent_campaign_authority_differs(
    tmp_path: Path,
) -> None:
    source, remote = source_and_remote(tmp_path)
    configured = settings(tmp_path, remote)
    checkout = ensure_checkout(configured)
    git(checkout, "config", "user.email", "runtime@example.invalid")
    git(checkout, "config", "user.name", "Runtime Test")

    (checkout / "state" / "meta.json").write_text('{"revision":19}\n', encoding="utf-8")
    git(checkout, "add", "state/meta.json")
    git(checkout, "commit", "-qm", "unpushed local gameplay")

    (source / "README.md").write_text("remote source change\n", encoding="utf-8")
    git(source, "add", "README.md")
    git(source, "commit", "-qm", "remote source change")
    git(source, "push", "-q", "origin", "main")

    with pytest.raises(BootstrapError, match="different campaign authority"):
        ensure_checkout(configured)
