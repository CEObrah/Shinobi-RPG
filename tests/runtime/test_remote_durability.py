import json
import os
import subprocess
from pathlib import Path
from typing import Optional

import pytest

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.tx import (
    GitRemoteDurability,
    GitStager,
    ReceiptStore,
    TransactionCoordinator,
    WriteAheadLog,
)
from shinobi_runtime.tx.errors import (
    RecoveryError,
    RemoteDivergenceError,
    RemoteDurabilityError,
    RemotePushError,
    WalError,
)


class InjectedCrash(BaseException):
    pass


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root)] + list(arguments),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def meta_bytes(revision: int) -> bytes:
    return (
        json.dumps(
            {
                "schema": "meta",
                "campaign_id": "remote-test",
                "revision": revision,
            },
            indent=2,
        ).encode("utf-8")
        + b"\n"
    )


def create_remote_campaign(tmp_path: Path):
    root = tmp_path / "campaign"
    bare = tmp_path / "campaign-remote.git"
    runtime = tmp_path / "runtime"
    (root / "state").mkdir(parents=True)
    (root / "state" / "meta.json").write_bytes(meta_bytes(3))
    (root / "state" / "owner.json").write_bytes(b'{"status":"before"}\n')
    git(root.parent, "init", "-q", str(root))
    git(root, "checkout", "-qb", "main")
    git(root, "config", "user.email", "runtime@example.invalid")
    git(root, "config", "user.name", "Runtime Test")
    git(root, "add", "state/meta.json", "state/owner.json")
    git(root, "commit", "-qm", "baseline")
    git(bare.parent, "init", "--bare", "-q", str(bare))
    git(root, "remote", "add", "origin", str(bare))
    git(root, "push", "-q", "-u", "origin", "main")

    repository = RepositoryStore(root)
    stager = GitStager(root)
    wal = WriteAheadLog(runtime / "wal")
    receipts = ReceiptStore(runtime / "receipts")
    remote = GitRemoteDurability(stager, "origin", "main", timeout_seconds=5)
    coordinator = TransactionCoordinator(
        repository,
        stager,
        wal,
        receipts,
        lock_path=runtime / "writer.lock",
        remote_durability=remote,
    )
    return root, bare, repository, stager, wal, receipts, remote, coordinator


def command(request_id: str = "request-remote") -> CommandEnvelope:
    return CommandEnvelope(
        campaign_id="remote-test",
        request_id=request_id,
        actor_id="pc_wei_tang",
        command_type="wait",
        expected_revision=3,
        submitted_at="2026-08-09T12:00:00Z",
        payload={"duration_seconds": 60},
        mode="gameplay",
    )


def execute(
    coordinator: TransactionCoordinator,
    request_id: str = "request-remote",
    crash_phase: Optional[str] = None,
):
    def injector(phase, manifest):
        if phase == crash_phase:
            raise InjectedCrash(phase)

    return coordinator.execute(
        command(request_id),
        transaction_id="tx." + request_id,
        created_at="2026-08-09T12:00:01Z",
        writes={
            "state/meta.json": meta_bytes(4),
            "state/owner.json": b'{"status":"after"}\n',
        },
        result={"status": "committed"},
        validator=lambda overlay, manifest: None,
        crash_injector=injector if crash_phase is not None else None,
    )


def add_external_remote_commit(bare: Path, tmp_path: Path) -> str:
    outsider = tmp_path / "outsider"
    git(tmp_path, "clone", "-q", "--branch", "main", str(bare), str(outsider))
    git(outsider, "config", "user.email", "outsider@example.invalid")
    git(outsider, "config", "user.name", "Competing Writer")
    (outsider / "external.txt").write_text("competing state\n", encoding="utf-8")
    git(outsider, "add", "external.txt")
    git(outsider, "commit", "-qm", "competing commit")
    git(outsider, "push", "-q", "origin", "main")
    return git(outsider, "rev-parse", "HEAD")


def test_required_remote_receives_exact_commit_before_receipt(tmp_path: Path) -> None:
    (
        root,
        bare,
        repository,
        stager,
        wal,
        receipts,
        remote,
        coordinator,
    ) = create_remote_campaign(tmp_path)

    result = execute(coordinator)

    assert result.status == "committed"
    assert result.commit_hash == stager.head()
    assert git(bare, "rev-parse", "refs/heads/main") == result.commit_hash
    assert wal.load("tx.request-remote")["status"] == "committed"
    assert wal.load("tx.request-remote")["durability"] == {
        "kind": "git_remote",
        "remote": "origin",
        "branch": "main",
    }
    assert receipts.get("request-remote") == result.receipt
    assert remote.verify_synchronized().remote_head == result.commit_hash

    wal_path = next(wal.directory.glob("*.json"))
    tampered = json.loads(wal_path.read_text(encoding="utf-8"))
    tampered["durability"] = {"kind": "local"}
    wal_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(WalError, match="durability digest mismatch"):
        wal.load("tx.request-remote")


def test_preflight_fails_closed_when_remote_head_advanced(tmp_path: Path) -> None:
    (
        root,
        bare,
        repository,
        stager,
        wal,
        receipts,
        remote,
        coordinator,
    ) = create_remote_campaign(tmp_path)
    original_head = stager.head()
    external_head = add_external_remote_commit(bare, tmp_path)

    with pytest.raises(RemoteDivergenceError, match="head_mismatch"):
        execute(coordinator)

    assert stager.head() == original_head
    assert external_head != original_head
    assert repository.current_revision() == 3
    assert wal.records() == ()
    assert receipts.get("request-remote") is None
    stager.assert_pristine()


def test_rejected_push_keeps_local_commit_and_recovery_retries(
    tmp_path: Path,
) -> None:
    (
        root,
        bare,
        repository,
        stager,
        wal,
        receipts,
        remote,
        coordinator,
    ) = create_remote_campaign(tmp_path)
    baseline = git(bare, "rev-parse", "refs/heads/main")
    hook = bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    os.chmod(hook, 0o755)

    with pytest.raises(RemotePushError, match="git_rejected") as error:
        execute(coordinator)

    local_commit = stager.head()
    assert error.value.returncode is not None
    assert local_commit != baseline
    assert git(bare, "rev-parse", "refs/heads/main") == baseline
    assert repository.current_revision() == 4
    assert wal.load("tx.request-remote")["status"] == "applied"
    assert receipts.get("request-remote") is None
    stager.assert_pristine()

    # Restarting with the durability settings accidentally removed cannot
    # downgrade an already prepared remote-required transaction.
    local_only = TransactionCoordinator(
        repository,
        stager,
        wal,
        receipts,
        lock_path=tmp_path / "runtime" / "writer.lock",
    )
    with pytest.raises(RecoveryError, match="requires Git remote durability"):
        local_only.recover()
    assert stager.head() == local_commit
    assert wal.load("tx.request-remote")["status"] == "applied"
    assert receipts.get("request-remote") is None

    hook.unlink()
    decisions = coordinator.recover()
    assert [decision.action for decision in decisions] == ["finalized_commit"]
    assert git(bare, "rev-parse", "refs/heads/main") == local_commit
    assert wal.load("tx.request-remote")["status"] == "committed"
    assert receipts.get("request-remote") is not None
    assert repository.current_revision() == 4


def test_crash_after_push_is_recognized_without_rollback(tmp_path: Path) -> None:
    (
        root,
        bare,
        repository,
        stager,
        wal,
        receipts,
        remote,
        coordinator,
    ) = create_remote_campaign(tmp_path)

    with pytest.raises(InjectedCrash):
        execute(coordinator, crash_phase="after_remote_push")

    pushed_commit = stager.head()
    assert git(bare, "rev-parse", "refs/heads/main") == pushed_commit
    assert repository.current_revision() == 4
    assert wal.load("tx.request-remote")["status"] == "applied"
    assert receipts.get("request-remote") is None

    decisions = coordinator.recover()
    assert [decision.commit_hash for decision in decisions] == [pushed_commit]
    assert wal.load("tx.request-remote")["status"] == "committed"
    assert receipts.get("request-remote") is not None
    assert git(bare, "rev-parse", "refs/heads/main") == pushed_commit


def test_crash_after_remote_preflight_writes_nothing(tmp_path: Path) -> None:
    (
        root,
        bare,
        repository,
        stager,
        wal,
        receipts,
        remote,
        coordinator,
    ) = create_remote_campaign(tmp_path)
    baseline = stager.head()

    with pytest.raises(InjectedCrash):
        execute(coordinator, crash_phase="after_remote_preflight")

    assert stager.head() == baseline
    assert git(bare, "rev-parse", "refs/heads/main") == baseline
    assert repository.current_revision() == 3
    assert wal.records() == ()
    assert receipts.get("request-remote") is None
    assert coordinator.recover() == ()
    stager.assert_pristine()


def test_recovery_never_overwrites_diverged_remote_or_rolls_back_local_commit(
    tmp_path: Path,
) -> None:
    (
        root,
        bare,
        repository,
        stager,
        wal,
        receipts,
        remote,
        coordinator,
    ) = create_remote_campaign(tmp_path)
    hook = bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    os.chmod(hook, 0o755)
    with pytest.raises(RemotePushError):
        execute(coordinator)
    local_commit = stager.head()
    hook.unlink()

    external_head = add_external_remote_commit(bare, tmp_path)
    with pytest.raises(RemoteDivergenceError, match="unexpected_remote_head"):
        coordinator.recover()

    assert git(bare, "rev-parse", "refs/heads/main") == external_head
    assert stager.head() == local_commit
    assert repository.current_revision() == 4
    assert wal.load("tx.request-remote")["status"] == "applied"
    assert receipts.get("request-remote") is None
    stager.assert_pristine()


def test_remote_configuration_is_both_or_neither_and_sanitizes_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, bare, repository, stager, wal, receipts, remote, coordinator = (
        create_remote_campaign(tmp_path)
    )
    assert GitRemoteDurability.from_env(stager, {}) is None
    with pytest.raises(RuntimeError, match="required when SHINOBI_GIT_URL"):
        GitRemoteDurability.from_env(
            stager,
            {"SHINOBI_GIT_URL": "https://example.invalid/private.git"},
        )
    with pytest.raises(RuntimeError, match="must be set together"):
        GitRemoteDurability.from_env(stager, {"SHINOBI_GIT_REMOTE": "origin"})
    with pytest.raises(RuntimeError, match="invalid required Git remote"):
        GitRemoteDurability.from_env(
            stager,
            {
                "SHINOBI_GIT_REMOTE": "--upload-pack=bad",
                "SHINOBI_GIT_BRANCH": "main",
            },
        )
    with pytest.raises(ValueError):
        GitRemoteDurability(stager, "origin", "main:other")

    # A configured token is accepted only through bootstrap's executable,
    # forced askpass wrapper.  The token is never placed in a Git argument.
    monkeypatch.setenv("SHINOBI_GIT_TOKEN", "test-token-never-logged")
    monkeypatch.delenv("GIT_ASKPASS", raising=False)
    monkeypatch.delenv("GIT_ASKPASS_REQUIRE", raising=False)
    with pytest.raises(RemoteDurabilityError, match="askpass_not_ready"):
        remote.verify_synchronized()
    askpass = tmp_path / "runtime" / "git-askpass"
    askpass.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    os.chmod(askpass, 0o700)
    monkeypatch.setenv("GIT_ASKPASS", str(askpass))
    monkeypatch.setenv("GIT_ASKPASS_REQUIRE", "force")
    assert remote.verify_synchronized().local_head == stager.head()

    secret_marker = "credential-secret-marker"
    git(root, "remote", "set-url", "origin", str(tmp_path / secret_marker))
    broken = GitRemoteDurability(stager, "origin", "main", timeout_seconds=1)
    with pytest.raises(RemoteDurabilityError) as error:
        broken.verify_synchronized()
    assert secret_marker not in str(error.value)
    assert not hasattr(error.value, "stderr")
