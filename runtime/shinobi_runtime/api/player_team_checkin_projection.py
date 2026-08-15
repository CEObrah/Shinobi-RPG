"""Player-safe exact reads for durable player-led team check-ins."""
from __future__ import annotations

import copy
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.operations import CampaignOperations, OperationError
from shinobi_runtime.commands.team_checkin_records import player_team_checkins, project_team_checkin

_INSTALLED = False
_GENERIC_CHECKIN_SUFFIX = "has routine field, training, or readiness matters ready to discuss."
_GENERIC_READY_SUFFIX = "has a fresh internal check-in ready."


def _install_api_reads() -> None:
    original_play_context = CampaignOperations.play_context
    if not getattr(original_play_context, "_player_team_checkin_reads", False):
        @wraps(original_play_context)
        def play_context(self: CampaignOperations) -> Mapping[str, Any]:
            response = copy.deepcopy(original_play_context(self))
            campaign = response.get("campaign") if isinstance(response, Mapping) else None
            player_id = campaign.get("player_id") if isinstance(campaign, Mapping) else None
            if not isinstance(player_id, str):
                return response
            try:
                checkins = player_team_checkins(self.repository, player_id)
            except (TypeError, ValueError):
                checkins = []
            refs = [row.get("checkin_ref") for row in checkins if isinstance(row.get("checkin_ref"), str)]
            unhandled = [row for row in checkins if not row.get("handled")]
            handled_refs = [row.get("checkin_ref") for row in checkins if row.get("handled") and isinstance(row.get("checkin_ref"), str)]
            unhandled_refs = [row.get("checkin_ref") for row in unhandled if isinstance(row.get("checkin_ref"), str)]

            scene = response.get("scene") if isinstance(response, dict) else None
            if isinstance(scene, dict):
                pressures = scene.get("observable_pressures")
                if isinstance(pressures, list):
                    pressures = [
                        value for value in pressures
                        if not (isinstance(value, str) and value.endswith(_GENERIC_READY_SUFFIX))
                    ]
                    for row in unhandled:
                        name = row.get("team_name")
                        contact = row.get("contact_actor_ref")
                        topics = row.get("topic_cues")
                        if isinstance(name, str) and isinstance(contact, str) and isinstance(topics, list):
                            message = f"{name} has an internal check-in ready with {contact}: " + "; ".join(str(value) for value in topics) + "."
                            if message not in pressures:
                                pressures.append(message)
                    scene["observable_pressures"] = pressures[:12]
                narrative = scene.get("narrative")
                if isinstance(narrative, dict):
                    reports = narrative.get("available_reports")
                    if isinstance(reports, list):
                        reports = [
                            value for value in reports
                            if not (isinstance(value, str) and value.endswith(_GENERIC_CHECKIN_SUFFIX))
                        ]
                        for row in unhandled:
                            name = row.get("team_name")
                            topics = row.get("topic_cues")
                            if isinstance(name, str) and isinstance(topics, list):
                                message = f"{name} check-in topics: " + "; ".join(str(value) for value in topics) + "."
                                if message not in reports:
                                    reports.append(message)
                        narrative["available_reports"] = reports[-6:]
                scene["team_checkin_handoffs"] = [
                    {
                        key: row.get(key)
                        for key in (
                            "checkin_ref", "source_event_ref", "team_ref", "team_name",
                            "contact_actor_ref", "ready_at", "topic_cues", "snapshot_basis",
                        )
                    }
                    for row in unhandled
                ]

            object_reads = response.get("object_reads") if isinstance(response, dict) else None
            if isinstance(object_reads, dict):
                prefixes = object_reads.get("supported_ref_prefixes")
                if isinstance(prefixes, list) and "team_checkin." not in prefixes:
                    prefixes.append("team_checkin.")
                object_reads["suggested_team_checkin_refs"] = refs
                object_reads["team_checkin_ref_count"] = len(refs)
                object_reads["unhandled_team_checkin_refs"] = unhandled_refs
                object_reads["handled_team_checkin_refs"] = handled_refs
                object_reads["use"] = str(object_reads.get("use") or "") + "; inspect team_checkin.<id> for a player-visible exact team check-in and its snapshotted agenda"
            validate_bounded_json(response, label="play context", allow_float=True)
            return response

        play_context._player_team_checkin_reads = True
        CampaignOperations.play_context = play_context

    original_inspect = CampaignOperations.inspect_game_object
    if not getattr(original_inspect, "_player_team_checkin_reads", False):
        @wraps(original_inspect)
        def inspect_game_object(self: CampaignOperations, object_ref: str) -> Mapping[str, Any]:
            if not object_ref.startswith("team_checkin."):
                return original_inspect(self, object_ref)
            try:
                with self._locked():
                    self.coordinator.git.assert_pristine()
                    before = self._read_fingerprint()
                    meta = self.repository.read_json(self.coordinator.meta_path)
                    player_id = meta.get("player_id") if isinstance(meta, Mapping) else None
                    if not isinstance(player_id, str):
                        raise OperationError(503, "object_access_policy_invalid")
                    try:
                        result = project_team_checkin(self.repository, object_ref, player_id)
                    except ValueError as exc:
                        raise OperationError(404, "object_not_player_visible") from exc
                    self._require_read_only(before, "object_inspection_mutated_campaign")
            except OperationError:
                raise
            except Exception as exc:
                raise OperationError(503, "object_inspection_invalid") from exc
            response = {"object_ref": object_ref, "view": "team_checkin", "object": result}
            try:
                validate_bounded_json(response, label="game object projection", allow_float=True)
            except ValueError as exc:
                raise OperationError(503, "object_projection_out_of_bounds") from exc
            return response

        inspect_game_object._player_team_checkin_reads = True
        CampaignOperations.inspect_game_object = inspect_game_object


def install_player_team_checkin_projection() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_api_reads()
    _INSTALLED = True


__all__ = ["install_player_team_checkin_projection"]
