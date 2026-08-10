"""Player-visible route discovery for place inspection.

The travel reducer requires exact registered route IDs, while callers are not
allowed to guess hidden IDs.  This projection resolves only the route relation
between the campaign player's current location and one already-authorized
place inspection.  It never exposes the broader route graph.
"""

from __future__ import annotations

from typing import Any, Mapping

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.operations import CampaignOperations, OperationError
from shinobi_runtime.commands.paths import TRAVEL_MECHANICS_PATH
from shinobi_runtime.domain import LocationGraph
from shinobi_runtime.tx.errors import DirtyRepositoryError, LockUnavailableError


_MAX_ROUTE_OPTIONS = 16


def discover_route_options(
    routes_record: Mapping[str, Any],
    mechanics: Mapping[str, Any],
    *,
    origin_id: str,
    destination_id: str,
) -> Mapping[str, Any]:
    """Return bounded executable route hints for one known destination.

    Local travel is represented by the reducer's stable ``route_local`` token.
    Strategic travel exposes only a registered route whose endpoints connect
    the origin anchor to the requested destination anchor.  If the requested
    place is below that destination anchor, the caller is told that a second
    local leg remains instead of being allowed to teleport to the sub-place.
    """

    graph = LocationGraph(routes_record)
    origin_anchor = graph.anchor(origin_id)
    destination_anchor = graph.anchor(destination_id)
    options: list[dict[str, Any]] = []

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
                or origin_anchor not in (route_from, route_to)
                or destination_anchor not in (route_from, route_to)
                or origin_anchor == destination_anchor
                or status not in status_multipliers
            ):
                continue
            candidates.append(route)
        candidates.sort(key=lambda row: str(row.get("id")))
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
        "options_truncated": len(options) >= _MAX_ROUTE_OPTIONS,
    }


class RouteAwareCampaignOperations(CampaignOperations):
    """Campaign operations with destination-scoped travel discovery on places."""

    def inspect_game_object(self, object_ref: str) -> Mapping[str, Any]:
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
                    result["travel_from_player"] = discover_route_options(
                        world,
                        mechanics,
                        origin_id=current_location,
                        destination_id=object_ref,
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
