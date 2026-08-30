"""Lightweight Jianghu local services and exact prices."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

_MW = Path(__file__).resolve().parents[3] / "game" / "data" / "martial-world"


def _services() -> Mapping[str, Any]:
    return json.loads((_MW / "services.json").read_text(encoding="utf-8"))


def _sites() -> Mapping[str, Any]:
    return json.loads((_MW / "local-sites.json").read_text(encoding="utf-8"))


def service_quote(*, site_ref: str, service_ref: str, buyer_age: int | None = None) -> dict[str, Any]:
    sites = _sites().get("sites", {})
    site = sites.get(site_ref) if isinstance(sites, Mapping) else None
    if not isinstance(site, Mapping):
        raise ValueError("unknown local site")
    site_type = str(site.get("site_type", ""))
    menus = _services().get("menus", {})
    menu = menus.get(site_type) if isinstance(menus, Mapping) else None
    row = menu.get(service_ref) if isinstance(menu, Mapping) else None
    if not isinstance(row, Mapping):
        raise ValueError("service unavailable")
    min_age = row.get("minimum_age")
    if isinstance(min_age, int) and (buyer_age is None or buyer_age < min_age):
        raise PermissionError("service age restricted")
    base = max(0, int(row.get("price_cash", 0)))
    multiplier = max(1, int(site.get("price_multiplier_milli", 1000)))
    price = (base * multiplier + 999) // 1000
    return {
        "site_ref": site_ref,
        "site_type": site_type,
        "service_ref": service_ref,
        "price_cash": price,
        "duration_minutes": max(0, int(row.get("duration_minutes", 0))),
        "minimum_age": min_age,
        "simulation_effect": str(row.get("simulation_effect") or ""),
    }


__all__ = ["service_quote"]
