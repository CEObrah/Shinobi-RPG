"""Shared exact-team membership view for staged transaction planning."""
from __future__ import annotations

from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError

_TEAM_REGISTRY_PATH = "state/team/registry.json"


def active_exact_team_members(
    planner: Any,
    record_writes: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    """Return active exact-team members, honoring staged after-images.

    Exact team owners remain authority. The active-team registry is bounded
    routing only, while staged exact-team records override repository
    before-images and reserve newly formed members immediately.
    """
    staged_registry = record_writes.get(_TEAM_REGISTRY_PATH)
    if staged_registry is None:
        try:
            registry = planner.repository.read_json(_TEAM_REGISTRY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("exact_team_registry_invalid") from exc
    else:
        registry = staged_registry
    refs = registry.get("active_teams") if isinstance(registry, Mapping) else None
    if (
        not isinstance(refs, list)
        or any(not isinstance(ref, str) or not ref for ref in refs)
    ):
        raise CommandRejectedError("exact_team_registry_invalid")

    staged_by_id: dict[str, Mapping[str, Any]] = {}
    for record in record_writes.values():
        if not isinstance(record, Mapping) or record.get("schema") != "exact-team":
            continue
        team_id = record.get("id")
        if isinstance(team_id, str) and team_id:
            staged_by_id[team_id] = record

    members: set[str] = set()
    for team_ref in refs:
        team = staged_by_id.get(team_ref)
        if team is None:
            try:
                _path, team = planner._exact_team(team_ref)
            except CommandRejectedError:
                continue
        if team.get("status") == "active":
            members.update(
                ref for ref in team.get("member_refs", []) if isinstance(ref, str) and ref
            )

    for team in staged_by_id.values():
        if team.get("status") == "active":
            members.update(
                ref for ref in team.get("member_refs", []) if isinstance(ref, str) and ref
            )
    return members
