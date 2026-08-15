"""Player-safe reads for mission-assignment preferences.

A pending request is player-owned selection state, not hidden institutional
intelligence. Expose only requests submitted by the authenticated player so the
GM can avoid duplicate or contradictory mission-availability menus while the
Mission Office remains the sole owner of actual offers.
"""
from __future__ import annotations

import copy
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.operations import CampaignOperations, OperationError
from shinobi_runtime.commands.mission_assignment_requests import MISSION_ASSIGNMENT_REQUEST_PATH

_INSTALLED = False
_GENERIC_REPORT = "An authorized operational report about a developing world concern has reached you."
_MAX_REQUEST_REFS = 32


def _player_requests(repository: Any, player_id: str) -> list[Mapping[str, Any]]:
    if repository.read_optional_bytes(MISSION_ASSIGNMENT_REQUEST_PATH) is None:
        return []
    try:
        registry = repository.read_json(MISSION_ASSIGNMENT_REQUEST_PATH)
    except (FileNotFoundError, ValueError) as exc:
        raise OperationError(503, "mission_assignment_request_registry_invalid") from exc
    requests = registry.get("requests") if isinstance(registry, Mapping) else None
    if not isinstance(requests, Mapping):
        raise OperationError(503, "mission_assignment_request_registry_invalid")
    rows: list[Mapping[str, Any]] = []
    for request_ref, request in sorted(requests.items()):
        if not isinstance(request_ref, str) or not isinstance(request, Mapping):
            raise OperationError(503, "mission_assignment_request_registry_invalid")
        if request.get("request_ref") != request_ref:
            raise OperationError(503, "mission_assignment_request_registry_invalid")
        if request.get("requester_ref") == player_id:
            rows.append(dict(request))
    return rows[-_MAX_REQUEST_REFS:]


def install_mission_assignment_request_projection() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_play_context = CampaignOperations.play_context
    if not getattr(original_play_context, "_mission_assignment_request_reads", False):
        @wraps(original_play_context)
        def play_context(self: CampaignOperations) -> Mapping[str, Any]:
            response = copy.deepcopy(original_play_context(self))
            campaign = response.get("campaign") if isinstance(response, Mapping) else None
            player_id = campaign.get("player_id") if isinstance(campaign, Mapping) else None
            if not isinstance(player_id, str):
                return response
            rows = _player_requests(self.repository, player_id)
            pending = [
                str(row["request_ref"])
                for row in rows
                if row.get("status") == "pending" and isinstance(row.get("request_ref"), str)
            ]
            object_reads = response.get("object_reads") if isinstance(response, dict) else None
            if isinstance(object_reads, dict):
                prefixes = object_reads.get("supported_ref_prefixes")
                if isinstance(prefixes, list) and "mission_assignment_request." not in prefixes:
                    prefixes.append("mission_assignment_request.")
                object_reads["suggested_pending_assignment_request_refs"] = pending
                object_reads["pending_assignment_request_count"] = len(pending)
                object_reads["use"] = str(object_reads.get("use") or "") + "; inspect mission_assignment_request.<id> for a player-submitted mission availability preference"

                # A handled legacy report can leave the pre-projection generic
                # placeholder in raw scene narrative. Never surface that stale
                # placeholder when no unhandled report remains.
                unhandled = object_reads.get("unhandled_report_refs")
                if isinstance(unhandled, list) and not unhandled:
                    scene = response.get("scene")
                    narrative = scene.get("narrative") if isinstance(scene, dict) else None
                    available = narrative.get("available_reports") if isinstance(narrative, dict) else None
                    if isinstance(available, list):
                        narrative["available_reports"] = [
                            value for value in available
                            if isinstance(value, str) and value != _GENERIC_REPORT
                        ]
            validate_bounded_json(response, label="play context", allow_float=True)
            return response

        play_context._mission_assignment_request_reads = True
        CampaignOperations.play_context = play_context

    original_inspect = CampaignOperations.inspect_game_object
    if not getattr(original_inspect, "_mission_assignment_request_reads", False):
        @wraps(original_inspect)
        def inspect_game_object(self: CampaignOperations, object_ref: str) -> Mapping[str, Any]:
            if not object_ref.startswith("mission_assignment_request."):
                return original_inspect(self, object_ref)
            try:
                with self._locked():
                    self.coordinator.git.assert_pristine()
                    before = self._read_fingerprint()
                    meta = self.repository.read_json(self.coordinator.meta_path)
                    player_id = meta.get("player_id") if isinstance(meta, Mapping) else None
                    if not isinstance(player_id, str):
                        raise OperationError(503, "object_access_policy_invalid")
                    rows = _player_requests(self.repository, player_id)
                    matches = [row for row in rows if row.get("request_ref") == object_ref]
                    if len(matches) != 1:
                        raise OperationError(404, "object_not_player_visible")
                    result = dict(matches[0])
                    self._require_read_only(before, "object_inspection_mutated_campaign")
            except OperationError:
                raise
            except Exception as exc:
                raise OperationError(503, "object_inspection_invalid") from exc
            response = {"object_ref": object_ref, "view": "mission_assignment_request", "object": result}
            try:
                validate_bounded_json(response, label="game object projection", allow_float=True)
            except ValueError as exc:
                raise OperationError(503, "object_projection_out_of_bounds") from exc
            return response

        inspect_game_object._mission_assignment_request_reads = True
        CampaignOperations.inspect_game_object = inspect_game_object

    _INSTALLED = True


__all__ = ["install_mission_assignment_request_projection", "_player_requests"]
