"""Extracted semantic command domain from the repository command planner.

The mixin owns domain reducers; orchestration, transaction framing, shared owner
resolution, and causal scheduler settlement remain on RepositoryCommandPlanner.
"""

from __future__ import annotations

import copy
import json
import re
from decimal import Decimal
from datetime import (
    datetime,
    timedelta,
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
    _BuiltPlan, _OwnerResolutionCache, _campaign_datetime, _exact_payload, _json_bytes, _stable_id,
)
from shinobi_runtime.commands.paths import (
    DEVELOPMENT_BANK_PATH as _DEVELOPMENT_BANK_PATH,
    ROUTES_PATH as _ROUTES_PATH,
    TEAM_TYPES_PATH as _TEAM_TYPES_PATH,
)
from shinobi_runtime.reducers import (
    TrainingInputs,
    settle_training,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import (
    CausalSchedulerRegistry,
    SchedulerHost,
    recurring_event,
)
from shinobi_runtime.sim.hosts import HostState
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.membership_routes import stage_team_change, team_refs_for_member
from shinobi_runtime.domain import LocationGraph
from shinobi_runtime.tx.manifest import TransactionManifest


class TeamCommandsMixin:
    def _team_service_villages(self, member_refs: Sequence[str]) -> tuple[str, ...]:
        try:
            pipeline = self.repository.read_json("state/reg/shinobi-career-pipeline.json")
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("shinobi_career_pipeline_invalid") from exc
        villages = pipeline.get("villages") if isinstance(pipeline, Mapping) else None
        if not isinstance(villages, Mapping):
            raise CommandRejectedError("shinobi_career_pipeline_invalid")
        result: set[str] = set()
        cache = _OwnerResolutionCache()
        for person_ref in member_refs:
            if not isinstance(person_ref, str):
                continue
            try:
                _path, _digest, person = self._resolve_covered_owner_view(person_ref, cache=cache)
            except CommandRejectedError:
                continue
            affiliation = person.get("village_or_affiliation") if isinstance(person, Mapping) else None
            if not isinstance(affiliation, str):
                continue
            matches = [key for key in villages if isinstance(key, str) and key.lower() in affiliation.lower()]
            if len(matches) == 1:
                result.add(matches[0])
        return tuple(sorted(result))

    def _generic_team_doctrine(
        self,
        team: Mapping[str, Any],
        *,
        at: CampaignTime,
        doctrine_identity: str,
        motto: str,
        training_focus: Sequence[str],
    ) -> Dict[str, Any]:
        team_id = team.get("id")
        leader = team.get("leader_ref")
        deputy = team.get("deputy_ref")
        members = team.get("member_refs")
        roles = team.get("roles")
        if (
            not isinstance(team_id, str)
            or not isinstance(leader, str)
            or not isinstance(members, list)
            or not members
            or not isinstance(roles, Mapping)
        ):
            raise CommandRejectedError("team_invalid")
        effective_deputy = deputy if isinstance(deputy, str) and deputy else next(
            (ref for ref in members if ref != leader), leader
        )
        instructors = team.get("training", {}).get("instructor_refs") if isinstance(team.get("training"), Mapping) else None
        if not isinstance(instructors, list) or not instructors:
            instructors = [leader]
        focus = [str(value) for value in training_focus if isinstance(value, str) and value]
        if not focus:
            focus = ["coordination", "mission fundamentals", "extraction"]
        doctrine_id = f"{team_id}.doctrine"
        primary = [leader]
        if effective_deputy != leader:
            primary.append(effective_deputy)
        return {
            "schema": "team-doctrine",
            "id": doctrine_id,
            "team_id": team_id,
            "name": f"{team.get('name', team_id)} Doctrine",
            "status": "active",
            "familiarity": {ref: 0 for ref in members},
            "effective_from": str(at),
            "approved_by": leader,
            "motto": motto,
            "command": {
                "captain": leader,
                "deputy": effective_deputy,
                "succession_order": [ref for ref in members if ref != leader][:3],
            },
            "roles": {str(k): str(v) for k, v in roles.items()},
            "phases": [
                {
                    "order": 1,
                    "name": "ASSESS",
                    "primary_members": primary,
                    "objective": "Establish the mission picture, authority, threats, and viable routes before committing the team.",
                    "procedures": ["Share verified observations.", "Confirm the immediate objective and constraints."],
                },
                {
                    "order": 2,
                    "name": "COORDINATE",
                    "primary_members": list(members),
                    "objective": "Concentrate complementary roles on the same mission problem instead of fragmenting into unrelated actions.",
                    "procedures": ["Maintain command signals.", "Use roles as responsibilities rather than isolated duels."],
                },
                {
                    "order": 3,
                    "name": "COMPLETE",
                    "primary_members": primary,
                    "objective": "Close the assigned objective while preserving the team and required information or captives.",
                    "procedures": ["Prefer the mission objective over unnecessary attrition.", "Transition to extraction once the objective is secured."],
                },
                {
                    "order": 4,
                    "name": "EXTRACT",
                    "primary_members": primary,
                    "objective": "Return personnel, casualties, prisoners, evidence, and reports through lawful channels.",
                    "procedures": ["Account for every member.", "Preserve classified information and evidence custody."],
                },
            ],
            "standing_rules": [
                {"name": "Mission authority", "rule": "Team action remains inside lawful assignment authority.", "procedures": ["Escalate when the mission exceeds granted authority."]},
                {"name": "Shared fight", "rule": "The team coordinates on common objectives rather than seeking disconnected personal engagements.", "procedures": ["Support the member currently carrying the decisive objective burden."]},
            ],
            "mission_modes": [
                {"mode": "reconnaissance", "directive": "Observe, verify, preserve secrecy, and report."},
                {"mode": "capture", "directive": "Control and restrain the authorized target while preserving required evidence."},
                {"mode": "protection", "directive": "Keep the protected person or asset viable and preserve an extraction route."},
                {"mode": "direct_combat", "directive": "Use coordinated force only as required to secure the assigned objective."},
            ],
            "contingencies": {"loss_of_control": ["Re-establish contact and command.", "Account for members and casualties.", "Create a viable withdrawal or regrouping route."]},
            "extraction": {"primary_members": primary, "procedures": ["Account for personnel and material objectives.", "Move through the safest lawful route consistent with mission urgency."]},
            "training": {
                "lead_instructors": list(dict.fromkeys(instructors)),
                "scheduled_sessions": ["Training is scheduled as a team commitment and grants credit only for actual attendance."],
                "shared_drills": focus,
                "role_focus": {ref: str(roles.get(ref, "team role execution")) for ref in members},
                "attendance_rule": "Only attended training supported by time, location, health, instructor, and facility state grants mechanical credit.",
                "interrupt_rule": "Missions, injury, travel, higher lawful orders, or missing facilities interrupt training without make-up double counting.",
                "no_double_counting": True,
            },
            "identity": doctrine_identity,
        }
    @staticmethod
    def _team_state_path(team_id: str) -> str:
        slug = re.sub(r"[^a-z0-9._-]+", "-", team_id.lower()).replace(".", "-")
        return f"state/team/{slug}.json"
    @staticmethod
    def _team_doctrine_path(team_id: str) -> str:
        slug = re.sub(r"[^a-z0-9._-]+", "-", team_id.lower()).replace(".", "-")
        return f"state/team/doctrine/{slug}.json"
    def _register_exact_team_state(
        self,
        *,
        team_id: str,
        name: str,
        team_type: str,
        parent_institution_ref: Optional[str],
        assignment_authority_ref: str,
        leader_ref: str,
        member_refs: Sequence[str],
        roles: Mapping[str, str],
        classification: str,
        at: CampaignTime,
        basis: str,
        scheduler: CausalSchedulerRegistry,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Tuple[str, Dict[str, Any]]:
        """Create one exact-team owner and register it with shared authorities."""

        path = self._team_state_path(team_id)
        if path in record_writes or self.repository.read_optional_bytes(path) is not None:
            raise CommandRejectedError("team_already_exists")
        members = [str(value) for value in member_refs]
        self._validate_team_type_roster(team_type, members)
        if (
            len(members) < 2
            or len(members) > 16
            or len(set(members)) != len(members)
            or leader_ref not in members
        ):
            raise CommandRejectedError("team_roster_invalid")
        for ref in members:
            try:
                self._resolve_covered_owner(ref, cache=_OwnerResolutionCache())
            except CommandRejectedError as exc:
                raise CommandRejectedError("team_member_unresolved") from exc
        role_map = {str(ref): str(roles.get(ref, "operative")) for ref in members}
        deputy_ref = next((ref for ref in members if ref != leader_ref), None)
        team = {
            "schema": "exact-team",
            "id": team_id,
            "name": name,
            "status": "active",
            "team_type": team_type,
            "parent_institution_ref": parent_institution_ref,
            "assignment_authority_ref": assignment_authority_ref,
            "leader_ref": leader_ref,
            "deputy_ref": deputy_ref,
            "member_refs": members,
            "roles": role_map,
            "classification": classification,
            "activation": {"at": str(at), "basis": basis},
            "doctrine_ref": None,
            "current_assignment_ref": None,
            "embedded_member_refs": [],
            "training": {
                "model_ref": "training.team",
                "instructor_refs": [leader_ref],
                "facility_refs": [],
                "recent_sessions": [],
            },
        }
        record_writes[path] = team

        def load(path_ref: str) -> Dict[str, Any]:
            if path_ref in record_writes:
                return record_writes[path_ref]
            try:
                raw = self.repository.read_json(path_ref)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("team_registry_invalid") from exc
            if not isinstance(raw, dict):
                raise CommandRejectedError("team_registry_invalid")
            record_writes[path_ref] = copy.deepcopy(raw)
            return record_writes[path_ref]

        registry = load("state/team/registry.json")
        active = registry.get("active_teams")
        if not isinstance(active, list):
            raise CommandRejectedError("team_registry_invalid")
        if team_id not in active:
            active.append(team_id)
            active.sort()
        try:
            stage_team_change(
                self.repository, record_writes, team_ref=team_id,
                after_members=member_refs, after_parent=parent_institution_ref,
                after_services=self._team_service_villages(member_refs),
            )
        except ValueError as exc:
            raise CommandRejectedError("membership_routes_invalid") from exc

        team_index = load("state/index/owners/team.json")
        owners = team_index.get("owners")
        if not isinstance(owners, dict):
            raise CommandRejectedError("team_owner_index_invalid")
        is_new_owner = team_id not in owners
        owners[team_id] = path
        owner_index = load("state/index/owners.json")
        if is_new_owner:
            count = owner_index.get("owner_count")
            if isinstance(count, int) and not isinstance(count, bool):
                owner_index["owner_count"] = count + 1

        host_id = "host.team." + team_id
        if host_id not in scheduler.hosts:
            due = at.add_seconds(7 * 24 * 60 * 60)
            scheduler.add_host(
                SchedulerHost(
                    state=HostState(
                        host_id=host_id, kind="exact_team", resolved_through=at,
                        safe_through=due.add_seconds(-1), handler_ref="causal.scheduler",
                        rng_namespace=team_id, next_due=due,
                    ),
                    authority_kind="exact_team", owner_ref=path, metadata={},
                )
            )
            scheduler.upsert_event(
                recurring_event(
                    kind="team.periodic_review", identity=team_id, host_id=host_id, due_at=due,
                    recurrence={"kind": "fixed_interval", "interval_seconds": 604800, "accrual_mode": "boundary_only"},
                    payload={"owner_ref": path}, priority=75, visibility="hidden", requires_player=False,
                )
            )
        return path, team
    def _team_development_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        """Adopt or revise one exact team's doctrine/training setup.

        The runtime knows only the generic exact-team model. Every exact team
        uses this same command; identity, doctrine, drills, instructors and
        facilities are data rather than planner branches.
        """

        _exact_payload(
            command.payload,
            (
                "team_ref",
                "doctrine_identity",
                "motto",
                "training_focus",
                "instructor_refs",
                "facility_refs",
            ),
            command.command_type,
        )
        team_ref = _stable_id(command.payload["team_ref"], "team_ref_invalid", prefix="team.")
        team_path, team_view = self._exact_team(team_ref)
        team = copy.deepcopy(dict(team_view))
        if team.get("status") != "active":
            raise CommandRejectedError("team_inactive")
        if team.get("leader_ref") != command.actor_id:
            raise CommandRejectedError("team_development_requires_leader")

        doctrine_identity = command.payload["doctrine_identity"]
        motto = command.payload["motto"]
        training_focus = command.payload["training_focus"]
        instructor_refs = command.payload["instructor_refs"]
        facility_refs = command.payload["facility_refs"]
        if not isinstance(doctrine_identity, str) or not doctrine_identity.strip():
            raise CommandRejectedError("team_doctrine_identity_invalid")
        if not isinstance(motto, str) or not motto.strip():
            raise CommandRejectedError("team_doctrine_motto_invalid")
        if (
            not isinstance(training_focus, Sequence)
            or isinstance(training_focus, (str, bytes, bytearray))
            or not training_focus
            or len(training_focus) > 16
            or any(not isinstance(value, str) or not value.strip() for value in training_focus)
        ):
            raise CommandRejectedError("team_training_focus_invalid")
        if (
            not isinstance(instructor_refs, Sequence)
            or isinstance(instructor_refs, (str, bytes, bytearray))
            or not instructor_refs
            or len(instructor_refs) > 16
            or len(set(instructor_refs)) != len(instructor_refs)
            or any(not isinstance(value, str) or not value for value in instructor_refs)
        ):
            raise CommandRejectedError("team_instructors_invalid")
        if (
            not isinstance(facility_refs, Sequence)
            or isinstance(facility_refs, (str, bytes, bytearray))
            or len(facility_refs) > 16
            or len(set(facility_refs)) != len(facility_refs)
            or any(not isinstance(value, str) or not value for value in facility_refs)
        ):
            raise CommandRejectedError("team_facilities_invalid")

        # Instructors may be team members or outside specialists, but every
        # named instructor must resolve as a persistent person.
        for instructor_ref in instructor_refs:
            try:
                _path, _digest, view = self._resolve_covered_owner_view(
                    instructor_ref, cache=_OwnerResolutionCache()
                )
            except CommandRejectedError as exc:
                raise CommandRejectedError("team_instructor_unresolved") from exc
            if not isinstance(view, Mapping):
                raise CommandRejectedError("team_instructor_unresolved")

        training = team.get("training")
        if not isinstance(training, dict):
            raise CommandRejectedError("team_training_invalid")
        training["model_ref"] = "training.team"
        training["instructor_refs"] = list(instructor_refs)
        training["facility_refs"] = list(facility_refs)

        doctrine_ref = team.get("doctrine_ref")
        doctrine_path = self._team_doctrine_path(team_ref)
        existing_doctrine = None
        if isinstance(doctrine_ref, str) and doctrine_ref:
            try:
                resolved_path, _digest, doctrine_view = self._resolve_covered_owner_view(
                    doctrine_ref, cache=_OwnerResolutionCache()
                )
                if isinstance(doctrine_view, Mapping) and doctrine_view.get("schema") == "team-doctrine":
                    doctrine_path = resolved_path
                    existing_doctrine = copy.deepcopy(dict(doctrine_view))
            except CommandRejectedError:
                existing_doctrine = None

        is_new_doctrine = existing_doctrine is None
        if is_new_doctrine:
            doctrine = self._generic_team_doctrine(
                team,
                at=current_time,
                doctrine_identity=doctrine_identity.strip(),
                motto=motto.strip(),
                training_focus=tuple(value.strip() for value in training_focus),
            )
            doctrine_ref = str(doctrine["id"])
            team["doctrine_ref"] = doctrine_ref
            doctrine_path = self._team_doctrine_path(team_ref)
        else:
            doctrine = existing_doctrine
            doctrine_ref = str(doctrine.get("id"))
            doctrine["effective_from"] = str(current_time)
            doctrine["approved_by"] = command.actor_id
            doctrine["identity"] = doctrine_identity.strip()
            doctrine["motto"] = motto.strip()
            doctrine_training = doctrine.get("training")
            if not isinstance(doctrine_training, dict):
                raise CommandRejectedError("team_doctrine_invalid")
            doctrine_training["lead_instructors"] = list(instructor_refs)
            doctrine_training["shared_drills"] = [value.strip() for value in training_focus]
            members = [value for value in team.get("member_refs", []) if isinstance(value, str)]
            doctrine_training["role_focus"] = {
                member: str(team.get("roles", {}).get(member, "team role execution"))
                for member in members
            }
            familiarity = doctrine.get("familiarity")
            if not isinstance(familiarity, dict):
                familiarity = {}
            doctrine["familiarity"] = {
                member: max(0, min(100, int(familiarity.get(member, 0))))
                for member in members
            }

        team_index = copy.deepcopy(self.repository.read_json("state/index/owners/team.json"))
        owners = team_index.get("owners") if isinstance(team_index, dict) else None
        if not isinstance(owners, dict):
            raise CommandRejectedError("team_owner_index_invalid")
        owner_index = copy.deepcopy(self.repository.read_json("state/index/owners.json"))
        if not isinstance(owner_index, dict):
            raise CommandRejectedError("owner_index_invalid")
        if doctrine_ref not in owners:
            owners[doctrine_ref] = doctrine_path
            count = owner_index.get("owner_count")
            if isinstance(count, int) and not isinstance(count, bool):
                owner_index["owner_count"] = count + 1
        else:
            owners[doctrine_ref] = doctrine_path

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="team_development_changed",
            at=current_time,
            host_refs=(team_ref,),
            actor_refs=(command.actor_id,),
            affected_owner_refs=(team_ref, doctrine_ref),
            material_consequence_refs=(
                f"doctrine:{doctrine_ref}:updated",
                "training_model:training.team",
            ),
            audience_refs=tuple(team.get("member_refs", [])),
            reducer_ref="shinobi_runtime.commands.team_development_resolution",
        )
        scene = copy.deepcopy(self._scene_base(current_time))
        scene["scene_summary"] = f"{team.get('name', team_ref)} updates its doctrine and team training plan."
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            self.scene_path: _json_bytes(scene),
            team_path: _json_bytes(team),
            doctrine_path: _json_bytes(doctrine),
            "state/index/owners/team.json": _json_bytes(team_index),
            "state/index/owners.json": _json_bytes(owner_index),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("team development write set changed after planning")
            self._assert_meta(
                overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time
            )
            staged_team = overlay.read_json(team_path)
            staged_doctrine = overlay.read_json(doctrine_path)
            if staged_team.get("doctrine_ref") != doctrine_ref:
                raise ValueError("team doctrine reference not persisted")
            if staged_team.get("training", {}).get("model_ref") != "training.team":
                raise ValueError("team training model diverged from generic team model")
            if staged_doctrine.get("team_id") != team_ref:
                raise ValueError("team doctrine owner mismatch")

        return _BuiltPlan(
            code="team_development_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "team_ref": team_ref,
                "doctrine_ref": doctrine_ref,
                "training_model_ref": "training.team",
                "semantic_event_id": event_id,
            },
            validator=validate,
        )
    def _team_lifecycle_resolution(
        self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            (
                "action", "team_ref", "name", "team_type", "parent_institution_ref",
                "assignment_authority_ref", "leader_ref", "member_refs", "roles",
                "classification", "assignment_ref", "reason",
            ),
            command.command_type,
        )
        action = command.payload["action"]
        if action not in ("form", "reorganize", "assign", "unassign", "dissolve"):
            raise CommandRejectedError("team_lifecycle_action_invalid")
        team_ref = _stable_id(command.payload["team_ref"], "team_ref_invalid", prefix="team.")
        scheduler = self._load_scheduler(current_time=current_time, scene=self._scene_base(current_time))
        record_writes: Dict[str, Dict[str, Any]] = {}
        world_events = self._world_events()
        requested_classification = command.payload["classification"]

        if action == "form":
            classification = requested_classification
            if classification not in ("public", "restricted", "secret"):
                raise CommandRejectedError("team_classification_invalid")
            authority_ref = _stable_id(command.payload["assignment_authority_ref"], "team_authority_invalid")
            authority = self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(
                holder_ref=command.actor_id, owner_ref=authority_ref
            )
            if not authority.allowed:
                raise CommandRejectedError("team_formation_not_authorized")
            member_refs = command.payload["member_refs"]
            roles = command.payload["roles"]
            if (
                not isinstance(member_refs, Sequence)
                or isinstance(member_refs, (str, bytes, bytearray))
                or not isinstance(roles, Mapping)
            ):
                raise CommandRejectedError("team_roster_invalid")
            leader_ref = _stable_id(command.payload["leader_ref"], "team_leader_invalid")
            name = command.payload["name"]
            team_type = command.payload["team_type"]
            parent = command.payload["parent_institution_ref"]
            if not isinstance(name, str) or not name.strip() or not isinstance(team_type, str) or not team_type.strip():
                raise CommandRejectedError("team_identity_invalid")
            if parent is not None and (not isinstance(parent, str) or not parent):
                raise CommandRejectedError("team_parent_invalid")
            team_path, team = self._register_exact_team_state(
                team_id=team_ref, name=name.strip(), team_type=team_type.strip(),
                parent_institution_ref=parent, assignment_authority_ref=authority_ref,
                leader_ref=leader_ref, member_refs=member_refs, roles=roles, classification=classification,
                at=current_time, basis=str(command.payload["reason"] or f"Lawful formation by {command.actor_id}."),
                scheduler=scheduler, record_writes=record_writes,
            )
            event_kind = "exact_team_formed"
        else:
            team_path, team_view = self._exact_team(team_ref)
            team = copy.deepcopy(dict(team_view))
            persisted_classification = team.get("classification")
            if persisted_classification not in ("public", "restricted", "secret"):
                raise CommandRejectedError("team_classification_invalid")
            if action == "reorganize":
                classification = requested_classification
                if classification not in ("public", "restricted", "secret"):
                    raise CommandRejectedError("team_classification_invalid")
            else:
                if requested_classification is not None and requested_classification != persisted_classification:
                    raise CommandRejectedError("team_classification_mismatch")
                classification = persisted_classification
            current_authority = str(team.get("assignment_authority_ref") or "")
            leader = team.get("leader_ref")
            deputy = team.get("deputy_ref")
            authority = self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(
                holder_ref=command.actor_id, owner_ref=current_authority
            ) if current_authority else None
            if command.actor_id not in (leader, deputy) and not (authority and authority.allowed):
                raise CommandRejectedError("team_lifecycle_not_authorized")
            record_writes[team_path] = team
            registry = copy.deepcopy(self.repository.read_json("state/team/registry.json"))
            record_writes["state/team/registry.json"] = registry
            active = registry.get("active_teams")
            if not isinstance(active, list):
                raise CommandRejectedError("team_registry_invalid")
            route_before_members = tuple(ref for ref in team.get("member_refs", []) if isinstance(ref, str))
            route_before_parent = team.get("parent_institution_ref") if isinstance(team.get("parent_institution_ref"), str) else None
            route_before_services = self._team_service_villages(route_before_members)
            route_before_assignment = team.get("current_assignment_ref") if isinstance(team.get("current_assignment_ref"), str) else None
            if action == "reorganize":
                member_refs = command.payload["member_refs"]
                roles = command.payload["roles"]
                leader_ref = command.payload["leader_ref"]
                if (
                    not isinstance(member_refs, Sequence)
                    or isinstance(member_refs, (str, bytes, bytearray))
                    or not isinstance(roles, Mapping)
                    or not isinstance(leader_ref, str)
                ):
                    raise CommandRejectedError("team_roster_invalid")
                if len(member_refs) < 2 or len(member_refs) > 16 or len(set(member_refs)) != len(member_refs) or leader_ref not in member_refs:
                    raise CommandRejectedError("team_roster_invalid")
                prospective_type = str(command.payload["team_type"] or team.get("team_type") or "")
                self._validate_team_type_roster(prospective_type, member_refs)
                for ref in member_refs:
                    self._resolve_covered_owner(ref, cache=_OwnerResolutionCache())
                team["member_refs"] = list(member_refs)
                team["leader_ref"] = leader_ref
                team["deputy_ref"] = next((ref for ref in member_refs if ref != leader_ref), None)
                team["roles"] = {str(ref): str(roles.get(ref, "operative")) for ref in member_refs}
                team["classification"] = classification
                current_assignment = team.get("current_assignment_ref")
                if isinstance(current_assignment, str) and current_assignment:
                    _formation_path, _force_ref, assigned_formation = self._formation_by_id(current_assignment)
                    formation_total = assigned_formation.get("personnel_total")
                    formation_location = assigned_formation.get("location_ref")
                    if (
                        isinstance(formation_total, bool)
                        or not isinstance(formation_total, int)
                        or formation_total < len(member_refs)
                        or not isinstance(formation_location, str)
                    ):
                        raise CommandRejectedError("team_assignment_exceeds_formation_strength")
                    for ref in member_refs:
                        existing_assignment, _existing_team_refs = self._exact_team_assignment_for_person(ref)
                        if existing_assignment is not None and existing_assignment != current_assignment:
                            raise CommandRejectedError("team_member_assignment_conflict")
                        try:
                            _member_path, _member_digest, member_view = self._resolve_covered_owner_view(
                                ref, cache=_OwnerResolutionCache()
                            )
                        except CommandRejectedError as exc:
                            raise CommandRejectedError("team_member_unresolved") from exc
                        if member_view.get("current_location_id") != formation_location:
                            raise CommandRejectedError("team_member_not_at_assigned_formation")
                    # Reorganization changes which exact identities overlay the
                    # already-counted formation bodies; it does not change the
                    # formation's aggregate headcount.
                    team["embedded_member_refs"] = list(member_refs)
                else:
                    team["embedded_member_refs"] = []
                if command.payload["name"] is not None:
                    team["name"] = str(command.payload["name"])
                if command.payload["team_type"] is not None:
                    team["team_type"] = str(command.payload["team_type"])
                training = team.get("training")
                if isinstance(training, dict):
                    instructors = training.get("instructor_refs", [])
                    if not isinstance(instructors, list) or any(not isinstance(ref, str) for ref in instructors):
                        raise CommandRejectedError("team_training_invalid")
                    # Outside specialists remain lawful instructors across roster
                    # changes. Reorganization changes the team roster, not the
                    # identity of already-authorized instructors.
                    if not instructors:
                        training["instructor_refs"] = [leader_ref]
                doctrine_ref = team.get("doctrine_ref")
                if isinstance(doctrine_ref, str) and doctrine_ref:
                    try:
                        doctrine_path, _digest, doctrine_view = self._resolve_covered_owner_view(doctrine_ref, cache=_OwnerResolutionCache())
                        doctrine = copy.deepcopy(dict(doctrine_view))
                        familiarity = doctrine.get("familiarity") if isinstance(doctrine.get("familiarity"), Mapping) else {}
                        doctrine["familiarity"] = {ref: int(familiarity.get(ref, 0)) for ref in member_refs}
                        doctrine["roles"] = dict(team["roles"])
                        record_writes[doctrine_path] = doctrine
                    except CommandRejectedError as exc:
                        raise CommandRejectedError("team_doctrine_invalid") from exc
                event_kind = "exact_team_reorganized"
            elif action == "assign":
                assignment_ref = _stable_id(
                    command.payload.get("assignment_ref"),
                    "team_assignment_ref_invalid",
                    prefix="formation.",
                )
                if team.get("current_assignment_ref") not in (None, assignment_ref):
                    raise CommandRejectedError("team_already_assigned")
                _formation_path, force_ref, _formation = self._formation_by_id(assignment_ref)
                formation_total = _formation.get("personnel_total")
                formation_location = _formation.get("location_ref")
                members = [ref for ref in team.get("member_refs", []) if isinstance(ref, str)]
                if (
                    isinstance(formation_total, bool)
                    or not isinstance(formation_total, int)
                    or formation_total < len(members)
                    or not isinstance(formation_location, str)
                ):
                    raise CommandRejectedError("team_assignment_exceeds_formation_strength")
                for ref in members:
                    existing_assignment, _existing_team_refs = self._exact_team_assignment_for_person(ref)
                    if existing_assignment is not None and existing_assignment != assignment_ref:
                        raise CommandRejectedError("team_member_assignment_conflict")
                    try:
                        _member_path, _member_digest, member_view = self._resolve_covered_owner_view(
                            ref, cache=_OwnerResolutionCache()
                        )
                    except CommandRejectedError as exc:
                        raise CommandRejectedError("team_member_unresolved") from exc
                    if member_view.get("current_location_id") != formation_location:
                        raise CommandRejectedError("team_member_not_at_assigned_formation")
                try:
                    _force_path, _force_digest, force_view = self._resolve_covered_owner_view(
                        force_ref, cache=_OwnerResolutionCache()
                    )
                except CommandRejectedError as exc:
                    raise CommandRejectedError("team_assignment_force_unresolved") from exc
                if not isinstance(force_view, Mapping) or force_view.get("schema") != "force":
                    raise CommandRejectedError("team_assignment_force_unresolved")
                force_authority = self._domain_authority(cache=_OwnerResolutionCache())
                force_grant = force_authority.force_grant(
                    grantor_ref=command.actor_id, force_record=force_view
                )
                if not force_grant.allowed:
                    command_decision = force_authority.force_command(
                        commander_ref=command.actor_id,
                        force_ref=force_ref,
                        operational_attachment_ref=assignment_ref,
                        named_actor_refs=tuple(
                            ref for ref in team.get("member_refs", []) if isinstance(ref, str)
                        ),
                        committed_count=len(team.get("member_refs", [])),
                        effective_at=str(current_time),
                    )
                    if not command_decision.allowed:
                        raise CommandRejectedError("team_assignment_force_not_authorized")
                team["current_assignment_ref"] = assignment_ref
                team["embedded_member_refs"] = members
                event_kind = "exact_team_assigned"
            elif action == "unassign":
                current_assignment = team.get("current_assignment_ref")
                requested_assignment = command.payload.get("assignment_ref")
                if current_assignment is None:
                    raise CommandRejectedError("team_not_assigned")
                if requested_assignment is not None and requested_assignment != current_assignment:
                    raise CommandRejectedError("team_assignment_ref_mismatch")
                team["current_assignment_ref"] = None
                team["embedded_member_refs"] = []
                event_kind = "exact_team_unassigned"
            else:
                team["status"] = "dissolved"
                team["current_assignment_ref"] = None
                team["embedded_member_refs"] = []
                if team_ref in active:
                    active.remove(team_ref)
                doctrine_ref = team.get("doctrine_ref")
                if isinstance(doctrine_ref, str) and doctrine_ref:
                    try:
                        doctrine_path, _digest, doctrine_view = self._resolve_covered_owner_view(doctrine_ref, cache=_OwnerResolutionCache())
                        doctrine = copy.deepcopy(dict(doctrine_view))
                        doctrine["status"] = "inactive"
                        record_writes[doctrine_path] = doctrine
                    except CommandRejectedError as exc:
                        raise CommandRejectedError("team_doctrine_invalid") from exc
                host_id = "host.team." + team_ref
                scheduler.hosts.pop(host_id, None)
                scheduler.queue.replace(event for event in scheduler.queue.snapshot() if event.target_host != host_id and event.source_host != host_id)
                event_kind = "exact_team_dissolved"
            if action in ("reorganize", "assign", "unassign", "dissolve"):
                after_members = tuple(ref for ref in team.get("member_refs", []) if isinstance(ref, str)) if action != "dissolve" else ()
                after_parent = team.get("parent_institution_ref") if action != "dissolve" and isinstance(team.get("parent_institution_ref"), str) else None
                after_services = self._team_service_villages(after_members) if after_members else ()
                after_assignment = team.get("current_assignment_ref") if action != "dissolve" and isinstance(team.get("current_assignment_ref"), str) else None
                try:
                    stage_team_change(
                        self.repository, record_writes, team_ref=team_ref,
                        before_members=route_before_members, after_members=after_members,
                        before_parent=route_before_parent, after_parent=after_parent,
                        before_services=route_before_services, after_services=after_services,
                        before_assignment=route_before_assignment, after_assignment=after_assignment,
                    )
                except ValueError as exc:
                    raise CommandRejectedError("membership_routes_invalid") from exc

        event_id = self._append_semantic_event(
            world_events, command=command, kind=event_kind, at=current_time,
            host_refs=(team_ref,), actor_refs=(command.actor_id,), affected_owner_refs=(team_path,),
            material_consequence_refs=tuple(
                ref for ref in (team_ref, team.get("current_assignment_ref")) if isinstance(ref, str)
            ), classification=classification,
            audience_refs=tuple(team.get("member_refs", [])), reducer_ref="shinobi_runtime.commands.team_lifecycle_resolution",
        )
        scene = copy.deepcopy(self._scene_base(current_time))
        scene["scene_summary"] = str(command.payload["reason"] or f"{team.get('name', team_ref)} lifecycle changes: {action}.")
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            self.scene_path: _json_bytes(scene),
            **self._scheduler_write_images(scheduler),
            **self._world_event_writes(world_events),
        }
        writes.update({path: _json_bytes(value) for path, value in record_writes.items()})
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("team lifecycle write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged = overlay.read_json(team_path)
            if action == "dissolve" and staged.get("status") != "dissolved":
                raise ValueError("team dissolution did not persist")
            if action == "assign" and staged.get("current_assignment_ref") != command.payload.get("assignment_ref"):
                raise ValueError("team assignment did not persist")
            if action == "assign" and sorted(staged.get("embedded_member_refs", [])) != sorted(staged.get("member_refs", [])):
                raise ValueError("team assignment exact identity overlay did not persist")
            if action in ("unassign", "dissolve") and staged.get("current_assignment_ref") is not None:
                raise ValueError("team assignment was not cleared")
            if action in ("unassign", "dissolve") and staged.get("embedded_member_refs"):
                raise ValueError("team embedded identity overlay was not cleared")
            if action in ("form", "reorganize") and staged.get("training", {}).get("model_ref") != "training.team":
                raise ValueError("exact team diverged from generic team training model")

        return _BuiltPlan(
            code="team_lifecycle_ready", affected_refs=expected_paths, writes=writes,
            result={
                "command_type": command.command_type,
                "action": action,
                "team_ref": team_ref,
                "current_assignment_ref": team.get("current_assignment_ref"),
                "semantic_event_id": event_id,
            },
            validator=validate,
        )
    def _team_training_schedule_limits(self) -> Tuple[int, Decimal, int, int, Decimal]:
        model = self._training_model("training.team")
        schedule = model.get("schedule_limits")
        if not isinstance(schedule, Mapping):
            raise CommandRejectedError("training_model_registry_invalid")
        cycle_days = schedule.get("cycle_length_days")
        weekly_hours = schedule.get("maximum_hours_per_member_per_week")
        recovery_hours = schedule.get("minimum_recovery_hours")
        recent_limit = schedule.get("recent_session_limit", 64)
        familiarity_rate = schedule.get("doctrine_familiarity_points_per_active_hour", "1")
        if (
            isinstance(cycle_days, bool)
            or not isinstance(cycle_days, int)
            or cycle_days <= 0
            or isinstance(recovery_hours, bool)
            or not isinstance(recovery_hours, int)
            or recovery_hours < 0
            or isinstance(recent_limit, bool)
            or not isinstance(recent_limit, int)
            or recent_limit < 8
            or recent_limit > 256
        ):
            raise CommandRejectedError("training_model_registry_invalid")
        try:
            weekly = Decimal(str(weekly_hours))
            familiarity = Decimal(str(familiarity_rate))
        except Exception as exc:
            raise CommandRejectedError("training_model_registry_invalid") from exc
        if not weekly.is_finite() or weekly <= 0 or not familiarity.is_finite() or familiarity < 0:
            raise CommandRejectedError("training_model_registry_invalid")
        return cycle_days, weekly, recovery_hours, recent_limit, familiarity
    def _record_team_training_session(
        self,
        team: Dict[str, Any],
        *,
        session_ref: str,
        member_targets: Mapping[str, str],
        instructor_ref: str,
        started_at: CampaignTime,
        ended_at: CampaignTime,
        active_hours: Decimal,
    ) -> None:
        """Validate the rolling exact-team schedule and append one resolved session.

        The rolling ledger is intentionally bounded. It exists only to enforce
        current schedule limits and recovery, while semantic events retain the
        long-form history.
        """

        training = team.get("training")
        if not isinstance(training, dict) or training.get("model_ref") != "training.team":
            raise CommandRejectedError("team_training_invalid")
        recent = training.get("recent_sessions")
        if recent is None:
            recent = []
        if not isinstance(recent, list):
            raise CommandRejectedError("team_training_history_invalid")

        cycle_days, weekly_limit, recovery_hours, recent_limit, _familiarity = (
            self._team_training_schedule_limits()
        )
        started_dt = _campaign_datetime(started_at)
        ended_dt = _campaign_datetime(ended_at)
        if ended_dt <= started_dt:
            raise CommandRejectedError("training_target_time_invalid")
        horizon_seconds = max(cycle_days * 86400, recovery_hours * 3600)
        history_cutoff = ended_dt - timedelta(seconds=horizon_seconds)
        weekly_cutoff = ended_dt - timedelta(days=cycle_days)

        retained: list[Dict[str, Any]] = []
        parsed: list[Tuple[datetime, Mapping[str, Any]]] = []
        for raw in recent:
            if not isinstance(raw, Mapping):
                raise CommandRejectedError("team_training_history_invalid")
            raw_end = raw.get("ended_at")
            raw_members = raw.get("member_refs")
            raw_hours = raw.get("active_hours")
            if (
                not isinstance(raw_end, str)
                or not isinstance(raw_members, list)
                or not raw_members
                or len(raw_members) > 16
                or len(raw_members) != len(set(raw_members))
                or any(not isinstance(ref, str) or not ref for ref in raw_members)
            ):
                raise CommandRejectedError("team_training_history_invalid")
            try:
                raw_time = CampaignTime.parse(raw_end)
                raw_dt = _campaign_datetime(raw_time)
                raw_active = Decimal(str(raw_hours))
            except Exception as exc:
                raise CommandRejectedError("team_training_history_invalid") from exc
            if not raw_active.is_finite() or raw_active <= 0 or raw_dt > started_dt:
                raise CommandRejectedError("team_training_history_invalid")
            if raw_dt > history_cutoff:
                row = copy.deepcopy(dict(raw))
                retained.append(row)
                parsed.append((raw_dt, row))

        members = tuple(sorted(member_targets))
        if not members or len(members) > 16:
            raise CommandRejectedError("team_training_members_invalid")
        for member_ref in members:
            used = Decimal("0")
            last_end: Optional[datetime] = None
            for raw_dt, raw in parsed:
                raw_members = raw.get("member_refs", [])
                if member_ref not in raw_members:
                    continue
                if raw_dt > weekly_cutoff:
                    used += Decimal(str(raw.get("active_hours")))
                if last_end is None or raw_dt > last_end:
                    last_end = raw_dt
            if used + active_hours > weekly_limit:
                raise CommandRejectedError("team_training_weekly_limit_exceeded")
            if last_end is not None:
                recovery = Decimal(str((started_dt - last_end).total_seconds())) / Decimal(3600)
                if recovery < Decimal(recovery_hours):
                    raise CommandRejectedError("team_training_recovery_required")

        session = {
            "session_ref": session_ref,
            "started_at": str(started_at),
            "ended_at": str(ended_at),
            "active_hours": format(active_hours.normalize(), "f"),
            "member_refs": list(members),
            "instructor_ref": instructor_ref,
            "targets": {ref: member_targets[ref] for ref in members},
        }
        retained.append(session)
        retained.sort(key=lambda row: (str(row.get("ended_at")), str(row.get("session_ref"))))
        if len(retained) > recent_limit:
            raise CommandRejectedError("team_training_history_capacity_exceeded")
        training["recent_sessions"] = retained
    def _team_training_session_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("team_ref", "member_targets", "instructor_ref", "target_time", "active_hours"),
            command.command_type,
        )
        team_ref = _stable_id(command.payload["team_ref"], "team_ref_invalid", prefix="team.")
        team_path, team_view = self._exact_team(team_ref)
        team = copy.deepcopy(dict(team_view))
        if team.get("status") != "active":
            raise CommandRejectedError("team_inactive")

        leader = team.get("leader_ref")
        deputy = team.get("deputy_ref")
        authority_ref = team.get("assignment_authority_ref")
        authority = None
        if isinstance(authority_ref, str) and authority_ref:
            authority = self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(
                holder_ref=command.actor_id, owner_ref=authority_ref
            )
        if command.actor_id not in (leader, deputy) and not (authority and authority.allowed):
            raise CommandRejectedError("team_training_not_authorized")

        raw_targets = command.payload["member_targets"]
        if (
            not isinstance(raw_targets, Mapping)
            or not raw_targets
            or len(raw_targets) > 16
            or any(not isinstance(ref, str) or not isinstance(target, str) for ref, target in raw_targets.items())
        ):
            raise CommandRejectedError("team_training_members_invalid")
        member_refs = tuple(sorted(raw_targets))
        roster = team.get("member_refs")
        if not isinstance(roster, list) or any(ref not in roster for ref in member_refs):
            raise CommandRejectedError("team_training_members_invalid")
        member_targets = {ref: str(raw_targets[ref]) for ref in member_refs}

        instructor_ref = _stable_id(
            command.payload["instructor_ref"], "training_instructor_invalid"
        )
        training = team.get("training")
        if not isinstance(training, Mapping) or training.get("model_ref") != "training.team":
            raise CommandRejectedError("team_training_invalid")
        allowed_instructors = training.get("instructor_refs")
        if not isinstance(allowed_instructors, list) or instructor_ref not in allowed_instructors:
            raise CommandRejectedError("training_instructor_not_authorized")
        facilities = training.get("facility_refs", [])
        if not isinstance(facilities, list) or any(not isinstance(ref, str) for ref in facilities):
            raise CommandRejectedError("team_training_invalid")

        try:
            target_time = CampaignTime.parse(command.payload["target_time"])
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("training_target_time_invalid") from exc
        if target_time <= current_time:
            raise CommandRejectedError("training_target_time_invalid")
        try:
            active_hours = Decimal(str(command.payload["active_hours"]))
        except Exception as exc:
            raise CommandRejectedError("training_active_hours_invalid") from exc
        elapsed_hours = Decimal(
            int((_campaign_datetime(target_time) - _campaign_datetime(current_time)).total_seconds())
        ) / Decimal(3600)
        if not active_hours.is_finite() or active_hours <= 0 or active_hours > elapsed_hours:
            raise CommandRejectedError("training_active_hours_invalid")

        model = self._training_model("training.team")
        factors = model.get("base_factors")
        if not isinstance(factors, Mapping) or model.get("requires_instructor") is not True:
            raise CommandRejectedError("training_model_registry_invalid")

        member_records: Dict[str, Dict[str, Any]] = {}
        member_paths: Dict[str, str] = {}
        locations: set[str] = set()
        for member_ref in member_refs:
            path, record = self._resolve_actor_for_write(member_ref)
            if record.get("life_status") not in ("active", "alive"):
                raise CommandRejectedError("training_actor_not_active")
            location = record.get("current_location_id")
            if not isinstance(location, str) or not location:
                raise CommandRejectedError("training_context_invalid")
            locations.add(location)
            member_paths[member_ref] = path
            member_records[member_ref] = record
            self._training_target(record, member_targets[member_ref])
        if len(locations) != 1:
            raise CommandRejectedError("team_training_not_colocated")
        location_ref = next(iter(locations))

        if instructor_ref in member_records:
            instructor_record = member_records[instructor_ref]
        else:
            _instructor_path, instructor_record = self._resolve_actor_for_write(instructor_ref)
        if instructor_record.get("life_status") not in ("active", "alive"):
            raise CommandRejectedError("training_instructor_unavailable")
        if instructor_record.get("current_location_id") != location_ref:
            raise CommandRejectedError("training_instructor_unavailable")
        if facilities and location_ref not in facilities:
            raise CommandRejectedError("training_facility_unavailable")
        facility_slots, facility_quality_factor = self._training_facility_capacity(
            location_ref,
            required_slots=len(member_refs),
            base_quality_factor=factors["facility_quality"],
            required_categories=tuple(sorted({"team_drill", *(self._training_category_for_target(target) for target in member_targets.values())})),
            module_required=bool(facilities),
        )

        session_ref = "training.session." + command.digest[:32]
        self._record_team_training_session(
            team,
            session_ref=session_ref,
            member_targets=member_targets,
            instructor_ref=instructor_ref,
            started_at=current_time,
            ended_at=target_time,
            active_hours=active_hours,
        )

        base = self._time_spanning_base(command, meta, current_time, target_time=target_time)
        if base.result.get("interrupted"):
            raise CommandRejectedError("time_boundary_requires_domain_settlement")
        reached = CampaignTime.parse(base.result["world_time"])
        if reached != target_time:
            raise CommandRejectedError("training_time_settlement_incomplete")

        try:
            banks = copy.deepcopy(self.repository.read_json(_DEVELOPMENT_BANK_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("development_bank_invalid") from exc
        entries = banks.get("entries") if isinstance(banks, dict) else None
        if not isinstance(entries, dict):
            raise CommandRejectedError("development_bank_invalid")

        outcomes: Dict[str, Any] = {}
        consequences: list[str] = []
        for member_ref in member_refs:
            record = member_records[member_ref]
            target = member_targets[member_ref]
            container, leaf, current_value = self._training_target(record, target)
            aptitude = self._training_aptitude(record, target)
            health_factor, recovery_factor = self._health_recovery_factor(record)
            instructor_aptitude = self._training_aptitude(instructor_record, target)
            instructor_quality = max(
                Decimal("0.85"),
                min(Decimal("1.20"), Decimal("0.90") + Decimal(instructor_aptitude) / Decimal(500)),
            )
            entry = entries.get(member_ref)
            if entry is None:
                entry = {"owner_type": "character", "resolved_through": str(current_time), "credits": {}}
                entries[member_ref] = entry
            if not isinstance(entry, dict) or not isinstance(entry.get("credits"), dict):
                raise CommandRejectedError("development_bank_invalid")
            residual = entry["credits"].get(target, 0)
            try:
                outcome = settle_training(
                    TrainingInputs(
                        scheduled_hours=str(active_hours),
                        attendance="1",
                        available_instructor_hours=str(active_hours),
                        required_instructor_hours=str(active_hours),
                        facility_slots=facility_slots,
                        required_slots=str(len(member_refs)),
                        equipment_sets=str(len(member_refs)),
                        required_sets=str(len(member_refs)),
                        instructor_quality_factor=str(instructor_quality),
                        facility_quality_factor=facility_quality_factor,
                        equipment_factor=factors["equipment"],
                        health_factor=health_factor,
                        recovery_factor=recovery_factor,
                        relevance_factor=factors["relevance"],
                        difficulty_fit_factor=factors["difficulty_fit"],
                        aptitude=aptitude,
                        experience_modifier="1",
                        current_value=current_value,
                        residual_units=residual,
                        representation="exact",
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise CommandRejectedError("training_resolution_invalid") from exc
            container[leaf] = outcome.ending_value
            entry["credits"][target] = float(outcome.residual_units)
            entry["resolved_through"] = str(target_time)
            outcomes[member_ref] = {
                "target": target,
                "starting_value": current_value,
                "ending_value": outcome.ending_value,
                "points_gained": outcome.points_gained,
                "residual_units": str(outcome.residual_units),
            }
            consequences.append(
                f"training:{member_ref}:{target}:{current_value}->{outcome.ending_value}"
            )

        doctrine_path: Optional[str] = None
        doctrine: Optional[Dict[str, Any]] = None
        doctrine_ref = team.get("doctrine_ref")
        _cycle, _weekly, _recovery, _recent, familiarity_rate = self._team_training_schedule_limits()
        familiarity_gain = int(active_hours * familiarity_rate)
        if isinstance(doctrine_ref, str) and doctrine_ref and familiarity_gain > 0:
            try:
                resolved_path, _digest, doctrine_view = self._resolve_covered_owner_view(
                    doctrine_ref, cache=_OwnerResolutionCache()
                )
            except CommandRejectedError as exc:
                raise CommandRejectedError("team_doctrine_invalid") from exc
            if not isinstance(doctrine_view, Mapping) or doctrine_view.get("schema") != "team-doctrine":
                raise CommandRejectedError("team_doctrine_invalid")
            doctrine_path = resolved_path
            doctrine = copy.deepcopy(dict(doctrine_view))
            familiarity = doctrine.get("familiarity")
            if not isinstance(familiarity, dict):
                raise CommandRejectedError("team_doctrine_invalid")
            for member_ref in member_refs:
                current = familiarity.get(member_ref, 0)
                if isinstance(current, bool) or not isinstance(current, int):
                    raise CommandRejectedError("team_doctrine_invalid")
                familiarity[member_ref] = min(100, max(0, current) + familiarity_gain)
            consequences.append(f"doctrine_familiarity:{doctrine_ref}:shared_training")

        world_events = self._world_events_after(base)
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="team_training_session_resolved",
            at=target_time,
            host_refs=(team_ref,),
            actor_refs=tuple(sorted(set(member_refs + (instructor_ref,)))),
            place_refs=(location_ref,),
            affected_owner_refs=tuple(sorted(set(member_paths.values()) | {team_path, _DEVELOPMENT_BANK_PATH} | ({doctrine_path} if doctrine_path else set()))),
            material_consequence_refs=tuple(consequences),
            classification=str(team.get("classification") or "public"),
            audience_refs=tuple(team.get("member_refs", [])),
            reducer_ref="shinobi_runtime.reducers.training.settle_training",
        )
        scene_after = json.loads(base.writes[self.scene_path].decode("utf-8"))
        scene_after["scene_summary"] = (
            f"{team.get('name', team_ref)} completes a shared training session through {target_time}."
        )
        scene_after["decision_required"] = "Choose the next consequential action."

        writes = dict(base.writes)
        writes[self.meta_path] = _json_bytes(self._meta_after(meta, command, world_time=target_time))
        writes[self.scene_path] = _json_bytes(scene_after)
        writes[team_path] = _json_bytes(team)
        writes[_DEVELOPMENT_BANK_PATH] = _json_bytes(banks)
        for member_ref, path in member_paths.items():
            writes[path] = _json_bytes(member_records[member_ref])
        if doctrine_path and doctrine is not None:
            writes[doctrine_path] = _json_bytes(doctrine)
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("team training write set changed after planning")
            self._assert_meta(
                overlay, manifest, meta_path=self.meta_path, command=command, world_time=target_time
            )
            staged_team = overlay.read_json(team_path)
            staged_training = staged_team.get("training")
            sessions = staged_training.get("recent_sessions") if isinstance(staged_training, Mapping) else None
            if not isinstance(sessions, list) or not any(
                isinstance(row, Mapping) and row.get("session_ref") == session_ref for row in sessions
            ):
                raise ValueError("team training session ledger did not persist")
            for member_ref, outcome in outcomes.items():
                staged_actor = overlay.read_json(member_paths[member_ref])
                _container, _leaf, staged_value = self._training_target(
                    staged_actor, outcome["target"]
                )
                if staged_value != outcome["ending_value"]:
                    raise ValueError("team training target after-image differs from reducer")
                staged_banks = overlay.read_json(_DEVELOPMENT_BANK_PATH)
                if staged_banks["entries"][member_ref]["resolved_through"] != str(target_time):
                    raise ValueError("team training development cursor did not advance")
            self._scheduler_from_reader(overlay)

        return _BuiltPlan(
            code="team_training_session_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "team_ref": team_ref,
                "session_ref": session_ref,
                "instructor_ref": instructor_ref,
                "active_hours": str(active_hours),
                "world_time": str(target_time),
                "member_outcomes": outcomes,
                "semantic_event_id": event_id,
            },
            validator=validate,
        )
    @staticmethod
    def _training_category_for_target(target: str) -> str:
        if target.startswith("repertoire.method_mastery.") or target.startswith("stats.repertoire.method_mastery."):
            return "technique"
        if target.startswith("martial_skills.") or target.startswith("attributes.") or target.startswith("stats.martial_skills."):
            return "martial"
        if target.startswith("chakra_dimensions.") or target.startswith("stats.chakra_dimensions."):
            return "chakra"
        lowered = target.lower()
        if "stealth" in lowered or "infiltration" in lowered or "suppression" in lowered:
            return "stealth"
        if "survival" in lowered or "tracking" in lowered:
            return "tracking"
        if "medical" in lowered:
            return "medical"
        return "combat"
    def _training_facility_capacity(
        self,
        location_ref: str,
        *,
        required_slots: int,
        base_quality_factor: object,
        required_categories: Sequence[str] = (),
        module_required: bool = False,
    ) -> Tuple[str, str]:
        """Resolve sparse facility mechanics from the canonical place registry.

        A place without a training module remains usable only when the calling
        training model does not explicitly require a registered facility. Such
        ambient training uses the model baseline and does not invent a bonus.
        """

        if isinstance(required_slots, bool) or not isinstance(required_slots, int) or required_slots <= 0:
            raise CommandRejectedError("training_facility_capacity_invalid")
        try:
            graph = LocationGraph(self.repository.read_json(_ROUTES_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("training_facility_registry_invalid") from exc
        place = graph.place(location_ref)
        modules = place.get("mechanical_modules") if isinstance(place, Mapping) else None
        training = modules.get("training") if isinstance(modules, Mapping) else None
        if not isinstance(training, Mapping):
            if module_required:
                raise CommandRejectedError("training_facility_unavailable")
            return str(required_slots), str(base_quality_factor)
        capacity = training.get("capacity_slots")
        quality = training.get("quality_milli")
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or capacity <= 0
            or isinstance(quality, bool)
            or not isinstance(quality, int)
            or quality < 100
            or quality > 1500
        ):
            raise CommandRejectedError("training_facility_registry_invalid")
        supported = training.get("supported_categories", [])
        if not isinstance(supported, list) or any(not isinstance(value, str) for value in supported):
            raise CommandRejectedError("training_facility_registry_invalid")
        supported_set = set(supported)
        aliases = {
            "martial": {"martial", "combat"},
            "chakra": {"chakra", "combat"},
            "technique": {"technique", "combat"},
            "stealth": {"stealth", "covert", "anbu"},
            "tracking": {"tracking", "survival", "recon"},
            "medical": {"medical"},
            "combat": {"combat", "martial"},
            "team_drill": {"team_drill"},
        }
        for category in required_categories:
            allowed = aliases.get(category, {category})
            if not (supported_set & allowed):
                raise CommandRejectedError("training_facility_category_unsupported")
        quality_factor = Decimal(quality) / Decimal(1000)
        return str(capacity), str(quality_factor)
    def _team_type_rule(self, team_type: str) -> Mapping[str, Any]:
        try:
            registry = self.repository.read_json(_TEAM_TYPES_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("team_type_registry_invalid") from exc
        types = registry.get("types") if isinstance(registry, Mapping) else None
        rule = types.get(team_type) if isinstance(types, Mapping) else None
        if not isinstance(rule, Mapping):
            raise CommandRejectedError("team_type_invalid")
        return rule
    def _validate_team_type_roster(self, team_type: str, member_refs: Sequence[str]) -> None:
        rule = self._team_type_rule(team_type)
        member_range = rule.get("named_member_range")
        if member_range is not None:
            if (
                not isinstance(member_range, Sequence)
                or isinstance(member_range, (str, bytes, bytearray))
                or len(member_range) != 2
                or any(isinstance(value, bool) or not isinstance(value, int) for value in member_range)
            ):
                raise CommandRejectedError("team_type_registry_invalid")
            minimum, maximum = int(member_range[0]), int(member_range[1])
            if len(member_refs) < minimum or len(member_refs) > maximum:
                raise CommandRejectedError("team_type_roster_invalid")
    def _exact_team(self, team_ref: str) -> Tuple[str, Mapping[str, Any]]:
        try:
            path, _digest, view = self._resolve_covered_owner_view(
                team_ref, cache=_OwnerResolutionCache()
            )
        except CommandRejectedError as exc:
            raise CommandRejectedError("team_unresolved") from exc
        if not isinstance(view, Mapping) or view.get("schema") != "exact-team" or view.get("id") != team_ref:
            raise CommandRejectedError("team_unresolved")
        members = view.get("member_refs")
        embedded = view.get("embedded_member_refs", [])
        if (
            not isinstance(members, list)
            or len(members) != len(set(members))
            or not isinstance(embedded, list)
            or len(embedded) != len(set(embedded))
            or any(ref not in members for ref in embedded)
        ):
            raise CommandRejectedError("team_invalid")
        assignment_ref = view.get("current_assignment_ref")
        if assignment_ref is None and embedded:
            raise CommandRejectedError("team_invalid")
        return path, view
    def _exact_team_assignment_for_person(
        self, person_ref: str
    ) -> Tuple[Optional[str], Tuple[str, ...]]:
        """Return one formation identity overlay for an exact person.

        A person may belong to multiple social teams, but their exact body can
        be embedded in only one operational formation at a time.  Team state
        owns that identity overlay; the formation never stores a duplicate
        exact roster.
        """

        assigned_refs: set[str] = set()
        team_refs: list[str] = []
        try:
            routed_team_refs = team_refs_for_member(self.repository, person_ref)
        except ValueError as exc:
            raise CommandRejectedError("membership_routes_invalid") from exc
        for team_ref in routed_team_refs:
            try:
                _path, team = self._exact_team(team_ref)
            except CommandRejectedError:
                continue
            if team.get("status") != "active":
                continue
            embedded = team.get("embedded_member_refs", [])
            if not isinstance(embedded, list) or person_ref not in embedded:
                continue
            assignment_ref = team.get("current_assignment_ref")
            if not isinstance(assignment_ref, str) or not assignment_ref:
                raise CommandRejectedError("team_embedded_assignment_invalid")
            assigned_refs.add(assignment_ref)
            team_refs.append(team_ref)
        if len(assigned_refs) > 1:
            raise CommandRejectedError("person_multiple_formation_assignments")
        return (next(iter(assigned_refs)) if assigned_refs else None, tuple(sorted(team_refs)))
    def _detach_embedded_person_from_formation(
        self,
        *,
        person_ref: str,
        expected_force_ref: str,
        team_writes: Dict[str, Dict[str, Any]],
        formation_writes: Dict[str, Dict[str, Any]],
    ) -> Optional[Mapping[str, Any]]:
        """Remove one exact identity overlay and one physical formation body.

        Team assignment owns the exact identity overlay.  Formation state owns
        aggregate headcount.  Detaching an embedded exact casualty therefore
        updates both authorities exactly once without copying a roster into the
        formation.
        """

        formation_ref, team_refs = self._exact_team_assignment_for_person(person_ref)
        if formation_ref is None:
            return None
        formation_path, force_ref, formation_view = self._formation_by_id(formation_ref)
        if force_ref != expected_force_ref:
            raise CommandRejectedError("team_assignment_force_mismatch")
        registry = formation_writes.get(formation_path)
        if registry is None:
            try:
                loaded = self.repository.read_json(formation_path)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("formation_registry_invalid") from exc
            if not isinstance(loaded, dict):
                raise CommandRejectedError("formation_registry_invalid")
            registry = copy.deepcopy(loaded)
            formation_writes[formation_path] = registry
        formations = registry.get("formations")
        if not isinstance(formations, list):
            raise CommandRejectedError("formation_registry_invalid")
        formation = next(
            (row for row in formations if isinstance(row, dict) and row.get("id") == formation_ref),
            None,
        )
        if not isinstance(formation, dict) or formation.get("force_ref") != force_ref:
            raise CommandRejectedError("formation_unresolved")
        total = formation.get("personnel_total")
        if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
            raise CommandRejectedError("formation_strength_invalid")
        capability_components_before = copy.deepcopy(formation.get("components", []))
        self._resize_formation_strength(formation, total - 1)

        team_paths: list[str] = []
        for team_ref in team_refs:
            team_path, team_view = self._exact_team(team_ref)
            team = team_writes.get(team_path)
            if team is None:
                team = copy.deepcopy(dict(team_view))
                team_writes[team_path] = team
            embedded = team.get("embedded_member_refs")
            if not isinstance(embedded, list) or person_ref not in embedded:
                raise CommandRejectedError("team_embedded_assignment_invalid")
            team["embedded_member_refs"] = [ref for ref in embedded if ref != person_ref]
            team_paths.append(team_path)
        return {
            "formation_ref": formation_ref,
            "formation_path": formation_path,
            "formation_location_ref": formation_view.get("location_ref"),
            "team_refs": team_refs,
            "team_paths": tuple(team_paths),
            "capability_components_before": capability_components_before,
        }
    def _team_command_authorizes(self, commander_ref: str, subject_ref: str, team_ref: str) -> bool:
        _path, team = self._exact_team(team_ref)
        return (
            team.get("status") == "active"
            and team.get("leader_ref") == commander_ref
            and subject_ref in team.get("member_refs", [])
        )

