"""Guarded repair for a stale player mission boundary after lawful delegation.

The repair changes only scheduler/scene routing. It never changes the mission,
participants, objective progress, rewards, injuries, travel, or campaign time.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import (
    _BuiltPlan,
    _OwnerResolutionCache,
    _exact_payload,
    _json_bytes,
    _stable_id,
)
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.mission_owner import MissionOwner, mission_owner_path
from shinobi_runtime.commands.paths import WORLD_EVENT_REGISTRY_PATH
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_COMMAND = "campaign_mission_boundary_repair"
_INSTALLED = False


def _faction_autonomy_guard(
    self: Any,
    *,
    faction_ref: str,
    mission_id: str,
) -> tuple[str, str]:
    try:
        path, digest, _view = self._resolve_covered_owner_view(
            faction_ref,
            cache=_OwnerResolutionCache(),
        )
        record = self.repository.read_json(path)
    except (CommandRejectedError, FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("mission_boundary_repair_faction_invalid") from exc
    faction = record.get("faction") if isinstance(record, Mapping) else None
    plan_state = faction.get("plan_state") if isinstance(faction, Mapping) else None
    autonomous = plan_state.get("autonomous_mission_refs") if isinstance(plan_state, Mapping) else None
    wake = plan_state.get("wake_required_mission_refs") if isinstance(plan_state, Mapping) else None
    if (
        not isinstance(autonomous, list)
        or not isinstance(wake, list)
        or mission_id not in autonomous
        or mission_id in wake
    ):
        raise CommandRejectedError("mission_boundary_repair_autonomy_route_invalid")
    return path, digest


def _repair(
    self: Any,
    command: CommandEnvelope,
    meta: Mapping[str, Any],
    current_time: CampaignTime,
) -> _BuiltPlan:
    _exact_payload(command.payload, ("mission_id",), command.command_type)
    mission_id = _stable_id(
        command.payload.get("mission_id"),
        "mission_boundary_repair_mission_id_invalid",
        prefix="mission.",
    )
    player_id = meta.get("player_id")
    if not isinstance(player_id, str) or command.actor_id != player_id:
        raise CommandRejectedError("mission_boundary_repair_actor_invalid")

    mission_path = mission_owner_path(mission_id)
    try:
        mission_digest = self.repository.digest(mission_path)
        owner = MissionOwner.from_record(self.repository.read_json(mission_path))
    except (FileNotFoundError, TypeError, ValueError) as exc:
        raise CommandRejectedError("mission_boundary_repair_mission_invalid") from exc
    if owner.mission.state in ("succeeded", "failed", "aborted", "expired"):
        raise CommandRejectedError("mission_boundary_repair_mission_terminal")
    if player_id in owner.mission.participant_refs:
        raise CommandRejectedError("mission_boundary_repair_player_still_participant")

    team_ref = owner.operation_ref
    if not isinstance(team_ref, str) or not team_ref.startswith("team."):
        raise CommandRejectedError("mission_boundary_repair_team_invalid")
    try:
        team_path, team = self._exact_team(team_ref)
        team_digest = self.repository.digest(team_path)
    except CommandRejectedError as exc:
        raise CommandRejectedError("mission_boundary_repair_team_invalid") from exc
    members = team.get("member_refs") if isinstance(team, Mapping) else None
    if (
        team.get("status") != "active"
        or team.get("leader_ref") != player_id
        or not isinstance(members, list)
        or player_id not in members
    ):
        raise CommandRejectedError("mission_boundary_repair_team_not_player_led")

    faction_path, faction_digest = _faction_autonomy_guard(
        self,
        faction_ref=owner.issuer_ref,
        mission_id=mission_id,
    )

    try:
        scene = copy.deepcopy(self.repository.read_json(self.scene_path))
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("campaign_scene_invalid") from exc
    decision = scene.get("decision_required") if isinstance(scene, Mapping) else None
    if (
        not isinstance(scene, dict)
        or scene.get("world_time") != str(current_time)
        or scene.get("time_passage_allowed") is not False
        or not isinstance(decision, str)
        or mission_id not in decision
    ):
        raise CommandRejectedError("mission_boundary_repair_scene_guard_failed")

    scheduler = self._load_scheduler(
        current_time=current_time,
        scene=self._scene_base(current_time),
    )
    host_id = "host." + mission_id
    stale = [
        event
        for event in scheduler.queue.snapshot()
        if event.target_host == host_id
        and event.kind == "mission.boundary"
        and event.payload.get("mission_id") == mission_id
        and event.requires_player is True
        and event.due_at <= current_time
    ]
    if len(stale) != 1 or host_id not in scheduler.hosts:
        raise CommandRejectedError("mission_boundary_repair_scheduler_guard_failed")

    self._sync_mission_scheduler(
        scheduler,
        owner=owner,
        path=mission_path,
        current_time=current_time,
    )
    if host_id in scheduler.hosts or any(
        event.target_host == host_id for event in scheduler.queue.snapshot()
    ):
        raise CommandRejectedError("mission_boundary_repair_scheduler_not_cleared")

    location = scene.get("location_id")
    if not isinstance(location, str) or not location:
        raise CommandRejectedError("campaign_scene_invalid")
    scene["decision_required"] = None
    scene["time_passage_allowed"] = True
    scene["scene_summary"] = (
        f"Time remains at {current_time} at {location}; no player decision boundary is pending."
    )

    world_events = self._world_events()
    event_id = self._append_semantic_event(
        world_events,
        command=command,
        kind="campaign_repair_applied",
        at=current_time,
        host_refs=(team_ref, owner.issuer_ref),
        actor_refs=(player_id,),
        causal_refs=(mission_id,),
        affected_owner_refs=(self.scene_path, self.scheduler_path),
        material_consequence_refs=(
            f"stale_player_mission_boundary_removed:{mission_id}",
            f"mission_autonomy_route_preserved:{mission_id}",
        ),
        classification="restricted",
        audience_refs=(player_id,),
        source_refs=(mission_id, team_ref, owner.issuer_ref),
        reducer_ref="shinobi_runtime.commands.campaign_mission_boundary_repair",
    )

    writes = {
        self.meta_path: _json_bytes(
            self._meta_after(meta, command, world_time=current_time)
        ),
        self.scene_path: _json_bytes(scene),
        **self._scheduler_write_images(scheduler),
        **self._world_event_writes(world_events),
    }
    writes = self._prune_noop_writes(writes)
    expected_paths = tuple(sorted(writes))
    guarded = {
        mission_path: mission_digest,
        team_path: team_digest,
        faction_path: faction_digest,
    }

    def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
        if overlay.changed_paths != expected_paths:
            raise ValueError("mission boundary repair write set changed after planning")
        for path, digest in guarded.items():
            if self.repository.digest(path) != digest:
                raise ValueError("mission boundary repair causal source changed")
        self._assert_meta(
            overlay,
            manifest,
            meta_path=self.meta_path,
            command=command,
            world_time=current_time,
        )
        staged_scene = overlay.read_json(self.scene_path)
        if (
            staged_scene.get("decision_required") is not None
            or staged_scene.get("time_passage_allowed") is not True
        ):
            raise ValueError("mission boundary repair scene after-image invalid")
        staged_scheduler = self._scheduler_from_reader(overlay)
        if host_id in staged_scheduler.hosts or any(
            event.target_host == host_id
            for event in staged_scheduler.queue.snapshot()
        ):
            raise ValueError("mission boundary repair scheduler after-image invalid")
        staged_events = overlay.read_json(WORLD_EVENT_REGISTRY_PATH).get("events", [])
        if not any(
            isinstance(item, Mapping) and item.get("id") == event_id
            for item in staged_events
        ):
            raise ValueError("mission boundary repair semantic event missing")

    return _BuiltPlan(
        code="campaign_mission_boundary_repair_ready",
        affected_refs=expected_paths,
        writes=writes,
        result={
            "command_type": command.command_type,
            "mission_id": mission_id,
            "team_ref": team_ref,
            "world_time": str(current_time),
            "status": "repaired",
        },
        validator=validate,
    )


def install_campaign_mission_boundary_repair() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_environment as module

    COMMAND_SPECS.setdefault(
        _COMMAND,
        CommandSpec(
            ("mission_id",),
            (),
            (
                "Remove one guarded stale player mission boundary after the mission "
                "has already been delegated into lawful faction autonomy."
            ),
            {"mission_id": "mission.<id>"},
            availability="ooc_dev_guarded_repair_only",
        ),
    )
    planner = module.CampaignCommandPlanner
    setattr(planner, "_" + _COMMAND, _repair)
    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)
    _INSTALLED = True


__all__ = ["install_campaign_mission_boundary_repair"]
