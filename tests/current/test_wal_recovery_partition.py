from __future__ import annotations

import json
from pathlib import Path

from shinobi_runtime.store.repository import RepositoryStore
from shinobi_runtime.tx.canonical import canonical_json_bytes, sha256_bytes
from shinobi_runtime.tx.manifest import FileMutation, TransactionManifest
from shinobi_runtime.tx.wal import WriteAheadLog


def _fixture(tmp_path: Path):
    root = tmp_path / "repo"
    (root / "state").mkdir(parents=True)
    before = b'{"schema":"meta","campaign_id":"test","revision":1}\n'
    after = b'{"schema":"meta","campaign_id":"test","revision":2}\n'
    (root / "state" / "meta.json").write_bytes(before)
    repo = RepositoryStore(root)
    mutation = FileMutation("state/meta.json", sha256_bytes(before), after)
    manifest = TransactionManifest(
        transaction_id="tx.partition.proof",
        campaign_id="test",
        request_id="req.partition.proof",
        command_digest="a" * 64,
        mode="gameplay",
        base_revision=1,
        target_revision=2,
        created_at="0061-09-27T21:15:54Z",
        mutations=(mutation,),
    )
    wal = WriteAheadLog(tmp_path / "runtime" / "wal")
    return repo, wal, manifest, before, after


def test_terminal_wal_history_is_outside_recovery_hot_path(tmp_path, monkeypatch):
    repo, wal, manifest, _before, _after = _fixture(tmp_path)
    wal.prepare(manifest, repo)
    wal.finish(manifest.transaction_id, repo)
    wal.mark_committed(manifest.transaction_id, repo)
    wal.archive_terminal(manifest.transaction_id)

    assert not tuple(wal.pending_directory.glob("*.json"))
    assert len(tuple(wal.terminal_directory.glob("*.json"))) == 1

    original = wal._read_path
    def bounded(path):
        if Path(path).parent == wal.terminal_directory:
            raise AssertionError("recovery hot path scanned terminal WAL history")
        return original(path)
    monkeypatch.setattr(wal, "_read_path", bounded)
    assert wal.recoverable_records() == ()
    assert wal.pending() == ()


def test_exact_rolled_back_transaction_can_be_prepared_again(tmp_path):
    repo, wal, manifest, before, _after = _fixture(tmp_path)
    wal.prepare(manifest, repo)
    wal.finish(manifest.transaction_id, repo)
    wal.rollback(manifest.transaction_id, repo)
    assert (repo.root / "state" / "meta.json").read_bytes() == before
    assert wal.load(manifest.transaction_id)["status"] == "rolled_back"

    retried = wal.prepare(manifest, repo)
    assert retried["status"] == "prepared"
    assert wal.load(manifest.transaction_id)["status"] == "prepared"
    assert len(wal.recoverable_records()) == 1


def test_legacy_flat_wal_is_migrated_into_partitioned_layout(tmp_path):
    repo, wal, manifest, _before, _after = _fixture(tmp_path)
    record = wal.prepare(manifest, repo)
    pending = wal._pending_path(manifest.transaction_id)
    legacy = wal.directory / pending.name
    legacy.write_bytes(pending.read_bytes())
    pending.unlink()

    restarted = WriteAheadLog(wal.directory)
    assert not legacy.exists()
    assert restarted._pending_path(manifest.transaction_id).exists()
    assert restarted.load(manifest.transaction_id)["status"] == "prepared"


def test_corrupt_legacy_flat_wal_fails_closed(tmp_path):
    from shinobi_runtime.tx.errors import WalError
    directory = tmp_path / "runtime" / "wal"
    directory.mkdir(parents=True)
    (directory / "corrupt.json").write_text("{not-json", encoding="utf-8")
    import pytest
    with pytest.raises(WalError):
        WriteAheadLog(directory)


def test_legacy_terminal_wal_migrates_losslessly_to_terminal_partition(tmp_path):
    repo, wal, manifest, _before, _after = _fixture(tmp_path)
    wal.prepare(manifest, repo)
    wal.finish(manifest.transaction_id, repo)
    wal.rollback(manifest.transaction_id, repo)
    terminal = wal._terminal_path(manifest.transaction_id)
    legacy = wal.directory / terminal.name
    legacy.write_bytes(terminal.read_bytes())
    terminal.unlink()

    restarted = WriteAheadLog(wal.directory)
    assert not legacy.exists()
    assert restarted._terminal_path(manifest.transaction_id).exists()
    assert restarted.load(manifest.transaction_id)["status"] == "rolled_back"
