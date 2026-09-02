"""Resolve faction-controlled facilities at the actor's actual physical site."""
from __future__ import annotations
import copy
from typing import Any, Mapping



def active_site_controllers(read_json: Any, site_ref: str) -> list[str]:
    """Return current institutional controllers from mutable faction truth."""
    if not isinstance(site_ref, str) or not site_ref:
        return []
    from .faction_registry import current_faction_refs
    from .faction_state import faction_path, hydrate_faction_state
    owners: list[str] = []
    for fid in current_faction_refs(read_json):
        try:
            faction = hydrate_faction_state(read_json(faction_path(fid)))
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            continue
        primary = str(faction.get('local_site_ref') or '')
        controlled = faction.get('controlled_estates') if isinstance(faction.get('controlled_estates'), Mapping) else {}
        if site_ref == primary or (isinstance(controlled, Mapping) and site_ref in controlled):
            owners.append(fid)
    return sorted(set(owners))


def active_site_controller(read_json: Any, site_ref: str) -> str | None:
    owners = active_site_controllers(read_json, site_ref)
    if len(owners) > 1:
        raise ValueError(f'jianghu site has multiple active controllers:{site_ref}')
    return owners[0] if owners else None


def controlled_estate(faction: Mapping[str,Any], site_ref: str) -> Mapping[str,Any] | None:
    rows=faction.get('controlled_estates') if isinstance(faction.get('controlled_estates'),Mapping) else {}
    row=rows.get(str(site_ref)) if isinstance(rows,Mapping) else None
    return row if isinstance(row,Mapping) else None


def buildings_at_site(faction: Mapping[str,Any], site_ref: str) -> dict[str,Any]:
    if str(site_ref) and str(site_ref)==str(faction.get('local_site_ref') or ''):
        row=faction.get('buildings')
        return copy.deepcopy(dict(row)) if isinstance(row,Mapping) else {}
    estate=controlled_estate(faction,site_ref)
    row=estate.get('buildings') if isinstance(estate,Mapping) else None
    return copy.deepcopy(dict(row)) if isinstance(row,Mapping) else {}


def infrastructure_at_site(faction: Mapping[str,Any], site_ref: str) -> dict[str,Any]:
    if str(site_ref) and str(site_ref)==str(faction.get('local_site_ref') or ''):
        row=faction.get('infrastructure')
        return copy.deepcopy(dict(row)) if isinstance(row,Mapping) else {}
    estate=controlled_estate(faction,site_ref)
    row=estate.get('infrastructure') if isinstance(estate,Mapping) else None
    return copy.deepcopy(dict(row)) if isinstance(row,Mapping) else {}


def enterprises_at_site(faction: Mapping[str,Any], site_ref: str) -> dict[str,Any]:
    if str(site_ref) and str(site_ref)==str(faction.get('local_site_ref') or ''):
        row=faction.get('enterprises')
        return copy.deepcopy(dict(row)) if isinstance(row,Mapping) else {}
    estate=controlled_estate(faction,site_ref)
    row=estate.get('enterprises') if isinstance(estate,Mapping) else None
    return copy.deepcopy(dict(row)) if isinstance(row,Mapping) else {}


def site_condition(faction: Mapping[str,Any], site_ref: str) -> dict[str,Any]:
    rows=faction.get('site_conditions') if isinstance(faction.get('site_conditions'),Mapping) else {}
    row=rows.get(str(site_ref)) if isinstance(rows,Mapping) else None
    return copy.deepcopy(dict(row)) if isinstance(row,Mapping) else {}


def set_site_condition(faction: Mapping[str,Any], site_ref: str, condition: Mapping[str,Any] | None) -> dict[str,Any]:
    out=copy.deepcopy(dict(faction)); rows=out.setdefault('site_conditions',{})
    if not isinstance(rows,dict): rows={}; out['site_conditions']=rows
    if condition:
        rows[str(site_ref)]=copy.deepcopy(dict(condition))
    else:
        rows.pop(str(site_ref),None)
        if not rows: out.pop('site_conditions',None)
    return out

__all__=['active_site_controller','active_site_controllers','buildings_at_site','controlled_estate','enterprises_at_site','infrastructure_at_site','set_site_condition','site_condition']
