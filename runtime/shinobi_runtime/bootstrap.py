"""Safe Railway checkout bootstrap followed by the private ASGI service.

Railway build files are ephemeral. Campaign commits therefore live in a Git
checkout on the mounted volume, while WAL and idempotency receipts live beside
it in a separate runtime directory. This module creates that checkout once,
safely synchronizes it on later boots, and refuses dirty or authority-changing
history divergence.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit


_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_CAMPAIGN_AUTHORITY_PATHS = ("state", "data", "schemas")


class BootstrapError(RuntimeError):
    """The persistent campaign checkout cannot be established safely."""


def _required_text(value: Optional[str], name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        raise BootstrapError(f"{name} must be non-empty bounded text")
    return value


def _safe_ref(value: Optional[str], name: str) -> str:
    value = _required_text(value, name)
    if not _SAFE_REF.fullmatch(value) or value.startswith("-") or ".." in value:
        raise BootstrapError(f"{name} is not a safe Git ref component")
    return value


@dataclass(frozen=True)
class CheckoutSettings:
    campaign_root: Path
    runtime_root: Path
    git_url: str
    remote: str = "origin"
    branch: str = "main"
    git_token: Optional[str] = None
    git_binary: str = "git"

    def __post_init__(self) -> None:
        campaign_root = Path(self.campaign_root).expanduser().resolve()
        runtime_root = Path(self.runtime_root).expanduser().resolve()
        if campaign_root == runtime_root or campaign_root in runtime_root.parents:
            raise BootstrapError("runtime root may not be inside the campaign checkout")
        if runtime_root in campaign_root.parents:
            raise BootstrapError("campaign checkout may not contain the runtime root")
        git_url = _required_text(self.git_url, "SHINOBI_GIT_URL")
        if len(git_url) > 2048:
            raise BootstrapError("SHINOBI_GIT_URL is too long")
        parsed_url = urlsplit(git_url)
        if parsed_url.scheme in ("http", "https") and (
            parsed_url.username is not None or parsed_url.password is not None
        ):
            raise BootstrapError(
                "SHINOBI_GIT_URL may not embed credentials; use SHINOBI_GIT_TOKEN"
            )
        if self.git_token is not None:
            token = _required_text(self.git_token, "SHINOBI_GIT_TOKEN")
            if len(token) > 4096:
                raise BootstrapError("SHINOBI_GIT_TOKEN is too long")
            if not git_url.startswith("https://"):
                raise BootstrapError("token authentication requires an HTTPS Git URL")
        _safe_ref(self.remote, "SHINOBI_GIT_REMOTE")
        _safe_ref(self.branch, "SHINOBI_GIT_BRANCH")
        _required_text(self.git_binary, "git binary")
        object.__setattr__(self, "campaign_root", campaign_root)
        object.__setattr__(self, "runtime_root", runtime_root)

    @classmethod
    def from_env(cls) -> "CheckoutSettings":
        return cls(
            campaign_root=Path(
                _required_text(
                    os.environ.get("SHINOBI_CAMPAIGN_ROOT"),
                    "SHINOBI_CAMPAIGN_ROOT",
                )
            ),
            runtime_root=Path(
                _required_text(
                    os.environ.get("SHINOBI_RUNTIME_ROOT"),
                    "SHINOBI_RUNTIME_ROOT",
                )
            ),
            git_url=_required_text(
                os.environ.get("SHINOBI_GIT_URL"),
                "SHINOBI_GIT_URL",
            ),
            remote=os.environ.get("SHINOBI_GIT_REMOTE", "origin"),
            branch=os.environ.get("SHINOBI_GIT_BRANCH", "main"),
            git_token=os.environ.get("SHINOBI_GIT_TOKEN"),
            git_binary=os.environ.get("SHINOBI_GIT_BINARY", "git"),
        )


def _askpass_environment(settings: CheckoutSettings) -> Mapping[str, str]:
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    if settings.git_token is None:
        return environment

    settings.runtime_root.mkdir(parents=True, exist_ok=True)
    wrapper = settings.runtime_root / "git-askpass"
    wrapper.write_text(
        "#!/bin/sh\nexec \"%s\" -m shinobi_runtime.git_askpass \"$@\"\n"
        % sys.executable.replace('"', '\\"'),
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    environment["GIT_ASKPASS"] = str(wrapper)
    environment["GIT_ASKPASS_REQUIRE"] = "force"
    environment["SHINOBI_GIT_TOKEN"] = settings.git_token
    return environment


def _run(
    settings: CheckoutSettings,
    arguments: Sequence[str],
    *,
    cwd: Optional[Path] = None,
) -> str:
    completed = subprocess.run(
        [settings.git_binary] + list(arguments),
        cwd=None if cwd is None else str(cwd),
        env=_askpass_environment(settings),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode:
        # Git output may echo a credential-bearing URL from a user's local
        # configuration. Do not include stdout/stderr in the exception.
        raise BootstrapError(
            "Git bootstrap operation failed with exit code %d" % completed.returncode
        )
    return completed.stdout.strip()


def _checkout_status(settings: CheckoutSettings) -> str:
    return _run(
        settings,
        ("status", "--porcelain=v1", "--untracked-files=all"),
        cwd=settings.campaign_root,
    )


def _assert_clean(settings: CheckoutSettings) -> None:
    status = _checkout_status(settings)
    if status:
        raise BootstrapError("persistent campaign checkout is dirty")


def _is_ancestor(settings: CheckoutSettings, older: str, newer: str) -> bool:
    completed = subprocess.run(
        [settings.git_binary, "merge-base", "--is-ancestor", older, newer],
        cwd=str(settings.campaign_root),
        env=_askpass_environment(settings),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode not in (0, 1):
        raise BootstrapError("Git ancestry check failed")
    return completed.returncode == 0


def _commit_text(
    settings: CheckoutSettings,
    commit: str,
    path: str,
) -> Optional[str]:
    """Return UTF-8 text for one committed path, or None when it is absent/invalid."""

    completed = subprocess.run(
        [settings.git_binary, "show", f"{commit}:{path}"],
        cwd=str(settings.campaign_root),
        env=_askpass_environment(settings),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode:
        return None
    try:
        return completed.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None


def _changed_campaign_authority_paths(
    settings: CheckoutSettings,
    local_head: str,
    remote_head: str,
) -> Tuple[str, ...]:
    completed = subprocess.run(
        [
            settings.git_binary,
            "diff",
            "--name-only",
            "-z",
            local_head,
            remote_head,
            "--",
            *_CAMPAIGN_AUTHORITY_PATHS,
        ],
        cwd=str(settings.campaign_root),
        env=_askpass_environment(settings),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode:
        raise BootstrapError("Git campaign-authority path comparison failed")
    try:
        decoded = completed.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise BootstrapError("campaign-authority path is not valid UTF-8") from exc
    return tuple(path for path in decoded.split("\x00") if path)


def _json_values_equal(left: object, right: object) -> bool:
    """Compare parsed JSON without making integer/float coercion equivalent."""

    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        if left.keys() != right.keys():
            return False
        return all(_json_values_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_values_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def _semantic_campaign_authority_matches(
    settings: CheckoutSettings,
    local_head: str,
    remote_head: str,
) -> bool:
    """Allow formatting-only state JSON changes during deliberate history replacement."""

    changed_paths = _changed_campaign_authority_paths(
        settings,
        local_head,
        remote_head,
    )
    for path in changed_paths:
        # Current mutable campaign truth is JSON under state/. Legacy root data/
        # or schemas/ differences remain fail-closed because they are not safe
        # to reinterpret here.
        if not path.startswith("state/") or not path.endswith(".json"):
            return False
        local_text = _commit_text(settings, local_head, path)
        remote_text = _commit_text(settings, remote_head, path)
        if local_text is None or remote_text is None:
            return False
        try:
            local_value = json.loads(local_text)
            remote_value = json.loads(remote_text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        if not _json_values_equal(local_value, remote_value):
            return False
    return True


def _campaign_identity_revision(
    settings: CheckoutSettings,
    commit: str,
) -> Optional[Tuple[str, int]]:
    raw_meta = _commit_text(settings, commit, "state/meta.json")
    if raw_meta is None:
        return None
    try:
        payload = json.loads(raw_meta)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    campaign_id = payload.get("campaign_id")
    revision = payload.get("revision")
    if (
        not isinstance(campaign_id, str)
        or not campaign_id
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
    ):
        return None
    return campaign_id, revision


def _campaign_authority_matches(
    settings: CheckoutSettings,
    local_head: str,
    remote_head: str,
) -> bool:
    """Return whether both commits contain equivalent committed campaign truth.

    Exact Git bytes are preferred. On an unrelated-history replacement only,
    state JSON may also compare semantically so harmless formatting or file-mode
    normalization does not strand a valid Railway volume. Any real state value,
    path-set, legacy data/, or legacy schemas/ difference still fails closed.
    """

    completed = subprocess.run(
        [
            settings.git_binary,
            "diff",
            "--quiet",
            "--exit-code",
            local_head,
            remote_head,
            "--",
            *_CAMPAIGN_AUTHORITY_PATHS,
        ],
        cwd=str(settings.campaign_root),
        env=_askpass_environment(settings),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode not in (0, 1):
        raise BootstrapError("Git campaign-authority comparison failed")
    if completed.returncode == 0:
        return True
    return _semantic_campaign_authority_matches(settings, local_head, remote_head)


def _adopt_remote_history(
    settings: CheckoutSettings,
    remote_ref: str,
    message: str,
) -> Path:
    _run(
        settings,
        ("reset", "--hard", remote_ref),
        cwd=settings.campaign_root,
    )
    _assert_clean(settings)
    print(message, file=sys.stderr)
    return settings.campaign_root


def ensure_checkout(settings: CheckoutSettings) -> Path:
    """Create or safely synchronize the volume-backed campaign checkout."""

    settings.runtime_root.mkdir(parents=True, exist_ok=True)
    git_directory = settings.campaign_root / ".git"
    if not git_directory.is_dir():
        if settings.campaign_root.exists() and any(settings.campaign_root.iterdir()):
            raise BootstrapError("campaign root exists but is not an empty Git checkout")
        settings.campaign_root.parent.mkdir(parents=True, exist_ok=True)
        _run(
            settings,
            (
                "clone",
                "--single-branch",
                "--branch",
                settings.branch,
                "--origin",
                settings.remote,
                "--",
                settings.git_url,
                str(settings.campaign_root),
            ),
        )

    configured_url = _run(
        settings,
        ("remote", "get-url", settings.remote),
        cwd=settings.campaign_root,
    )
    if configured_url != settings.git_url:
        raise BootstrapError("configured Git remote URL differs from SHINOBI_GIT_URL")
    _run(
        settings,
        ("fetch", "--no-tags", settings.remote, settings.branch),
        cwd=settings.campaign_root,
    )
    local_head = _run(settings, ("rev-parse", "HEAD"), cwd=settings.campaign_root)
    remote_ref = f"refs/remotes/{settings.remote}/{settings.branch}"
    remote_head = _run(
        settings,
        ("rev-parse", "--verify", remote_ref),
        cwd=settings.campaign_root,
    )
    dirty = bool(_checkout_status(settings))
    if dirty:
        # A process can die after WAL preparation and owner-byte application
        # but before the Git commit. The transaction coordinator has the
        # bounded before-images needed to repair that state, but only after
        # Uvicorn starts constructing the app. Preserve a dirty checkout here
        # only when its committed HEAD still exactly matches the fetched
        # production branch. Startup recovery will either restore the WAL
        # paths and become pristine or fail closed on any unrelated dirt.
        if local_head != remote_head:
            raise BootstrapError(
                "dirty campaign checkout does not match the remote branch"
            )
        return settings.campaign_root
    if local_head == remote_head:
        return settings.campaign_root
    if _is_ancestor(settings, local_head, remote_head):
        _run(
            settings,
            ("merge", "--ff-only", remote_ref),
            cwd=settings.campaign_root,
        )
        _assert_clean(settings)
        return settings.campaign_root
    if _is_ancestor(settings, remote_head, local_head):
        # A crash may leave a verified local transaction commit awaiting remote
        # recovery. Preserve it; TransactionCoordinator.recover owns the push.
        return settings.campaign_root

    # A repository may be intentionally recreated or its branch history may be
    # replaced while the Railway volume still contains the old lineage. A clean
    # checkout can adopt the remote lineage when campaign truth is equivalent.
    # This includes formatting-only JSON normalization, not real value changes.
    if _campaign_authority_matches(settings, local_head, remote_head):
        return _adopt_remote_history(
            settings,
            remote_ref,
            "Shinobi bootstrap: adopted remote Git history after verifying "
            "equivalent campaign authority",
        )

    # A replaced remote can also legitimately contain campaign commits newer
    # than a stale clean Railway checkout. Revision is the campaign's monotonic
    # durable ordering invariant, so a strictly newer remote revision for the
    # same campaign may be adopted. Never use this rule in the opposite
    # direction: local-newer state may represent an unpushed transaction whose
    # WAL/receipt recovery still owns exact durability.
    local_campaign = _campaign_identity_revision(settings, local_head)
    remote_campaign = _campaign_identity_revision(settings, remote_head)
    if local_campaign is not None and remote_campaign is not None:
        local_campaign_id, local_revision = local_campaign
        remote_campaign_id, remote_revision = remote_campaign
        if local_campaign_id != remote_campaign_id:
            raise BootstrapError(
                "local and remote histories refer to different campaign IDs"
            )
        if remote_revision > local_revision:
            return _adopt_remote_history(
                settings,
                remote_ref,
                "Shinobi bootstrap: adopted replaced remote history with newer "
                f"campaign revision {remote_revision} (local {local_revision})",
            )
        if local_revision > remote_revision:
            raise BootstrapError(
                f"local campaign revision {local_revision} is newer than remote "
                f"revision {remote_revision} after repository history replacement"
            )
        raise BootstrapError(
            f"local and remote campaign authority conflict at revision "
            f"{local_revision} after repository history replacement"
        )

    raise BootstrapError(
        "local and remote campaign histories diverged with different campaign authority"
    )


def main() -> int:
    settings = CheckoutSettings.from_env()
    ensure_checkout(settings)
    git_environment = _askpass_environment(settings)
    for name in ("GIT_TERMINAL_PROMPT", "GIT_ASKPASS", "GIT_ASKPASS_REQUIRE"):
        if name in git_environment:
            os.environ[name] = git_environment[name]
    port = os.environ.get("PORT", "8000")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise BootstrapError("PORT must be in 1..65535")
    os.execvp(
        "uvicorn",
        (
            "uvicorn",
            "shinobi_runtime.api.entrypoint:app",
            "--host",
            "0.0.0.0",
            "--port",
            port,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
