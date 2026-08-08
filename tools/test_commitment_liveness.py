#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors = []


def err(message):
    errors.append(message)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        err(f"json:{path.relative_to(ROOT)}:{exc}")
        return {}


def parse_time(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"SE-(-?\d+)-(\d+)-(\d+)T(\d+):(\d+):(\d+)", value)
    if not match:
        return None
    year, month, day, hour, minute, second = map(int, match.groups())
    days = year * 372 + (month - 1) * 31 + (day - 1)
    return (((days * 24) + hour) * 60 + minute) * 60 + second


frontier = read_json(ROOT / "state/time/frontier.json")
world_time = frontier.get("world_time")
world_key = parse_time(world_time)
if world_key is None:
    err(f"bad_world_time:{world_time}")


def process_coverage(process):
    if process.get("coverage_ref"):
        data = read_json(ROOT / process["coverage_ref"])
        return list(data.get("owner_ids", []))
    return list(process.get("coverage", []))


coverage_counts = {}
processes = {}
for process in frontier.get("processes", []):
    process_id = process.get("id")
    if process_id:
        processes[process_id] = process
    for owner_id in process_coverage(process):
        coverage_counts[owner_id] = coverage_counts.get(owner_id, 0) + 1

# Mission/contract/project lifecycle must be machine-readable and temporally live.
registry = read_json(ROOT / "state/reg/missions-contracts-projects.json")
if registry.get("schema") != "shinobi-domain-registry" or registry.get("owner_id") != "missions_contracts_projects":
    err("mission_registry_identity")

closed_statuses = {"completed", "failed", "cancelled", "expired"}
open_statuses = {"scheduled", "active", "blocked", "proposed"}


def check_due(record, kind):
    status = record.get("status")
    record_id = record.get("id")
    due = record.get("next_due_at")
    if status in open_statuses:
        operation_ref = record.get("operation_ref") if kind == "mission" else None
        if due is None and not operation_ref:
            err(f"timeless_open_{kind}:{record_id}:{status}")
        if due is not None:
            due_key = parse_time(due)
            if due_key is None:
                err(f"bad_{kind}_next_due:{record_id}:{due}")
            elif world_key is not None and due_key <= world_key:
                err(f"overdue_{kind}:{record_id}:{due}:{world_time}")
    elif status in closed_statuses:
        if due is not None:
            err(f"closed_{kind}_has_next_due:{record_id}:{due}")
        if not str(record.get("result") or "").strip():
            err(f"closed_{kind}_missing_result:{record_id}")
    else:
        err(f"unknown_{kind}_status:{record_id}:{status}")


for mission in registry.get("active_missions", []):
    if not isinstance(mission, dict):
        err(f"unstructured_mission:{mission!r}")
        continue
    check_due(mission, "mission")
    status = mission.get("status")
    starts = parse_time(mission.get("starts_at"))
    deadline = parse_time(mission.get("deadline_at")) if mission.get("deadline_at") is not None else None
    if starts is None:
        err(f"bad_mission_start:{mission.get('id')}:{mission.get('starts_at')}")
    if status == "scheduled" and starts is not None and world_key is not None and starts <= world_key:
        err(f"scheduled_mission_should_have_woken:{mission.get('id')}:{mission.get('starts_at')}")
    if status in {"active", "blocked"} and deadline is not None and world_key is not None and deadline <= world_key:
        err(f"mission_deadline_crossed:{mission.get('id')}:{mission.get('deadline_at')}")

for contract in registry.get("contracts", []):
    if not isinstance(contract, dict):
        err(f"unstructured_contract:{contract!r}")
        continue
    check_due(contract, "contract")
    expiry = parse_time(contract.get("expires_at")) if contract.get("expires_at") is not None else None
    if contract.get("status") in open_statuses and expiry is not None and world_key is not None and expiry <= world_key:
        err(f"contract_expiry_crossed:{contract.get('id')}:{contract.get('expires_at')}")

for project in registry.get("projects", []):
    if not isinstance(project, dict):
        err(f"unstructured_project:{project!r}")
        continue
    check_due(project, "project")
    status = project.get("status")
    completed_at = project.get("completed_at")
    if status in closed_statuses:
        completed_key = parse_time(completed_at)
        if completed_key is None:
            err(f"closed_project_missing_completion:{project.get('id')}:{completed_at}")
        elif world_key is not None and completed_key > world_key:
            err(f"project_completed_in_future:{project.get('id')}:{completed_at}")
    elif completed_at is not None:
        err(f"open_project_has_completion:{project.get('id')}:{completed_at}")

# Deployment, recovery and other exact domain deadlines must wake even when their
# aggregate owner process is not due. Only open/recovering states are checked.
active_labels = {
    "active", "active_hidden", "latent_active", "blocked", "deployed", "in_progress",
    "ongoing", "recovering", "scheduled", "forming", "proposed"
}
deadline_fields = ("return_at", "recovery_due_at", "next_review_at", "deadline_at", "expires_at")


def walk_deadlines(value, label):
    if isinstance(value, dict):
        status = str(value.get("status", "")).lower()
        if status in active_labels:
            for field in deadline_fields:
                due = value.get(field)
                if due is None:
                    continue
                due_key = parse_time(due)
                if due_key is None:
                    err(f"bad_active_deadline:{label}:{field}:{due}")
                elif world_key is not None and due_key <= world_key:
                    err(f"overdue_active_deadline:{label}:{field}:{due}:{world_time}")
        for key, child in value.items():
            walk_deadlines(child, f"{label}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk_deadlines(child, f"{label}/{index}")


for path in (ROOT / "state").rglob("*.json"):
    if "index" in path.parts or "unit-kernel" in path.parts:
        continue
    walk_deadlines(read_json(path), str(path.relative_to(ROOT)))

# Every live world pressure has exactly one temporal process coverage path. The
# pressure process may cover several pressures together, but a pressure may not be
# uncovered or double-driven.
pressure_registry = read_json(ROOT / "state/world/world-pressures.json")
pressures = (pressure_registry.get("payload") or {}).get("pressures", [])
for pressure in pressures:
    pressure_id = pressure.get("id")
    status = pressure.get("status")
    if status in {"active", "active_hidden", "latent_active"}:
        count = coverage_counts.get(pressure_id, 0)
        if count != 1:
            err(f"world_pressure_coverage:{pressure_id}:{count}")

if errors:
    print(f"COMMITMENT LIVENESS FAIL {len(errors)}")
    for message in errors:
        print(f"- {message}")
    raise SystemExit(1)

print("COMMITMENT LIVENESS OK")
print(f"missions={len(registry.get('active_missions', []))} contracts={len(registry.get('contracts', []))} projects={len(registry.get('projects', []))} pressures={len(pressures)}")
