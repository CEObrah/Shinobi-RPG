import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors = []


def read_json(path):
    return json.loads(path.read_text())


# House Tang: tightly coupled operational state belongs to the House owner.
process_sidecar = ROOT / "state/house/process.json"
if process_sidecar.exists():
    errors.append("forbidden authoritative House process sidecar remains: state/house/process.json")

houses = {}
for path in (ROOT / "state/house").glob("*.json"):
    data = read_json(path)
    if data.get("schema") == "house":
        houses[data["id"]] = (path, data)

for house_id, (path, house) in houses.items():
    cohorts = house.get("cohorts", [])
    if not isinstance(cohorts, list):
        errors.append(f"{path.relative_to(ROOT)}: cohorts must be an array")
        continue
    member_ids = set(house.get("member_ids", []))
    cohort_members = []
    rostered_members = []
    for cohort in cohorts:
        if cohort.get("owner") != house_id:
            errors.append(f"{path.relative_to(ROOT)}: cohort {cohort.get('id')} owner must be {house_id}")
        for member_id in cohort.get("members", []):
            cohort_members.append(member_id)
            if member_id not in member_ids:
                errors.append(f"{path.relative_to(ROOT)}: cohort member {member_id} is not a House member")
        roster_refs = cohort.get("roster_refs", [])
        if not isinstance(roster_refs, list):
            errors.append(f"{path.relative_to(ROOT)}: cohort {cohort.get('id')} roster_refs must be an array")
            roster_refs = []
        for member_id in roster_refs:
            rostered_members.append(member_id)
            if member_id not in member_ids:
                errors.append(f"{path.relative_to(ROOT)}: rostered member {member_id} is not a House member")
    all_cohort_members = cohort_members + rostered_members
    if len(all_cohort_members) != len(set(all_cohort_members)):
        errors.append(f"{path.relative_to(ROOT)}: a person appears in more than one House cohort")
    aggregate_sum = 0
    for cohort in cohorts:
        aggregate_count = cohort.get("aggregate_count")
        if not isinstance(aggregate_count, int) or aggregate_count < 0:
            errors.append(f"{path.relative_to(ROOT)}: cohort {cohort.get('id')} aggregate_count must be a nonnegative integer")
            continue
        aggregate_sum += aggregate_count
        if aggregate_count != len(cohort.get("roster_refs", [])):
            errors.append(
                f"{path.relative_to(ROOT)}: cohort {cohort.get('id')} aggregate_count must equal roster_refs length"
            )
        profile = cohort.get("cohort_profile")
        if aggregate_count == 0 and profile is not None:
            errors.append(f"{path.relative_to(ROOT)}: cohort {cohort.get('id')} zero aggregate_count must have null cohort_profile")
        if aggregate_count > 0 and (not isinstance(profile, dict) or profile.get("representation") != "house_cohort"):
            errors.append(f"{path.relative_to(ROOT)}: cohort {cohort.get('id')} aggregate personnel require house_cohort profile")
    if house.get("rostered_member_count") != aggregate_sum:
        errors.append(f"{path.relative_to(ROOT)}: rostered_member_count drift {house.get('rostered_member_count')} != {aggregate_sum}")
    registry_path = ROOT / "state/person-core/house-tang.json"
    if not registry_path.exists():
        errors.append(f"{path.relative_to(ROOT)}: roster_core_ref has no person-core owner")
    else:
        registry = read_json(registry_path)
        if registry.get("id") != house.get("roster_core_ref") or registry.get("owner_ref") != house_id:
            errors.append(f"{path.relative_to(ROOT)}: person-core registry identity/owner drift")
        if set(registry.get("people", {})) != set(rostered_members):
            errors.append(f"{path.relative_to(ROOT)}: roster_refs and person-core registry differ")
    unassigned = set(house.get("unassigned_members", []))
    external = set(house.get("externally_assigned_members", []))
    if unassigned & external:
        errors.append(f"{path.relative_to(ROOT)}: member cannot be both unassigned and externally assigned")
    classified = set(all_cohort_members) | unassigned | external
    if classified != member_ids:
        errors.append(
            f"{path.relative_to(ROOT)}: House member classification mismatch "
            f"missing={sorted(member_ids - classified)} extra={sorted(classified - member_ids)}"
        )
    process = house.get("operating_process")
    if not isinstance(process, dict) or not process.get("id") or not process.get("status"):
        errors.append(f"{path.relative_to(ROOT)}: embedded operating_process must have id and status")

# Redundant House unit projection was removed; House organization/cohorts live in the House owner.
projection_path = ROOT / "state/house/units.json"
if projection_path.exists():
    errors.append("redundant House unit projection must not be recreated: state/house/units.json")

owner_shard = read_json(ROOT / "state/index/owners/house.json")
if "house.tang.units" in owner_shard.get("owners", {}):
    errors.append("removed House unit projection must not be registered as an owner")

# Load exact characters and top-level operational teams once for cross-owner checks.
exact_characters = {}
exact_character_paths = [ROOT / "state/player.json"] + sorted((ROOT / "state/char").glob("*.json"))
for path in exact_character_paths:
    data = read_json(path)
    if data.get("schema") == "shinobi_character" and data.get("owner_id"):
        exact_characters[data["owner_id"]] = (path, data)

teams = {}
team_dir = ROOT / "state/team"
if team_dir.exists():
    for path in team_dir.glob("*.json"):
        data = read_json(path)
        team_id = data.get("id")
        if team_id:
            teams[team_id] = (path, data)

# Legacy career mirrors may remain structurally, but rank mirrors must agree.
# current_unit_or_office at top level may be a human-readable role label, while
# career_state.current_unit_or_office is a stable owner ID. Validate the ID by
# resolving it to the actual team rather than comparing unlike representations.
for owner_id, (path, character) in exact_characters.items():
    career = character.get("career_state")
    if not isinstance(career, dict):
        continue
    official_rank = character.get("official_rank_or_status")
    career_rank = career.get("current_rank_or_status")
    if career_rank is None:
        career_rank = career.get("rank")
    if official_rank is not None and career_rank is not None and official_rank != career_rank:
        errors.append(
            f"{path.relative_to(ROOT)}: rank mirror drift "
            f"official_rank_or_status={official_rank!r} career={career_rank!r}"
        )

    career_owner = career.get("current_unit_or_office")
    if isinstance(career_owner, str) and career_owner.startswith("team."):
        if career_owner not in teams:
            errors.append(f"{path.relative_to(ROOT)}: career team owner missing: {career_owner}")
            continue
        team_path, team = teams[career_owner]
        schema = team.get("schema")
        if schema == "exact-team":
            roster = set(team.get("member_refs", []))
        else:
            roster = set(team.get("members", []))
        if owner_id not in roster:
            errors.append(
                f"{path.relative_to(ROOT)}: career team drift {career_owner} does not contain {owner_id} "
                f"in {team_path.relative_to(ROOT)}"
            )

# Development: if a shared bank entry exists, its resolved_through is the sole
# development cursor. The exact character cannot carry a second writable cursor.
bank_path = ROOT / "state/development/banks.json"
if bank_path.exists():
    bank = read_json(bank_path)
    for owner_id, entry in bank.get("entries", {}).items():
        if entry.get("owner_type") != "character":
            continue
        if owner_id not in exact_characters:
            errors.append(f"{bank_path.relative_to(ROOT)}: character bank entry has no exact owner: {owner_id}")
            continue
        path, character = exact_characters[owner_id]
        development = character.get("development") or {}
        if "last_settled_at" in development:
            errors.append(
                f"{path.relative_to(ROOT)}: duplicate development cursor; "
                f"bank resolved_through is authoritative for {owner_id}"
            )
        credits = entry.get("credits", {})
        if not isinstance(credits, dict):
            errors.append(f"{bank_path.relative_to(ROOT)}: credits must be an object for {owner_id}")
        else:
            for target, value in credits.items():
                if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                    errors.append(f"{bank_path.relative_to(ROOT)}: invalid residual credit {owner_id}:{target}={value}")

# Team operational state and assignment/provenance remain separate authorities,
# but an assignment explicitly tied to a team must reproduce commander and roster.
assignments_path = ROOT / "state/org/assignments.json"
if assignments_path.exists():
    assignments = read_json(assignments_path)
    for record in assignments.get("records", []):
        limits = record.get("authority_limits") or {}
        team_id = limits.get("team_id") if isinstance(limits, dict) else None
        if not team_id:
            continue
        if team_id not in teams:
            errors.append(f"{assignments_path.relative_to(ROOT)}: assignment {record.get('id')} references missing team {team_id}")
            continue
        team_path, team = teams[team_id]
        commander = team.get("leader_ref") if team.get("schema") == "exact-team" else (team.get("commander") or team.get("jonin_instructor"))
        if record.get("receiving_commander") != commander:
            errors.append(
                f"{assignments_path.relative_to(ROOT)}: assignment {record.get('id')} commander "
                f"{record.get('receiving_commander')} != {team_path.relative_to(ROOT)} commander {commander}"
            )
        members = team.get("member_refs") if team.get("schema") == "exact-team" else team.get("members")
        if record.get("assignment_kind") == "raw_personnel" and isinstance(members, list):
            expected = set(members)
            if commander:
                expected.discard(commander)
            allocated = set(record.get("raw_allocations", []))
            if allocated != expected:
                errors.append(
                    f"{assignments_path.relative_to(ROOT)}: assignment {record.get('id')} raw roster drift "
                    f"missing={sorted(expected - allocated)} extra={sorted(allocated - expected)}"
                )

# Legacy micro-unit and tactical-team owners are retired. Their absence is checked
# by the production audit; active force/formation conservation is validated elsewhere.

if errors:
    print(f"STATE CONSOLIDATION FAIL {len(errors)}")
    for error in errors:
        print(f"- {error}")
    raise SystemExit(1)

print("STATE CONSOLIDATION OK")
