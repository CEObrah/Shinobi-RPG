from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

import shinobi_runtime.bootstrap as bootstrap_module
from shinobi_runtime.bootstrap import BootstrapError, CheckoutSettings, ensure_checkout

CAMPAIGN_ID = "jianghu-wei-main"
PLAYER_ID = "pc_wei_tang"
WORLD_TIME = "SE-0061-09-27T21:21:45"
BASELINE_A = "test-baseline-a"


def git(root: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert cp.returncode == 0, cp.stderr
    return cp.stdout.strip()


def meta(revision: int) -> str:
    return json.dumps(
        {
            "schema": "meta",
            "campaign_id": CAMPAIGN_ID,
            "revision": revision,
            "player_id": PLAYER_ID,
            "time": WORLD_TIME,
        },
        separators=(",", ":"),
    ) + "\n"


def state_digest(root: Path) -> str:
    state = root / "state"
    digest = hashlib.sha256()
    for path in sorted(
        (path for path in state.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(state).as_posix(),
    ):
        rel = path.relative_to(state).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def release_contract(root: Path, baseline_id: str, revision: int) -> str:
    return json.dumps(
        {
            "schema": "release-campaign-state-contract-1.0",
            "release_baseline_id": baseline_id,
            "campaign_id": CAMPAIGN_ID,
            "player_id": PLAYER_ID,
            "revision": revision,
            "world_time": WORLD_TIME,
            "state_tree_sha256": state_digest(root),
        },
        separators=(",", ":"),
    ) + "\n"


def make_repo(tmp_path: Path) -> tuple[Path, Path, CheckoutSettings]:
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    git(source, "config", "user.email", "source@example.invalid")
    git(source, "config", "user.name", "Source Test")
    (source / "state").mkdir()
    (source / "runtime" / "shinobi_runtime").mkdir(parents=True)
    (source / "state" / "meta.json").write_text(meta(1), encoding="utf-8")
    (source / "runtime" / "shinobi_runtime" / "engine.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "runtime" / "contracts").mkdir(parents=True)
    (source / "runtime" / "contracts" / "release-campaign-state.json").write_text(
        release_contract(source, BASELINE_A, 1), encoding="utf-8"
    )
    git(source, "add", ".")
    git(source, "commit", "-qm", "revision-1 baseline")

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(source), str(remote)], check=True)
    git(source, "remote", "add", "origin", str(remote))

    settings = CheckoutSettings(
        campaign_root=tmp_path / "volume" / "campaign",
        runtime_root=tmp_path / "volume" / "runtime",
        git_url=str(remote),
        branch="main",
    )
    return source, remote, settings


def test_single_main_preserves_state_commit_across_source_release(tmp_path: Path) -> None:
    source, _remote, settings = make_repo(tmp_path)
    checkout = ensure_checkout(settings)
    assert git(checkout, "branch", "--show-current") == "main"

    git(checkout, "config", "user.email", "runtime@example.invalid")
    git(checkout, "config", "user.name", "Runtime Test")
    (checkout / "state" / "meta.json").write_text(meta(2), encoding="utf-8")
    git(checkout, "add", "state/meta.json")
    git(checkout, "commit", "-qm", "runtime gameplay revision 2")
    git(checkout, "push", "-q", "origin", "main")

    git(source, "pull", "-q", "--ff-only", "origin", "main")
    (source / "runtime" / "shinobi_runtime" / "engine.py").write_text("VALUE = 2\n", encoding="utf-8")
    git(source, "add", "runtime/shinobi_runtime/engine.py")
    git(source, "commit", "-qm", "source release after gameplay")
    source_head = git(source, "rev-parse", "HEAD")
    git(source, "push", "-q", "origin", "main")

    ensure_checkout(settings)
    assert git(checkout, "branch", "--show-current") == "main"
    assert git(checkout, "rev-parse", "HEAD") == source_head
    saved = json.loads((checkout / "state" / "meta.json").read_text(encoding="utf-8"))
    assert saved["revision"] == 2
    assert (checkout / "runtime" / "shinobi_runtime" / "engine.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_replacement_release_baseline_can_reset_a_newer_stale_persistent_volume(
    tmp_path: Path,
) -> None:
    _source, remote, settings = make_repo(tmp_path)
    checkout = ensure_checkout(settings)
    git(checkout, "config", "user.email", "runtime@example.invalid")
    git(checkout, "config", "user.name", "Runtime Test")
    (checkout / "state/meta.json").write_text(meta(2), encoding="utf-8")
    git(checkout, "add", "state/meta.json")
    git(checkout, "commit", "-qm", "stale gameplay revision 2")
    stale_head = git(checkout, "rev-parse", "HEAD")
    git(checkout, "push", "-q", "origin", "main")
    old_receipt = settings.runtime_root / "receipts" / "old-request.json"
    old_receipt.parent.mkdir(parents=True, exist_ok=True)
    old_receipt.write_text("retired receipt\n", encoding="utf-8")

    replacement = tmp_path / "replacement"
    replacement.mkdir()
    git(replacement, "init", "-q", "-b", "main")
    git(replacement, "config", "user.email", "reset@example.invalid")
    git(replacement, "config", "user.name", "Reset Test")
    (replacement / "state").mkdir()
    (replacement / "runtime" / "shinobi_runtime").mkdir(parents=True)
    (replacement / "runtime" / "contracts").mkdir(parents=True)
    (replacement / "state/meta.json").write_text(meta(1), encoding="utf-8")
    (replacement / "runtime/shinobi_runtime/engine.py").write_text("VALUE = 3\n", encoding="utf-8")
    (replacement / "runtime/contracts/release-campaign-state.json").write_text(
        release_contract(replacement, "test-baseline-b", 1), encoding="utf-8"
    )
    git(replacement, "add", ".")
    git(replacement, "commit", "-qm", "replacement certified baseline")
    replacement_head = git(replacement, "rev-parse", "HEAD")
    git(replacement, "remote", "add", "origin", str(remote))
    git(replacement, "push", "-q", "--force", "origin", "main")

    ensure_checkout(settings)

    assert git(checkout, "rev-parse", "HEAD") == replacement_head
    assert git(checkout, "rev-parse", "HEAD") != stale_head
    saved = json.loads((checkout / "state/meta.json").read_text(encoding="utf-8"))
    assert saved["revision"] == 1
    contract = json.loads(
        (checkout / "runtime/contracts/release-campaign-state.json").read_text(encoding="utf-8")
    )
    assert contract["release_baseline_id"] == "test-baseline-b"
    assert not (settings.runtime_root / "receipts").exists()
    retired_receipts = list(
        (settings.runtime_root / "retired-release-baselines").glob(
            "*/receipts/old-request.json"
        )
    )
    assert len(retired_receipts) == 1
    assert retired_receipts[0].read_text(encoding="utf-8") == "retired receipt\n"


def test_fast_forward_release_baseline_change_is_verified_and_retires_old_receipts(
    tmp_path: Path,
) -> None:
    source, _remote, settings = make_repo(tmp_path)
    checkout = ensure_checkout(settings)
    old_head = git(checkout, "rev-parse", "HEAD")
    old_receipt = settings.runtime_root / "receipts" / "old-request.json"
    old_receipt.parent.mkdir(parents=True, exist_ok=True)
    old_receipt.write_text("old lineage\n", encoding="utf-8")

    contract_path = source / "runtime/contracts/release-campaign-state.json"
    contract_path.write_text(
        release_contract(source, "test-baseline-b", 1), encoding="utf-8"
    )
    git(source, "add", "runtime/contracts/release-campaign-state.json")
    git(source, "commit", "-qm", "declare fast-forward replacement baseline")
    new_head = git(source, "rev-parse", "HEAD")
    git(source, "push", "-q", "origin", "main")

    ensure_checkout(settings)

    assert git(checkout, "rev-parse", "HEAD") == new_head
    assert git(checkout, "rev-parse", "HEAD") != old_head
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    assert contract["release_baseline_id"] == "test-baseline-b"
    assert not (settings.runtime_root / "receipts").exists()
    retired = list(
        (settings.runtime_root / "retired-release-baselines").glob(
            "*/receipts/old-request.json"
        )
    )
    assert len(retired) == 1
    assert retired[0].read_text(encoding="utf-8") == "old lineage\n"


def test_release_baseline_recovery_retirement_failure_restores_previous_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _remote, settings = make_repo(tmp_path)
    checkout = ensure_checkout(settings)
    old_head = git(checkout, "rev-parse", "HEAD")
    contract_path = source / "runtime/contracts/release-campaign-state.json"
    contract_path.write_text(
        release_contract(source, "test-baseline-b", 1), encoding="utf-8"
    )
    git(source, "add", "runtime/contracts/release-campaign-state.json")
    git(source, "commit", "-qm", "declare replacement baseline")
    git(source, "push", "-q", "origin", "main")

    def fail_retirement(*_args: object, **_kwargs: object) -> None:
        raise BootstrapError("release baseline recovery-store retirement failed")

    monkeypatch.setattr(bootstrap_module, "_retire_recovery_store", fail_retirement)
    with pytest.raises(BootstrapError, match="recovery-store retirement failed"):
        ensure_checkout(settings)

    assert git(checkout, "rev-parse", "HEAD") == old_head
    assert json.loads((checkout / "state/meta.json").read_text(encoding="utf-8"))["revision"] == 1


def test_replacement_release_baseline_fails_closed_when_state_digest_is_wrong(
    tmp_path: Path,
) -> None:
    _source, remote, settings = make_repo(tmp_path)
    checkout = ensure_checkout(settings)
    git(checkout, "config", "user.email", "runtime@example.invalid")
    git(checkout, "config", "user.name", "Runtime Test")
    (checkout / "state/meta.json").write_text(meta(2), encoding="utf-8")
    git(checkout, "add", "state/meta.json")
    git(checkout, "commit", "-qm", "local gameplay revision 2")
    local_head = git(checkout, "rev-parse", "HEAD")

    replacement = tmp_path / "bad-replacement"
    replacement.mkdir()
    git(replacement, "init", "-q", "-b", "main")
    git(replacement, "config", "user.email", "reset@example.invalid")
    git(replacement, "config", "user.name", "Reset Test")
    (replacement / "state").mkdir()
    (replacement / "runtime" / "shinobi_runtime").mkdir(parents=True)
    (replacement / "runtime" / "contracts").mkdir(parents=True)
    (replacement / "state/meta.json").write_text(meta(1), encoding="utf-8")
    (replacement / "runtime/shinobi_runtime/engine.py").write_text("VALUE = 3\n", encoding="utf-8")
    bad = json.loads(release_contract(replacement, "test-baseline-b", 1))
    bad["state_tree_sha256"] = "f" * 64
    (replacement / "runtime/contracts/release-campaign-state.json").write_text(
        json.dumps(bad, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    git(replacement, "add", ".")
    git(replacement, "commit", "-qm", "bad replacement baseline")
    git(replacement, "remote", "add", "origin", str(remote))
    git(replacement, "push", "-q", "--force", "origin", "main")

    with pytest.raises(BootstrapError, match="replacement release baseline failed verification"):
        ensure_checkout(settings)

    assert git(checkout, "rev-parse", "HEAD") == local_head
    assert json.loads((checkout / "state/meta.json").read_text(encoding="utf-8"))["revision"] == 2
