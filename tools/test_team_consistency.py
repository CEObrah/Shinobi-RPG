#!/usr/bin/env python3
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []
warnings: list[str] = []

def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

owners: dict[str, tuple[Path, dict]] = {}
for path in [ROOT / "state/player.json", *sorted((ROOT / "state/char").glob("*.json"))]:
    data = load(path)
    owner_id = data.get("owner_id")
    if owner_id:
        owners[owner_id] = (path, data)

registry_path = ROOT / "state/team/team-doctrine-registry.json"
registry = load(registry_path) if registry_path.exists() else {}
registered_active = set(registry.get("active_teams", []))
frontier = load(ROOT / "state/time/frontier.json")
team_process = next((p for p in frontier.get("processes", []) if p.get("id") == "process_team_doctrine_training"), {})
process_coverage = set(team_process.get("coverage", []))
seen_active: set[str] = set()

for path in sorted((ROOT / "state/team").glob("*.json")):
    data = load(path)
    if data.get("schema") != "team" or data.get("status") != "active":
        continue
    team_id = data.get("id")
    if not team_id:
        errors.append(f"active_team_missing_id:{path.relative_to(ROOT)}")
        continue
    if team_id in seen_active:
        errors.append(f"duplicate_active_team_id:{team_id}")
    seen_active.add(team_id)
    if team_id not in registered_active:
        errors.append(f"active_team_missing_training_registry:{team_id}")
    if team_id not in process_coverage:
        errors.append(f"active_team_missing_training_process_coverage:{team_id}")

    members = [data.get("jonin_instructor"), *data.get("genin", [])]
    members = [m for m in members if m]
    if len(members) != len(set(members)):
        errors.append(f"active_team_duplicate_member:{team_id}")
    role_keys = set((data.get("roles") or {}).keys())
    for role_key in sorted(role_keys - set(members)):
        errors.append(f"active_team_role_for_nonmember:{team_id}:{role_key}")

    for member_id in members:
        owner = owners.get(member_id)
        if owner is None:
            errors.append(f"active_team_missing_member_owner:{team_id}:{member_id}")
            continue
        owner_path, member = owner
        status = str(member.get("team_status", "")).lower()
        if "unassigned" in status or "pending_assignment" in status:
            errors.append(f"stale_team_status_mirror:{team_id}:{owner_path.relative_to(ROOT)}:{member.get('team_status')}")
        current = member.get("current_unit_or_office")
        career = member.get("career_state")
        if member_id != data.get("jonin_instructor"):
            if current != team_id:
                errors.append(f"stale_team_unit_mirror:{team_id}:{owner_path.relative_to(ROOT)}:{current}")
            if isinstance(career, dict) and "current_unit_or_office" in career and career.get("current_unit_or_office") != team_id:
                errors.append(f"stale_team_career_mirror:{team_id}:{owner_path.relative_to(ROOT)}:{career.get('current_unit_or_office')}")
        else:
            command = str((member.get("career_state") or {}).get("command", "")).lower()
            current_text = str(current or "").lower()
            team_name = str(data.get("name", "")).lower()
            if "no permanent team" in command:
                errors.append(f"stale_team_command_mirror:{team_id}:{owner_path.relative_to(ROOT)}:{command}")
            if team_name and team_name not in command and team_name not in current_text:
                errors.append(f"stale_instructor_team_mirror:{team_id}:{owner_path.relative_to(ROOT)}")

for team_id in sorted(registered_active - seen_active):
    errors.append(f"training_registry_active_team_not_active:{team_id}")
for team_id in sorted(process_coverage - seen_active):
    errors.append(f"training_process_coverage_team_not_active:{team_id}")

if warnings:
    print("TEAM CONSISTENCY WARNINGS")
    for warning in warnings:
        print("-", warning)
if errors:
    print("TEAM CONSISTENCY FAILED")
    for error in errors:
        print("-", error)
    sys.exit(1)
print("TEAM CONSISTENCY OK")
