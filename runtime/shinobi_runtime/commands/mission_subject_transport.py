"""Mission-principal rendezvous, travel, and evidence mechanics.

A person named by a protect/escort briefing is a material mission dependency,
not narration metadata.  This mixin stages a generic exact person at the
briefing rendezvous when an accepted mission becomes active, carries that person
through mission-aware travel at the slowest party pace, and emits evidence that
can prove a no-contact protection objective without inventing combat.
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal, ROUND_CEILING
from typing import Any, Dict, Mapping, Sequence, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import (
    _BuiltPlan,
    _OwnerResolutionCache,
    _declared_payload,
    _json_bytes,
    _stable_id,
)
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import (
    ROUTES_PATH as _ROUTES_PATH,
    TRAVEL_MECHANICS_PATH as _TRAVEL_MECHANICS_PATH,
    WORLD_EVENT_REGISTRY_PATH as _WORLD_EVENT_REGISTRY_PATH,
)
from shinobi_runtime.domain import LocationGraph
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


_PRINCIPAL_MOVEMENT_OBJECTIVES = frozenset(("protect", "escort"))


class _OverlayWithoutExtraPaths:
    """Delegate an overlay while hiding composed paths from a base validator."""

    def __init__(self, overlay: StagedOverlay, extra_paths: Sequence[str]) -> None:
        self._overlay = overlay
        self._extra_paths = frozenset(extra_paths)

    @property
    def changed_paths(self) -> Tuple[str, ...]:
        return tuple(
            path for path in self._overlay.changed_paths if path not in self._extra_paths
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._overlay, name)


class MissionSubjectTransportMixin:
    """Bind typed person briefs to physical mission state."""

    def _mission_person_record(
        self,
        subject_ref: str,
        *,
        staged_writes: Mapping[str, bytes] | None = None,
    ) -> Tuple[str, Dict[str, Any]]:
        cache = _OwnerResolutionCache()
        try:
            path, _digest, view = self._resolve_covered_owner_view(
                subject_ref, cache=cache
            )
        except CommandRejectedError as exc:
            raise CommandRejectedError("mission_subject_owner_unresolved") from exc
        try:
            if staged_writes is not None and path in staged_writes:
                record = json.loads(staged_writes[path].decode("utf-8"))
            else:
                record = self.repository.read_json(path)
        except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CommandRejectedError("mission_subject_owner_invalid") from exc
        if (
            not isinstance(record, dict)
            or record.get("schema") != "person"
            or record.get("id") != subject_ref
            or not isinstance(view, Mapping)
            or view.get("id") != subject_ref
        ):
            raise CommandRejectedError("mission_subject_requires_exact_person")
        return path, copy.deepcopy(record)

    @staticmethod
    def _mission_person_speed(record: Mapping[str, Any]) -> Decimal:
        stats = record.get("stats")
        martial = stats.get("martial_skills") if isinstance(stats, Mapping) else None
        attributes = stats.get("attributes") if isinstance(stats, Mapping) else None
        movement = martial.get("movement") if isinstance(martial, Mapping) else None
        endurance = attributes.get("endurance") if isinstance(attributes, Mapping) else None
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (movement, endurance)
        ):
            raise CommandRejectedError("mission_subject_travel_capability_invalid")
        speed = Decimal("0.65") + Decimal(movement) / Decimal(200) + Decimal(endurance) / Decimal(500)
        return min(Decimal("1.80"), max(Decimal("0.50"), speed))

    @staticmethod
    def _brief_moves_person(owner: Any) -> bool:
        brief = getattr(owner, "briefing", None)
        return bool(
            brief is not None
            and brief.objective_kind in _PRINCIPAL_MOVEMENT_OBJECTIVES
            and brief.subject_kind == "person"
            and isinstance(brief.subject_ref, str)
            and brief.subject_ref
            and isinstance(brief.destination_place_ref, str)
            and brief.destination_place_ref
            and isinstance(brief.route_id, str)
            and brief.route_id
        )

    def _mission_transition(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        if command.payload.get("target_state") != "active":
            return super()._mission_transition(command, meta, current_time)

        mission_id = command.payload.get("mission_id")
        if not isinstance(mission_id, str):
            return super()._mission_transition(command, meta, current_time)
        _path, owner = self._read_mission(
            mission_id, actor_id=command.actor_id, current_time=current_time
        )
        if not self._brief_moves_person(owner):
            return super()._mission_transition(command, meta, current_time)

        brief = owner.briefing
        assert brief is not None and brief.subject_ref is not None
        scene = self._scene_base(current_time)
        if scene.get("location_id") != brief.report_place_ref:
            raise CommandRejectedError("mission_subject_rendezvous_required")

        subject_path, subject = self._mission_person_record(brief.subject_ref)
        subject_location = subject.get("loc")
        if not isinstance(subject_location, str):
            raise CommandRejectedError("mission_subject_location_invalid")
        graph = self._location_graph()
        try:
            if graph.anchor(subject_location) != graph.anchor(brief.report_place_ref):
                raise CommandRejectedError("mission_subject_not_at_rendezvous")
        except ValueError as exc:
            raise CommandRejectedError("mission_subject_location_invalid") from exc

        base = super()._mission_transition(command, meta, current_time)
        if subject_location == brief.report_place_ref:
            result = dict(base.result)
            result.update(
                {
                    "protected_subject_ref": brief.subject_ref,
                    "report_place_ref": brief.report_place_ref,
                }
            )
            return _BuiltPlan(
                code=base.code,
                affected_refs=base.affected_refs,
                writes=base.writes,
                result=result,
                validator=base.validator,
            )

        subject["loc"] = brief.report_place_ref
        if "resolved_through" in subject:
            subject["resolved_through"] = str(current_time)

        writes = dict(base.writes)
        writes[subject_path] = _json_bytes(subject)
        event_id = base.result.get("semantic_event_id")
        raw_events = writes.get(_WORLD_EVENT_REGISTRY_PATH)
        if not isinstance(event_id, str) or raw_events is None:
            raise CommandRejectedError("mission_subject_staging_history_missing")
        try:
            registry = json.loads(raw_events.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CommandRejectedError("world_event_registry_invalid") from exc
        events = registry.get("events") if isinstance(registry, dict) else None
        event = next(
            (
                item
                for item in events or []
                if isinstance(item, dict) and item.get("id") == event_id
            ),
            None,
        )
        if not isinstance(event, dict):
            raise CommandRejectedError("mission_subject_staging_history_missing")
        affected = event.get("affected_owner_refs")
        material = event.get("material_consequence_refs")
        if not isinstance(affected, list) or not isinstance(material, list):
            raise CommandRejectedError("world_event_registry_invalid")
        if subject_path not in affected:
            affected.append(subject_path)
            affected.sort()
        consequence = f"mission_subject_staged:{brief.subject_ref}:{brief.report_place_ref}"
        if consequence not in material:
            material.append(consequence)
            material.sort()
        writes[_WORLD_EVENT_REGISTRY_PATH] = _json_bytes(registry)
        expected_paths = tuple(sorted(writes))
        base_validator = base.validator

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            base_validator(_OverlayWithoutExtraPaths(overlay, (subject_path,)), manifest)
            if overlay.changed_paths != expected_paths:
                raise ValueError("mission subject staging write set changed after planning")
            staged = overlay.read_json(subject_path)
            if staged.get("loc") != brief.report_place_ref:
                raise ValueError("mission subject did not stage at briefing rendezvous")
            staged_events = overlay.read_json(_WORLD_EVENT_REGISTRY_PATH).get("events", [])
            staged_event = next(
                (
                    item
                    for item in staged_events
                    if isinstance(item, Mapping) and item.get("id") == event_id
                ),
                None,
            )
            if (
                not isinstance(staged_event, Mapping)
                or consequence not in staged_event.get("material_consequence_refs", [])
            ):
                raise ValueError("mission subject staging lacks semantic consequence")

        result = dict(base.result)
        result.update(
            {
                "protected_subject_ref": brief.subject_ref,
                "report_place_ref": brief.report_place_ref,
                "subject_staged": True,
            }
        )
        return _BuiltPlan(
            code=base.code,
            affected_refs=expected_paths,
            writes=writes,
            result=result,
            validator=validate,
        )

    def _travel_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        mission_raw = command.payload.get("mission_ref")
        if not isinstance(mission_raw, str):
            return super()._travel_resolution(command, meta, current_time)
        mission_id = _stable_id(
            mission_raw, "travel_mission_ref_invalid", prefix="mission."
        )
        _mission_path, owner = self._read_mission(
            mission_id, actor_id=command.actor_id, current_time=current_time
        )
        if not self._brief_moves_person(owner):
            return super()._travel_resolution(command, meta, current_time)
        if owner.mission.state != "active":
            raise CommandRejectedError("travel_mission_not_active")

        brief = owner.briefing
        assert brief is not None and brief.subject_ref is not None
        _declared_payload(command.payload, command.command_type)
        route_id = _stable_id(
            command.payload["route_id"], "travel_route_invalid", prefix="route_"
        )
        destination_id = _stable_id(
            command.payload["destination_id"], "travel_destination_invalid"
        )
        if route_id != brief.route_id or destination_id != brief.destination_place_ref:
            raise CommandRejectedError("mission_travel_briefing_mismatch")
        if brief.depart_by is not None and current_time > brief.depart_by:
            raise CommandRejectedError("mission_departure_window_missed")

        raw_travelers = command.payload["traveler_refs"]
        if (
            not isinstance(raw_travelers, Sequence)
            or isinstance(raw_travelers, (str, bytes, bytearray))
            or not 1 <= len(raw_travelers) <= 16
            or any(not isinstance(ref, str) for ref in raw_travelers)
        ):
            raise CommandRejectedError("travel_party_invalid")
        traveler_refs = tuple(
            _stable_id(ref, "travel_traveler_invalid") for ref in raw_travelers
        )
        if len(set(traveler_refs)) != len(traveler_refs) or command.actor_id not in traveler_refs:
            raise CommandRejectedError("travel_party_invalid")
        if any(ref not in set(owner.mission.participant_refs) for ref in traveler_refs):
            raise CommandRejectedError("travel_party_not_mission_participants")

        context_raw = command.payload.get("party_context_ref")
        party_context_ref = (
            None
            if context_raw is None
            else _stable_id(context_raw, "travel_party_context_invalid")
        )
        if len(traveler_refs) == 1:
            if party_context_ref is not None:
                raise CommandRejectedError("travel_party_context_invalid")
            party_authority_basis = "mission_travel"
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

        subject_path, subject = self._mission_person_record(brief.subject_ref)
        if subject.get("loc") != current_location:
            raise CommandRejectedError("mission_subject_not_with_travel_party")

        try:
            routes_record = self.repository.read_json(_ROUTES_PATH)
            mechanics = self.repository.read_json(_TRAVEL_MECHANICS_PATH)
            location_graph = LocationGraph(routes_record)
        except (FileNotFoundError, TypeError, ValueError) as exc:
            raise CommandRejectedError("travel_registry_invalid") from exc
        origin_anchor = location_graph.anchor(current_location)
        route = next(
            (
                item
                for item in location_graph.routes
                if isinstance(item, Mapping) and item.get("id") == route_id
            ),
            None,
        )
        if route is None:
            raise CommandRejectedError("travel_route_invalid")
        endpoints = (route.get("from"), route.get("to"))
        if (
            origin_anchor not in endpoints
            or destination_id not in endpoints
            or destination_id == origin_anchor
        ):
            raise CommandRejectedError("travel_route_endpoint_mismatch")
        reference_days = route.get("reference_travel_days")
        status_multipliers = (
            mechanics.get("route_status_multipliers")
            if isinstance(mechanics, Mapping)
            else None
        )
        if (
            isinstance(reference_days, bool)
            or not isinstance(reference_days, (int, float))
            or reference_days <= 0
            or not isinstance(status_multipliers, Mapping)
        ):
            raise CommandRejectedError("travel_registry_invalid")
        status_multiplier = status_multipliers.get(route.get("status"))
        if isinstance(status_multiplier, bool) or not isinstance(
            status_multiplier, (int, float)
        ):
            raise CommandRejectedError("travel_registry_invalid")

        speeds = []
        for _traveler_ref, (_path, record) in travelers.items():
            martial = record.get("martial_skills")
            attributes = record.get("attributes")
            movement = martial.get("movement") if isinstance(martial, Mapping) else None
            endurance = attributes.get("endurance") if isinstance(attributes, Mapping) else None
            if any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in (movement, endurance)
            ):
                raise CommandRejectedError("travel_capability_invalid")
            speed = Decimal("0.65") + Decimal(movement) / Decimal(200) + Decimal(endurance) / Decimal(500)
            speeds.append(min(Decimal("1.80"), max(Decimal("0.50"), speed)))
        speeds.append(self._mission_person_speed(subject))
        speed = min(speeds)
        hours = (
            Decimal(str(reference_days))
            * Decimal(24)
            * Decimal(str(status_multiplier))
            / speed
        )
        seconds = int((hours * Decimal(3600)).to_integral_value(rounding=ROUND_CEILING))
        arrival = current_time.add_seconds(seconds)
        base = self._time_spanning_base(
            command, meta, current_time, target_time=arrival
        )
        if base.result.get("interrupted"):
            raise CommandRejectedError("time_boundary_requires_domain_settlement")
        if CampaignTime.parse(base.result["world_time"]) != arrival:
            raise CommandRejectedError("travel_time_settlement_incomplete")

        traveler_paths = []
        for traveler_ref, (path, pretravel) in travelers.items():
            record = pretravel
            if path in base.writes:
                try:
                    staged = json.loads(base.writes[path].decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    raise CommandRejectedError("traveler_after_time_invalid") from exc
                if not isinstance(staged, dict):
                    raise CommandRejectedError("traveler_after_time_invalid")
                record = staged
            life = record.get("life_course_state")
            history = life.get("location_history") if isinstance(life, dict) else None
            if not isinstance(history, list) or not history:
                raise CommandRejectedError("traveler_location_history_invalid")
            history.append(
                {
                    "at": str(arrival),
                    "location_id": destination_id,
                    "reason": f"completed protected mission travel via {route_id}",
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
            travelers[traveler_ref] = (path, record)
            traveler_paths.append(path)

        subject_path, subject_after = self._mission_person_record(
            brief.subject_ref, staged_writes=base.writes
        )
        subject_after["loc"] = destination_id
        if "resolved_through" in subject_after:
            subject_after["resolved_through"] = str(arrival)

        scene = json.loads(base.writes[self.scene_path].decode("utf-8"))
        scene["location_id"] = destination_id
        scene["scene_summary"] = (
            f"Protected mission party and {brief.subject_ref} arrive at {destination_id} "
            f"at {arrival} via {route_id}."
        )
        scene["decision_required"] = "Choose the next consequential action at the destination."
        world_events = self._world_events_after(base)
        event_kind = (
            "protected_travel_completed"
            if brief.objective_kind == "protect"
            else "travel_completed"
        )
        consequence = f"protected_subject:{brief.subject_ref}:{destination_id}"
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind=event_kind,
            at=arrival,
            host_refs=(current_location, destination_id),
            actor_refs=tuple((*traveler_refs, brief.subject_ref)),
            place_refs=(current_location, destination_id),
            causal_refs=(mission_id,),
            affected_owner_refs=tuple(
                sorted((*traveler_paths, subject_path, self.scene_path))
            ),
            material_consequence_refs=tuple(
                [
                    *(f"location:{ref}:{destination_id}" for ref in traveler_refs),
                    consequence,
                ]
            ),
            audience_refs=(command.actor_id,),
            route_refs=(route_id,),
            reducer_ref="shinobi_runtime.commands.mission_subject_transport",
        )
        writes = dict(base.writes)
        writes[self.meta_path] = _json_bytes(
            self._meta_after(meta, command, world_time=arrival)
        )
        writes[self.scene_path] = _json_bytes(scene)
        for _traveler_ref, (path, record) in travelers.items():
            writes[path] = _json_bytes(record)
        writes[subject_path] = _json_bytes(subject_after)
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("protected travel write set changed after planning")
            self._assert_meta(
                overlay,
                manifest,
                meta_path=self.meta_path,
                command=command,
                world_time=arrival,
            )
            for traveler_ref, (path, _record) in travelers.items():
                if overlay.read_json(path).get("current_location_id") != destination_id:
                    raise ValueError(
                        f"protected travel destination was not persisted for {traveler_ref}"
                    )
            if overlay.read_json(subject_path).get("loc") != destination_id:
                raise ValueError("protected mission subject did not travel with party")
            if overlay.read_json(self.scene_path).get("location_id") != destination_id:
                raise ValueError("scene and protected travel party location diverged")
            CausalSchedulerRegistry.from_record(overlay.read_json(self.scheduler_path))
            events = overlay.read_json(_WORLD_EVENT_REGISTRY_PATH).get("events", [])
            event = next(
                (
                    item
                    for item in events
                    if isinstance(item, Mapping) and item.get("id") == event_id
                ),
                None,
            )
            if (
                not isinstance(event, Mapping)
                or event.get("kind") != event_kind
                or consequence not in event.get("material_consequence_refs", [])
            ):
                raise ValueError("protected travel lacks principal arrival evidence")

        return _BuiltPlan(
            code="travel_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "route_id": route_id,
                "origin_id": current_location,
                "destination_id": destination_id,
                "traveler_refs": list(traveler_refs),
                "protected_subject_ref": brief.subject_ref,
                "travel_seconds": seconds,
                "arrival_time": str(arrival),
                "mission_ref": mission_id,
                "party_authority_basis": party_authority_basis,
                "semantic_event_id": event_id,
            },
            validator=validate,
        )

    def _mission_objective_evidence(
        self,
        *,
        owner: Any,
        objective_id: str,
        target_status: str,
        progress_milli: int,
        evidence_event_id: str,
        current_time: CampaignTime,
    ) -> Tuple[str, str]:
        objective = owner.mission.objective_by_id.get(objective_id)
        if objective is not None and objective.kind == "protect":
            event, _digest = self._world_event_record_and_digest(evidence_event_id)
            if isinstance(event, Mapping) and event.get("kind") == "protected_travel_completed":
                brief = owner.briefing
                if (
                    target_status != "succeeded"
                    or progress_milli != 1000
                    or brief is None
                    or brief.subject_kind != "person"
                    or not isinstance(brief.subject_ref, str)
                    or not isinstance(brief.destination_place_ref, str)
                ):
                    raise CommandRejectedError("mission_protect_travel_evidence_invalid")
                expected = (
                    f"protected_subject:{brief.subject_ref}:{brief.destination_place_ref}"
                )
                consequences = event.get("material_consequence_refs")
                places = event.get("place_refs")
                causal = event.get("causal_refs")
                if (
                    not isinstance(consequences, list)
                    or expected not in consequences
                    or not isinstance(places, list)
                    or brief.destination_place_ref not in places
                    or not isinstance(causal, list)
                    or owner.mission_id not in causal
                ):
                    raise CommandRejectedError("mission_protect_travel_evidence_invalid")
        return super()._mission_objective_evidence(
            owner=owner,
            objective_id=objective_id,
            target_status=target_status,
            progress_milli=progress_milli,
            evidence_event_id=evidence_event_id,
            current_time=current_time,
        )


__all__ = ["MissionSubjectTransportMixin"]
