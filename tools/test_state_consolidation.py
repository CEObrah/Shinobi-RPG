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
