"""Bounded source-image freshness diagnostics for the production runtime.

Railway builds the executable Python image from one Git commit while the mutable
campaign checkout lives on a persistent volume and may legitimately advance by
state-only gameplay commits.  A healthy production process therefore needs to
prove that its build commit is an ancestor of the live checkout and that every
commit after the build changed only ``state/``.  Any non-state delta means the
running image is older than source/configuration already present in the
campaign checkout and must not be reported healthy.

The check is read-only and uses fixed Git commands against the configured
repository root.  It never accepts repository paths or Git arguments from a
caller.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple

_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_RAILWAY_MARKERS = (
    "RAILWAY_PROJECT_ID",
    "RAILWAY_SERVICE_ID",
    "RAILWAY_ENVIRONMENT_ID",
    "RAILWAY_DEPLOYMENT_ID",
)


@dataclass(frozen=True)
class DeploymentFreshness:
    status: str
    source_revision: Optional[str]
    checkout_revision: Optional[str]
    non_state_paths: Tuple[str, ...]
    production: bool
    reason: str

    @property
    def healthy(self) -> bool:
        if self.status == "fresh":
            return True
        # Local/unit-test processes are allowed to lack Railway build metadata.
        return self.status == "unverified" and not self.production

    def diagnostic(self) -> str:
        return (
            "deployment_source:summary "
            f"status={self.status} production={str(self.production).lower()} "
            f"source_revision={self.source_revision or 'none'} "
            f"checkout_revision={self.checkout_revision or 'none'} "
            f"non_state_delta_count={len(self.non_state_paths)} "
            f"reason={self.reason}"
        )


def _git(
    root: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _head(root: Path) -> Optional[str]:
    completed = _git(root, "rev-parse", "HEAD")
    if completed.returncode:
        return None
    try:
        value = completed.stdout.decode("ascii", errors="strict").strip().lower()
    except UnicodeDecodeError:
        return None
    return value if _SHA.fullmatch(value) else None


def inspect_deployment_freshness(
    repository_root: object,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> DeploymentFreshness:
    """Compare the immutable Railway build commit with the live checkout.

    State-only commits after the build are expected and healthy.  A non-state
    path changed after the build, missing/invalid Railway build identity in a
    Railway process, an unknown build commit, or divergent lineage is not.
    """
    root = Path(repository_root).resolve()
    environment = os.environ if environ is None else environ
    production = any(bool(environment.get(name)) for name in _RAILWAY_MARKERS)
    checkout = _head(root)
    source_raw = environment.get("RAILWAY_GIT_COMMIT_SHA")
    source = source_raw.lower() if isinstance(source_raw, str) else None

    if checkout is None:
        return DeploymentFreshness(
            status="unverified",
            source_revision=source if source and _SHA.fullmatch(source) else None,
            checkout_revision=None,
            non_state_paths=(),
            production=production,
            reason="checkout_head_unavailable",
        )
    if source is None or not _SHA.fullmatch(source):
        return DeploymentFreshness(
            status="unverified",
            source_revision=None,
            checkout_revision=checkout,
            non_state_paths=(),
            production=production,
            reason="build_revision_unavailable",
        )

    known = _git(root, "cat-file", "-e", f"{source}^{{commit}}")
    if known.returncode:
        return DeploymentFreshness(
            status="stale",
            source_revision=source,
            checkout_revision=checkout,
            non_state_paths=(),
            production=production,
            reason="build_revision_not_in_checkout_history",
        )

    ancestor = _git(root, "merge-base", "--is-ancestor", source, checkout)
    if ancestor.returncode != 0:
        return DeploymentFreshness(
            status="stale",
            source_revision=source,
            checkout_revision=checkout,
            non_state_paths=(),
            production=production,
            reason="build_revision_not_checkout_ancestor",
        )

    changed = _git(root, "diff", "--name-only", "-z", source, checkout, "--")
    if changed.returncode:
        return DeploymentFreshness(
            status="unverified",
            source_revision=source,
            checkout_revision=checkout,
            non_state_paths=(),
            production=production,
            reason="checkout_delta_unavailable",
        )
    try:
        paths = tuple(
            sorted(
                path.decode("utf-8", errors="strict")
                for path in changed.stdout.split(b"\x00")
                if path
            )
        )
    except UnicodeDecodeError:
        return DeploymentFreshness(
            status="unverified",
            source_revision=source,
            checkout_revision=checkout,
            non_state_paths=(),
            production=production,
            reason="checkout_delta_invalid_utf8",
        )
    non_state = tuple(path for path in paths if not path.startswith("state/"))
    if non_state:
        return DeploymentFreshness(
            status="stale",
            source_revision=source,
            checkout_revision=checkout,
            non_state_paths=non_state,
            production=production,
            reason="non_state_source_ahead_of_running_image",
        )
    return DeploymentFreshness(
        status="fresh",
        source_revision=source,
        checkout_revision=checkout,
        non_state_paths=(),
        production=production,
        reason="build_covers_all_non_state_checkout_changes",
    )


__all__ = ["DeploymentFreshness", "inspect_deployment_freshness"]
