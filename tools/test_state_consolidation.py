import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors = []


def read_json(path):
    return json.loads(path.read_text())


# Tightly coupled House operational state belongs to the House owner.
for forbidden in (ROOT / "state/house/units.json", ROOT / "state/house/process.json"):
    if forbidden.exists():
        errors.append(f"forbidden House operational sidecar remains: {forbidden.relative_to(ROOT)}")

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
