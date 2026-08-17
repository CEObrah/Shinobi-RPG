"""Strategic conflict, formation movement, and custody command domain.

These mechanics are isolated from the orchestration planner to keep large-war
state changes auditable without loading unrelated family, medical, or social
reducers.
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal, ROUND_CEILING
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache, _campaign_datetime, _json_bytes, _stable_id
from shinobi_runtime.domain import LocationGraph
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry, SchedulerHost, one_shot_event
from shinobi_runtime.sim.hosts import HostState
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.security import apply_route_security_detection, decode_security_information_records
from shinobi_runtime.diplomacy import (
    DIPLOMACY_PATH as _DIPLOMACY_PATH,
    border_route_restriction,
    client_state_access_basis,
    defense_obligation_specs,
    hostility_barrier,
)
from shinobi_runtime.tx.manifest import TransactionManifest
from shinobi_runtime.membership_routes import team_refs_for_assignment


from shinobi_runtime.commands.paths import (
    ROUTES_PATH as _ROUTES_PATH,
    TRAVEL_MECHANICS_PATH as _TRAVEL_MECHANICS_PATH,
    CONFLICT_MECHANICS_PATH as _CONFLICT_MECHANICS_PATH,
    CONFLICT_REGISTRY_PATH as _CONFLICT_REGISTRY_PATH,
    CUSTODY_REGISTRY_PATH as _CUSTODY_REGISTRY_PATH,
    POPULATION_REGISTRY_PATH as _POPULATION_REGISTRY_PATH,
    ASSIGNMENTS_PATH as _ASSIGNMENTS_PATH,
    COMMITMENT_REGISTRY_PATH as _COMMITMENT_REGISTRY_PATH,
)


class StrategicCommandsMixin:
    def _require_formation_diplomatic_access(
        self, *, force_ref: str, force_owner_ref: str, destination_ref: str, route_ref: str
    ) -> Optional[str]:
        """Enforce active military-access treaties for governed destinations.

        Governance is the territorial authority. Ungoverned/cold places retain the
        existing movement rules; once a jurisdiction exists, foreign formations
        need sovereign/garrison authority or an exact active access agreement.
        """
        try:
            governance = self.repository.read_json("state/reg/governance.json")
        except (FileNotFoundError, ValueError):
            return None
        rows = governance.get("jurisdictions") if isinstance(governance, Mapping) else None
        matches = [
            row for row in (rows.values() if isinstance(rows, Mapping) else ())
            if isinstance(row, Mapping) and row.get("place_ref") == destination_ref
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise CommandRejectedError("formation_movement_governance_ambiguous")
        jurisdiction = matches[0]
        sovereign = jurisdiction.get("sovereign_ref")
        administration = jurisdiction.get("administration_ref")
        garrison_force = jurisdiction.get("garrison_force_ref")
        if force_ref == garrison_force or force_owner_ref in (sovereign, administration):
            return f"jurisdiction:{jurisdiction.get('id')}:domestic"
        try:
            diplomacy = self.repository.read_json(_DIPLOMACY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("formation_movement_diplomacy_invalid") from exc
        if not isinstance(diplomacy, Mapping):
            raise CommandRejectedError("formation_movement_diplomacy_invalid")

        border = border_route_restriction(
            diplomacy,
            force_owner_ref=force_owner_ref,
            sovereign_ref=sovereign if isinstance(sovereign, str) else None,
            administration_ref=administration if isinstance(administration, str) else None,
            destination_ref=destination_ref,
            route_ref=route_ref,
        )
        if border is not None and not border[1]:
            raise CommandRejectedError("formation_movement_border_agreement_violation")

        client_basis = client_state_access_basis(
            diplomacy,
            force_owner_ref=force_owner_ref,
            sovereign_ref=sovereign if isinstance(sovereign, str) else None,
            administration_ref=administration if isinstance(administration, str) else None,
        )
        if client_basis is not None:
            return client_basis

        agreements = diplomacy.get("agreements") if isinstance(diplomacy, Mapping) else None
        for agreement_ref, agreement in sorted((agreements or {}).items()):
            if (
                not isinstance(agreement, Mapping)
                or agreement.get("status") != "active"
                or agreement.get("agreement_type") != "military_access"
            ):
                continue
            provisions = agreement.get("provisions")
            if not isinstance(provisions, Mapping):
                continue
            grantor = provisions.get("grantor_ref")
            grantee = provisions.get("grantee_ref")
            place_refs = provisions.get("place_refs")
            route_refs = provisions.get("route_refs")
            if grantor not in (sovereign, administration):
                continue
            if grantee not in (force_owner_ref, force_ref):
                continue
            if not isinstance(place_refs, list) or destination_ref not in place_refs:
                continue
            if isinstance(route_refs, list) and route_refs and route_ref not in route_refs:
                continue
            return f"agreement:{agreement_ref}"
        raise CommandRejectedError("formation_movement_diplomatic_access_required")

    def _formation_movement_resolution(
        self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime
    ) -> _BuiltPlan:
        spec = COMMAND_SPECS[command.command_type]
        allowed = set(spec.required_fields) | set(spec.optional_fields)
        if set(command.payload) - allowed or any(key not in command.payload for key in spec.required_fields):
            raise CommandRejectedError("formation_movement_resolution_payload_fields_invalid")
        formation_ref = _stable_id(command.payload.get("formation_ref"), "formation_ref_invalid", prefix="formation.")
        route_id = _stable_id(command.payload.get("route_id"), "formation_route_invalid", prefix="route_")
        destination_id = _stable_id(command.payload.get("destination_id"), "formation_destination_invalid", prefix="place.")
        posture = command.payload.get("movement_posture", "standard")
        if posture not in ("standard", "forced", "cautious"):
            raise CommandRejectedError("formation_movement_posture_invalid")
        attachment_raw = command.payload.get("operational_attachment_ref")
        attachment = None if attachment_raw is None else _stable_id(attachment_raw, "formation_attachment_invalid")

        formation_path, force_ref, formation_view = self._formation_by_id(formation_ref)
        formation = copy.deepcopy(dict(formation_view))
        current_location = formation.get("location_ref")
        personnel_total = formation.get("personnel_total")
        readiness = formation.get("readiness")
        cohesion = formation.get("cohesion")
        if (
            not isinstance(current_location, str)
            or isinstance(personnel_total, bool) or not isinstance(personnel_total, int) or personnel_total <= 0
            or isinstance(readiness, bool) or not isinstance(readiness, int)
            or isinstance(cohesion, bool) or not isinstance(cohesion, int)
        ):
            raise CommandRejectedError("formation_movement_state_invalid")

        cache = _OwnerResolutionCache()
        force_path, _force_digest, force_view = self._resolve_covered_owner_view(force_ref, cache=cache)
        if not isinstance(force_view, Mapping) or force_view.get("schema") != "force":
            raise CommandRejectedError("formation_force_unresolved")
        authority = self._domain_authority(cache=cache)
        grant = authority.force_grant(grantor_ref=command.actor_id, force_record=force_view)
        if grant.allowed:
            authority_basis = grant.basis
        else:
            decision = authority.force_command(
                commander_ref=command.actor_id,
                force_ref=force_ref,
                operational_attachment_ref=attachment or formation_ref,
                named_actor_refs=(),
                committed_count=personnel_total,
                effective_at=str(current_time),
            )
            if not decision.allowed:
                raise CommandRejectedError("formation_movement_not_authorized")
            authority_basis = decision.basis

        try:
            routes_record = self.repository.read_json(_ROUTES_PATH)
            mechanics = self.repository.read_json(_TRAVEL_MECHANICS_PATH)
            graph = LocationGraph(routes_record)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("formation_movement_registry_invalid") from exc
        origin_anchor = graph.anchor(current_location)
        route = next((row for row in graph.routes if isinstance(row, Mapping) and row.get("id") == route_id), None)
        if route is None:
            raise CommandRejectedError("formation_route_invalid")
        endpoints = (route.get("from"), route.get("to"))
        if origin_anchor not in endpoints or destination_id not in endpoints or destination_id == origin_anchor:
            raise CommandRejectedError("formation_route_endpoint_mismatch")
        reference_days = route.get("reference_travel_days")
        status_multipliers = mechanics.get("route_status_multipliers") if isinstance(mechanics, Mapping) else None
        formation_rules = mechanics.get("formation_travel") if isinstance(mechanics, Mapping) else None
        if (
            isinstance(reference_days, bool) or not isinstance(reference_days, (int, float)) or reference_days <= 0
            or not isinstance(status_multipliers, Mapping)
            or not isinstance(formation_rules, Mapping)
        ):
            raise CommandRejectedError("formation_movement_registry_invalid")
        route_multiplier = status_multipliers.get(route.get("status"))
        if isinstance(route_multiplier, bool) or not isinstance(route_multiplier, (int, float)):
            raise CommandRejectedError("formation_route_blocked")
        force_side_ref = force_view.get("owner_ref") if isinstance(force_view.get("owner_ref"), str) and force_view.get("owner_ref") else force_ref
        diplomatic_access_basis = self._require_formation_diplomatic_access(
            force_ref=force_ref, force_owner_ref=force_side_ref, destination_ref=destination_id, route_ref=route_id
        )
        route_multiplier = Decimal(str(route_multiplier)) * self._conflict_route_multiplier(
            route_id, traveler_side_ref=force_side_ref
        )
        size_bands = formation_rules.get("size_multipliers")
        posture_times = formation_rules.get("posture_time_multipliers")
        posture_readiness = formation_rules.get("posture_readiness_delta")
        if not isinstance(size_bands, list) or not isinstance(posture_times, Mapping) or not isinstance(posture_readiness, Mapping):
            raise CommandRejectedError("formation_movement_registry_invalid")
        size_multiplier = None
        for band in size_bands:
            if not isinstance(band, Mapping):
                continue
            maximum = band.get("max_personnel")
            multiplier = band.get("multiplier")
            if (
                isinstance(maximum, int) and not isinstance(maximum, bool)
                and isinstance(multiplier, (int, float)) and not isinstance(multiplier, bool)
                and personnel_total <= maximum
            ):
                size_multiplier = Decimal(str(multiplier))
                break
        posture_multiplier = posture_times.get(posture)
        readiness_delta = posture_readiness.get(posture)
        if (
            size_multiplier is None
            or isinstance(posture_multiplier, bool) or not isinstance(posture_multiplier, (int, float))
            or isinstance(readiness_delta, bool) or not isinstance(readiness_delta, int)
        ):
            raise CommandRejectedError("formation_movement_registry_invalid")
        coordination_speed = min(Decimal("1.05"), max(Decimal("0.60"), Decimal(readiness + cohesion) / Decimal(160)))
        hours = (
            Decimal(str(reference_days)) * Decimal(24) * Decimal(str(route_multiplier))
            * size_multiplier * Decimal(str(posture_multiplier)) / coordination_speed
        )
        seconds = int((hours * Decimal(3600)).to_integral_value(rounding=ROUND_CEILING))

        # Military formation movement consumes conserved aggregate field supply.
        # This remains scalable: we charge the formation as a whole rather than
        # materializing one ration/ammunition record per soldier.  Civil levies
        # without a registered military stock are intentionally outside this
        # rule until a governance/logistics owner supplies them.
        logistics_result = None
        stock_path = None
        stock = None
        stock_ref = "stock." + force_ref.replace(".", "_")
        try:
            stock_path, stock, stock_owner = self._stock_record(stock_ref)
        except CommandRejectedError as exc:
            if not force_ref.startswith("force.civil.") or str(exc) != "inventory_stock_unresolved":
                raise
        if stock_path is not None and stock is not None:
            if stock_owner != force_ref or stock.get("schema") != "shinobi-stock":
                raise CommandRejectedError("formation_movement_supply_stock_invalid")
            logistics = formation_rules.get("logistics_consumption")
            if not isinstance(logistics, Mapping):
                raise CommandRejectedError("formation_movement_registry_invalid")
            ration_rate = logistics.get("rations_person_days_per_travel_day_milli")
            water_rate = logistics.get("water_liters_per_person_day_milli")
            posture_rates = logistics.get("posture_consumption_milli")
            posture_rate = posture_rates.get(posture) if isinstance(posture_rates, Mapping) else None
            if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (ration_rate, water_rate, posture_rate)):
                raise CommandRejectedError("formation_movement_registry_invalid")
            person_days_milli = int((hours * Decimal(personnel_total) * Decimal(1000) / Decimal(24)).to_integral_value(rounding=ROUND_CEILING))
            ration_units = int((Decimal(person_days_milli) * Decimal(ration_rate) * Decimal(posture_rate) / Decimal(1000 * 1000 * 1000)).to_integral_value(rounding=ROUND_CEILING))
            water_liters = int((Decimal(person_days_milli) * Decimal(water_rate) * Decimal(posture_rate) / Decimal(1000 * 1000 * 1000)).to_integral_value(rounding=ROUND_CEILING))
            ration_units = max(1, ration_units)
            water_liters = max(1, water_liters)
            before_rations = stock.get("rations_days")
            before_water = stock.get("water_liters")
            if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (before_rations, before_water)):
                raise CommandRejectedError("formation_movement_supply_stock_invalid")
            if before_rations < ration_units or before_water < water_liters:
                raise CommandRejectedError("formation_movement_supplies_insufficient")
            stock["rations_days"] = before_rations - ration_units
            stock["water_liters"] = before_water - water_liters
            stock["last_reconciled_at"] = str(current_time)
            logistics_result = {
                "stock_ref": stock_ref,
                "rations_days_consumed": ration_units,
                "water_liters_consumed": water_liters,
                "rations_days_after": stock["rations_days"],
                "water_liters_after": stock["water_liters"],
            }

        arrival = current_time.add_seconds(seconds)
        base = self._time_spanning_base(command, meta, current_time, target_time=arrival)
        if base.result.get("interrupted") or CampaignTime.parse(base.result["world_time"]) != arrival:
            raise CommandRejectedError("time_boundary_requires_domain_settlement")

        registry = copy.deepcopy(self.repository.read_json(formation_path))
        rows = registry.get("formations") if isinstance(registry, dict) else None
        target = next((row for row in rows or [] if isinstance(row, dict) and row.get("id") == formation_ref), None)
        if not isinstance(target, dict):
            raise CommandRejectedError("formation_unresolved")
        target["location_ref"] = destination_id
        target["readiness"] = max(1, min(100, int(target.get("readiness", readiness)) + readiness_delta))
        person_writes: Dict[str, Dict[str, Any]] = {}
        embedded_refs: set[str] = set()
        moved_team_refs: list[str] = []
        moved_command_refs: list[str] = []
        try:
            assigned_team_refs = team_refs_for_assignment(self.repository, formation_ref)
        except ValueError as exc:
            raise CommandRejectedError("membership_routes_invalid") from exc
        for team_ref in assigned_team_refs:
            team_path, team = self._exact_team(team_ref)
            if team.get("status") != "active" or team.get("current_assignment_ref") != formation_ref:
                continue
            embedded = team.get("embedded_member_refs")
            if not isinstance(embedded, list):
                raise CommandRejectedError("team_embedded_assignment_invalid")
            moved_team_refs.append(team_ref)
            for person_ref in embedded:
                if not isinstance(person_ref, str):
                    raise CommandRejectedError("team_embedded_assignment_invalid")
                if person_ref in embedded_refs:
                    continue
                person_path, person = self._resolve_actor_for_write(person_ref)
                if person.get("current_location_id") != current_location:
                    raise CommandRejectedError("formation_embedded_person_location_mismatch")
                life = person.get("life_course_state")
                if not isinstance(life, dict):
                    raise CommandRejectedError("traveler_location_history_invalid")
                history = life.get("location_history")
                if not isinstance(history, list) or not history:
                    raise CommandRejectedError("traveler_location_history_invalid")
                history.append({
                    "at": str(arrival),
                    "location_id": destination_id,
                    "reason": f"moved with {formation_ref} via {route_id}",
                })
                history[:] = history[-self.MAX_LOCATION_HISTORY:]
                changes = life.get("location_changes", 0)
                if isinstance(changes, bool) or not isinstance(changes, int) or changes < 0:
                    raise CommandRejectedError("traveler_location_history_invalid")
                life["location_changes"] = changes + 1
                person["current_location_id"] = destination_id
                person_writes[person_path] = person
                embedded_refs.add(person_ref)

        # Command authority does not imply physical presence.  An exact receiving
        # commander moves with the formation only when the active assignment is
        # explicitly attached to this formation and the person is physically at
        # the departure point.  A commander elsewhere remains a remote command
        # authority and is never teleported by the assignment record.
        try:
            assignment_registry = self.repository.read_json(_ASSIGNMENTS_PATH)
        except (FileNotFoundError, ValueError):
            assignment_registry = {"records": []}
        assignment_records = assignment_registry.get("records") if isinstance(assignment_registry, Mapping) else None
        if not isinstance(assignment_records, list):
            raise CommandRejectedError("force_assignment_registry_invalid")
        for assignment in assignment_records:
            if not isinstance(assignment, Mapping) or assignment.get("status", "active") != "active":
                continue
            if assignment.get("source_owner") != force_ref or assignment.get("operational_attachment_ref") != formation_ref:
                continue
            start = assignment.get("start")
            expiry = assignment.get("expires_at")
            if isinstance(start, str):
                try:
                    if CampaignTime.parse(start) > current_time:
                        continue
                except (TypeError, ValueError) as exc:
                    raise CommandRejectedError("force_assignment_registry_invalid") from exc
            if isinstance(expiry, str):
                try:
                    if CampaignTime.parse(expiry) <= current_time:
                        continue
                except (TypeError, ValueError) as exc:
                    raise CommandRejectedError("force_assignment_registry_invalid") from exc
            commander_ref = assignment.get("receiving_commander")
            if not isinstance(commander_ref, str) or commander_ref in embedded_refs:
                continue
            try:
                commander_path, commander = self._resolve_actor_for_write(commander_ref)
            except CommandRejectedError:
                # Some lawful command recipients may be institutional rather than
                # exact people.  Those authorities have no physical person state.
                continue
            if commander.get("current_location_id") != current_location:
                continue
            life = commander.get("life_course_state")
            history = life.get("location_history") if isinstance(life, dict) else None
            if not isinstance(history, list) or not history:
                raise CommandRejectedError("traveler_location_history_invalid")
            history.append({
                "at": str(arrival),
                "location_id": destination_id,
                "reason": f"moved with command attachment to {formation_ref} via {route_id}",
            })
            history[:] = history[-self.MAX_LOCATION_HISTORY:]
            changes = life.get("location_changes", 0)
            if isinstance(changes, bool) or not isinstance(changes, int) or changes < 0:
                raise CommandRejectedError("traveler_location_history_invalid")
            life["location_changes"] = changes + 1
            commander["current_location_id"] = destination_id
            person_writes[commander_path] = commander
            embedded_refs.add(commander_ref)
            moved_command_refs.append(commander_ref)

        scene = json.loads(base.writes[self.scene_path].decode("utf-8"))
        if command.actor_id in embedded_refs:
            scene["location_id"] = destination_id
        scene["scene_summary"] = (
            f"{formation_ref} completes movement to {destination_id} via {route_id} at {arrival}."
        )
        world_events = self._world_events_after(base)
        event_id = self._append_semantic_event(
            world_events, command=command, kind="formation_moved", at=arrival,
            host_refs=(formation_ref,), actor_refs=tuple(sorted(embedded_refs)),
            place_refs=(origin_anchor, destination_id), route_refs=(route_id,),
            affected_owner_refs=tuple(sorted((formation_path, *person_writes, *(() if stock_path is None else (stock_path,))))),
            material_consequence_refs=tuple(
                [
                    f"formation_location:{formation_ref}:{origin_anchor}->{destination_id}",
                    f"travel_seconds:{seconds}",
                    f"movement_posture:{posture}",
                ]
                + ([] if logistics_result is None else [
                    f"rations_days_consumed:{logistics_result['rations_days_consumed']}",
                    f"water_liters_consumed:{logistics_result['water_liters_consumed']}",
                ])
            ),
            classification="restricted", audience_refs=(command.actor_id,),
            reducer_ref="game/data/mechanics/travel.json#formation_travel",
        )
        security_records = decode_security_information_records(base.writes)
        security_detections = apply_route_security_detection(
            self, command=command, at=arrival, route_ref=route_id, subject_ref=formation_ref,
            crossing_ref=event_id, world_events=world_events, staged_records=security_records, intrusion=(diplomatic_access_basis is None),
            concealment_milli=0, subject_owner_refs=(force_side_ref, force_ref),
        )
        writes = dict(base.writes)
        writes[self.meta_path] = _json_bytes(self._meta_after(meta, command, world_time=arrival))
        writes[self.scene_path] = _json_bytes(scene)
        writes[formation_path] = _json_bytes(registry)
        if stock_path is not None and stock is not None:
            writes[stock_path] = _json_bytes(stock)
        for path, person in person_writes.items():
            writes[path] = _json_bytes(person)
        for path, record in security_records.items():
            writes[path] = _json_bytes(record)
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("formation movement write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=arrival)
            staged_registry = overlay.read_json(formation_path)
            staged_rows = staged_registry.get("formations", [])
            staged = next((row for row in staged_rows if isinstance(row, Mapping) and row.get("id") == formation_ref), None)
            if not isinstance(staged, Mapping) or staged.get("location_ref") != destination_id:
                raise ValueError("formation movement location missing")
            if staged.get("personnel_total") != personnel_total:
                raise ValueError("formation movement changed headcount")
            for path in person_writes:
                if overlay.read_json(path).get("current_location_id") != destination_id:
                    raise ValueError("embedded exact person did not move with formation")
            if stock_path is not None and logistics_result is not None:
                staged_stock = overlay.read_json(stock_path)
                if staged_stock.get("rations_days") != logistics_result["rations_days_after"] or staged_stock.get("water_liters") != logistics_result["water_liters_after"]:
                    raise ValueError("formation movement supply consumption drift")
            for path, record in security_records.items():
                if path in expected and overlay.read_json(path) != record:
                    raise ValueError("formation security after-image mismatch")
            self._scheduler_from_reader(overlay)

        return _BuiltPlan(
            code="formation_movement_resolution_ready",
            affected_refs=expected,
            writes=writes,
            result={
                "formation_ref": formation_ref,
                "force_ref": force_ref,
                "origin_id": origin_anchor,
                "destination_id": destination_id,
                "route_id": route_id,
                "movement_posture": posture,
                "travel_seconds": seconds,
                "arrival_time": str(arrival),
                "embedded_team_refs": moved_team_refs,
                "embedded_person_refs": sorted(embedded_refs),
                "moved_command_refs": sorted(moved_command_refs),
                "authority_basis": authority_basis,
                "diplomatic_access_basis": diplomatic_access_basis,
                "logistics": logistics_result,
                "security_detections": security_detections,
                "semantic_event_id": event_id,
            },
            validator=validate,
        )


    def _active_conflict_registry(self) -> Mapping[str, Any]:
        raw = self.repository.read_optional_bytes(_CONFLICT_REGISTRY_PATH)
        if raw is None:
            return {"schema": "conflict-registry", "records": {}}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CommandRejectedError("conflict_registry_invalid") from exc
        if not isinstance(value, Mapping) or value.get("schema") != "conflict-registry" or not isinstance(value.get("records"), Mapping):
            raise CommandRejectedError("conflict_registry_invalid")
        return value


    def _formation_front_effects(
        self,
        *,
        formation_ref: str,
        force_side_ref: Optional[str],
        location_ref: Optional[str],
    ) -> Tuple[str, int]:
        """Return bounded supply state and friendly fortification bonus."""
        supply = "supported"
        fortification = 0
        try:
            graph = LocationGraph(self.repository.read_json(_ROUTES_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("location_registry_invalid") from exc
        location_anchor = graph.anchor(location_ref) if isinstance(location_ref, str) else None
        registry = self._active_conflict_registry()
        for conflict in registry.get("records", {}).values():
            if not isinstance(conflict, Mapping) or conflict.get("status") not in ("active", "ceasefire"):
                continue
            fronts = conflict.get("fronts")
            if not isinstance(fronts, Mapping):
                continue
            for front in fronts.values():
                if not isinstance(front, Mapping) or front.get("status") != "active":
                    continue
                formation_refs = front.get("formation_refs")
                places = front.get("place_refs")
                at_front = (
                    not isinstance(places, list)
                    or not places
                    or (isinstance(location_ref, str) and location_ref in places)
                    or (isinstance(location_anchor, str) and location_anchor in places)
                )
                if isinstance(formation_refs, list) and formation_ref in formation_refs and at_front:
                    order = {"supported": 0, "strained": 1, "critical": 2, "cut_off": 3}
                    route_refs = front.get("route_refs")
                    route_state = front.get("route_state")
                    if isinstance(route_refs, list) and route_refs:
                        best_severity = 3
                        for route_ref in route_refs:
                            row = route_state.get(route_ref) if isinstance(route_state, Mapping) else None
                            status = row.get("status", "open") if isinstance(row, Mapping) else "open"
                            controller = row.get("controller_ref") if isinstance(row, Mapping) else None
                            disruption = row.get("disruption_milli", 0) if isinstance(row, Mapping) else 0
                            if status == "open":
                                severity = 0 if controller in (None, force_side_ref) else 1
                            elif status == "contested":
                                severity = 1 if controller in (None, force_side_ref) else 2
                            elif status == "disrupted":
                                severity = 2 if controller in (None, force_side_ref) else 3
                            elif status == "blocked":
                                severity = 3
                            else:
                                raise CommandRejectedError("conflict_route_state_invalid")
                            if isinstance(disruption, int) and not isinstance(disruption, bool):
                                if disruption >= 750:
                                    severity = min(3, severity + 1)
                                elif disruption >= 400 and severity < 3:
                                    severity = min(3, severity + 1)
                            best_severity = min(best_severity, severity)
                        candidate = ("supported", "strained", "critical", "cut_off")[best_severity]
                        if order[candidate] > order[supply]:
                            supply = candidate
                    if force_side_ref is not None and front.get("control_ref") == force_side_ref:
                        raw = front.get("fortification_milli")
                        if isinstance(raw, int) and not isinstance(raw, bool):
                            fortification = max(fortification, max(0, min(1000, raw)))
        return supply, fortification


    def _conflict_route_multiplier(
        self, route_id: str, *, traveler_side_ref: Optional[str] = None
    ) -> Decimal:
        """Return bounded movement friction from active strategic route state.

        Route status is shared physical state.  Controller identity matters only
        when a formation from a different conflict side attempts to use the
        route; hostile control adds delay rather than silently teleporting the
        formation or inventing detailed logistics.
        """
        multiplier = Decimal("1")
        rank = {"open": 0, "contested": 1, "disrupted": 2, "blocked": 3}
        strongest = "open"
        disruption = 0
        hostile_control = False
        registry = self._active_conflict_registry()
        for conflict in registry.get("records", {}).values():
            if not isinstance(conflict, Mapping) or conflict.get("status") != "active":
                continue
            fronts = conflict.get("fronts")
            if not isinstance(fronts, Mapping):
                continue
            for front in fronts.values():
                if not isinstance(front, Mapping) or front.get("status") != "active":
                    continue
                state = front.get("route_state")
                row = state.get(route_id) if isinstance(state, Mapping) else None
                if not isinstance(row, Mapping):
                    continue
                status = row.get("status")
                if status in rank and rank[status] > rank[strongest]:
                    strongest = status
                controller = row.get("controller_ref")
                if (
                    isinstance(traveler_side_ref, str)
                    and isinstance(controller, str)
                    and controller
                    and controller != traveler_side_ref
                ):
                    hostile_control = True
                raw = row.get("disruption_milli")
                if isinstance(raw, int) and not isinstance(raw, bool):
                    disruption = max(disruption, max(0, min(1000, raw)))
        if strongest == "blocked":
            raise CommandRejectedError("formation_route_blocked_by_conflict")
        if strongest == "contested":
            multiplier *= Decimal("1.20")
        elif strongest == "disrupted":
            multiplier *= Decimal("1.45")
        if hostile_control:
            multiplier *= Decimal("1.15")
        multiplier *= Decimal(1000 + disruption // 2) / Decimal(1000)
        return multiplier


    def _aggregate_combat_winning_owner_refs(self, event: Mapping[str, Any]) -> set[str]:
        """Resolve strategic side owners that actually won an aggregate battle.

        World-event prose is not authority.  The combat operation owns the
        victorious side refs and participant/force mapping, so route control and
        occupation can only be claimed by a side that the battle state records
        as victorious.
        """
        if event.get("kind") != "aggregate_combat_resolved":
            return set()
        host_refs = event.get("host_refs")
        if not isinstance(host_refs, list):
            return set()
        combat_refs = [ref for ref in host_refs if isinstance(ref, str) and ref]
        if len(combat_refs) != 1:
            return set()
        try:
            operation = self.repository.read_json(self._operation_owner_path(combat_refs[0]))
        except (FileNotFoundError, ValueError, CommandRejectedError):
            return set()
        if (
            not isinstance(operation, Mapping)
            or operation.get("schema") != "combat-operation"
            or operation.get("status") != "resolved"
        ):
            return set()
        outcome = operation.get("outcome")
        participants = operation.get("participants")
        victorious = outcome.get("victorious_side_refs") if isinstance(outcome, Mapping) else None
        if not isinstance(victorious, list) or not isinstance(participants, list):
            return set()
        event_id = event.get("id")
        if isinstance(event_id, str) and outcome.get("semantic_event_id") != event_id:
            return set()
        victorious_sides = {ref for ref in victorious if isinstance(ref, str) and ref}
        if not victorious_sides:
            return set()
        result: set[str] = set()
        cache = _OwnerResolutionCache()
        for participant in participants:
            if not isinstance(participant, Mapping) or participant.get("side_ref") not in victorious_sides:
                continue
            force_ref = participant.get("force_ref")
            if not isinstance(force_ref, str) or not force_ref:
                continue
            try:
                _path, _digest, force = self._resolve_covered_owner_view(force_ref, cache=cache)
            except CommandRejectedError:
                continue
            if not isinstance(force, Mapping) or force.get("schema") != "force":
                continue
            owner_ref = force.get("owner_ref")
            result.add(owner_ref if isinstance(owner_ref, str) and owner_ref else force_ref)
        return result


    def _conflict_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        spec = COMMAND_SPECS[command.command_type]
        if any(key not in set(spec.required_fields) | set(spec.optional_fields) for key in command.payload) or any(key not in command.payload for key in spec.required_fields):
            raise CommandRejectedError("conflict_resolution_payload_fields_invalid")
        action = command.payload.get("action")
        if action not in ("start", "open_front", "assign_formation", "unassign_formation", "route_control", "fortify", "occupy", "close_front", "ceasefire", "resume", "end"):
            raise CommandRejectedError("conflict_action_invalid")
        conflict_ref = _stable_id(command.payload.get("conflict_ref"), "conflict_ref_invalid", prefix="conflict.")
        try:
            registry = copy.deepcopy(dict(self._active_conflict_registry()))
            routes_record = self.repository.read_json(_ROUTES_PATH)
            graph = LocationGraph(routes_record)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("conflict_registry_invalid") from exc
        records = registry.get("records")
        if not isinstance(records, dict):
            raise CommandRejectedError("conflict_registry_invalid")
        base: Optional[_BuiltPlan] = None
        world_time = current_time
        fortification_result: Optional[Mapping[str, Any]] = None

        def owner_authority(owner_ref: str) -> str:
            decision = self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(
                holder_ref=command.actor_id, owner_ref=owner_ref
            )
            if not decision.allowed:
                raise CommandRejectedError("conflict_authority_denied")
            return decision.basis

        authority_basis = ""
        pending_defense_specs: list[dict[str, str]] = []
        defense_commitment_refs: list[str] = []
        commitment_registry: Optional[Dict[str, Any]] = None
        scheduler_for_obligations: Optional[CausalSchedulerRegistry] = None
        if action == "start":
            if conflict_ref in records:
                raise CommandRejectedError("conflict_exists")
            name = command.payload.get("name")
            sides = command.payload.get("side_refs")
            objectives = command.payload.get("objectives", {})
            if not isinstance(name, str) or not name or len(name) > 200:
                raise CommandRejectedError("conflict_name_invalid")
            if not isinstance(sides, Sequence) or isinstance(sides, (str, bytes, bytearray)) or not 2 <= len(sides) <= 8 or len(sides) != len(set(sides)) or any(not isinstance(x, str) or not x for x in sides):
                raise CommandRejectedError("conflict_sides_invalid")
            if not isinstance(objectives, Mapping):
                raise CommandRejectedError("conflict_objectives_invalid")
            sides = list(sides)
            # Every conflict side is a real persistent authority owner. A
            # caller cannot create a war against a typo or invented faction ID.
            for side in sides:
                try:
                    _side_path, _side_digest, side_view = self._resolve_covered_owner_view(
                        side, cache=_OwnerResolutionCache()
                    )
                except CommandRejectedError as exc:
                    raise CommandRejectedError("conflict_side_unresolved") from exc
                if not isinstance(side_view, Mapping):
                    raise CommandRejectedError("conflict_side_unresolved")
            clean_objectives = {}
            for side in sides:
                vals = objectives.get(side, ())
                if not isinstance(vals, Sequence) or isinstance(vals, (str, bytes, bytearray)) or any(not isinstance(v, str) or len(v) > 500 for v in vals):
                    raise CommandRejectedError("conflict_objectives_invalid")
                clean_objectives[side] = list(vals)
            # Starting a war requires leadership authority on at least one side.
            bases=[]
            initiating_sides=[]
            for side in sides:
                try:
                    bases.append(owner_authority(side))
                    initiating_sides.append(side)
                except CommandRejectedError:
                    pass
            if not bases:
                raise CommandRejectedError("conflict_authority_denied")
            target_sides=[side for side in sides if side not in initiating_sides]
            if not target_sides:
                raise CommandRejectedError("conflict_requires_external_counterparty")
            authority_basis = bases[0]
            try:
                diplomacy_for_start = self.repository.read_json(_DIPLOMACY_PATH)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("conflict_diplomacy_invalid") from exc
            if not isinstance(diplomacy_for_start, Mapping):
                raise CommandRejectedError("conflict_diplomacy_invalid")
            barrier = hostility_barrier(
                diplomacy_for_start, initiator_refs=initiating_sides, target_refs=target_sides
            )
            if barrier is not None:
                raise CommandRejectedError(f"conflict_blocked_by_{barrier[1]}_agreement")
            pending_defense_specs = defense_obligation_specs(
                diplomacy_for_start,
                initiator_refs=initiating_sides,
                target_refs=target_sides,
                conflict_ref=conflict_ref,
            )
            records[conflict_ref] = {
                "id": conflict_ref, "name": name, "status": "active", "side_refs": sides,
                "objectives": clean_objectives, "started_at": str(current_time), "ended_at": None,
                "ceasefire_consents": [], "end_consents": [], "support_alignments": {}, "fronts": {},
            }
            event_kind = "conflict_started"
            affected_ref = conflict_ref
        else:
            conflict = records.get(conflict_ref)
            if not isinstance(conflict, dict):
                raise CommandRejectedError("conflict_unresolved")
            sides = conflict.get("side_refs")
            if not isinstance(sides, list):
                raise CommandRejectedError("conflict_registry_invalid")
            bases=[]
            actor_authorized_sides=[]
            for side in sides:
                if not isinstance(side, str):
                    continue
                try:
                    basis = owner_authority(side)
                except CommandRejectedError:
                    continue
                bases.append(basis)
                actor_authorized_sides.append(side)
            if not bases:
                raise CommandRejectedError("conflict_authority_denied")
            authority_basis = bases[0]
            fronts = conflict.get("fronts")
            if not isinstance(fronts, dict):
                raise CommandRejectedError("conflict_registry_invalid")
            if action in ("ceasefire", "resume", "end"):
                ceasefire_consents = conflict.get("ceasefire_consents")
                end_consents = conflict.get("end_consents")
                if not isinstance(ceasefire_consents, list) or not isinstance(end_consents, list):
                    raise CommandRejectedError("conflict_registry_invalid")
                if action == "ceasefire":
                    if conflict.get("status") != "active":
                        raise CommandRejectedError("conflict_status_invalid")
                    added = False
                    for side in actor_authorized_sides:
                        if side not in ceasefire_consents:
                            ceasefire_consents.append(side)
                            added = True
                    ceasefire_consents.sort()
                    if not added:
                        raise CommandRejectedError("conflict_ceasefire_consent_already_recorded")
                    if set(ceasefire_consents) == set(sides):
                        conflict["status"] = "ceasefire"
                        end_consents.clear()
                        event_kind = "conflict_ceasefire"
                    else:
                        event_kind = "conflict_ceasefire_consent_recorded"
                elif action == "resume":
                    if conflict.get("status") != "ceasefire":
                        raise CommandRejectedError("conflict_status_invalid")
                    # A ceasefire can be broken unilaterally by a lawful side; it
                    # does not require the opponent's consent to resume hostilities.
                    conflict["status"] = "active"
                    ceasefire_consents.clear()
                    end_consents.clear()
                    event_kind = "conflict_resumed"
                else:
                    if conflict.get("status") == "ended":
                        raise CommandRejectedError("conflict_status_invalid")
                    added = False
                    for side in actor_authorized_sides:
                        if side not in end_consents:
                            end_consents.append(side)
                            added = True
                    end_consents.sort()
                    if not added:
                        raise CommandRejectedError("conflict_end_consent_already_recorded")
                    if set(end_consents) == set(sides):
                        conflict["status"] = "ended"
                        conflict["ended_at"] = str(current_time)
                        ceasefire_consents.clear()
                        for front in fronts.values():
                            if isinstance(front, dict):
                                front["status"] = "closed"
                        event_kind = "conflict_ended"
                    else:
                        event_kind = "conflict_end_consent_recorded"
                affected_ref=conflict_ref
            else:
                front_ref = _stable_id(command.payload.get("front_ref"), "conflict_front_ref_invalid", prefix="front.")
                if action == "open_front":
                    if conflict.get("status") != "active" or front_ref in fronts:
                        raise CommandRejectedError("conflict_front_status_invalid")
                    front_name=command.payload.get("front_name")
                    places=command.payload.get("place_refs",[]); routes=command.payload.get("route_refs",[])
                    if not isinstance(front_name,str) or not front_name or len(front_name)>200:
                        raise CommandRejectedError("conflict_front_name_invalid")
                    if (not isinstance(places, Sequence) or isinstance(places, (str, bytes, bytearray))
                        or not isinstance(routes, Sequence) or isinstance(routes, (str, bytes, bytearray))
                        or any(not isinstance(x, str) for x in [*places, *routes])):
                        raise CommandRejectedError("conflict_front_geography_invalid")
                    for place_ref in places:
                        if graph.place(place_ref) is None:
                            raise CommandRejectedError("conflict_front_place_invalid")
                    route_rows={
                        row.get("id"): row
                        for row in graph.routes
                        if isinstance(row, Mapping) and isinstance(row.get("id"), str)
                    }
                    if any(route_ref not in route_rows for route_ref in routes):
                        raise CommandRejectedError("conflict_front_route_invalid")
                    if not places and not routes:
                        raise CommandRejectedError("conflict_front_geography_empty")

                    # A front is one materially connected area, not an arbitrary
                    # bag of distant places.  Child places collapse to strategic
                    # route anchors; selected routes must connect all anchors.
                    place_anchors={graph.anchor(place_ref) for place_ref in places}
                    selected_routes=[route_rows[route_ref] for route_ref in routes]
                    route_nodes={
                        endpoint
                        for row in selected_routes
                        for endpoint in (row.get("from"), row.get("to"))
                        if isinstance(endpoint, str)
                    }
                    if selected_routes:
                        all_nodes=set(route_nodes) | set(place_anchors)
                        adjacency={node:set() for node in all_nodes}
                        for row in selected_routes:
                            left, right=row.get("from"), row.get("to")
                            if isinstance(left,str) and isinstance(right,str):
                                adjacency.setdefault(left,set()).add(right)
                                adjacency.setdefault(right,set()).add(left)
                        if place_anchors - route_nodes:
                            raise CommandRejectedError("conflict_front_geography_disconnected")
                        if all_nodes:
                            start=next(iter(all_nodes)); seen={start}; stack=[start]
                            while stack:
                                node=stack.pop()
                                for nxt in adjacency.get(node,()):
                                    if nxt not in seen:
                                        seen.add(nxt); stack.append(nxt)
                            if seen != all_nodes:
                                raise CommandRejectedError("conflict_front_geography_disconnected")
                    elif len(place_anchors) > 1:
                        raise CommandRejectedError("conflict_front_geography_disconnected")
                    fronts[front_ref]={"id":front_ref,"name":front_name,"status":"active","place_refs":sorted(set(places)),"route_refs":sorted(set(routes)),"formation_refs":[],"control_ref":None,"fortification_milli":0,"route_state":{},"occupations":{}}
                    event_kind="conflict_front_opened"; affected_ref=front_ref
                else:
                    front=fronts.get(front_ref)
                    if not isinstance(front,dict) or front.get("status")!="active": raise CommandRejectedError("conflict_front_unresolved")
                    if action in ("assign_formation", "unassign_formation"):
                        formation_ref=_stable_id(command.payload.get("formation_ref"),"conflict_formation_invalid",prefix="formation.")
                        _formation_path,force_ref,formation=self._formation_by_id(formation_ref)
                        # Assignment changes require authority over the actual formation's force too.
                        cache=_OwnerResolutionCache(); _fp,_fd,fv=self._resolve_covered_owner_view(force_ref,cache=cache)
                        if not isinstance(fv,Mapping): raise CommandRejectedError("conflict_force_invalid")
                        force_side_ref=fv.get("owner_ref") if isinstance(fv.get("owner_ref"),str) and fv.get("owner_ref") else force_ref
                        if force_side_ref not in sides: raise CommandRejectedError("conflict_formation_not_a_side")
                        grant=self._domain_authority(cache=cache).force_grant(grantor_ref=command.actor_id,force_record=fv)
                        if not grant.allowed:
                            decision=self._domain_authority(cache=cache).force_command(commander_ref=command.actor_id,force_ref=force_ref,operational_attachment_ref=formation_ref,named_actor_refs=(),committed_count=int(formation.get("personnel_total",0)),effective_at=str(current_time))
                            if not decision.allowed: raise CommandRejectedError("conflict_formation_authority_denied")
                        if action == "assign_formation":
                            for other_ref, other_front in fronts.items():
                                if other_ref == front_ref or not isinstance(other_front, Mapping) or other_front.get("status") != "active":
                                    continue
                                refs = other_front.get("formation_refs")
                                if isinstance(refs, list) and formation_ref in refs:
                                    raise CommandRejectedError("conflict_formation_already_assigned")
                            if formation_ref not in front["formation_refs"]:
                                front["formation_refs"].append(formation_ref); front["formation_refs"].sort()
                            event_kind="conflict_formation_assigned"
                        else:
                            if formation_ref not in front.get("formation_refs",[]):
                                raise CommandRejectedError("conflict_formation_not_assigned")
                            front["formation_refs"].remove(formation_ref)
                            event_kind="conflict_formation_unassigned"
                        affected_ref=formation_ref
                    elif action=="route_control":
                        route_id=_stable_id(command.payload.get("route_id"),"conflict_route_invalid")
                        if route_id not in front.get("route_refs",[]): raise CommandRejectedError("conflict_route_not_on_front")
                        status=command.payload.get("route_status")
                        disruption=command.payload.get("disruption_milli",0)
                        control_ref=command.payload.get("control_ref")
                        evidence_ref=command.payload.get("evidence_event_ref")
                        if status not in ("open","contested","disrupted","blocked") or isinstance(disruption,bool) or not isinstance(disruption,int) or not 0<=disruption<=1000:
                            raise CommandRejectedError("conflict_route_state_invalid")
                        if control_ref is not None and control_ref not in actor_authorized_sides: raise CommandRejectedError("conflict_control_ref_not_authorized")
                        if not isinstance(evidence_ref, str) or not evidence_ref:
                            raise CommandRejectedError("conflict_route_evidence_invalid")
                        world=self._world_events(); event=self._world_event_by_id(evidence_ref, registry=world)
                        if not isinstance(event, Mapping):
                            raise CommandRejectedError("conflict_route_evidence_invalid")
                        evidence_kind=event.get("kind")
                        valid_evidence=False
                        if evidence_kind=="aggregate_combat_resolved":
                            route_row=next((row for row in graph.routes if isinstance(row, Mapping) and row.get("id")==route_id), None)
                            endpoints={route_row.get("from"),route_row.get("to")} if isinstance(route_row, Mapping) else set()
                            event_places=event.get("place_refs")
                            valid_evidence=isinstance(event_places,list) and bool(endpoints.intersection(x for x in event_places if isinstance(x,str)))
                            if valid_evidence and control_ref is not None:
                                winners=self._aggregate_combat_winning_owner_refs(event)
                                if control_ref not in winners:
                                    raise CommandRejectedError("conflict_route_controller_did_not_win_evidence")
                        elif evidence_kind=="mission_settled":
                            causal=event.get("causal_refs")
                            valid_evidence=isinstance(causal,list) and (front_ref in causal or route_id in causal)
                            # Missions may lawfully disrupt/block a route, but an
                            # ordinary mission settlement does not by itself prove
                            # territorial controller identity.
                            if control_ref is not None:
                                raise CommandRejectedError("conflict_route_mission_cannot_assign_controller")
                        if not valid_evidence:
                            raise CommandRejectedError("conflict_route_evidence_invalid")
                        front["route_state"][route_id]={"status":status,"controller_ref":control_ref,"disruption_milli":disruption,"evidence_event_ref":evidence_ref}
                        event_kind="conflict_route_control_changed"; affected_ref=route_id
                    elif action=="fortify":
                        formation_ref=_stable_id(command.payload.get("formation_ref"),"conflict_formation_invalid",prefix="formation.")
                        if formation_ref not in front.get("formation_refs",[]):
                            raise CommandRejectedError("conflict_formation_not_assigned")
                        formation_path, force_ref, formation = self._formation_by_id(formation_ref)
                        cache=_OwnerResolutionCache(); _fp,_fd,force_view=self._resolve_covered_owner_view(force_ref,cache=cache)
                        if not isinstance(force_view,Mapping): raise CommandRejectedError("conflict_force_invalid")
                        control_ref=force_view.get("owner_ref") if isinstance(force_view.get("owner_ref"),str) and force_view.get("owner_ref") else force_ref
                        if control_ref not in actor_authorized_sides:
                            raise CommandRejectedError("conflict_fortification_controller_mismatch")
                        grant=self._domain_authority(cache=cache).force_grant(grantor_ref=command.actor_id,force_record=force_view)
                        if not grant.allowed:
                            decision=self._domain_authority(cache=cache).force_command(
                                commander_ref=command.actor_id, force_ref=force_ref, operational_attachment_ref=formation_ref,
                                named_actor_refs=(), committed_count=int(formation.get("personnel_total",0)), effective_at=str(current_time),
                            )
                            if not decision.allowed: raise CommandRejectedError("conflict_formation_authority_denied")
                        location_ref=formation.get("location_ref")
                        anchor=graph.anchor(location_ref) if isinstance(location_ref,str) else None
                        route_rows={row.get("id"):row for row in graph.routes if isinstance(row,Mapping) and isinstance(row.get("id"),str)}
                        front_anchors=set(x for x in front.get("place_refs",[]) if isinstance(x,str))
                        for route_ref in front.get("route_refs",[]):
                            row=route_rows.get(route_ref)
                            if isinstance(row,Mapping):
                                front_anchors.update(x for x in (row.get("from"),row.get("to")) if isinstance(x,str))
                        if anchor not in front_anchors:
                            raise CommandRejectedError("conflict_fortification_formation_not_present")
                        existing_control=front.get("control_ref")
                        if existing_control not in (None, control_ref):
                            raise CommandRejectedError("conflict_fortification_controller_mismatch")
                        try:
                            target_time=CampaignTime.parse(command.payload.get("target_time"))
                            active_hours=Decimal(str(command.payload.get("active_hours")))
                            mechanics=self.repository.read_json(_CONFLICT_MECHANICS_PATH)
                        except (TypeError,ValueError,FileNotFoundError) as exc:
                            raise CommandRejectedError("conflict_fortification_time_invalid") from exc
                        elapsed=Decimal(int((_campaign_datetime(target_time)-_campaign_datetime(current_time)).total_seconds()))/Decimal(3600)
                        fort_rules=mechanics.get("fortification") if isinstance(mechanics,Mapping) else None
                        bands=fort_rules.get("size_rates_milli_per_active_hour") if isinstance(fort_rules,Mapping) else None
                        minimum=fort_rules.get("minimum_active_hours") if isinstance(fort_rules,Mapping) else None
                        maximum=fort_rules.get("max_milli") if isinstance(fort_rules,Mapping) else None
                        personnel=formation.get("personnel_total")
                        if (target_time<=current_time or not active_hours.is_finite() or active_hours<=0 or active_hours>elapsed
                            or not isinstance(bands,list) or isinstance(minimum,bool) or not isinstance(minimum,(int,float))
                            or isinstance(maximum,bool) or not isinstance(maximum,int) or not 1<=maximum<=1000
                            or isinstance(personnel,bool) or not isinstance(personnel,int) or personnel<=0
                            or active_hours<Decimal(str(minimum))):
                            raise CommandRejectedError("conflict_fortification_time_invalid")
                        rate=None
                        for band in bands:
                            if not isinstance(band,Mapping): continue
                            cap=band.get("max_personnel"); candidate=band.get("rate")
                            if (isinstance(cap,int) and not isinstance(cap,bool) and personnel<=cap
                                and isinstance(candidate,int) and not isinstance(candidate,bool) and candidate>0):
                                rate=candidate; break
                        if rate is None: raise CommandRejectedError("conflict_fortification_mechanics_invalid")
                        gain=int(active_hours*Decimal(rate))
                        if gain<=0: raise CommandRejectedError("conflict_fortification_work_too_small")
                        before=front.get("fortification_milli",0)
                        if isinstance(before,bool) or not isinstance(before,int) or not 0<=before<=maximum:
                            raise CommandRejectedError("conflict_fortification_invalid")
                        after=min(maximum,before+gain)
                        if after==before: raise CommandRejectedError("conflict_fortification_already_maximum")
                        base=self._time_spanning_base(command,meta,current_time,target_time=target_time)
                        if base.result.get("interrupted") or CampaignTime.parse(base.result["world_time"])!=target_time:
                            raise CommandRejectedError("time_boundary_requires_domain_settlement")
                        world_time=target_time
                        front["fortification_milli"]=after; front["control_ref"]=control_ref
                        fortification_result={"formation_ref":formation_ref,"before_milli":before,"after_milli":after,"active_hours":str(active_hours),"rate_milli_per_hour":rate}
                        event_kind="conflict_front_fortified"; affected_ref=front_ref
                    elif action=="occupy":
                        place_ref=_stable_id(command.payload.get("place_ref"),"conflict_occupation_place_invalid")
                        control_ref=command.payload.get("control_ref"); evidence_ref=command.payload.get("evidence_event_ref")
                        if place_ref not in front.get("place_refs",[]) or control_ref not in actor_authorized_sides or not isinstance(evidence_ref,str):
                            raise CommandRejectedError("conflict_occupation_invalid")
                        world=self._world_events(); event=self._world_event_by_id(evidence_ref, registry=world)
                        event_places=event.get("place_refs") if isinstance(event,Mapping) else None
                        if (not isinstance(event,Mapping) or event.get("kind") != "aggregate_combat_resolved"
                            or not isinstance(event_places,list) or place_ref not in event_places):
                            raise CommandRejectedError("conflict_occupation_evidence_invalid")
                        winners=self._aggregate_combat_winning_owner_refs(event)
                        if control_ref not in winners:
                            raise CommandRejectedError("conflict_occupation_controller_did_not_win_evidence")
                        front["control_ref"]=control_ref; front.setdefault("occupations", {})[place_ref]={"place_ref":place_ref,"controller_ref":control_ref,"evidence_event_ref":evidence_ref,"since":str(current_time)}
                        event_kind="conflict_place_occupied"; affected_ref=place_ref
                    elif action=="close_front":
                        if front.get("formation_refs"):
                            raise CommandRejectedError("conflict_front_has_assigned_formations")
                        front["status"]="closed"; event_kind="conflict_front_closed"; affected_ref=front_ref
                    else:
                        raise CommandRejectedError("conflict_action_invalid")

        if pending_defense_specs:
            try:
                commitment_registry = copy.deepcopy(self.repository.read_json(_COMMITMENT_REGISTRY_PATH))
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("commitment_registry_invalid") from exc
            commitment_records = commitment_registry.get("records") if isinstance(commitment_registry, dict) else None
            if not isinstance(commitment_records, list):
                raise CommandRejectedError("commitment_registry_invalid")
            scheduler_for_obligations = self._load_scheduler(current_time=current_time, scene={})
            agreements_for_start = diplomacy_for_start.get("agreements") if isinstance(diplomacy_for_start, Mapping) else None
            due_at = current_time.add_seconds(7 * 24 * 60 * 60)
            existing_commitment_ids = {
                row.get("id") for row in commitment_records if isinstance(row, Mapping) and isinstance(row.get("id"), str)
            }
            for index, spec_row in enumerate(pending_defense_specs):
                agreement_ref = spec_row["agreement_ref"]
                agreement = agreements_for_start.get(agreement_ref) if isinstance(agreements_for_start, Mapping) else None
                visibility = agreement.get("visibility") if isinstance(agreement, Mapping) else "restricted"
                if visibility not in ("public", "restricted", "secret"):
                    visibility = "restricted"
                commitment_ref = f"commitment.treaty.{command.digest[:16]}.{index:02d}"
                if commitment_ref in existing_commitment_ids:
                    raise CommandRejectedError("treaty_defense_commitment_conflict")
                obligor = spec_row["obligor_ref"]
                beneficiary = spec_row["beneficiary_ref"]
                agreement_type = spec_row["agreement_type"]
                commitment_records.append({
                    "id": commitment_ref,
                    "kind": "obligation",
                    "subject_ref": obligor,
                    "target_ref": beneficiary,
                    "host_ref": agreement_ref,
                    "created_at": str(current_time),
                    "due_at": str(due_at),
                    "status": "active",
                    "summary": f"{obligor} must resolve its {agreement_type} defense obligation to {beneficiary} after {conflict_ref} began.",
                    "visibility": visibility,
                    "authority_basis": f"agreement:{agreement_ref}:{agreement_type}",
                    "causal_ref": conflict_ref,
                    "autonomous_review_count": 0,
                })
                existing_commitment_ids.add(commitment_ref)
                defense_commitment_refs.append(commitment_ref)
                host_id = "host." + commitment_ref
                scheduler_for_obligations.add_host(
                    SchedulerHost(
                        state=HostState(
                            host_id=host_id,
                            kind="commitment",
                            resolved_through=current_time,
                            safe_through=due_at.add_seconds(-1),
                            handler_ref="causal.scheduler",
                            rng_namespace=commitment_ref,
                            next_due=due_at,
                        ),
                        authority_kind="commitment",
                        owner_ref=_COMMITMENT_REGISTRY_PATH,
                        metadata={"commitment_id": commitment_ref},
                    )
                )
                scheduler_for_obligations.upsert_event(
                    one_shot_event(
                        kind="commitment.due",
                        identity=commitment_ref,
                        source_host=host_id,
                        target_host=host_id,
                        due_at=due_at,
                        payload={"commitment_id": commitment_ref},
                        priority=30,
                        visibility="world_only",
                        requires_player=False,
                    )
                )

        world_events=self._world_events_after(base)
        material_refs=[affected_ref, *defense_commitment_refs]
        if fortification_result is not None:
            material_refs.append(f"fortification:{front_ref}:{fortification_result['before_milli']}->{fortification_result['after_milli']}")
        event_affected_paths = (_CONFLICT_REGISTRY_PATH,) + (
            (_COMMITMENT_REGISTRY_PATH, self.scheduler_path) if defense_commitment_refs else ()
        )
        event_id=self._append_semantic_event(world_events,command=command,kind=event_kind,at=world_time,host_refs=(conflict_ref,),actor_refs=(command.actor_id,),affected_owner_refs=event_affected_paths,material_consequence_refs=tuple(material_refs),classification="public",reducer_ref="shinobi_runtime.commands.conflict_resolution")
        writes=dict(base.writes) if base is not None else {}
        writes[self.meta_path]=_json_bytes(self._meta_after(meta,command,world_time=world_time))
        writes[_CONFLICT_REGISTRY_PATH]=_json_bytes(registry)
        if commitment_registry is not None and scheduler_for_obligations is not None:
            writes[_COMMITMENT_REGISTRY_PATH]=_json_bytes(commitment_registry)
            writes.update(self._scheduler_write_images(scheduler_for_obligations))
        if base is not None and self.scene_path in writes:
            scene=json.loads(writes[self.scene_path].decode("utf-8"))
            scene["scene_summary"]=f"{command.actor_id} fortifies {front_ref} through {world_time}."
            writes[self.scene_path]=_json_bytes(scene)
        writes.update(self._world_event_writes(world_events))
        writes=self._prune_noop_writes(writes); expected=tuple(sorted(writes))
        def validate(overlay:StagedOverlay,manifest:TransactionManifest)->None:
            if overlay.changed_paths!=expected: raise ValueError("conflict write set changed after planning")
            self._assert_meta(overlay,manifest,meta_path=self.meta_path,command=command,world_time=world_time)
            staged=overlay.read_json(_CONFLICT_REGISTRY_PATH)
            if not isinstance(staged.get("records",{}).get(conflict_ref),Mapping): raise ValueError("conflict after-image missing")
            if fortification_result is not None:
                staged_front=staged["records"][conflict_ref].get("fronts",{}).get(front_ref)
                if not isinstance(staged_front,Mapping) or staged_front.get("fortification_milli")!=fortification_result["after_milli"]:
                    raise ValueError("conflict fortification after-image invalid")
            if defense_commitment_refs:
                staged_commitments = overlay.read_json(_COMMITMENT_REGISTRY_PATH).get("records", [])
                persisted = {row.get("id") for row in staged_commitments if isinstance(row, Mapping)}
                if not set(defense_commitment_refs).issubset(persisted):
                    raise ValueError("treaty defense obligations missing from commitment registry")
                self._scheduler_from_reader(overlay)
        final_conflict = records.get(conflict_ref)
        return _BuiltPlan(
            code="conflict_resolution_ready",
            affected_refs=expected,
            writes=writes,
            result={
                "conflict_ref": conflict_ref,
                "action": action,
                "status": final_conflict.get("status") if isinstance(final_conflict, Mapping) else None,
                "ceasefire_consents": list(final_conflict.get("ceasefire_consents", ())) if isinstance(final_conflict, Mapping) else [],
                "end_consents": list(final_conflict.get("end_consents", ())) if isinstance(final_conflict, Mapping) else [],
                "authority_basis": authority_basis,
                "world_time": str(world_time),
                "fortification": fortification_result,
                "semantic_event_id": event_id,
                "defense_commitment_refs": list(defense_commitment_refs),
            },
            validator=validate,
        )


    def _custody_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        spec = COMMAND_SPECS[command.command_type]
        required = set(spec.required_fields)
        optional = set(spec.optional_fields)
        if not required.issubset(command.payload) or set(command.payload) - required - optional:
            raise CommandRejectedError("custody_resolution_payload_fields_invalid")
        action = command.payload.get("action")
        if action not in ("detain", "transfer", "release", "exchange", "escape"):
            raise CommandRejectedError("custody_action_invalid")
        custody_ref = _stable_id(command.payload.get("custody_ref"), "custody_ref_invalid", prefix="custody.")
        try:
            registry = copy.deepcopy(self.repository.read_json(_CUSTODY_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("custody_registry_invalid") from exc
        records = registry.get("records") if isinstance(registry, dict) else None
        if not isinstance(records, dict):
            raise CommandRejectedError("custody_registry_invalid")
        try:
            graph = LocationGraph(self.repository.read_json(_ROUTES_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("custody_place_registry_invalid") from exc

        def text(name: str, *, required_field: bool = False, max_len: int = 1000) -> Optional[str]:
            value = command.payload.get(name)
            if value is None and not required_field:
                return None
            if not isinstance(value, str) or not value or len(value) > max_len:
                raise CommandRejectedError(f"custody_{name}_invalid")
            return value

        def custody_module(place_ref: str) -> Tuple[Mapping[str, Any], int, int]:
            place = graph.place(place_ref)
            modules = place.get("mechanical_modules") if isinstance(place, Mapping) else None
            custody = modules.get("custody") if isinstance(modules, Mapping) else None
            if not isinstance(place, Mapping) or not isinstance(custody, Mapping):
                raise CommandRejectedError("custody_facility_required")
            capacity = custody.get("capacity_slots")
            security = custody.get("security_milli")
            if (
                isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0
                or isinstance(security, bool) or not isinstance(security, int) or not 0 <= security <= 1000
            ):
                raise CommandRejectedError("custody_facility_invalid")
            return place, capacity, security

        def occupancy(place_ref: str, *, excluding: Optional[str] = None) -> int:
            return sum(
                int(row.get("count", 0))
                for ref, row in records.items()
                if ref != excluding and isinstance(row, Mapping)
                and row.get("status") == "detained" and row.get("place_ref") == place_ref
                and isinstance(row.get("count"), int) and not isinstance(row.get("count"), bool)
            )

        def leadership_basis(place: Mapping[str, Any], custodian_ref: str) -> str:
            if command.mode == "autonomous" and command.actor_id == custodian_ref:
                return "autonomous_custodian"
            authority_ref = place.get("authority_ref")
            candidates = [x for x in (authority_ref, custodian_ref) if isinstance(x, str) and x]
            for owner_ref in candidates:
                try:
                    decision = self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(
                        holder_ref=command.actor_id, owner_ref=owner_ref
                    )
                except CommandRejectedError:
                    continue
                if decision.allowed:
                    return decision.basis
            if command.actor_id == custodian_ref:
                return "direct_custodian"
            raise CommandRejectedError("custody_authority_denied")

        force_writes: Dict[str, Dict[str, Any]] = {}
        team_writes: Dict[str, Dict[str, Any]] = {}
        formation_writes: Dict[str, Dict[str, Any]] = {}
        person_writes: Dict[str, Dict[str, Any]] = {}
        population: Optional[Dict[str, Any]] = None
        authority_basis = "self_escape" if action == "escape" else ""

        if action == "detain":
            existing = records.get(custody_ref)
            pending = existing if isinstance(existing, dict) and existing.get("status") == "captured_pending_placement" else None
            if existing is not None and pending is None:
                raise CommandRejectedError("custody_record_exists")

            supplied_subject_ref = command.payload.get("subject_ref")
            supplied_force_ref = command.payload.get("force_ref")
            supplied_count = command.payload.get("count")
            if pending is not None:
                if pending.get("subject_kind") != "exact" or not isinstance(pending.get("subject_ref"), str):
                    raise CommandRejectedError("custody_pending_record_invalid")
                subject_kind = "exact"
                subject_ref = str(pending["subject_ref"])
                force_ref = pending.get("force_ref")
                count = 1
                if supplied_subject_ref is not None and _stable_id(supplied_subject_ref, "custody_subject_invalid") != subject_ref:
                    raise CommandRejectedError("custody_pending_subject_mismatch")
                if supplied_force_ref is not None and _stable_id(supplied_force_ref, "custody_force_invalid") != force_ref:
                    raise CommandRejectedError("custody_pending_force_mismatch")
                if supplied_count not in (None, 1):
                    raise CommandRejectedError("custody_exact_count_invalid")
                person_path, person = self._resolve_actor_for_write(subject_ref)
                readiness = person.get("condition", {}).get("readiness") if isinstance(person.get("condition"), Mapping) else None
                if readiness != "captured":
                    raise CommandRejectedError("custody_pending_subject_not_captured")
                person_writes[person_path] = person
            else:
                subject_ref = supplied_subject_ref
                force_ref = supplied_force_ref
                if subject_ref is not None:
                    subject_ref = _stable_id(subject_ref, "custody_subject_invalid")
                    if force_ref is not None:
                        force_ref = _stable_id(force_ref, "custody_force_invalid")
                    count = supplied_count if supplied_count is not None else 1
                    if count != 1:
                        raise CommandRejectedError("custody_exact_count_invalid")
                    subject_kind = "exact"
                    person_path, person = self._resolve_actor_for_write(subject_ref)
                    if person.get("life_status") not in ("active", "alive"):
                        raise CommandRejectedError("custody_subject_unavailable")
                    person_writes[person_path] = person
                else:
                    if force_ref is None:
                        raise CommandRejectedError("custody_subject_required")
                    force_ref = _stable_id(force_ref, "custody_force_invalid")
                    count = supplied_count
                    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                        raise CommandRejectedError("custody_count_invalid")
                    subject_kind = "aggregate"
                    subject_ref = None

            custodian_ref = _stable_id(command.payload.get("custodian_ref"), "custody_custodian_invalid")
            place_ref = _stable_id(command.payload.get("place_ref"), "custody_place_invalid")
            place, capacity, security = custody_module(place_ref)
            authority_basis = leadership_basis(place, custodian_ref)
            if occupancy(place_ref, excluding=custody_ref if pending is not None else None) + count > capacity:
                raise CommandRejectedError("custody_facility_capacity_exceeded")
            summary = text("summary", required_field=True)
            visibility = command.payload.get("visibility")
            if visibility not in ("public", "restricted", "secret"):
                raise CommandRejectedError("custody_visibility_invalid")

            if subject_kind == "exact":
                person = next(iter(person_writes.values()))
                old_location = person.get("current_location_id")
                person["current_location_id"] = place_ref
                life = person.get("life_course_state")
                history = life.get("location_history") if isinstance(life, dict) else None
                if not isinstance(history, list) or not history:
                    raise CommandRejectedError("person_location_history_missing")
                if old_location != place_ref:
                    history.append({"at": str(current_time), "location_id": place_ref, "reason": "detained"})
                    history[:] = history[-64:]
                try:
                    population = copy.deepcopy(self.repository.read_json(_POPULATION_REGISTRY_PATH))
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("population_registry_invalid") from exc
                readiness = person.get("condition", {}).get("readiness") if isinstance(person.get("condition"), Mapping) else None
                if readiness == "captured":
                    # Combat or a prior capture action already moved this body into
                    # captured/missing accounting. Facility placement must not
                    # capture the same person a second time.
                    pools = population.get("pools") if isinstance(population, Mapping) else None
                    if isinstance(pools, Mapping):
                        for pool in pools.values():
                            if not isinstance(pool, Mapping) or pool.get("category") != "shinobi_service":
                                continue
                            rep = pool.get("representation")
                            refs = rep.get("rostered_person_refs") if isinstance(rep, Mapping) else None
                            if isinstance(refs, list) and subject_ref in refs and isinstance(pool.get("linked_force_ref"), str):
                                force_ref = pool.get("linked_force_ref")
                                break
                else:
                    capture_info = self._reconcile_rostered_person_capture(
                        population,
                        person_ref=subject_ref,
                        force_writes=force_writes,
                        team_writes=team_writes,
                        formation_writes=formation_writes,
                    )
                    if capture_info is not None:
                        force_ref = str(capture_info.get("force_ref"))
            else:
                try:
                    force_path, _digest, force_view = self._resolve_covered_owner_view(force_ref, cache=_OwnerResolutionCache())
                except CommandRejectedError as exc:
                    raise CommandRejectedError("custody_force_invalid") from exc
                availability = force_view.get("availability") if isinstance(force_view, Mapping) else None
                captured = availability.get("captured_or_missing") if isinstance(availability, Mapping) else None
                if isinstance(captured, bool) or not isinstance(captured, int):
                    raise CommandRejectedError("custody_aggregate_not_captured")
                already_registered = sum(
                    int(other.get("count", 0))
                    for other_ref, other in records.items()
                    if other_ref != custody_ref and isinstance(other, Mapping)
                    and other.get("force_ref") == force_ref
                    and other.get("status") in ("detained", "captured_pending_placement")
                    and isinstance(other.get("count"), int) and not isinstance(other.get("count"), bool)
                )
                if already_registered + count > captured:
                    raise CommandRejectedError("custody_aggregate_not_captured")

            if pending is None:
                records[custody_ref] = {
                    "id": custody_ref, "subject_kind": subject_kind, "subject_ref": subject_ref,
                    "force_ref": force_ref, "count": count, "custodian_ref": custodian_ref,
                    "place_ref": place_ref, "status": "detained", "captured_at": str(current_time),
                    "detained_at": str(current_time), "source_combat_ref": None,
                    "updated_at": str(current_time), "summary": summary, "visibility": visibility,
                }
                event_kind = "custody_detained"
            else:
                pending["force_ref"] = force_ref
                pending["custodian_ref"] = custodian_ref
                pending["place_ref"] = place_ref
                pending["status"] = "detained"
                pending["detained_at"] = str(current_time)
                pending["updated_at"] = str(current_time)
                pending["summary"] = summary
                pending["visibility"] = visibility
                event_kind = "custody_placed"
            material_count = count
        else:
            record = records.get(custody_ref)
            if not isinstance(record, dict) or record.get("status") != "detained":
                raise CommandRejectedError("custody_record_not_active")
            subject_kind = record.get("subject_kind")
            count = record.get("count")
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                raise CommandRejectedError("custody_record_invalid")
            old_place_ref = record.get("place_ref")
            old_custodian_ref = record.get("custodian_ref")
            old_place, _old_capacity, old_security = custody_module(str(old_place_ref))
            if action == "escape":
                subject_ref = record.get("subject_ref")
                if subject_kind != "exact" or not isinstance(subject_ref, str) or command.actor_id != subject_ref:
                    raise CommandRejectedError("custody_escape_not_subject")
                person_path, person = self._resolve_actor_for_write(subject_ref)
                capability = self._combat_capability(person)
                escape_score = (capability.escape + capability.mobility + capability.stealth) * 5 // 3
                if escape_score < old_security:
                    raise CommandRejectedError("custody_escape_failed")
                escape_location = graph.anchor(str(old_place_ref))
                if not isinstance(escape_location, str) or not escape_location or escape_location == old_place_ref:
                    raise CommandRejectedError("custody_escape_destination_unresolved")
                person["current_location_id"] = escape_location
                life = person.get("life_course_state")
                history = life.get("location_history") if isinstance(life, dict) else None
                if not isinstance(history, list) or not history:
                    raise CommandRejectedError("person_location_history_missing")
                history.append({"at": str(current_time), "location_id": escape_location, "reason": "escaped custody"})
                history[:] = history[-64:]
                condition = person.get("condition")
                if isinstance(condition, dict) and condition.get("readiness") == "captured":
                    condition["readiness"] = "ready"
                person_writes[person_path] = person
                record["status"] = "escaped"
                event_kind = "custody_escaped"
            else:
                authority_basis = leadership_basis(old_place, str(old_custodian_ref))
                if action == "transfer":
                    new_custodian_ref = _stable_id(command.payload.get("new_custodian_ref"), "custody_new_custodian_invalid")
                    new_place_ref = _stable_id(command.payload.get("new_place_ref"), "custody_new_place_invalid")
                    new_place, capacity, _security = custody_module(new_place_ref)
                    receiving_basis = leadership_basis(new_place, new_custodian_ref)
                    if occupancy(new_place_ref, excluding=custody_ref) + count > capacity:
                        raise CommandRejectedError("custody_facility_capacity_exceeded")
                    authority_basis = f"{authority_basis};receiving:{receiving_basis}"
                    record["custodian_ref"] = new_custodian_ref
                    record["place_ref"] = new_place_ref
                    if subject_kind == "exact" and isinstance(record.get("subject_ref"), str):
                        person_path, person = self._resolve_actor_for_write(record["subject_ref"])
                        person["current_location_id"] = new_place_ref
                        life = person.get("life_course_state")
                        history = life.get("location_history") if isinstance(life, dict) else None
                        if not isinstance(history, list) or not history:
                            raise CommandRejectedError("person_location_history_missing")
                        history.append({"at": str(current_time), "location_id": new_place_ref, "reason": "custody transfer"})
                        history[:] = history[-64:]
                        person_writes[person_path] = person
                    event_kind = "custody_transferred"
                else:
                    record["status"] = "released" if action == "release" else "exchanged"
                    if subject_kind == "exact" and isinstance(record.get("subject_ref"), str):
                        person_path, person = self._resolve_actor_for_write(record["subject_ref"])
                        condition = person.get("condition")
                        if isinstance(condition, dict) and condition.get("readiness") == "captured":
                            condition["readiness"] = "ready"
                        person_writes[person_path] = person
                    event_kind = "custody_released" if action == "release" else "custody_exchanged"
            record["updated_at"] = str(current_time)
            supplied_summary = text("summary")
            if supplied_summary is not None:
                record["summary"] = supplied_summary
            supplied_visibility = command.payload.get("visibility")
            if supplied_visibility is not None:
                if supplied_visibility not in ("public", "restricted", "secret"):
                    raise CommandRejectedError("custody_visibility_invalid")
                record["visibility"] = supplied_visibility
            material_count = count

            if record.get("status") in ("released", "exchanged", "escaped"):
                force_ref = record.get("force_ref")
                if isinstance(force_ref, str):
                    try:
                        force_path, _digest, force_view = self._resolve_covered_owner_view(force_ref, cache=_OwnerResolutionCache())
                    except CommandRejectedError as exc:
                        raise CommandRejectedError("custody_force_invalid") from exc
                    force = copy.deepcopy(dict(force_view))
                    availability = force.get("availability")
                    captured = availability.get("captured_or_missing") if isinstance(availability, dict) else None
                    if isinstance(captured, bool) or not isinstance(captured, int) or captured < count:
                        raise CommandRejectedError("custody_force_capture_accounting_invalid")
                    availability["captured_or_missing"] = captured - count
                    return_class = "ready_24h"
                    if subject_kind == "exact" and isinstance(record.get("subject_ref"), str):
                        try:
                            person_path, person = self._resolve_actor_for_write(record["subject_ref"])
                            readiness = person.get("condition", {}).get("readiness") if isinstance(person.get("condition"), Mapping) else None
                            if readiness in ("injured", "incapacitated"):
                                return_class = "medical_or_recovery"
                            person_writes.setdefault(person_path, person)
                        except CommandRejectedError as exc:
                            raise CommandRejectedError("custody_subject_invalid") from exc
                    if not isinstance(availability.get(return_class), int) or isinstance(availability.get(return_class), bool):
                        raise CommandRejectedError("custody_force_capture_accounting_invalid")
                    availability[return_class] += count
                    if sum(availability.values()) != force.get("total"):
                        raise CommandRejectedError("custody_force_capture_accounting_invalid")
                    force_writes[force_path] = force

        world_events = self._world_events()
        active_record = records[custody_ref]
        event_id = self._append_semantic_event(
            world_events, command=command, kind=event_kind, at=current_time,
            host_refs=(str(active_record.get("custodian_ref")),),
            actor_refs=tuple(x for x in (command.actor_id, active_record.get("subject_ref")) if isinstance(x, str)),
            affected_owner_refs=(_CUSTODY_REGISTRY_PATH, *force_writes.keys(), *person_writes.keys(), *team_writes.keys(), *formation_writes.keys()),
            material_consequence_refs=(custody_ref,), classification=str(active_record.get("visibility", "restricted")),
            reducer_ref="shinobi_runtime.commands.custody_resolution",
        )
        writes: Dict[str, bytes] = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            _CUSTODY_REGISTRY_PATH: _json_bytes(registry),
            **self._world_event_writes(world_events),
        }
        if population is not None:
            writes[_POPULATION_REGISTRY_PATH] = _json_bytes(population)
        for path, value in {**force_writes, **team_writes, **formation_writes, **person_writes}.items():
            writes[path] = _json_bytes(value)
        writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("custody write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged = overlay.read_json(_CUSTODY_REGISTRY_PATH)
            row = staged.get("records", {}).get(custody_ref) if isinstance(staged, Mapping) else None
            if not isinstance(row, Mapping) or row.get("status") != active_record.get("status") or row.get("count") != material_count:
                raise ValueError("custody after-image invalid")
            if row.get("status") == "detained":
                _place, capacity, _security = custody_module(str(row.get("place_ref")))
                staged_records = staged.get("records", {})
                used = sum(
                    int(x.get("count", 0)) for x in staged_records.values()
                    if isinstance(x, Mapping) and x.get("status") == "detained" and x.get("place_ref") == row.get("place_ref")
                )
                if used > capacity:
                    raise ValueError("custody capacity exceeded after staging")
            for force_path in force_writes:
                staged_force = overlay.read_json(force_path)
                availability = staged_force.get("availability")
                if not isinstance(availability, Mapping) or sum(availability.values()) != staged_force.get("total"):
                    raise ValueError("custody force conservation failed")
            staged_records = staged.get("records", {}) if isinstance(staged, Mapping) else {}
            represented_by_force: Dict[str, int] = {}
            for custody_row in staged_records.values():
                if not isinstance(custody_row, Mapping) or custody_row.get("status") not in ("detained", "captured_pending_placement"):
                    continue
                custody_force = custody_row.get("force_ref")
                custody_count = custody_row.get("count")
                if not isinstance(custody_force, str) or isinstance(custody_count, bool) or not isinstance(custody_count, int) or custody_count <= 0:
                    continue
                represented_by_force[custody_force] = represented_by_force.get(custody_force, 0) + custody_count
            for custody_force, represented in represented_by_force.items():
                try:
                    force_path, _digest, _view = self._resolve_covered_owner_view(custody_force, cache=_OwnerResolutionCache())
                except CommandRejectedError as exc:
                    raise ValueError("custody force unresolved after staging") from exc
                staged_force = overlay.read_json(force_path) if force_path in overlay.changed_paths else self.repository.read_json(force_path)
                availability = staged_force.get("availability") if isinstance(staged_force, Mapping) else None
                captured = availability.get("captured_or_missing") if isinstance(availability, Mapping) else None
                if isinstance(captured, bool) or not isinstance(captured, int) or represented > captured:
                    raise ValueError("custody records exceed captured force personnel")

        return _BuiltPlan(
            code="custody_resolution_ready",
            affected_refs=expected,
            writes=writes,
            result={
                "custody_ref": custody_ref, "action": action,
                "status": active_record.get("status"), "place_ref": active_record.get("place_ref"),
                "custodian_ref": active_record.get("custodian_ref"), "count": active_record.get("count"),
                "authority_basis": authority_basis, "semantic_event_id": event_id,
            },
            validator=validate,
        )


