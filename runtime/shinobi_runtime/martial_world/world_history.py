"""Bounded semantic world-event receipts.

Current owners remain authoritative.  This compact journal exists only for
meaningful completed events that future causality/player delivery can reference;
it is never a dialogue transcript or monthly debug log.
"""
from __future__ import annotations
import copy
from typing import Any, Mapping

_MAX_RECENT = 256


def record_event(state: Mapping[str, Any], *, at: str, kind: str, **facts: Any) -> dict[str, Any]:
    out = copy.deepcopy(dict(state))
    recent = out.setdefault("recent", [])
    counters = out.setdefault("counters", {})
    totals = out.setdefault("totals", {})
    if not isinstance(recent, list) or not isinstance(counters, dict) or not isinstance(totals, dict):
        raise ValueError("jianghu world history invalid")
    row = {"at": str(at), "kind": str(kind)}
    for key, value in facts.items():
        if value is None or value == [] or value == {}:
            continue
        row[str(key)] = copy.deepcopy(value)
    recent.append(row)
    if len(recent) > _MAX_RECENT:
        del recent[:-_MAX_RECENT]
    counters[str(kind)] = max(0, int(counters.get(str(kind), 0))) + 1
    aggregate = totals.setdefault(str(kind), {})
    if not isinstance(aggregate, dict):
        aggregate = {}; totals[str(kind)] = aggregate
    # Keep only compact additive scalar evidence. IDs, prose and collections
    # remain in the bounded recent window rather than becoming lifetime blobs.
    for key, value in facts.items():
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        aggregate[str(key)] = int(aggregate.get(str(key), 0)) + int(value)
    return out


__all__ = ["record_event"]
