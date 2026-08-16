"""One-shot repair for the duplicated July Black Hound player mission offer."""
from __future__ import annotations

import copy
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _exact_payload, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.mission_assignment_requests import MISSION_ASSIGNMENT_REQUEST_PATH
from shinobi_runtime.commands.paths import INVENTORY_REGISTRY_PATH, WORLD_EVENT_REGISTRY_PATH
from shinobi_runtime.commands.mission_owner import MissionOwner, mission_owner_path
from shinobi_runtime.commands.player_mission_continuity import mission_assignment_signature
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.reducers.missions import settle_mission, transition_mission
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_REPAIR_ID = "repair.blackhound_player_mission_continuity.2026-08-16"
_BAD_MISSION = "mission.offer.b42cba36bd4b0f447a"
_PRIOR_ESCORT = "mission.offer.0a7361790026211550"
_PRIOR_INVESTIGATION = "mission.offer.2132adf0c1e8a6f134"
_REQUEST_REF = "mission_assignment_request.0252a597b5ce5b19955f3f2e"
_TEAM_REF = "team.blackhound"
_HISTORY_PATH = "state/team/history/team.blackhound.json"
_BAD_PRESSURE = "A new mission offer from the Mission Office is awaiting review."
_BAD_REPORT = "The Mission Office has new operational tasking available for review."
_INSTALLED = False


def _owner(repository: Any, mission_ref: str) -> tuple[str, MissionOwner]:
    path = mission_owner_path(mission_ref)
    try:
        return path, MissionOwner.from_record(repository.read_json(path))
    except (FileNotFoundError, TypeError, ValueError) as exc:
        raise CommandRejectedError("campaign_mission_continuity_repair_source_invalid") from exc


def _signature(owner: MissionOwner) -> tuple[object, ...]:
    if owner.briefing is None:
        raise CommandRejectedError("campaign_mission_continuity_repair_source_invalid")
    return mission_assignment_signature(
        owner.briefing.objective_kind,
        owner.briefing.to_record(),
    )


def _repair(
    self: Any,
    command: CommandEnvelope,
    meta: Mapping[str, Any],
    current_time: CampaignTime,
) -> _BuiltPlan:
    _exact_payload(command.payload, ("repair_id",), "campaign_mission_continuity_repair")
    if command.payload["repair_id"] != _REPAIR_ID:
        raise CommandRejectedError("campaign_mission_continuity_repair_id_invalid")
    if command.actor_id != meta.get("player_id") or command.actor_id != "pc_wei_tang":
        raise CommandRejectedError("campaign_mission_continuity_repair_actor_invalid")
    if str(current_time) != "SE-0061-07-02T07:00:00":
        raise CommandRejectedError("campaign_mission_continuity_repair_time_invalid")

    bad_path, bad = _owner(self.repository, _BAD_MISSION)
    escort_path, escort = _owner(self.repository, _PRIOR_ESCORT)
    investigation_path, investigation = _owner(self.repository, _PRIOR_INVESTIGATION)
    if (
        bad.operation_ref != _TEAM_REF
        or bad.mission.state != "offered"
        or escort.operation_ref != _TEAM_REF
        or escort.mission.state != "succeeded"
        or investigation.operation_ref != _TEAM_REF
        or investigation.mission.state != "succeeded"
        or escort.closed_at is None
        or investigation.closed_at is None
        or investigation.closed_at <= escort.closed_at
        or _signature(bad) != _signature(escort)
        or _signature(investigation) == _signature(escort)
    ):
        raise CommandRejectedError("campaign_mission_continuity_repair_guard_failed")

    try:
        requests = copy.deepcopy(self.repository.read_json(MISSION_ASSIGNMENT_REQUEST_PATH))
        history = copy.deepcopy(self.repository.read_json(_HISTORY_PATH))
        scene = copy.deepcopy(self.repository.read_json(self.scene_path))
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("campaign_mission_continuity_repair_source_invalid") from exc

    rows = requests.get("requests") if isinstance(requests, dict) else None
    if not isinstance(rows, list):
        raise CommandRejectedError("campaign_mission_continuity_repair_request_invalid")
    matches = [row for row in rows if isinstance(row, dict) and row.get("request_ref") == _REQUEST_REF]
    if len(matches) != 1:
        raise CommandRejectedError("campaign_mission_continuity_repair_request_invalid")
    request = matches[0]
    if (
        request.get("team_ref") != _TEAM_REF
        or request.get("requester_ref") != command.actor_id
        or request.get("status") != "fulfilled"
        or request.get("offer_ref") != _BAD_MISSION
    ):
        raise CommandRejectedError("campaign_mission_continuity_repair_request_invalid")

    if (
        not isinstance(history, dict)
        or history.get("schema") != "team-operational-history"
        or history.get("team_id") != _TEAM_REF
        or history.get("missions_total") != 0
        or history.get("missions_succeeded") != 0
        or history.get("missions_failed") != 0
        or history.get("last_mission_ref") is not None
    ):
        raise CommandRejectedError("campaign_mission_continuity_repair_history_guard_failed")

    if (
        not isinstance(scene, dict)
        or scene.get("schema") != "scene"
        or scene.get("world_time") != str(current_time)
    ):
        raise CommandRejectedError("campaign_scene_invalid")

    try:
        invalidated = transition_mission(
            bad.mission,
            "expired",
            reason_ref="repair.duplicate_player_offer_invalidated",
        )
        settled = settle_mission(invalidated, _REPAIR_ID)
        bad_after = bad.with_mission(settled.mission, effective_at=current_time)
    except (TypeError, ValueError) as exc:
        raise CommandRejectedError("campaign_mission_continuity_repair_transition_invalid") from exc

    inventory, inventory_consequences = self._mission_settlement_inventory(bad_after)
    if inventory is None:
        raise CommandRejectedError("campaign_mission_continuity_repair_escrow_invalid")
    if not any(item.startswith("mission_escrow_refund:currency.ryo:") for item in inventory_consequences):
        raise CommandRejectedError("campaign_mission_continuity_repair_escrow_invalid")

    request["status"] = "pending"
    request.pop("fulfilled_at", None)
    request.pop("offer_ref", None)

    history["missions_total"] = 2
    history["missions_succeeded"] = 2
    history["missions_failed"] = 0
    history["recent_mission_refs"] = [_PRIOR_ESCORT, _PRIOR_INVESTIGATION]
    history["last_mission_ref"] = _PRIOR_INVESTIGATION
    history["last_result_at"] = str(investigation.closed_at)
    history["as_of"] = str(current_time)

    loaded = scene.get("loaded_owner_ids")
    if not isinstance(loaded, list):
        raise CommandRejectedError("campaign_scene_invalid")
    scene["loaded_owner_ids"] = [ref for ref in loaded if ref != _BAD_MISSION]
    pressures = scene.get("observable_pressures")
    if isinstance(pressures, list):
        scene["observable_pressures"] = [value for value in pressures if value != _BAD_PRESSURE]
    narrative = scene.get("narrative")
    if isinstance(narrative, dict):
        reports = narrative.get("available_reports")
        if isinstance(reports, list):
            narrative["available_reports"] = [value for value in reports if value != _BAD_REPORT]

    scheduler = self._load_scheduler(
        current_time=current_time,
        scene=self._scene_base(current_time),
    )
    self._sync_mission_scheduler(
        scheduler,
        owner=bad_after,
        path=bad_path,
        current_time=current_time,
    )

    world_events = self._world_events()
    event_id = self._append_semantic_event(
        world_events,
        command=command,
        kind="campaign_repair_applied",
        at=current_time,
        host_refs=(_TEAM_REF, bad.issuer_ref),
        actor_refs=(command.actor_id,),
        causal_refs=(_BAD_MISSION, _PRIOR_ESCORT, _PRIOR_INVESTIGATION),
        affected_owner_refs=(
            bad_path,
            MISSION_ASSIGNMENT_REQUEST_PATH,
            _HISTORY_PATH,
            INVENTORY_REGISTRY_PATH,
            self.scene_path,
            self.scheduler_path,
        ),
        material_consequence_refs=(
            f"duplicate_offer_invalidated:{_BAD_MISSION}",
            f"assignment_request_reopened:{_REQUEST_REF}",
            f"team_history_backfilled:{_TEAM_REF}:2_succeeded",
            *inventory_consequences,
        ),
        classification="restricted",
        audience_refs=(command.actor_id,),
        source_refs=(_BAD_MISSION, _PRIOR_ESCORT, _PRIOR_INVESTIGATION),
        reducer_ref="shinobi_runtime.commands.campaign_mission_continuity_repair",
    )

    writes = {
        self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
        self.scene_path: _json_bytes(scene),
        bad_path: _json_bytes(bad_after.to_record()),
        MISSION_ASSIGNMENT_REQUEST_PATH: _json_bytes(requests),
        _HISTORY_PATH: _json_bytes(history),
        INVENTORY_REGISTRY_PATH: _json_bytes(inventory),
        **self._scheduler_write_images(scheduler),
        **self._world_event_writes(world_events),
    }
    writes = self._prune_noop_writes(writes)
    expected_paths = tuple(sorted(writes))
    expected_bad = bad_after.to_record()

    def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
        if overlay.changed_paths != expected_paths:
            raise ValueError("campaign mission continuity repair write set changed after planning")
        self._assert_meta(
            overlay,
            manifest,
            meta_path=self.meta_path,
            command=command,
            world_time=current_time,
        )
        if MissionOwner.from_record(overlay.read_json(bad_path)).to_record() != expected_bad:
            raise ValueError("campaign mission continuity repair mission after-image changed")
        if overlay.read_json(MISSION_ASSIGNMENT_REQUEST_PATH) != requests:
            raise ValueError("campaign mission continuity repair request after-image changed")
        if overlay.read_json(_HISTORY_PATH) != history:
            raise ValueError("campaign mission continuity repair history after-image changed")
        if overlay.read_json(INVENTORY_REGISTRY_PATH) != inventory:
            raise ValueError("campaign mission continuity repair inventory after-image changed")
        self._scheduler_from_reader(overlay)
        staged_events = overlay.read_json(WORLD_EVENT_REGISTRY_PATH).get("events", [])
        if not any(
            isinstance(item, Mapping) and item.get("id") == event_id
            for item in staged_events
        ):
            raise ValueError("campaign mission continuity repair semantic event missing")

    return _BuiltPlan(
        code="campaign_mission_continuity_repair_ready",
        affected_refs=expected_paths,
        writes=writes,
        result={
            "command_type": command.command_type,
            "repair_id": _REPAIR_ID,
            "invalidated_mission_ref": _BAD_MISSION,
            "restored_assignment_request_ref": _REQUEST_REF,
            "backfilled_team_ref": _TEAM_REF,
            "world_time": str(current_time),
            "status": "repaired",
        },
        validator=validate,
    )


def install_campaign_mission_continuity_repair() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_environment as module

    COMMAND_SPECS.setdefault(
        "campaign_mission_continuity_repair",
        CommandSpec(
            ("repair_id",),
            (),
            "Apply the guarded one-shot Black Hound duplicate-mission continuity repair.",
            {"repair_id": _REPAIR_ID},
            availability="ooc_dev_guarded_repair_only",
        ),
    )
    planner = module.CampaignCommandPlanner
    setattr(planner, "_campaign_mission_continuity_repair", _repair)
    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)
    _INSTALLED = True


__all__ = ["install_campaign_mission_continuity_repair"]
