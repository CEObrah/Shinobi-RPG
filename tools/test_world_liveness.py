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
    # Campaign comparisons need deterministic ordering, not Gregorian leap-year semantics.
    days = year * 372 + (month - 1) * 31 + (day - 1)
    return (((days * 24) + hour) * 60 + minute) * 60 + second


frontier = read_json(ROOT / "state/time/frontier.json")
runtime = read_json(ROOT / "state/runtime.json")
world_time = frontier.get("world_time")
world_key = parse_time(world_time)
if world_key is None:
    err(f"invalid_world_time:{world_time}")

processes = {}
coverage_by_process = {}


def process_coverage(process):
    process_id = process.get("id")
    inline = process.get("coverage")
    coverage_ref = process.get("coverage_ref")
    if inline is not None and coverage_ref is not None:
        err(f"process_has_inline_and_ref_coverage:{process_id}")
    if coverage_ref:
        path = ROOT / coverage_ref
        if not path.exists():
            err(f"missing_process_coverage:{process_id}:{coverage_ref}")
            return []
        data = read_json(path)
        if data.get("schema") != "process-coverage":
            err(f"bad_process_coverage_schema:{process_id}:{coverage_ref}")
        if data.get("process_id") != process_id:
            err(f"process_coverage_id_mismatch:{process_id}:{coverage_ref}:{data.get('process_id')}")
        owners = data.get("owner_ids", [])
    else:
        owners = inline or []
    if not isinstance(owners, list) or any(not isinstance(owner, str) for owner in owners):
        err(f"invalid_process_coverage_list:{process_id}")
        return []
    if len(owners) != len(set(owners)):
        err(f"duplicate_process_coverage_owner:{process_id}")
    return owners


for process in frontier.get("processes", []):
    process_id = process.get("id")
    if not process_id:
        err("process_missing_id")
        continue
    if process_id in processes:
        err(f"duplicate_process_id:{process_id}")
    processes[process_id] = process
    coverage_by_process[process_id] = process_coverage(process)

# Global runtime settlement is valid only when every continuous process has a
# matching semantic receipt at the same exact frontier.
if runtime.get("schema") != "shinobi-world-runtime":
    err("world_runtime_schema")
if runtime.get("last_settled_at") != world_time:
    err(f"world_runtime_frontier_drift:{runtime.get('last_settled_at')}:{world_time}")
receipts = runtime.get("completed_reviews")
if not isinstance(receipts, dict):
    err("completed_reviews_not_object")
    receipts = {}

valid_outcomes = {"no_op", "deferred", "blocked", "failed", "succeeded"}
for process_id, process in processes.items():
    if process.get("status") != "active":
        continue
    recurrence = process.get("recurrence") or {}
    if recurrence.get("accrual_mode") != "continuous":
        continue
    if process.get("settled_through") != world_time:
        err(f"continuous_process_not_closed:{process_id}:{process.get('settled_through')}:{world_time}")
    coverage = coverage_by_process.get(process_id, [])
    if not coverage:
        err(f"continuous_process_empty_coverage:{process_id}")
    receipt = receipts.get(process_id)
    if not isinstance(receipt, dict):
        err(f"continuous_process_missing_receipt:{process_id}")
        continue
    if receipt.get("process_id") != process_id:
        err(f"receipt_process_id_mismatch:{process_id}:{receipt.get('process_id')}")
    if receipt.get("reviewed_through") != process.get("settled_through"):
        err(f"receipt_frontier_mismatch:{process_id}:{receipt.get('reviewed_through')}:{process.get('settled_through')}")
    reviewed_from = parse_time(receipt.get("reviewed_from")) if receipt.get("reviewed_from") is not None else None
    reviewed_through = parse_time(receipt.get("reviewed_through"))
    if reviewed_through is None:
        err(f"receipt_bad_reviewed_through:{process_id}")
    if reviewed_from is not None and reviewed_through is not None and reviewed_from > reviewed_through:
        err(f"receipt_reverse_interval:{process_id}")
    if receipt.get("outcome") not in valid_outcomes:
        err(f"receipt_bad_outcome:{process_id}:{receipt.get('outcome')}")
    if receipt.get("outcome") == "no_op" and receipt.get("material_change") is not False:
        err(f"receipt_noop_material_change:{process_id}")
    expected_ref = process.get("coverage_ref")
    if receipt.get("coverage_ref") != expected_ref:
        err(f"receipt_coverage_ref_drift:{process_id}:{receipt.get('coverage_ref')}:{expected_ref}")
    if receipt.get("coverage_count") != len(coverage):
        err(f"receipt_coverage_count_drift:{process_id}:{receipt.get('coverage_count')}:{len(coverage)}")
    if not isinstance(receipt.get("result"), str) or not receipt.get("result").strip():
        err(f"receipt_missing_result:{process_id}")

for receipt_id, receipt in receipts.items():
    if receipt_id not in processes:
        err(f"receipt_for_missing_process:{receipt_id}")
    elif isinstance(receipt, dict) and receipt.get("process_id") != receipt_id:
        err(f"receipt_key_id_drift:{receipt_id}:{receipt.get('process_id')}")

# Every saved owner-local schedule is a real clock. A slow aggregate process may
# not hide an overdue exact owner. This scan deliberately discovers future owner
# types instead of requiring another manually maintained list.
def walk_schedules(value, path_label):
    if isinstance(value, dict):
        if isinstance(value.get("last_settled_at"), str) and isinstance(value.get("next_due_at"), str):
            owner = value.get("owner_id") or path_label
            last_key = parse_time(value.get("last_settled_at"))
            due_key = parse_time(value.get("next_due_at"))
            if last_key is None:
                err(f"bad_owner_local_last_settled:{owner}:{value.get('last_settled_at')}")
            if due_key is None:
                err(f"bad_owner_local_next_due:{owner}:{value.get('next_due_at')}")
            if world_key is not None and last_key is not None and last_key > world_key:
                err(f"owner_local_cursor_in_future:{owner}:{value.get('last_settled_at')}")
            if world_key is not None and due_key is not None and due_key <= world_key:
                err(f"overdue_owner_local_deadline:{owner}:{value.get('next_due_at')}:{world_time}")
            if last_key is not None and due_key is not None and due_key <= last_key:
                err(f"owner_local_deadline_not_forward:{owner}")
        for key, child in value.items():
            walk_schedules(child, f"{path_label}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk_schedules(child, f"{path_label}/{index}")


for path in (ROOT / "state").rglob("*.json"):
    # Derived indexes/caches are not liveness authority.
    if "index" in path.parts or "unit-kernel" in path.parts:
        continue
    walk_schedules(read_json(path), str(path.relative_to(ROOT)))

# Exact NPCs that carry a schedule profile and a runtime cursor may not maintain
# two disagreeing general-autonomy clocks inside the same owner.
character_paths = [ROOT / "state/player.json"] + sorted((ROOT / "state/char").glob("*.json"))
for path in character_paths:
    character = read_json(path)
    if character.get("schema") != "shinobi_character":
        continue
    owner_id = character.get("owner_id")
    schedule = character.get("schedule_profile")
    if not isinstance(schedule, dict):
        continue
    if schedule.get("owner_id") != owner_id:
        err(f"character_schedule_owner_drift:{path.relative_to(ROOT)}:{schedule.get('owner_id')}:{owner_id}")
    runtime_cursor = (character.get("runtime") or {}).get("last_settled_at")
    schedule_cursor = schedule.get("last_settled_at")
    if runtime_cursor is not None and schedule_cursor is not None and runtime_cursor != schedule_cursor:
        err(f"character_autonomy_cursor_drift:{owner_id}:{runtime_cursor}:{schedule_cursor}")

# Active faction plans are autonomous commitments. Their own review cursor drives
# wake-up and cannot be replaced by a monthly process timestamp. Seven days is the
# maximum fallback gap for an active plan; causal information can wake it sooner.
active_faction_max_gap_seconds = 7 * 24 * 60 * 60
for path in sorted((ROOT / "state/reg/factions").glob("*.json")):
    data = read_json(path)
    if data.get("schema") != "faction-owner":
        continue
    faction = data.get("faction") or {}
    faction_id = faction.get("id")
    process_id = faction.get("development_process_id")
    if process_id not in processes:
        err(f"faction_missing_process:{faction_id}:{process_id}")
    elif faction_id not in coverage_by_process.get(process_id, []):
        err(f"faction_not_in_process_coverage:{faction_id}:{process_id}")
    plan = faction.get("plan_state") or {}
    last_review = parse_time(plan.get("last_review_at"))
    if last_review is None:
        err(f"faction_missing_review_cursor:{faction_id}")
        continue
    if world_key is not None and last_review > world_key:
        err(f"faction_review_in_future:{faction_id}:{plan.get('last_review_at')}")
    active = faction.get("status") == "active" and plan.get("status") == "active"
    if active and world_key is not None and world_key - last_review > active_faction_max_gap_seconds:
        err(f"active_faction_review_stale:{faction_id}:{plan.get('last_review_at')}:{world_time}")
    if active and not str(faction.get("current_plan") or "").strip():
        err(f"active_faction_missing_current_plan:{faction_id}")
    if active and not str(plan.get("wake_policy") or "").strip():
        err(f"active_faction_missing_wake_policy:{faction_id}")

# The world-structure process must cover every authoritative top-level world
# registry except world pressures, whose individual pressure owners have their own
# dedicated clocks, plus every force/institution stock owner in state/stock.
world_structures = processes.get("process_world_structures_monthly")
if not world_structures:
    err("missing_world_structures_process")
else:
    if not world_structures.get("coverage_ref"):
        err("world_structures_requires_coverage_ref")
    actual = set(coverage_by_process.get("process_world_structures_monthly", []))
    expected = set()
    for path in sorted((ROOT / "state/world").glob("*.json")):
        data = read_json(path)
        if data.get("schema") == "shinobi-world-registry" and path.name != "world-pressures.json":
            owner_id = data.get("owner_id")
            if owner_id:
                expected.add(owner_id)
    stock_dir = ROOT / "state/stock"
    if stock_dir.exists():
        for path in sorted(stock_dir.glob("*.json")):
            data = read_json(path)
            owner_id = data.get("owner_id") or data.get("id")
            if owner_id:
                expected.add(owner_id)
    missing = sorted(expected - actual)
    if missing:
        err(f"world_structures_uncovered:{missing}")

if errors:
    print(f"WORLD LIVENESS FAIL {len(errors)}")
    for message in errors:
        print(f"- {message}")
    raise SystemExit(1)

print("WORLD LIVENESS OK")
print(f"continuous_receipts={sum(1 for p in processes.values() if p.get('status') == 'active' and (p.get('recurrence') or {}).get('accrual_mode') == 'continuous')} processes={len(processes)}")
