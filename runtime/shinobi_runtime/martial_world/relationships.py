"""Bounded deterministic person-to-person relationship event reduction.

Relationship state stores only current directed scores.  Events are ephemeral
causal inputs: callers must prove that the observer actually knows the event.
No dialogue transcript or append-only social history is persisted.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

_SCORE_KEYS = ("trust", "affection", "respect", "familiarity")

# Baseline change at severity 1000 and a neutral/low current score.  Positive
# and negative deltas diminish as a score approaches its relevant bound.
_EVENT_DELTAS: dict[str, dict[str, int]] = {
    "cooperation":      {"trust": 5,  "affection": 1,  "respect": 3,   "familiarity": 4},
    "rescue":           {"trust": 14, "affection": 6,  "respect": 10,  "familiarity": 8},
    "betrayal":         {"trust": -28,"affection": -18,"respect": -14, "familiarity": 6},
    "promise_kept":     {"trust": 9,  "affection": 2,  "respect": 5,   "familiarity": 3},
    "promise_broken":   {"trust": -14,"affection": -8, "respect": -9,  "familiarity": 3},
    "teaching":         {"trust": 4,  "affection": 2,  "respect": 7,   "familiarity": 6},
    "treatment":        {"trust": 7,  "affection": 3,  "respect": 6,   "familiarity": 5},
    "sparring":         {"trust": 1,  "affection": 1,  "respect": 5,   "familiarity": 6},
    "fighting":         {"trust": -5, "affection": -4, "respect": 1,   "familiarity": 7},
    "humiliation":      {"trust": -6, "affection": -10,"respect": -18, "familiarity": 4},
    "shared_danger":    {"trust": 8,  "affection": 4,  "respect": 6,   "familiarity": 8},
    "shared_travel":    {"trust": 3,  "affection": 2,  "respect": 2,   "familiarity": 7},
    "conversation":     {"trust": 1,  "affection": 0,  "respect": 1,   "familiarity": 5},
}


def supported_relationship_event(event_kind: str) -> bool:
    return str(event_kind) in _EVENT_DELTAS


def _clamp_score(key: str, value: int) -> int:
    if key == "familiarity":
        return max(0, min(100, int(value)))
    return max(-100, min(100, int(value)))


def _diminished_delta(key: str, current: int, base: int, severity_milli: int) -> int:
    current = _clamp_score(key, current)
    severity = max(0, min(2000, int(severity_milli)))
    scaled = int(round(int(base) * severity / 1000.0))
    if scaled == 0:
        return 0
    lo = 0 if key == "familiarity" else -100
    hi = 100
    if scaled > 0:
        room = max(0, hi - current)
        if room <= 0:
            return 0
        amount = max(1, (scaled * room + 99) // 100)
        return min(room, amount)
    room = max(0, current - lo)
    if room <= 0:
        return 0
    amount = max(1, ((-scaled) * room + 99) // 100)
    return -min(room, amount)


def relationship_event_delta(
    current: Mapping[str, Any], *, event_kind: str, severity_milli: int = 1000,
    protect_affection: bool = False,
) -> dict[str, int]:
    """Return the bounded score delta for one directed observer reaction."""
    if not supported_relationship_event(event_kind):
        raise KeyError(event_kind)
    base = _EVENT_DELTAS[str(event_kind)]
    result: dict[str, int] = {}
    for key in _SCORE_KEYS:
        if key == "affection" and protect_affection:
            result[key] = 0
            continue
        result[key] = _diminished_delta(
            key, int(current.get(key, 0)), int(base.get(key, 0)), severity_milli,
        )
    return result


def apply_relationship_event(
    state: Mapping[str, Any], *, observer_ref: str, subject_ref: str,
    event_kind: str, observer_knows: bool, severity_milli: int = 1000,
    protected_player_ref: str | None = "pc_wei_tang",
) -> dict[str, Any]:
    """Reduce a known material event into one directed current relationship.

    ``observer_ref`` is the person whose scores may change. ``subject_ref`` is
    the person the event concerns.  Unknown events do nothing.  The protected
    player's affection never changes autonomously; familiarity/trust/respect
    may still reflect directly experienced events without choosing romance.
    """
    if not observer_knows:
        return {
            "state_after": copy.deepcopy(dict(state)),
            "applied": False,
            "reason": "observer_does_not_know_event",
            "delta": {key: 0 for key in _SCORE_KEYS},
        }
    if not observer_ref or not subject_ref or observer_ref == subject_ref:
        raise ValueError("relationship event requires two distinct people")
    if not supported_relationship_event(event_kind):
        raise KeyError(event_kind)

    out = copy.deepcopy(dict(state))
    out.setdefault("schema", "jianghu-social-state-1.0")
    relationships = out.setdefault("relationships", {})
    if not isinstance(relationships, dict):
        raise ValueError("jianghu social relationships invalid")
    edge_ref = f"{observer_ref}|{subject_ref}"
    raw = relationships.get(edge_ref, {})
    if raw not in (None, {}) and not isinstance(raw, Mapping):
        raise ValueError("jianghu social relationship invalid")
    current = {key: _clamp_score(key, int(raw.get(key, 0)) if isinstance(raw, Mapping) else 0) for key in _SCORE_KEYS}
    delta = relationship_event_delta(
        current, event_kind=event_kind, severity_milli=severity_milli,
        protect_affection=(protected_player_ref is not None and observer_ref == protected_player_ref),
    )
    after = {key: _clamp_score(key, current[key] + delta[key]) for key in _SCORE_KEYS}
    relationships[edge_ref] = after
    return {
        "state_after": out,
        "applied": any(delta.values()),
        "reason": "applied",
        "delta": delta,
        "relationship_after": after,
        "edge_ref": edge_ref,
    }


__all__ = [
    "apply_relationship_event",
    "relationship_event_delta",
    "supported_relationship_event",
]
