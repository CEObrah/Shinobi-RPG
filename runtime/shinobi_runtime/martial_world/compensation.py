"""Lightweight deterministic faction compensation.

One faction treasury and one personal cash balance per person are enough.  This
module deliberately does not create payroll ledgers, bank accounts, or per-site
books.  Monthly settlement consumes real faction cash and transfers it into the
existing roster's ``personal_cash`` values.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

_MW = Path(__file__).resolve().parents[3] / "game" / "data" / "martial-world"


def _cfg() -> Mapping[str, Any]:
    return json.loads((_MW / "compensation.json").read_text(encoding="utf-8"))


def _office_key(value: str) -> str:
    return value.split(":", 1)[0]


def monthly_stipend(person: Mapping[str, Any]) -> int:
    cfg = _cfg()
    grade = str(person.get("membership_grade") or "")
    base = int(cfg.get("grade_monthly_cash", {}).get(grade, 0))
    office_rates = cfg.get("office_monthly_cash", {})
    bonus = 0
    offices = person.get("standing_offices", [])
    if isinstance(offices, Sequence) and not isinstance(offices, (str, bytes)):
        for raw in offices:
            if isinstance(raw, str):
                bonus += int(office_rates.get(_office_key(raw), 0))
    return max(0, base + bonus)


def settle_monthly_compensation(
    faction: Mapping[str, Any],
    roster: Mapping[str, Any],
) -> dict[str, Any]:
    people = roster.get("people", [])
    if not isinstance(people, list):
        raise ValueError("jianghu roster invalid")
    treasury = max(0, int(faction.get("treasury_cash", 0)))
    faction_after = dict(faction)
    roster_after = {**roster, "people": [dict(p) for p in people]}
    due_total = 0
    paid_total = 0
    paid_people = 0
    unpaid_people = 0
    # Stable identity order prevents list-order manipulation from changing who is
    # paid first under a genuine treasury shortfall.
    indexed = list(enumerate(roster_after["people"]))
    indexed.sort(key=lambda pair: str(pair[1].get("person_id", "")))
    for _idx, person in indexed:
        health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
        if health.get("status") == "dead":
            continue
        due = monthly_stipend(person)
        due_total += due
        if due <= 0:
            continue
        paid = min(treasury, due)
        treasury -= paid
        person["personal_cash"] = max(0, int(person.get("personal_cash", 0))) + paid
        paid_total += paid
        if paid == due:
            paid_people += 1
        else:
            unpaid_people += 1
    faction_after["treasury_cash"] = treasury
    return {
        "faction": faction_after,
        "roster": roster_after,
        "due_cash": due_total,
        "paid_cash": paid_total,
        "shortfall_cash": due_total - paid_total,
        "paid_people": paid_people,
        "unpaid_people": unpaid_people,
    }


__all__ = ["monthly_stipend", "settle_monthly_compensation"]
