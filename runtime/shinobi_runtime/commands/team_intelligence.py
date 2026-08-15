"""Bounded NPC team assembly over the generic exact-team transaction system."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.membership_routes import team_refs_for_member
from shinobi_runtime.autonomy import AutonomousDecision, AutonomousPolicyBook
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import POPULATION_REGISTRY_PATH as _POPULATION_REGISTRY_PATH, TEAM_TYPES_PATH as _TEAM_TYPES_PATH
from shinobi_runtime.commands.team_composition import (
    CAPABILITY_DIMENSIONS, TeamMemberProfile, build_compact_doctrine,
    capability_profile_from_record, derive_member_roles, doctrine_seed,
    player_controlled_record, select_complementary_roster,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry


class TeamIntelligenceMixin:
    """Member-aware doctrine plus deterministic, local NPC roster assembly."""

    def _autonomy_policy_book(self) -> AutonomousPolicyBook:
        book = super()._autonomy_policy_book()
        assignments: Dict[str, Mapping[str, Any]] = {}
        for faction_id, raw in book.faction_assignments.items():
            row = dict(raw)
            if not isinstance(row.get("team_creation"), Mapping) and isinstance(row.get("force_ref"), str):
                row["team_creation"] = {"mode": "dynamic", "team_type": "temporary_task_force", "target_size": 4}
            assignments[faction_id] = row
        return AutonomousPolicyBook(
            profiles=book.profiles, faction_assignments=assignments,
            team_profiles=book.team_profiles, institution_assignments=book.institution_assignments,
        )

    def _member_profile(self, person_ref: str) -> Optional[TeamMemberProfile]:
        try:
            _path, _digest, view = self._resolve_covered_owner_view(person_ref, cache=_OwnerResolutionCache())
        except CommandRejectedError:
            return None
        if not isinstance(view, Mapping) or view.get("schema") not in ("shinobi_character", "person"):
            return None
        profile = capability_profile_from_record(person_ref, view)
        if player_controlled_record(view):
            return TeamMemberProfile(person_ref, False, "player_agency_protected", profile.scores)
        return profile


    def _candidate_refs(self, payload: Mapping[str, Any], faction_record: Mapping[str, Any], spec: Mapping[str, Any]) -> Tuple[str, ...]:
        """Return the exact organizational eligibility pool for an autonomous team.

        Specialist institutions may not silently draft from an entire village force
        merely because those people share a force owner.  Eligibility comes from the
        team's declared candidate pool, the faction's declared operational members,
        and its saved leadership/key-member roster.  Expansion requires an explicit
        membership/intake mechanic, not a hidden whole-force fallback.
        """
        preferred = [x for x in spec.get("candidate_refs", []) if isinstance(x, str)] if isinstance(spec.get("candidate_refs"), list) else []
        explicit = [x for x in payload.get("mission_participant_refs", []) if isinstance(x, str)] if isinstance(payload.get("mission_participant_refs"), list) else []
        faction = faction_record.get("faction") if isinstance(faction_record.get("faction"), Mapping) else {}
        leadership = [x for x in faction.get("leadership_ids", []) if isinstance(x, str)] if isinstance(faction.get("leadership_ids"), list) else []
        key_members = [x for x in faction.get("key_member_ids", []) if isinstance(x, str)] if isinstance(faction.get("key_member_ids"), list) else []
        ordered: list[str] = []
        for ref in preferred + explicit + leadership + key_members:
            if ref not in ordered:
                ordered.append(ref)
        return tuple(ordered)

    def _roster_target(self, team_type: str, requested: int, available: int) -> int:
        minimum, maximum = 2, 16
        try:
            registry = self.repository.read_json(_TEAM_TYPES_PATH)
        except (FileNotFoundError, ValueError):
            registry = {}
        types = registry.get("types") if isinstance(registry, Mapping) else {}
        row = types.get(team_type) if isinstance(types, Mapping) else {}
        bounds = row.get("named_member_range") if isinstance(row, Mapping) else None
        if isinstance(bounds, list) and len(bounds) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in bounds):
            minimum, maximum = max(2, bounds[0]), min(16, bounds[1])
        if available < minimum:
            return 0
        return max(minimum, min(maximum, requested, available))

    def _team_view(self, team_ref: str, record_writes: Mapping[str, Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
        for record in record_writes.values():
            if isinstance(record, Mapping) and record.get("schema") == "exact-team" and record.get("id") == team_ref:
                return record
        try:
            _path, team = self._exact_team(team_ref)
        except CommandRejectedError:
            return None
        return team

    def _active_autonomous_team(self, faction_record: Mapping[str, Any], record_writes: Mapping[str, Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
        faction = faction_record.get("faction") if isinstance(faction_record.get("faction"), Mapping) else {}
        plan = faction.get("plan_state") if isinstance(faction.get("plan_state"), Mapping) else {}
        refs = plan.get("autonomous_team_refs") if isinstance(plan, Mapping) else []
        if isinstance(refs, list):
            for ref in refs:
                if isinstance(ref, str):
                    team = self._team_view(ref, record_writes)
                    if isinstance(team, Mapping) and team.get("status") == "active":
                        return team
        return None

    def _generic_team_doctrine(self, team: Mapping[str, Any], *, at: CampaignTime, doctrine_identity: str, motto: str, training_focus: Sequence[str]) -> Dict[str, Any]:
        profiles: Dict[str, TeamMemberProfile] = {}
        for ref in [x for x in team.get("member_refs", []) if isinstance(x, str)]:
            profiles[ref] = self._member_profile(ref) or TeamMemberProfile(ref, True, "profile_unavailable", {d: 0 for d in CAPABILITY_DIMENSIONS})
        return build_compact_doctrine(team, profiles, at=at, doctrine_identity=doctrine_identity, motto=motto, training_focus=training_focus)

    def _register_exact_team_state(self, *, team_id: str, name: str, team_type: str, parent_institution_ref: Optional[str], assignment_authority_ref: str, leader_ref: str, member_refs: Sequence[str], roles: Mapping[str, str], classification: str, at: CampaignTime, basis: str, scheduler: CausalSchedulerRegistry, record_writes: Dict[str, Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
        path, team = super()._register_exact_team_state(
            team_id=team_id, name=name, team_type=team_type, parent_institution_ref=parent_institution_ref,
            assignment_authority_ref=assignment_authority_ref, leader_ref=leader_ref, member_refs=member_refs,
            roles=roles, classification=classification, at=at, basis=basis, scheduler=scheduler, record_writes=record_writes,
        )
        profiles = [p for ref in team.get("member_refs", []) if isinstance(ref, str) for p in [self._member_profile(ref)] if p is not None]
        identity, motto = doctrine_seed(profiles)
        try:
            policy = self._autonomy_policy_book().team_profile(team_type)
        except (TypeError, ValueError):
            policy = {}
        focus = policy.get("training_focus", []) if isinstance(policy, Mapping) else []
        doctrine = self._generic_team_doctrine(team, at=at, doctrine_identity=identity, motto=motto, training_focus=focus if isinstance(focus, list) else ())
        doctrine["approved_by"] = assignment_authority_ref
        doctrine_ref, doctrine_path = str(doctrine["id"]), self._team_doctrine_path(team_id)
        team["doctrine_ref"] = doctrine_ref
        record_writes[path], record_writes[doctrine_path] = team, doctrine
        team_index = record_writes.get("state/index/owners/team.json")
        if not isinstance(team_index, dict):
            team_index = copy.deepcopy(self.repository.read_json("state/index/owners/team.json")); record_writes["state/index/owners/team.json"] = team_index
        owners = team_index.get("owners")
        if not isinstance(owners, dict):
            raise CommandRejectedError("team_owner_index_invalid")
        owner_index = record_writes.get("state/index/owners.json")
        if not isinstance(owner_index, dict):
            owner_index = copy.deepcopy(self.repository.read_json("state/index/owners.json")); record_writes["state/index/owners.json"] = owner_index
        if doctrine_ref not in owners and isinstance(owner_index.get("owner_count"), int) and not isinstance(owner_index.get("owner_count"), bool):
            owner_index["owner_count"] += 1
        owners[doctrine_ref] = doctrine_path
        return path, team

    def _apply_autonomous_decision(self, *, decision: Any, at: CampaignTime, command: CommandEnvelope, scheduler: CausalSchedulerRegistry, world_events: Dict[str, Any], record_writes: Dict[str, Dict[str, Any]], faction_record: Dict[str, Any]) -> Mapping[str, Any]:
        if decision.kind == "team_form":
            payload, spec = decision.payload, decision.payload.get("team_creation")
            if not isinstance(spec, Mapping):
                return {"kind": "team_form", "skipped": "no_team_creation_policy"}
            active = self._active_autonomous_team(faction_record, record_writes)
            if active is not None:
                return {"kind": "team_form", "skipped": "active_autonomous_team_exists", "team_id": active.get("id")}
            allow_existing = spec.get("allow_existing_team_members") is True
            profiles: list[TeamMemberProfile] = []
            for ref in self._candidate_refs(payload, faction_record, spec):
                profile = self._member_profile(ref)
                if profile is None:
                    continue
                if ref == decision.actor_ref and spec.get("include_commander") is not True:
                    profile = TeamMemberProfile(ref, False, "commander_not_self_tasked", profile.scores)
                elif not allow_existing:
                    try:
                        routed = team_refs_for_member(self.repository, ref)
                    except ValueError as exc:
                        raise CommandRejectedError("membership_routes_invalid") from exc
                    staged_commitment = any(
                        isinstance(row, Mapping) and row.get("schema") == "exact-team" and row.get("status") == "active"
                        and ref in row.get("member_refs", [])
                        for row in record_writes.values()
                    )
                    if routed or staged_commitment:
                        profile = TeamMemberProfile(ref, False, "active_exact_team_commitment", profile.scores)
                profiles.append(profile)
            preferred = [x for x in spec.get("candidate_refs", []) if isinstance(x, str)] if isinstance(spec.get("candidate_refs"), list) else []
            requested = spec.get("target_size") if isinstance(spec.get("target_size"), int) and not isinstance(spec.get("target_size"), bool) else (len(preferred) if len(preferred) >= 2 else 4)
            team_type = str(spec.get("team_type") or "temporary_task_force")
            target = self._roster_target(team_type, requested, sum(1 for p in profiles if p.available))
            selected = select_complementary_roster(profiles, target_size=target, preferred_refs=preferred, preferred_leader_ref=spec.get("leader_ref") if isinstance(spec.get("leader_ref"), str) else None) if target >= 2 else ()
            if len(selected) < 2:
                return {"kind": "team_form", "skipped": "insufficient_available_personnel"}
            roster, leader = [p.person_ref for p in selected], selected[0].person_ref
            roles = derive_member_roles(selected, leader_ref=leader)
            faction_id = str(payload.get("faction_id") or "faction.unknown")
            explicit_id = spec.get("team_id")
            if isinstance(explicit_id, str) and explicit_id.startswith("team."):
                team_id = explicit_id
            else:
                digest = hashlib.sha256(f"{faction_id}\x00{at}\x00{'|'.join(roster)}".encode()).hexdigest()[:12]
                slug = re.sub(r"[^a-z0-9]+", ".", faction_id.lower()).strip(".")
                team_id = f"team.{slug.removeprefix('faction.')}.auto.{digest}"
            authority = spec.get("assignment_authority_ref") if isinstance(spec.get("assignment_authority_ref"), str) else decision.actor_ref
            classification = str(spec.get("classification") or payload.get("classification") or "restricted")
            parent = spec.get("parent_institution_ref") if isinstance(spec.get("parent_institution_ref"), str) else faction_id
            name = str(spec.get("name") or f"{faction_id.rsplit('.', 1)[-1].replace('_', ' ').title()} Field Cell")
            try:
                path, team = self._register_exact_team_state(
                    team_id=team_id, name=name, team_type=team_type, parent_institution_ref=parent,
                    assignment_authority_ref=authority, leader_ref=leader, member_refs=roster, roles=roles,
                    classification=classification, at=at, basis=f"Autonomous roster selection by {faction_id} from its bounded lawful personnel pool.",
                    scheduler=scheduler, record_writes=record_writes,
                )
            except CommandRejectedError as exc:
                if exc.code == "team_already_exists":
                    return {"kind": "team_form", "skipped": "team_already_exists", "team_id": team_id}
                raise
            faction = faction_record.get("faction"); plan = faction.get("plan_state") if isinstance(faction, dict) else None
            if not isinstance(plan, dict):
                raise CommandRejectedError("faction_owner_invalid")
            refs = plan.setdefault("autonomous_team_refs", [])
            if not isinstance(refs, list):
                raise CommandRejectedError("faction_owner_invalid")
            retained: list[str] = []
            for ref in refs:
                if not isinstance(ref, str):
                    continue
                existing = self._team_view(ref, record_writes)
                if isinstance(existing, Mapping) and existing.get("status") == "active":
                    retained.append(ref)
            refs[:] = retained
            if team_id not in refs:
                refs.append(team_id); refs.sort()
            doctrine_ref, doctrine_path = team.get("doctrine_ref"), self._team_doctrine_path(team_id)
            event_id = self._append_internal_event(
                world_events, command=command, identity=f"{team_id}:{at}:formed", kind="exact_team_formed", at=at,
                host_refs=(faction_id, team_id), actor_refs=tuple(roster), affected_owner_refs=(path, doctrine_path),
                material_consequence_refs=tuple(x for x in (team_id, doctrine_ref) if isinstance(x, str)), classification=classification,
                audience_refs=(), source_refs=(decision.actor_ref,),
            )
            return {"kind": "team_form", "event_id": event_id, "team_id": team_id, "leader_ref": leader, "member_refs": roster, "doctrine_ref": doctrine_ref}

        if decision.kind == "mission_generate":
            team = self._active_autonomous_team(faction_record, record_writes)
            members = [x for x in team.get("member_refs", []) if isinstance(x, str)] if isinstance(team, Mapping) else []
            if members:
                payload = dict(decision.payload); payload["mission_participant_refs"] = members
                decision = AutonomousDecision(kind=decision.kind, actor_ref=decision.actor_ref, reason=decision.reason, payload=payload, material=decision.material)
        return super()._apply_autonomous_decision(
            decision=decision, at=at, command=command, scheduler=scheduler, world_events=world_events,
            record_writes=record_writes, faction_record=faction_record,
        )

    def _compact_plan_doctrine(self, plan: _BuiltPlan, *, at: CampaignTime, identity: Optional[str] = None, motto: Optional[str] = None, focus: Optional[Sequence[str]] = None) -> _BuiltPlan:
        writes = dict(plan.writes); team = doctrine = None; doctrine_path = None
        for path, raw in writes.items():
            try:
                record = json.loads(raw.decode("utf-8"))
            except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if record.get("schema") == "exact-team": team = record
            elif record.get("schema") == "team-doctrine": doctrine, doctrine_path = record, path
        if not isinstance(team, Mapping) or not isinstance(doctrine, Mapping) or doctrine_path is None:
            return plan
        training = doctrine.get("training") if isinstance(doctrine.get("training"), Mapping) else {}
        compact = self._generic_team_doctrine(
            team, at=at,
            doctrine_identity=identity.strip() if isinstance(identity, str) and identity.strip() else str(doctrine.get("identity") or "adaptive combined-arms doctrine"),
            motto=motto.strip() if isinstance(motto, str) and motto.strip() else str(doctrine.get("motto") or "See. Shape. Secure. Return."),
            training_focus=focus if focus is not None else (training.get("shared_drills", []) if isinstance(training.get("shared_drills"), list) else ()),
        )
        old_familiarity = doctrine.get("familiarity") if isinstance(doctrine.get("familiarity"), Mapping) else {}
        compact["familiarity"] = {ref: max(0, min(100, int(old_familiarity.get(ref, 0)))) for ref in team.get("member_refs", []) if isinstance(ref, str)}
        compact["approved_by"], compact["status"] = str(doctrine.get("approved_by") or compact["approved_by"]), str(doctrine.get("status") or "active")
        writes[doctrine_path] = _json_bytes(compact)
        return _BuiltPlan(code=plan.code, affected_refs=plan.affected_refs, writes=writes, result=plan.result, validator=plan.validator)

    def _team_development_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        plan = super()._team_development_resolution(command, meta, current_time)
        return self._compact_plan_doctrine(plan, at=current_time, identity=command.payload.get("doctrine_identity"), motto=command.payload.get("motto"), focus=command.payload.get("training_focus"))

    def _team_lifecycle_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        plan = super()._team_lifecycle_resolution(command, meta, current_time)
        return self._compact_plan_doctrine(plan, at=current_time) if command.payload.get("action") in ("form", "reorganize") else plan

    def _apply_team_autonomy_review(self, *, owner_ref: str, at: CampaignTime, compacted: int, command: CommandEnvelope, scheduler: CausalSchedulerRegistry, policy_book: AutonomousPolicyBook, world_events: Dict[str, Any], record_writes: Dict[str, Dict[str, Any]]) -> Mapping[str, Any]:
        result = super()._apply_team_autonomy_review(
            owner_ref=owner_ref, at=at, compacted=compacted, command=command, scheduler=scheduler,
            policy_book=policy_book, world_events=world_events, record_writes=record_writes,
        )
        if result.get("skipped"):
            return result
        team = record_writes.get(owner_ref)
        if not isinstance(team, Mapping) or not isinstance(team.get("doctrine_ref"), str):
            return result
        doctrine_ref = team["doctrine_ref"]; doctrine_path = None; doctrine = None
        for path, record in record_writes.items():
            if isinstance(record, Mapping) and record.get("schema") == "team-doctrine" and record.get("id") == doctrine_ref:
                doctrine_path, doctrine = path, record; break
        if doctrine is None:
            try:
                doctrine_path, _digest, view = self._resolve_covered_owner_view(doctrine_ref, cache=_OwnerResolutionCache())
                doctrine = view if isinstance(view, Mapping) else None
            except CommandRejectedError:
                return result
        if doctrine is None or doctrine_path is None:
            return result
        try:
            persisted_team_index = self.repository.read_json("state/index/owners/team.json")
        except (FileNotFoundError, ValueError):
            persisted_team_index = {}
        persisted_owners = persisted_team_index.get("owners") if isinstance(persisted_team_index, Mapping) else {}
        if isinstance(persisted_owners, Mapping) and doctrine_ref not in persisted_owners:
            owner_index = record_writes.get("state/index/owners.json")
            if not isinstance(owner_index, dict):
                owner_index = copy.deepcopy(self.repository.read_json("state/index/owners.json"))
                record_writes["state/index/owners.json"] = owner_index
            if isinstance(owner_index.get("owner_count"), int) and not isinstance(owner_index.get("owner_count"), bool):
                owner_index["owner_count"] += 1
        training = doctrine.get("training") if isinstance(doctrine.get("training"), Mapping) else {}
        compact = self._generic_team_doctrine(team, at=at, doctrine_identity=str(doctrine.get("identity") or "adaptive combined-arms doctrine"), motto=str(doctrine.get("motto") or "See. Shape. Secure. Return."), training_focus=training.get("shared_drills", []) if isinstance(training.get("shared_drills"), list) else ())
        old = doctrine.get("familiarity") if isinstance(doctrine.get("familiarity"), Mapping) else {}
        compact["familiarity"] = {ref: max(0, min(100, int(old.get(ref, 0)))) for ref in team.get("member_refs", []) if isinstance(ref, str)}
        compact["approved_by"] = str(doctrine.get("approved_by") or compact["approved_by"])
        record_writes[doctrine_path] = compact
        return result
