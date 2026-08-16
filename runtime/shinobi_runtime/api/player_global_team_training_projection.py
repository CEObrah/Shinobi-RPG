"""Cross-team recovery and weekly-load projection for exact team inspection."""
from __future__ import annotations

from decimal import Decimal
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.commands.global_team_training_load import member_team_training_load
from shinobi_runtime.sim.events import CampaignTime

_INSTALLED = False


def install_player_global_team_training_projection() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.api import campaign_environment as module

    operations = module.RouteAwareCampaignOperations
    original = operations._team_training_interface
    if getattr(original, "_global_team_training_projection", False):
        _INSTALLED = True
        return

    @wraps(original)
    def wrapped(self: Any, team: Mapping[str, Any]) -> Mapping[str, Any]:
        result = dict(original(self, team))
        readiness = dict(result.get("readiness") or {})
        members = team.get("member_refs")
        if not isinstance(members, list) or any(not isinstance(ref, str) for ref in members):
            raise OperationError(503, "object_team_invalid")
        try:
            meta = self.repository.read_json(self.coordinator.meta_path)
            current_time = CampaignTime.parse(meta.get("time"))
            loads = {
                member_ref: member_team_training_load(
                    self.repository, member_ref, as_of=current_time
                )
                for member_ref in members
            }
        except (FileNotFoundError, TypeError, ValueError, CommandRejectedError) as exc:
            raise OperationError(503, "object_team_training_load_invalid") from exc

        member_recovery: dict[str, Mapping[str, Any]] = {}
        aggregate_ready_at = current_time
        all_ready = True
        all_under_weekly_limit = True
        for member_ref in members:
            load = loads[member_ref]
            ready_at = load["recovery_ready_at"]
            weekly_used = load["weekly_hours_used"]
            weekly_remaining = load["weekly_hours_remaining"]
            if not isinstance(ready_at, CampaignTime) or not isinstance(weekly_used, Decimal):
                raise OperationError(503, "object_team_training_load_invalid")
            if not isinstance(weekly_remaining, Decimal):
                raise OperationError(503, "object_team_training_load_invalid")
            ready_now = bool(load["recovery_ready_now"])
            all_ready = all_ready and ready_now
            all_under_weekly_limit = all_under_weekly_limit and weekly_remaining > 0
            if ready_at > aggregate_ready_at:
                aggregate_ready_at = ready_at
            previous = load["last_session_ended_at"]
            member_recovery[member_ref] = {
                "last_session_ended_at": None if previous is None else str(previous),
                "recovery_ready_at": str(ready_at),
                "recovery_ready_now": ready_now,
                "weekly_hours_used_all_teams": format(weekly_used.normalize(), "f"),
                "weekly_hours_remaining_all_teams": format(weekly_remaining.normalize(), "f"),
            }

        readiness["member_recovery"] = member_recovery
        readiness["next_recovery_eligible_at_for_all_members"] = str(aggregate_ready_at)
        readiness["all_members_recovery_ready_now"] = all_ready
        readiness["all_members_within_global_weekly_limit"] = all_under_weekly_limit
        readiness["global_training_load_applies_across_exact_teams"] = True
        readiness["can_start_full_team_session_now"] = bool(
            all_ready
            and all_under_weekly_limit
            and readiness.get("all_members_colocated_now") is True
            and readiness.get("full_team_authorized_instructor_colocated_now") is True
            and readiness.get("full_team_at_registered_facility_now") is True
        )
        result["readiness"] = readiness
        return result

    wrapped._global_team_training_projection = True  # type: ignore[attr-defined]
    operations._team_training_interface = wrapped
    _INSTALLED = True


__all__ = ["install_player_global_team_training_projection"]
