"""Final player-safe API projection for current derived environment."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shinobi_runtime.api.campaign_manufacturing_discovery import RouteAwareCampaignOperations as _Base
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.environment import environment_snapshot


class RouteAwareCampaignOperations(_Base):
    """Production reads with current-location environment and unchanged truth boundaries."""

    def command_contract(self, command_type: str) -> Mapping[str, Any]:
        result = dict(super().command_contract(command_type))
        if command_type == "travel_resolution":
            guidance = result.get("input_guidance")
            if not isinstance(guidance, dict):
                guidance = dict(guidance) if isinstance(guidance, Mapping) else {}
                result["input_guidance"] = guidance
            guidance["environment"] = {
                "rule": "Travel duration already consumes authoritative derived route weather; never add a second weather delay."
            }
        return result

    def _project_play_context(
        self,
        meta: object,
        scene: object,
        player: object,
        state_root: str,
    ) -> Mapping[str, Any]:
        projected = dict(super()._project_play_context(meta, scene, player, state_root))
        if not isinstance(meta, Mapping):
            raise OperationError(503, "environment_context_invalid")
        world_time = meta.get("time")
        location_ref = None
        if isinstance(scene, Mapping):
            candidate = scene.get("location_id")
            if isinstance(candidate, str) and candidate:
                location_ref = candidate
        if location_ref is None and isinstance(player, Mapping):
            candidate = player.get("current_location_id")
            if isinstance(candidate, str) and candidate:
                location_ref = candidate
        if not isinstance(world_time, str) or not world_time or not isinstance(location_ref, str):
            raise OperationError(503, "environment_context_invalid")
        try:
            projected["environment"] = environment_snapshot(
                self.repository,
                world_time=world_time,
                location_ref=location_ref,
            )
        except (FileNotFoundError, TypeError, ValueError, KeyError) as exc:
            raise OperationError(503, "environment_context_invalid") from exc
        commands = projected.get("commands")
        if isinstance(commands, Mapping):
            updated_commands = dict(commands)
            command_types = updated_commands.get("command_types")
            if isinstance(command_types, Mapping):
                updated_types = {
                    key: dict(value) if isinstance(value, Mapping) else value
                    for key, value in command_types.items()
                }
                travel = updated_types.get("travel_resolution")
                if isinstance(travel, dict):
                    guidance = travel.setdefault("input_guidance", {})
                    if isinstance(guidance, dict):
                        guidance["environment"] = {
                            "rule": "Travel duration already consumes authoritative derived route weather; never add a second weather delay."
                        }
                updated_commands["command_types"] = updated_types
            projected["commands"] = updated_commands
        return projected


__all__ = ["RouteAwareCampaignOperations"]
