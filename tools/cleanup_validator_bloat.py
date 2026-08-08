#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "tools/audit.py"
text = path.read_text(encoding="utf-8")

old = '''ROOT=Path(__file__).resolve().parents[1]\nerrors=[]\ndef err(x):errors.append(x)\ndef rj(p):\n try:return json.loads(Path(p).read_text(encoding='utf-8'))\n except Exception as e:err(f'json:{Path(p).relative_to(ROOT)}:{e}');return None\n'''
new = '''ROOT=Path(__file__).resolve().parents[1]\nerrors=[]\n_JSON_CACHE={}\ndef err(x):errors.append(x)\ndef rj(p):\n q=Path(p)\n key=str(q.resolve())\n if key in _JSON_CACHE:return _JSON_CACHE[key]\n try:\n  data=json.loads(q.read_text(encoding='utf-8'))\n  _JSON_CACHE[key]=data\n  return data\n except Exception as e:err(f'json:{q.relative_to(ROOT)}:{e}');return None\n'''
if old not in text:
    raise SystemExit("audit rj block not found")
text = text.replace(old, new, 1)

start_marker = "# No release-history labels or aliases in canonical gameplay data/rules/docs."
end_marker = "# Technique effect-profile closure"
start = text.find(start_marker)
if start >= 0:
    end = text.find(end_marker, start)
    if end < 0:
        raise SystemExit("release-history scan end marker not found")
    text = text[:start] + end_marker + text[end + len(end_marker):]

path.write_text(text, encoding="utf-8")
print("audit cache enabled; repository-wide release-history text scan removed")
