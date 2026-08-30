from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "runtime/shinobi_runtime/api/repair.py"
text = path.read_text(encoding="utf-8")
old = '            if not isinstance(raw_ids, list) or not 1 <= len(raw_ids) <= _MAX_REPAIR_CHAIN:\n'
new = '            if not isinstance(raw_ids, (list, tuple)) or not 1 <= len(raw_ids) <= _MAX_REPAIR_CHAIN:\n'
if text.count(old) != 1:
    raise SystemExit("repair sequence validator match missing or ambiguous")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("canonical tuple/list repair sequence payload accepted")
