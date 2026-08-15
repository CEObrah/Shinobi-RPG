"""Static loadout refit-policy resolution shared by API discovery and commands.

A loadout may declare a narrowly scoped team fitting policy. The policy does
not grant general stock leadership: it authorizes only the named holder's
registered loadout under the named exact team, using the listed conserved
stocks in deterministic order.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence, Tuple


LOADOUT_INDEX_PATH = "game/data/loadout-records/index.json"
_MAX_LOADOUT_RECORDS = 512
_ALLOWED_TEAM_ROLES = frozenset(("leader", "deputy", "instructor", "holder_self"))


def _loadout_record(repository: Any, loadout_ref: str) -> Mapping[str, Any]:
    index = repository.read_json(LOADOUT_INDEX_PATH)
    routes = index.get("loadouts") if isinstance(index, Mapping) else None
    if not isinstance(routes, Mapping) or len(routes) > _MAX_LOADOUT_RECORDS:
        raise ValueError("loadout refit policy index invalid")
    path = routes.get(loadout_ref)
    if not isinstance(path, str) or not path:
        raise ValueError("loadout refit policy loadout unresolved")
    record = repository.read_json(path)
    loadout = record.get("loadout") if isinstance(record, Mapping) else None
    if (
        not isinstance(loadout, Mapping)
        or loadout.get("id") != loadout_ref
    ):
        raise ValueError("loadout refit policy loadout invalid")
    return loadout


def loadout_refit_policy(repository: Any, loadout_ref: str) -> Optional[Mapping[str, Any]]:
    """Return one validated optional refit policy for a registered loadout."""

    loadout = _loadout_record(repository, loadout_ref)
    raw = loadout.get("refit_policy")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("loadout refit policy invalid")

    assignment_ref = raw.get("assignment_ref")
    holder_ref = raw.get("holder_ref")
    roles = raw.get("authorized_team_roles")
    stocks = raw.get("supply_stock_refs")
    if (
        not isinstance(assignment_ref, str)
        or not assignment_ref.startswith("team.")
        or not isinstance(holder_ref, str)
        or not holder_ref
        or not isinstance(roles, list)
        or not roles
        or len(roles) > 8
        or len(set(roles)) != len(roles)
        or any(role not in _ALLOWED_TEAM_ROLES for role in roles)
        or not isinstance(stocks, list)
        or not stocks
        or len(stocks) > 8
        or len(set(stocks)) != len(stocks)
        or any(not isinstance(ref, str) or not ref.startswith("stock.") for ref in stocks)
    ):
        raise ValueError("loadout refit policy invalid")

    return {
        "assignment_ref": assignment_ref,
        "holder_ref": holder_ref,
        "authorized_team_roles": list(roles),
        "supply_stock_refs": list(stocks),
    }


def assignment_refit_policies(
    repository: Any,
    assignment_ref: str,
) -> Tuple[Tuple[str, Mapping[str, Any]], ...]:
    """Return bounded registered loadout policies for one exact team."""

    index = repository.read_json(LOADOUT_INDEX_PATH)
    routes = index.get("loadouts") if isinstance(index, Mapping) else None
    if not isinstance(routes, Mapping) or len(routes) > _MAX_LOADOUT_RECORDS:
        raise ValueError("loadout refit policy index invalid")

    rows: list[Tuple[str, Mapping[str, Any]]] = []
    for loadout_ref in sorted(routes):
        if not isinstance(loadout_ref, str):
            raise ValueError("loadout refit policy index invalid")
        policy = loadout_refit_policy(repository, loadout_ref)
        if policy is not None and policy.get("assignment_ref") == assignment_ref:
            rows.append((loadout_ref, policy))
    return tuple(rows)


def actor_team_policy_roles(
    team: Mapping[str, Any],
    *,
    actor_ref: str,
    holder_ref: str,
) -> frozenset[str]:
    """Derive only the team roles relevant to a registered refit policy."""

    roles: set[str] = set()
    if team.get("leader_ref") == actor_ref:
        roles.add("leader")
    if team.get("deputy_ref") == actor_ref:
        roles.add("deputy")
    training = team.get("training")
    instructors = training.get("instructor_refs") if isinstance(training, Mapping) else None
    if isinstance(instructors, Sequence) and not isinstance(instructors, (str, bytes, bytearray)):
        if actor_ref in instructors:
            roles.add("instructor")
    if actor_ref == holder_ref:
        roles.add("holder_self")
    return frozenset(roles)
