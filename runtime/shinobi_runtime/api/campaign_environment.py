"""Final player-safe API projection for current derived environment."""
from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from shinobi_runtime.api.campaign_manufacturing_discovery import RouteAwareCampaignOperations as _Base
from shinobi_runtime.api.command_discovery import compact_play_context
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.api.player_activity_handoff_projection import derive_activity_handoff
from shinobi_runtime.environment import environment_snapshot
from shinobi_runtime.tx.errors import DirtyRepositoryError, LockUnavailableError


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

    def command_identity(self) -> Mapping[str, Any]:
        """Read only the authoritative metadata needed to construct a command.

        MCP preview should not build a scene, cast, institution projection, or
        command catalog merely to learn the campaign ID, revision, and player
        actor. This keeps preview independent of long-campaign context growth
        while preserving the same pristine-check and writer-lock guarantees as
        other public reads.
        """

        try:
            with self._locked():
                self.coordinator.git.assert_pristine()
                before = self._read_fingerprint()
                meta = self.repository.read_json(self.coordinator.meta_path)
                if not isinstance(meta, Mapping):
                    raise OperationError(503, "campaign_command_identity_invalid")
                campaign_id = meta.get("campaign_id")
                revision = meta.get("revision")
                world_time = meta.get("time")
                player_id = meta.get("player_id")
                if (
                    not isinstance(campaign_id, str)
                    or not campaign_id
                    or isinstance(revision, bool)
                    or not isinstance(revision, int)
                    or revision < 0
                    or not isinstance(world_time, str)
                    or not world_time
                    or not isinstance(player_id, str)
                    or not player_id
                    or player_id not in self.allowed_actor_ids
                ):
                    raise OperationError(503, "campaign_command_identity_invalid")
                self._require_read_only(before, "command_identity_mutated_campaign")
                return {
                    "campaign_id": campaign_id,
                    "revision": revision,
                    "world_time": world_time,
                    "player_id": player_id,
                }
        except OperationError:
            raise
        except LockUnavailableError as exc:
            raise OperationError(503, "campaign_writer_busy") from exc
        except DirtyRepositoryError as exc:
            raise OperationError(503, "campaign_repository_dirty") from exc
        except (FileNotFoundError, TypeError, ValueError) as exc:
            raise OperationError(503, "campaign_command_identity_invalid") from exc

    def play_context(self) -> Mapping[str, Any]:
        """Expose one compact wire-safe handoff from every production transport.

        The rich projection remains an internal assembly detail. Before wire
        compaction, derive one compact activity/turn-completion cue strictly from
        the already-authoritative player-visible scene and domain handoffs. The
        activity cue is projection only; it never becomes a second save or
        authorizes a protected player decision.
        """

        context = copy.deepcopy(dict(super().play_context()))
        scene = context.get("scene")
        if isinstance(scene, dict):
            scene["activity_handoff"] = derive_activity_handoff(scene)
        return compact_play_context(context)

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
