import json
import os
import subprocess
from pathlib import Path
from typing import Optional

import pytest

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.tx import GitStager, ReceiptStore, TransactionCoordinator, WriteAheadLog
from shinobi_runtime.tx.errors import (
    CommitVerificationError,
    DirtyRepositoryError,
    GitCommitError,
    StaleRevisionError,
    WalError,
)


class InjectedCrash(BaseException):
    """Bypass ordinary exception cleanup like a terminated process would."""


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
                "campaign_id": "coordinator-test",
                "revision": revision,
                "note": "raw owner formatting is not canonicalized",
            },
            indent=2,
        ).encode("utf-8")
        + b"\n"
    )


def create_git_campaign(tmp_path: Path, revision: int = 3):
    root = tmp_path / "campaign"
    runtime = tmp_path / "runtime"
    (root / "state").mkdir(parents=True)
    (root / "game" / "rules").mkdir(parents=True)
    (root / "state" / "meta.json").write_bytes(meta_bytes(revision))
    (root / "state" / "owner.json").write_bytes(b'{"status": "before", "ratio": 1.25}\n')
    (root / "game" / "rules" / "float-owner.json").write_bytes(
        b'{\n  "ratio": 1.25,\n  "label": "before"\n}\n'
    )
    git(root.parent, "init", "-q", str(root))
    git(root, "config", "user.email", "runtime@example.invalid")
    git(root, "config", "user.name", "Runtime Test")
    git(root, "add", "state/meta.json", "state/owner.json", "game/rules/float-owner.json")
    git(root, "commit", "-qm", "baseline")

    repository = RepositoryStore(root)
    stager = GitStager(root)
    wal = WriteAheadLog(runtime / "wal")
    receipts = ReceiptStore(runtime / "receipts")
    coordinator = TransactionCoordinator(
        repository,
        stager,
        wal,
        receipts,
        lock_path=runtime / "writer.lock",
    )
    return root, repository, stager, wal, receipts, coordinator


def gameplay_command(
    revision: int = 3,
    request_id: str = "request-gameplay",
) -> CommandEnvelope:
    return CommandEnvelope(
        campaign_id="coordinator-test",
        request_id=request_id,
        actor_id="pc_wei_tang",
        command_type="wait",
        expected_revision=revision,
        submitted_at="2026-08-09T12:00:00Z",
        payload={"duration_seconds": 60},
        mode="gameplay",
    )


def maintenance_command(
    revision: int = 3,
    request_id: str = "request-maintenance",
) -> CommandEnvelope:
    return CommandEnvelope(
        campaign_id="coordinator-test",
        request_id=request_id,
        actor_id="maintenance_codex",
        command_type="maintenance_fix",
        expected_revision=revision,
        submitted_at="2026-08-09T12:00:00Z",
        payload={"issue_id": "M-001"},
        mode="maintenance",
    )


def gameplay_writes() -> dict:
    return {
        "state/meta.json": meta_bytes(4),
        "state/owner.json": b'{"status": "after", "ratio": 1.25}\n',
    }


def validating_overlay(overlay, manifest) -> None:
    assert manifest.mode == "gameplay"
    assert overlay.read_json("state/meta.json")["revision"] == 4
    assert overlay.read_json("state/owner.json")["status"] == "after"
    assert overlay.repository.current_revision() == 3


def execute_gameplay(
    coordinator: TransactionCoordinator,
    crash_phase: Optional[str] = None,
):
    def injector(phase, manifest) -> None:
        if phase == crash_phase:
            raise InjectedCrash(phase)

    return coordinator.execute(
        gameplay_command(),
        transaction_id="tx-gameplay-001",
        created_at="2026-08-09T12:00:01Z",
        writes=gameplay_writes(),
        result={"status": "committed", "visible": ["one minute passes"]},
        validator=validating_overlay,
        crash_injector=injector if crash_phase is not None else None,
    )


def test_coordinator_commits_exact_paths_trailers_and_duplicate_receipt(
    tmp_path: Path,
) -> None:
    root, repository, stager, wal, receipts, coordinator = create_git_campaign(tmp_path)
    execution = execute_gameplay(coordinator)

    assert execution.status == "committed"
    assert repository.current_revision() == 4
    assert repository.read_bytes("state/owner.json") == gameplay_writes()["state/owner.json"]
    assert set(execution.readback_hashes) == {
        "state/meta.json",
        "state/owner.json",
    }
    commit = stager.get_commit(execution.commit_hash)
    assert commit.paths == ("state/meta.json", "state/owner.json")
    assert commit.trailers["Shinobi-Transaction"] == "tx-gameplay-001"
    assert commit.trailers["Shinobi-World-Revision"] == "4"
    assert commit.trailers["Shinobi-Mode"] == "gameplay"
    assert wal.load("tx-gameplay-001")["status"] == "committed"
    assert receipts.get("request-gameplay") == execution.receipt
    stager.assert_pristine()

    # The original expected revision is now stale, but the idempotency lookup is
    # intentionally resolved before planning a second mutation.
    duplicate = execute_gameplay(coordinator)
    assert duplicate.status == "duplicate"
    assert duplicate.receipt == execution.receipt
    assert git(root, "rev-list", "--count", "HEAD") == "2"


def test_recovery_finds_transaction_at_head_without_history_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, repository, stager, wal, receipts, coordinator = create_git_campaign(tmp_path)
    with pytest.raises(InjectedCrash):
        execute_gameplay(coordinator, crash_phase="after_git_commit")
    committed_head = stager.head()

    calls = []
    original_run = stager._run_bytes

    def recording_run(arguments, input_bytes=None):
        arguments = tuple(arguments)
        calls.append(arguments)
        return original_run(arguments, input_bytes)

    monkeypatch.setattr(stager, "_run_bytes", recording_run)

    decisions = coordinator.recover()

    lookup_commands = [
        arguments[0]
        for arguments in calls
        if arguments and arguments[0] in {"rev-parse", "show", "diff-tree", "log"}
    ]
    assert lookup_commands == ["rev-parse", "show", "diff-tree"]
    assert [decision.commit_hash for decision in decisions] == [committed_head]
    assert wal.load("tx-gameplay-001")["status"] == "committed"
    assert receipts.get("request-gameplay") is not None
    assert repository.current_revision() == 4


def test_find_transaction_commit_keeps_bounded_history_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, repository, stager, wal, receipts, coordinator = create_git_campaign(tmp_path)
    transaction = execute_gameplay(coordinator)
    (root / "later.txt").write_text("later non-transaction commit\n", encoding="utf-8")
    git(root, "add", "later.txt")
    git(root, "commit", "-qm", "later commit")

    assert stager.find_transaction_commit("tx-gameplay-001", max_count=1) is None

    calls = []
    original_run = stager._run_bytes

    def recording_run(arguments, input_bytes=None):
        arguments = tuple(arguments)
        calls.append(arguments)
        return original_run(arguments, input_bytes)

    monkeypatch.setattr(stager, "_run_bytes", recording_run)
    found = stager.find_transaction_commit("tx-gameplay-001", max_count=2)

    assert found is not None
    assert found.commit_hash == transaction.commit_hash
    assert found.paths == ("state/meta.json", "state/owner.json")
    assert [arguments[0] for arguments in calls] == [
        "rev-parse",
        "show",
        "diff-tree",
        "log",
        "show",
        "diff-tree",
    ]
    assert calls[3] == ("log", "--all", "--format=%H", "--max-count=2")


def test_recovery_head_fast_path_still_verifies_exact_manifest_paths(
    tmp_path: Path,
) -> None:
    root, repository, stager, wal, receipts, coordinator = create_git_campaign(tmp_path)
    with pytest.raises(InjectedCrash):
        execute_gameplay(coordinator, crash_phase="after_git_commit")

    (root / "unexpected.txt").write_text("not in the WAL manifest\n", encoding="utf-8")
    git(root, "add", "unexpected.txt")
    git(root, "commit", "--amend", "--no-edit", "-q")

    with pytest.raises(CommitVerificationError, match="commit paths"):
        coordinator.recover()

    assert wal.load("tx-gameplay-001")["status"] == "applied"
    assert receipts.get("request-gameplay") is None
    assert repository.current_revision() == 4


def test_stale_command_fails_without_wal_or_git_change(tmp_path: Path) -> None:
    root, repository, stager, wal, receipts, coordinator = create_git_campaign(tmp_path)
    original_head = stager.head()
    with pytest.raises(StaleRevisionError):
        coordinator.execute(
            gameplay_command(revision=2, request_id="stale"),
            transaction_id="tx-stale",
            created_at="2026-08-09T12:00:01Z",
            writes=gameplay_writes(),
            result={"status": "committed"},
            validator=lambda overlay, manifest: None,
        )
    assert repository.current_revision() == 3
    assert stager.head() == original_head
    assert wal.records() == ()
    assert receipts.get("stale") is None
    stager.assert_pristine()


def test_maintenance_commit_preserves_revision_and_raw_float_owner(
    tmp_path: Path,
) -> None:
    root, repository, stager, wal, receipts, coordinator = create_git_campaign(tmp_path)
    proposed = b'{\n  "ratio": 1.25,\n  "label": "after-maintenance"\n}\n'

    def validate(overlay, manifest) -> None:
        assert manifest.mode == "maintenance"
        assert manifest.base_revision == manifest.target_revision == 3
        assert overlay.read_bytes("game/rules/float-owner.json") == proposed
        assert overlay.read_json("game/rules/float-owner.json")["ratio"] == 1.25

    execution = coordinator.execute(
        maintenance_command(),
        transaction_id="tx-maintenance-001",
        created_at="2026-08-09T12:00:02Z",
        writes={"game/rules/float-owner.json": proposed},
        result={"status": "committed", "issue_id": "M-001"},
        validator=validate,
    )
    assert execution.status == "committed"
    assert execution.receipt.committed_revision == 3
    assert repository.current_revision() == 3
    assert repository.read_bytes("game/rules/float-owner.json") == proposed
    commit = stager.get_commit(execution.commit_hash)
    assert commit.trailers["Shinobi-World-Revision"] == "3"
    assert commit.trailers["Shinobi-Mode"] == "maintenance"
    assert commit.paths == ("game/rules/float-owner.json",)


def test_git_commit_failure_rolls_back_exact_bytes_and_index(tmp_path: Path) -> None:
    root, repository, stager, wal, receipts, coordinator = create_git_campaign(tmp_path)
    original_head = stager.head()
    original_meta = repository.read_bytes("state/meta.json")
    original_owner = repository.read_bytes("state/owner.json")
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    os.chmod(hook, 0o755)

    with pytest.raises(GitCommitError):
        execute_gameplay(coordinator)

    assert stager.head() == original_head
    assert repository.read_bytes("state/meta.json") == original_meta
    assert repository.read_bytes("state/owner.json") == original_owner
    assert repository.current_revision() == 3
    assert wal.load("tx-gameplay-001")["status"] == "rolled_back"
    assert receipts.get("request-gameplay") is None
    stager.assert_pristine()

    # A rolled-back transaction ID is an audit identity, not a reusable slot.
    hook.unlink()
    with pytest.raises(WalError, match="retry with a new transaction ID"):
        execute_gameplay(coordinator)
    assert repository.current_revision() == 3
    stager.assert_pristine()


def test_coordinator_fails_closed_on_preexisting_staged_path(tmp_path: Path) -> None:
    root, repository, stager, wal, receipts, coordinator = create_git_campaign(tmp_path)
    (root / "unexpected.txt").write_text("not part of transaction\n", encoding="utf-8")
    git(root, "add", "unexpected.txt")
    with pytest.raises(DirtyRepositoryError):
        execute_gameplay(coordinator)
    assert stager.staged_paths() == ("unexpected.txt",)
    assert repository.current_revision() == 3
    assert wal.records() == ()


def test_git_pristine_rejects_ignored_campaign_authority_file(
    tmp_path: Path,
) -> None:
    root, repository, stager, wal, receipts, coordinator = create_git_campaign(tmp_path)
    (root / ".gitignore").write_text("state/ignored-owner.json\n", encoding="utf-8")
    git(root, "add", ".gitignore")
    git(root, "commit", "-qm", "ignore adversarial owner")
    ignored = root / "state" / "ignored-owner.json"
    ignored.write_text('{"schema":"adversarial"}\n', encoding="utf-8")

    assert git(root, "status", "--porcelain") == ""
    assert stager.untracked_paths() == ("state/ignored-owner.json",)
    with pytest.raises(DirtyRepositoryError):
        stager.assert_pristine()


@pytest.mark.parametrize("phase", TransactionCoordinator.PHASES)
def test_crash_injection_recovery_at_every_coordinator_phase(
    tmp_path: Path,
    phase: str,
) -> None:
    root, repository, stager, wal, receipts, coordinator = create_git_campaign(tmp_path)
    with pytest.raises(InjectedCrash):
        execute_gameplay(coordinator, crash_phase=phase)

    recovered = coordinator.recover()
    committed_phases = {
        "after_git_commit",
        "after_readback",
        "after_remote_push",
        "after_wal_commit",
        "after_receipt",
    }
    if phase in committed_phases:
        assert repository.current_revision() == 4
        assert repository.read_bytes("state/owner.json") == gameplay_writes()["state/owner.json"]
        receipt = receipts.get("request-gameplay")
        assert receipt is not None
        assert receipt.committed_revision == 4
        assert stager.find_transaction_commit("tx-gameplay-001") is not None
    else:
        assert repository.current_revision() == 3
        assert repository.read_json("state/owner.json")["status"] == "before"
        assert receipts.get("request-gameplay") is None
        assert stager.find_transaction_commit("tx-gameplay-001") is None
    stager.assert_pristine()
