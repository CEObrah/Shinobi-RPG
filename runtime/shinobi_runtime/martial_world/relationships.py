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
    # Only events with a current production emitter belong here. New social
    # semantics are added when a real action/world event can produce them.
    "rescue":           {"trust": 14, "affection": 6,  "respect": 10,  "familiarity": 8},
    "teaching":         {"trust": 4,  "affection": 2,  "respect": 7,   "familiarity": 6},
    "treatment":        {"trust": 7,  "affection": 3,  "respect": 6,   "familiarity": 5},
    "fighting":         {"trust": -5, "affection": -4, "respect": 1,   "familiarity": 7},
    "shared_danger":    {"trust": 8,  "affection": 4,  "respect": 6,   "familiarity": 8},
    "shared_travel":    {"trust": 3,  "affection": 2,  "respect": 2,   "familiarity": 7},
    "conversation":     {"trust": 1,  "affection": 0,  "respect": 1,   "familiarity": 5},
    "oath_breach":      {"trust": -22,"affection": -8, "respect": -12, "familiarity": 5},
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


def _apply_relationship_event_in_place(
    out: dict[str, Any], *, observer_ref: str, subject_ref: str,
    event_kind: str, severity_milli: int, protected_player_ref: str | None,
) -> dict[str, Any]:
    """Apply one already-known event to a mutable copied social owner."""
    relationships = out.setdefault("relationships", {})
    if not isinstance(relationships, dict):
        raise ValueError("jianghu social relationships invalid")
    edge_ref = f"{observer_ref}|{subject_ref}"
    raw = relationships.get(edge_ref, {})
    if raw not in (None, {}) and not isinstance(raw, Mapping):
        raise ValueError("jianghu social relationship invalid")
    current = {
        key: _clamp_score(key, int(raw.get(key, 0)) if isinstance(raw, Mapping) else 0)
        for key in _SCORE_KEYS
    }
    delta = relationship_event_delta(
        current, event_kind=event_kind, severity_milli=severity_milli,
        protect_affection=(protected_player_ref is not None and observer_ref == protected_player_ref),
    )
    after = {key: _clamp_score(key, current[key] + delta[key]) for key in _SCORE_KEYS}
    relationships[edge_ref] = after
    return {"delta": delta, "relationship_after": after, "edge_ref": edge_ref}


def apply_relationship_event(
    state: Mapping[str, Any], *, observer_ref: str, subject_ref: str,
    event_kind: str, observer_knows: bool, severity_milli: int = 1000,
    protected_player_ref: str | None = "pc_wei_tang",
) -> dict[str, Any]:
    """Reduce a known material event into one directed current relationship.

    ``observer_ref`` is the person whose scores may change. ``subject_ref`` is
    the person the event concerns. Unknown events do nothing. The protected
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
    applied = _apply_relationship_event_in_place(
        out, observer_ref=observer_ref, subject_ref=subject_ref,
        event_kind=event_kind, severity_milli=severity_milli,
        protected_player_ref=protected_player_ref,
    )
    return {
        "state_after": out,
        "applied": any(applied["delta"].values()),
        "reason": "applied",
        **applied,
    }


def apply_sparse_group_relationship_event(
    state: Mapping[str, Any], *, participant_refs: list[str] | tuple[str, ...],
    event_kind: str, severity_milli: int = 1000,
    protected_player_ref: str | None = "pc_wei_tang",
) -> dict[str, Any]:
    """Apply a shared event without materializing an all-pairs social graph.

    A real traveling/fighting group can contain many people, but one shared
    episode does not make every member a consequential personal relationship
    of every other member. Existing material relationships are reinforced. New
    NPC bonds are a deterministic sparse ring so every participant can acquire
    social ties with O(n) state rather than O(n^2). If the protected player is
    present, their direct reciprocal relationships with all co-participants are
    retained because those are player-facing social facts. The social owner is
    copied once for the whole event.
    """
    if not supported_relationship_event(event_kind):
        raise KeyError(event_kind)
    refs: list[str] = []
    for raw in participant_refs:
        ref = str(raw) if isinstance(raw, str) else ""
        if ref and ref not in refs:
            refs.append(ref)
    out = copy.deepcopy(dict(state))
    out.setdefault("schema", "jianghu-social-state-1.0")
    relationships = out.setdefault("relationships", {})
    if not isinstance(relationships, dict):
        raise ValueError("jianghu social relationships invalid")
    if len(refs) < 2:
        return {"state_after": out, "edge_refs": [], "pair_count": 0}

    allowed = set(refs)
    pairs: set[tuple[str, str]] = set()

    # Preserve/reinforce already-materialized current relationships among the
    # people who actually shared this event.
    for edge_ref in list(relationships):
        if not isinstance(edge_ref, str) or "|" not in edge_ref:
            continue
        a, b = edge_ref.split("|", 1)
        if a in allowed and b in allowed and a != b:
            pairs.add(tuple(sorted((a, b))))

    # New autonomous social state is sparse. A ring gives each NPC at most two
    # new cohort neighbors for this episode, independent of party size.
    if len(refs) == 2:
        pairs.add(tuple(sorted((refs[0], refs[1]))))
    else:
        for idx, a in enumerate(refs):
            b = refs[(idx + 1) % len(refs)]
            if a != b:
                pairs.add(tuple(sorted((a, b))))

    # Player-facing relationships are directly consequential and remain exact.
    if protected_player_ref and protected_player_ref in allowed:
        for other in refs:
            if other != protected_player_ref:
                pairs.add(tuple(sorted((protected_player_ref, other))))

    edge_refs: list[str] = []
    index = {ref: idx for idx, ref in enumerate(refs)}
    ordered_pairs = sorted(pairs, key=lambda pair: (min(index[pair[0]], index[pair[1]]), max(index[pair[0]], index[pair[1]]), pair))
    for a, b in ordered_pairs:
        for observer_ref, subject_ref in ((a, b), (b, a)):
            applied = _apply_relationship_event_in_place(
                out, observer_ref=observer_ref, subject_ref=subject_ref,
                event_kind=event_kind, severity_milli=severity_milli,
                protected_player_ref=protected_player_ref,
            )
            edge_refs.append(str(applied["edge_ref"]))
    return {"state_after": out, "edge_refs": edge_refs, "pair_count": len(ordered_pairs)}


__all__ = [
    "apply_relationship_event",
    "apply_sparse_group_relationship_event",
    "relationship_event_delta",
    "supported_relationship_event",
]
