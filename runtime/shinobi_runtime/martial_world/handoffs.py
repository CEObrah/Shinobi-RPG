"""Canonical salience classification for autonomous Jianghu results."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[3]
_POLICY = _ROOT / "game" / "data" / "martial-world" / "handoff.json"


@lru_cache(maxsize=1)
def _data() -> Mapping[str, Any]:
    raw = json.loads(_POLICY.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("canonical handoff policy invalid")
    return raw


def _class_policy(class_name: str) -> dict[str, Any]:
    rows = _data().get("salience_classes", {})
    row = rows.get(class_name) if isinstance(rows, Mapping) else None
    if not isinstance(row, Mapping):
        raise ValueError(f"canonical handoff salience class missing: {class_name}")
    return {
        "class": class_name,
        "player_facing": bool(row.get("player_facing")),
        "interrupts_event_seeking": bool(row.get("interrupts_event_seeking")),
        "requires_player_decision": bool(row.get("requires_player_decision")),
    }


def _event_kind_set(class_name: str) -> frozenset[str]:
    rows = _data().get("event_kinds", {})
    values = rows.get(class_name, []) if isinstance(rows, Mapping) else []
    if not isinstance(values, list):
        raise ValueError(f"canonical handoff event kind registry invalid: {class_name}")
    return frozenset(str(value) for value in values if isinstance(value, str) and value)


def classify_handoff(event: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(event.get("kind") or "")
    if event.get("requires_player_decision") is True or kind in _event_kind_set("hard_decision"):
        return _class_policy("hard_decision")
    if event.get("delivered_to_player") is True or kind in _event_kind_set("soft_player_facing"):
        return _class_policy("soft_player_facing")
    return _class_policy("internal")


__all__ = ["classify_handoff"]
