"""Promotion-exam participation and bracket integrity repairs.

The promotion-exam career pipeline remains the sole mutable exam owner. This
extension closes three integration gaps without creating a second rules engine:

* valid legacy ``life_status=active`` shinobi remain eligible wherever the
  runtime already treats active/alive as equivalent living states;
* eligible non-player exact teams are registered and evaluated by the host
  institution instead of requiring player commands for NPC lifecycle work; and
* finals never pair two candidates registered from the same team. If only one
  team remains, those candidates are co-finalists and the tournament portion is
  complete without fabricating an intra-team duel.

Registration/evaluation cardinality is determined by lawful exact state, not by
an arbitrary engine ceiling. If this subsystem later needs bounded work, it must
use resumable causal chunks rather than invalidating a large exam.
"""
from __future__ import annotations

import copy
import hashlib
import json
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache, _json_bytes
from shinobi_runtime.commands.domains.autonomy import AutonomyCommandsMixin
from shinobi_runtime.commands.domains.time import TimeCommandsMixin
from shinobi_runtime.commands import promotion_exam_evaluation as evaluation
from shinobi_runtime.commands import promotion_exam_finals as finals
from shinobi_runtime.commands import promotion_exam_scheduler as scheduler
from shinobi_runtime.sim.events import CampaignTime

_INSTALLED = False
_CAREER = "state/reg/shinobi-career-pipeline.json"


def _living(person: Mapping[str, Any]) -> bool:
    return person.get("life_status") in ("alive", "active")


def _person_matches_profile(person: Mapping[str, Any], profile: Mapping[str, Any]) -> bool:
    if person.get("schema") != "shinobi_character" or not _living(person):
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
    career = person.get("career_state")
    return isinstance(career, Mapping) and career.get("promotion_eligible") is True


def _registration_team_map(
    pipeline: Mapping[str, Any], cycle_id: str
) -> tuple[dict[str, str], dict[str, str]]:
    history = pipeline.get("history")
    if not isinstance(history, list):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    team_by_candidate: dict[str, str] = {}
    instructor_by_candidate: dict[str, str] = {}
    for row in history:
        if not (
            isinstance(row, Mapping)
            and row.get("kind") == "promotion_exam_registration"
            and row.get("cycle_id") == cycle_id
        ):
            continue
        team_ref = row.get("team_ref")
        instructor_ref = row.get("instructor_ref")
        refs = row.get("candidate_refs")
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref for ref in refs):
            raise CommandRejectedError("shinobi_career_pipeline_invalid")
        for candidate_ref in refs:
            resolved_team = team_ref if isinstance(team_ref, str) and team_ref else f"candidate:{candidate_ref}"
            prior_team = team_by_candidate.get(candidate_ref)
            if prior_team is not None and prior_team != resolved_team:
                raise CommandRejectedError("promotion_exam_candidate_team_conflict")
            team_by_candidate[candidate_ref] = resolved_team
            if isinstance(instructor_ref, str) and instructor_ref:
                prior_instructor = instructor_by_candidate.get(candidate_ref)
                if prior_instructor is not None and prior_instructor != instructor_ref:
                    raise CommandRejectedError("promotion_exam_candidate_team_conflict")
                instructor_by_candidate[candidate_ref] = instructor_ref
    return team_by_candidate, instructor_by_candidate


def _seeded(cycle_id: str, candidates: tuple[str, ...] | list[str]) -> list[str]:
    return sorted(
        candidates,
        key=lambda ref: hashlib.sha256(f"{cycle_id}|{ref}".encode("utf-8")).hexdigest(),
    )


def _cross_team_pairs(
    contenders: list[str], team_by_candidate: Mapping[str, str]
) -> tuple[list[tuple[str, str]], list[str]]:
    remaining = list(contenders)
    pairs: list[tuple[str, str]] = []
    byes: list[str] = []
    while remaining:
        first = remaining.pop(0)
        first_team = team_by_candidate.get(first, f"candidate:{first}")
        opponent_index = next(
            (
                index
                for index, candidate in enumerate(remaining)
                if team_by_candidate.get(candidate, f"candidate:{candidate}") != first_team
            ),
            None,
        )
        if opponent_index is None:
            byes.append(first)
            byes.extend(remaining)
            break
        opponent = remaining.pop(opponent_index)
        pairs.append((first, opponent))
    return pairs, byes


def team_safe_finals_state(
    pipeline: Mapping[str, Any],
    profile: Mapping[str, Any],
    cycle_id: str,
) -> Mapping[str, Any]:
    config = profile.get("finals_format")
    if not isinstance(config, Mapping) or config.get("model") != "single_elimination":
        raise CommandRejectedError("promotion_exam_finals_rules_invalid")
    entrants = _seeded(
        cycle_id, finals.promotion_exam_finals_candidate_refs(pipeline, cycle_id)
    )
    team_by_candidate, _instructor_by_candidate = _registration_team_map(pipeline, cycle_id)
    for candidate_ref in entrants:
        team_by_candidate.setdefault(candidate_ref, f"candidate:{candidate_ref}")
    settled = {
        row["bout_ref"]: row
        for row in finals.promotion_exam_bout_rows(pipeline, cycle_id)
    }
    if len(entrants) <= 1:
        return {
            "candidate_refs": entrants,
            "open_bouts": [],
            "settled_bouts": list(settled.values()),
            "complete": True,
            "champion_ref": entrants[0] if entrants else None,
            "co_finalist_refs": [],
        }

    contenders = entrants
    round_index = 1
    while len(contenders) > 1:
        pairs, byes = _cross_team_pairs(contenders, team_by_candidate)
        if not pairs:
            return {
                "candidate_refs": entrants,
                "open_bouts": [],
                "settled_bouts": list(settled.values()),
                "complete": True,
                "champion_ref": None,
                "co_finalist_refs": list(contenders),
            }
        next_round: list[str] = list(byes)
        open_bouts: list[dict[str, Any]] = []
        for match_index, pair in enumerate(pairs):
            bout_ref = finals._bout_ref(cycle_id, round_index, match_index)
            row = settled.get(bout_ref)
            expected_pair = list(pair)
            if row is None:
                open_bouts.append(
                    {
                        "bout_ref": bout_ref,
                        "round_index": round_index,
                        "match_index": match_index,
                        "candidate_refs": expected_pair,
                    }
                )
                continue
            if list(row.get("candidate_refs", ())) != expected_pair:
                raise CommandRejectedError("promotion_exam_bout_bracket_conflict")
            next_round.append(str(row["winner_ref"]))
        if open_bouts:
            return {
                "candidate_refs": entrants,
                "open_bouts": open_bouts,
                "settled_bouts": list(settled.values()),
                "complete": False,
                "champion_ref": None,
                "co_finalist_refs": [],
            }
        contenders = _seeded(cycle_id, next_round)
        round_index += 1
    return {
        "candidate_refs": entrants,
        "open_bouts": [],
        "settled_bouts": list(settled.values()),
        "complete": True,
        "champion_ref": contenders[0] if contenders else None,
        "co_finalist_refs": [],
    }


def team_safe_finals_complete(
    pipeline: Mapping[str, Any], profile: Mapping[str, Any], cycle_id: str
) -> bool:
    return bool(team_safe_finals_state(pipeline, profile, cycle_id)["complete"])


def _registration_authority_refs(profile: Mapping[str, Any]) -> set[str]:
    configured = profile.get("registration_team_authority_refs")
    if configured is None:
        configured = [profile.get("institution_ref")]
    if (
        not isinstance(configured, list)
        or any(not isinstance(value, str) or not value for value in configured)
    ):
        raise CommandRejectedError("promotion_exam_rules_invalid")
    return set(configured)


def eligible_npc_team_registrations(
    self: Any,
    *,
    profile: Mapping[str, Any],
    pipeline: Mapping[str, Any],
    cycle_id: str,
    player_id: str,
) -> list[dict[str, Any]]:
    try:
        registry = self.repository.read_json("state/team/registry.json")
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("promotion_exam_team_registry_invalid") from exc
    active_teams = registry.get("active_teams") if isinstance(registry, Mapping) else None
    if not isinstance(active_teams, list) or any(not isinstance(ref, str) for ref in active_teams):
        raise CommandRejectedError("promotion_exam_team_registry_invalid")
    registered = set(scheduler.registered_candidate_refs(pipeline, cycle_id))
    institution_ref = profile.get("institution_ref")
    allowed_authorities = _registration_authority_refs(profile)
    cache = _OwnerResolutionCache()
    result: list[dict[str, Any]] = []
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
            or team.get("assignment_authority_ref") not in allowed_authorities
            or not isinstance(leader_ref, str)
            or not leader_ref
            or leader_ref == player_id
            or not isinstance(members, list)
            or any(not isinstance(ref, str) or not ref for ref in members)
        ):
            continue
        candidates: list[str] = []
        for member_ref in members:
            if member_ref == leader_ref or member_ref in registered:
                continue
            try:
                _path, _digest, person = self._resolve_covered_owner_view(
                    member_ref, cache=cache
                )
            except CommandRejectedError:
                continue
            if isinstance(person, Mapping) and _person_matches_profile(person, profile):
                candidates.append(member_ref)
        if not candidates:
            continue
        result.append(
            {
                "team_ref": team_ref,
                "instructor_ref": leader_ref,
                "candidate_refs": sorted(set(candidates)),
            }
        )
        registered.update(candidates)
    return result


def _append_npc_registrations(
    pipeline: dict[str, Any],
    *,
    profile: Mapping[str, Any],
    cycle_id: str,
    at: CampaignTime,
    registrations: list[dict[str, Any]],
    repair: bool = False,
) -> list[str]:
    history = pipeline.get("history")
    if not isinstance(history, list):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    added: list[str] = []
    for row in registrations:
        candidates = list(row["candidate_refs"])
        history.append(
            {
                "kind": "promotion_exam_registration",
                "at": str(at),
                "cycle_id": cycle_id,
                "profile_ref": profile["id"],
                "team_ref": row["team_ref"],
                "instructor_ref": row["instructor_ref"],
                "candidate_refs": candidates,
                "canon_status": scheduler._CANON_STATUS,
                "registration_mode": (
                    "ooc_dev_current_cycle_reconciliation"
                    if repair
                    else "institution_autonomous_team_submission"
                ),
            }
        )
        added.extend(candidates)
    if len(history) > scheduler._CURSOR:
        del history[:-scheduler._CURSOR]
    return added


def _append_npc_evaluations(
    self: Any,
    *,
    pipeline: dict[str, Any],
    profile: Mapping[str, Any],
    cycle_id: str,
    phase: str,
    at: CampaignTime,
    player_id: str,
    only_candidates: set[str] | None = None,
    repair: bool = False,
) -> list[dict[str, Any]]:
    config = evaluation._evaluation_config(profile, phase)
    entrants = evaluation.promotion_exam_stage_candidate_refs(
        pipeline, profile, cycle_id, phase
    )
    existing = {
        row["candidate_ref"]
        for row in evaluation.promotion_exam_evaluation_rows(
            pipeline, cycle_id, phase=phase
        )
    }
    team_by_candidate, instructor_by_candidate = _registration_team_map(
        pipeline, cycle_id
    )
    cache = _OwnerResolutionCache()
    results: list[dict[str, Any]] = []
    for candidate_ref in entrants:
        if candidate_ref in existing:
            continue
        if only_candidates is not None and candidate_ref not in only_candidates:
            continue
        if instructor_by_candidate.get(candidate_ref) == player_id:
            continue
        try:
            _path, _digest, person = self._resolve_covered_owner_view(
                candidate_ref, cache=cache
            )
        except CommandRejectedError:
            continue
        if not isinstance(person, Mapping) or not _living(person):
            continue
        condition = person.get("condition")
        if isinstance(condition, Mapping) and condition.get("readiness") not in (None, "ready"):
            continue
        score, threshold, outcome = evaluation._score_candidate(person, config)
        results.append(
            {
                "candidate_ref": candidate_ref,
                "team_ref": team_by_candidate.get(candidate_ref, f"candidate:{candidate_ref}"),
                "score": score,
                "threshold": threshold,
                "outcome": outcome,
            }
        )
    if not results:
        return []
    history = pipeline.get("history")
    if not isinstance(history, list):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    evaluator_ref = profile.get("authority_ref")
    if not isinstance(evaluator_ref, str) or not evaluator_ref:
        raise CommandRejectedError("promotion_exam_rules_invalid")
    for result in results:
        history.append(
            {
                "kind": "promotion_exam_evaluation",
                "at": str(at),
                "cycle_id": cycle_id,
                "profile_ref": profile["id"],
                "phase": phase,
                "team_ref": result["team_ref"],
                "evaluator_ref": evaluator_ref,
                "candidate_ref": result["candidate_ref"],
                "score": result["score"],
                "threshold": result["threshold"],
                "outcome": result["outcome"],
                "canon_status": scheduler._CANON_STATUS,
                "evaluation_mode": (
                    "ooc_dev_current_cycle_reconciliation"
                    if repair
                    else "institution_autonomous_exact_candidate"
                ),
            }
        )
    if len(history) > scheduler._CURSOR:
        del history[:-scheduler._CURSOR]
    return results


class _BaseOverlay:
    def __init__(self, overlay: Any, base_writes: Mapping[str, bytes]) -> None:
        self._overlay = overlay
        self._base_writes = dict(base_writes)
        self.changed_paths = tuple(sorted(base_writes))

    def read_json(self, path: str) -> Any:
        raw = self._base_writes.get(path)
        if raw is not None:
            return json.loads(raw.decode("utf-8"))
        return self._overlay.read_json(path)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._overlay, name)


def _pipeline_after(repository: Any, writes: Mapping[str, bytes]) -> dict[str, Any]:
    raw = writes.get(_CAREER)
    try:
        value = (
            json.loads(raw.decode("utf-8"))
            if isinstance(raw, (bytes, bytearray))
            else copy.deepcopy(repository.read_json(_CAREER))
        )
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CommandRejectedError("shinobi_career_pipeline_invalid") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema") != "shinobi-career-pipeline"
        or value.get("version") != 1
        or not isinstance(value.get("history"), list)
    ):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    return value


def _install_life_status_compatibility() -> None:
    scheduler._person_matches_profile = _person_matches_profile
    try:
        from shinobi_runtime.api import player_promotion_exam_projection as projection

        projection._person_matches_profile = _person_matches_profile
    except ImportError:
        pass


class _LifeStatusPlannerProxy:
    def __init__(self, planner: Any) -> None:
        self._planner = planner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._planner, name)

    def _resolve_covered_owner_view(self, *args: Any, **kwargs: Any) -> Any:
        path, digest, person = self._planner._resolve_covered_owner_view(*args, **kwargs)
        if isinstance(person, Mapping) and person.get("life_status") == "active":
            normalized = copy.deepcopy(dict(person))
            normalized["life_status"] = "alive"
            return path, digest, normalized
        return path, digest, person


def _install_finals_bracket() -> None:
    finals.promotion_exam_finals_state = team_safe_finals_state
    finals.promotion_exam_finals_complete = team_safe_finals_complete
    try:
        from shinobi_runtime.api import player_promotion_exam_projection as projection

        projection.promotion_exam_finals_state = team_safe_finals_state
    except ImportError:
        pass

    from shinobi_runtime.commands import campaign_player_handoffs as planner_module

    planner = planner_module.CampaignCommandPlanner
    original = getattr(planner, "_promotion_exam_bout_resolution", None)
    if original is not None and not getattr(original, "_promotion_exam_life_status_compat", False):
        @wraps(original)
        def wrapped(self: Any, command: Any, meta: Mapping[str, Any], current_time: CampaignTime) -> Any:
            return original(_LifeStatusPlannerProxy(self), command, meta, current_time)

        wrapped._promotion_exam_life_status_compat = True  # type: ignore[attr-defined]
        setattr(planner, "_promotion_exam_bout_resolution", wrapped)


def _install_npc_registration() -> None:
    original = AutonomyCommandsMixin._apply_institution_autonomy_review
    if getattr(original, "_promotion_exam_npc_registration", False):
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
            (
                row
                for row in scheduler.promotion_exam_profiles(self.repository)
                if row.get("id") == profile_ref
            ),
            None,
        )
        if not isinstance(profile, Mapping):
            raise CommandRejectedError("promotion_exam_cycle_state_invalid")
        pipeline = scheduler._pipeline(self.repository, record_writes)
        registrations = eligible_npc_team_registrations(
            self,
            profile=profile,
            pipeline=pipeline,
            cycle_id=cycle_id,
            player_id=command.actor_id,
        )
        if not registrations:
            return base
        added = _append_npc_registrations(
            pipeline,
            profile=profile,
            cycle_id=cycle_id,
            at=at,
            registrations=registrations,
        )
        record_writes[_CAREER] = pipeline
        event_id = self._append_internal_event(
            world_events,
            command=command,
            identity=f"{cycle_id}:npc-team-registration",
            kind="promotion_exam_npc_teams_registered",
            at=at,
            host_refs=(str(profile["institution_ref"]),),
            affected_owner_refs=(_CAREER,),
            material_consequence_refs=tuple(
                f"promotion_exam_registration:{cycle_id}:{ref}" for ref in added
            ),
            classification="public",
            audience_refs=(command.actor_id,),
            source_refs=(str(profile["institution_ref"]),),
            reducer_ref="shinobi_runtime.commands.promotion_exam_integrity",
        )
        enriched = dict(base)
        enriched["promotion_exam_autonomous_registration"] = {
            "cycle_id": cycle_id,
            "candidate_count": len(added),
            "team_count": len(registrations),
            "event_id": event_id,
        }
        return enriched

    wrapped._promotion_exam_npc_registration = True  # type: ignore[attr-defined]
    AutonomyCommandsMixin._apply_institution_autonomy_review = wrapped


def _install_npc_evaluation() -> None:
    original = TimeCommandsMixin._advance_time
    if getattr(original, "_promotion_exam_npc_evaluation", False):
        return

    @wraps(original)
    def wrapped(
        self: Any,
        command: Any,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        base = original(self, command, meta, current_time)
        reached_raw = base.result.get("world_time") if isinstance(base.result, Mapping) else None
        if not isinstance(reached_raw, str):
            return base
        try:
            reached = CampaignTime.parse(reached_raw)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("promotion_exam_cycle_state_invalid") from exc
        pipeline = _pipeline_after(self.repository, base.writes)
        profiles = scheduler.promotion_exam_profiles(self.repository)
        profile_by_id = {
            row.get("id"): row
            for row in profiles
            if isinstance(row.get("id"), str)
        }
        evaluation_summaries: list[dict[str, Any]] = []
        for cycle in scheduler.active_promotion_exam_cycles(pipeline, profiles):
            phase = cycle.get("phase")
            cycle_id = cycle.get("cycle_id")
            profile = profile_by_id.get(cycle.get("profile_ref"))
            if (
                phase not in ("qualification", "field_evaluation")
                or not isinstance(cycle_id, str)
                or not isinstance(profile, Mapping)
            ):
                continue
            results = _append_npc_evaluations(
                self,
                pipeline=pipeline,
                profile=profile,
                cycle_id=cycle_id,
                phase=str(phase),
                at=reached,
                player_id=command.actor_id,
            )
            if results:
                evaluation_summaries.append(
                    {
                        "cycle_id": cycle_id,
                        "phase": phase,
                        "candidate_count": len(results),
                    }
                )
        if not evaluation_summaries:
            return base

        world_events = self._world_events_after(base)
        event_ids: list[str] = []
        for summary in evaluation_summaries:
            event_ids.append(
                self._append_internal_event(
                    world_events,
                    command=command,
                    identity=f"{summary['cycle_id']}:{summary['phase']}:npc-evaluation:{reached}",
                    kind="promotion_exam_npc_stage_evaluated",
                    at=reached,
                    host_refs=(str(summary["cycle_id"]),),
                    affected_owner_refs=(_CAREER,),
                    material_consequence_refs=(
                        f"npc_evaluation_count:{summary['candidate_count']}",
                    ),
                    classification="restricted",
                    audience_refs=(command.actor_id,),
                    source_refs=(str(summary["cycle_id"]),),
                    reducer_ref="shinobi_runtime.commands.promotion_exam_integrity",
                )
            )
        writes = dict(base.writes)
        writes[_CAREER] = _json_bytes(pipeline)
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))
        base_writes = dict(base.writes)
        original_validator = base.validator
        expected_pipeline = copy.deepcopy(pipeline)

        def validate(overlay: Any, manifest: Any) -> None:
            if original_validator is not None:
                original_validator(_BaseOverlay(overlay, base_writes), manifest)
            if overlay.changed_paths != expected_paths:
                raise ValueError("promotion exam NPC evaluation write set changed after planning")
            if overlay.read_json(_CAREER) != expected_pipeline:
                raise ValueError("promotion exam NPC evaluation after-image differs from plan")

        result = dict(base.result)
        result["promotion_exam_autonomous_evaluation"] = evaluation_summaries
        result["promotion_exam_autonomous_evaluation_event_ids"] = event_ids
        return _BuiltPlan(
            code=base.code,
            affected_refs=expected_paths,
            writes=writes,
            result=result,
            validator=validate,
        )

    wrapped._promotion_exam_npc_evaluation = True  # type: ignore[attr-defined]
    TimeCommandsMixin._advance_time = wrapped


def install_promotion_exam_integrity() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_life_status_compatibility()
    _install_finals_bracket()
    _install_npc_registration()
    _install_npc_evaluation()
    _INSTALLED = True


__all__ = [
    "eligible_npc_team_registrations",
    "install_promotion_exam_integrity",
    "team_safe_finals_complete",
    "team_safe_finals_state",
]
