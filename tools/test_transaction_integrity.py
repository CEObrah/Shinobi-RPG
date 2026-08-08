#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def load_at(ref: str, path: str) -> dict:
    raw = git("show", f"{ref}:{path}")
    return json.loads(raw)


def fail(message: str) -> None:
    print("TRANSACTION INTEGRITY FAILED")
    print("-", message)
    sys.exit(1)


try:
    parent = git("rev-parse", "HEAD^")
except subprocess.CalledProcessError:
    print("TRANSACTION INTEGRITY OK (no parent commit available)")
    sys.exit(0)

current = json.loads((ROOT / "state/meta.json").read_text(encoding="utf-8"))
try:
    previous = load_at(parent, "state/meta.json")
except (subprocess.CalledProcessError, json.JSONDecodeError):
    print("TRANSACTION INTEGRITY OK (parent has no readable campaign meta)")
    sys.exit(0)

changed = set(git("diff", "--name-only", parent, "HEAD").splitlines())
meta_changed = "state/meta.json" in changed
revision_changed = current.get("revision") != previous.get("revision")
time_changed = current.get("time") != previous.get("time")

if not meta_changed:
    if revision_changed or time_changed:
        fail("campaign meta differs from parent without state/meta.json appearing in the commit diff")
    print("TRANSACTION INTEGRITY OK (maintenance/non-gameplay commit)")
    sys.exit(0)

if not revision_changed and not time_changed:
    print("TRANSACTION INTEGRITY OK (meta maintenance only)")
    sys.exit(0)

prev_rev = previous.get("revision")
cur_rev = current.get("revision")
if not isinstance(prev_rev, int) or not isinstance(cur_rev, int) or cur_rev != prev_rev + 1:
    fail(f"gameplay revision must advance exactly once in one commit: {prev_rev!r} -> {cur_rev!r}")

required = {"state/meta.json", "state/scene.json", "state/time/frontier.json"}
missing = sorted(required - changed)
if missing:
    fail("gameplay transaction split across commits; missing core owners: " + ", ".join(missing))

if time_changed and "state/runtime.json" not in changed:
    fail("world time changed without state/runtime.json in the same commit")

scene = json.loads((ROOT / "state/scene.json").read_text(encoding="utf-8"))
frontier = json.loads((ROOT / "state/time/frontier.json").read_text(encoding="utf-8"))
if scene.get("world_time") != current.get("time"):
    fail(f"scene world_time {scene.get('world_time')!r} != meta time {current.get('time')!r}")
if frontier.get("world_time") != current.get("time"):
    fail(f"frontier world_time {frontier.get('world_time')!r} != meta time {current.get('time')!r}")

if time_changed:
    runtime = json.loads((ROOT / "state/runtime.json").read_text(encoding="utf-8"))
    if runtime.get("last_settled_at") != current.get("time"):
        fail(
            f"runtime last_settled_at {runtime.get('last_settled_at')!r} "
            f"!= meta time {current.get('time')!r}"
        )

print("TRANSACTION INTEGRITY OK")
print(f"revision={prev_rev}->{cur_rev} time={previous.get('time')}->{current.get('time')}")
print("changed_core=" + ",".join(sorted(required & changed)))
