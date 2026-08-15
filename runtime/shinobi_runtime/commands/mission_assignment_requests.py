"""Durable player mission-assignment request helpers.

Requests persist only player-owned selection preferences. They never contain a
mission target, objective, destination, reward, threat, cause, or outcome. The
existing institutional mission generator remains the sole owner of offer
content.
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any, Mapping, MutableMapping, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.living_world_support import _OBJECTIVE_DIMENSIONS

MISSION_ASSIGNMENT_REQUEST_PATH = "state/reg/mission-assignment-requests.json"
MISSION_ASSIGNMENT_OFFICE_REF = "institution.konoha.mission_assignment"
MISSION_ASSIGNMENT_DESK_REF = "place.konoha.mission_assignment_desk"
MISSION_RANK_ORDER = ("D", "C", "B", "A", "S")
MISSION_FOCI = frozenset(("combat",))
_COMBAT_DIMENSIONS = frozenset(("assault", "capture"))


def empty_assignment_request_registry() -> dict[str, Any]:
    return {
        "schema": "mission-assignment-request-registry",
        "owner_id": "registry.mission_assignment_requests",
        "owner_type": "mission_assignment_request_registry",
        "requests": {},
    }


def load_assignment_request_registry(
    repository: Any,
    *,
    record_writes: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    staged = record_writes.get(MISSION_ASSIGNMENT_REQUEST_PATH) if isinstance(record_writes, Mapping) else None
    if staged is not None:
        row = copy.deepcopy(staged)
    elif repository.read_optional_bytes(MISSION_ASSIGNMENT_REQUEST_PATH) is None:
        row = empty_assignment_request_registry()
    else:
        try:
            row = copy.deepcopy(repository.read_json(MISSION_ASSIGNMENT_REQUEST_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("mission_assignment_request_registry_invalid") from exc
    if (
        not isinstance(row, dict)
        or row.get("schema") != "mission-assignment-request-registry"
        or row.get("owner_id") != "registry.mission_assignment_requests"
        or row.get("owner_type") != "mission_assignment_request_registry"
        or not isinstance(row.get("requests"), dict)
    ):
        raise CommandRejectedError("mission_assignment_request_registry_invalid")
    return row


def normalize_acceptable_ranks(value: object) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes, bytearray))
        or not isinstance(value, Sequence)
        or not value
        or len(value) > len(MISSION_RANK_ORDER)
        or any(not isinstance(rank, str) or rank not in MISSION_RANK_ORDER for rank in value)
        or len(set(value)) != len(value)
    ):
        raise CommandRejectedError("mission_assignment_request_ranks_invalid")
    selected = set(value)
    return tuple(rank for rank in MISSION_RANK_ORDER if rank in selected)


def objective_matches_focus(focus: str, objective_kind: str) -> bool:
    if focus != "combat" or not isinstance(objective_kind, str):
        return False
    dimensions = _OBJECTIVE_DIMENSIONS.get(objective_kind, ())
    return bool(_COMBAT_DIMENSIONS.intersection(dimensions))


def filter_candidates_for_focus(
    candidates: Sequence[tuple[str, str]],
    focus: str,
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (demand_ref, objective_kind)
        for demand_ref, objective_kind in candidates
        if objective_matches_focus(focus, objective_kind)
    )


def assignment_request_ref(team_ref: str, requester_ref: str, command_digest: str) -> str:
    digest = hashlib.sha256(
        f"{team_ref}\x00{requester_ref}\x00{command_digest}".encode("utf-8")
    ).hexdigest()[:24]
    return f"mission_assignment_request.{digest}"


def pending_assignment_request(
    registry: Mapping[str, Any],
    *,
    requester_ref: str | None = None,
    team_ref: str | None = None,
) -> Mapping[str, Any] | None:
    requests = registry.get("requests")
    if not isinstance(requests, Mapping):
        raise CommandRejectedError("mission_assignment_request_registry_invalid")
    rows = []
    for request_ref, request in requests.items():
        if not isinstance(request_ref, str) or not isinstance(request, Mapping):
            raise CommandRejectedError("mission_assignment_request_registry_invalid")
        if request.get("request_ref") != request_ref or request.get("status") != "pending":
            continue
        if requester_ref is not None and request.get("requester_ref") != requester_ref:
            continue
        if team_ref is not None and request.get("team_ref") != team_ref:
            continue
        submitted_at = request.get("submitted_at")
        if not isinstance(submitted_at, str) or not submitted_at:
            raise CommandRejectedError("mission_assignment_request_registry_invalid")
        rows.append((submitted_at, request_ref, request))
    if not rows:
        return None
    rows.sort(key=lambda item: (item[0], item[1]))
    return rows[0][2]


def fulfill_assignment_request(
    repository: Any,
    record_writes: MutableMapping[str, dict[str, Any]],
    *,
    request_ref: str,
    offer_ref: str,
    fulfilled_at: str,
) -> Mapping[str, Any]:
    registry = load_assignment_request_registry(repository, record_writes=record_writes)
    request = registry["requests"].get(request_ref)
    if not isinstance(request, dict) or request.get("status") != "pending":
        raise CommandRejectedError("mission_assignment_request_not_pending")
    request["status"] = "fulfilled"
    request["fulfilled_at"] = fulfilled_at
    request["offer_ref"] = offer_ref
    record_writes[MISSION_ASSIGNMENT_REQUEST_PATH] = registry
    return request


__all__ = [
    "MISSION_ASSIGNMENT_REQUEST_PATH",
    "MISSION_ASSIGNMENT_OFFICE_REF",
    "MISSION_ASSIGNMENT_DESK_REF",
    "MISSION_RANK_ORDER",
    "MISSION_FOCI",
    "assignment_request_ref",
    "empty_assignment_request_registry",
    "filter_candidates_for_focus",
    "fulfill_assignment_request",
    "load_assignment_request_registry",
    "normalize_acceptable_ranks",
    "objective_matches_focus",
    "pending_assignment_request",
]
