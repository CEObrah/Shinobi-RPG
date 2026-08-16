"""Keep mission scheduler wakes aligned with actual player participation.

The causal scheduler treats ``mission.boundary`` as an interrupt-only event.
Faction-autonomous missions therefore must not retain a player mission wake once
the campaign player is no longer a mission participant. Delegation already
routes those missions through faction autonomy; this extension removes the stale
scheduler mirror while preserving player-participating mission boundaries.
"""
from __future__ import annotations

from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.constants import TERMINAL_MISSION_STATES
from shinobi_runtime.commands.mission_owner import MissionOwner
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.hosts import HostState
from shinobi_runtime.sim.scheduler import (
    CausalSchedulerRegistry,
    SchedulerHost,
    one_shot_event,
)

_INSTALLED = False


def _campaign_player_id(planner: Any) -> str:
    try:
        meta = planner.repository.read_json(planner.meta_path)
    except (FileNotFoundError, ValueError, TypeError) as exc:
        raise CommandRejectedError("campaign_meta_invalid") from exc
    player_id = meta.get("player_id") if isinstance(meta, Mapping) else None
    if not isinstance(player_id, str) or not player_id:
        raise CommandRejectedError("campaign_meta_invalid")
    return player_id


def _sync_mission_scheduler(
    self: Any,
    scheduler: CausalSchedulerRegistry,
    *,
    owner: MissionOwner,
    path: str,
    current_time: CampaignTime,
) -> None:
    """Mirror only mission boundaries that can actually require this player."""

    host_id = "host." + owner.mission_id
    scheduler.queue.replace(
        event
        for event in scheduler.queue.snapshot()
        if event.target_host != host_id
    )
    scheduler.hosts.pop(host_id, None)

    if owner.mission.state in TERMINAL_MISSION_STATES:
        scheduler.metrics.update(
            {
                "host_count": len(scheduler.hosts),
                "pending_event_count": len(scheduler.queue),
            }
        )
        return

    player_id = _campaign_player_id(self)
    if player_id not in owner.mission.participant_refs:
        # Non-player missions are advanced by their owning faction/autonomy
        # lifecycle. ``mission.boundary`` has no non-player fact handler and
        # must not remain as a false player interrupt.
        scheduler.metrics.update(
            {
                "host_count": len(scheduler.hosts),
                "pending_event_count": len(scheduler.queue),
            }
        )
        return

    due_candidates = [
        value
        for value in (owner.next_due_at, owner.deadline_at)
        if value is not None and value > current_time
    ]
    if due_candidates:
        due = min(due_candidates)
        scheduler.add_host(
            SchedulerHost(
                state=HostState(
                    host_id=host_id,
                    kind="mission",
                    resolved_through=current_time,
                    safe_through=due.add_seconds(-1),
                    handler_ref="causal.scheduler",
                    rng_namespace=owner.mission_id,
                    next_due=None,
                ),
                authority_kind="mission",
                owner_ref=path,
                metadata={"mission_id": owner.mission_id},
            )
        )
        scheduler.upsert_event(
            one_shot_event(
                kind="mission.boundary",
                identity=owner.mission_id,
                source_host=host_id,
                target_host=host_id,
                due_at=due,
                payload={"mission_id": owner.mission_id, "owner_ref": path},
                priority=20,
                visibility="player_known",
                requires_player=True,
            )
        )

    scheduler.metrics.update(
        {
            "host_count": len(scheduler.hosts),
            "pending_event_count": len(scheduler.queue),
        }
    )


def _patch_planner(planner: type) -> None:
    setattr(planner, "_sync_mission_scheduler", _sync_mission_scheduler)


def install_mission_boundary_integrity() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from shinobi_runtime.commands.domains.missions import MissionCommandsMixin
    from shinobi_runtime.commands.planner import RepositoryCommandPlanner

    _patch_planner(MissionCommandsMixin)
    _patch_planner(RepositoryCommandPlanner)

    try:
        from shinobi_runtime.commands import campaign_environment as module

        _patch_planner(module.CampaignCommandPlanner)
    except ImportError:
        pass
    try:
        from shinobi_runtime.commands import campaign_mission_assignment as module

        _patch_planner(module.CampaignCommandPlanner)
    except ImportError:
        pass
    try:
        from shinobi_runtime.commands import campaign_player_handoffs as module

        _patch_planner(module.CampaignCommandPlanner)
    except ImportError:
        pass

    _INSTALLED = True


__all__ = [
    "install_mission_boundary_integrity",
    "_campaign_player_id",
    "_sync_mission_scheduler",
]
