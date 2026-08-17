"""Generic authority and membership resolution.

Authority is proved from persisted organizational facts. A caller may reference
an authority, but this module decides whether that authority actually permits
the requested action.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Tuple


OwnerLoader = Callable[[str], Mapping[str, Any]]


@dataclass(frozen=True)
class AuthorityDecision:
    allowed: bool
    basis: str
    authority_ref: Optional[str] = None
    organization_ref: Optional[str] = None


class DomainAuthorityResolver:
    """Resolve generic team, institution and force authority.

    The resolver is setting-neutral. It understands common record shapes, not
    campaign-specific organization, team, or person names.
    """

    def __init__(
        self,
        *,
        load_owner: OwnerLoader,
        assignments: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._load_owner = load_owner
        self._assignments = assignments or {"records": []}

    def _owner(self, ref: str) -> Optional[Mapping[str, Any]]:
        try:
            value = self._load_owner(ref)
        except Exception:
            return None
        return value if isinstance(value, Mapping) else None

    @staticmethod
    def _team_members(team: Mapping[str, Any]) -> Tuple[str, ...]:
        values = team.get("member_refs")
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            return ()
        return tuple(values)

    def team_command(
        self,
        *,
        commander_ref: str,
        subject_refs: Sequence[str],
        team_ref: str,
    ) -> AuthorityDecision:
        team = self._owner(team_ref)
        if not isinstance(team, Mapping) or team.get("schema") != "exact-team":
            return AuthorityDecision(False, "team_unresolved")
        if team.get("status") != "active":
            return AuthorityDecision(False, "team_inactive", organization_ref=team_ref)
        members = set(self._team_members(team))
        if not set(subject_refs).issubset(members):
            return AuthorityDecision(False, "subjects_not_team_members", organization_ref=team_ref)
        leaders = {team.get("leader_ref"), team.get("deputy_ref")}
        leaders.discard(None)
        if commander_ref not in leaders:
            return AuthorityDecision(False, "commander_not_team_leadership", organization_ref=team_ref)
        return AuthorityDecision(True, "team_leadership", authority_ref=team_ref, organization_ref=team_ref)

    def owner_leadership(
        self,
        *,
        holder_ref: str,
        owner_ref: str,
    ) -> AuthorityDecision:
        owner = self._owner(owner_ref)
        if not isinstance(owner, Mapping):
            return AuthorityDecision(False, "owner_unresolved", organization_ref=owner_ref)
        if holder_ref == owner_ref:
            return AuthorityDecision(True, "owner_self", authority_ref=owner_ref, organization_ref=owner_ref)
        leader = owner.get("leader_id") or owner.get("leader_ref")
        if leader == holder_ref:
            return AuthorityDecision(True, "owner_leadership", authority_ref=owner_ref, organization_ref=owner_ref)
        leadership = owner.get("leadership")
        if isinstance(leadership, Mapping) and holder_ref in {value for value in leadership.values() if isinstance(value, str)}:
            return AuthorityDecision(True, "owner_leadership_role", authority_ref=owner_ref, organization_ref=owner_ref)
        leadership_ids = owner.get("leadership_ids")
        if isinstance(leadership_ids, list) and holder_ref in {value for value in leadership_ids if isinstance(value, str)}:
            return AuthorityDecision(True, "owner_leadership", authority_ref=owner_ref, organization_ref=owner_ref)
        return AuthorityDecision(False, "not_owner_leadership", organization_ref=owner_ref)

    def institution_leadership(
        self,
        *,
        holder_ref: str,
        institution_ref: str,
    ) -> AuthorityDecision:
        institution = self._owner(institution_ref)
        if not isinstance(institution, Mapping):
            return AuthorityDecision(False, "institution_unresolved")
        leader = institution.get("leader_id") or institution.get("leader_ref")
        leadership_ids = institution.get("leadership_ids")
        if leader != holder_ref and not (
            isinstance(leadership_ids, list) and holder_ref in {value for value in leadership_ids if isinstance(value, str)}
        ):
            return AuthorityDecision(False, "not_institution_leader", organization_ref=institution_ref)
        return AuthorityDecision(True, "institution_leadership", authority_ref=institution_ref, organization_ref=institution_ref)

    def mission_tasking(
        self,
        *,
        issuer_ref: str,
        authority_ref: str,
        participant_refs: Sequence[str],
        candidate_team_refs: Iterable[str],
    ) -> AuthorityDecision:
        """Prove that an issuer can task the selected participants.

        Valid bases are exact-team leadership, a team's registered assignment
        authority, or leadership of the institution that is that assignment
        authority. Merely naming an existing owner is never sufficient.
        """
        participants = set(participant_refs)
        for team_ref in candidate_team_refs:
            team = self._owner(team_ref)
            if not isinstance(team, Mapping) or team.get("schema") != "exact-team":
                continue
            members = set(self._team_members(team))
            if not participants.issubset(members):
                continue
            if authority_ref == team_ref:
                decision = self.team_command(
                    commander_ref=issuer_ref,
                    subject_refs=participant_refs,
                    team_ref=team_ref,
                )
                if decision.allowed:
                    return decision
            assignment_authority = team.get("assignment_authority_ref")
            if assignment_authority != authority_ref:
                continue
            if issuer_ref == authority_ref:
                return AuthorityDecision(True, "team_assignment_authority", authority_ref=authority_ref, organization_ref=team_ref)
            institutional = self.institution_leadership(holder_ref=issuer_ref, institution_ref=authority_ref)
            if institutional.allowed:
                return AuthorityDecision(True, "institution_tasks_assigned_team", authority_ref=authority_ref, organization_ref=team_ref)
        return AuthorityDecision(False, "mission_tasking_not_authorized")

    def force_grant(
        self,
        *,
        grantor_ref: str,
        force_record: Mapping[str, Any],
    ) -> AuthorityDecision:
        owner_ref = force_record.get("owner_ref")
        if not isinstance(owner_ref, str) or not owner_ref:
            return AuthorityDecision(False, "force_owner_missing")
        owner = self._owner(owner_ref)
        if not isinstance(owner, Mapping):
            return AuthorityDecision(False, "force_owner_unresolved", organization_ref=owner_ref)
        if grantor_ref == owner_ref:
            return AuthorityDecision(True, "force_owner", authority_ref=owner_ref, organization_ref=owner_ref)
        leader = owner.get("leader_id") or owner.get("leader_ref")
        leadership_ids = owner.get("leadership_ids")
        if leader == grantor_ref or (
            isinstance(leadership_ids, list) and grantor_ref in {value for value in leadership_ids if isinstance(value, str)}
        ):
            return AuthorityDecision(True, "force_owner_leadership", authority_ref=owner_ref, organization_ref=owner_ref)
        return AuthorityDecision(False, "force_grant_not_authorized", organization_ref=owner_ref)

    def force_command(
        self,
        *,
        commander_ref: str,
        force_ref: str,
        operational_attachment_ref: Optional[str],
        named_actor_refs: Sequence[str] = (),
        committed_count: Optional[int] = None,
        effective_at: Optional[str] = None,
    ) -> AuthorityDecision:
        records = self._assignments.get("records")
        if not isinstance(records, list):
            return AuthorityDecision(False, "assignment_registry_invalid")
        named = set(named_actor_refs)
        for record in records:
            if not isinstance(record, Mapping):
                continue
            if record.get("status", "active") != "active":
                continue
            if record.get("source_owner") != force_ref:
                continue
            if record.get("receiving_commander") != commander_ref:
                continue
            if effective_at is not None:
                start = record.get("start")
                expiry = record.get("expires_at")
                if isinstance(start, str) and start > effective_at:
                    continue
                if isinstance(expiry, str) and expiry <= effective_at:
                    continue
            if record.get("status", "active") != "active":
                continue
            record_attachment = record.get("operational_attachment_ref")
            if operational_attachment_ref is not None and record_attachment not in (None, operational_attachment_ref):
                continue
            raw_allocations = record.get("raw_allocations")
            formation_refs = record.get("formation_refs")
            allocated_count = record.get("allocated_count")
            if committed_count is not None and isinstance(allocated_count, int) and committed_count > allocated_count:
                continue
            if named:
                allocated = set(raw_allocations) if isinstance(raw_allocations, list) else set()
                if not named.issubset(allocated):
                    continue
            limits = record.get("authority_limits")
            if operational_attachment_ref is not None:
                direct_attachment = record.get("operational_attachment_ref")
                allowed_attachment = direct_attachment
                if allowed_attachment is None and isinstance(limits, Mapping):
                    allowed_attachment = limits.get("operation_ref") or limits.get("team_id")
                if allowed_attachment is not None and allowed_attachment != operational_attachment_ref:
                    continue
            return AuthorityDecision(
                True,
                "force_assignment",
                authority_ref=str(record.get("id") or force_ref),
                organization_ref=force_ref,
            )
        return AuthorityDecision(False, "force_command_not_authorized", organization_ref=force_ref)

    def travel_party(
        self,
        *,
        actor_ref: str,
        traveler_refs: Sequence[str],
        candidate_team_refs: Iterable[str],
    ) -> AuthorityDecision:
        travelers = tuple(traveler_refs)
        if travelers == (actor_ref,) or set(travelers) == {actor_ref}:
            return AuthorityDecision(True, "self_travel")
        for team_ref in candidate_team_refs:
            decision = self.team_command(
                commander_ref=actor_ref,
                subject_refs=travelers,
                team_ref=team_ref,
            )
            if decision.allowed:
                return AuthorityDecision(True, "team_travel_order", authority_ref=team_ref, organization_ref=team_ref)
        return AuthorityDecision(False, "travel_party_not_authorized")
