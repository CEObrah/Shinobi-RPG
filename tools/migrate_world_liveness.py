#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def dump(rel, data):
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# 1. Character structure: general world scheduling is not character-owned.
schema_rel = "schemas/shinobi-character.schema.json"
schema = load(schema_rel)
schema.setdefault("properties", {})["runtime"] = False
schema["properties"]["schedule_profile"] = False
dump(schema_rel, schema)

template_rel = "data/runtime/templates/shinobi_character.template.json"
template = load(template_rel)
root_contract = template["object_contracts"][""]
for key in ("runtime", "schedule_profile"):
    if key in root_contract.get("allowed_keys", []):
        root_contract["allowed_keys"].remove(key)
    if key in root_contract.get("canonical_order", []):
        root_contract["canonical_order"].remove(key)
template["object_contracts"].pop("/runtime", None)
template["object_contracts"].pop("/schedule_profile", None)
for key in list(template.get("type_contracts", {})):
    if key == "/runtime" or key.startswith("/runtime/") or key == "/schedule_profile" or key.startswith("/schedule_profile/"):
        del template["type_contracts"][key]
template["writing_rules"] = [
    "Load this template, its registered blank owner skeleton, and the characters system contract before creating or structurally editing a persistent exact character.",
    "A character owns personal state, capability, condition, location, goals and other registered character facts. General autonomous scheduling is not character-owned.",
    "Routine NPC review cadence and process settlement belong to the temporal runtime. Persist only genuine domain deadlines such as deployment return, recovery, mission, training or other registered causal clocks in their owning fields/systems.",
    "Development timing remains governed by the training_development contract and is not the general autonomous-world scheduler.",
    "Do not add an unregistered field; structural change requires maintenance first."
]
dump(template_rel, template)

blank_rel = "data/runtime/blank-owners/shinobi_character.blank.json"
blank = load(blank_rel)
blank.pop("runtime", None)
blank.pop("schedule_profile", None)
dump(blank_rel, blank)

# Remove the two general scheduler mirrors from every exact character, including
# the player. This does not change any character fact, goal, resource, location,
# capability, development credit, mission, relationship or injury.
for path in [ROOT / "state/player.json", *sorted((ROOT / "state/char").glob("*.json"))]:
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for key in ("runtime", "schedule_profile"):
        if key in data:
            del data[key]
            changed = True
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# Character update contract now points autonomous timing to the time process.
characters_rel = "data/runtime/system-contracts/characters.json"
characters = load(characters_rel)
characters["read_first"] = [
    "exact/lite owner",
    "only causal health, relationship, knowledge, career, command, training or dedicated deadline references"
]
characters["write_order"] = [
    "validate causal evidence and elapsed time",
    "write character-owned fields only",
    "write health/relationship/knowledge/office/command changes to their dedicated owners",
    "write general autonomous review timing only through the time_process authority; never add a second character scheduler cursor",
    "rebuild only affected derived indexes"
]
characters["invariants"] = [
    "Never invent player intent.",
    "Unknown is better than filler.",
    "No skill/rank/behavior growth without causal evidence and time.",
    "General autonomous scheduling is centralized in the temporal runtime; exact character owners do not persist runtime or schedule_profile mirrors.",
    "Development, recovery, deployment, mission and other real domain clocks remain separate when their mechanics require them.",
    "Do not back-project future canon achievements."
]
validators = characters.setdefault("validators", [])
if "tools/test_world_liveness.py" not in validators:
    validators.append("tools/test_world_liveness.py")
dump(characters_rel, characters)

# 2. Align the named-character frontier process with the existing centralized
# life-course registry and convert coarse world reviews to boundary-only clocks.
life = load("state/reg/life-course-registry.json")
life_process = life.get("process_state", {}).get("id")
life_cursor = life.get("process_state", {}).get("last_settled_at")
if life_process != "process_named_character_life_course":
    raise SystemExit(f"unexpected life-course process id: {life_process}")

old_cov_rel = "state/time/coverage/process_named_characters_monthly.json"
new_cov_rel = "state/time/coverage/process_named_character_life_course.json"
old_cov = load(old_cov_rel)
old_cov["process_id"] = life_process
dump(new_cov_rel, old_cov)
(ROOT / old_cov_rel).unlink()

frontier_rel = "state/time/frontier.json"
frontier = load(frontier_rel)
for process in frontier.get("processes", []):
    pid = process.get("id")
    if pid == "process_named_characters_monthly":
        process["id"] = life_process
        process["settlement_mode"] = "batchable"
        process["settled_through"] = life_cursor
        process["source"] = "named_character_life_course"
        process["recurrence"] = {
            "kind": "calendar_month_start",
            "accrual_mode": "boundary_only",
            "clock": "00:00:00"
        }
        process["coverage_ref"] = new_cov_rel
        process["wake_policy"] = "exact birthday, deployment, injury, assignment, information delivery or immediate causal wake-up; monthly batch fallback"
    elif pid == "process_world_forces_monthly":
        process["recurrence"]["accrual_mode"] = "boundary_only"
        process["wake_policy"] = "deployment, mission, conflict, shortage, casualty, command change or immediate causal wake-up; monthly batch fallback"
    elif pid == "process_world_structures_monthly":
        process["recurrence"]["accrual_mode"] = "boundary_only"
        process["wake_policy"] = "embedded owner deadline, stock reconciliation trigger, institutional change or immediate causal wake-up; monthly batch fallback"
    elif pid == "process_living_world_monthly":
        process["recurrence"]["accrual_mode"] = "boundary_only"
        process["wake_policy"] = "active faction review deadline, support-person deadline, information delivery or immediate causal wake-up; monthly batch fallback"
dump(frontier_rel, frontier)

# 3. Runtime receipts exist for continuous closure only. Coarse boundary-only
# processes do not claim semantic review between their due boundaries.
runtime_rel = "state/runtime.json"
runtime = load(runtime_rel)
for key in (
    "process_named_characters_monthly",
    "process_named_character_life_course",
    "process_world_forces_monthly",
    "process_world_structures_monthly",
    "process_living_world_monthly",
):
    runtime.get("completed_reviews", {}).pop(key, None)
dump(runtime_rel, runtime)

# 4. Reusable autonomous policy no longer implies a per-owner duplicated clock.
policies_rel = "data/runtime/process-policies.json"
policies = load(policies_rel)
policies["contract_defaults"]["execution_mode"] = "aggregate_boundary_or_causal_wakeup"
policies["policy_templates"]["autonomous_owner_v1"]["replanning_policy"] = (
    "At its aggregate review boundary or any earlier causal wake-up, reassess goals, authority, knowledge, health, resources, dependencies, opposition and risks. "
    "Complete, block, fail, continue or supersede the current plan and create a lawful successor plan when autonomy permits."
)
policies["policy_templates"]["autonomous_owner_v1"]["safe_compaction_policy"] = (
    "Stable distant routine may share an aggregate boundary review. Split immediately on material events, owner-local domain deadlines, thresholds, player dependencies or causal intersections."
)
dump(policies_rel, policies)

# 5. Autonomous simulation text uses the same current rule.
auto_rel = "data/runtime/autonomous-world-simulation.json"
auto = load(auto_rel)
auto["efficiency_rule"] = (
    "Do not instantiate routine distant detail or write a scheduler cursor into every actor. Stable activity is reviewed on its aggregate process boundary; exact owners wake immediately for movement, conflict, resource transfer, leadership, casualty, territory, mission, contract, discovery, a registered domain deadline or other consequential state."
)
auto["successor_rule"] = (
    "After every material operation or autonomous review boundary, surviving autonomous owners re-evaluate goals and may create lawful successor actions. During a time skip, exact causal successors continue through the remaining interval until target time or a hard player interrupt; dormant routine actors need no per-scene polling."
)
dump(auto_rel, auto)

print("world liveness consolidation migration complete")
