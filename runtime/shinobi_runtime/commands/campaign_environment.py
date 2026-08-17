"""Final production planner layer for deterministic environment mechanics."""
from __future__ import annotations

import json
from decimal import Decimal, ROUND_CEILING
from typing import Any, Dict, Mapping, Sequence, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.campaign_player_handoffs import CampaignCommandPlanner as _Base
from shinobi_runtime.commands.core import _BuiltPlan, _declared_payload, _json_bytes, _stable_id
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import ROUTES_PATH as _ROUTES_PATH, TRAVEL_MECHANICS_PATH as _TRAVEL_MECHANICS_PATH
from shinobi_runtime.domain import LocationGraph
from shinobi_runtime.security.route_access import actor_knows_route
from shinobi_runtime.environment import apply_environment_to_terrain, environment_snapshot, route_travel_factor_milli
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


class CampaignCommandPlanner(_Base):
    """Production planner with derived weather in travel and existing terrain channels."""

    def _terrain_state_for_location(
        self, *, location_ref: object, side_refs: Sequence[str], mechanics: Mapping[str, Any]
    ):
        terrain = super()._terrain_state_for_location(
            location_ref=location_ref,
            side_refs=side_refs,
            mechanics=mechanics,
        )
        if not isinstance(location_ref, str) or not location_ref:
            return terrain
        meta = self.repository.read_json(self.meta_path)
        world_time = meta.get("time") if isinstance(meta, Mapping) else None
        if not isinstance(world_time, str) or not world_time:
            raise CommandRejectedError("environment_time_invalid")
        try:
            environment = environment_snapshot(
                self.repository,
                world_time=world_time,
                location_ref=location_ref,
            )
            return apply_environment_to_terrain(terrain, environment)
        except (FileNotFoundError, TypeError, ValueError, KeyError) as exc:
            raise CommandRejectedError("environment_projection_invalid") from exc

    def _travel_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _declared_payload(command.payload, command.command_type)
        route_id = _stable_id(command.payload["route_id"], "travel_route_invalid", prefix="route_")
        destination_id = _stable_id(command.payload["destination_id"], "travel_destination_invalid")
        raw_travelers = command.payload["traveler_refs"]
        if (
            not isinstance(raw_travelers, Sequence)
            or isinstance(raw_travelers, (str, bytes, bytearray))
            or not 1 <= len(raw_travelers) <= 16
            or any(not isinstance(ref, str) for ref in raw_travelers)
        ):
            raise CommandRejectedError("travel_party_invalid")
        traveler_refs = tuple(_stable_id(ref, "travel_traveler_invalid") for ref in raw_travelers)
        if len(set(traveler_refs)) != len(traveler_refs) or command.actor_id not in traveler_refs:
            raise CommandRejectedError("travel_party_invalid")
        context_raw = command.payload.get("party_context_ref")
        party_context_ref = None if context_raw is None else _stable_id(context_raw, "travel_party_context_invalid")
        mission_raw = command.payload.get("mission_ref")
        mission_id = None
        if mission_raw is not None:
            mission_id = _stable_id(mission_raw, "travel_mission_ref_invalid", prefix="mission.")
            _mission_path, mission_owner = self._read_mission(
                mission_id, actor_id=command.actor_id, current_time=current_time
            )
            if mission_owner.mission.state != "active":
                raise CommandRejectedError("travel_mission_not_active")
            mission_participants = set(mission_owner.mission.participant_refs)
            if any(ref not in mission_participants for ref in traveler_refs):
                raise CommandRejectedError("travel_party_not_mission_participants")

        if len(traveler_refs) == 1:
            if party_context_ref is not None:
                raise CommandRejectedError("travel_party_context_invalid")
            party_authority_basis = "mission_travel" if mission_id is not None else "self_travel"
        else:
            if party_context_ref is None:
                raise CommandRejectedError("travel_party_context_required")
            decision = self._domain_authority().travel_party(
                actor_ref=command.actor_id,
                traveler_refs=traveler_refs,
                candidate_team_refs=(party_context_ref,),
            )
            if not decision.allowed:
                raise CommandRejectedError("travel_party_not_authorized")
            party_authority_basis = decision.basis

        travelers: Dict[str, Tuple[str, Dict[str, Any]]] = {}
        for traveler_ref in traveler_refs:
            path, record = self._resolve_actor_for_write(traveler_ref)
            if record.get("life_status") not in ("active", "alive"):
                raise CommandRejectedError("travel_traveler_not_active")
            travelers[traveler_ref] = (path, record)

        _player_path, player = travelers[command.actor_id]
        current_location = player.get("current_location_id")
        if not isinstance(current_location, str):
            raise CommandRejectedError("travel_origin_invalid")
        for _traveler_ref, (_path, record) in travelers.items():
            if record.get("current_location_id") != current_location:
                raise CommandRejectedError("travel_party_not_co_located")
        try:
            routes_record = self.repository.read_json(_ROUTES_PATH)
            mechanics = self.repository.read_json(_TRAVEL_MECHANICS_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("travel_registry_invalid") from exc
        try:
            location_graph = LocationGraph(routes_record)
        except ValueError as exc:
            raise CommandRejectedError("travel_registry_invalid") from exc
        origin_anchor = location_graph.anchor(current_location)
        routes = list(location_graph.routes)
        route = next((item for item in routes if isinstance(item, Mapping) and item.get("id") == route_id), None)
        local_travel = route_id == "route_local"
        if local_travel:
            destination_anchor = location_graph.anchor(destination_id)
            local_rules = mechanics.get("local_travel") if isinstance(mechanics, Mapping) else None
            reference_hours = local_rules.get("reference_hours") if isinstance(local_rules, Mapping) else None
            if (
                destination_id == current_location
                or destination_anchor != origin_anchor
                or isinstance(reference_hours, bool)
                or not isinstance(reference_hours, (int, float))
                or reference_hours <= 0
            ):
                raise CommandRejectedError("travel_route_endpoint_mismatch")
            reference_days = float(reference_hours) / 24.0
            status_multiplier = 1.0
            environment_origin = current_location
        else:
            if route is None:
                raise CommandRejectedError("travel_route_invalid")
            if not actor_knows_route(self.repository, command.actor_id, route):
                raise CommandRejectedError("travel_route_unknown")
            endpoints = (route.get("from"), route.get("to"))
            if origin_anchor not in endpoints or destination_id not in endpoints or destination_id == origin_anchor:
                raise CommandRejectedError("travel_route_endpoint_mismatch")
            reference_days = route.get("reference_travel_days")
            if isinstance(reference_days, bool) or not isinstance(reference_days, (int, float)) or reference_days <= 0:
                raise CommandRejectedError("travel_registry_invalid")
            status_multipliers = mechanics.get("route_status_multipliers") if isinstance(mechanics, Mapping) else None
            if not isinstance(status_multipliers, Mapping):
                raise CommandRejectedError("travel_registry_invalid")
            status_multiplier = status_multipliers.get(route.get("status"))
            if isinstance(status_multiplier, bool) or not isinstance(status_multiplier, (int, float)):
                raise CommandRejectedError("travel_registry_invalid")
            environment_origin = origin_anchor

        speeds = []
        for _traveler_ref, (_path, record) in travelers.items():
            martial = record.get("martial_skills")
            attributes = record.get("attributes")
            movement = martial.get("movement") if isinstance(martial, Mapping) else None
            endurance = attributes.get("endurance") if isinstance(attributes, Mapping) else None
            if any(isinstance(value, bool) or not isinstance(value, int) for value in (movement, endurance)):
                raise CommandRejectedError("travel_capability_invalid")
            speed = Decimal("0.65") + Decimal(movement) / Decimal(200) + Decimal(endurance) / Decimal(500)
            speeds.append(min(Decimal("1.80"), max(Decimal("0.50"), speed)))
        speed = min(speeds)

        reference_route_hours = max(
            1,
            int(
                (
                    Decimal(str(reference_days))
                    * Decimal(24)
                    * Decimal(str(status_multiplier))
                ).to_integral_value(rounding=ROUND_CEILING)
            ),
        )
        try:
            environment_factor = route_travel_factor_milli(
                self.repository,
                world_time=str(current_time),
                origin_ref=environment_origin,
                destination_ref=destination_id,
                base_hours=reference_route_hours,
            )
            departure_environment = environment_snapshot(
                self.repository,
                world_time=str(current_time),
                location_ref=environment_origin,
            )
        except (FileNotFoundError, TypeError, ValueError, KeyError) as exc:
            raise CommandRejectedError("environment_projection_invalid") from exc

        hours = (
            Decimal(str(reference_days))
            * Decimal(24)
            * Decimal(str(status_multiplier))
            * Decimal(environment_factor)
            / Decimal(1000)
            / speed
        )
        seconds = int((hours * Decimal(3600)).to_integral_value(rounding=ROUND_CEILING))
        arrival = current_time.add_seconds(seconds)
        base = self._time_spanning_base(command, meta, current_time, target_time=arrival)
        if base.result.get("interrupted"):
            raise CommandRejectedError("time_boundary_requires_domain_settlement")
        if CampaignTime.parse(base.result["world_time"]) != arrival:
            raise CommandRejectedError("travel_time_settlement_incomplete")

        traveler_paths = []
        for traveler_ref, (path, record) in travelers.items():
            life = record.get("life_course_state")
            if not isinstance(life, dict):
                raise CommandRejectedError("traveler_location_history_invalid")
            history = life.get("location_history")
            if not isinstance(history, list) or not history:
                raise CommandRejectedError("traveler_location_history_invalid")
            history.append({"at": str(arrival), "location_id": destination_id, "reason": f"completed deterministic party travel via {route_id}"})
            history[:] = history[-self.MAX_LOCATION_HISTORY:]
            changes = life.get("location_changes")
            if changes is None:
                life["location_changes"] = 1
            elif isinstance(changes, bool) or not isinstance(changes, int) or changes < 0:
                raise CommandRejectedError("traveler_location_history_invalid")
            else:
                life["location_changes"] = changes + 1
            record["current_location_id"] = destination_id
            traveler_paths.append(path)
        scene = json.loads(base.writes[self.scene_path].decode("utf-8"))
        scene["location_id"] = destination_id
        scene["scene_summary"] = f"Travel party arrives at {destination_id} at {arrival} via {route_id}; travel consumed {seconds} seconds."
        scene["decision_required"] = "Choose the next consequential action at the destination."
        world_events = self._world_events_after(base)
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="travel_completed",
            at=arrival,
            host_refs=((current_location if local_travel else origin_anchor), destination_id),
            actor_refs=tuple(traveler_refs),
            place_refs=((current_location if local_travel else origin_anchor), destination_id),
            causal_refs=((mission_id,) if mission_id is not None else ()),
            affected_owner_refs=tuple(sorted((*traveler_paths, self.scene_path))),
            material_consequence_refs=tuple(f"location:{traveler_ref}:{destination_id}" for traveler_ref in traveler_refs),
            audience_refs=(command.actor_id,),
            route_refs=(route_id,),
            reducer_ref="game/data/mechanics/travel.json",
        )
        writes = dict(base.writes)
        writes[self.meta_path] = _json_bytes(self._meta_after(meta, command, world_time=arrival))
        writes[self.scene_path] = _json_bytes(scene)
        for _traveler_ref, (path, record) in travelers.items():
            writes[path] = _json_bytes(record)
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("travel write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=arrival)
            for traveler_ref, (path, _record) in travelers.items():
                if overlay.read_json(path).get("current_location_id") != destination_id:
                    raise ValueError(f"travel destination was not persisted for {traveler_ref}")
            if overlay.read_json(self.scene_path).get("location_id") != destination_id:
                raise ValueError("scene and traveler location diverged")
            self._scheduler_from_reader(overlay)

        return _BuiltPlan(
            code="travel_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "route_id": route_id,
                "origin_id": (current_location if local_travel else origin_anchor),
                "destination_id": destination_id,
                "traveler_refs": list(traveler_refs),
                "travel_seconds": seconds,
                "arrival_time": str(arrival),
                "mission_ref": mission_id,
                "party_authority_basis": party_authority_basis,
                "environment_travel_factor_milli": environment_factor,
                "departure_weather_block_ref": departure_environment["weather_block_ref"],
                "semantic_event_id": event_id,
            },
            validator=validate,
        )


__all__ = ["CampaignCommandPlanner"]
