"""Extracted semantic command domain from the repository command planner.

The mixin owns domain reducers; orchestration, transaction framing, shared owner
resolution, and causal scheduler settlement remain on RepositoryCommandPlanner.
"""

from __future__ import annotations

import copy
import json
import re
from decimal import (
    Decimal,
    ROUND_CEILING,
)
from typing import (
    Any,
    Dict,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.core import (
    _BuiltPlan,
    _OwnerResolutionCache,
    _declared_payload,
    _exact_payload,
    _json_bytes,
    _stable_id,
)
from shinobi_runtime.commands.paths import (
    POPULATION_REGISTRY_PATH as _POPULATION_REGISTRY_PATH,
    ROUTES_PATH as _ROUTES_PATH,
    TRAVEL_MECHANICS_PATH as _TRAVEL_MECHANICS_PATH,
    RECRUITMENT_POLICIES_PATH as _RECRUITMENT_POLICIES_PATH,
)
from shinobi_runtime.membership_routes import team_refs_for_member
from shinobi_runtime.reducers import (
    PopulationPool,
    PopulationTransfer,
    apply_transfer,
    neutral_proportional_selection,
    materialize_member,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.domain import LocationGraph
from shinobi_runtime.security.route_access import actor_knows_route
from shinobi_runtime.people.profiles import numeric_map, profile_entry_for
from shinobi_runtime.tx.manifest import TransactionManifest


_GOVERNANCE_PATH = "state/reg/governance.json"

class PopulationTravelCommandsMixin:
    MAX_POPULATION_TRANSFER_HISTORY = 512
    MAX_LOCATION_HISTORY = 64

    @classmethod
    def _trim_population_transfer_history(cls, transfers: list[Any]) -> None:
        if len(transfers) > cls.MAX_POPULATION_TRANSFER_HISTORY:
            del transfers[:-cls.MAX_POPULATION_TRANSFER_HISTORY]

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
        mission_owner = None
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

        player_path, player = travelers[command.actor_id]
        current_location = player.get("current_location_id")
        if not isinstance(current_location, str):
            raise CommandRejectedError("travel_origin_invalid")
        for traveler_ref, (_path, record) in travelers.items():
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
        speeds = []
        for traveler_ref, (_path, record) in travelers.items():
            martial = record.get("martial_skills")
            attributes = record.get("attributes")
            movement = martial.get("movement") if isinstance(martial, Mapping) else None
            endurance = attributes.get("endurance") if isinstance(attributes, Mapping) else None
            if any(isinstance(value, bool) or not isinstance(value, int) for value in (movement, endurance)):
                raise CommandRejectedError("travel_capability_invalid")
            speed = Decimal("0.65") + Decimal(movement) / Decimal(200) + Decimal(endurance) / Decimal(500)
            speeds.append(min(Decimal("1.80"), max(Decimal("0.50"), speed)))
        # A party travels at the pace of its slowest exact member.  This avoids
        # granting free movement to a slower companion merely because the
        # command actor is fast.
        speed = min(speeds)
        hours = Decimal(str(reference_days)) * Decimal(24) * Decimal(str(status_multiplier)) / speed
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
            history.append(
                {
                    "at": str(arrival),
                    "location_id": destination_id,
                    "reason": f"completed deterministic party travel via {route_id}",
                }
            )
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
        scene["scene_summary"] = (
            f"Travel party arrives at {destination_id} at {arrival} via {route_id}; travel consumed {seconds} seconds."
        )
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
            material_consequence_refs=tuple(
                f"location:{traveler_ref}:{destination_id}" for traveler_ref in traveler_refs
            ),
            audience_refs=(command.actor_id,),
            route_refs=(route_id,),
            reducer_ref="game/data/mechanics/travel.json",
        )
        writes = dict(base.writes)
        writes[self.meta_path] = _json_bytes(self._meta_after(meta, command, world_time=arrival))
        writes[self.scene_path] = _json_bytes(scene)
        for traveler_ref, (path, record) in travelers.items():
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
                "semantic_event_id": event_id,
            },
            validator=validate,
        )
    def _recruitment_policy(self, policy_ref: str) -> Mapping[str, Any]:
        try:
            registry = self.repository.read_json(_RECRUITMENT_POLICIES_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("recruitment_policy_registry_invalid") from exc
        policies = registry.get("policies") if isinstance(registry, Mapping) else None
        policy = policies.get(policy_ref) if isinstance(policies, Mapping) else None
        if not isinstance(policy, Mapping):
            raise CommandRejectedError("recruitment_policy_invalid")
        eligible = policy.get("eligible_source_categories")
        if not isinstance(eligible, list) or any(not isinstance(x, str) for x in eligible):
            raise CommandRejectedError("recruitment_policy_registry_invalid")
        return policy

    def _recruitment_background_profile(self, background_ref: str) -> Mapping[str, Any]:
        try:
            registry = self.repository.read_json(_RECRUITMENT_POLICIES_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("recruitment_policy_registry_invalid") from exc
        profiles = registry.get("background_profiles") if isinstance(registry, Mapping) else None
        profile = profiles.get(background_ref) if isinstance(profiles, Mapping) else None
        if not isinstance(profile, Mapping) or not isinstance(profile.get("population_key"), str):
            raise CommandRejectedError("recruitment_background_invalid")
        return profile

    def _recruitment_scout_quality(self, scout_ref: Optional[str], authority_ref: str) -> int:
        if scout_ref is None:
            return 0
        scout_ref = _stable_id(scout_ref, "recruitment_scout_invalid")
        try:
            _path, scout = self._resolve_actor_for_write(scout_ref)
        except CommandRejectedError as exc:
            raise CommandRejectedError("recruitment_scout_invalid") from exc
        if scout_ref != authority_ref:
            try:
                scout_team_refs = team_refs_for_member(self.repository, scout_ref)
            except ValueError as exc:
                raise CommandRejectedError("membership_routes_invalid") from exc
            authorized = any(
                self._team_command_authorizes(authority_ref, scout_ref, team_ref)
                for team_ref in scout_team_refs
            )
            if not authorized:
                raise CommandRejectedError("recruitment_scout_not_authorized")
        attrs = scout.get("attributes") if isinstance(scout.get("attributes"), Mapping) else {}
        ops = scout.get("operational_skills") if isinstance(scout.get("operational_skills"), Mapping) else {}
        values = []
        for container, key in ((attrs, "awareness"), (ops, "investigation"), (ops, "tracking")):
            value = container.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                values.append(max(0, min(200, value)))
        return sum(values) // len(values) if values else 0

    def _background_force_capability(
        self, force: Mapping[str, Any], profile: Optional[Mapping[str, Any]], *, scout_quality: int
    ) -> Dict[str, Any]:
        reserve = force.get("reserve_capability")
        baseline = reserve.get("training_or_instruction") if isinstance(reserve, Mapping) else None
        if not isinstance(baseline, Mapping):
            baseline = next((row for row in (reserve or {}).values() if isinstance(row, Mapping)), None) if isinstance(reserve, Mapping) else None
        if not isinstance(baseline, Mapping):
            raise CommandRejectedError("force_reserve_capability_invalid")
        state = self._reserve_capability_state(baseline)
        projection = profile.get("force_projection") if isinstance(profile, Mapping) else None
        if isinstance(projection, Mapping):
            f_offsets = projection.get("fundamental_offsets")
            m_offsets = projection.get("method_offsets")
            if isinstance(f_offsets, Mapping):
                for key, offset in f_offsets.items():
                    if key in state["fundamentals"] and isinstance(offset, int) and not isinstance(offset, bool):
                        state["fundamentals"][key] = max(0, min(200, state["fundamentals"][key] + offset))
            if isinstance(m_offsets, Mapping):
                for key, offset in m_offsets.items():
                    if key in state["methods"] and isinstance(offset, int) and not isinstance(offset, bool):
                        state["methods"][key] = max(0, min(200, state["methods"][key] + offset))
        distribution = profile.get("distribution") if isinstance(profile, Mapping) else None
        if isinstance(distribution, Mapping):
            spread = distribution.get("spread")
            if isinstance(spread, int) and not isinstance(spread, bool):
                state["spread"] = max(1, min(50, spread))
        # Scouting selects a somewhat stronger tail of the same eligible background.
        # It cannot create specialists or reveal/reroll individual hidden stats.
        selection_lift = min(8, max(0, scout_quality) // 30)
        if selection_lift and isinstance(projection, Mapping):
            for group in ("fundamentals", "methods"):
                offsets = projection.get("fundamental_offsets" if group == "fundamentals" else "method_offsets")
                if not isinstance(offsets, Mapping):
                    continue
                for key, offset in offsets.items():
                    if isinstance(offset, int) and offset > 0 and key in state[group]:
                        state[group][key] = min(200, state[group][key] + selection_lift)
        return state

    @staticmethod
    def _background_numeric_distributions(
        profile: Optional[Mapping[str, Any]],
        *,
        count: int,
        capability_state: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Return schema-native sufficient statistics for one recruited background.

        Covariance/provenance remain in the static background policy addressed by
        ``background_ref``.  The transfer receipt stores only numeric sufficient
        statistics so it remains compact and schema-compatible.
        """
        if not isinstance(profile, Mapping) or count <= 0:
            return {}
        distribution = profile.get("distribution")
        spread = distribution.get("spread", 14) if isinstance(distribution, Mapping) else 14
        if isinstance(spread, bool) or not isinstance(spread, int):
            spread = 14
        spread = max(1, min(50, spread))

        out: Dict[str, Dict[str, Any]] = {}
        projection = profile.get("force_projection")
        if isinstance(capability_state, Mapping) and isinstance(projection, Mapping):
            groups = (
                ("fundamentals", projection.get("fundamental_offsets")),
                ("methods", projection.get("method_offsets")),
            )
            for group_name, offsets in groups:
                values = capability_state.get(group_name)
                if not isinstance(values, Mapping) or not isinstance(offsets, Mapping):
                    continue
                for key in sorted(offsets):
                    mean = values.get(key)
                    if isinstance(mean, bool) or not isinstance(mean, int):
                        continue
                    out[f"combat.{group_name}.{key}"] = {
                        "count": count,
                        "mean": mean,
                        "sd": spread,
                        "min": max(0, mean - 2 * spread),
                        "max": min(200, mean + 2 * spread),
                    }
            if out:
                return out

        mean_offsets = distribution.get("mean_offsets") if isinstance(distribution, Mapping) else None
        if isinstance(mean_offsets, Mapping):
            for key, mean in sorted(mean_offsets.items()):
                if isinstance(mean, bool) or not isinstance(mean, (int, float)):
                    continue
                out[f"background_offset.{key}"] = {
                    "count": count,
                    "mean": mean,
                    "sd": spread,
                    "min": max(-200, mean - 2 * spread),
                    "max": min(200, mean + 2 * spread),
                }
        return out

    @staticmethod
    def _pool_reducer_view(pool_id: str, record: Mapping[str, Any]) -> PopulationPool:
        count = record.get("count")
        profile = record.get("profile")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise CommandRejectedError("population_pool_invalid")
        if not isinstance(profile, Mapping):
            raise CommandRejectedError("population_pool_invalid")
        raw_dimensions = profile.get("dimension_counts")
        if isinstance(raw_dimensions, Mapping) and raw_dimensions:
            dimensions = {
                str(name): dict(categories)
                for name, categories in raw_dimensions.items()
                if isinstance(categories, Mapping)
            }
            if len(dimensions) != len(raw_dimensions):
                raise CommandRejectedError("population_pool_invalid")
        else:
            categories = profile.get("category_counts")
            if not isinstance(categories, Mapping):
                raise CommandRejectedError("population_pool_invalid")
            dimensions = {"category": dict(categories or {"all": count})}
        try:
            return PopulationPool(
                pool_id=pool_id,
                total=count,
                dimensions=dimensions,
            )
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("population_pool_invalid") from exc
    def _population_move(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
        *,
        recruitment: bool,
    ) -> _BuiltPlan:
        keys = (
            ("source_pool_id", "destination_pool_id", "requested_count", "policy_ref", "authority_ref")
            if recruitment
            else ("source_pool_id", "destination_pool_id", "count", "authority_ref")
        )
        if recruitment:
            _declared_payload(command.payload, command.command_type)
        else:
            _exact_payload(command.payload, keys, command.command_type)
        source_id = _stable_id(command.payload["source_pool_id"], "population_source_invalid")
        destination_id = _stable_id(command.payload["destination_pool_id"], "population_destination_invalid")
        authority_ref = _stable_id(command.payload["authority_ref"], "population_authority_invalid")
        if authority_ref != command.actor_id:
            raise CommandRejectedError("population_authority_actor_mismatch")
        if source_id == destination_id:
            raise CommandRejectedError("population_transfer_same_pool")
        policy = None
        if recruitment:
            requested = command.payload["requested_count"]
            if isinstance(requested, bool) or not isinstance(requested, int) or requested <= 0:
                raise CommandRejectedError("recruitment_counts_invalid")
            policy_ref = _stable_id(command.payload["policy_ref"], "recruitment_policy_invalid", prefix="recruitment.")
            policy = self._recruitment_policy(policy_ref)
            background_raw = command.payload.get("background_ref")
            background_ref = None if background_raw is None else _stable_id(background_raw, "recruitment_background_invalid", prefix="background.")
            background_profile = None if background_ref is None else self._recruitment_background_profile(background_ref)
            scout_raw = command.payload.get("scout_ref")
            scout_ref = None if scout_raw is None else _stable_id(scout_raw, "recruitment_scout_invalid")
            scout_quality = self._recruitment_scout_quality(scout_ref, authority_ref)
        else:
            count = command.payload["count"]
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                raise CommandRejectedError("population_transfer_count_invalid")
            requested = count
            policy_ref = None
            background_ref = None
            background_profile = None
            scout_ref = None
            scout_quality = 0

        try:
            registry = copy.deepcopy(self.repository.read_json(_POPULATION_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("population_registry_invalid") from exc
        pools = registry.get("pools") if isinstance(registry, dict) else None
        if not isinstance(pools, dict):
            raise CommandRejectedError("population_registry_invalid")
        source_record = pools.get(source_id)
        destination_record = pools.get(destination_id)
        if not isinstance(source_record, dict) or not isinstance(destination_record, dict):
            raise CommandRejectedError("population_pool_not_found")

        source_owner = source_record.get("owner_ref")
        if not isinstance(source_owner, str):
            raise CommandRejectedError("population_pool_owner_invalid")
        authority = self._domain_authority().owner_leadership(
            holder_ref=authority_ref,
            owner_ref=source_owner,
        )
        if not authority.allowed:
            raise CommandRejectedError("population_transfer_not_authorized")

        recruitment_jurisdiction_ref = None
        if recruitment:
            try:
                governance = self.repository.read_json(_GOVERNANCE_PATH)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("governance_registry_invalid") from exc
            jurisdictions = governance.get("jurisdictions") if isinstance(governance, Mapping) else None
            if not isinstance(jurisdictions, Mapping):
                raise CommandRejectedError("governance_registry_invalid")
            governed = [
                (ref, row) for ref, row in jurisdictions.items()
                if isinstance(ref, str) and isinstance(row, Mapping) and row.get("population_pool_ref") == source_id
            ]
            if len(governed) > 1:
                raise CommandRejectedError("governance_population_jurisdiction_ambiguous")
            if governed:
                recruitment_jurisdiction_ref, jurisdiction = governed[0]
                if jurisdiction.get("recruitment_rights") is not True:
                    raise CommandRejectedError("governance_recruitment_rights_denied")

        source = self._pool_reducer_view(source_id, source_record)
        destination = self._pool_reducer_view(destination_id, destination_record)
        source_representation = source_record.get("representation")
        destination_representation = destination_record.get("representation")
        if not isinstance(source_representation, Mapping) or not isinstance(destination_representation, Mapping):
            raise CommandRejectedError("population_representation_invalid")
        source_anonymous = source_representation.get("anonymous_count")
        destination_anonymous = destination_representation.get("anonymous_count")
        if (
            isinstance(source_anonymous, bool) or not isinstance(source_anonymous, int) or source_anonymous < 0
            or isinstance(destination_anonymous, bool) or not isinstance(destination_anonymous, int) or destination_anonymous < 0
            or source_anonymous + int(source_representation.get("rostered_count", -1)) != source.total
            or destination_anonymous + int(destination_representation.get("rostered_count", -1)) != destination.total
        ):
            raise CommandRejectedError("population_representation_invalid")
        source_category = source_record.get("category")
        if recruitment:
            eligible = policy.get("eligible_source_categories") if isinstance(policy, Mapping) else None
            if source_category not in eligible:
                raise CommandRejectedError("recruitment_eligibility_invalid")
            background_available = source_anonymous
            if background_profile is not None:
                background_key = background_profile.get("population_key")
                background_counts = source.dimensions.get("background")
                if not isinstance(background_key, str) or not isinstance(background_counts, Mapping):
                    raise CommandRejectedError("recruitment_background_unavailable")
                bucket = background_counts.get(background_key, 0)
                if isinstance(bucket, bool) or not isinstance(bucket, int) or bucket < 0:
                    raise CommandRejectedError("population_pool_invalid")
                background_available = bucket
            accepted = min(requested, source_anonymous, background_available)
            if accepted <= 0:
                raise CommandRejectedError("recruitment_eligible_source_insufficient")
            count = accepted
            selection_mode = str(policy.get("selection_mode", "neutral_proportional"))
        else:
            if requested > source_anonymous:
                raise CommandRejectedError("population_source_insufficient")
            accepted = requested
            count = requested
            selection_mode = "neutral_proportional"
        try:
            selected = neutral_proportional_selection(source, count)
            if recruitment and background_profile is not None:
                background_key = str(background_profile["population_key"])
                selected["background"] = {background_key: count}
            transfer_id = (("recruitment." if recruitment else "transfer.") + command.digest[:24])
            transfer = PopulationTransfer(
                transfer_id=transfer_id,
                source_pool_id=source_id,
                destination_pool_id=destination_id,
                count=count,
                selected_dimensions=selected,
                selection_mode=("explicit_selection" if recruitment else selection_mode),
            )
            source_after, destination_after = apply_transfer(source, destination, transfer)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("population_transfer_invalid") from exc

        source_record["count"] = source_after.total
        source_record["profile"]["dimension_counts"] = {
            name: dict(values) for name, values in source_after.dimensions.items()
        }
        source_record["profile"]["category_counts"] = {
            str(source_record.get("category", "population")): source_after.total
        }
        source_record["last_changed_at"] = str(current_time)
        source_record["status"] = "exhausted" if source_after.total == 0 else "active"
        destination_record["count"] = destination_after.total
        destination_record["profile"]["dimension_counts"] = {
            name: dict(values) for name, values in destination_after.dimensions.items()
        }
        destination_record["profile"]["category_counts"] = {
            str(destination_record.get("category", "population")): destination_after.total
        }
        destination_record["last_changed_at"] = str(current_time)
        destination_record["status"] = "active" if destination_after.total > 0 else destination_record.get("status", "active")
        source_record["representation"]["anonymous_count"] = source_anonymous - count
        destination_record["representation"]["anonymous_count"] = destination_anonymous + count

        # A population pool that is the physical census owner of a force must
        # update that force in the same transaction.  Merely linking a pool to a
        # force does not imply the pool is counted in force.total; population_pool_id
        # is the exact conservation relation.
        force_writes: Dict[str, Dict[str, Any]] = {}
        accepted_numeric_distributions = (
            self._background_numeric_distributions(background_profile, count=count)
            if recruitment and background_profile is not None
            else {}
        )

        def physical_force(record: Mapping[str, Any], pool_id: str) -> Optional[Tuple[str, Dict[str, Any]]]:
            force_ref = record.get("linked_force_ref")
            if not isinstance(force_ref, str) or not force_ref:
                return None
            try:
                force_path, _digest, force_view = self._resolve_covered_owner_view(
                    force_ref, cache=_OwnerResolutionCache()
                )
            except CommandRejectedError as exc:
                raise CommandRejectedError("population_linked_force_invalid") from exc
            if not isinstance(force_view, Mapping) or force_view.get("schema") != "force":
                raise CommandRejectedError("population_linked_force_invalid")
            if force_view.get("population_pool_id") != pool_id:
                return None
            force = force_writes.get(force_path)
            if force is None:
                force = copy.deepcopy(dict(force_view))
                force_writes[force_path] = force
            return force_path, force

        source_force = physical_force(source_record, source_id)
        destination_force = physical_force(destination_record, destination_id)
        if source_force is not None:
            _source_force_path, force = source_force
            if force.get("total") != source.total:
                raise CommandRejectedError("population_linked_force_conservation_failed")
            availability = force.get("availability")
            if not isinstance(availability, dict):
                raise CommandRejectedError("population_linked_force_invalid")
            reserved = self._reserved_availability_claims(str(force.get("id")))
            remaining = count
            for source_class in (
                "mobilizable_30d", "mobilizable_7d", "ready_24h",
                "training_or_instruction", "essential_fixed_duty",
            ):
                current = availability.get(source_class)
                if not isinstance(current, int) or isinstance(current, bool) or current <= 0:
                    continue
                free = max(0, current - reserved.get(source_class, 0))
                if free <= 0:
                    continue
                moved = min(free, remaining)
                self._reserve_draw(force, source_class, moved)
                availability[source_class] = current - moved
                remaining -= moved
                if remaining == 0:
                    break
            if remaining:
                raise CommandRejectedError("population_linked_force_unassigned_personnel_insufficient")
            force["total"] = int(force.get("total", 0)) - count
            self._validate_reserve_counts(force)
            if force["total"] != source_after.total or sum(availability.values()) != force["total"]:
                raise CommandRejectedError("population_linked_force_conservation_failed")

        if destination_force is not None:
            _destination_force_path, force = destination_force
            # If source and destination somehow route to the same physical force,
            # validate against its already-adjusted total rather than duplicating people.
            expected_before = destination.total
            if source_force is not None and source_force[1] is force:
                expected_before -= count
            if force.get("total") != expected_before:
                raise CommandRejectedError("population_linked_force_conservation_failed")
            availability = force.get("availability")
            if not isinstance(availability, dict):
                raise CommandRejectedError("population_linked_force_invalid")
            incoming_state = self._background_force_capability(
                force, background_profile if recruitment else None, scout_quality=scout_quality
            )
            if recruitment and background_profile is not None:
                accepted_numeric_distributions = self._background_numeric_distributions(
                    background_profile, count=count, capability_state=incoming_state
                )
            training = availability.get("training_or_instruction")
            if isinstance(training, bool) or not isinstance(training, int) or training < 0:
                raise CommandRejectedError("population_linked_force_invalid")
            availability["training_or_instruction"] = training + count
            self._reserve_add(force, "training_or_instruction", incoming_state, count)
            force["total"] = int(force.get("total", 0)) + count
            self._validate_reserve_counts(force)
            if force["total"] != destination_after.total or sum(availability.values()) != force["total"]:
                raise CommandRejectedError("population_linked_force_conservation_failed")

        transfer_record = {
            "id": transfer_id,
            "at": str(current_time),
            "source_pool_id": source_id,
            "destination_ref": destination_id,
            "requested_count": requested,
            "accepted": accepted,
            "rejected": requested - accepted,
            "authority_ref": authority_ref,
            "authority_basis": authority.basis,
            "policy_ref": policy_ref,
            "governance_jurisdiction_ref": recruitment_jurisdiction_ref,
            "method": (
                "policy_background_conserved_selection"
                if recruitment and background_ref is not None
                else ("policy_eligible_proportional" if recruitment else "neutral_proportional")
            ),
            "accepted_profile": {
                "numeric_distributions": accepted_numeric_distributions,
                "category_counts": {str(source_category or "population"): count},
                "dimension_counts": {name: dict(values) for name, values in selected.items()},
                "tags": (["recruitment", background_ref] if recruitment and background_ref is not None else ["recruitment" if recruitment else "population_transfer"]),
            },
            "materialized_person_ids": [],
            "source_removed": count,
            "destination_added": count,
            "selection_note": (
                (
                    "Runtime-derived background selection from a conserved eligible bucket; shortage partially fulfills and scouting can only select within that bucket."
                    if background_ref is not None
                    else "Runtime-derived acceptance from a conserved eligible source pool; caller supplied requested slots, not accepted results."
                )
                if recruitment
                else "Deterministic neutral proportional transfer from a conserved source pool."
            ),
        }
        transfers = registry.get("transfers")
        if not isinstance(transfers, list):
            raise CommandRejectedError("population_registry_invalid")
        transfers.append(transfer_record)
        self._trim_population_transfer_history(transfers)
        world_events = self._world_events()
        kind = "recruitment_resolved" if recruitment else "population_transferred"
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind=kind,
            at=current_time,
            host_refs=(source_record.get("owner_ref"), destination_record.get("owner_ref")),
            actor_refs=(authority_ref,),
            affected_owner_refs=tuple(sorted((_POPULATION_REGISTRY_PATH, *force_writes.keys()))),
            material_consequence_refs=(
                f"population:{source_id}:-{count}",
                f"population:{destination_id}:+{count}",
            ),
            audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.reducers.population.apply_transfer",
        )
        scene = copy.deepcopy(self._scene_base(current_time))
        scene["scene_summary"] = f"Population transaction {transfer_id} conserves {count} people from {source_id} to {destination_id}."
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            self.scene_path: _json_bytes(scene),
            _POPULATION_REGISTRY_PATH: _json_bytes(registry),
            **{path: _json_bytes(record) for path, record in force_writes.items()},
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("population transfer write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged = overlay.read_json(_POPULATION_REGISTRY_PATH)
            before_total = source.total + destination.total
            after_total = staged["pools"][source_id]["count"] + staged["pools"][destination_id]["count"]
            if before_total != after_total:
                raise ValueError("population transfer violated headcount conservation")
            if staged["transfers"][-1]["id"] != transfer_id:
                raise ValueError("population transfer receipt missing")

        return _BuiltPlan(
            code=("recruitment_resolution_ready" if recruitment else "population_transfer_ready"),
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "transfer_id": transfer_id,
                "source_pool_id": source_id,
                "destination_pool_id": destination_id,
                "requested_count": requested,
                "accepted": accepted,
                "rejected": requested - accepted,
                "authority_basis": authority.basis,
                "policy_ref": policy_ref,
                "governance_jurisdiction_ref": recruitment_jurisdiction_ref,
                "background_ref": background_ref,
                "scout_ref": scout_ref,
                "selection_quality_tier": (None if scout_ref is None else min(5, scout_quality // 35)),
                "semantic_event_id": event_id,
            },
            validator=validate,
        )
    def _population_transfer(self, command, meta, current_time) -> _BuiltPlan:
        return self._population_move(command, meta, current_time, recruitment=False)
    def _recruitment_resolution(self, command, meta, current_time) -> _BuiltPlan:
        return self._population_move(command, meta, current_time, recruitment=True)
    @staticmethod
    def _exactify_house_profile(
        registry: Mapping[str, Any],
        *,
        person_ref: str,
        core: Mapping[str, Any],
        profile: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Build an exact component exclusively from already-saved lite facts.

        Exactification is representation deepening, not generation.  Every
        numerical capability copied here is owned by the persistent House
        profile; values that did not previously exist remain absent/defaulted
        rather than being rerolled from neighboring characters.
        """
        values = numeric_map(registry, profile)

        def integer(path: str, default: Optional[int] = None) -> Optional[int]:
            value = values.get(path)
            if value is None:
                return default
            return max(0, min(200, int(round(value))))

        def numeric(path: str, default: Optional[float] = None) -> Optional[float]:
            value = values.get(path)
            return default if value is None else float(value)

        categories = profile.get("category_values")
        institutional = profile.get("institutional_progression")
        if not isinstance(categories, Mapping) or not isinstance(institutional, Mapping):
            raise CommandRejectedError("person_exactification_profile_invalid")

        frame_values = categories.get("frame")
        frame = frame_values[0] if isinstance(frame_values, list) and frame_values and isinstance(frame_values[0], str) else "average"
        standing = institutional.get("standing")
        if not isinstance(standing, str) or not standing:
            standing = "house_member"
        method_mastery = institutional.get("method_mastery")
        if not isinstance(method_mastery, Mapping):
            method_mastery = {}
        packages = institutional.get("training_package_refs")
        if not isinstance(packages, list):
            packages = []

        attributes = {
            key: integer(f"stats.attributes.{key}", 0)
            for key in ("agility", "awareness", "composure", "coordination", "endurance", "intelligence", "presence", "strength", "toughness")
        }
        chakra_dimensions = {
            key: integer(f"stats.chakra_dimensions.{key}", 0)
            for key in ("casting_stability", "control", "efficiency", "hand_seal_speed", "multitasking", "output", "sensing", "suppression")
        }
        martial_skills = {
            key: integer(f"stats.martial_skills.{key}", 0)
            for key in ("bow", "grappling", "heavy_weapon", "movement", "polearm", "staff", "stealth", "sword", "thrown_tools", "unarmed")
        }
        operational_skills = {
            key: integer(f"stats.operational_skills.{key}", 0)
            for key in ("infiltration", "investigation", "leadership", "medicine", "survival", "tactics", "team_coordination", "tracking", "traps")
        }
        domain_proficiencies = {
            key: integer(f"stats.domain_proficiencies.{key}", 0)
            for key in ("barrier", "genjutsu", "medical", "sealing", "wind")
        }
        aptitude = {
            key: integer(f"aptitude.{key}", 0)
            for key in (
                "academic_learning", "chakra_learning", "genjutsu_learning", "medical_learning",
                "nature_transformation_learning", "physical_learning", "sensory_learning",
                "social_learning", "tactical_learning", "technical_learning",
            )
        }
        aptitude["source"] = "persistent_house_individual_lite_profile"

        resources = {
            "chakra": {
                "capacity": integer("stats.resources.chakra.capacity", 0),
                "current": integer("stats.resources.chakra.current", 0),
            },
            "fatigue": {
                "capacity": integer("stats.resources.fatigue.capacity", 0),
                "current": integer("stats.resources.fatigue.current", 0),
            },
            "health": {
                "capacity": integer("stats.resources.health.capacity", 0),
                "current": integer("stats.resources.health.current", 0),
            },
            "strain": {
                "safe_capacity": integer("stats.resources.strain.safe_capacity", 0),
                "current": integer("stats.resources.strain.current", 0),
            },
        }
        exact = {
            "schema": "shinobi_character",
            "owner_id": person_ref,
            "owner_type": "character",
            "name": str(core.get("name", person_ref)),
            "birth_date": core.get("birth_date"),
            "birth_date_source": core.get("birth_date_source"),
            "body": {
                "adult_height_cm": numeric("body.adult_height_cm", 170.0),
                "growth_end_age": integer("body.growth_end_age", 18),
                "current_weight_kg": numeric("body.current_weight_kg", 65.0),
                "frame": frame,
                "source": "persistent_house_individual_lite_profile",
            },
            "appearance": integer("appearance", 100),
            "appearance_source": "persistent_house_individual_lite_profile",
            "attributes": attributes,
            "chakra_dimensions": chakra_dimensions,
            "martial_skills": martial_skills,
            "operational_skills": operational_skills,
            "domain_proficiencies": domain_proficiencies,
            "aptitude": aptitude,
            "resources": resources,
            "repertoire": {
                "packages": [str(x) for x in packages if isinstance(x, str)],
                "package_mastery": {},
                "latent_or_locked_techniques": [str(x) for x in institutional.get("latent_or_locked_techniques", []) if isinstance(x, str)],
                "bloodlines": [],
                "method_mastery": {
                    str(key): max(0, min(200, int(value)))
                    for key, value in method_mastery.items()
                    if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)
                },
            },
            "condition": {"disabilities": [], "illness": None, "injuries": [], "poison": None, "readiness": "ready"},
            "career_state": {
                "current_rank_or_status": standing,
                "current_assignment_or_office": None,
                "promotion_eligible": False,
                "retirement_eligible": False,
            },
            "official_rank_or_status": standing,
            "current_location_id": core.get("location_ref"),
            "life_status": "active" if core.get("life_status") == "alive" else core.get("life_status"),
            "resolution_tier": "featured",
            "village_or_affiliation": core.get("affiliation_ref"),
            "institutional_training_packages": [str(x) for x in packages if isinstance(x, str)],
            "background": {"origin": core.get("origin"), "career": standing},
            "development": {"current_training": [], "weighted_adaptation": None},
            "method_deltas": {},
            "equipment_exceptions": {"add": [], "remove": []},
            "named_items": [],
            "roles": [str(x) for x in core.get("duty_tags", []) if isinstance(x, str)],
            "unique_methods": [],
        }
        return exact

    def _person_exactification(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _declared_payload(command.payload, command.command_type)
        person_ref = _stable_id(command.payload["person_ref"], "person_exactification_person_invalid")
        authority_ref = _stable_id(command.payload["authority_ref"], "person_exactification_authority_invalid")
        if command.actor_id != authority_ref:
            raise CommandRejectedError("person_exactification_authority_actor_mismatch")
        reason = command.payload.get("reason")
        if reason is not None and (not isinstance(reason, str) or not reason.strip() or len(reason) > 240):
            raise CommandRejectedError("person_exactification_reason_invalid")

        try:
            core_path, _digest, core_view = self._resolve_covered_owner_view(
                person_ref, cache=_OwnerResolutionCache()
            )
            registry = copy.deepcopy(self.repository.read_json(core_path))
        except (CommandRejectedError, FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("person_exactification_person_invalid") from exc
        if registry.get("schema") != "person-core-registry" or not isinstance(core_view, Mapping):
            raise CommandRejectedError("person_exactification_requires_rostered_person")
        people = registry.get("people")
        profiles = registry.get("profiles")
        core = people.get(person_ref) if isinstance(people, Mapping) else None
        if not isinstance(core, dict) or not isinstance(profiles, Mapping):
            raise CommandRejectedError("person_exactification_requires_profile")
        try:
            profile = profile_entry_for(registry, person_ref)
        except ValueError as exc:
            raise CommandRejectedError("person_exactification_profile_invalid") from exc
        if profile is None:
            raise CommandRejectedError("person_exactification_requires_profile")
        component_refs = core.get("component_refs")
        if not isinstance(component_refs, dict):
            raise CommandRejectedError("person_exactification_component_registry_invalid")
        if "profile.exact" in component_refs:
            raise CommandRejectedError("person_already_exact")
        owner_ref = registry.get("owner_ref")
        if not isinstance(owner_ref, str):
            raise CommandRejectedError("person_exactification_owner_invalid")
        authority = self._domain_authority().owner_leadership(holder_ref=authority_ref, owner_ref=owner_ref)
        if not authority.allowed:
            raise CommandRejectedError("person_exactification_not_authorized")

        exact_path = f"state/char/{person_ref}.json"
        try:
            self.repository.read_json(exact_path)
        except FileNotFoundError:
            pass
        else:
            raise CommandRejectedError("person_exactification_path_conflict")
        exact = self._exactify_house_profile(
            registry, person_ref=person_ref, core=core, profile=profile
        )
        component_refs["profile.exact"] = exact_path
        core["resolved_through"] = str(current_time)
        provenance = core.get("provenance")
        if isinstance(provenance, dict):
            provenance["materialized_at"] = str(current_time)

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="person_exactified",
            at=current_time,
            host_refs=(owner_ref,),
            actor_refs=(authority_ref,),
            affected_owner_refs=(core_path, exact_path),
            material_consequence_refs=(f"person:{person_ref}:representation:exact",),
            audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.commands.domains.population.person_exactification",
        )
        scene = copy.deepcopy(self._scene_base(current_time))
        scene["scene_summary"] = f"{person_ref} now carries exact mechanics without changing identity or population representation."
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            self.scene_path: _json_bytes(scene),
            core_path: _json_bytes(registry),
            exact_path: _json_bytes(exact),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        original_profile = numeric_map(registry, profile)

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("person exactification write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged_registry = overlay.read_json(core_path)
            staged_exact = overlay.read_json(exact_path)
            staged_profile = profile_entry_for(staged_registry, person_ref)
            if staged_profile is None or numeric_map(staged_registry, staged_profile) != original_profile:
                raise ValueError("person exactification rerolled established profile")
            staged_core = staged_registry.get("people", {}).get(person_ref, {})
            if staged_core.get("id") != person_ref or staged_core.get("component_refs", {}).get("profile.exact") != exact_path:
                raise ValueError("person exactification broke identity continuity")
            if staged_exact.get("owner_id") != person_ref or staged_exact.get("schema") != "shinobi_character":
                raise ValueError("person exactification component invalid")

        return _BuiltPlan(
            code="person_exactification_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "person_ref": person_ref,
                "exact_component_ref": exact_path,
                "identity_preserved": True,
                "population_delta": 0,
                "profile_rerolled": False,
                "semantic_event_id": event_id,
            },
            validator=validate,
        )

    def _person_materialization(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            (
                "source_pool_id", "authority_ref", "name", "aliases", "pronouns",
                "birth_date", "origin", "location_ref", "role_profile_ref", "identity_cues",
            ),
            command.command_type,
        )
        source_id = _stable_id(command.payload["source_pool_id"], "materialization_source_invalid", prefix="pool.")
        authority_ref = _stable_id(command.payload["authority_ref"], "materialization_authority_invalid")
        if authority_ref != command.actor_id:
            raise CommandRejectedError("materialization_authority_actor_mismatch")
        name = command.payload["name"]
        pronouns = command.payload["pronouns"]
        birth_date = command.payload["birth_date"]
        origin = command.payload["origin"]
        location_ref = _stable_id(command.payload["location_ref"], "materialization_location_invalid")
        role_profile_ref = _stable_id(command.payload["role_profile_ref"], "materialization_role_invalid")
        aliases = command.payload["aliases"]
        cues = command.payload["identity_cues"]
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise CommandRejectedError("materialization_identity_invalid")
        if not isinstance(pronouns, str) or not pronouns.strip() or len(pronouns) > 40:
            raise CommandRejectedError("materialization_identity_invalid")
        if not isinstance(origin, str) or not origin.strip() or len(origin) > 160:
            raise CommandRejectedError("materialization_identity_invalid")
        if not isinstance(birth_date, str) or not re.fullmatch(r"SE-[0-9]{4}-[0-9]{2}-[0-9]{2}", birth_date):
            raise CommandRejectedError("materialization_birth_date_invalid")
        if (
            not isinstance(aliases, Sequence) or isinstance(aliases, (str, bytes, bytearray))
            or any(not isinstance(v, str) or not v.strip() or len(v) > 120 for v in aliases)
            or len(aliases) != len(set(aliases))
            or len(aliases) > 8
        ):
            raise CommandRejectedError("materialization_identity_invalid")
        if not isinstance(cues, Mapping) or set(cues) != {"appearance", "temperament", "doctrine_expression"}:
            raise CommandRejectedError("materialization_identity_invalid")
        if any(not isinstance(cues[k], str) or not cues[k].strip() or len(cues[k]) > 500 for k in cues):
            raise CommandRejectedError("materialization_identity_invalid")

        try:
            registry = copy.deepcopy(self.repository.read_json(_POPULATION_REGISTRY_PATH))
            core_registry = copy.deepcopy(self.repository.read_json("state/person-core/world.json"))
            person_index = copy.deepcopy(self.repository.read_json("state/index/owners/person.json"))
            owner_index = copy.deepcopy(self.repository.read_json("state/index/owners.json"))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("materialization_registry_invalid") from exc
        pools = registry.get("pools") if isinstance(registry, dict) else None
        source_record = pools.get(source_id) if isinstance(pools, dict) else None
        if not isinstance(source_record, dict):
            raise CommandRejectedError("population_pool_not_found")
        source_owner = source_record.get("owner_ref")
        if not isinstance(source_owner, str):
            raise CommandRejectedError("population_pool_owner_invalid")
        authority = self._domain_authority().owner_leadership(holder_ref=authority_ref, owner_ref=source_owner)
        if not authority.allowed:
            raise CommandRejectedError("materialization_not_authorized")
        graph = LocationGraph(self.repository.read_json(_ROUTES_PATH))
        if graph.place(location_ref) is None and graph.anchor(location_ref) == location_ref:
            # Exact owner-backed places may be absent from the compact route graph; require them to resolve.
            try:
                self._resolve_covered_owner(location_ref, cache=_OwnerResolutionCache())
            except CommandRejectedError as exc:
                raise CommandRejectedError("materialization_location_invalid") from exc

        source = self._pool_reducer_view(source_id, source_record)
        representation = source_record.get("representation")
        if not isinstance(representation, Mapping):
            raise CommandRejectedError("population_representation_invalid")
        anonymous_count = representation.get("anonymous_count")
        rostered_count = representation.get("rostered_count")
        rostered_refs = representation.get("rostered_person_refs")
        if (
            isinstance(anonymous_count, bool) or not isinstance(anonymous_count, int) or anonymous_count <= 0
            or isinstance(rostered_count, bool) or not isinstance(rostered_count, int) or rostered_count < 0
            or not isinstance(rostered_refs, list)
            or anonymous_count + rostered_count != source.total
        ):
            raise CommandRejectedError("materialization_source_insufficient")
        try:
            source_after, selected = materialize_member(source)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("materialization_source_insufficient") from exc
        person_id = "person." + command.digest[:24]
        people = core_registry.get("people") if isinstance(core_registry, dict) else None
        index_owners = person_index.get("owners") if isinstance(person_index, dict) else None
        if not isinstance(people, dict) or not isinstance(index_owners, dict):
            raise CommandRejectedError("materialization_registry_invalid")
        if person_id in people or person_id in index_owners:
            raise CommandRejectedError("materialization_identity_conflict")

        core = {
            "id": person_id,
            "name": name.strip(),
            "aliases": list(aliases),
            "pronouns": pronouns.strip(),
            "birth_date": birth_date,
            "birth_date_source": "materialization_identity_input",
            "origin": origin.strip(),
            "life_status": "alive",
            "affiliation_ref": source_owner,
            "location_ref": location_ref,
            "cohort_ref": source_id,
            "cohort_slot": len(people),
            "role_profile_ref": role_profile_ref,
            "duty_tags": [],
            "resolved_through": str(current_time),
            "identity_cues": {k: cues[k].strip() for k in ("appearance", "temperament", "doctrine_expression")},
            "component_refs": {},
            "provenance": {
                "source_kind": "population_materialization",
                "source_ref": source_id,
                "materialized_at": str(current_time),
                "selection_method": "deterministic_neutral_proportional",
            },
        }
        people[person_id] = core
        index_owners[person_id] = "state/person-core/world.json"
        owner_index["owner_count"] = int(owner_index.get("owner_count", 0)) + 1

        # Representation upgrade only: the physical pool total and demographic
        # distributions remain unchanged because this human already existed.
        source_record["representation"]["anonymous_count"] = anonymous_count - 1
        source_record["representation"]["rostered_count"] = rostered_count + 1
        source_record["representation"]["rostered_person_refs"] = sorted([*rostered_refs, person_id])
        source_record["last_changed_at"] = str(current_time)
        transfer_id = "materialization." + command.digest[:24]
        registry["transfers"].append({
            "id": transfer_id,
            "at": str(current_time),
            "source_pool_id": source_id,
            "destination_ref": person_id,
            "requested_count": 1,
            "accepted": 1,
            "rejected": 0,
            "authority_ref": authority_ref,
            "authority_basis": authority.basis,
            "policy_ref": None,
            "method": "materialization_neutral_proportional",
            "accepted_profile": {
                "numeric_distributions": {},
                "category_counts": {str(source_record.get("category", "population")): 1},
                "dimension_counts": {name: dict(values) for name, values in selected.items()},
                "tags": ["materialization", "persistent_identity"],
            },
            "materialized_person_ids": [person_id],
            "source_removed": 0,
            "destination_added": 0,
            "selection_note": "One already-existing anonymous representation became a sparse persistent identity; physical population, capability, equipment, training, accomplishment, and relationships were unchanged.",
        })
        self._trim_population_transfer_history(registry["transfers"])

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events, command=command, kind="person_materialized", at=current_time,
            host_refs=(source_owner,), actor_refs=(authority_ref,),
            affected_owner_refs=(_POPULATION_REGISTRY_PATH, "state/person-core/world.json"),
            material_consequence_refs=(f"population_representation:{source_id}:anonymous-1", f"person:{person_id}:+1"),
            audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.reducers.population.materialize_member",
        )
        scene = copy.deepcopy(self._scene_base(current_time))
        scene["scene_summary"] = f"{person_id} is now represented as a persistent identity from {source_id}."
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            self.scene_path: _json_bytes(scene),
            _POPULATION_REGISTRY_PATH: _json_bytes(registry),
            "state/person-core/world.json": _json_bytes(core_registry),
            "state/index/owners/person.json": _json_bytes(person_index),
            "state/index/owners.json": _json_bytes(owner_index),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("person materialization write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged_pop = overlay.read_json(_POPULATION_REGISTRY_PATH)
            staged_core = overlay.read_json("state/person-core/world.json")
            staged_source = staged_pop["pools"][source_id]
            if staged_source["count"] != source.total:
                raise ValueError("materialization changed physical population")
            staged_rep = staged_source.get("representation", {})
            if staged_rep.get("anonymous_count") != anonymous_count - 1 or staged_rep.get("rostered_count") != rostered_count + 1:
                raise ValueError("materialization did not move exactly one representation slot")
            if person_id not in staged_rep.get("rostered_person_refs", []):
                raise ValueError("materialized identity missing from population representation")
            if person_id not in staged_core.get("people", {}):
                raise ValueError("materialized identity missing")
            if staged_pop["transfers"][-1]["materialized_person_ids"] != [person_id]:
                raise ValueError("materialization provenance receipt missing")

        return _BuiltPlan(
            code="person_materialization_ready", affected_refs=expected_paths, writes=writes,
            result={
                "command_type": command.command_type, "person_id": person_id,
                "source_pool_id": source_id, "selected_profile": {name: dict(values) for name, values in selected.items()},
                "semantic_event_id": event_id,
            }, validator=validate,
        )

