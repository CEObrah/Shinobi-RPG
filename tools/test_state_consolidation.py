import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors = []


def read_json(path):
    return json.loads(path.read_text())


# Tightly coupled House operational state belongs to the House owner.
process_sidecar = ROOT / "state/house/process.json"
if process_sidecar.exists():
    errors.append("forbidden authoritative House process sidecar remains: state/house/process.json")

houses = {}
for path in (ROOT / "state/house").glob("*.json"):
    data = read_json(path)
    if data.get("schema") == "house":
        houses[data["id"]] = (path, data)

for house_id, (path, house) in houses.items():
    units = house.get("permanent_units", [])
    if not isinstance(units, list):
        errors.append(f"{path.relative_to(ROOT)}: permanent_units must be an array")
        continue

    member_ids = set(house.get("member_ids", []))
    unit_members = []
    for unit in units:
        if unit.get("owner") != house_id:
            errors.append(f"{path.relative_to(ROOT)}: unit {unit.get('id')} owner must be {house_id}")
        for member_id in unit.get("members", []):
            unit_members.append(member_id)
            if member_id not in member_ids:
                errors.append(f"{path.relative_to(ROOT)}: unit member {member_id} is not a House member")

    if len(unit_members) != len(set(unit_members)):
        errors.append(f"{path.relative_to(ROOT)}: a person appears in more than one permanent House unit")

    unassigned = set(house.get("unassigned_members", []))
    external = set(house.get("externally_assigned_members", []))
    if unassigned & external:
        errors.append(f"{path.relative_to(ROOT)}: member cannot be both unassigned and externally assigned")

    classified = set(unit_members) | unassigned | external
    if classified != member_ids:
        missing = sorted(member_ids - classified)
        extra = sorted(classified - member_ids)
        errors.append(f"{path.relative_to(ROOT)}: House member classification mismatch missing={missing} extra={extra}")

    process = house.get("operating_process")
    if not isinstance(process, dict) or not process.get("id") or not process.get("status"):
        errors.append(f"{path.relative_to(ROOT)}: embedded operating_process must have id and status")

# Compatibility projection may exist, but it must be non-authoritative and exactly reproduce House-owned facts.
projection_path = ROOT / "state/house/units.json"
if projection_path.exists():
    projection = read_json(projection_path)
    source_ref = projection.get("source_ref")
    if projection.get("authority") is not False or projection.get("derived_cache") is not True:
        errors.append("state/house/units.json must be a non-authoritative derived cache")
    if source_ref != "state/house/tang.json":
        errors.append("state/house/units.json must source state/house/tang.json")
    source = read_json(ROOT / source_ref) if source_ref and (ROOT / source_ref).exists() else {}
    projections = {
        "permanent_units": "permanent_units",
        "unassigned_members": "unassigned_members",
        "externally_assigned_members": "externally_assigned_members",
        "formation_library_ref": "formation_library_ref",
        "reconstitution_policy_ref": "reconstitution_policy_ref",
    }
    for cache_key, source_key in projections.items():
        if projection.get(cache_key) != source.get(source_key):
            errors.append(f"state/house/units.json drift: {cache_key} != House owner {source_key}")
    if projection.get("note") != source.get("standing_readiness_order"):
        errors.append("state/house/units.json drift: note != House standing_readiness_order")

# The derived owner index must not expose the compatibility projection as an owner.
owner_shard = read_json(ROOT / "state/index/owners/house.json")
if "house.tang.units" in owner_shard.get("owners", {}):
    errors.append("derived House unit projection must not be registered as an owner")

# Exact-character development can use the shared residual bank, but once a bank
# exists its resolved_through field is the sole development cursor. The same
# cursor must not also be independently writable on the character sheet.
exact_characters = {}
exact_character_paths = [ROOT / "state/player.json"] + sorted((ROOT / "state/char").glob("*.json"))
for path in exact_character_paths:
    data = read_json(path)
    if data.get("schema") == "shinobi_character" and data.get("owner_id"):
        exact_characters[data["owner_id"]] = (path, data)

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
                f"{path.relative_to(ROOT)}: duplicate development cursor; bank resolved_through is authoritative for {owner_id}"
            )
        credits = entry.get("credits", {})
        if not isinstance(credits, dict):
            errors.append(f"{bank_path.relative_to(ROOT)}: credits must be an object for {owner_id}")
        else:
            for target, value in credits.items():
                if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                    errors.append(f"{bank_path.relative_to(ROOT)}: invalid residual credit {owner_id}:{target}={value}")

# Operational team state and assignment/provenance remain separate authorities,
# but any assignment explicitly tied to a team must reproduce the same commander
# and named raw-personnel roster.
teams = {}
team_dir = ROOT / "state/team"
if team_dir.exists():
    for path in team_dir.glob("*.json"):
        data = read_json(path)
        team_id = data.get("id")
        if team_id:
            teams[team_id] = (path, data)

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
        commander = team.get("commander") or team.get("jonin_instructor")
        if record.get("receiving_commander") != commander:
            errors.append(
                f"{assignments_path.relative_to(ROOT)}: assignment {record.get('id')} commander "
                f"{record.get('receiving_commander')} != {team_path.relative_to(ROOT)} commander {commander}"
            )
        if record.get("assignment_kind") == "raw_personnel" and isinstance(team.get("members"), list):
            expected = set(team.get("members", []))
            if commander:
                expected.discard(commander)
            allocated = set(record.get("raw_allocations", []))
            if allocated != expected:
                errors.append(
                    f"{assignments_path.relative_to(ROOT)}: assignment {record.get('id')} raw roster drift "
                    f"missing={sorted(expected - allocated)} extra={sorted(allocated - expected)}"
                )

# A force owns aggregate personnel accounting while materialized units own their
# operational state. Those files remain separate, but materialized unit headcount
# must exactly equal the force's claimed unit population.
materialized_by_force = {}
unit_dir = ROOT / "state/unit"
if unit_dir.exists():
    for path in unit_dir.glob("*.json"):
        unit = read_json(path)
        if unit.get("schema") != "unit":
            continue
        parent_force = unit.get("parent_force")
        personnel = unit.get("personnel") or {}
        count = personnel.get("count") if isinstance(personnel, dict) else None
        if parent_force and isinstance(count, int):
            materialized_by_force[parent_force] = materialized_by_force.get(parent_force, 0) + count

force_dir = ROOT / "state/force"
if force_dir.exists():
    for path in force_dir.glob("*.json"):
        force = read_json(path)
        if force.get("schema") != "force":
            continue
        force_id = force.get("id")
        claims = force.get("unit_claims", [])
        if not isinstance(claims, list):
            continue
        claimed_count = sum(int(claim.get("count", 0)) for claim in claims if isinstance(claim, dict))
        materialized_count = materialized_by_force.get(force_id, 0)
        if claimed_count != materialized_count:
            errors.append(
                f"{path.relative_to(ROOT)}: unit claim drift claimed={claimed_count} materialized={materialized_count}"
            )

# A battle kernel is allowed to be split because it is explicitly derived.
kernel_dir = ROOT / "state/unit-kernel"
if kernel_dir.exists():
    for path in kernel_dir.glob("*.json"):
        data = read_json(path)
        if data.get("derived_cache") is not True:
            errors.append(f"{path.relative_to(ROOT)}: unit kernel split is allowed only when derived_cache is true")

if errors:
    print(f"STATE CONSOLIDATION FAIL {len(errors)}")
    for error in errors:
        print(f"- {error}")
    raise SystemExit(1)

print("STATE CONSOLIDATION OK")
