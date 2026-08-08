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


def parse_birth(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"SE-(-?\d+)-(\d+)-(\d+)", value)
    if not match:
        return None
    return tuple(map(int, match.groups()))


frontier = read_json(ROOT / "state/time/frontier.json")
runtime = read_json(ROOT / "state/runtime.json")
world_time = frontier.get("world_time")
world_key = parse_time(world_time)
if world_key is None:
    err(f"invalid_world_time:{world_time}")
world_match = re.fullmatch(r"SE-(-?\d+)-(\d+)-(\d+)T(\d+):(\d+):(\d+)", world_time or "")
world_year = int(world_match.group(1)) if world_match else None

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

# Only systems whose mechanics genuinely accrue continuously may claim exact
# closure at every world-time update. They require a semantic receipt at the
# same frontier. Coarse routine reviews are boundary-only and need no fake
# partial-interval receipt.
if runtime.get("schema") != "shinobi-world-runtime":
    err("world_runtime_schema")
if runtime.get("last_settled_at") != world_time:
    err(f"world_runtime_frontier_drift:{runtime.get('last_settled_at')}:{world_time}")
receipts = runtime.get("completed_reviews")
if not isinstance(receipts, dict):
    err("completed_reviews_not_object")
    receipts = {}

valid_outcomes = {"no_op", "deferred", "blocked", "failed", "succeeded"}
continuous_ids = set()
for process_id, process in processes.items():
    if process.get("status") != "active":
        continue
    recurrence = process.get("recurrence") or {}
    if recurrence.get("accrual_mode") != "continuous":
        continue
    continuous_ids.add(process_id)
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
    elif receipt_id not in continuous_ids:
        err(f"receipt_for_noncontinuous_process:{receipt_id}")
    elif isinstance(receipt, dict) and receipt.get("process_id") != receipt_id:
        err(f"receipt_key_id_drift:{receipt_id}:{receipt.get('process_id')}")

# Every genuine owner-local schedule object remains a real clock. The migration
# removes generic exact-character scheduler mirrors, so this dynamic scan now
# covers consolidated institution and other domain-owned schedules instead.
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
    if "index" in path.parts or "unit-kernel" in path.parts:
        continue
    walk_schedules(read_json(path), str(path.relative_to(ROOT)))

# Exact character owners no longer carry the general autonomous-world clock.
# Their development and dedicated domain clocks remain separate authorities.
characters = {}
character_paths = [ROOT / "state/player.json"] + sorted((ROOT / "state/char").glob("*.json"))
for path in character_paths:
    character = read_json(path)
    if character.get("schema") != "shinobi_character":
        continue
    owner_id = character.get("owner_id")
    if owner_id:
        characters[owner_id] = (path, character)
    for forbidden in ("runtime", "schedule_profile"):
        if forbidden in character:
            err(f"character_general_scheduler_mirror:{path.relative_to(ROOT)}:{forbidden}")

# Named-character life course is centralized under the existing registry and
# monthly boundary process. Exact birthdays are causal wakes and cannot wait for
# the monthly batch.
life = read_json(ROOT / "state/reg/life-course-registry.json")
life_state = life.get("process_state") or {}
life_process_id = life_state.get("id")
if life_process_id != "process_named_character_life_course":
    err(f"life_course_process_id:{life_process_id}")
life_process = processes.get(life_process_id)
if not life_process:
    err("missing_named_character_life_course_process")
else:
    if (life_process.get("recurrence") or {}).get("accrual_mode") != "boundary_only":
        err("named_character_life_course_must_be_boundary_only")
    if life_process.get("settled_through") != life_state.get("last_settled_at"):
        err(f"life_course_cursor_drift:{life_process.get('settled_through')}:{life_state.get('last_settled_at')}")
    expected_ref = "state/time/coverage/process_named_character_life_course.json"
    if life_process.get("coverage_ref") != expected_ref:
        err(f"life_course_coverage_ref:{life_process.get('coverage_ref')}")
    if (ROOT / "state/time/coverage/process_named_characters_monthly.json").exists():
        err("obsolete_named_character_monthly_coverage_present")
    if "process_named_characters_monthly" in processes:
        err("obsolete_named_character_monthly_process_present")

life_cursor = parse_time(life_state.get("last_settled_at"))
if life_cursor is None:
    err(f"bad_life_course_cursor:{life_state.get('last_settled_at')}")
elif world_year is not None and world_key is not None:
    for owner_id in coverage_by_process.get(life_process_id, []):
        entry = characters.get(owner_id)
        if not entry:
            err(f"life_course_missing_exact_character:{owner_id}")
            continue
        _, character = entry
        birth = parse_birth(character.get("birth_date"))
        if not birth:
            err(f"life_course_bad_birth_date:{owner_id}:{character.get('birth_date')}")
            continue
        _, month, day = birth
        birthday = parse_time(f"SE-{world_year:04d}-{month:02d}-{day:02d}T00:00:00")
        if birthday is not None and life_cursor < birthday <= world_key:
            err(f"unsettled_exact_birthday:{owner_id}:SE-{world_year:04d}-{month:02d}-{day:02d}T00:00:00")

# Active faction plans are autonomous commitments. Their local review cursor is a
# genuine plan clock even though routine world review remains monthly. Seven days
# is the maximum fallback gap; causal information wakes them sooner.
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
    if active and world_key is not None and world_key - last_review >= active_faction_max_gap_seconds:
        err(f"active_faction_review_due:{faction_id}:{plan.get('last_review_at')}:{world_time}")
    if active and not str(faction.get("current_plan") or "").strip():
        err(f"active_faction_missing_current_plan:{faction_id}")
    if active and not str(plan.get("wake_policy") or "").strip():
        err(f"active_faction_missing_wake_policy:{faction_id}")

# Coarse world processes must remain boundary-only. If elapsed minutes really
# matter, that state belongs in a narrower continuous system instead.
for process_id in ("process_named_character_life_course", "process_world_forces_monthly", "process_world_structures_monthly", "process_living_world_monthly"):
    process = processes.get(process_id)
    if process and (process.get("recurrence") or {}).get("accrual_mode") != "boundary_only":
        err(f"coarse_world_process_not_boundary_only:{process_id}")

# World structures process dynamically covers every top-level world registry
# except pressure registry, plus every institutional/force stock owner.
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
    extra = sorted(actual - expected)
    if missing:
        err(f"world_structures_uncovered:{missing}")
    if extra:
        err(f"world_structures_orphan_coverage:{extra}")

if errors:
    print(f"WORLD LIVENESS FAIL {len(errors)}")
    for message in errors:
        print(f"- {message}")
    raise SystemExit(1)

print("WORLD LIVENESS OK")
print(f"continuous_receipts={len(continuous_ids)} processes={len(processes)} exact_characters={len(characters)}")
