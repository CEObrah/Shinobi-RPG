from pathlib import Path
import subprocess

import pytest

from shinobi_runtime.store import CommittedContentRootCache, content_root


def test_content_root_is_path_and_byte_deterministic(tmp_path: Path):
    (tmp_path / "state" / "b").mkdir(parents=True)
    (tmp_path / "state" / "a.json").write_bytes(b"one\n")
    (tmp_path / "state" / "b" / "x.json").write_bytes(b"two\n")
    first = content_root(tmp_path)
    second = content_root(tmp_path)
    assert first == second
    assert [entry.path for entry in first.entries] == [
        "state/a.json", "state/b/x.json"
    ]

    (tmp_path / "state" / "b" / "x.json").write_bytes(b"changed\n")
    assert content_root(tmp_path).root_sha256 != first.root_sha256


def test_current_campaign_root_is_stable_and_nonempty():
    root = Path(__file__).resolve().parents[2]
    first = content_root(root)
    second = content_root(root)
    assert first.root_sha256 == second.root_sha256
    # State locality is a quality goal. The campaign intentionally removed the
    # retired 1,848-file micro-organization/capability/kernel fanout, so file count must not be a
    # proxy for completeness. Verify a substantial state root and the new
    # bounded formation authority instead.
    assert len(first.entries) > 400
    assert any(entry.path == "state/meta.json" for entry in first.entries)
    assert any(entry.path.startswith("state/formation/") for entry in first.entries)
    assert not any(entry.path.startswith("state/unit/") for entry in first.entries)
    assert not any(entry.path.startswith("state/unit-capability/") for entry in first.entries)
    assert not any(entry.path.startswith("state/unit-kernel/") for entry in first.entries)


def test_committed_root_cache_reuses_one_commit_and_invalidates_on_new_key(
    tmp_path: Path,
):
    (tmp_path / "state").mkdir()
    owner = tmp_path / "state" / "owner.json"
    owner.write_bytes(b'{"value":1}\n')
    cache = CommittedContentRootCache(tmp_path)
    first = cache.read("commit-one")

    # The cache contract is deliberately keyed by a caller-proven clean Git
    # commit.  Reusing that key avoids reopening the state tree.
    owner.write_bytes(b'{"value":2}\n')
    assert cache.read("commit-one") is first

    second = cache.read("commit-two")
    assert second.root_sha256 != first.root_sha256


def test_tracked_only_cache_excludes_ignored_files_from_head_identity(
    tmp_path: Path,
):
    (tmp_path / "state").mkdir()
    tracked = tmp_path / "state" / "tracked.json"
    ignored = tmp_path / "state" / "ignored.json"
    tracked.write_bytes(b'{"tracked":1}\n')
    ignored.write_bytes(b'{"ignored":1}\n')
    (tmp_path / ".gitignore").write_text("state/ignored.json\n", encoding="utf-8")

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".gitignore", "state/tracked.json")
    _git(
        tmp_path,
        "-c", "user.name=Shinobi Runtime",
        "-c", "user.email=runtime@example.invalid",
        "commit", "-qm", "tracked root fixture",
    )
    head = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()

    tracked_root = content_root(tmp_path, tracked_only=True)
    all_files_root = content_root(tmp_path)
    assert [entry.path for entry in tracked_root.entries] == [
        "state/tracked.json",
    ]
    assert [entry.path for entry in all_files_root.entries] == [
        "state/ignored.json",
        "state/tracked.json",
    ]

    cache = CommittedContentRootCache(tmp_path, tracked_only=True)
    first = cache.read(head)
    ignored.write_bytes(b'{"ignored":2,"adversarial_padding":"x"}\n')
    assert _git(tmp_path, "status", "--porcelain").stdout == ""
    assert cache.read(head) is first
    assert content_root(tmp_path, tracked_only=True).root_sha256 == first.root_sha256
    assert content_root(tmp_path).root_sha256 != all_files_root.root_sha256

    tracked.write_bytes(b'{"tracked":2}\n')
    assert cache.read("different-key").root_sha256 != first.root_sha256


def test_tracked_only_rejects_tracked_symlinks(tmp_path: Path):
    (tmp_path / "state").mkdir()
    (tmp_path / "outside.json").write_bytes(b'{"outside":true}\n')
    (tmp_path / "state" / "link.json").symlink_to("../outside.json")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "state/link.json")

    with pytest.raises(ValueError, match="do not follow symlinks"):
        content_root(tmp_path, tracked_only=True)


def test_content_root_rejects_absolute_or_escaping_include_roots(tmp_path: Path):
    (tmp_path / "state").mkdir()
    with pytest.raises(ValueError, match="relative"):
        content_root(tmp_path, include_roots=(str(tmp_path / "state"),))
    with pytest.raises(ValueError, match="relative"):
        content_root(tmp_path, include_roots=("../state",))


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
