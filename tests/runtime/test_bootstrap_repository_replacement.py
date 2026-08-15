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


def source_and_remote_with_campaign(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    git(source, "config", "user.email", "bootstrap@example.invalid")
    git(source, "config", "user.name", "Bootstrap Test")
    (source / "state").mkdir()
    (source / "state" / "meta.json").write_text(
        '{"campaign_id":"shinobi-test","revision":18}\n',
        encoding="utf-8",
    )
    (source / "state" / "player.json").write_text(
        '{"id":"pc","value":1}\n',
        encoding="utf-8",
    )
    (source / "README.md").write_text("baseline\n", encoding="utf-8")
    git(source, "add", "state", "README.md")
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


def replace_remote_history(
    source: Path,
    *,
    revision: int,
    player_value: int,
    campaign_id: str = "shinobi-test",
    formatted: bool = False,
) -> str:
    git(source, "checkout", "--orphan", "replacement")
    git(source, "rm", "-rf", ".")
    (source / "state").mkdir()
    if formatted:
        meta = (
            "{\n"
            f'  "campaign_id": "{campaign_id}",\n'
            f'  "revision": {revision}\n'
            "}\n"
        )
        player = "{\n  \"value\": %d,\n  \"id\": \"pc\"\n}\n" % player_value
    else:
        meta = (
            f'{{"campaign_id":"{campaign_id}","revision":{revision}}}\n'
        )
        player = f'{{"id":"pc","value":{player_value}}}\n'
    (source / "state" / "meta.json").write_text(meta, encoding="utf-8")
    (source / "state" / "player.json").write_text(player, encoding="utf-8")
    (source / "README.md").write_text("replacement\n", encoding="utf-8")
    git(source, "add", "state", "README.md")
    git(source, "commit", "-qm", "replacement root")
    git(source, "branch", "-M", "main")
    git(source, "push", "-q", "--force", "origin", "main")
    return git(source, "rev-parse", "HEAD")


def test_bootstrap_rehomes_clean_replaced_history_when_campaign_authority_matches(
    tmp_path: Path,
) -> None:
    source, remote = source_and_remote(tmp_path)
    configured = settings(tmp_path, remote)
    checkout = ensure_checkout(configured)
    old_checkout_head = git(checkout, "rev-parse", "HEAD")

    # Simulate the production repository being recreated/replaced while the
    # Railway volume still contains the old lineage. Campaign truth is copied
    # byte-for-byte into the replacement root, but source/docs history is new.
    git(source, "checkout", "--orphan", "replacement")
    git(source, "rm", "-rf", ".")
    (source / "state").mkdir()
    (source / "state" / "meta.json").write_text('{"revision":18}\n', encoding="utf-8")
    (source / "README.md").write_text("remote replacement docs\n", encoding="utf-8")
    git(source, "add", "state/meta.json", "README.md")
    git(source, "commit", "-qm", "replacement root")
    git(source, "branch", "-M", "main")
    git(source, "push", "-q", "--force", "origin", "main")
    remote_head = git(source, "rev-parse", "HEAD")

    assert old_checkout_head != remote_head
    assert ensure_checkout(configured) == checkout
    assert git(checkout, "rev-parse", "HEAD") == remote_head
    assert (checkout / "README.md").read_text(encoding="utf-8") == "remote replacement docs\n"
    assert (checkout / "state" / "meta.json").read_text(encoding="utf-8") == '{"revision":18}\n'


def test_bootstrap_rehomes_semantically_equal_formatted_state_after_history_replacement(
    tmp_path: Path,
) -> None:
    source, remote = source_and_remote_with_campaign(tmp_path)
    configured = settings(tmp_path, remote)
    checkout = ensure_checkout(configured)

    remote_head = replace_remote_history(
        source,
        revision=18,
        player_value=1,
        formatted=True,
    )

    assert ensure_checkout(configured) == checkout
    assert git(checkout, "rev-parse", "HEAD") == remote_head


def test_bootstrap_rehomes_same_campaign_when_replaced_remote_revision_is_newer(
    tmp_path: Path,
) -> None:
    source, remote = source_and_remote_with_campaign(tmp_path)
    configured = settings(tmp_path, remote)
    checkout = ensure_checkout(configured)

    remote_head = replace_remote_history(
        source,
        revision=19,
        player_value=2,
    )

    assert ensure_checkout(configured) == checkout
    assert git(checkout, "rev-parse", "HEAD") == remote_head
    assert '"revision":19' in (checkout / "state" / "meta.json").read_text(encoding="utf-8")
    assert '"value":2' in (checkout / "state" / "player.json").read_text(encoding="utf-8")


def test_bootstrap_refuses_replaced_remote_when_local_campaign_revision_is_newer(
    tmp_path: Path,
) -> None:
    source, remote = source_and_remote_with_campaign(tmp_path)
    configured = settings(tmp_path, remote)
    checkout = ensure_checkout(configured)
    git(checkout, "config", "user.email", "runtime@example.invalid")
    git(checkout, "config", "user.name", "Runtime Test")

    (checkout / "state" / "meta.json").write_text(
        '{"campaign_id":"shinobi-test","revision":20}\n',
        encoding="utf-8",
    )
    (checkout / "state" / "player.json").write_text(
        '{"id":"pc","value":3}\n',
        encoding="utf-8",
    )
    git(checkout, "add", "state")
    git(checkout, "commit", "-qm", "local gameplay")

    replace_remote_history(source, revision=19, player_value=2)

    with pytest.raises(BootstrapError, match="local campaign revision 20 is newer"):
        ensure_checkout(configured)


def test_bootstrap_refuses_same_revision_conflicting_campaign_state(
    tmp_path: Path,
) -> None:
    source, remote = source_and_remote_with_campaign(tmp_path)
    configured = settings(tmp_path, remote)
    ensure_checkout(configured)

    replace_remote_history(source, revision=18, player_value=99)

    with pytest.raises(BootstrapError, match="conflict at revision 18"):
        ensure_checkout(configured)


def test_bootstrap_refuses_different_campaign_id_after_history_replacement(
    tmp_path: Path,
) -> None:
    source, remote = source_and_remote_with_campaign(tmp_path)
    configured = settings(tmp_path, remote)
    ensure_checkout(configured)

    replace_remote_history(
        source,
        revision=19,
        player_value=2,
        campaign_id="other-campaign",
    )

    with pytest.raises(BootstrapError, match="different campaign IDs"):
        ensure_checkout(configured)


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
