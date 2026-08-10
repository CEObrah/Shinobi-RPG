#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_PATHS = {
    "state/meta.json",
    "state/scene.json",
    "state/time/causal-scheduler.json",
}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def load_at(ref: str, path: str) -> dict:
    if ref == "__archive__":
        return json.loads((ROOT / path).read_text(encoding="utf-8"))
    raw = git("show", f"{ref}:{path}")
    return json.loads(raw)


def fail(message: str) -> None:
    print("TRANSACTION INTEGRITY FAILED")
    print("-", message)
    sys.exit(1)


def validate_snapshot(ref: str) -> tuple[dict, dict, dict]:
    """Validate current clock authorities without inventing unavailable history."""
    try:
        meta = load_at(ref, "state/meta.json")
        scene = load_at(ref, "state/scene.json")
        scheduler = load_at(ref, "state/time/causal-scheduler.json")
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError) as exc:
        fail(f"{ref} has no readable committed campaign core: {exc}")

    revision = meta.get("revision")
    world_time = meta.get("time")
    if meta.get("schema") != "meta":
        fail(f"{ref} state/meta.json has unexpected schema {meta.get('schema')!r}")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        fail(f"{ref} has invalid campaign revision {revision!r}")
    if not isinstance(world_time, str) or not world_time:
        fail(f"{ref} has invalid campaign time {world_time!r}")
    if scene.get("world_time") != world_time:
        fail(f"{ref} scene world_time {scene.get('world_time')!r} != meta time {world_time!r}")
    if scheduler.get("schema") != "causal-scheduler-registry":
        fail(f"{ref} scheduler has unexpected schema {scheduler.get('schema')!r}")
    if scheduler.get("world_time") != world_time:
        fail(f"{ref} scheduler world_time {scheduler.get('world_time')!r} != meta time {world_time!r}")
    if scheduler.get("authority") is not True:
        fail(f"{ref} scheduler is not authoritative")
    if (ROOT / "state/time/frontier.json").exists() and ref == "__archive__":
        fail("retired state/time/frontier.json must not coexist with causal scheduler authority")
    if (ROOT / "state/runtime.json").exists() and ref == "__archive__":
        fail("retired state/runtime.json must not coexist with causal scheduler authority")
    return meta, scene, scheduler


if not (ROOT / ".git").exists():
    current, scene, scheduler = validate_snapshot("__archive__")
    missing = sorted(path for path in CORE_PATHS if not (ROOT / path).exists())
    if missing:
        fail("archive baseline is missing campaign core owners: " + ", ".join(missing))
    print("TRANSACTION INTEGRITY OK (archive snapshot mode; Git history intentionally unavailable)")
    print(f"revision={current.get('revision')} time={current.get('time')}")
    print(f"scheduler_hosts={len(scheduler.get('hosts', {}))} queued_events={len(scheduler.get('events', []))}")
    print("validated_core=" + ",".join(sorted(CORE_PATHS)))
    sys.exit(0)

try:
    head_line = git("rev-list", "--parents", "-n", "1", "HEAD").split()
except subprocess.CalledProcessError as exc:
    fail(f"cannot resolve HEAD: {exc}")
if not head_line:
    fail("cannot resolve HEAD")

head = head_line[0]
current, scene, scheduler = validate_snapshot(head)

if len(head_line) == 1:
    tracked = set(git("ls-tree", "-r", "--name-only", head).splitlines())
    missing = sorted(CORE_PATHS - tracked)
    if missing:
        fail("genesis baseline is missing committed core owners: " + ", ".join(missing))
    dirty_state = subprocess.run(
        ["git", "status", "--porcelain", "--", "state"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if dirty_state:
        fail("uncommitted campaign-state changes cannot be transaction-validated against a root baseline")
    print("TRANSACTION INTEGRITY OK (validated genesis/import baseline; prior revisions unverifiable)")
    print(f"revision={current.get('revision')} time={current.get('time')}")
    print("validated_core=" + ",".join(sorted(CORE_PATHS)))
    sys.exit(0)

parent_count = len(head_line) - 1
parent = head_line[1]
previous, previous_scene, previous_scheduler = validate_snapshot(parent)

changed = set(git("diff", "--name-only", parent, "HEAD").splitlines())
meta_changed = "state/meta.json" in changed
revision_changed = current.get("revision") != previous.get("revision")
time_changed = current.get("time") != previous.get("time")

if not meta_changed:
    print("TRANSACTION INTEGRITY OK (maintenance/non-gameplay commit)")
    print(f"revision={current.get('revision')} time={current.get('time')}")
    print(f"examined_changed_paths={len(changed)} parents={parent_count}")
    sys.exit(0)

if not revision_changed and not time_changed:
    print("TRANSACTION INTEGRITY OK (meta maintenance only)")
    sys.exit(0)

if parent_count != 1:
    fail(f"gameplay transaction cannot be a merge commit: parents={parent_count}")

prev_rev = previous.get("revision")
cur_rev = current.get("revision")
if not isinstance(prev_rev, int) or not isinstance(cur_rev, int) or cur_rev != prev_rev + 1:
    fail(f"gameplay revision must advance exactly once in one commit: {prev_rev!r} -> {cur_rev!r}")

required = {"state/meta.json", "state/scene.json"}
if time_changed:
    required.add("state/time/causal-scheduler.json")
missing = sorted(required - changed)
if missing:
    fail("gameplay transaction split across commits; missing core owners: " + ", ".join(missing))

print("TRANSACTION INTEGRITY OK")
print(f"revision={prev_rev}->{cur_rev} time={previous.get('time')}->{current.get('time')}")
print("changed_core=" + ",".join(sorted(required & changed)))
