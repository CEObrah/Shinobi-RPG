"""Cross-team exact training load derived from persisted team session ledgers.

Exact-team training.recent_sessions remain the sole session authority. This
module aggregates those ledgers by person so multiple team memberships cannot
reset recovery or weekly-hour limits. During one autonomous time transaction,
staged team after-images are included so two reviews in the same transaction
cannot double-book the same person.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import timedelta
from decimal import Decimal
from functools import wraps
from typing import Any, Mapping, MutableMapping, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _campaign_datetime
from shinobi_runtime.sim.events import CampaignTime

_TEAM_INDEX = "state/index/owners/team.json"
_TRAINING_MODELS = "game/rules/training/models.json"
_STAGED_TEAM_WRITES: ContextVar[Mapping[str, Mapping[str, Any]] | None] = ContextVar(
    "global_team_training_staged_writes", default=None
)
_INSTALLED = False


def _schedule_limits(repository: Any) -> tuple[int, Decimal, int]:
    try:
        registry = repository.read_json(_TRAINING_MODELS)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("training_model_registry_invalid") from exc
    models = registry.get("models") if isinstance(registry, Mapping) else None
    model = models.get("training.team") if isinstance(models, Mapping) else None
    schedule = model.get("schedule_limits") if isinstance(model, Mapping) else None
    if not isinstance(schedule, Mapping):
        raise CommandRejectedError("training_model_registry_invalid")
    cycle_days = schedule.get("cycle_length_days")
    recovery_hours = schedule.get("minimum_recovery_hours")
    try:
        weekly_limit = Decimal(str(schedule.get("maximum_hours_per_member_per_week")))
    except Exception as exc:
        raise CommandRejectedError("training_model_registry_invalid") from exc
    if (
        isinstance(cycle_days, bool)
        or not isinstance(cycle_days, int)
        or cycle_days <= 0
        or isinstance(recovery_hours, bool)
        or not isinstance(recovery_hours, int)
        or recovery_hours < 0
        or not weekly_limit.is_finite()
        or weekly_limit <= 0
    ):
        raise CommandRejectedError("training_model_registry_invalid")
    return cycle_days, weekly_limit, recovery_hours


def _team_records(
    repository: Any,
    *,
    record_writes: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    try:
        index = repository.read_json(_TEAM_INDEX)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("team_owner_index_invalid") from exc
    owners = index.get("owners") if isinstance(index, Mapping) else None
    if not isinstance(owners, Mapping):
        raise CommandRejectedError("team_owner_index_invalid")
    records: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for owner_ref, path in sorted(owners.items()):
        if not isinstance(owner_ref, str) or not owner_ref.startswith("team."):
            continue
        if not isinstance(path, str) or path in seen:
            continue
        staged = record_writes.get(path) if isinstance(record_writes, Mapping) else None
        if isinstance(staged, Mapping):
            record = staged
        else:
            try:
                record = repository.read_json(path)
            except (FileNotFoundError, ValueError):
                continue
        if isinstance(record, Mapping) and record.get("schema") == "exact-team":
            records.append(record)
            seen.add(path)
    return tuple(records)


def member_team_training_sessions(
    repository: Any,
    member_ref: str,
    *,
    record_writes: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    by_session: dict[str, Mapping[str, Any]] = {}
    for team in _team_records(repository, record_writes=record_writes):
        roster = team.get("member_refs")
        if not isinstance(roster, list) or member_ref not in roster:
            continue
        if any(not isinstance(ref, str) or not ref for ref in roster):
            raise CommandRejectedError("team_training_history_invalid")
        training = team.get("training")
        recent = training.get("recent_sessions") if isinstance(training, Mapping) else None
        if recent is None:
            continue
        if not isinstance(recent, list):
            raise CommandRejectedError("team_training_history_invalid")
        for row in recent:
            if not isinstance(row, Mapping):
                raise CommandRejectedError("team_training_history_invalid")
            members = row.get("member_refs")
            session_ref = row.get("session_ref")
            if not isinstance(members, list) or not isinstance(session_ref, str) or not session_ref:
                raise CommandRejectedError("team_training_history_invalid")
            if member_ref not in members:
                continue
            existing = by_session.get(session_ref)
            if existing is not None and dict(existing) != dict(row):
                raise CommandRejectedError("team_training_session_identity_conflict")
            by_session[session_ref] = row
    return tuple(by_session[key] for key in sorted(by_session))


def member_team_training_load(
    repository: Any,
    member_ref: str,
    *,
    as_of: CampaignTime,
    record_writes: Mapping[str, Mapping[str, Any]] | None = None,
) -> Mapping[str, Any]:
    cycle_days, weekly_limit, recovery_hours = _schedule_limits(repository)
    as_of_dt = _campaign_datetime(as_of)
    weekly_cutoff = as_of_dt - timedelta(days=cycle_days)
    used = Decimal("0")
    last_end: CampaignTime | None = None
    for row in member_team_training_sessions(
        repository, member_ref, record_writes=record_writes
    ):
        try:
            ended_at = CampaignTime.parse(row.get("ended_at"))
            ended_dt = _campaign_datetime(ended_at)
            active_hours = Decimal(str(row.get("active_hours")))
        except Exception as exc:
            raise CommandRejectedError("team_training_history_invalid") from exc
        if not active_hours.is_finite() or active_hours <= 0:
            raise CommandRejectedError("team_training_history_invalid")
        if ended_dt > as_of_dt:
            continue
        if ended_dt > weekly_cutoff:
            used += active_hours
        if last_end is None or ended_dt > _campaign_datetime(last_end):
            last_end = ended_at
    ready_at = as_of if last_end is None else last_end.add_seconds(recovery_hours * 3600)
    return {
        "weekly_hours_used": used,
        "weekly_hours_remaining": max(Decimal("0"), weekly_limit - used),
        "weekly_limit": weekly_limit,
        "last_session_ended_at": last_end,
        "recovery_ready_at": ready_at,
        "recovery_ready_now": ready_at <= as_of,
        "cycle_days": cycle_days,
        "recovery_hours": recovery_hours,
    }


def assert_global_team_training_load(
    repository: Any,
    member_refs: Sequence[str],
    *,
    started_at: CampaignTime,
    ended_at: CampaignTime,
    active_hours: Decimal,
    record_writes: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    if not active_hours.is_finite() or active_hours <= 0:
        raise CommandRejectedError("training_active_hours_invalid")
    started_dt = _campaign_datetime(started_at)
    for member_ref in member_refs:
        load = member_team_training_load(
            repository,
            member_ref,
            as_of=ended_at,
            record_writes=record_writes,
        )
        if load["weekly_hours_used"] + active_hours > load["weekly_limit"]:
            raise CommandRejectedError("team_training_weekly_limit_exceeded")
        previous = load["last_session_ended_at"]
        if previous is not None:
            recovery = Decimal(str((started_dt - _campaign_datetime(previous)).total_seconds())) / Decimal(3600)
            if recovery < Decimal(load["recovery_hours"]):
                raise CommandRejectedError("team_training_recovery_required")


def install_global_team_training_load() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_environment as module

    planner = module.CampaignCommandPlanner
    original_record = planner._record_team_training_session
    if not getattr(original_record, "_global_team_training_load", False):
        @wraps(original_record)
        def record_wrapped(
            self: Any,
            team: MutableMapping[str, Any],
            *,
            session_ref: str,
            member_targets: Mapping[str, str],
            instructor_ref: str,
            started_at: CampaignTime,
            ended_at: CampaignTime,
            active_hours: Decimal,
            member_target_hours: Mapping[str, Mapping[str, Decimal]] | None = None,
        ) -> None:
            assert_global_team_training_load(
                self.repository,
                tuple(sorted(member_targets)),
                started_at=started_at,
                ended_at=ended_at,
                active_hours=active_hours,
                record_writes=_STAGED_TEAM_WRITES.get(),
            )
            original_record(
                self,
                team,
                session_ref=session_ref,
                member_targets=member_targets,
                instructor_ref=instructor_ref,
                started_at=started_at,
                ended_at=ended_at,
                active_hours=active_hours,
                member_target_hours=member_target_hours,
            )

        record_wrapped._global_team_training_load = True  # type: ignore[attr-defined]
        planner._record_team_training_session = record_wrapped

    original_autonomy = planner._apply_autonomous_team_training
    if not getattr(original_autonomy, "_global_team_training_stage_scope", False):
        @wraps(original_autonomy)
        def autonomy_wrapped(self: Any, *args: Any, **kwargs: Any):
            record_writes = kwargs.get("record_writes")
            token = _STAGED_TEAM_WRITES.set(
                record_writes if isinstance(record_writes, Mapping) else None
            )
            try:
                return original_autonomy(self, *args, **kwargs)
            finally:
                _STAGED_TEAM_WRITES.reset(token)

        autonomy_wrapped._global_team_training_stage_scope = True  # type: ignore[attr-defined]
        planner._apply_autonomous_team_training = autonomy_wrapped

    _INSTALLED = True


__all__ = [
    "assert_global_team_training_load",
    "install_global_team_training_load",
    "member_team_training_load",
    "member_team_training_sessions",
]
