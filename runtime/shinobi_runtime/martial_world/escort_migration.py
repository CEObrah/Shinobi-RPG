"""Idempotent maintenance migration for legacy open escort contracts.

This module is deliberately a pure state transform. It never writes files and
never advances campaign time/revision. The maintenance transaction wrapper owns
persistence, validation, Git durability and idempotency.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Mapping

from .escort import ordinary_public_lot_quantity, plan_escort_objective, quote_escort_objective

_CONTRACTS = "state/martial-world/contracts/index.json"
_GEOGRAPHY = "game/data/martial-world/geography.json"
_TRAVEL = "game/data/martial-world/travel.json"
_POLICY_VERSION = 3
_OPEN_PRE_DEPARTURE = frozenset({"offered", "accepted"})


def _route_index(geography: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = geography.get("routes", []) if isinstance(geography, Mapping) else []
    if not isinstance(rows, list):
        raise ValueError("jianghu geography routes invalid")
    return {
        str(row["id"]): row
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("id"), str) and row.get("id")
    }


def _place_regions(geography: Mapping[str, Any]) -> dict[str, str]:
    places = geography.get("places", {}) if isinstance(geography, Mapping) else {}
    if not isinstance(places, Mapping):
        return {}
    return {
        str(ref): str(row["climate_profile"])
        for ref, row in places.items()
        if isinstance(ref, str)
        and isinstance(row, Mapping)
        and isinstance(row.get("climate_profile"), str)
        and row.get("climate_profile")
    }


def _issuer_market(issuer_ref: str) -> tuple[str, str]:
    if not issuer_ref.startswith("market:"):
        raise ValueError("legacy escort migration requires exact market issuer")
    region = issuer_ref.split(":", 1)[1]
    if not region:
        raise ValueError("legacy escort migration market issuer is empty")
    return region, f"state/martial-world/markets/{region}.json"


def _route_regions(
    objective: Mapping[str, Any], route: Mapping[str, Any],
    geography: Mapping[str, Any], issuer_region: str,
) -> tuple[str, str]:
    source = str(objective.get("source_region") or issuer_region)
    destination = str(objective.get("destination_region") or "")
    if destination:
        return source, destination
    regions = _place_regions(geography)
    a = regions.get(str(route.get("from") or ""), "")
    b = regions.get(str(route.get("to") or ""), "")
    if source == a and b:
        return source, b
    if source == b and a:
        return source, a
    raise ValueError("legacy escort migration cannot infer destination region")


def _cargo_terms(objective: Mapping[str, Any], kind: str) -> tuple[str, int, int]:
    if kind not in {"escort_shipment", "escort_mixed_convoy"}:
        return "", 0, 0
    item_ref = str(objective.get("item_ref") or "")
    old_quantity = max(0, int(objective.get("quantity", 0)))
    old_value = max(0, int(objective.get("cargo_value_cash", 0)))
    if not item_ref or old_quantity <= 0:
        raise ValueError("legacy cargo escort is missing physical cargo")
    new_quantity = ordinary_public_lot_quantity(item_ref, old_quantity)
    if new_quantity <= 0:
        raise ValueError("legacy cargo escort normalized to empty shipment")
    if new_quantity == old_quantity:
        return item_ref, new_quantity, old_value
    if old_value <= 0:
        raise ValueError("legacy cargo escort is missing cargo value")
    unit_value = max(1, old_value // old_quantity)
    return item_ref, new_quantity, unit_value * new_quantity


def plan_escort_policy_v3_migration(
    read_json: Callable[[str], Mapping[str, Any]],
) -> dict[str, Any]:
    """Return conserved owner after-images for all open pre-v3 escort offers.

    Only pre-departure contracts are migrated. IDs, offer/acceptance status,
    beneficiary, participants, offer/expiry times and source_ref are preserved.
    No cargo has left the source market at this lifecycle stage, so only escrow
    and the physical objective need reconciliation.
    """
    contracts = copy.deepcopy(dict(read_json(_CONTRACTS)))
    active = contracts.get("active", {}) if isinstance(contracts, Mapping) else {}
    if not isinstance(active, dict):
        raise ValueError("jianghu contract index invalid")
    geography = read_json(_GEOGRAPHY)
    travel = read_json(_TRAVEL)
    routes = _route_index(geography)

    market_cache: dict[str, dict[str, Any]] = {}
    market_paths_changed: set[str] = set()
    migrated_refs: list[str] = []
    accepted_refs: list[str] = []
    refund_cash = 0
    topup_cash = 0
    escrow_delta = 0
    market_cash_delta = 0

    for contract_ref in sorted(str(ref) for ref in active if isinstance(ref, str)):
        contract = active.get(contract_ref)
        if not isinstance(contract, dict):
            continue
        if contract.get("contract_type") != "escort" or str(contract.get("status") or "") not in _OPEN_PRE_DEPARTURE:
            continue
        objective = contract.get("objective", {})
        if not isinstance(objective, Mapping):
            raise ValueError("legacy escort objective invalid")
        if max(0, int(objective.get("escort_policy_version", 0))) >= _POLICY_VERSION:
            continue

        kind = str(objective.get("kind") or "escort_shipment")
        route_ref = str(objective.get("route_ref") or "")
        route = routes.get(route_ref)
        if not isinstance(route, Mapping):
            raise ValueError(f"legacy escort route missing:{contract_ref}")
        issuer_ref = str(contract.get("issuer_ref") or "")
        issuer_region, market_path = _issuer_market(issuer_ref)
        source_region, destination_region = _route_regions(
            objective, route, geography, issuer_region,
        )
        item_ref, quantity, cargo_value = _cargo_terms(objective, kind)
        protected_refs = objective.get("protected_person_refs", [])
        if not isinstance(protected_refs, list):
            protected_refs = []
        protected_count = max(0, int(objective.get("protected_people_count", 0)))
        civilian_party_kind = objective.get("civilian_party_kind")

        new_objective = plan_escort_objective(
            kind=kind,
            route=route,
            travel=travel,
            source_region=source_region,
            destination_region=destination_region,
            item_ref=item_ref,
            quantity=quantity,
            cargo_value_cash=cargo_value,
            protected_person_refs=[str(x) for x in protected_refs if isinstance(x, str)],
            protected_people_count=protected_count,
            civilian_party_kind=str(civilian_party_kind) if civilian_party_kind else None,
        )
        deadline_hours = objective.get("deadline_hours")
        if isinstance(deadline_hours, int) and not isinstance(deadline_hours, bool) and deadline_hours > 0:
            new_objective["deadline_hours"] = deadline_hours
        new_reward = max(0, int(quote_escort_objective(new_objective)["total_reward_cash"]))
        old_escrow = max(0, int(contract.get("escrow_cash", 0)))

        market = market_cache.get(market_path)
        if market is None:
            market = copy.deepcopy(dict(read_json(market_path)))
            market_cache[market_path] = market
        old_market_cash = max(0, int(market.get("cash_pool", 0)))
        change = new_reward - old_escrow
        if change > old_market_cash:
            raise ValueError(f"legacy escort issuer cannot fund corrected quote:{contract_ref}")
        market["cash_pool"] = old_market_cash - change
        if change:
            market_paths_changed.add(market_path)
            market_cash_delta -= change
            if change < 0:
                refund_cash += -change
            else:
                topup_cash += change

        contract["objective"] = new_objective
        contract["reward_cash"] = new_reward
        contract["escrow_cash"] = new_reward
        escrow_delta += new_reward - old_escrow
        migrated_refs.append(contract_ref)
        if contract.get("status") == "accepted":
            accepted_refs.append(contract_ref)

    if market_cash_delta + escrow_delta != 0:
        raise ValueError("legacy escort migration violates cash conservation")
    if not migrated_refs:
        return {
            "writes": {},
            "migrated_contract_refs": [],
            "accepted_contract_refs": [],
            "refund_cash": 0,
            "topup_cash": 0,
            "escrow_delta": 0,
            "market_cash_delta": 0,
        }

    writes: dict[str, Mapping[str, Any]] = {_CONTRACTS: contracts}
    for path in sorted(market_paths_changed):
        writes[path] = market_cache[path]
    return {
        "writes": writes,
        "migrated_contract_refs": migrated_refs,
        "accepted_contract_refs": accepted_refs,
        "refund_cash": refund_cash,
        "topup_cash": topup_cash,
        "escrow_delta": escrow_delta,
        "market_cash_delta": market_cash_delta,
    }


__all__ = ["plan_escort_policy_v3_migration"]
