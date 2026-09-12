"""Owner-bounded annual Jianghu public ranking publication."""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .handoffs import classify_handoff
from .rankings import publish_rankings

_REPUTATION = "state/martial-world/reputation.json"


def settle_ranking_publications(
    *, read_json: Callable[[str], Mapping[str, Any]], writes: dict[str, Any],
    events: Sequence[Mapping[str, Any]], at: datetime,
) -> dict[str, Any]:
    due = [row for row in events if isinstance(row, Mapping) and row.get("kind") == "jianghu_ranking_publication"]
    if not due:
        return {"reviews": [], "handoffs": []}
    raw = writes.get(_REPUTATION)
    reputation = copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else copy.deepcopy(dict(read_json(_REPUTATION)))
    audiences = reputation.get("audiences", {}) if isinstance(reputation, Mapping) else {}
    records = [
        {"person_id": str(ref), **dict(row)}
        for ref, row in audiences.items()
        if isinstance(ref, str) and isinstance(row, Mapping)
    ] if isinstance(audiences, Mapping) else []
    rows = publish_rankings(records)[:100]
    rankings = reputation.setdefault("rankings", {})
    if not isinstance(rankings, dict):
        raise ValueError("jianghu ranking owner invalid")
    rankings["public"] = {"published_at": at.isoformat(), "rows": rows}
    writes[_REPUTATION] = reputation
    reviews: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []
    # At most one canonical annual publication should be due, but processing
    # every due row makes duplicate scheduler corruption visible rather than
    # silently hiding it while keeping the same derived table.
    for event in sorted(due, key=lambda row: str(row.get("event_id") or "")):
        notice = {
            "kind": "ranking_publication", "published_at": at.isoformat(), "top": rows[:10],
            "delivered_to_player": True, "requires_player_decision": False,
        }
        handoff = classify_handoff(notice)
        reviews.append({
            "kind": "jianghu_ranking_publication", "event_id": event.get("event_id"),
            "ranked_count": len(rows), "handoff": handoff,
        })
        handoffs.append({**notice, "handoff": handoff})
    return {"reviews": reviews, "handoffs": handoffs}


__all__ = ["settle_ranking_publications"]
