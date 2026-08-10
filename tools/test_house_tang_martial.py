import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRAINING = [
    "House Tang instructor curriculum.json",
    "House Tang sword-master curriculum.json",
    "House Tang senior-disciple curriculum.json",
    "House Tang junior-disciple curriculum.json",
]
DOCTRINES = [
    "Invisible Court Core: Command Doctrine.json",
    "Invisible Court Core: Court Defense.json",
    "Invisible Court Core: Court Interdiction.json",
    "Invisible Court Core: Manor Defense.json",
]

errors = []

training_base = json.loads((ROOT / "game/data/organization/training-records/train.house_tang.core.json").read_text())
if training_base["profile"].get("inherits") == "train.civil.general":
    errors.append("House Tang martial training core must not inherit civilian general training")

for name in TRAINING:
    data = json.loads((ROOT / "game/data/organization/training-records" / name).read_text())
    if data["profile"].get("inherits") != "train.house_tang.core":
        errors.append(f"{name}: must inherit train.house_tang.core")

doctrine_base = json.loads((ROOT / "game/data/organization/doctrine-records/doc.house_tang.invisible_court.json").read_text())
base = doctrine_base["doctrine"]
if base.get("inherits") == "doc.civil.general":
    errors.append("Invisible Court martial core must not inherit civilian general doctrine")
if base.get("institution_overlay_ref") != "game/data/organization/institution-doctrine-overlays.json#house_tang":
    errors.append("Invisible Court martial core must use the House Tang institutional overlay")
if "version" in base:
    errors.append("Invisible Court gameplay doctrine core must not carry a version field")

for name in DOCTRINES:
    data = json.loads((ROOT / "game/data/organization/doctrine-records" / name).read_text())
    doctrine = data["doctrine"]
    if doctrine.get("inherits") != "doc.house_tang.invisible_court":
        errors.append(f"{name}: must inherit doc.house_tang.invisible_court")
    if doctrine.get("institution_overlay_ref") != "game/data/organization/institution-doctrine-overlays.json#house_tang":
        errors.append(f"{name}: must use the House Tang institutional overlay")
    if "version" in doctrine:
        errors.append(f"{name}: gameplay doctrine must not carry a version field")

if errors:
    print(f"HOUSE TANG MARTIAL FAIL {len(errors)}")
    for error in errors:
        print(f"- {error}")
    raise SystemExit(1)

print("HOUSE TANG MARTIAL OK")
