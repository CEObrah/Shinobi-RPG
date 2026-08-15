"""Durable player-facing report handoffs and lawful remote mission availability.

This final campaign planner layer keeps two player-facing workflows causal:
handled reports remain readable history without repeating as fresh interruptions,
and an exact-team leader may register a mission-assignment preference remotely
only from an authored secure-communications site in the same country as the
assignment desk. Neither workflow creates a mission, offer, response, or outcome.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.campaign_mission_assignment import CampaignCommandPlanner as _Base
from shinobi_runtime.commands.core import _BuiltPlan, _declared_payload, _exact_payload, _json_bytes, _stable_id
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.mission_assignment_requests import (
    MISSION_ASSIGNMENT_DESK_REF,
    MISSION_ASSIGNMENT_OFFICE_REF,
    MISSION_ASSIGNMENT_REQUEST_PATH,
    MISSION_FOCI,
    assignment_request_ref,
    load_assignment_request_registry,
    normalize_acceptable_ranks,
    pending_assignment_request,
)
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.information import InformationStore
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_SITE_DEFINITIONS_PATH = "game/data/content/strategic-site-definitions.json"
_WORLD_PLACES_PATH = "state/world/routes-and-settlements.json"
_ASSIGNMENT_SUBMISSION_MODES = frozenset(("assignment_desk", "registered_message"))
_REPORT_HANDLINGS = frozenset(("acknowledge", "keep_compartmented"))
_MAX_HANDLED_REPORT_REFS = 64


COMMAND_SPECS["mission_assignment_request_resolution"] = CommandSpec(
    ("team_ref", "acceptable_ranks", "mission_focus"),
    ("submission_mode",),
    "Submit one bounded mission-assignment preference; the assignment authority still derives any actual mission offer from lawful world demand.",
    {
        "team_ref": "team.<id>",
        "acceptable_ranks": "[D,C,B,A,S]",
        "mission_focus": "general|combat",
        "submission_mode": "assignment_desk|registered_message",
    },
)
COMMAND_SPECS.setdefault(
    "report_handoff_resolution",
    CommandSpec(
        ("report_ref", "handling"),
        (),
        "Record that the player has handled one delivered report without changing its informational content.",
        {
            "report_ref": "delivery.<id>",
            "handling": "acknowledge|keep_compartmented",
        },
    ),
)


class CampaignCommandPlanner(_Base):
    """Production planner with durable report handling and lawful remote requests."""

    COMMAND_TYPES = frozenset(COMMAND_SPECS)

    def _place_country(self, place_ref: str) -> str:
        try:
            registry = self.repository.read_json(_WORLD_PLACES_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("mission_assignment_request_place_registry_invalid") from exc
        payload = registry.get("payload") if isinstance(registry, Mapping) else None
        places = payload.get("places") if isinstance(payload, Mapping) else None
        if not isinstance(places, list):
            raise CommandRejectedError("mission_assignment_request_place_registry_invalid")
        matches = [row for row in places if isinstance(row, Mapping) and row.get("id") == place_ref]
        if len(matches) != 1:
            raise CommandRejectedError("mission_assignment_request_place_unresolved")
        country_ref = matches[0].get("country_id")
        if not isinstance(country_ref, str) or not country_ref:
            raise CommandRejectedError("mission_assignment_request_place_registry_invalid")
        return country_ref

    def _site_has_secure_communications(self, place_ref: str) -> bool:
        try:
            catalog = self.repository.read_json(_SITE_DEFINITIONS_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("mission_assignment_request_site_catalog_invalid") from exc
        records = catalog.get("records") if isinstance(catalog, Mapping) else None
        record = records.get(place_ref) if isinstance(records, Mapping) else None
        facilities = record.get("facilities") if isinstance(record, Mapping) else None
        return isinstance(facilities, list) and "secure_communications" in facilities

    def _mission_assignment_request_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _declared_payload(command.payload, command.command_type)
        team_ref = _stable_id(command.payload.get("team_ref"), "mission_assignment_request_team_invalid", prefix="team.")
        ranks = normalize_acceptable_ranks(command.payload.get("acceptable_ranks"))
        focus = command.payload.get("mission_focus")
        if focus not in MISSION_FOCI:
            raise CommandRejectedError("mission_assignment_request_focus_invalid")
        submission_mode = command.payload.get("submission_mode", "assignment_desk")
        if submission_mode not in _ASSIGNMENT_SUBMISSION_MODES:
            raise CommandRejectedError("mission_assignment_request_submission_mode_invalid")

        _team_path, team = self._exact_team(team_ref)
        members = team.get("member_refs") if isinstance(team, Mapping) else None
        authority_ref = team.get("assignment_authority_ref") if isinstance(team, Mapping) else None
        if (
            team.get("status") != "active"
            or not isinstance(members, list)
            or command.actor_id not in members
            or team.get("leader_ref") != command.actor_id
            or team.get("current_assignment_ref") is not None
            or not isinstance(authority_ref, str)
            or not authority_ref
        ):
            raise CommandRejectedError("mission_assignment_request_team_unavailable")

        scene = copy.deepcopy(self._scene_base(current_time))
        current_place = scene.get("location_id")
        if not isinstance(current_place, str) or not current_place:
            raise CommandRejectedError("mission_assignment_request_place_unresolved")
        if submission_mode == "assignment_desk":
            if current_place != MISSION_ASSIGNMENT_DESK_REF:
                raise CommandRejectedError("mission_assignment_request_wrong_place")
        else:
            if not self._site_has_secure_communications(current_place):
                raise CommandRejectedError("mission_assignment_request_secure_channel_unavailable")
            if self._place_country(current_place) != self._place_country(MISSION_ASSIGNMENT_DESK_REF):
                raise CommandRejectedError("mission_assignment_request_remote_channel_out_of_scope")

        registry = load_assignment_request_registry(self.repository)
        if pending_assignment_request(registry, requester_ref=command.actor_id, team_ref=team_ref) is not None:
            raise CommandRejectedError("mission_assignment_request_already_pending")

        request_ref = assignment_request_ref(team_ref, command.actor_id, command.digest)
        requests = registry["requests"]
        if request_ref in requests:
            raise CommandRejectedError("mission_assignment_request_conflict")
        requests[request_ref] = {
            "request_ref": request_ref,
            "team_ref": team_ref,
            "requester_ref": command.actor_id,
            "assignment_authority_ref": authority_ref,
            "assignment_office_ref": MISSION_ASSIGNMENT_OFFICE_REF,
            "acceptable_ranks": list(ranks),
            "mission_focus": focus,
            "submission_mode": submission_mode,
            "submitted_from_place_ref": current_place,
            "status": "pending",
            "submitted_at": str(current_time),
        }

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="mission_assignment_requested",
            at=current_time,
            host_refs=(MISSION_ASSIGNMENT_OFFICE_REF, team_ref),
            actor_refs=(command.actor_id,),
            place_refs=(current_place,),
            causal_refs=(team_ref,),
            affected_owner_refs=(MISSION_ASSIGNMENT_REQUEST_PATH,),
            material_consequence_refs=(request_ref,),
            classification="restricted",
            audience_refs=(command.actor_id, authority_ref),
            source_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.commands.campaign_player_handoffs.mission_assignment_request_resolution",
        )
        if submission_mode == "registered_message":
            scene["scene_summary"] = (
                f"{team_ref} registers a {focus} mission-availability preference for ranks "
                f"{','.join(ranks)} with {MISSION_ASSIGNMENT_OFFICE_REF} by secure communication from {current_place}."
            )
        else:
            scene["scene_summary"] = (
                f"{team_ref} submits a {focus} mission assignment request for ranks "
                f"{','.join(ranks)} at {MISSION_ASSIGNMENT_DESK_REF}."
            )
        scene["decision_required"] = None
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            self.scene_path: _json_bytes(scene),
            MISSION_ASSIGNMENT_REQUEST_PATH: _json_bytes(registry),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("mission assignment request write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged = overlay.read_json(MISSION_ASSIGNMENT_REQUEST_PATH)
            request = staged.get("requests", {}).get(request_ref)
            if (
                not isinstance(request, Mapping)
                or request.get("status") != "pending"
                or tuple(request.get("acceptable_ranks", ())) != ranks
                or request.get("mission_focus") != focus
                or request.get("submission_mode") != submission_mode
                or request.get("submitted_from_place_ref") != current_place
            ):
                raise ValueError("mission assignment request did not persist")

        return _BuiltPlan(
            code="mission_assignment_request_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "request_ref": request_ref,
                "team_ref": team_ref,
                "acceptable_ranks": list(ranks),
                "mission_focus": focus,
                "submission_mode": submission_mode,
                "submitted_from_place_ref": current_place,
                "status": "pending",
                "semantic_event_id": event_id,
            },
            validator=validate,
        )

    def _report_handoff_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(command.payload, ("report_ref", "handling"), command.command_type)
        report_ref = _stable_id(command.payload.get("report_ref"), "report_handoff_report_invalid", prefix="delivery.")
        handling = command.payload.get("handling")
        if handling not in _REPORT_HANDLINGS:
            raise CommandRejectedError("report_handoff_handling_invalid")

        information = InformationStore(self.repository)
        try:
            delivery = information.delivery(report_ref)
        except ValueError as exc:
            raise CommandRejectedError("information_registry_invalid") from exc
        if not isinstance(delivery, Mapping) or delivery.get("recipient_ref") != command.actor_id:
            raise CommandRejectedError("report_handoff_not_player_delivery")
        claim_id = delivery.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id:
            raise CommandRejectedError("information_delivery_invalid")

        scene = copy.deepcopy(self._scene_base(current_time))
        narrative = scene.get("narrative")
        if not isinstance(narrative, dict):
            raise CommandRejectedError("campaign_scene_invalid")
        raw_handled = narrative.get("handled_report_refs", [])
        if not isinstance(raw_handled, list) or any(not isinstance(value, str) or not value for value in raw_handled):
            raise CommandRejectedError("campaign_scene_invalid")
        if report_ref in raw_handled:
            raise CommandRejectedError("report_handoff_already_handled")
        handled = [*raw_handled, report_ref][-_MAX_HANDLED_REPORT_REFS:]
        narrative["handled_report_refs"] = handled
        if handling == "keep_compartmented":
            scene["scene_summary"] = f"Wei handles {report_ref} and keeps its contents compartmented."
        else:
            scene["scene_summary"] = f"Wei acknowledges {report_ref} as handled."
        scene["decision_required"] = None

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="information_report_handled",
            at=current_time,
            host_refs=(),
            actor_refs=(command.actor_id,),
            place_refs=(scene.get("location_id"),),
            causal_refs=(report_ref, claim_id),
            affected_owner_refs=(self.scene_path,),
            material_consequence_refs=(f"report_handling:{handling}:{report_ref}",),
            classification="restricted",
            audience_refs=(command.actor_id,),
            knowledge_refs=(claim_id,),
            source_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.commands.campaign_player_handoffs.report_handoff_resolution",
        )
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            self.scene_path: _json_bytes(scene),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("report handoff write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged_scene = overlay.read_json(self.scene_path)
            staged_narrative = staged_scene.get("narrative") if isinstance(staged_scene, Mapping) else None
            staged_handled = staged_narrative.get("handled_report_refs") if isinstance(staged_narrative, Mapping) else None
            if not isinstance(staged_handled, list) or report_ref not in staged_handled:
                raise ValueError("report handoff did not persist")

        return _BuiltPlan(
            code="report_handoff_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "report_ref": report_ref,
                "handling": handling,
                "status": "handled",
                "semantic_event_id": event_id,
            },
            validator=validate,
        )


__all__ = ["CampaignCommandPlanner"]
