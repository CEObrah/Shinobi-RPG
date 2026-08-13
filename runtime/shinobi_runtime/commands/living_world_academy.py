from __future__ import annotations

from shinobi_runtime.commands.living_world_support import *
from shinobi_runtime.commands.team_composition import TeamMemberProfile

_POPULATION_REGISTRY_PATH = "state/population/registry.json"


def _configured_refs(spec: Mapping[str, Any], key: str, *, limit: int) -> list[str]:
    raw = spec.get(key, [])
    if not isinstance(raw, list):
        raise CommandRejectedError("autonomous_team_policy_invalid")
    refs = [ref for ref in raw if isinstance(ref, str) and ref]
    return list(dict.fromkeys(refs))[:limit]


def _academy_affinity_groups(spec: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """Return bounded, validated soft cohort preferences.

    These are administrative preferences, not canon locks. A group is used only
    while all of its students are actually eligible; otherwise ordinary
    capability-complementary selection remains the fallback.
    """
    raw = spec.get("cohort_affinity_groups", [])
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise CommandRejectedError("autonomous_team_policy_invalid")
    groups: list[Mapping[str, Any]] = []
    for row in raw[:32]:
        if not isinstance(row, Mapping):
            raise CommandRejectedError("autonomous_team_policy_invalid")
        students = row.get("student_refs")
        instructors = row.get("preferred_instructor_refs", [])
        if (
            not isinstance(students, list)
            or len(students) != 3
            or any(not isinstance(ref, str) or not ref for ref in students)
            or len(set(students)) != 3
            or not isinstance(instructors, list)
            or any(not isinstance(ref, str) or not ref for ref in instructors)
        ):
            raise CommandRejectedError("autonomous_team_policy_invalid")
        groups.append(
            {
                "student_refs": tuple(students),
                "preferred_instructor_refs": tuple(dict.fromkeys(instructors)),
            }
        )
    return tuple(groups)


def _academy_student_roster(
    graduates: Sequence[TeamMemberProfile],
    *,
    spec: Mapping[str, Any],
) -> tuple[tuple[TeamMemberProfile, ...], Optional[Mapping[str, Any]]]:
    eligible = {profile.person_ref: profile for profile in graduates if profile.available}
    for group in _academy_affinity_groups(spec):
        refs = group["student_refs"]
        if all(ref in eligible for ref in refs):
            return tuple(eligible[ref] for ref in refs), group
    return tuple(select_complementary_roster(graduates, target_size=3)), None


def _academy_instructor(
    instructors: Sequence[TeamMemberProfile],
    *,
    affinity_group: Optional[Mapping[str, Any]],
) -> Optional[TeamMemberProfile]:
    eligible = [profile for profile in instructors if profile.available]
    if not eligible:
        return None
    preferred: set[str] = set()
    if isinstance(affinity_group, Mapping):
        raw = affinity_group.get("preferred_instructor_refs", ())
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            preferred = {ref for ref in raw if isinstance(ref, str)}
    preferred_pool = [profile for profile in eligible if profile.person_ref in preferred]
    pool = preferred_pool or eligible
    return max(
        pool,
        key=lambda row: (
            row.scores.get("leadership", 0) * 2 + row.scores.get("support", 0),
            row.person_ref,
        ),
    )


class LivingWorldAcademyMixin:
    def _academy_dynamic_candidate_refs(
        self,
        *,
        spec: Mapping[str, Any],
        policy_book: Any,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> tuple[list[str], list[str]]:
        """Return bounded exact graduate and explicitly authorized instructor candidates.

        Exact Genin may expand dynamically from the conserved Academy service
        roster so future materialized graduates can join the living world.
        Instructors are different: Academy leadership is an institutional office,
        not a generic property of being a ready Jōnin. The configured instructor
        pool therefore remains authoritative unless policy explicitly enables
        service-pool instructor recruitment.
        """
        graduates = _configured_refs(spec, "graduate_candidate_refs", limit=64)
        instructors = _configured_refs(spec, "instructor_candidate_refs", limit=64)
        allow_service_instructors = spec.get("allow_service_pool_instructors") is True
        parent = spec.get("parent_institution_ref")
        if not isinstance(parent, str) or not parent:
            return graduates, instructors
        try:
            assignment = policy_book.institution_assignment(parent)
        except Exception:
            return graduates, instructors
        service_pool_id = assignment.get("service_pool_id") if isinstance(assignment, Mapping) else None
        if not isinstance(service_pool_id, str) or not service_pool_id:
            return graduates, instructors
        population = record_writes.get(_POPULATION_REGISTRY_PATH)
        if population is None:
            try:
                population = self.repository.read_json(_POPULATION_REGISTRY_PATH)
            except (FileNotFoundError, ValueError):
                population = None
        pools = population.get("pools") if isinstance(population, Mapping) else None
        service = pools.get(service_pool_id) if isinstance(pools, Mapping) else None
        representation = service.get("representation") if isinstance(service, Mapping) else None
        rostered = representation.get("rostered_person_refs") if isinstance(representation, Mapping) else None
        if not isinstance(rostered, list):
            return graduates, instructors
        for ref in rostered[:256]:
            if not isinstance(ref, str) or not ref:
                continue
            try:
                _path, _digest, record = self._resolve_covered_owner_view(
                    ref, cache=_OwnerResolutionCache()
                )
            except CommandRejectedError:
                continue
            if not isinstance(record, Mapping):
                continue
            rank = str(
                record.get("official_rank_or_status")
                or (record.get("career_state") or {}).get("current_rank_or_status")
                or ""
            ).lower()
            if "genin" in rank and "academy" not in rank:
                graduates.append(ref)
            if allow_service_instructors and ("jonin" in rank or "jōnin" in rank):
                instructors.append(ref)
        return (
            list(dict.fromkeys(graduates))[:128],
            list(dict.fromkeys(instructors))[:128],
        )

    def _apply_academy_team_form(
        self,
        *,
        decision: Any,
        at: CampaignTime,
        command: CommandEnvelope,
        scheduler: CausalSchedulerRegistry,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
        faction_record: Dict[str, Any],
    ) -> Optional[Mapping[str, Any]]:
        """Form one standing instructor-led team from already-graduated exact people."""
        spec = decision.payload.get("team_creation")
        if not isinstance(spec, Mapping) or spec.get("mode") != "academy_dynamic":
            return None
        faction_id = decision.payload.get("faction_id")
        if not isinstance(faction_id, str):
            raise CommandRejectedError("autonomous_team_policy_invalid")
        faction = faction_record.get("faction") if isinstance(faction_record, Mapping) else None
        plan = faction.get("plan_state") if isinstance(faction, Mapping) else None
        if not isinstance(plan, dict):
            raise CommandRejectedError("faction_owner_invalid")
        formed = plan.setdefault("autonomous_team_refs", [])
        if not isinstance(formed, list):
            raise CommandRejectedError("faction_owner_invalid")
        raw_limit = spec.get("max_active_teams", 8)
        limit = max(
            1,
            min(
                32,
                raw_limit
                if isinstance(raw_limit, int) and not isinstance(raw_limit, bool)
                else 8,
            ),
        )
        active_count = 0
        for ref in formed[:64]:
            if not isinstance(ref, str):
                continue
            try:
                _path, team = self._living_team_view(ref, record_writes=record_writes)
            except CommandRejectedError:
                continue
            if team.get("status") == "active":
                active_count += 1
        if active_count >= limit:
            return {
                "kind": "team_form",
                "skipped": "autonomous_team_capacity_reached",
                "capacity": limit,
            }

        active_members = self._active_exact_team_members(record_writes)
        policy_book = self._autonomy_policy_book()
        graduate_refs, instructor_refs = self._academy_dynamic_candidate_refs(
            spec=spec,
            policy_book=policy_book,
            record_writes=record_writes,
        )

        graduates: list[TeamMemberProfile] = []
        for ref in graduate_refs:
            if ref in active_members or ref == command.actor_id:
                continue
            try:
                _path, _digest, record = self._resolve_covered_owner_view(
                    ref, cache=_OwnerResolutionCache()
                )
            except CommandRejectedError:
                continue
            rank = (
                str(
                    record.get("official_rank_or_status")
                    or (record.get("career_state") or {}).get("current_rank_or_status")
                    or ""
                ).lower()
                if isinstance(record, Mapping)
                else ""
            )
            profile = self._living_member_profile(ref, record_writes=record_writes)
            if (
                "genin" in rank
                and "academy" not in rank
                and profile is not None
                and profile.available
            ):
                graduates.append(profile)
        if len(graduates) < 3:
            return {"kind": "team_form", "skipped": "fewer_than_three_unassigned_exact_genin"}

        students, affinity_group = _academy_student_roster(graduates, spec=spec)
        if len(students) != 3:
            return {"kind": "team_form", "skipped": "no_complementary_genin_trio"}

        instructors: list[TeamMemberProfile] = []
        for ref in instructor_refs:
            if ref in active_members or ref == command.actor_id:
                continue
            try:
                _path, _digest, record = self._resolve_covered_owner_view(
                    ref, cache=_OwnerResolutionCache()
                )
            except CommandRejectedError:
                continue
            if not isinstance(record, Mapping):
                continue
            rank = str(
                record.get("official_rank_or_status")
                or (record.get("career_state") or {}).get("current_rank_or_status")
                or ""
            ).lower()
            profile = self._living_member_profile(ref, record_writes=record_writes)
            if (
                ("jonin" in rank or "jōnin" in rank)
                and profile is not None
                and profile.available
            ):
                instructors.append(profile)
        instructor = _academy_instructor(instructors, affinity_group=affinity_group)
        if instructor is None:
            return {"kind": "team_form", "skipped": "no_available_exact_jonin_instructor"}

        roster = (instructor, *students)
        suffix = hashlib.sha256(
            f"{faction_id}\x00{at}\x00{'|'.join(sorted(profile.person_ref for profile in roster))}".encode()
        ).hexdigest()[:10]
        prefix = str(spec.get("team_id_prefix") or "team.generated")
        team_id = f"{prefix}.{suffix}"
        roles = derive_member_roles(roster, leader_ref=instructor.person_ref)
        path, team = self._register_exact_team_state(
            team_id=team_id,
            name=f"{str(spec.get('team_name_prefix') or 'Mission Team')} {suffix.upper()}",
            team_type=str(spec.get("team_type") or "standard_mission_team"),
            parent_institution_ref=str(spec.get("parent_institution_ref") or faction_id),
            assignment_authority_ref=str(spec.get("assignment_authority_ref") or decision.actor_ref),
            leader_ref=instructor.person_ref,
            member_refs=[profile.person_ref for profile in roster],
            roles=roles,
            classification=str(spec.get("classification") or "public"),
            at=at,
            basis="Autonomous standing-team assignment from current graduated unassigned exact personnel.",
            scheduler=scheduler,
            record_writes=record_writes,
        )
        lifecycle = team.get("lifecycle")
        if isinstance(lifecycle, dict):
            lifecycle.update(
                {
                    "purpose_kind": "standing",
                    "purpose_ref": None,
                    "purpose_status": "active",
                    "replacement_policy": "maintain_strength",
                    "target_size": len(team.get("member_refs", [])),
                    "exclusive_active_membership": True,
                    "autonomy_owner_ref": faction_id,
                }
            )
        record_writes[path] = team
        if team_id not in formed:
            formed.append(team_id)
            formed.sort()
        event_id = self._append_internal_event(
            world_events,
            command=command,
            identity=f"{team_id}:{at}:academy-team",
            kind="exact_team_assigned_from_graduates",
            at=at,
            host_refs=(faction_id, team_id),
            actor_refs=tuple(team.get("member_refs", [])),
            affected_owner_refs=(path, self._team_history_path(team_id)),
            material_consequence_refs=(team_id,),
            classification=str(team.get("classification") or "public"),
            audience_refs=tuple(team.get("member_refs", [])),
            source_refs=(str(team.get("assignment_authority_ref")),),
        )
        return {
            "kind": "team_form",
            "team_id": team_id,
            "event_id": event_id,
            "member_refs": list(team.get("member_refs", [])),
            "selection_basis": (
                "current_conserved_service_roster_rank_availability_exclusive_membership_"
                "authorized_instructor_pool_soft_cohort_affinity_complementary_capabilities"
            ),
        }


__all__ = [
    "LivingWorldAcademyMixin",
    "_academy_affinity_groups",
    "_academy_student_roster",
    "_academy_instructor",
]
