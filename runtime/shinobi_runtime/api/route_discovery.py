"""Player-visible command discovery for bounded gameplay objects.

The runtime requires exact semantic IDs and payload values while callers are
forbidden to guess hidden state. This extension projects only executable values
that the campaign player is already entitled to know: destination-scoped route
options, exact teams containing the player, trainable paths for those team
members, and narrowly registered team refit policies.
"""

from __future__ import annotations

from typing import Any, Collection, Mapping, Sequence

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.operations import CampaignOperations, OperationError
from shinobi_runtime.commands.constants import TRAINABLE_ROOTS
from shinobi_runtime.commands.paths import TRAVEL_MECHANICS_PATH
from shinobi_runtime.domain import LocationGraph
from shinobi_runtime.membership_routes import team_refs_for_member
from shinobi_runtime.domain.equipment import (
    actor_team_policy_roles,
    assignment_refit_policies,
)
from shinobi_runtime.tx.errors import DirtyRepositoryError, LockUnavailableError
from shinobi_runtime.security.route_access import known_nonpublic_routes_for_connection


_MAX_ROUTE_OPTIONS = 16
_MAX_PLAYER_TEAM_REFS = 128  # output window, never a world-validity ceiling
_MAX_TRAINABLE_PATHS_PER_MEMBER = 192


def discover_route_options(
    routes_record: Mapping[str, Any],
    mechanics: Mapping[str, Any],
    *,
    origin_id: str,
    destination_id: str,
    known_route_refs: Collection[str] = (),
) -> Mapping[str, Any]:
    """Return bounded executable route hints for one known destination."""

    graph = LocationGraph(routes_record)
    origin_anchor = graph.anchor(origin_id)
    destination_anchor = graph.anchor(destination_id)
    options: list[dict[str, Any]] = []
    options_truncated = False

    if origin_id != destination_id and origin_anchor == destination_anchor:
        local_rules = mechanics.get("local_travel") if isinstance(mechanics, Mapping) else None
        reference_hours = local_rules.get("reference_hours") if isinstance(local_rules, Mapping) else None
        if (
            isinstance(reference_hours, bool)
            or not isinstance(reference_hours, (int, float))
            or reference_hours <= 0
        ):
            raise ValueError("local travel mechanics invalid")
        options.append(
            {
                "route_id": "route_local",
                "destination_id": destination_id,
                "route_kind": "local",
                "reference_hours": reference_hours,
                "requires_local_completion": False,
            }
        )
    elif origin_anchor != destination_anchor:
        status_multipliers = mechanics.get("route_status_multipliers") if isinstance(mechanics, Mapping) else None
        if not isinstance(status_multipliers, Mapping):
            raise ValueError("route status mechanics invalid")
        candidates = []
        for route in graph.routes:
            route_id = route.get("id")
            route_from = route.get("from")
            route_to = route.get("to")
            status = route.get("status")
            if (
                not isinstance(route_id, str)
                or not route_id.startswith("route_")
                or (
                    route.get("knowledge_classification", "public") != "public"
                    and route_id not in known_route_refs
                )
                or origin_anchor not in (route_from, route_to)
                or destination_anchor not in (route_from, route_to)
                or origin_anchor == destination_anchor
                or status not in status_multipliers
            ):
                continue
            candidates.append(route)
        candidates.sort(key=lambda row: str(row.get("id")))
        options_truncated = len(candidates) > _MAX_ROUTE_OPTIONS
        for route in candidates[:_MAX_ROUTE_OPTIONS]:
            option = {
                "route_id": route["id"],
                "destination_id": destination_anchor,
                "route_kind": "registered",
                "mode": route.get("mode"),
                "status": route.get("status"),
                "travel_days_band": route.get("travel_days_band"),
                "reference_travel_days": route.get("reference_travel_days"),
                "requires_local_completion": destination_id != destination_anchor,
            }
            if destination_id != destination_anchor:
                option["final_destination_id"] = destination_id
            options.append(option)

    return {
        "origin_id": origin_id,
        "origin_anchor_ref": origin_anchor,
        "destination_id": destination_id,
        "destination_anchor_ref": destination_anchor,
        "route_options": options,
        "options_truncated": options_truncated,
    }


def _value_at_dotted_path(record: Mapping[str, Any], path: str) -> object:
    current: object = record
    for segment in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(segment)
    return current


def _numeric_leaf_paths(value: object, *, prefix: str) -> list[str]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [prefix]
    if not isinstance(value, Mapping):
        return []
    result: list[str] = []
    for key in sorted(value):
        if not isinstance(key, str) or not key:
            continue
        child = value.get(key)
        child_prefix = f"{prefix}.{key}" if prefix else key
        result.extend(_numeric_leaf_paths(child, prefix=child_prefix))
    return result


class RouteAwareCampaignOperations(CampaignOperations):
    """Campaign operations with bounded executable-value discovery."""

    def _player_exact_team_refs(self, player_id: str) -> tuple[str, ...]:
        try:
            team_ids = team_refs_for_member(self.repository, player_id)
        except ValueError as exc:
            raise OperationError(503, "object_access_policy_invalid") from exc
        return tuple(team_ids[:_MAX_PLAYER_TEAM_REFS])

    def _player_exact_team_ref_count(self, player_id: str) -> int:
        """Count exact player teams through direct membership routing."""
        try:
            return len(team_refs_for_member(self.repository, player_id))
        except ValueError as exc:
            raise OperationError(503, "object_access_policy_invalid") from exc

    def _project_play_context(
        self,
        meta: object,
        scene: object,
        player: object,
        state_root: str,
    ) -> Mapping[str, Any]:
        base = dict(super()._project_play_context(meta, scene, player, state_root))
        if not isinstance(meta, Mapping):
            raise OperationError(503, "play_context_invalid")
        player_id = meta.get("player_id")
        if not isinstance(player_id, str):
            raise OperationError(503, "play_context_invalid")
        refs = self._player_exact_team_refs(player_id)
        exact_team_count = self._player_exact_team_ref_count(player_id)
        object_reads = dict(base.get("object_reads", {}))
        object_reads["suggested_exact_team_refs"] = list(refs)
        object_reads["exact_team_ref_count"] = exact_team_count
        object_reads["exact_team_refs_truncated"] = exact_team_count > len(refs)
        object_reads["exact_team_discovery_basis"] = "current exact teams whose persisted roster contains the player"
        base["object_reads"] = object_reads
        return base

    def _team_training_interface(self, team: Mapping[str, Any]) -> Mapping[str, Any]:
        members = team.get("member_refs")
        training = team.get("training")
        if not isinstance(members, list) or not isinstance(training, Mapping):
            raise OperationError(503, "object_team_invalid")
        if any(not isinstance(ref, str) or not ref for ref in members):
            raise OperationError(503, "object_team_invalid")

        paths_by_member: dict[str, list[str]] = {}
        truncated: list[str] = []
        for member_ref in members:
            _member_path, member = self._owner_record(member_ref)
            paths: list[str] = []
            for root in sorted(TRAINABLE_ROOTS):
                root_value = _value_at_dotted_path(member, root)
                paths.extend(_numeric_leaf_paths(root_value, prefix=root))
            paths = sorted(set(paths))
            if len(paths) > _MAX_TRAINABLE_PATHS_PER_MEMBER:
                paths = paths[:_MAX_TRAINABLE_PATHS_PER_MEMBER]
                truncated.append(member_ref)
            paths_by_member[member_ref] = paths

        instructors = training.get("instructor_refs", [])
        facilities = training.get("facility_refs", [])
        if (
            not isinstance(instructors, list)
            or any(not isinstance(ref, str) for ref in instructors)
            or not isinstance(facilities, list)
            or any(not isinstance(ref, str) for ref in facilities)
        ):
            raise OperationError(503, "object_team_invalid")
        return {
            "command_type": "team_training_session_resolution",
            "member_targets_shape": "object mapping each trained exact member_ref to one trainable development path string",
            "eligible_member_refs": list(members),
            "valid_member_target_paths": paths_by_member,
            "target_paths_truncated_for": truncated,
            "instructor_refs": list(instructors),
            "facility_refs": list(facilities),
            "model_ref": training.get("model_ref"),
            "target_time_rule": "later than current world time",
            "active_hours_rule": "positive and no greater than elapsed hours to target_time",
        }

    def _team_equipment_interface(
        self,
        team: Mapping[str, Any],
        *,
        player_id: str,
    ) -> Mapping[str, Any]:
        team_ref = team.get("id")
        members = team.get("member_refs")
        if not isinstance(team_ref, str) or not isinstance(members, list):
            raise OperationError(503, "object_team_invalid")
        candidates: list[dict[str, Any]] = []
        try:
            policies = assignment_refit_policies(self.repository, team_ref)
        except (FileNotFoundError, TypeError, ValueError) as exc:
            raise OperationError(503, "object_team_refit_policy_invalid") from exc
        for loadout_ref, policy in policies:
            holder_ref = policy.get("holder_ref")
            supply_refs = policy.get("supply_stock_refs")
            allowed_roles = policy.get("authorized_team_roles")
            if (
                not isinstance(holder_ref, str)
                or holder_ref not in members
                or not isinstance(supply_refs, list)
                or not supply_refs
                or not isinstance(allowed_roles, list)
            ):
                raise OperationError(503, "object_team_refit_policy_invalid")
            actor_roles = actor_team_policy_roles(
                team,
                actor_ref=player_id,
                holder_ref=holder_ref,
            )
            effective_roles = sorted(actor_roles.intersection(allowed_roles))
            if not effective_roles:
                continue
            _holder_path, holder = self._owner_record(holder_ref)
            candidates.append(
                {
                    "holder_ref": holder_ref,
                    "current_loadout_ref": holder.get("equipment_loadout_id"),
                    "target_loadout_ref": loadout_ref,
                    "stock_ref": supply_refs[0],
                    "supply_stock_refs": list(supply_refs),
                    "authority_roles": effective_roles,
                }
            )
        return {
            "command_type": "inventory_resolution",
            "action": "refit",
            "candidate_refits": candidates,
            "stock_ref_rule": "use the candidate stock_ref; additional registered supply stocks are conserved automatically",
        }

    def _inspect_team(self, object_ref: str) -> Mapping[str, Any]:
        try:
            with self._locked():
                self.coordinator.git.assert_pristine()
                before = self._read_fingerprint()
                meta = self.repository.read_json(self.coordinator.meta_path)
                player_id = meta.get("player_id") if isinstance(meta, Mapping) else None
                if not isinstance(player_id, str):
                    raise OperationError(503, "object_access_policy_invalid")
                _path, team = self._owner_record(object_ref)
                members = team.get("member_refs") if isinstance(team, Mapping) else None
                if (
                    team.get("schema") != "exact-team"
                    or not isinstance(members, list)
                    or any(not isinstance(value, str) for value in members)
                    or player_id not in members
                ):
                    raise OperationError(404, "object_not_player_visible")
                result = {
                    key: team.get(key)
                    for key in (
                        "schema", "id", "name", "status", "team_type",
                        "leader_ref", "deputy_ref", "member_refs",
                        "assignment_authority_ref", "doctrine_ref",
                        "current_assignment_ref", "location_ref",
                    )
                    if key in team
                }
                result["training_interface"] = self._team_training_interface(team)
                result["equipment_interface"] = self._team_equipment_interface(
                    team,
                    player_id=player_id,
                )
                self._require_read_only(before, "object_inspection_mutated_campaign")
        except OperationError:
            raise
        except LockUnavailableError as exc:
            raise OperationError(503, "campaign_writer_busy") from exc
        except DirtyRepositoryError as exc:
            raise OperationError(503, "campaign_repository_dirty") from exc
        except (FileNotFoundError, TypeError, ValueError) as exc:
            raise OperationError(503, "object_inspection_invalid") from exc

        response = {"object_ref": object_ref, "view": "exact_team", "object": result}
        try:
            validate_bounded_json(response, label="game object projection", allow_float=True)
        except ValueError as exc:
            raise OperationError(503, "object_projection_out_of_bounds") from exc
        return response

    def inspect_game_object(self, object_ref: str) -> Mapping[str, Any]:
        if object_ref.startswith("team."):
            return self._inspect_team(object_ref)
        if not object_ref.startswith("place."):
            return super().inspect_game_object(object_ref)

        try:
            with self._locked():
                self.coordinator.git.assert_pristine()
                before = self._read_fingerprint()
                meta = self.repository.read_json(self.coordinator.meta_path)
                player_id = meta.get("player_id") if isinstance(meta, Mapping) else None
                if not isinstance(player_id, str):
                    raise OperationError(503, "object_access_policy_invalid")

                world = self.repository.read_json("state/world/routes-and-settlements.json")
                graph = LocationGraph(world)
                place = graph.place(object_ref)
                if not isinstance(place, Mapping):
                    raise OperationError(404, "object_not_player_visible")

                current_location = None
                try:
                    _player_path, player = self._owner_record(player_id)
                    current_location = player.get("current_location_id") or player.get("location_ref")
                except OperationError:
                    pass

                classification = place.get("knowledge_classification", "public")
                locally_known = current_location in {object_ref, place.get("route_anchor_ref")}
                if classification != "public" and not locally_known:
                    raise OperationError(404, "object_not_player_visible")

                result = {
                    key: place.get(key)
                    for key in (
                        "id",
                        "name",
                        "country_id",
                        "kind",
                        "status",
                        "timeline_status",
                        "route_anchor_ref",
                        "authority_ref",
                        "knowledge_classification",
                        "mechanical_modules",
                    )
                    if key in place
                }
                if isinstance(current_location, str) and current_location:
                    mechanics = self.repository.read_json(TRAVEL_MECHANICS_PATH)
                    if not isinstance(mechanics, Mapping):
                        raise OperationError(503, "travel_registry_invalid")
                    known_route_refs = known_nonpublic_routes_for_connection(
                        self.repository,
                        player_id,
                        graph,
                        origin_id=current_location,
                        destination_id=object_ref,
                    )
                    result["travel_from_player"] = discover_route_options(
                        world,
                        mechanics,
                        origin_id=current_location,
                        destination_id=object_ref,
                        known_route_refs=known_route_refs,
                    )

                self._require_read_only(before, "object_inspection_mutated_campaign")
        except OperationError:
            raise
        except LockUnavailableError as exc:
            raise OperationError(503, "campaign_writer_busy") from exc
        except DirtyRepositoryError as exc:
            raise OperationError(503, "campaign_repository_dirty") from exc
        except (FileNotFoundError, TypeError, ValueError) as exc:
            raise OperationError(503, "object_inspection_invalid") from exc

        response = {"object_ref": object_ref, "view": "place_summary", "object": result}
        try:
            validate_bounded_json(response, label="game object projection", allow_float=True)
        except ValueError as exc:
            raise OperationError(503, "object_projection_out_of_bounds") from exc
        return response
