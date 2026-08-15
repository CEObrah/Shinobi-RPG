"""Persistent exact-team purpose, roster protection, and bounded reconstitution."""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.autonomy import AutonomousDecision, AutonomousPolicyBook
from shinobi_runtime.commands.constants import TERMINAL_MISSION_STATES
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.mission_owner import MissionOwner, mission_owner_path
from shinobi_runtime.commands.team_composition import (
    CAPABILITY_DIMENSIONS,
    TACTICAL_DIMENSIONS,
    TeamMemberProfile,
    derive_member_roles,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry


class TeamLifecycleIntelligenceMixin:
    """Keep exact teams intact for their saved purpose and replace permanent losses lawfully."""

    _PERMANENT_LOSS_REASONS = frozenset({"dead", "deceased", "missing", "inactive", "retired"})

    def _autonomy_policy_book(self) -> AutonomousPolicyBook:
        book = super()._autonomy_policy_book()
        assignments: Dict[str, Mapping[str, Any]] = {}
        for faction_id, raw in book.faction_assignments.items():
            row = dict(raw)
            spec = row.get("team_creation")
            if isinstance(spec, Mapping):
                clean = dict(spec)
                # Active exact-team membership is exclusive by current contract.
                clean.setdefault("purpose_kind", "standing")
                clean.setdefault("replacement_policy", "maintain_strength")
                row["team_creation"] = clean
            assignments[faction_id] = row
        return AutonomousPolicyBook(
            profiles=book.profiles,
            faction_assignments=assignments,
            team_profiles=book.team_profiles,
            institution_assignments=book.institution_assignments,
        )

    @staticmethod
    def _default_team_lifecycle(team: Mapping[str, Any]) -> Dict[str, Any]:
        members = [ref for ref in team.get("member_refs", []) if isinstance(ref, str)]
        return {
            "purpose_kind": "standing",
            "purpose_ref": None,
            "purpose_status": "active",
            "replacement_policy": "authority_review",
            "target_size": len(members),
            "exclusive_active_membership": True,
            "autonomy_owner_ref": None,
        }

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
        path, team = super()._register_exact_team_state(
            team_id=team_id,
            name=name,
            team_type=team_type,
            parent_institution_ref=parent_institution_ref,
            assignment_authority_ref=assignment_authority_ref,
            leader_ref=leader_ref,
            member_refs=member_refs,
            roles=roles,
            classification=classification,
            at=at,
            basis=basis,
            scheduler=scheduler,
            record_writes=record_writes,
        )
        if not isinstance(team.get("lifecycle"), Mapping):
            team["lifecycle"] = self._default_team_lifecycle(team)
        record_writes[path] = team
        return path, team

    def _active_autonomous_teams(
        self,
        faction_record: Mapping[str, Any],
        record_writes: Mapping[str, Mapping[str, Any]],
    ) -> Tuple[Mapping[str, Any], ...]:
        faction = faction_record.get("faction") if isinstance(faction_record.get("faction"), Mapping) else {}
        plan = faction.get("plan_state") if isinstance(faction.get("plan_state"), Mapping) else {}
        refs = plan.get("autonomous_team_refs") if isinstance(plan, Mapping) else []
        teams = []
        if isinstance(refs, list):
            for ref in refs:
                if not isinstance(ref, str):
                    continue
                team = self._team_view(ref, record_writes)
                if isinstance(team, Mapping) and team.get("status") == "active":
                    teams.append(team)
        return tuple(sorted(teams, key=lambda row: str(row.get("id", ""))))

    def _active_autonomous_team(
        self,
        faction_record: Mapping[str, Any],
        record_writes: Mapping[str, Mapping[str, Any]],
    ) -> Optional[Mapping[str, Any]]:
        teams = self._active_autonomous_teams(faction_record, record_writes)
        if getattr(self, "_team_form_allows_additional", False):
            # Team creation is constrained by the declared eligible roster and
            # exclusive active membership, not by an arbitrary organization-wide
            # count.  The lower layer may therefore attempt one additional team
            # during this bounded review; lack of eligible people stops creation.
            return None
        return teams[0] if teams else None

    @staticmethod
    def _mission_purpose_is_terminal(repository: Any, purpose_ref: str) -> bool:
        try:
            owner = MissionOwner.from_record(repository.read_json(mission_owner_path(purpose_ref)))
        except (FileNotFoundError, TypeError, ValueError):
            return False
        return owner.mission.state in TERMINAL_MISSION_STATES

    def _team_lifecycle_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        if command.payload.get("action") == "dissolve":
            team_ref = command.payload.get("team_ref")
            if isinstance(team_ref, str):
                _path, team = self._exact_team(team_ref)
                lifecycle = team.get("lifecycle") if isinstance(team.get("lifecycle"), Mapping) else None
                if isinstance(lifecycle, Mapping) and lifecycle.get("purpose_kind") == "mission_bound":
                    status = lifecycle.get("purpose_status")
                    purpose_ref = lifecycle.get("purpose_ref")
                    if status == "active" and (
                        not isinstance(purpose_ref, str)
                        or not self._mission_purpose_is_terminal(self.repository, purpose_ref)
                    ):
                        raise CommandRejectedError("team_purpose_still_active")
        plan = super()._team_lifecycle_resolution(command, meta, current_time)
        if command.payload.get("action") != "dissolve":
            return plan
        writes = dict(plan.writes)
        changed = False
        for path, raw in list(writes.items()):
            try:
                record = json.loads(raw.decode("utf-8"))
            except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(record, dict) or record.get("schema") != "exact-team":
                continue
            lifecycle = record.get("lifecycle")
            if isinstance(lifecycle, dict):
                lifecycle["purpose_status"] = "ended"
                writes[path] = _json_bytes(record)
                changed = True
        return _BuiltPlan(
            code=plan.code,
            affected_refs=plan.affected_refs,
            writes=writes if changed else plan.writes,
            result=plan.result,
            validator=plan.validator,
        )

    def _apply_autonomous_decision(
        self,
        *,
        decision: Any,
        at: CampaignTime,
        command: CommandEnvelope,
        scheduler: CausalSchedulerRegistry,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
        faction_record: Dict[str, Any],
    ) -> Mapping[str, Any]:
        clean_decision = decision
        allow_additional = False
        if getattr(decision, "kind", None) == "team_form":
            payload = dict(decision.payload)
            spec = payload.get("team_creation")
            if isinstance(spec, Mapping):
                clean_spec = dict(spec)
                payload["team_creation"] = clean_spec
                clean_decision = AutonomousDecision(
                    kind=decision.kind,
                    actor_ref=decision.actor_ref,
                    reason=decision.reason,
                    payload=payload,
                    material=decision.material,
                )
                # Explicit stable team IDs are unique. Dynamic team policies may
                # create another team whenever enough eligible uncommitted people
                # exist.
                allow_additional = not isinstance(clean_spec.get("team_id"), str)
        if allow_additional:
            self._team_form_allows_additional = True
        try:
            result = super()._apply_autonomous_decision(
                decision=clean_decision,
                at=at,
                command=command,
                scheduler=scheduler,
                world_events=world_events,
                record_writes=record_writes,
                faction_record=faction_record,
            )
        finally:
            if hasattr(self, "_team_form_allows_additional"):
                delattr(self, "_team_form_allows_additional")

        team_id = result.get("team_id") if isinstance(result, Mapping) else None
        if getattr(clean_decision, "kind", None) == "team_form" and isinstance(team_id, str):
            spec = clean_decision.payload.get("team_creation")
            faction_id = clean_decision.payload.get("faction_id")
            if isinstance(spec, Mapping) and isinstance(faction_id, str):
                for path, record in record_writes.items():
                    if not isinstance(record, dict) or record.get("schema") != "exact-team" or record.get("id") != team_id:
                        continue
                    lifecycle = record.get("lifecycle")
                    if not isinstance(lifecycle, dict):
                        lifecycle = self._default_team_lifecycle(record)
                        record["lifecycle"] = lifecycle
                    kind = spec.get("purpose_kind")
                    lifecycle["purpose_kind"] = kind if kind in ("standing", "mission_bound") else "standing"
                    purpose_ref = spec.get("purpose_ref")
                    lifecycle["purpose_ref"] = purpose_ref if isinstance(purpose_ref, str) and purpose_ref else None
                    lifecycle["purpose_status"] = "active"
                    replacement = spec.get("replacement_policy")
                    lifecycle["replacement_policy"] = replacement if replacement in ("maintain_strength", "authority_review", "none") else "maintain_strength"
                    lifecycle["target_size"] = len([ref for ref in record.get("member_refs", []) if isinstance(ref, str)])
                    lifecycle["exclusive_active_membership"] = True
                    lifecycle["autonomy_owner_ref"] = faction_id
                    record_writes[path] = record
                    break
        return result

    @classmethod
    def _permanent_loss(cls, profile: Optional[TeamMemberProfile]) -> bool:
        return profile is not None and profile.availability_reason in cls._PERMANENT_LOSS_REASONS

    @staticmethod
    def _replacement_score(profile: TeamMemberProfile, coverage: Mapping[str, int]) -> int:
        gain = sum(max(0, profile.scores.get(dim, 0) - coverage.get(dim, 0)) for dim in TACTICAL_DIMENSIONS)
        baseline = sum(profile.scores.get(dim, 0) for dim in TACTICAL_DIMENSIONS) // max(1, len(TACTICAL_DIMENSIONS))
        return gain * 3 + baseline + profile.scores.get("leadership", 0) // 3 + profile.scores.get("support", 0) // 3

    def _apply_team_autonomy_review(
        self,
        *,
        owner_ref: str,
        at: CampaignTime,
        compacted: int,
        command: CommandEnvelope,
        scheduler: CausalSchedulerRegistry,
        policy_book: AutonomousPolicyBook,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Mapping[str, Any]:
        result = super()._apply_team_autonomy_review(
            owner_ref=owner_ref,
            at=at,
            compacted=compacted,
            command=command,
            scheduler=scheduler,
            policy_book=policy_book,
            world_events=world_events,
            record_writes=record_writes,
        )
        team = record_writes.get(owner_ref)
        if not isinstance(team, dict) or team.get("schema") != "exact-team" or team.get("status") != "active":
            return result
        members = [ref for ref in team.get("member_refs", []) if isinstance(ref, str)]
        lifecycle = team.get("lifecycle")
        if not isinstance(lifecycle, dict) or lifecycle.get("replacement_policy") != "maintain_strength":
            return result
        autonomy_owner = lifecycle.get("autonomy_owner_ref")
        if not isinstance(autonomy_owner, str) or not autonomy_owner:
            return result

        profiles = {ref: self._member_profile(ref) for ref in members}
        if any(profile is not None and profile.availability_reason == "player_agency_protected" for profile in profiles.values()):
            return result
        lost = [ref for ref in members if self._permanent_loss(profiles.get(ref))]
        if not lost:
            return result
        survivors = [ref for ref in members if ref not in lost]
        target = lifecycle.get("target_size", len(members))
        if isinstance(target, bool) or not isinstance(target, int):
            target = len(members)
        target = max(2, min(16, target))
        needed = max(0, target - len(survivors))

        assignment = policy_book.faction_assignments.get(autonomy_owner)
        if not isinstance(assignment, Mapping):
            return {**dict(result), "reconstitution": {"status": "vacancy_pending", "lost_member_refs": lost}}
        spec = assignment.get("team_creation")
        if not isinstance(spec, Mapping):
            return {**dict(result), "reconstitution": {"status": "vacancy_pending", "lost_member_refs": lost}}
        try:
            _path, _digest, faction_view = self._resolve_covered_owner_view(autonomy_owner, cache=_OwnerResolutionCache())
        except CommandRejectedError:
            return {**dict(result), "reconstitution": {"status": "vacancy_pending", "lost_member_refs": lost}}
        if not isinstance(faction_view, Mapping):
            return result

        other_active = self._active_exact_team_members(record_writes) - set(members)
        candidates: list[TeamMemberProfile] = []
        for ref in self._candidate_refs(assignment, faction_view, spec):
            if ref in other_active or ref in members:
                continue
            profile = self._member_profile(ref)
            if profile is None or not profile.available:
                continue
            candidates.append(profile)

        survivor_profiles: Dict[str, TeamMemberProfile] = {}
        for ref in survivors:
            profile = profiles.get(ref)
            if profile is None:
                profile = TeamMemberProfile(ref, True, "profile_unavailable", {dim: 0 for dim in CAPABILITY_DIMENSIONS})
            survivor_profiles[ref] = profile
        coverage = {
            dim: max((profile.scores.get(dim, 0) for profile in survivor_profiles.values()), default=0)
            for dim in TACTICAL_DIMENSIONS
        }
        recruits: list[TeamMemberProfile] = []
        remaining = list(candidates)
        while remaining and len(recruits) < needed:
            chosen = sorted(remaining, key=lambda profile: (-self._replacement_score(profile, coverage), profile.person_ref))[0]
            recruits.append(chosen)
            remaining.remove(chosen)
            for dim in TACTICAL_DIMENSIONS:
                coverage[dim] = max(coverage[dim], chosen.scores.get(dim, 0))

        new_roster = survivors + [profile.person_ref for profile in recruits]
        if len(new_roster) < 2:
            return {**dict(result), "reconstitution": {"status": "vacancy_pending", "lost_member_refs": lost}}
        all_profiles = list(survivor_profiles.values()) + recruits
        old_leader = team.get("leader_ref")
        if old_leader in new_roster:
            leader = str(old_leader)
        else:
            leader = sorted(all_profiles, key=lambda profile: (-profile.scores.get("leadership", 0), profile.person_ref))[0].person_ref
        old_deputy = team.get("deputy_ref")
        if old_deputy in new_roster and old_deputy != leader:
            deputy = str(old_deputy)
        else:
            deputy = next((profile.person_ref for profile in sorted(all_profiles, key=lambda profile: (-profile.scores.get("leadership", 0), profile.person_ref)) if profile.person_ref != leader), None)

        team["member_refs"] = new_roster
        team["leader_ref"] = leader
        team["deputy_ref"] = deputy
        team["roles"] = derive_member_roles(all_profiles, leader_ref=leader)
        training = team.get("training")
        if isinstance(training, dict):
            instructors = [ref for ref in training.get("instructor_refs", []) if isinstance(ref, str) and ref not in lost]
            if leader not in instructors:
                instructors.insert(0, leader)
            training["instructor_refs"] = list(dict.fromkeys(instructors))
        record_writes[owner_ref] = team

        doctrine_ref = team.get("doctrine_ref")
        doctrine_path = None
        old_doctrine: Optional[Mapping[str, Any]] = None
        if isinstance(doctrine_ref, str):
            for path, record in record_writes.items():
                if isinstance(record, Mapping) and record.get("schema") == "team-doctrine" and record.get("id") == doctrine_ref:
                    doctrine_path, old_doctrine = path, record
                    break
            if old_doctrine is None:
                try:
                    doctrine_path, _digest, view = self._resolve_covered_owner_view(doctrine_ref, cache=_OwnerResolutionCache())
                    old_doctrine = view if isinstance(view, Mapping) else None
                except CommandRejectedError:
                    old_doctrine = None
        if doctrine_path is not None and isinstance(old_doctrine, Mapping):
            old_training = old_doctrine.get("training") if isinstance(old_doctrine.get("training"), Mapping) else {}
            compact = self._generic_team_doctrine(
                team,
                at=at,
                doctrine_identity=str(old_doctrine.get("identity") or "adaptive combined-arms doctrine"),
                motto=str(old_doctrine.get("motto") or "See. Shape. Secure. Return."),
                training_focus=old_training.get("shared_drills", []) if isinstance(old_training.get("shared_drills"), list) else (),
            )
            familiarity = old_doctrine.get("familiarity") if isinstance(old_doctrine.get("familiarity"), Mapping) else {}
            compact["familiarity"] = {ref: max(0, min(100, int(familiarity.get(ref, 0)))) for ref in new_roster}
            compact["approved_by"] = str(old_doctrine.get("approved_by") or compact["approved_by"])
            record_writes[doctrine_path] = compact

        event_id = self._append_internal_event(
            world_events,
            command=command,
            identity=f"{team.get('id')}:{at}:reconstituted",
            kind="exact_team_reconstituted",
            at=at,
            host_refs=(str(team.get("id")), autonomy_owner),
            actor_refs=tuple(new_roster),
            affected_owner_refs=tuple(ref for ref in (owner_ref, doctrine_path) if isinstance(ref, str)),
            material_consequence_refs=tuple(lost + [profile.person_ref for profile in recruits]),
            classification=str(team.get("classification") or "restricted"),
            audience_refs=(),
            source_refs=(autonomy_owner,),
        )
        return {
            **dict(result),
            "reconstitution": {
                "status": "reconstituted" if len(new_roster) >= target else "partial_reconstitution",
                "event_id": event_id,
                "lost_member_refs": lost,
                "replacement_member_refs": [profile.person_ref for profile in recruits],
                "member_refs": new_roster,
                "leader_ref": leader,
                "target_size": target,
            },
        }
