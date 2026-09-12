#!/usr/bin/env python3
"""Run maintained pytest files in isolated, bounded subprocesses.

A monolithic pytest process can finish assertions and still stall during plugin
or interpreter teardown. Each test file therefore gets its own process group,
hard timeout, and file-backed log so leaked descendants cannot keep a capture
pipe open and hide the actual verdict.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]


CERTIFIED_ROOTS = (
    "runtime",
    "game",
    "state",
    "tests/current",
    "tests/playability",
    "tools",
    "plugins/shinobi-rpg/skill/shinobi-game-master",
)
CERTIFIED_TOP_LEVEL_FILES = (
    "pyproject.toml",
    "requirements.txt",
    "railway.toml",
    "docs/RUNTIME_SERVICE_DEPLOYMENT.md",
)


def _certification_fingerprint(root: Path | None = None) -> str:
    """Bind resumable shard evidence to the exact maintained release tree."""
    root = ROOT if root is None else root
    digest = hashlib.sha256()
    files: list[Path] = []
    for rel in CERTIFIED_ROOTS:
        base = root / rel
        if base.exists():
            files.extend(path for path in base.rglob("*") if path.is_file())
    for name in CERTIFIED_TOP_LEVEL_FILES:
        path = root / name
        if path.is_file():
            files.append(path)
    for path in sorted(set(files), key=lambda item: item.relative_to(root).as_posix()):
        rel = path.relative_to(root)
        if any(part in {".git", ".pytest_cache", "__pycache__", "artifacts"} for part in rel.parts):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        digest.update(rel.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _load_checkpoint(path: Path, fingerprint: str, files: list[str]) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if data.get("source_fingerprint") != fingerprint or data.get("files") != files:
        return set()
    passed = data.get("passed_files")
    return {str(item) for item in passed} if isinstance(passed, list) else set()


def _save_checkpoint(path: Path, fingerprint: str, files: list[str], passed: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "source_fingerprint": fingerprint,
                "files": files,
                "passed_files": sorted(passed),
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _default_files() -> list[str]:
    paths = [
        *(ROOT / "tests/current").glob("test_*.py"),
        *(ROOT / "tests/playability").glob("test_*.py"),
    ]
    return [str(path.relative_to(ROOT)) for path in sorted(paths)]


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _test_selectors(rel: str) -> list[str]:
    """Return stable top-level pytest selectors without importing the module."""
    path = ROOT / rel
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    selectors: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            selectors.append(f"{rel}::{node.name}")
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test_"):
                    selectors.append(f"{rel}::{node.name}::{child.name}")
    return selectors


def _run_pytest(args: list[str], *, env: dict[str, str], timeout_seconds: float) -> tuple[int | None, bool, str, float]:
    t0 = time.monotonic()
    with tempfile.NamedTemporaryFile(mode="w+b", delete=True) as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        timed_out = False
        try:
            proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc)
        log.flush()
        log.seek(0)
        output = log.read().decode("utf-8", errors="replace")
    return proc.returncode, timed_out, output, time.monotonic() - t0


def _timeout_split_pass(rel: str, *, env: dict[str, str], timeout_seconds: float, chunk_size: int = 16) -> tuple[bool, str]:
    """Retry a timed-out file as bounded test-node groups.

    This distinguishes a slow file/module teardown from a genuinely hanging test.
    A node that still exceeds the same timeout remains a real timeout.
    """
    selectors = _test_selectors(rel)
    if len(selectors) <= 1:
        return False, "no splittable test nodes"
    notes: list[str] = []
    for start in range(0, len(selectors), max(1, chunk_size)):
        group = selectors[start : start + max(1, chunk_size)]
        rc, timed_out, output, elapsed = _run_pytest(group, env=env, timeout_seconds=timeout_seconds)
        label = f"nodes {start + 1}-{start + len(group)}/{len(selectors)}"
        if timed_out:
            return False, f"{label} timed out after {timeout_seconds:g}s\n{output[-4000:]}"
        if rc != 0:
            return False, f"{label} failed ({elapsed:.2f}s)\n{output[-8000:]}"
        notes.append(f"{label} {elapsed:.2f}s")
    return True, "; ".join(notes)


def run(
    files: Iterable[str],
    *,
    timeout_seconds: float = 60.0,
    quiet_passes: bool = False,
    checkpoint_path: Path | None = None,
) -> int:
    file_list = [str(raw) for raw in files]
    fingerprint = _certification_fingerprint() if checkpoint_path is not None else ""
    checkpoint_passed = (
        _load_checkpoint(checkpoint_path, fingerprint, file_list)
        if checkpoint_path is not None
        else set()
    )
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    runtime = str(ROOT / "runtime")
    env["PYTHONPATH"] = runtime + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    failures: list[tuple[str, str]] = []
    timeouts: list[str] = []
    passed = 0
    split_passed = 0
    started = time.monotonic()

    for rel in file_list:
        if rel in checkpoint_passed:
            passed += 1
            if not quiet_passes:
                print(f"SHARD RESUME PASS {rel}", flush=True)
            continue
        path = ROOT / rel
        if not path.is_file():
            failures.append((rel, "test file not found"))
            continue
        selectors = _test_selectors(rel)
        # Very large historical audit modules are cheaper and more reliable when
        # split before execution. This avoids deliberately burning a full file
        # timeout merely to discover that module/interpreter teardown is slow.
        if len(selectors) > 40:
            ok, detail = _timeout_split_pass(rel, env=env, timeout_seconds=timeout_seconds)
            if ok:
                passed += 1
                split_passed += 1
                checkpoint_passed.add(rel)
                if checkpoint_path is not None:
                    _save_checkpoint(checkpoint_path, fingerprint, file_list, checkpoint_passed)
                if not quiet_passes:
                    print(f"SHARD PRE-SPLIT PASS {rel} ({detail})", flush=True)
                continue
            if "timed out" in detail:
                timeouts.append(rel)
                print(f"SHARD TIMEOUT {rel}", flush=True)
            else:
                failures.append((rel, detail))
                print(f"SHARD FAIL {rel}", flush=True)
            print(detail[-8000:], flush=True)
            continue

        rc, timed_out, output, elapsed = _run_pytest([rel], env=env, timeout_seconds=timeout_seconds)
        if timed_out:
            ok, detail = _timeout_split_pass(rel, env=env, timeout_seconds=timeout_seconds)
            if ok:
                passed += 1
                split_passed += 1
                checkpoint_passed.add(rel)
                if checkpoint_path is not None:
                    _save_checkpoint(checkpoint_path, fingerprint, file_list, checkpoint_passed)
                if not quiet_passes:
                    print(f"SHARD SPLIT-PASS {rel} ({detail})", flush=True)
                continue
            timeouts.append(rel)
            print(f"SHARD TIMEOUT {rel} after {timeout_seconds:g}s", flush=True)
            if detail.strip():
                print(detail[-8000:], flush=True)
            elif output.strip():
                print(output[-4000:], flush=True)
            continue
        if rc != 0:
            failures.append((rel, output[-8000:]))
            print(f"SHARD FAIL {rel} ({elapsed:.2f}s)", flush=True)
            print(output[-8000:], flush=True)
        else:
            passed += 1
            checkpoint_passed.add(rel)
            if checkpoint_path is not None:
                _save_checkpoint(checkpoint_path, fingerprint, file_list, checkpoint_passed)
            if not quiet_passes:
                summary = next((line for line in reversed(output.splitlines()) if " passed" in line or " skipped" in line), "passed")
                print(f"SHARD PASS {rel} ({elapsed:.2f}s) {summary}", flush=True)

    total = passed + len(failures) + len(timeouts)
    print(
        f"PYTEST SHARDS: files={total} passed={passed} split_passed={split_passed} failed={len(failures)} "
        f"timed_out={len(timeouts)} elapsed={time.monotonic()-started:.2f}s",
        flush=True,
    )
    if failures or timeouts:
        if failures:
            print("FAILED FILES: " + " ".join(name for name, _ in failures), flush=True)
        if timeouts:
            print("TIMED OUT FILES: " + " ".join(timeouts), flush=True)
        return 1
    if checkpoint_path is not None:
        checkpoint_path.unlink(missing_ok=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--quiet-passes", action="store_true")
    parser.add_argument("--checkpoint", type=Path)
    args = parser.parse_args()
    return run(
        args.files or _default_files(),
        timeout_seconds=max(1.0, args.timeout),
        quiet_passes=args.quiet_passes,
        checkpoint_path=args.checkpoint,
    )


if __name__ == "__main__":
    raise SystemExit(main())
