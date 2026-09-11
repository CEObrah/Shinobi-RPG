"""Chronology wrapper for field development and player-visible wakes.

The production frontier remains the sole world scheduler. This module only
post-processes a frontier that has already been deterministically settled:
route hours become bounded field/rest development, newly player-visible funded
contracts become soft handoffs, and requested standing retinues are assigned
from conserved current faction people at their scheduled review.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from shinobi_runtime.api.contract_visibility import contract_is_player_visible
from .field_development import apply_field_activity
from .commitments import derived_commitment_state
from .handoffs import classify_handoff
from .institutional_progression import (
    close_expired_contract_dossiers, settle_closed_mission_records, stage_house_assignment_offers,
    settle_house_assignment_offer_deliveries, settle_house_career_offer_deliveries,
)
from .scheduler import upsert_one_off_event
from .live_state import roster_person, set_roster_person
from .rest_practice import (
    apply_rest_practice,
    journey_hour_budget,
    practice_domain,
    practice_pressure_milli,
)
from .retinues import select_retinue_members
from .retinue_support import provision_retinue_role_issue
from .faction_state import inventory_path as faction_inventory_path
from .physical_presence import physical_unavailable_person_refs
from .time_integration import settle_core_frontier as _settle_core_frontier

_ROUTE_OPERATIONS = "state/martial-world/route-operations.json"
_CONTRACTS = "state/martial-world/contracts/index.json"
_DEPLOYMENTS = "state/martial-world/deployments.json"
_EQUIPMENT = "state/martial-world/equipment-ledger.json"
_META = "state/meta.json"
_SCHEDULER_PATH = "state/martial-world/scheduler.json"


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


def _movement_elapsed_seconds(row: Mapping[str, Any]) -> int:
    if "elapsed_seconds" not in row:
        raise ValueError("route movement elapsed_seconds missing")
    return max(0, int(row["elapsed_seconds"]))


def _movement_required_seconds(row: Mapping[str, Any]) -> int:
    if "required_seconds" not in row:
        raise ValueError("route movement required_seconds missing")
    return max(0, int(row["required_seconds"]))


def _movement_delta_hours_milli(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> int:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        seconds = max(0, _movement_elapsed_seconds(after) - _movement_elapsed_seconds(before))
    elif not isinstance(before, Mapping) and isinstance(after, Mapping):
        seconds = _movement_elapsed_seconds(after)
    elif isinstance(before, Mapping) and not isinstance(after, Mapping):
        seconds = max(0, _movement_required_seconds(before) - _movement_elapsed_seconds(before))
    else:
        seconds = 0
    return max(0, seconds * 1000 // 3600)


def _active_retinue_roles(state: Mapping[str, Any]) -> dict[str, str]:
    rows = state.get("deployments", {}) if isinstance(state, Mapping) else {}
    if not isinstance(rows, Mapping):
        return {}
    out: dict[str, str] = {}
    for retinue_ref in sorted(str(ref) for ref in rows if isinstance(ref, str)):
        row = rows.get(retinue_ref)
        if not isinstance(row, Mapping) or row.get("operation_kind") != "standing_retinue" or row.get("status") != "active":
            continue
        roles = row.get("member_roles", {}) if isinstance(row.get("member_roles"), Mapping) else {}
        members = row.get("member_refs", []) if isinstance(row.get("member_refs"), list) else []
        for ref in members:
            if isinstance(ref, str) and ref:
                out.setdefault(ref, str(roles.get(ref) or ""))
    return out


def _apply_route_field_development(
    *, read_json: Callable[[str], Mapping[str, Any]], writes: dict[str, Any]
) -> list[dict[str, Any]]:
    # Field/rest development is a delta projection of route movement. If the
    # route owner did not change at this frontier, the before/after movement
    # sets are identical and there is nothing to settle.
    if _ROUTE_OPERATIONS not in writes:
        return []
    before_state = _read_or(read_json, _ROUTE_OPERATIONS, {"movements": {}})
    after_state_raw = writes.get(_ROUTE_OPERATIONS, before_state)
    after_state = copy.deepcopy(dict(after_state_raw)) if isinstance(after_state_raw, Mapping) else {"movements": {}}
    before_moves = before_state.get("movements", {}) if isinstance(before_state.get("movements"), Mapping) else {}
    after_moves = after_state.get("movements", {}) if isinstance(after_state.get("movements"), Mapping) else {}
    if not isinstance(before_moves, Mapping) or not isinstance(after_moves, Mapping):
        return []

    deployments_raw = writes.get(_DEPLOYMENTS)
    deployments = copy.deepcopy(dict(deployments_raw)) if isinstance(deployments_raw, Mapping) else _read_or(
        read_json, _DEPLOYMENTS, {"deployments": {}}
    )
    retinue_roles = _active_retinue_roles(deployments)
    view = _FrontierReadView(read_json, writes)
    training_segment_cache: dict[str, Mapping[str, Any] | None] = {}
    roster_owner_cache: dict[str, dict[str, Any]] = {}
    faction_cache: dict[str, Mapping[str, Any]] = {}
    summaries: list[dict[str, Any]] = []
    for movement_ref in sorted(set(str(x) for x in before_moves) | set(str(x) for x in after_moves)):
        before = before_moves.get(movement_ref)
        after = after_moves.get(movement_ref)
        clock_row = after if isinstance(after, Mapping) else before
        if not isinstance(clock_row, Mapping) or "required_seconds" not in clock_row or "elapsed_seconds" not in clock_row:
            continue
        delta_hours_milli = _movement_delta_hours_milli(before if isinstance(before, Mapping) else None, after if isinstance(after, Mapping) else None)
        if delta_hours_milli <= 0:
            continue
        source = after if isinstance(after, Mapping) else before
        if not isinstance(source, Mapping):
            continue
        participants = [str(x) for x in source.get("participant_refs", []) if isinstance(x, str)] if isinstance(source.get("participant_refs"), list) else []
        if not participants:
            continue
        contract_ref = str(source.get("contract_ref") or "")
        leader_ref = str(source.get("leader_ref") or participants[0])
        activity_kind = "escort_travel" if contract_ref else "road_travel"
        budget = journey_hour_budget(delta_hours_milli)
        active_hours = int(budget["active_route_hours_milli"])
        practice_hours = int(budget["rest_practice_hours_milli"])
        developed = 0
        points = 0
        practice_points = 0
        for person_ref in participants:
            try:
                path, roster, ordinal, person = roster_person(
                    view, person_ref, training_segment_cache=training_segment_cache,
                    roster_owner_cache=roster_owner_cache, faction_cache=faction_cache,
                )
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                continue
            person_after, summary = apply_field_activity(
                person,
                duration_hours_milli=active_hours,
                activity_kind=activity_kind,
                leader=person_ref == leader_ref,
                pressure_milli=800 if contract_ref else 650,
            )
            domain_rows = summary.get("domains", []) if isinstance(summary, Mapping) else []
            if isinstance(domain_rows, list):
                points += sum(max(0, int(row.get("points", 0))) for row in domain_rows if isinstance(row, Mapping))
            domain = practice_domain(person_after, retinue_role=retinue_roles.get(person_ref))
            person_after, rest_summary = apply_rest_practice(
                person_after,
                duration_hours_milli=practice_hours,
                domain=domain,
                pressure_milli=practice_pressure_milli(journey=True),
            )
            practice_points += max(0, int(rest_summary.get("points", 0)))
            writes[path] = set_roster_person(roster, ordinal, person_after, mutate=True)
            roster_owner_cache[path] = writes[path]
            developed += 1
        if developed:
            summaries.append({
                "movement_ref": movement_ref,
                "activity_kind": activity_kind,
                "hours_settled_milli": delta_hours_milli,
                "active_route_hours_milli": active_hours,
                "rest_practice_hours_milli": practice_hours,
                "people_developed": developed,
                "capability_points": points,
                "rest_practice_points": practice_points,
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

    view = _FrontierReadView(read_json, writes)
    try:
        meta = view.read_json(_META)
        player_id = str(meta.get("player_id") or "") if isinstance(meta, Mapping) else ""
        _path, _roster, _ordinal, player = roster_person(view, player_id)
        faction_ref = str(player.get("faction_ref") or "")
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        return []
    if not player_id:
        return []

    world_time = at.isoformat()
    existing = {str(row.get("contract_ref") or "") for row in handoffs if isinstance(row, Mapping)}
    added: list[str] = []
    for contract_ref in sorted(str(x) for x in after_active if isinstance(x, str)):
        contract = after_active.get(contract_ref)
        if not isinstance(contract, Mapping) or str(contract.get("status") or "") != "offered":
            continue
        if not contract_is_player_visible(
            contract, player_id=player_id, faction_ref=faction_ref, world_time=world_time,
        ):
            continue
        before_contract = before_active.get(contract_ref)
        was_visible = isinstance(before_contract, Mapping) and contract_is_player_visible(
            before_contract, player_id=player_id, faction_ref=faction_ref, world_time=world_time,
        )
        if was_visible or contract_ref in existing:
            continue
        notice = {
            "kind": "funded_contract_offer",
            "event_id": f"funded_contract_offer:{contract_ref}",
            "contract_ref": contract_ref,
            "issuer_ref": str(contract.get("issuer_ref") or ""),
            "beneficiary_ref": contract.get("beneficiary_ref"),
            "reward_cash": max(0, int(contract.get("reward_cash", 0))),
            "expires_at": contract.get("expires_at"),
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
    view = _FrontierReadView(read_json, writes)
    commitments = derived_commitment_state(view.read_json)
    person_index = commitments.get("person_index", {}) if isinstance(commitments.get("person_index"), Mapping) else {}
    base_unavailable_refs = {str(ref) for ref in person_index if isinstance(ref, str)}
    base_unavailable_refs |= physical_unavailable_person_refs(view.read_json)
    base_unavailable = sorted(base_unavailable_refs)
    settled: list[dict[str, Any]] = []

    for event in sorted(assignment_events, key=lambda row: str(row.get("event_id") or "")):
        retinue_ref = str(event.get("retinue_ref") or event.get("owner_ref") or "")
        current = rows.get(retinue_ref)
        if not isinstance(current, Mapping) or current.get("operation_kind") != "standing_retinue" or current.get("status") != "assignment_pending":
            continue
        leader_ref = str(current.get("leader_ref") or "")
        chooser_refs = [str(ref) for ref in current.get("chooser_refs", []) if isinstance(ref, str)] if isinstance(current.get("chooser_refs"), list) else []
        reserved_elsewhere = {
            str(member_ref)
            for other_ref, other in rows.items()
            if str(other_ref) != retinue_ref
            and isinstance(other, Mapping)
            and other.get("operation_kind") == "standing_retinue"
            and other.get("status") == "active"
            and isinstance(other.get("member_refs"), list)
            for member_ref in other.get("member_refs", [])
            if isinstance(member_ref, str)
        }
        try:
            _path, roster, _ordinal, leader = roster_person(view, leader_ref)
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            continue
        people = roster.get("people", []) if isinstance(roster.get("people"), list) else []
        requested_count = max(1, int(current.get("requested_count", 3)))
        try:
            member_refs, roles = select_retinue_members(
                leader,
                [row for row in people if isinstance(row, Mapping)],
                year=at.year,
                unavailable_refs=[*base_unavailable, *sorted(reserved_elsewhere), *chooser_refs],
                target_count=requested_count,
            )
        except ValueError:
            member_refs, roles = [], {}
        if len(member_refs) < requested_count:
            failed = copy.deepcopy(dict(current))
            failed["status"] = "assignment_blocked"
            failed["assignment_reviewed_at"] = at.isoformat()
            failed["assignment_blocked_reason"] = "insufficient_currently_available_members"
            rows[retinue_ref] = failed
            notice = {
                "kind": "retinue_assignment_blocked",
                "event_id": f"retinue_assignment_blocked:{retinue_ref}:{at.isoformat()}",
                "retinue_ref": retinue_ref,
                "chooser_refs": list(chooser_refs),
                "leader_ref": leader_ref,
                "reason": failed["assignment_blocked_reason"],
                "delivered_to_player": True,
                "requires_player_decision": False,
            }
            handoffs.append({**notice, "handoff": classify_handoff(notice)})
            continue
        assigned = copy.deepcopy(dict(current))
        assigned["member_refs"] = member_refs
        assigned["member_roles"] = {ref: roles[ref] for ref in member_refs}
        assigned["status"] = "active"
        assigned["assigned_at"] = at.isoformat()
        faction_ref = str(assigned.get("faction_ref") or "")
        provisioning: dict[str, Any] = {}
        if faction_ref:
            inv_path = faction_inventory_path(faction_ref)
            inventory = copy.deepcopy(writes.get(inv_path)) if isinstance(writes.get(inv_path), Mapping) else _read_or(view.read_json, inv_path, {"equipment": {}})
            equipment = copy.deepcopy(writes.get(_EQUIPMENT)) if isinstance(writes.get(_EQUIPMENT), Mapping) else _read_or(view.read_json, _EQUIPMENT, {"schema": "jianghu-equipment-ledger-1.0"})
            provisioning_changed = False
            for member_ref in member_refs:
                role = str(assigned["member_roles"].get(member_ref) or "")
                issued = provision_retinue_role_issue(
                    role=role, faction_ref=faction_ref, person_ref=member_ref,
                    inventory=inventory, equipment_ledger=equipment,
                )
                inventory = issued["inventory_after"]
                equipment = issued["equipment_ledger_after"]
                if issued.get("issued") or issued.get("shortfall"):
                    provisioning[member_ref] = {
                        "role": role,
                        "issued": copy.deepcopy(issued.get("issued", {})),
                        "shortfall": copy.deepcopy(issued.get("shortfall", {})),
                    }
                provisioning_changed = provisioning_changed or bool(issued.get("issued"))
            if provisioning_changed:
                writes[inv_path] = inventory
                writes[_EQUIPMENT] = equipment
        rows[retinue_ref] = assigned
        notice = {
            "kind": "retinue_assigned",
            "event_id": f"retinue_assigned:{retinue_ref}:{at.isoformat()}",
            "retinue_ref": retinue_ref,
            "chooser_refs": list(chooser_refs),
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
            "role_provisioning": provisioning,
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
    expired_missions = close_expired_contract_dossiers(
        read_json=read_json, writes=writes, handoffs=handoffs, reviews=reviews, at=at,
    )
    offer_delivery_events: list[dict[str, Any]] = []
    delivered_assignment_offers = settle_house_assignment_offer_deliveries(
        read_json=read_json, writes=writes, handoffs=handoffs, events=events, at=at,
        pending_one_off_events=offer_delivery_events,
    )
    delivered_career_offers = settle_house_career_offer_deliveries(
        read_json=read_json, writes=writes, handoffs=handoffs, events=events, at=at,
        pending_one_off_events=offer_delivery_events,
    )
    mission_settlements = settle_closed_mission_records(
        read_json=read_json, writes=writes, handoffs=handoffs, at=at,
        pending_one_off_events=offer_delivery_events,
    )
    assignment_offers = stage_house_assignment_offers(
        read_json=read_json, writes=writes, handoffs=handoffs, reviews=reviews, at=at,
        pending_one_off_events=offer_delivery_events,
    )
    if offer_delivery_events:
        schedule_after = copy.deepcopy(out.get("schedule_after", {}))
        if not isinstance(schedule_after, Mapping):
            raise ValueError("jianghu scheduler projection missing after progression")
        for one_off in offer_delivery_events:
            schedule_after = upsert_one_off_event(schedule_after, one_off)
        out["schedule_after"] = schedule_after
        writes[_SCHEDULER_PATH] = copy.deepcopy(dict(schedule_after))
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
    if expired_missions:
        reviews.append({"kind": "institutional_contract_expiry", "operation_refs": expired_missions[:32]})
    if mission_settlements:
        reviews.append({"kind": "institutional_mission_settlement", "operations": mission_settlements[:32]})
    if delivered_assignment_offers:
        reviews.append({"kind": "house_assignment_delivery", "operation_refs": delivered_assignment_offers[:8]})
    if delivered_career_offers:
        reviews.append({"kind": "house_career_offer_delivery", "operation_refs": delivered_career_offers[:8]})
    if assignment_offers:
        reviews.append({"kind": "house_assignment_review", "operation_refs": assignment_offers[:8]})
    out["writes"] = writes
    out["handoffs"] = handoffs
    out["reviews"] = reviews
    return out


def settle_martial_world_frontier(
    *, read_json: Callable[[str], Mapping[str, Any]], schedule: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]], at: datetime,
) -> dict[str, Any]:
    frontier = _settle_core_frontier(read_json=read_json, schedule=schedule, events=events, at=at)
    return augment_frontier_with_progression(read_json=read_json, frontier=frontier, at=at, events=events)


__all__ = ["settle_martial_world_frontier", "augment_frontier_with_progression"]
