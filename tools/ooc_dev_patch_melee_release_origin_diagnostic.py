from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "tests/current/test_combat_contact_pursuit_repair.py"
text = path.read_text(encoding="utf-8")
old = '''    trajectory = event["trace"]["trajectory"]\n'''
new = '''    assert "trace" in event, event\n    trajectory = event["trace"]["trajectory"]\n'''
if text.count(old) != 1:
    raise SystemExit("diagnostic trace assertion match missing or ambiguous")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("post-approach event diagnostic enabled")
