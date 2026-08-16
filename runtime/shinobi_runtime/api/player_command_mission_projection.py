"""Player-safe read projection for current missions of player-led exact teams.

Direct mission command authority remains participant-based. This extension adds
read-only command oversight for current missions assigned to an exact active
team led by the player, including a delegated mission after the player leaves
its participant set. It never broadens mission write authority.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.commands.mission_context_index import (
    CURRENT_MISSION_STATES,
    participant_current_refs,
)
from shinobi_runtime.commands.mission_owner import MissionOwner, mission_owner_path
from shinobi_runtime.membership_routes import team_refs_for_member
from shinobi_runtime.tx.errors import DirtyRepositoryError, LockUnavailableError

_INSTALLED = False
_MAX_COMMAND_MISSIONS = 16


def _player_led_exact_teams(operations: Any, player_id: str) -> dict[str, tuple[str, ...]]:
    try:
        team_refs = team_refs_for_member(operations.repository, player_id)
    except ValueError as exc:
        raise OperationError(503, "mission_command_context_invalid") from exc
    result: dict[str, tuple[str, ...]] = {}
    for team_ref in team_refs:
        try:
            _path, team = operations._owner_record(team_ref)
        except OperationError:
            continue
        members = team.get("member_refs") if isinstance(team, Mapping) else None
        if (
            team.get("schema") != "exact-team"
            or team.get("status") != "active"
            or team.get("leader_ref") != player_id
            or not isinstance(members, list)
            or player_id not in members
            or any(not isinstance(ref, str) or not ref for ref in members)
        ):
            continue
        result[team_ref] = tuple(members)
    return result


def _player_command_mission_context(
    operations: Any,
    player_id: str,
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """Return bounded current mission reads for player-led exact teams."""
    teams = _player_led_exact_teams(operations, player_id)
    if not teams:
        return (), (), False
    try:
        index = operations._mission_context_index()
    except OperationError:
        raise
    except (TypeError, ValueError) as exc:
        raise OperationError(503, "mission_command_context_invalid") from exc

    candidate_refs: set[str] = set()
    for members in teams.values():
        for member_ref in members:
            try:
                candidate_refs.update(participant_current_refs(index, member_ref))
            except (TypeError, ValueError) as exc:
                raise OperationError(503, "mission_command_context_invalid") from exc

    selected: list[str] = []
    briefing: list[str] = []
    for mission_id in sorted(candidate_refs):
        path = mission_owner_path(mission_id)
        try:
            owner = MissionOwner.from_record(operations.repository.read_json(path))
        except (FileNotFoundError, TypeError, ValueError):
            continue
        team_ref = owner.operation_ref
        if (
            owner.mission.state not in CURRENT_MISSION_STATES
            or not isinstance(team_ref, str)
            or team_ref not in teams
        ):
            continue
        selected.append(mission_id)
        if owner.briefing is not None:
            briefing.append(mission_id)

    truncated = len(selected) > _MAX_COMMAND_MISSIONS
    bounded = selected[:_MAX_COMMAND_MISSIONS]
    briefing_set = set(briefing)
    return (
        tuple(bounded),
        tuple(ref for ref in bounded if ref in briefing_set),
        truncated,
    )


def _mission_is_player_command_readable(operations: Any, player_id: str, mission_id: str) -> bool:
    mission_ids, _briefing, _truncated = _player_command_mission_context(
        operations, player_id
    )
    return mission_id in mission_ids


def install_player_command_mission_projection() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.api import campaign_environment as module

    operations = module.RouteAwareCampaignOperations
    original_projection = operations._project_play_context
    original_inspect = operations.inspect_game_object

    if not getattr(original_projection, "_player_command_mission_projection", False):
        @wraps(original_projection)
        def projected(self: Any, meta: object, scene: object, player: object, state_root: str) -> Mapping[str, Any]:
            payload = dict(original_projection(self, meta, scene, player, state_root))
            campaign = payload.get("campaign") if isinstance(payload, Mapping) else None
            player_id = campaign.get("player_id") if isinstance(campaign, Mapping) else None
            if not isinstance(player_id, str):
                raise OperationError(503, "mission_command_context_invalid")
            command_ids, command_briefing_ids, truncated = _player_command_mission_context(
                self, player_id
            )
            reads = payload.get("mission_reads")
            updated_reads = dict(reads) if isinstance(reads, Mapping) else {}
            direct_briefing = updated_reads.get("operational_brief_owner_ids", [])
            if not isinstance(direct_briefing, list) or any(
                not isinstance(ref, str) for ref in direct_briefing
            ):
                raise OperationError(503, "mission_command_context_invalid")
            updated_reads["command_mission_owner_ids"] = list(command_ids)
            combined_briefing = list(dict.fromkeys([
                *direct_briefing,
                *command_briefing_ids,
            ]))
            updated_reads["operational_brief_owner_ids"] = combined_briefing[:_MAX_COMMAND_MISSIONS]
            updated_reads["command_missions_truncated"] = truncated
            updated_reads["command_scope"] = (
                "Read-only oversight for current missions assigned to an active exact team "
                "led by the player. Mission write authority remains participant-based."
            )
            payload["mission_reads"] = updated_reads
            validate_bounded_json(payload, label="play context", allow_float=True)
            return payload

        projected._player_command_mission_projection = True  # type: ignore[attr-defined]
        operations._project_play_context = projected

    if not getattr(original_inspect, "_player_command_mission_projection", False):
        @wraps(original_inspect)
        def inspected(self: Any, object_ref: str) -> Mapping[str, Any]:
            try:
                return original_inspect(self, object_ref)
            except OperationError as exc:
                if (
                    exc.status_code != 404
                    or exc.code != "object_not_player_visible"
                    or not isinstance(object_ref, str)
                    or not object_ref.startswith("mission.")
                ):
                    raise
            try:
                with self._locked():
                    self.coordinator.git.assert_pristine()
                    before = self._read_fingerprint()
                    meta = self.repository.read_json(self.coordinator.meta_path)
                    player_id = meta.get("player_id") if isinstance(meta, Mapping) else None
                    if not isinstance(player_id, str):
                        raise OperationError(503, "mission_command_context_invalid")
                    if not _mission_is_player_command_readable(self, player_id, object_ref):
                        raise OperationError(404, "object_not_player_visible")
                    path = mission_owner_path(object_ref)
                    owner = MissionOwner.from_record(self.repository.read_json(path))
                    response = {
                        "object_ref": object_ref,
                        "view": "mission_command_oversight",
                        "object": owner.to_record(),
                    }
                    self._require_read_only(before, "object_inspection_mutated_campaign")
            except OperationError:
                raise
            except LockUnavailableError as exc:
                raise OperationError(503, "campaign_writer_busy") from exc
            except DirtyRepositoryError as exc:
                raise OperationError(503, "campaign_repository_dirty") from exc
            except (FileNotFoundError, TypeError, ValueError, CommandRejectedError) as exc:
                raise OperationError(503, "mission_command_context_invalid") from exc
            try:
                validate_bounded_json(response, label="game object projection", allow_float=True)
            except ValueError as exc:
                raise OperationError(503, "object_projection_out_of_bounds") from exc
            return response

        inspected._player_command_mission_projection = True  # type: ignore[attr-defined]
        operations.inspect_game_object = inspected

    _INSTALLED = True


__all__ = [
    "_mission_is_player_command_readable",
    "_player_command_mission_context",
    "install_player_command_mission_projection",
]
