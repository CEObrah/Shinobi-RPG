"""Player-safe current status for the player's House growth and roster.

This is a read projection only. House, roster, commitment, and training owners
remain authoritative; the projection exists so routine offscreen progression is
visible without repository browsing or guessed IDs.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.commands.paths import COMMITMENT_REGISTRY_PATH

_INSTALLED = False
_HOUSE_REF = "house.tang"
_ROSTER_PATH = "state/person-core/house-tang.json"
_OUTREACH_PREFIX = "institution_recruitment_outreach:recruitment.sword_manor_outreach"


def _house_roster_status(self: Any) -> Mapping[str, Any]:
    try:
        _house_path, house = self._owner_record(_HOUSE_REF)
        roster = self.repository.read_json(_ROSTER_PATH)
    except (FileNotFoundError, ValueError, OperationError) as exc:
        raise OperationError(503, "growth_discovery_invalid") from exc
    if house.get("schema") != "house" or roster.get("schema") != "person-core-registry":
        raise OperationError(503, "growth_discovery_invalid")

    member_ids = house.get("member_ids")
    cohorts = house.get("cohorts")
    profiles = roster.get("profiles")
    if not isinstance(member_ids, list) or not isinstance(cohorts, list) or not isinstance(profiles, Mapping):
        raise OperationError(503, "growth_discovery_invalid")

    standing_counts: dict[str, int] = {}
    resolved: list[str] = []
    for profile in profiles.values():
        institutional = profile.get("institutional_progression") if isinstance(profile, Mapping) else None
        if not isinstance(institutional, Mapping):
            continue
        standing = institutional.get("standing")
        if isinstance(standing, str) and standing:
            standing_counts[standing] = standing_counts.get(standing, 0) + 1
        cursor = institutional.get("resolved_through")
        if isinstance(cursor, str) and cursor:
            resolved.append(cursor)

    cohort_rows = []
    for cohort in cohorts:
        if not isinstance(cohort, Mapping):
            continue
        roster_refs = cohort.get("roster_refs")
        count = len(roster_refs) if isinstance(roster_refs, list) else cohort.get("aggregate_count")
        cohort_rows.append(
            {
                "cohort_ref": cohort.get("id"),
                "training": cohort.get("training"),
                "member_count": count,
            }
        )

    operating = house.get("operating_process")
    last_review = operating.get("last_review") if isinstance(operating, Mapping) else None
    return {
        "member_count": len(member_ids),
        "home_place_ref": house.get("home"),
        "last_house_review": last_review,
        "standing_counts": dict(sorted(standing_counts.items())),
        "training_resolved_through_min": min(resolved) if resolved else None,
        "training_resolved_through_max": max(resolved) if resolved else None,
        "cohorts": cohort_rows[:24],
    }


def _outreach_status(self: Any) -> Mapping[str, Any]:
    try:
        registry = self.repository.read_json(COMMITMENT_REGISTRY_PATH)
    except (FileNotFoundError, ValueError) as exc:
        raise OperationError(503, "growth_discovery_invalid") from exc
    records = registry.get("records") if isinstance(registry, Mapping) else None
    if not isinstance(records, list):
        raise OperationError(503, "growth_discovery_invalid")

    matched = []
    for row in records:
        if not isinstance(row, Mapping):
            continue
        basis = row.get("authority_basis")
        if (
            row.get("kind") == "promise"
            and row.get("host_ref") == _HOUSE_REF
            and isinstance(basis, str)
            and basis.startswith(_OUTREACH_PREFIX)
        ):
            matched.append(row)
    if not matched:
        return {
            "status": "inactive",
            "commitment_count": 0,
            "started_at": None,
            "review_at": None,
            "pending_source_pool_refs": [],
            "review_ready_source_pool_refs": [],
        }

    active = sorted(
        row.get("target_ref")
        for row in matched
        if row.get("status") == "active" and isinstance(row.get("target_ref"), str)
    )
    ready = sorted(
        row.get("target_ref")
        for row in matched
        if row.get("status") == "overdue" and isinstance(row.get("target_ref"), str)
    )
    due = sorted(
        row.get("due_at") for row in matched if isinstance(row.get("due_at"), str)
    )
    created = sorted(
        row.get("created_at") for row in matched if isinstance(row.get("created_at"), str)
    )
    refs = sorted(
        row.get("id") for row in matched if isinstance(row.get("id"), str)
    )
    status = "review_ready" if ready else ("active" if active else "settled")
    return {
        "status": status,
        "commitment_count": len(matched),
        "commitment_refs": refs[:32],
        "started_at": created[0] if created else None,
        "review_at": due[0] if due else None,
        "pending_source_pool_refs": active[:32],
        "review_ready_source_pool_refs": ready[:32],
    }


def install_player_house_status_projection() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.api import campaign_environment as module

    operations = module.RouteAwareCampaignOperations
    original = operations._growth_discovery
    if getattr(original, "_player_house_status_projection", False):
        _INSTALLED = True
        return

    @wraps(original)
    def wrapped(self: Any, player_id: str) -> Mapping[str, Any]:
        result = dict(original(self, player_id))
        houses = result.get("player_house_growth")
        if not isinstance(houses, list):
            raise OperationError(503, "growth_discovery_invalid")
        has_tang = any(
            isinstance(row, Mapping) and row.get("institution_ref") == _HOUSE_REF
            for row in houses
        )
        if not has_tang:
            return result

        house_status = _house_roster_status(self)
        outreach_status = _outreach_status(self)
        enriched_houses = []
        for row in houses:
            if not isinstance(row, Mapping):
                raise OperationError(503, "growth_discovery_invalid")
            updated = dict(row)
            if updated.get("institution_ref") == _HOUSE_REF:
                updated["current_status"] = house_status
            enriched_houses.append(updated)
        result["player_house_growth"] = enriched_houses

        policies = result.get("recruitment_policies")
        if not isinstance(policies, list):
            raise OperationError(503, "growth_discovery_invalid")
        enriched_policies = []
        for row in policies:
            if not isinstance(row, Mapping):
                raise OperationError(503, "growth_discovery_invalid")
            updated = dict(row)
            if updated.get("policy_ref") == "recruitment.sword_manor_outreach":
                updated["outreach_status"] = outreach_status
            enriched_policies.append(updated)
        result["recruitment_policies"] = enriched_policies
        return result

    wrapped._player_house_status_projection = True  # type: ignore[attr-defined]
    operations._growth_discovery = wrapped
    _INSTALLED = True


__all__ = ["install_player_house_status_projection"]
