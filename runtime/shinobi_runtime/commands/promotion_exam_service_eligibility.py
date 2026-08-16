"""Source-rank service eligibility lifecycle for promotion examinations.

Academy graduation intentionally clears promotion eligibility. This extension
provides the missing later lifecycle: at a promotion-exam registration opening,
exact Genin on active standard mission teams become eligible only after an
authored minimum period of persisted source-rank service. Eligibility is an
institutional status and may be reviewed for player-led teams, but autonomous
registration still excludes them so the player's submission choice is preserved.
"""
from __future__ import annotations

import copy
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _OwnerResolutionCache, _campaign_datetime
from shinobi_runtime.commands.domains.autonomy import AutonomyCommandsMixin
from shinobi_runtime.commands import promotion_exam_scheduler as scheduler
from shinobi_runtime.commands import promotion_exam_integrity as integrity
from shinobi_runtime.sim.events import CampaignTime

_INSTALLED = False


def _eligibility_config(profile: Mapping[str, Any]) -> tuple[int, bool]:
    config = profile.get("eligibility_review")
    if not isinstance(config, Mapping):
        raise CommandRejectedError("promotion_exam_rules_invalid")
    days = config.get("minimum_source_rank_service_days")
    requires_active_team = config.get("requires_active_standard_mission_team")
    requires_ready = config.get("requires_ready_condition")
    if (
        isinstance(days, bool)
        or not isinstance(days, int)
        or days < 0
        or days > 3650
        or requires_active_team is not True
        or not isinstance(requires_ready, bool)
    ):
        raise CommandRejectedError("promotion_exam_rules_invalid")
    return days, requires_ready


def _latest_source_rank_at(person: Mapping[str, Any], profile: Mapping[str, Any]) -> CampaignTime | None:
    source_rank = scheduler._rank_key(profile.get("source_rank"))
    life = person.get("life_course_state")
    rows = life.get("rank_history") if isinstance(life, Mapping) else None
    if source_rank is None or not isinstance(rows, list):
        return None
    found: CampaignTime | None = None
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if scheduler._rank_key(row.get("rank")) != source_rank:
            continue
        raw_at = row.get("at")
        if not isinstance(raw_at, str):
            continue
        try:
            ranked_at = CampaignTime.parse(raw_at)
        except (TypeError, ValueError):
            continue
        if found is None or ranked_at > found:
            found = ranked_at
    return found


def service_eligibility_due(
    person: Mapping[str, Any],
    profile: Mapping[str, Any],
    at: CampaignTime,
) -> bool:
    if person.get("schema") != "shinobi_character" or person.get("life_status") not in ("active", "alive"):
        return False
    career = person.get("career_state")
    if not isinstance(career, Mapping):
        return False
    if career.get("promotion_eligible") is True:
        return False
    source_rank = scheduler._rank_key(profile.get("source_rank"))
    if source_rank is None or scheduler._rank_key(person.get("official_rank_or_status")) != source_rank:
        return False
    service_village = profile.get("service_village")
    affiliation = person.get("village_or_affiliation")
    if (
        not isinstance(service_village, str)
        or not isinstance(affiliation, str)
        or service_village.lower() not in affiliation.lower()
    ):
        return False
    minimum_days, requires_ready = _eligibility_config(profile)
    if requires_ready:
        condition = person.get("condition")
        if isinstance(condition, Mapping) and condition.get("readiness") not in (None, "ready"):
            return False
    ranked_at = _latest_source_rank_at(person, profile)
    if ranked_at is None or ranked_at > at:
        return False
    elapsed_seconds = int((_campaign_datetime(at) - _campaign_datetime(ranked_at)).total_seconds())
    return elapsed_seconds >= minimum_days * 24 * 60 * 60


def _registration_authorities(profile: Mapping[str, Any]) -> set[str]:
    raw = profile.get("registration_team_authority_refs")
    if raw is None:
        raw = [profile.get("institution_ref")]
    if not isinstance(raw, list) or any(not isinstance(value, str) or not value for value in raw):
        raise CommandRejectedError("promotion_exam_rules_invalid")
    return set(raw)


def review_npc_team_eligibility(
    self: Any,
    *,
    profile: Mapping[str, Any],
    at: CampaignTime,
    player_id: str,
    record_writes: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    try:
        registry = self.repository.read_json("state/team/registry.json")
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("promotion_exam_team_registry_invalid") from exc
    active_teams = registry.get("active_teams") if isinstance(registry, Mapping) else None
    if not isinstance(active_teams, list) or any(not isinstance(ref, str) for ref in active_teams):
        raise CommandRejectedError("promotion_exam_team_registry_invalid")
    institution_ref = profile.get("institution_ref")
    authorities = _registration_authorities(profile)
    cache = _OwnerResolutionCache()
    reviewed: list[dict[str, Any]] = []
    for team_ref in sorted(set(active_teams)):
        try:
            _team_path, team = self._exact_team(team_ref)
        except CommandRejectedError:
            continue
        members = team.get("member_refs") if isinstance(team, Mapping) else None
        leader_ref = team.get("leader_ref") if isinstance(team, Mapping) else None
        if (
            team.get("schema") != "exact-team"
            or team.get("status") != "active"
            or team.get("team_type") != "standard_mission_team"
            or team.get("parent_institution_ref") != institution_ref
            or team.get("assignment_authority_ref") not in authorities
            or not isinstance(leader_ref, str)
            or not leader_ref
            or not isinstance(members, list)
            or any(not isinstance(ref, str) or not ref for ref in members)
        ):
            continue
        for member_ref in members:
            if member_ref == leader_ref:
                continue
            try:
                path, _digest, view = self._resolve_covered_owner_view(member_ref, cache=cache)
            except CommandRejectedError:
                continue
            subject = record_writes.get(path)
            if subject is None:
                subject = copy.deepcopy(dict(view)) if isinstance(view, Mapping) else None
            if not isinstance(subject, dict) or not service_eligibility_due(subject, profile, at):
                continue
            career = subject.get("career_state")
            if not isinstance(career, dict):
                raise CommandRejectedError("career_state_invalid")
            career["promotion_eligible"] = True
            life = subject.get("life_course_state")
            status_history = life.get("status_history") if isinstance(life, dict) else None
            if isinstance(status_history, list):
                status_history.append(
                    f"{at}: promotion eligibility: {profile.get('source_rank')} service threshold satisfied for {profile.get('id')}."
                )
                if len(status_history) > 128:
                    del status_history[:-128]
            record_writes[path] = subject
            reviewed.append(
                {
                    "candidate_ref": member_ref,
                    "team_ref": team_ref,
                    "instructor_ref": leader_ref,
                    "player_led": leader_ref == player_id,
                    "path": path,
                }
            )
    return reviewed


def _group_new_registrations(
    reviewed: list[dict[str, Any]],
    *,
    pipeline: Mapping[str, Any],
    cycle_id: str,
    player_id: str,
) -> list[dict[str, Any]]:
    registered = set(scheduler.registered_candidate_refs(pipeline, cycle_id))
    grouped: dict[tuple[str, str], list[str]] = {}
    for row in reviewed:
        candidate_ref = row["candidate_ref"]
        if candidate_ref in registered or row.get("instructor_ref") == player_id:
            continue
        key = (row["team_ref"], row["instructor_ref"])
        grouped.setdefault(key, []).append(candidate_ref)
    return [
        {
            "team_ref": team_ref,
            "instructor_ref": instructor_ref,
            "candidate_refs": sorted(set(candidate_refs)),
        }
        for (team_ref, instructor_ref), candidate_refs in sorted(grouped.items())
        if candidate_refs
    ]


def install_promotion_exam_service_eligibility() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original = AutonomyCommandsMixin._apply_institution_autonomy_review
    if getattr(original, "_promotion_exam_service_eligibility", False):
        _INSTALLED = True
        return

    @wraps(original)
    def wrapped(
        self: Any,
        *,
        institution: dict[str, Any],
        at: CampaignTime,
        compacted: int,
        command: Any,
        policy_book: Any,
        world_events: dict[str, Any],
        record_writes: dict[str, dict[str, Any]],
    ) -> Mapping[str, Any]:
        base = original(
            self,
            institution=institution,
            at=at,
            compacted=compacted,
            command=command,
            policy_book=policy_book,
            world_events=world_events,
            record_writes=record_writes,
        )
        cycle = base.get("promotion_exam_cycle") if isinstance(base, Mapping) else None
        if not isinstance(cycle, Mapping) or cycle.get("phase") != "registration":
            return base
        cycle_id = cycle.get("cycle_id")
        profile_ref = cycle.get("profile_ref")
        if not isinstance(cycle_id, str) or not isinstance(profile_ref, str):
            raise CommandRejectedError("promotion_exam_cycle_state_invalid")
        profile = next(
            (row for row in scheduler.promotion_exam_profiles(self.repository) if row.get("id") == profile_ref),
            None,
        )
        if not isinstance(profile, Mapping):
            raise CommandRejectedError("promotion_exam_cycle_state_invalid")
        reviewed = review_npc_team_eligibility(
            self,
            profile=profile,
            at=at,
            player_id=command.actor_id,
            record_writes=record_writes,
        )
        if not reviewed:
            return base
        pipeline = scheduler._pipeline(self.repository, record_writes)
        registrations = _group_new_registrations(
            reviewed,
            pipeline=pipeline,
            cycle_id=cycle_id,
            player_id=command.actor_id,
        )
        added = integrity._append_npc_registrations(
            pipeline,
            profile=profile,
            cycle_id=cycle_id,
            at=at,
            registrations=registrations,
        )
        record_writes["state/reg/shinobi-career-pipeline.json"] = pipeline
        event_id = self._append_internal_event(
            world_events,
            command=command,
            identity=f"{cycle_id}:service-eligibility",
            kind="promotion_exam_service_eligibility_reviewed",
            at=at,
            host_refs=(str(profile["institution_ref"]),),
            actor_refs=tuple(row["candidate_ref"] for row in reviewed),
            affected_owner_refs=tuple(sorted({row["path"] for row in reviewed} | {"state/reg/shinobi-career-pipeline.json"})),
            material_consequence_refs=tuple(
                [f"promotion_eligible:{row['candidate_ref']}:true" for row in reviewed]
                + [f"promotion_exam_registration:{cycle_id}:{ref}" for ref in added]
            ),
            classification="restricted",
            audience_refs=(command.actor_id,),
            source_refs=(str(profile["institution_ref"]),),
            reducer_ref="shinobi_runtime.commands.promotion_exam_service_eligibility",
        )
        enriched = dict(base)
        enriched["promotion_exam_service_eligibility"] = {
            "cycle_id": cycle_id,
            "eligible_candidate_count": len(reviewed),
            "auto_registered_candidate_count": len(added),
            "player_led_eligible_candidate_count": sum(1 for row in reviewed if row.get("player_led") is True),
            "event_id": event_id,
        }
        return enriched

    wrapped._promotion_exam_service_eligibility = True  # type: ignore[attr-defined]
    AutonomyCommandsMixin._apply_institution_autonomy_review = wrapped
    _INSTALLED = True


__all__ = [
    "install_promotion_exam_service_eligibility",
    "review_npc_team_eligibility",
    "service_eligibility_due",
]
