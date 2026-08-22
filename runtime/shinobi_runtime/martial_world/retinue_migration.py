"""Pure maintenance repair for the permanent travel-team selector policy.

The original permanent-team selector considered raw complementary capability but
not long-term cohort compatibility. That could assign elderly House members to a
teenage heir's standing travel team. This planner recomputes only the affected
standing retinue from the existing conserved roster under the corrected policy.
It never creates, deletes, rerolls, moves, trains or otherwise mutates people.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Callable, Mapping

from .retinues import select_retinue_members

_DEPLOYMENTS = "state/martial-world/deployments.json"
_COMMITMENTS = "state/martial-world/commitments.json"
_META = "state/meta.json"
_RETINUE_REF = "retinue.wei.permanent_travel_team"
_POLICY_NAME = "permanent_travel_team_cohort_v2"


def _world_year(meta: Mapping[str, Any]) -> int:
    # ``time`` is the canonical persisted meta field. ``world_time`` is accepted
    # only for bounded test/adapter compatibility with read projections.
    raw = str(meta.get("time") or meta.get("world_time") or "")
    match = re.match(r"(?:SE-)?(\d+)-", raw)
    if not match:
        raise ValueError("permanent-team migration cannot resolve world year")
    return int(match.group(1))


def _find_person(people: list[Any], person_ref: str) -> Mapping[str, Any] | None:
    for row in people:
        if isinstance(row, Mapping) and str(row.get("person_id") or "") == person_ref:
            return row
    return None


def plan_permanent_team_cohort_v2_migration(
    read_json: Callable[[str], Mapping[str, Any]],
) -> dict[str, Any]:
    """Return an idempotent deployment-only correction for the affected team."""
    deployments_raw = read_json(_DEPLOYMENTS)
    deployments = copy.deepcopy(dict(deployments_raw)) if isinstance(deployments_raw, Mapping) else {}
    rows = deployments.get("deployments", {}) if isinstance(deployments.get("deployments"), Mapping) else {}
    current = rows.get(_RETINUE_REF) if isinstance(rows, Mapping) else None
    if not isinstance(current, Mapping):
        return {"migration": _POLICY_NAME, "writes": {}, "reason": "retinue_absent"}
    if current.get("operation_kind") != "standing_retinue" or current.get("status") != "active":
        return {"migration": _POLICY_NAME, "writes": {}, "reason": "retinue_not_active"}

    leader_ref = str(current.get("leader_ref") or "")
    faction_ref = str(current.get("faction_ref") or "")
    if not leader_ref or not faction_ref:
        raise ValueError("permanent-team migration missing leader or faction")

    roster_path = f"state/martial-world/people/{faction_ref}.json"
    roster = read_json(roster_path)
    people = roster.get("people", []) if isinstance(roster, Mapping) else []
    if not isinstance(people, list):
        raise ValueError("permanent-team migration roster invalid")
    leader = _find_person(people, leader_ref)
    if not isinstance(leader, Mapping):
        raise ValueError("permanent-team migration leader missing from roster")

    meta = read_json(_META)
    year = _world_year(meta)
    commitments = read_json(_COMMITMENTS)
    person_index = commitments.get("person_index", {}) if isinstance(commitments, Mapping) else {}
    unavailable = {
        str(ref)
        for ref in person_index
        if isinstance(person_index, Mapping) and isinstance(ref, str) and ref
    }

    # Standing teams do not reserve hours, but a person already attached to a
    # different active standing retinue should not be duplicated into Wei's.
    for other_ref, other in rows.items():
        if str(other_ref) == _RETINUE_REF or not isinstance(other, Mapping):
            continue
        if other.get("operation_kind") != "standing_retinue" or other.get("status") != "active":
            continue
        members = other.get("member_refs", []) if isinstance(other.get("member_refs"), list) else []
        unavailable.update(str(ref) for ref in members if isinstance(ref, str) and ref)

    chooser_refs = current.get("chooser_refs", []) if isinstance(current.get("chooser_refs"), list) else []
    unavailable.update(str(ref) for ref in chooser_refs if isinstance(ref, str) and ref)
    chooser_ref = current.get("chooser_ref")
    if isinstance(chooser_ref, str) and chooser_ref:
        unavailable.add(chooser_ref)

    member_refs, member_roles = select_retinue_members(
        leader,
        [row for row in people if isinstance(row, Mapping)],
        requested_count=3,
        year=year,
        unavailable_refs=sorted(unavailable),
    )
    if len(member_refs) != 3 or len(member_roles) != 3:
        raise ValueError("corrected permanent-team policy cannot fill three lawful companions")

    old_refs = [str(ref) for ref in current.get("member_refs", []) if isinstance(ref, str)]
    old_roles = current.get("member_roles", {}) if isinstance(current.get("member_roles"), Mapping) else {}
    normalized_old_roles = {str(ref): str(role) for ref, role in old_roles.items() if isinstance(ref, str)}
    normalized_new_roles = {str(ref): str(member_roles[ref]) for ref in member_refs}
    if old_refs == member_refs and normalized_old_roles == normalized_new_roles:
        return {
            "migration": _POLICY_NAME,
            "writes": {},
            "reason": "already_current",
            "previous_member_refs": old_refs,
            "member_refs": member_refs,
            "member_roles": normalized_new_roles,
        }

    after = copy.deepcopy(dict(current))
    after["member_refs"] = list(member_refs)
    after["member_roles"] = normalized_new_roles
    # This is a maintenance correction of the original delegated choice, not a
    # second in-world appointment. Preserve requested/assigned timestamps and
    # all chooser authority exactly as committed.
    rows[_RETINUE_REF] = after
    deployments["deployments"] = rows
    return {
        "migration": _POLICY_NAME,
        "writes": {_DEPLOYMENTS: deployments},
        "reason": "selector_policy_corrected",
        "retinue_ref": _RETINUE_REF,
        "previous_member_refs": old_refs,
        "member_refs": list(member_refs),
        "member_roles": normalized_new_roles,
        "world_year": year,
    }


__all__ = ["plan_permanent_team_cohort_v2_migration"]
