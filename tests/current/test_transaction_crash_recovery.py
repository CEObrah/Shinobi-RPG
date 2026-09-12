from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.tx import GitStager, ReceiptStore, TransactionCoordinator, WriteAheadLog


class ProcessCrash(BaseException):
    pass


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _git(root: Path, *args: str) -> None:
    completed = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def _fixture(tmp_path: Path):
    root = tmp_path / "repo"
    (root / "state").mkdir(parents=True)
    meta = {
        "schema": "jianghu.meta",
        "campaign_id": "campaign.crash.test",
        "game": "jianghu",
        "revision": 1,
        "time": "SE-0061-01-01T00:00:00",
        "player_id": "pc.test",
    }
    (root / "state/meta.json").write_bytes(_json_bytes(meta))
    (root / "state/example.json").write_bytes(_json_bytes({"value": "before"}))
    _git(root, "init", "-q")
    _git(root, "add", "state/meta.json", "state/example.json")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@invalid", "commit", "-qm", "baseline")

    runtime_root = tmp_path / "runtime"
    repository = RepositoryStore(root)

    def coordinator() -> TransactionCoordinator:
        return TransactionCoordinator(
            repository=RepositoryStore(root),
            git=GitStager(root),
            wal=WriteAheadLog(runtime_root / "wal"),
            receipts=ReceiptStore(runtime_root / "receipts"),
            lock_path=runtime_root / "runtime.lock",
        )

    command = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="request.crash.test",
        actor_id="pc.test",
        command_type="test_transition",
        expected_revision=1,
        submitted_at="0061-01-01T00:00:00Z",
        payload={"intent": "prove crash recovery"},
        mode="gameplay",
    )
    after_meta = dict(meta, revision=2, time="SE-0061-01-01T00:01:00")
    writes = {
        "state/meta.json": _json_bytes(after_meta),
        "state/example.json": _json_bytes({"value": "after"}),
    }
    return root, runtime_root, coordinator, command, writes


def _execute(coordinator: TransactionCoordinator, command: CommandEnvelope, writes, crash_injector=None):
    return coordinator.execute(
        command,
        transaction_id="tx.crash.recovery.test",
        created_at="0061-01-01T00:01:00Z",
        writes=writes,
        result={"status": "ok"},
        validator=lambda overlay, manifest: None,
        crash_injector=crash_injector,
    )


def test_process_death_after_owner_bytes_rolls_back_and_same_request_can_retry(tmp_path: Path):
    root, runtime_root, make_coordinator, command, writes = _fixture(tmp_path)

    def crash(phase, manifest):
        if phase == "after_apply":
            raise ProcessCrash()

    with pytest.raises(ProcessCrash):
        _execute(make_coordinator(), command, writes, crash)
    assert json.loads((root / "state/meta.json").read_text())["revision"] == 2

    recovered = make_coordinator().recover()
    assert any(decision.action == "rolled_back" for decision in recovered)
    assert json.loads((root / "state/meta.json").read_text())["revision"] == 1
    assert json.loads((root / "state/example.json").read_text())["value"] == "before"

    result = _execute(make_coordinator(), command, writes)
    assert result.status == "committed"
    assert result.receipt.committed_revision == 2
    assert not tuple((runtime_root / "wal/pending").glob("*.json"))


def test_process_death_after_git_commit_finalizes_one_receipt_without_second_mutation(tmp_path: Path):
    root, runtime_root, make_coordinator, command, writes = _fixture(tmp_path)

    def crash(phase, manifest):
        if phase == "after_git_commit":
            raise ProcessCrash()

    with pytest.raises(ProcessCrash):
        _execute(make_coordinator(), command, writes, crash)
    head_after_crash = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    assert json.loads((root / "state/meta.json").read_text())["revision"] == 2

    recovered = make_coordinator().recover()
    assert any(decision.action == "finalized_commit" for decision in recovered)
    receipt = make_coordinator().receipts.get(command.request_id)
    assert receipt is not None
    assert receipt.committed_revision == 2
    assert not tuple((runtime_root / "wal/pending").glob("*.json"))

    duplicate = _execute(make_coordinator(), command, writes)
    assert duplicate.status == "duplicate"
    assert subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip() == head_after_crash


def test_manifest_owned_atomic_temp_is_removed_during_recovery(tmp_path: Path):
    root, runtime_root, make_coordinator, command, writes = _fixture(tmp_path)

    def crash(phase, manifest):
        if phase == "after_apply":
            target = root / "state/meta.json"
            temp = target.parent / ".meta.json.owned.tmp"
            temp.write_bytes(target.read_bytes())
            raise ProcessCrash()

    with pytest.raises(ProcessCrash):
        _execute(make_coordinator(), command, writes, crash)
    make_coordinator().recover()
    assert not (root / "state/.meta.json.owned.tmp").exists()
    assert json.loads((root / "state/meta.json").read_text())["revision"] == 1


def test_unrelated_atomic_temp_remains_fail_closed(tmp_path: Path):
    from shinobi_runtime.tx.errors import RecoveryError

    root, _runtime_root, make_coordinator, command, writes = _fixture(tmp_path)

    def crash(phase, manifest):
        if phase == "after_apply":
            (root / "state/.unrelated.json.owned.tmp").write_text("unproven", encoding="utf-8")
            raise ProcessCrash()

    with pytest.raises(ProcessCrash):
        _execute(make_coordinator(), command, writes, crash)
    with pytest.raises(RecoveryError):
        make_coordinator().recover()
    assert (root / "state/.unrelated.json.owned.tmp").exists()
