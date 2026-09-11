"""Small cached readers for canonical Jianghu rule catalogs.

Static JSON remains the one data authority.  Runtime code imports these helpers
instead of copying closed registries or policy numbers into Python modules.
"""
from __future__ import annotations
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

_ROOT=Path(__file__).resolve().parents[3]
_MW=_ROOT/'game/data/martial-world'

@lru_cache(maxsize=None)
def _read(name:str)->Mapping[str,Any]:
    row=json.loads((_MW/name).read_text(encoding='utf-8'))
    if not isinstance(row,Mapping): raise ValueError(f'jianghu catalog invalid: {name}')
    return row

def character_system()->Mapping[str,Any]: return _read('character-system.json')
def field_command_catalog()->Mapping[str,Any]: return _read('field-command.json')
def development_rules()->Mapping[str,Any]: return _read('development.json')
def combat_rules()->Mapping[str,Any]: return _read('combat.json')

def combat_martial_disciplines()->tuple[str,...]:
    rows=character_system().get('martial_disciplines',{})
    if not isinstance(rows,Mapping) or not rows: raise ValueError('martial discipline catalog invalid')
    return tuple(str(k) for k in rows)

def office_catalog()->Mapping[str,Any]:
    structure=_read('faction-structure.json')
    rows=structure.get('offices',{})
    return rows if isinstance(rows,Mapping) else {}

def office_relevant_skill(office:str)->str|None:
    row=office_catalog().get(str(office),{})
    if isinstance(row,Mapping) and isinstance(row.get('relevant_skill'),str): return str(row['relevant_skill'])
    # Legacy structure rows predate explicit relevant_skill; keep the mapping in
    # this one catalog adapter rather than shadowing it across consumers.
    mapping={
      'chief_physician':'medicine',
      'quartermaster':'administration','chief_steward':'administration','treasurer':'commerce',
      'chief_martial_instructor':'instruction',
      'field_commander':'command','deputy_field_commander':'command','scout_leader':'stealth_scouting',
    }
    return mapping.get(str(office))

__all__=['character_system','field_command_catalog','development_rules','combat_rules','combat_martial_disciplines','office_catalog','office_relevant_skill']
