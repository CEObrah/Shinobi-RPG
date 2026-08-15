"""Production team-playability projection extensions.

The generic route-aware operations expose exact trainable values. This extension
adds bounded schedule and co-location readiness derived from the same persisted
team/member/model authorities used by the training reducer, without revealing
exact teammate locations.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.api.route_discovery import (
    RouteAwareCampaignOperations as _BaseRouteAwareCampaignOperations,
)
from shinobi_runtime.commands.paths import TRAINING_MODELS_PATH
from shinobi_runtime.sim.events import CampaignTime


_MAX_TEAM_MEMBERS = 16


def project_team_training_readiness(
    team: Mapping[str, Any],
    member_records: Mapping[str, Mapping[str, Any]],
    instructor_records: Mapping[str, Mapping[str, Any]],
    model: Mapping[str, Any],
    *,
    current_time: CampaignTime,
) -> Mapping[str, Any]:
    """Project bounded readiness from persisted team training history.

    Location identities remain private to the runtime. The projection exposes
    only which team members currently share a location and which authorized
    instructors are present with each group.
    """

    members = team.get("member_refs")
    training = team.get("training")
    schedule = model.get("schedule_limits") if isinstance(model, Mapping) else None
    if (
        not isinstance(members, list)
        or not members
        or len(members) > _MAX_TEAM_MEMBERS
        or len(set(members)) != len(members)
        or any(not isinstance(ref, str) or not ref for ref in members)
        or not isinstance(training, Mapping)
        or not isinstance(schedule, Mapping)
    ):
        raise OperationError(503, "object_team_invalid")

    cycle_days = schedule.get("cycle_length_days")
    recovery_hours = schedule.get("minimum_recovery_hours")
    recent_limit = schedule.get("recent_session_limit", 64)
    try:
        weekly_limit = Decimal(str(schedule.get("maximum_hours_per_member_per_week")))
    except Exception as exc:
        raise OperationError(503, "object_team_invalid") from exc
    if (
        isinstance(cycle_days, bool)
        or not isinstance(cycle_days, int)
        or cycle_days <= 0
        or isinstance(recovery_hours, bool)
        or not isinstance(recovery_hours, int)
        or recovery_hours < 0
        or isinstance(recent_limit, bool)
        or not isinstance(recent_limit, int)
        or recent_limit <= 0
        or not weekly_limit.is_finite()
        or weekly_limit <= 0
    ):
        raise OperationError(503, "object_team_invalid")

    recent = training.get("recent_sessions", [])
    if not isinstance(recent, list) or len(recent) > recent_limit:
        raise OperationError(503, "object_team_invalid")

    last_end: dict[str, CampaignTime | None] = {ref: None for ref in members}
    latest_session: tuple[CampaignTime, Mapping[str, Any]] | None = None
    for raw in recent:
        if not isinstance(raw, Mapping):
            raise OperationError(503, "object_team_invalid")
        raw_end = raw.get("ended_at")
        raw_members = raw.get("member_refs")
        if (
            not isinstance(raw_end, str)
            or not isinstance(raw_members, list)
            or not raw_members
            or len(raw_members) > _MAX_TEAM_MEMBERS
            or len(set(raw_members)) != len(raw_members)
            or any(not isinstance(ref, str) or not ref for ref in raw_members)
        ):
            raise OperationError(503, "object_team_invalid")
        try:
            ended = CampaignTime.parse(raw_end)
        except (TypeError, ValueError) as exc:
            raise OperationError(503, "object_team_invalid") from exc
        if ended > current_time:
            raise OperationError(503, "object_team_invalid")
        if latest_session is None or ended > latest_session[0]:
            latest_session = (ended, raw)
        for member_ref in raw_members:
            if member_ref not in last_end:
                continue
            previous = last_end[member_ref]
            if previous is None or ended > previous:
                last_end[member_ref] = ended

    location_groups: dict[str, list[str]] = {}
    for member_ref in members:
        record = member_records.get(member_ref)
        location = record.get("current_location_id") if isinstance(record, Mapping) else None
        if not isinstance(location, str) or not location:
            raise OperationError(503, "object_team_invalid")
        location_groups.setdefault(location, []).append(member_ref)

    instructor_locations: dict[str, str] = {}
    for instructor_ref, record in instructor_records.items():
        location = record.get("current_location_id") if isinstance(record, Mapping) else None
        if not isinstance(instructor_ref, str) or not isinstance(location, str) or not location:
            raise OperationError(503, "object_team_invalid")
        instructor_locations[instructor_ref] = location

    member_recovery: dict[str, Mapping[str, Any]] = {}
    aggregate_ready_at = current_time
    all_recovery_ready = True
    for member_ref in members:
        ended = last_end[member_ref]
        if ended is None:
            ready_at = current_time
            last_ended_at = None
        else:
            ready_at = ended.add_seconds(recovery_hours * 3600)
            last_ended_at = str(ended)
        ready_now = ready_at <= current_time
        all_recovery_ready = all_recovery_ready and ready_now
        if ready_at > aggregate_ready_at:
            aggregate_ready_at = ready_at
        member_recovery[member_ref] = {
            "last_session_ended_at": last_ended_at,
            "recovery_ready_at": str(ready_at),
            "recovery_ready_now": ready_now,
        }

    colocated_groups = []
    for location_ref, group_members in sorted(
        location_groups.items(), key=lambda item: tuple(sorted(item[1]))
    ):
        group_members = sorted(group_members)
        colocated_instructors = sorted(
            ref
            for ref, ref_location in instructor_locations.items()
            if ref_location == location_ref
        )
        colocated_groups.append(
            {
                "member_refs": group_members,
                "authorized_instructor_refs_present": colocated_instructors,
            }
        )

    all_colocated = len(location_groups) == 1
    full_team_instructor_present = False
    if all_colocated:
        only_location = next(iter(location_groups))
        full_team_instructor_present = any(
            location == only_location for location in instructor_locations.values()
        )

    latest_summary = None
    if latest_session is not None:
        raw = latest_session[1]
        latest_summary = {
            key: raw.get(key)
            for key in (
                "started_at",
                "ended_at",
                "active_hours",
                "member_refs",
                "instructor_ref",
                "targets",
            )
            if key in raw
        }

    return {
        "schedule_limits": {
            "cycle_length_days": cycle_days,
            "maximum_hours_per_member_per_week": format(weekly_limit.normalize(), "f"),
            "minimum_recovery_hours": recovery_hours,
        },
        "member_recovery": member_recovery,
        "next_recovery_eligible_at_for_all_members": str(aggregate_ready_at),
        "all_members_recovery_ready_now": all_recovery_ready,
        "all_members_colocated_now": all_colocated,
        "full_team_authorized_instructor_colocated_now": full_team_instructor_present,
        "can_start_full_team_session_now": (
            all_recovery_ready and all_colocated and full_team_instructor_present
        ),
        "colocated_member_groups": colocated_groups,
        "latest_resolved_session": latest_summary,
    }


class RouteAwareCampaignOperations(_BaseRouteAwareCampaignOperations):
    """Route-aware operations plus exact-team schedule/co-location readiness."""

    def _team_training_interface(self, team: Mapping[str, Any]) -> Mapping[str, Any]:
        base = dict(super()._team_training_interface(team))
        members = team.get("member_refs")
        training = team.get("training")
        if not isinstance(members, list) or not isinstance(training, Mapping):
            raise OperationError(503, "object_team_invalid")

        meta = self.repository.read_json(self.coordinator.meta_path)
        model_ref = training.get("model_ref")
        current_raw = meta.get("time") if isinstance(meta, Mapping) else None
        if not isinstance(model_ref, str) or not isinstance(current_raw, str):
            raise OperationError(503, "object_team_invalid")
        try:
            current_time = CampaignTime.parse(current_raw)
            registry = self.repository.read_json(TRAINING_MODELS_PATH)
        except (FileNotFoundError, TypeError, ValueError) as exc:
            raise OperationError(503, "object_team_invalid") from exc
        models = registry.get("models") if isinstance(registry, Mapping) else None
        model = models.get(model_ref) if isinstance(models, Mapping) else None
        if not isinstance(model, Mapping):
            raise OperationError(503, "object_team_invalid")

        member_records: dict[str, Mapping[str, Any]] = {}
        for member_ref in members:
            _path, record = self._owner_record(member_ref)
            member_records[member_ref] = record

        instructor_refs = training.get("instructor_refs", [])
        if not isinstance(instructor_refs, list) or any(
            not isinstance(ref, str) or not ref for ref in instructor_refs
        ):
            raise OperationError(503, "object_team_invalid")
        instructor_records: dict[str, Mapping[str, Any]] = {}
        for instructor_ref in instructor_refs:
            if instructor_ref in member_records:
                instructor_records[instructor_ref] = member_records[instructor_ref]
            else:
                _path, record = self._owner_record(instructor_ref)
                instructor_records[instructor_ref] = record

        base["readiness"] = project_team_training_readiness(
            team,
            member_records,
            instructor_records,
            model,
            current_time=current_time,
        )
        return base


__all__ = ["RouteAwareCampaignOperations", "project_team_training_readiness"]
