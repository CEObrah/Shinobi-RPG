"""Chronology wrapper for field development and player-visible wakes.

The production frontier remains the sole world scheduler. This module only
post-processes a frontier that has already been deterministically settled:
route hours become bounded field development, newly-created public funded
contracts become soft player-facing handoffs, and requested standing retinues
are assigned from conserved current faction people at their scheduled review.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .field_development import apply_field_activity
from .handoffs import classify_handoff
from .live_state import roster_person, set_roster_person
from .retinues import select_retinue_members
from .time_integration import settle_martial_world_frontier as _settle_martial_world_frontier

_ROUTE_OPERATIONS = "state/martial-world/route-operations.json"
_COMMITMENTS = "state/martial-world/commitments.json"
_CONTRACTS = "state/martial-world/contracts/index.json"
_DEPLOYMENTS = "state/martial-world/deployments.json"


class _FrontierReadView:
    def __init__(self, read_json: Callable[[str], Mapping[str, Any]], writes: dict[str, Any]) -> None:
        self._read_json = read_json
        self._writes = writes

    def read_json(self, path: str) -> Any:
        if path in self._writes:
            return copy.deepcopy(self._writes[path])
        return copy.deepcopy(self._read_json(path))


def _read_or(read_json: Callable[[str], Mapping[str, Any]], path: str, fallback: Mapping[str, Any]) -> dict[str, Any]:
    try:
        row = read_json(path)
    except FileNotFoundError:
        row = fallback
    return copy.deepcopy(dict(row)) if isinstance(row, Mapping) else copy.deepcopy(dict(fallback))


def _movement_delta_hours(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> int:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        return max(0, int(after.get("elapsed_hours", 0)) - int(before.get("elapsed_hours", 0)))
    if not isinstance(before, Mapping) and isinstance(after, Mapping):
        return max(0, int(after.get("elapsed_hours", 0)))
    if isinstance(before, Mapping) and not isinstance(after, Mapping):
        required = max(0, int(before.get("required_hours", 0)))
        elapsed = max(0, int(before.get("elapsed_hours", 0)))
        return max(0, required - elapsed)
    return 0


def _commitment_actor(commitments: Mapping[str, Any], movement_ref: str, contract_ref: str) -> str | None:
    rows = commitments.get("commitments", {}) if isinstance(commitments, Mapping) else {}
    if not isinstance(rows, Mapping):
        return None
    candidates = {str(x) for x in (movement_ref, contract_ref) if isinstance(x, str) and x}
    for row in rows.values():
        if not isinstance(row, Mapping) or str(row.get("activity_ref") or "") not in candidates:
            continue
        actor = row.get("actor_ref")
        if isinstance(actor, str) and actor:
            return actor
    return None


def _apply_route_field_development(
    *, read_json: Callable[[str], Mapping[str, Any]], writes: dict[str, Any]
) -> list[dict[str, Any]]:
    before_state = _read_or(read_json, _ROUTE_OPERATIONS, {"movements": {}})
    after_state_raw = writes.get(_ROUTE_OPERATIONS, before_state)
    after_state = copy.deepcopy(dict(after_state_raw)) if isinstance(after_state_raw, Mapping) else {"movements": {}}
    before_moves = before_state.get("movements", {}) if isinstance(before_state.get("movements"), Mapping) else {}
    after_moves = after_state.get("movements", {}) if isinstance(after_state.get("movements"), Mapping) else {}
    if not isinstance(before_moves, Mapping) or not isinstance(after_moves, Mapping):
        return []

    commitments_raw = writes.get(_COMMITMENTS)
    commitments = copy.deepcopy(dict(commitments_raw)) if isinstance(commitments_raw, Mapping) else _read_or(read_json, _COMMITMENTS, {"commitments": {}})
    view = _FrontierReadView(read_json, writes)
    summaries: list[dict[str, Any]] = []
    for movement_ref in sorted(set(str(x) for x in before_moves) | set(str(x) for x in after_moves)):
        before = before_moves.get(movement_ref)
        after = after_moves.get(movement_ref)
        delta_hours = _movement_delta_hours(before if isinstance(before, Mapping) else None, after if isinstance(after, Mapping) else None)
        if delta_hours <= 0:
            continue
        source = after if isinstance(after, Mapping) else before
        if not isinstance(source, Mapping):
            continue
        participants = [str(x) for x in source.get("participant_refs", []) if isinstance(x, str)] if isinstance(source.get("participant_refs"), list) else []
        if not participants:
            continue
        contract_ref = str(source.get("contract_ref") or "")
        leader_ref = _commitment_actor(commitments, movement_ref, contract_ref)
        activity_kind = "escort_travel" if contract_ref else "road_travel"
        developed = 0
        points = 0
        for person_ref in participants:
            try:
                path, roster, ordinal, person = roster_person(view, person_ref)
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                continue
            person_after, summary = apply_field_activity(
                person,
                duration_hours_milli=delta_hours * 1000,
                activity_kind=activity_kind,
                leader=person_ref == leader_ref,
                pressure_milli=800 if contract_ref else 650,
            )
            domain_rows = summary.get("domains", []) if isinstance(summary, Mapping) else []
            if isinstance(domain_rows, list):
                points += sum(max(0, int(row.get("points", 0))) for row in domain_rows if isinstance(row, Mapping))
            writes[path] = set_roster_person(roster, ordinal, person_after)
            developed += 1
        if developed:
            summaries.append({
                "movement_ref": movement_ref,
                "activity_kind": activity_kind,
                "hours_settled": delta_hours,
                "people_developed": developed,
                "capability_points": points,
            })
    return summaries


def _append_new_contract_handoffs(
    *, read_json: Callable[[str], Mapping[str, Any]], writes: dict[str, Any],
    handoffs: list[dict[str, Any]], at: datetime,
) -> list[str]:
    raw_after = writes.get(_CONTRACTS)
    if not isinstance(raw_after, Mapping):
        return []
    before = _read_or(read_json, _CONTRACTS, {"active": {}})
    before_active = before.get("active", {}) if isinstance(before.get("active"), Mapping) else {}
    after_active = raw_after.get("active", {}) if isinstance(raw_after.get("active"), Mapping) else {}
    if not isinstance(before_active, Mapping) or not isinstance(after_active, Mapping):
        return []
    existing = {str(row.get("contract_ref") or "") for row in handoffs if isinstance(row, Mapping)}
    added: list[str] = []
    for contract_ref in sorted(set(str(x) for x in after_active) - set(str(x) for x in before_active)):
        contract = after_active.get(contract_ref)
        if not isinstance(contract, Mapping) or str(contract.get("status") or "") != "offered" or contract.get("beneficiary_ref") not in (None, ""):
            continue
        expires_raw = contract.get("expires_at")
        try:
            expires = datetime.fromisoformat(str(expires_raw))
        except (TypeError, ValueError):
            continue
        if expires <= at or contract_ref in existing:
            continue
        notice = {
            "kind": "funded_contract_offer",
            "event_id": f"funded_contract_offer:{contract_ref}",
            "contract_ref": contract_ref,
            "issuer_ref": str(contract.get("issuer_ref") or ""),
            "reward_cash": max(0, int(contract.get("reward_cash", 0))),
            "expires_at": str(expires_raw),
            "delivered_to_player": True,
            "requires_player_decision": False,
        }
        handoffs.append({**notice, "handoff": classify_handoff(notice)})
        existing.add(contract_ref)
        added.append(contract_ref)
    return added


def _settle_retinue_assignments(
    *, read_json: Callable[[str], Mapping[str, Any]], writes: dict[str, Any],
    handoffs: list[dict[str, Any]], events: Sequence[Mapping[str, Any]], at: datetime,
) -> list[dict[str, Any]]:
    assignment_events = [
        row for row in events
        if isinstance(row, Mapping) and row.get("kind") == "retinue_assignment_review"
    ]
    if not assignment_events:
        return []
    state_raw = writes.get(_DEPLOYMENTS)
    state = copy.deepcopy(dict(state_raw)) if isinstance(state_raw, Mapping) else _read_or(
        read_json, _DEPLOYMENTS, {"schema": "jianghu-deployment-state-1.0", "deployments": {}}
    )
    rows = state.setdefault("deployments", {})
    if not isinstance(rows, dict):
        return []
    commitments_raw = writes.get(_COMMITMENTS)
    commitments = copy.deepcopy(dict(commitments_raw)) if isinstance(commitments_raw, Mapping) else _read_or(
        read_json, _COMMITMENTS, {"commitments": {}, "person_index": {}}
    )
    person_index = commitments.get("person_index", {}) if isinstance(commitments.get("person_index"), Mapping) else {}
    unavailable = sorted(str(ref) for ref in person_index if isinstance(ref, str))
    view = _FrontierReadView(read_json, writes)
    settled: list[dict[str, Any]] = []

    for event in sorted(assignment_events, key=lambda row: str(row.get("event_id") or "")):
        retinue_ref = str(event.get("retinue_ref") or event.get("owner_ref") or "")
        current = rows.get(retinue_ref)
        if not isinstance(current, Mapping) or current.get("operation_kind") != "standing_retinue" or current.get("status") != "assignment_pending":
            continue
        leader_ref = str(current.get("leader_ref") or "")
        try:
            _path, roster, _ordinal, leader = roster_person(view, leader_ref)
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            continue
        people = roster.get("people", []) if isinstance(roster.get("people"), list) else []
        requested = max(2, min(3, int(current.get("requested_count", 2))))
        member_refs, roles = select_retinue_members(
            leader,
            [row for row in people if isinstance(row, Mapping)],
            requested_count=requested,
            year=at.year,
            unavailable_refs=unavailable,
        )
        if len(member_refs) < requested:
            failed = copy.deepcopy(dict(current))
            failed["status"] = "assignment_blocked"
            failed["assignment_reviewed_at"] = at.isoformat()
            failed["assignment_blocked_reason"] = "insufficient_currently_available_members"
            rows[retinue_ref] = failed
            continue
        assigned = copy.deepcopy(dict(current))
        assigned["member_refs"] = member_refs
        assigned["member_roles"] = {ref: roles[ref] for ref in member_refs}
        assigned["status"] = "active"
        assigned["assigned_at"] = at.isoformat()
        rows[retinue_ref] = assigned
        notice = {
            "kind": "retinue_assigned",
            "event_id": f"retinue_assigned:{retinue_ref}:{at.isoformat()}",
            "retinue_ref": retinue_ref,
            "chooser_ref": str(assigned.get("chooser_ref") or ""),
            "leader_ref": leader_ref,
            "member_refs": list(member_refs),
            "member_roles": copy.deepcopy(assigned["member_roles"]),
            "delivered_to_player": True,
            "requires_player_decision": False,
        }
        handoffs.append({**notice, "handoff": classify_handoff(notice)})
        settled.append({
            "retinue_ref": retinue_ref,
            "member_refs": list(member_refs),
            "member_roles": copy.deepcopy(assigned["member_roles"]),
        })
    writes[_DEPLOYMENTS] = state
    return settled


def augment_frontier_with_progression(
    *, read_json: Callable[[str], Mapping[str, Any]], frontier: Mapping[str, Any], at: datetime,
    events: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    out = copy.deepcopy(dict(frontier))
    writes = {
        str(path): copy.deepcopy(dict(record))
        for path, record in dict(out.get("writes", {})).items()
        if isinstance(path, str) and isinstance(record, Mapping)
    }
    handoffs = [copy.deepcopy(dict(row)) for row in out.get("handoffs", []) if isinstance(row, Mapping)]
    route_development = _apply_route_field_development(read_json=read_json, writes=writes)
    retinues = _settle_retinue_assignments(
        read_json=read_json, writes=writes, handoffs=handoffs, events=events, at=at,
    )
    new_contracts = _append_new_contract_handoffs(read_json=read_json, writes=writes, handoffs=handoffs, at=at)
    reviews = [copy.deepcopy(dict(row)) for row in out.get("reviews", []) if isinstance(row, Mapping)]
    if route_development:
        reviews.append({
            "kind": "field_development",
            "movement_count": len(route_development),
            "movements": route_development[:32],
        })
    if retinues:
        reviews.append({"kind": "retinue_assignment_review", "retinues": retinues[:16]})
    if new_contracts:
        reviews.append({"kind": "player_visible_contract_wake", "contract_refs": new_contracts[:32]})
    out["writes"] = writes
    out["handoffs"] = handoffs
    out["reviews"] = reviews
    return out


def settle_martial_world_frontier(
    *, read_json: Callable[[str], Mapping[str, Any]], schedule: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]], at: datetime,
) -> dict[str, Any]:
    frontier = _settle_martial_world_frontier(read_json=read_json, schedule=schedule, events=events, at=at)
    return augment_frontier_with_progression(read_json=read_json, frontier=frontier, at=at, events=events)


__all__ = ["settle_martial_world_frontier", "augment_frontier_with_progression"]
