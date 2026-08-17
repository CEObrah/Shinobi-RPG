"""Derived leadership context for player-led exact-team check-ins.

This module does not create a personality, agenda, relationship, or progression
owner. It translates existing team, doctrine, operational-history, and directed
relationship evidence into one bounded contact snapshot at the moment a durable
check-in event is created.
"""
from __future__ import annotations

from typing import Any, Mapping

from shinobi_runtime.commands.living_world_support import _RELATIONSHIP_ROOT, _slug

_MAX_TOPICS = 3


def _append_topic(topics: list[str], value: object) -> None:
    if isinstance(value, str) and value and value not in topics:
        topics.append(value)


def leadership_topic_cues(
    team: Mapping[str, Any],
    profile: Mapping[str, Any],
    doctrine: Mapping[str, Any] | None,
    history: Mapping[str, Any] | None = None,
) -> list[str]:
    """Derive a bounded agenda from already-authoritative team evidence."""

    topics: list[str] = []
    assignment_ref = team.get("current_assignment_ref")
    if isinstance(assignment_ref, str) and assignment_ref:
        _append_topic(topics, "current assignment readiness, delegation, and contingencies")

    familiarity: Mapping[str, Any] | None = None
    doctrine_training: Mapping[str, Any] | None = None
    if isinstance(doctrine, Mapping):
        raw_familiarity = doctrine.get("familiarity")
        familiarity = raw_familiarity if isinstance(raw_familiarity, Mapping) else None
        raw_training = doctrine.get("training")
        doctrine_training = raw_training if isinstance(raw_training, Mapping) else None

    members = [ref for ref in team.get("member_refs", []) if isinstance(ref, str)]
    if isinstance(familiarity, Mapping):
        values = [
            value
            for ref in members
            for value in [familiarity.get(ref)]
            if isinstance(value, int) and not isinstance(value, bool)
        ]
        if values and (min(values) < 50 or max(values) - min(values) >= 20):
            _append_topic(topics, "uneven doctrine familiarity and where leadership attention is needed")

    # A recent mission is useful leadership material even when the mission is
    # already terminal. The cue does not infer success/failure beyond what the
    # history owner actually records; it simply preserves an after-action lane.
    if isinstance(history, Mapping):
        last_mission = history.get("last_mission_ref")
        last_result_at = history.get("last_result_at")
        if isinstance(last_mission, str) and last_mission and isinstance(last_result_at, str) and last_result_at:
            _append_topic(topics, "latest mission lessons, delegated ownership, and follow-through")

    training = team.get("training")
    recent = training.get("recent_sessions") if isinstance(training, Mapping) else None
    if isinstance(recent, list) and recent:
        latest = recent[-1]
        targets = latest.get("targets") if isinstance(latest, Mapping) else None
        if isinstance(targets, Mapping):
            distinct = {
                value for value in targets.values()
                if isinstance(value, str) and value
            }
            if len(distinct) > 1:
                _append_topic(topics, "integrating recent individual training into team coordination")
            elif distinct:
                _append_topic(topics, "transferring the latest training block into field execution")

    if isinstance(doctrine_training, Mapping):
        role_focus = doctrine_training.get("role_focus")
        if isinstance(role_focus, Mapping):
            active_focus = {
                value for value in role_focus.values()
                if isinstance(value, str) and value
            }
            if len(active_focus) > 1:
                _append_topic(topics, "role cross-coverage, deputy initiative, and succession under pressure")

    training_focus = profile.get("training_focus", [])
    if isinstance(training_focus, list):
        for value in training_focus:
            _append_topic(topics, value)
            if len(topics) >= _MAX_TOPICS:
                break

    if not isinstance(assignment_ref, str) or not assignment_ref:
        _append_topic(topics, "next training block, readiness, and what the team can own without Wei")

    if not topics:
        topics.append("readiness, role coverage, and the next training block")
    return topics[:_MAX_TOPICS]


def topic_ownership_cues(topics: list[str]) -> list[str]:
    """Classify what the team can own without deciding anything for Wei."""

    result: list[str] = []
    for topic in topics[:_MAX_TOPICS]:
        lowered = topic.lower()
        if "contingenc" in lowered or "leadership attention" in lowered:
            result.append("shared_boundary")
        elif "mission lessons" in lowered:
            result.append("team_can_own_follow_through")
        elif "deputy" in lowered or "cross-coverage" in lowered:
            result.append("team_can_own_reversible_execution")
        elif "training" in lowered or "coordination" in lowered or "readiness" in lowered:
            result.append("team_can_own_routine_preparation")
        else:
            result.append("shared_boundary")
    return result


def relationship_contact_mode(repository: Any, contact_ref: str, player_ref: str) -> str:
    """Translate a directed saved relationship into observable contact style.

    Raw relationship numbers remain private implementation evidence. The returned
    mode is the committed social presentation of this check-in, not a disclosure
    of hidden sentiment and not permission to invent promises, loyalty, romance,
    hostility, or a protected decision.
    """

    path = f"{_RELATIONSHIP_ROOT}/{_slug(contact_ref)}.json"
    try:
        shard = repository.read_json(path)
    except (FileNotFoundError, ValueError):
        return "professional"
    edges = shard.get("relationship_edges") if isinstance(shard, Mapping) else None
    if not isinstance(edges, Mapping):
        return "professional"
    candidates = [
        row for row in edges.values()
        if isinstance(row, Mapping)
        and row.get("source_id") == contact_ref
        and row.get("target_id") == player_ref
    ]
    if not candidates:
        return "professional"
    # Directed relationship type is part of the stable edge identity; if more
    # than one lawful edge ever exists, choose deterministically by id.
    edge = sorted(candidates, key=lambda row: str(row.get("id", "")))[0]
    tension = edge.get("current_tension")
    if isinstance(tension, str) and tension and tension != "none_saved":
        return "tension_aware_professional"
    trust = edge.get("trust")
    respect = edge.get("respect")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (trust, respect)):
        return "professional"
    if trust >= 65 and respect >= 65:
        return "direct_trusted_professional"
    if respect >= 65:
        return "respectful_direct"
    if trust <= 35:
        return "explicit_confirmation"
    return "professional"


__all__ = [
    "leadership_topic_cues",
    "relationship_contact_mode",
    "topic_ownership_cues",
]
