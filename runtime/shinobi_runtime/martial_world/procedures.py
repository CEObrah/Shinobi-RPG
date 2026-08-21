"""Closed deterministic procedure-time registry."""
from __future__ import annotations
import json
from pathlib import Path
_ROOT=Path(__file__).resolve().parents[3]; _MW=_ROOT/'game/data/martial-world'
def procedure_duration_minutes(kind:str)->int:
    d=json.loads((_MW/'procedures.json').read_text(encoding='utf-8')); row=d['procedures'].get(kind)
    if not isinstance(row,dict): raise KeyError(kind)
    return int(row['duration_minutes'])
