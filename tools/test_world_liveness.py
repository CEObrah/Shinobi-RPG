#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []


def err(message: str) -> None:
    errors.append(message)


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        err(f"json:{path.relative_to(ROOT)}:{exc}")
        return {}


def parse_time(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"SE-(-?\d+)-(\d+)-(\d+)T(\d+):(\d+):(\d+)", value)
    if not match:
        return None
    year, month, day, hour, minute, second = map(int, match.groups())
    days = year * 372 + (month - 1) * 31 + day - 1
    return ((((days * 24) + hour) * 60 + minute) * 60) + second


meta = read_json(ROOT / "state/meta.json")
scheduler = read_json(ROOT / "state/time/causal-scheduler.json")
world_time = meta.get("time")
world_key = parse_time(world_time)
if world_key is None:
    err(f"invalid_world_time:{world_time}")
if scheduler.get("schema") != "causal-scheduler-registry":
    err("scheduler_schema")
if scheduler.get("authority") is not True:
    err("scheduler_not_authority")
if scheduler.get("world_time") != world_time:
    err(f"scheduler_world_time_drift:{scheduler.get('world_time')}:{world_time}")

hosts = scheduler.get("hosts")
events = scheduler.get("events")
metrics = scheduler.get("metrics")
if not isinstance(hosts, dict) or not hosts:
    err("no_scheduler_hosts_examined")
    hosts = {}
if not isinstance(events, list):
    err("scheduler_events_not_list")
    events = []
if not isinstance(metrics, dict):
    err("scheduler_metrics_not_object")
    metrics = {}

# No legacy polling authority may survive the cutover.
for rel in (
    "state/time/frontier.json",
    "state/time/coverage",
    "state/runtime.json",
    "state/reg/life-course-registry.json",
):
    if (ROOT / rel).exists():
        err(f"legacy_temporal_authority_present:{rel}")

# Explicit events must be bounded, future-facing, and target a known host.
queued_by_host: dict[str, list[dict]] = {host_id: [] for host_id in hosts}
seen_events: set[str] = set()
for event in events:
    if not isinstance(event, dict):
        err(f"bad_scheduler_event:{event!r}")
        continue
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        err("scheduler_event_missing_id")
    elif event_id in seen_events:
        err(f"duplicate_scheduler_event:{event_id}")
    else:
        seen_events.add(event_id)
    target = event.get("target_host")
    if target not in hosts:
        err(f"scheduler_event_unknown_host:{event_id}:{target}")
        continue
    queued_by_host[target].append(event)
    due_key = parse_time(event.get("due_at"))
    if due_key is None:
        err(f"scheduler_event_bad_due:{event_id}:{event.get('due_at')}")
    elif world_key is not None and due_key <= world_key:
        err(f"scheduler_event_overdue:{event_id}:{event.get('due_at')}:{world_time}")

# Host cursors and next_due must exactly agree with the queue.
for host_id, wrapper in hosts.items():
    if not isinstance(wrapper, dict):
        err(f"scheduler_host_not_object:{host_id}")
        continue
    state = wrapper.get("state") or {}
    if state.get("host_id") != host_id:
        err(f"scheduler_host_key_drift:{host_id}:{state.get('host_id')}")
    resolved = parse_time(state.get("resolved_through"))
    safe = parse_time(state.get("safe_through"))
    if resolved is None or safe is None:
        err(f"scheduler_host_bad_cursor:{host_id}")
    else:
        if resolved > safe:
            err(f"scheduler_host_reverse_horizon:{host_id}")
        if world_key is not None and resolved > world_key:
            err(f"scheduler_host_resolved_in_future:{host_id}")
    host_events = queued_by_host.get(host_id, [])
    expected_due = min((event.get("due_at") for event in host_events), default=None)
    if state.get("next_due") != expected_due:
        err(f"scheduler_host_next_due_drift:{host_id}:{state.get('next_due')}:{expected_due}")
    if expected_due is not None:
        due_key = parse_time(expected_due)
        if due_key is not None and safe is not None and safe >= due_key:
            err(f"scheduler_host_safe_crosses_wake:{host_id}:{state.get('safe_through')}:{expected_due}")

# Production locality must never regress to global person/faction discovery.
for key in ("global_person_scans", "named_persons_scanned_per_advance", "global_faction_directory_scans", "faction_directory_scans_per_advance"):
    if key in metrics and metrics.get(key) not in (0, None):
        err(f"scheduler_global_scan_regression:{key}:{metrics.get(key)}")

# Exact character owners must not carry a second general scheduler.
character_paths = [ROOT / "state/player.json"] + sorted((ROOT / "state/char").glob("*.json"))
exact_people = 0
for path in character_paths:
    person = read_json(path)
    if person.get("schema") != "shinobi_character":
        continue
    exact_people += 1
    for forbidden in ("runtime", "schedule_profile", "coverage_ref"):
        if forbidden in person:
            err(f"character_scheduler_mirror:{path.relative_to(ROOT)}:{forbidden}")
if exact_people == 0:
    err("no_exact_people_examined")

# Domain-owned exact deadlines remain real clocks even when the scheduler is cold.
def walk_deadlines(value: object, label: str) -> None:
    if isinstance(value, dict):
        status = str(value.get("status", "")).lower()
        if status in {"active", "active_hidden", "latent_active", "blocked", "deployed", "in_progress", "ongoing", "recovering", "scheduled", "forming", "proposed"}:
            for field in ("return_at", "recovery_due_at", "next_review_at", "deadline_at", "expires_at", "due_at"):
                if field not in value or value.get(field) is None:
                    continue
                due_key = parse_time(value.get(field))
                if due_key is None:
                    err(f"bad_active_deadline:{label}:{field}:{value.get(field)}")
                elif world_key is not None and due_key <= world_key:
                    err(f"overdue_active_deadline:{label}:{field}:{value.get(field)}:{world_time}")
        for key, child in value.items():
            walk_deadlines(child, f"{label}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk_deadlines(child, f"{label}/{index}")

schedule_files = 0
for path in (ROOT / "state").rglob("*.json"):
    if "index" in path.parts:
        continue
    schedule_files += 1
    walk_deadlines(read_json(path), str(path.relative_to(ROOT)))
if schedule_files == 0:
    err("no_state_files_examined")

# Every active faction and canon pressure must have exactly one explicit causal host.
faction_registry = read_json(ROOT / "state/reg/factions.json")
faction_ids = set((faction_registry.get("record_index") or {}).keys())
for faction_id in faction_ids:
    host_id = f"host.faction.{faction_id}"
    if host_id not in hosts:
        err(f"missing_faction_host:{faction_id}")

pressure_registry = read_json(ROOT / "state/canon/pressures.json")
active_pressures = 0
for pressure_id, front in (pressure_registry.get("pressures") or {}).items():
    if front.get("status") not in {"active", "active_hidden", "latent_active"}:
        continue
    active_pressures += 1
    host_id = f"host.canon_pressure.{pressure_id}"
    wrapper = hosts.get(host_id)
    if not isinstance(wrapper, dict):
        err(f"missing_canon_pressure_host:{pressure_id}")
        continue
    state = wrapper.get("state") or {}
    boundary = front.get("next_boundary") or {}
    if boundary.get("host_ref") != host_id:
        err(f"canon_pressure_boundary_host_drift:{pressure_id}:{boundary.get('host_ref')}")
    if boundary.get("settled_through") != state.get("resolved_through"):
        err(f"canon_pressure_cursor_drift:{pressure_id}")
    if boundary.get("due_at") != state.get("next_due"):
        err(f"canon_pressure_due_drift:{pressure_id}")

if errors:
    print(f"WORLD LIVENESS FAIL {len(errors)}")
    for message in errors[:300]:
        print("-", message)
    raise SystemExit(1)

print("WORLD LIVENESS OK")
print(
    f"scheduler_hosts={len(hosts)} queued_events={len(events)} "
    f"exact_people={exact_people} factions={len(faction_ids)} active_pressures={active_pressures} "
    f"state_files_examined={schedule_files}"
)
