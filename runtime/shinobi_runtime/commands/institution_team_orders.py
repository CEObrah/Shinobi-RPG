"""Production semantic commands for secondary affiliations and exact-team orders.

These commands close two representation gaps that should not be solved by
rewriting career rank, legal House membership, or the player's own travel.
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal, ROUND_CEILING
from typing import Any, Dict, Mapping, Sequence, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import (
    _BuiltPlan,
    _campaign_datetime,
    _exact_payload,
    _json_bytes,
    _stable_id,
)
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import (
    ROUTES_PATH as _ROUTES_PATH,
    TRAVEL_MECHANICS_PATH as _TRAVEL_MECHANICS_PATH,
)
from shinobi_runtime.domain import LocationGraph
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


_SECONDARY_RELATIONSHIP_KINDS = frozenset(("honorary", "affiliate", "trainee", "associate", "staff"))


class InstitutionTeamOrdersMixin:
    """First-class secondary affiliation and leader-directed team movement."""

    def _institution_affiliation_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("action", "subject_ref", "institution_ref", "relationship_kind", "role", "grade", "reason", "visibility"),
            command.command_type,
        )
        action = command.payload["action"]
        if action not in ("grant", "update", "revoke"):
            raise CommandRejectedError("institution_affiliation_action_invalid")
        subject_ref = _stable_id(command.payload["subject_ref"], "institution_affiliation_subject_invalid")
        institution_ref = _stable_id(command.payload["institution_ref"], "institution_affiliation_institution_invalid")
        reason = command.payload["reason"]
        visibility = command.payload["visibility"]
        if not isinstance(reason, str) or not reason or len(reason) > 1000:
            raise CommandRejectedError("institution_affiliation_reason_invalid")
        if visibility not in ("public", "restricted", "secret"):
            raise CommandRejectedError("institution_affiliation_visibility_invalid")

        try:
            _institution_path, _digest, institution = self._resolve_covered_owner_view(institution_ref)
        except TypeError:
            # Older resolver signatures require an explicit cache, while the
            # production resolver may provide a default. Use the public domain
            # authority read below as the final authority check either way.
            institution = None
        except CommandRejectedError as exc:
            raise CommandRejectedError("institution_affiliation_institution_unresolved") from exc
        if institution is None:
            try:
                authority = self._domain_authority().owner_leadership(
                    holder_ref=command.actor_id, owner_ref=institution_ref
                )
            except Exception as exc:
                raise CommandRejectedError("institution_affiliation_institution_unresolved") from exc
        else:
            authority = self._domain_authority().owner_leadership(
                holder_ref=command.actor_id, owner_ref=institution_ref
            )
        if not authority.allowed:
            raise CommandRejectedError("institution_affiliation_authority_denied")

        path, subject = self._resolve_actor_for_write(subject_ref)
        if subject.get("schema") != "shinobi_character":
            raise CommandRejectedError("institution_affiliation_subject_not_character")
        affiliations = subject.setdefault("institutional_affiliations", {})
        if not isinstance(affiliations, dict):
            raise CommandRejectedError("institution_affiliations_invalid")
        previous = copy.deepcopy(affiliations.get(institution_ref))

        if action == "revoke":
            if command.payload["relationship_kind"] is not None or command.payload["role"] is not None or command.payload["grade"] is not None:
                raise CommandRejectedError("institution_affiliation_revoke_fields_invalid")
            if institution_ref not in affiliations:
                raise CommandRejectedError("institution_affiliation_missing")
            affiliations.pop(institution_ref)
            current = None
        else:
            relationship_kind = command.payload["relationship_kind"]
            role = command.payload["role"]
            grade = command.payload["grade"]
            if relationship_kind not in _SECONDARY_RELATIONSHIP_KINDS:
                raise CommandRejectedError("institution_affiliation_relationship_kind_invalid")
            if not isinstance(role, str) or not role or len(role) > 128:
                raise CommandRejectedError("institution_affiliation_role_invalid")
            if not isinstance(grade, str) or not grade or len(grade) > 128:
                raise CommandRejectedError("institution_affiliation_grade_invalid")
            if action == "grant" and institution_ref in affiliations:
                raise CommandRejectedError("institution_affiliation_already_exists")
            if action == "update" and institution_ref not in affiliations:
                raise CommandRejectedError("institution_affiliation_missing")
            current = {
                "institution_ref": institution_ref,
                "relationship_kind": relationship_kind,
                "role": role,
                "grade": grade,
                "status": "active",
                "effective_from": str(current_time),
                "granted_by": command.actor_id,
                "reason": reason,
                "visibility": visibility,
                "legal_membership_conferred": False,
            }
            affiliations[institution_ref] = current

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="institution_affiliation_changed",
            at=current_time,
            host_refs=(institution_ref,),
            actor_refs=(command.actor_id, subject_ref),
            affected_owner_refs=(path,),
            material_consequence_refs=(f"institution-affiliation:{subject_ref}:{institution_ref}:{action}",),
            classification=visibility,
            audience_refs=(command.actor_id, subject_ref),
            reducer_ref="shinobi_runtime.commands.institution_team_orders.institution_affiliation_resolution",
        )
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            path: _json_bytes(subject),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("institution affiliation write set changed after planning")
            self._assert_meta(
                overlay,
                manifest,
                meta_path=self.meta_path,
                command=command,
                world_time=current_time,
            )
            staged = overlay.read_json(path).get("institutional_affiliations")
            if not isinstance(staged, Mapping):
                raise ValueError("institution affiliations missing")
            if action == "revoke" and institution_ref in staged:
                raise ValueError("institution affiliation revoke did not persist")
            if action != "revoke" and staged.get(institution_ref) != current:
                raise ValueError("institution affiliation did not persist")

        return _BuiltPlan(
            code="institution_affiliation_resolution_ready",
            affected_refs=expected,
            writes=writes,
            result={
                "command_type": command.command_type,
                "action": action,
                "subject_ref": subject_ref,
                "institution_ref": institution_ref,
                "previous": previous,
                "current": current,
                "authority_basis": authority.basis,
                "semantic_event_id": event_id,
            },
            validator=validate,
        )

    def _team_movement_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("team_ref", "route_id", "destination_id", "traveler_refs", "summary"),
            command.command_type,
        )
        team_ref = _stable_id(command.payload["team_ref"], "team_movement_team_invalid", prefix="team.")
        route_id = _stable_id(command.payload["route_id"], "team_movement_route_invalid", prefix="route_")
        destination_id = _stable_id(command.payload["destination_id"], "team_movement_destination_invalid")
        summary = command.payload["summary"]
        if not isinstance(summary, str) or not summary or len(summary) > 1000:
            raise CommandRejectedError("team_movement_summary_invalid")
        raw_travelers = command.payload["traveler_refs"]
        if (
            not isinstance(raw_travelers, Sequence)
            or isinstance(raw_travelers, (str, bytes, bytearray))
            or not 1 <= len(raw_travelers) <= 16
            or any(not isinstance(ref, str) for ref in raw_travelers)
        ):
            raise CommandRejectedError("team_movement_party_invalid")
        traveler_refs = tuple(_stable_id(ref, "team_movement_traveler_invalid") for ref in raw_travelers)
        if len(set(traveler_refs)) != len(traveler_refs) or command.actor_id in traveler_refs:
            raise CommandRejectedError("team_movement_party_invalid")

        _team_path, team = self._exact_team(team_ref)
        if team.get("status") != "active":
            raise CommandRejectedError("team_movement_team_inactive")
        if command.actor_id not in (team.get("leader_ref"), team.get("deputy_ref")):
            raise CommandRejectedError("team_movement_authority_denied")
        members = team.get("member_refs")
        if not isinstance(members, list) or any(ref not in members for ref in traveler_refs):
            raise CommandRejectedError("team_movement_party_not_members")

        travelers: Dict[str, Tuple[str, Dict[str, Any]]] = {}
        for traveler_ref in traveler_refs:
            path, record = self._resolve_actor_for_write(traveler_ref)
            if record.get("life_status") not in ("active", "alive"):
                raise CommandRejectedError("team_movement_traveler_not_active")
            travelers[traveler_ref] = (path, record)
        origins = {record.get("current_location_id") for _path, record in travelers.values()}
        if len(origins) != 1:
            raise CommandRejectedError("team_movement_party_not_colocated")
        current_location = next(iter(origins))
        if not isinstance(current_location, str):
            raise CommandRejectedError("team_movement_origin_invalid")

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
        route = next(
            (item for item in location_graph.routes if isinstance(item, Mapping) and item.get("id") == route_id),
            None,
        )
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
            endpoints = (route.get("from"), route.get("to"))
            if origin_anchor not in endpoints or destination_id not in endpoints or destination_id == origin_anchor:
                raise CommandRejectedError("travel_route_endpoint_mismatch")
            reference_days = route.get("reference_travel_days")
            if isinstance(reference_days, bool) or not isinstance(reference_days, (int, float)) or reference_days <= 0:
                raise CommandRejectedError("travel_registry_invalid")
            status_multipliers = mechanics.get("route_status_multipliers") if isinstance(mechanics, Mapping) else None
            status_multiplier = status_multipliers.get(route.get("status")) if isinstance(status_multipliers, Mapping) else None
            if isinstance(status_multiplier, bool) or not isinstance(status_multiplier, (int, float)):
                raise CommandRejectedError("travel_registry_invalid")

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
        hours = Decimal(str(reference_days)) * Decimal(24) * Decimal(str(status_multiplier)) / speed
        seconds = int((hours * Decimal(3600)).to_integral_value(rounding=ROUND_CEILING))
        arrival = current_time.add_seconds(seconds)

        base = self._time_spanning_base(command, meta, current_time, target_time=arrival)
        if base.result.get("interrupted"):
            raise CommandRejectedError("time_boundary_requires_domain_settlement")
        if CampaignTime.parse(base.result["world_time"]) != arrival:
            raise CommandRejectedError("travel_time_settlement_incomplete")

        traveler_paths = []
        history_limit = getattr(self, "MAX_LOCATION_HISTORY", 64)
        for traveler_ref, (path, record) in travelers.items():
            life = record.get("life_course_state")
            if not isinstance(life, dict):
                raise CommandRejectedError("traveler_location_history_invalid")
            history = life.get("location_history")
            if not isinstance(history, list) or not history:
                raise CommandRejectedError("traveler_location_history_invalid")
            history.append({
                "at": str(arrival),
                "location_id": destination_id,
                "reason": f"completed authorized team movement via {route_id}",
            })
            history[:] = history[-history_limit:]
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
        # This order does not relocate the player. Keep the player's scene at the
        # player's authoritative location while the global clock advances.
        scene["scene_summary"] = (
            f"Authorized {team_ref} party movement completes at {arrival}; "
            f"{len(traveler_refs)} member(s) arrive at {destination_id}."
        )
        scene["decision_required"] = "Choose the next consequential action."
        world_events = self._world_events_after(base)
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="team_movement_completed",
            at=arrival,
            host_refs=(team_ref, current_location, destination_id),
            actor_refs=(command.actor_id, *traveler_refs),
            place_refs=(current_location, destination_id),
            affected_owner_refs=tuple(sorted(traveler_paths)),
            material_consequence_refs=tuple(
                f"location:{traveler_ref}:{destination_id}" for traveler_ref in traveler_refs
            ),
            audience_refs=(command.actor_id,),
            route_refs=(route_id,),
            reducer_ref="shinobi_runtime.commands.institution_team_orders.team_movement_resolution",
        )
        writes = dict(base.writes)
        writes[self.meta_path] = _json_bytes(self._meta_after(meta, command, world_time=arrival))
        writes[self.scene_path] = _json_bytes(scene)
        for _traveler_ref, (path, record) in travelers.items():
            writes[path] = _json_bytes(record)
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes))
        player_path, player = self._resolve_actor_for_write(command.actor_id)
        player_location = player.get("current_location_id")

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("team movement write set changed after planning")
            self._assert_meta(
                overlay,
                manifest,
                meta_path=self.meta_path,
                command=command,
                world_time=arrival,
            )
            for traveler_ref, (path, _record) in travelers.items():
                if overlay.read_json(path).get("current_location_id") != destination_id:
                    raise ValueError(f"team movement destination missing for {traveler_ref}")
            if overlay.read_json(player_path).get("current_location_id") != player_location:
                raise ValueError("team movement relocated the command actor")
            if overlay.read_json(self.scene_path).get("location_id") != player_location:
                raise ValueError("team movement displaced the player scene")
            self._scheduler_from_reader(overlay)

        return _BuiltPlan(
            code="team_movement_resolution_ready",
            affected_refs=expected,
            writes=writes,
            result={
                "command_type": command.command_type,
                "team_ref": team_ref,
                "route_id": route_id,
                "origin_id": current_location,
                "destination_id": destination_id,
                "traveler_refs": list(traveler_refs),
                "travel_seconds": seconds,
                "arrival_time": str(arrival),
                "authority_basis": "exact_team_leadership",
                "semantic_event_id": event_id,
                "summary": summary,
            },
            validator=validate,
        )


__all__ = ["InstitutionTeamOrdersMixin"]
