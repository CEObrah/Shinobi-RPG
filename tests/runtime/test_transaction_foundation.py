import json
import os
import subprocess
from pathlib import Path

import pytest

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.tx import (
    AtomicManifestPersister,
    GitStager,
    IdempotencyReceipt,
    ReceiptStore,
    TransactionPlanner,
    WriteAheadLog,
    canonical_json_bytes,
)
from shinobi_runtime.tx.errors import (
    ConcurrentModificationError,
    IdempotencyConflictError,
    LockUnavailableError,
    PartialApplyError,
    StaleRevisionError,
)
from shinobi_runtime.tx.locking import SingleWriterLock


def make_repository(tmp_path: Path, revision: int = 7) -> RepositoryStore:
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "meta.json").write_bytes(
        canonical_json_bytes(
            {
                "schema": "meta",
                "campaign_id": "test-campaign",
                "revision": revision,
            }
        )
    )
    return RepositoryStore(tmp_path)


def command(
    revision: int = 7,
    request_id: str = "req-001",
    value: str = "wait",
) -> CommandEnvelope:
    return CommandEnvelope(
        campaign_id="test-campaign",
        request_id=request_id,
        actor_id="pc_wei_tang",
        command_type="act",
        expected_revision=revision,
        submitted_at="2026-08-09T12:00:00Z",
        payload={"intent": value, "duration_seconds": 60},
    )


def advanced_meta(revision: int) -> bytes:
    return canonical_json_bytes(
        {"schema": "meta", "campaign_id": "test-campaign", "revision": revision}
    )


def test_canonical_json_is_order_independent_and_forbids_float() -> None:
    left = {"z": [3, {"b": "雪", "a": True}], "a": None}
    right = {"a": None, "z": [3, {"a": True, "b": "雪"}]}
    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert canonical_json_bytes(left).endswith(b"\n")
    with pytest.raises(TypeError, match="floating-point"):
        canonical_json_bytes({"unsafe": 0.1})


def test_envelope_payload_is_frozen_and_digest_is_deterministic() -> None:
    source = {"b": [2, 3], "a": "same"}
    first = command()
    second = CommandEnvelope(
        campaign_id="test-campaign",
        request_id="req-001",
        actor_id="pc_wei_tang",
        command_type="act",
        expected_revision=7,
        submitted_at="2026-08-09T12:00:00Z",
        payload={"duration_seconds": 60, "intent": "wait"},
    )
    assert first.digest == second.digest
    frozen = CommandEnvelope(
        campaign_id="test-campaign",
        request_id="freeze",
        actor_id="pc_wei_tang",
        command_type="act",
        expected_revision=7,
        submitted_at="2026-08-09T12:00:00Z",
        payload=source,
    )
    source["a"] = "changed"
    assert frozen.to_record()["payload"]["a"] == "same"
    with pytest.raises(TypeError):
        frozen.payload["a"] = "cannot mutate"


def test_planner_rejects_stale_base_before_building_manifest(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    planner = TransactionPlanner(repository)
    with pytest.raises(StaleRevisionError) as error:
        planner.plan(
            command(revision=6),
            transaction_id="tx-stale",
            created_at="2026-08-09T12:00:01Z",
            writes={"state/meta.json": advanced_meta(7)},
        )
    assert error.value.expected == 6
    assert error.value.actual == 7


def test_persister_rechecks_owner_hashes_after_planning(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    manifest = TransactionPlanner(repository).plan(
        command(request_id="req-race"),
        transaction_id="tx-race",
        created_at="2026-08-09T12:00:01Z",
        writes={"state/meta.json": advanced_meta(8)},
    )
    (tmp_path / "state" / "meta.json").write_bytes(advanced_meta(9))
    with pytest.raises(ConcurrentModificationError):
        AtomicManifestPersister(repository).apply(manifest)


def test_single_writer_lock_rejects_concurrent_holder(tmp_path: Path) -> None:
    first = SingleWriterLock(tmp_path / "runtime" / "writer.lock")
    first.acquire()
    try:
        with pytest.raises(LockUnavailableError):
            SingleWriterLock(tmp_path / "runtime" / "writer.lock").acquire()
    finally:
        first.release()
    with SingleWriterLock(tmp_path / "runtime" / "writer.lock") as acquired:
        assert acquired.acquired


def test_receipt_duplicate_returns_original_and_changed_request_conflicts(tmp_path: Path) -> None:
    store = ReceiptStore(tmp_path / "receipts")
    original = command()
    receipt = IdempotencyReceipt.for_command(
        original,
        transaction_id="tx-001",
        committed_revision=8,
        committed_at="2026-08-09T12:00:02Z",
        result={"status": "committed", "visible": ["one minute passes"]},
    )
    assert store.put(receipt) == receipt
    assert store.put(receipt).transaction_id == "tx-001"
    assert store.lookup(original).result["status"] == "committed"

    changed = command(value="train")
    with pytest.raises(IdempotencyConflictError):
        store.lookup(changed)


def _partial_apply(path: str, index: int) -> None:
    if index == 1:
        raise RuntimeError("simulated process crash")


def test_wal_can_roll_back_partial_atomic_application(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    planner = TransactionPlanner(repository)
    manifest = planner.plan(
        command(),
        transaction_id="tx-rollback",
        created_at="2026-08-09T12:00:03Z",
        writes={
            "state/example.json": canonical_json_bytes({"value": "after"}),
            "state/meta.json": advanced_meta(8),
        },
    )
    wal = WriteAheadLog(tmp_path / "runtime" / "wal")
    wal.prepare(manifest, repository)
    persister = AtomicManifestPersister(repository)
    with pytest.raises(PartialApplyError):
        persister.apply(manifest, after_apply=_partial_apply)

    assert wal.classify("tx-rollback", repository) == "partial"
    record = wal.rollback("tx-rollback", repository)
    assert record["status"] == "rolled_back"
    assert repository.read_optional_bytes("state/example.json") is None
    assert repository.current_revision() == 7
    assert wal.pending() == ()


def test_wal_can_finish_partial_atomic_application_and_commit(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    manifest = TransactionPlanner(repository).plan(
        command(request_id="req-finish"),
        transaction_id="tx-finish",
        created_at="2026-08-09T12:00:04Z",
        writes={
            "state/example.json": canonical_json_bytes({"value": "after"}),
            "state/meta.json": advanced_meta(8),
        },
    )
    wal = WriteAheadLog(tmp_path / "runtime" / "wal")
    wal.prepare(manifest, repository)
    with pytest.raises(PartialApplyError):
        AtomicManifestPersister(repository).apply(
            manifest, after_apply=_partial_apply
        )

    applied = wal.finish("tx-finish", repository)
    assert applied["status"] == "applied"
    assert repository.current_revision() == 8
    assert json.loads(repository.read_bytes("state/example.json"))["value"] == "after"
    committed = wal.mark_committed("tx-finish", repository)
    assert committed["status"] == "committed"
    assert wal.pending() == ()


def test_git_stager_adds_only_literal_explicit_paths(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "config",
            "user.email",
            "test@example.invalid",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Runtime Test"],
        check=True,
    )
    (tmp_path / "first.json").write_text("one\n", encoding="utf-8")
    (tmp_path / "second.json").write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "first.json", "second.json"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "base"], check=True)

    (tmp_path / "first.json").write_text("changed one\n", encoding="utf-8")
    (tmp_path / "second.json").write_text("changed two\n", encoding="utf-8")
    stager = GitStager(tmp_path)
    assert stager.stage(["first.json"]) == ("first.json",)
    assert stager.staged_paths() == ("first.json",)
    unstaged = subprocess.run(
        ["git", "-C", str(tmp_path), "diff", "--name-only"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.splitlines()
    assert unstaged == ["second.json"]


def test_git_stager_commit_has_runtime_identity_without_git_user_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "fresh-clone"
    (root / "state").mkdir(parents=True)
    (root / "state" / "meta.json").write_bytes(advanced_meta(7))
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "add", "state/meta.json"], check=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Bootstrap",
            "-c",
            "user.email=bootstrap@invalid",
            "commit",
            "-qm",
            "baseline",
        ],
        check=True,
    )
    # Isolate the operation from any machine-level identity, as in a fresh
    # Railway clone with no user.name/user.email configured.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    repository = RepositoryStore(root)
    manifest = TransactionPlanner(repository).plan(
        command(request_id="req-no-git-identity"),
        transaction_id="tx-no-git-identity",
        created_at="2026-08-09T12:00:05Z",
        writes={"state/meta.json": advanced_meta(8)},
    )
    AtomicManifestPersister(repository).apply(manifest)
    stager = GitStager(root)
    stager.stage(manifest.paths)

    commit = stager.commit(manifest)

    identity = subprocess.run(
        ["git", "-C", str(root), "show", "-s", "--format=%an <%ae>", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert identity == "Shinobi Runtime <runtime@invalid>"
    assert commit.commit_hash == stager.head()
