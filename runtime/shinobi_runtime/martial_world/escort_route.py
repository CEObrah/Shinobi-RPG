"""Objective-neutral reconciliation for physical escort route settlement.

The mature route reducer still owns exposure, outlaw pressure and exact contact.
This adapter repairs the old cargo-only closing assumptions for movements tagged
``movement_kind=escort_contract``. It stages into the same frontier write set and
never commits independently.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .live_state import roster_person, set_roster_person

_ROUTE_OPS = "state/martial-world/route-operations.json"
_WORLD_HISTORY = "state/martial-world/world-history.json"
_LOCAL_SITES = "game/data/martial-world/local-sites.json"


class _WriteView:
    def __init__(self, read_json: Callable[[str], Any], writes: Mapping[str, Any]) -> None:
        self._read_json = read_json
        self._writes = writes

    def read_json(self, path: str) -> Any:
        if path in self._writes:
            return copy.deepcopy(self._writes[path])
        return copy.deepcopy(self._read_json(path))


def _arrival_site(local_sites: Mapping[str, Any], place_ref: str) -> str:
    sites = local_sites.get("sites", {}) if isinstance(local_sites, Mapping) else {}
    if not isinstance(sites, Mapping):
        return place_ref
    rows = sorted(
        str(site_ref) for site_ref, row in sites.items()
        if isinstance(site_ref, str) and isinstance(row, Mapping)
        and str(row.get("parent_place_ref") or "") == place_ref
    )
    if not rows:
        return place_ref
    public = [
        site_ref for site_ref in rows
        if str((sites.get(site_ref) or {}).get("public_access", "public"))
        not in {"restricted_by_faction_policy", "private"}
    ]
    return (public or rows)[0]


def _contract_outcomes(history: Mapping[str, Any], *, at: str) -> dict[str, bool]:
    recent = history.get("recent", []) if isinstance(history, Mapping) else []
    result: dict[str, bool] = {}
    if not isinstance(recent, list):
        return result
    for row in recent:
        if not isinstance(row, Mapping) or str(row.get("at") or "") != at:
            continue
        kind = str(row.get("kind") or "")
        ref = row.get("contract_ref")
        if isinstance(ref, str) and kind in {"contract_completed", "contract_failed"}:
            result[ref] = kind == "contract_completed"
    return result


def reconcile_escort_route_settlement(
    *, read_json: Callable[[str], Any], writes: dict[str, Any],
    events: Sequence[Mapping[str, Any]], at: datetime,
) -> list[dict[str, Any]]:
    """Repair cargo-only closing assumptions after one legacy route frontier."""
    route_refs = {
        str(row.get("owner_ref")) for row in events
        if isinstance(row, Mapping) and row.get("kind") == "route_activity_cycle"
        and isinstance(row.get("owner_ref"), str)
    }
    if not route_refs:
        return []
    try:
        before_ops = read_json(_ROUTE_OPS)
    except FileNotFoundError:
        return []
    after_ops = writes.get(_ROUTE_OPS, before_ops)
    before_moves = before_ops.get("movements", {}) if isinstance(before_ops, Mapping) else {}
    after_moves = after_ops.get("movements", {}) if isinstance(after_ops, Mapping) else {}
    if not isinstance(before_moves, Mapping) or not isinstance(after_moves, Mapping):
        return []
    try:
        history = writes.get(_WORLD_HISTORY, read_json(_WORLD_HISTORY))
    except FileNotFoundError:
        history = {"recent": []}
    outcomes = _contract_outcomes(history, at=at.isoformat())
    if not outcomes:
        return []
    try:
        local_sites = read_json(_LOCAL_SITES)
    except FileNotFoundError:
        local_sites = {"sites": {}}
    view = _WriteView(read_json, writes)
    reviews: list[dict[str, Any]] = []

    for movement_ref, raw in sorted(before_moves.items(), key=lambda item: str(item[0])):
        if not isinstance(movement_ref, str) or not isinstance(raw, Mapping):
            continue
        if raw.get("movement_kind") != "escort_contract" or str(raw.get("route_ref") or "") not in route_refs:
            continue
        if movement_ref in after_moves or movement_ref not in outcomes:
            continue
        success = bool(outcomes[movement_ref])
        objective_kind = str(raw.get("objective_kind") or "escort_shipment")
        item_ref = str(raw.get("item_ref") or "")
        quantity = max(0, int(raw.get("quantity", 0)))
        destination_region = str(raw.get("destination_region") or "")
        destination_place = str(raw.get("destination_place_ref") or "")

        # The historical closer always touched destination market stock. Person
        # jobs carry no cargo, so remove the compatibility empty-key artifact
        # before schema/template validation sees the staged after-image.
        if success and quantity <= 0 and not item_ref and destination_region:
            market_path = f"state/martial-world/markets/{destination_region}.json"
            market = writes.get(market_path)
            if isinstance(market, Mapping):
                market_after = copy.deepcopy(dict(market))
                stock = market_after.get("stock")
                if isinstance(stock, dict) and stock.get("") == 0:
                    stock.pop("", None)
                    writes[market_path] = market_after

        moved_refs: list[str] = []
        if success and destination_place:
            destination = _arrival_site(local_sites, destination_place)
            for ref in raw.get("protected_person_refs", []) if isinstance(raw.get("protected_person_refs"), list) else []:
                if not isinstance(ref, str) or not ref:
                    continue
                try:
                    path, roster, ordinal, person = roster_person(view, ref)
                except (FileNotFoundError, KeyError, TypeError, ValueError):
                    continue
                if str(person.get("location_ref") or "") == destination:
                    continue
                person_after = copy.deepcopy(dict(person))
                person_after["location_ref"] = destination
                writes[path] = set_roster_person(roster, ordinal, person_after)
                view = _WriteView(read_json, writes)
                moved_refs.append(ref)

        reviews.append({
            "kind": "escort_objective_settlement",
            "contract_ref": movement_ref,
            "objective_kind": objective_kind,
            "success": success,
            "protected_people_count": max(0, int(raw.get("protected_people_count", 0))),
            "protected_person_refs_moved": moved_refs,
            "cargo_quantity": quantity,
        })
    return reviews


__all__ = ["reconcile_escort_route_settlement"]
