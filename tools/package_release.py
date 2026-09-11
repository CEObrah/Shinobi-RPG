#!/usr/bin/env python3
"""Build and self-verify the exact Shinobi RPG repository release ZIP."""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSIENT_DIR_NAMES = frozenset({
    ".git", ".pytest_cache", "__pycache__", "artifacts", ".mypy_cache",
    ".ruff_cache", ".shinobi-runtime",
})
TRANSIENT_EXACT_PATHS = frozenset({
    ".release-certification.json", ".release-pytest-shards.json", ".coverage", ".DS_Store",
})
TRANSIENT_SUFFIXES = frozenset({".pyc", ".pyo"})
REQUIRED_PATHS = frozenset({
    "railway.toml",
    "docs/RUNTIME_SERVICE_DEPLOYMENT.md",
    "runtime/contracts/release-campaign-state.json",
    "runtime/shinobi_runtime/release_baseline.py",
    "runtime/shinobi_runtime/api/mcp.py",
    "state/meta.json",
    "plugins/shinobi-rpg/skill/shinobi-game-master/SKILL.md",
    "tests/playability/test_delivery_chain_contract.py",
    "tests/playability/test_release_packaging_contract.py",
    "tools/mutation_audit.py",
    "tools/package_release.py",
})


def _include(rel: Path) -> bool:
    if any(part in TRANSIENT_DIR_NAMES for part in rel.parts):
        return False
    if rel.suffix in TRANSIENT_SUFFIXES:
        return False
    return rel.as_posix() not in TRANSIENT_EXACT_PATHS


def package_file_map(root: Path = ROOT) -> dict[str, str]:
    """Return exact packaged paths mapped to content SHA-256."""
    root = root.resolve()
    result: dict[str, str] = {}
    for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix()):
        if path.is_symlink():
            raise RuntimeError(f"release package refuses symlink: {path.relative_to(root)}")
        rel = path.relative_to(root)
        if not _include(rel):
            continue
        result[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    missing = sorted(REQUIRED_PATHS - set(result))
    if missing:
        raise RuntimeError("release package missing required path(s): " + ", ".join(missing))
    return result


def _zip_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_release_zip(output: Path, root: Path = ROOT) -> tuple[dict[str, str], str]:
    """Create a deterministic ZIP from the exact maintained repository tree."""
    root = root.resolve()
    output = output.expanduser().resolve()
    if output.is_relative_to(root):
        raise RuntimeError("release ZIP output must live outside the repository root")
    output.parent.mkdir(parents=True, exist_ok=True)
    expected = package_file_map(root)
    tmp = output.with_name(output.name + ".tmp")
    tmp.unlink(missing_ok=True)
    prefix = root.name
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for rel in sorted(expected):
                path = root / rel
                info = zipfile.ZipInfo(f"{prefix}/{rel}", date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                mode = 0o755 if os.access(path, os.X_OK) else 0o644
                info.external_attr = mode << 16
                archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        tmp.replace(output)
    finally:
        tmp.unlink(missing_ok=True)
    return expected, _zip_sha256(output)


def _extracted_file_map(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix()
        result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def verify_release_zip(output: Path, expected: dict[str, str], *, smoke: bool = True) -> None:
    """Reopen, extract, byte-compare, and smoke-check the built artifact."""
    output = output.expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="shinobi-release-package-") as tmpdir:
        tmp = Path(tmpdir)
        with zipfile.ZipFile(output, "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise RuntimeError("release ZIP contains duplicate entries")
            prefix = ROOT.name + "/"
            if any(not name.startswith(prefix) or name.endswith("/") for name in names):
                raise RuntimeError("release ZIP has unexpected entry layout")
            archive.extractall(tmp)
        extracted = tmp / ROOT.name
        actual = _extracted_file_map(extracted)
        if actual != expected:
            missing = sorted(set(expected) - set(actual))[:8]
            extra = sorted(set(actual) - set(expected))[:8]
            changed = sorted(k for k in set(expected) & set(actual) if expected[k] != actual[k])[:8]
            raise RuntimeError(f"release ZIP byte mismatch missing={missing} extra={extra} changed={changed}")
        if smoke:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(extracted / "runtime")
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
            for command in (
                [sys.executable, "tools/quick_check.py"],
                [sys.executable, "tools/verify_release_state.py"],
                [sys.executable, "tools/sync_gm_skill_contract.py", "--check"],
            ):
                completed = subprocess.run(command, cwd=extracted, env=env, check=False)
                if completed.returncode:
                    raise RuntimeError("packaged smoke failed: " + " ".join(command))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--no-smoke", action="store_true")
    args = parser.parse_args()
    expected, digest = build_release_zip(args.output)
    verify_release_zip(args.output, expected, smoke=not args.no_smoke)
    print(f"RELEASE PACKAGE OK: files={len(expected)} sha256={digest} path={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
