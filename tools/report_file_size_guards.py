#!/usr/bin/env python3
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
patterns = [
    re.compile(r"st_size"),
    re.compile(r"getsize\s*\("),
    re.compile(r"max[_-]?bytes", re.I),
    re.compile(r"byte[_-]?limit", re.I),
    re.compile(r"size[_-]?limit", re.I),
    re.compile(r"too[_-]?large", re.I),
    re.compile(r"len\s*\([^\n]*(?:read_bytes|encode\s*\()"),
]
for path in sorted(ROOT.rglob('*')):
    if not path.is_file() or '.git' in path.parts:
        continue
    rel = path.relative_to(ROOT).as_posix()
    if rel == 'tools/report_file_size_guards.py':
        continue
    if path.suffix.lower() not in {'.py', '.yml', '.yaml', '.md', '.json', '.txt'}:
        continue
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except Exception:
        continue
    for i, line in enumerate(lines, 1):
        if any(p.search(line) for p in patterns):
            print(f'{rel}:{i}:{line.strip()}')
