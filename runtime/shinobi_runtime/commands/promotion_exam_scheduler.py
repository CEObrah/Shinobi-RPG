from __future__ import annotations

import copy
from functools import wraps
from typing import Any, Dict, Mapping, Optional, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import (
    _BuiltPlan,
    _OwnerResolutionCache,
    _exact_payload,
    _json_bytes,
    _stable_id,
)
from shinobi_runtime.commands.domains.autonomy import AutonomyCommandsMixin
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.promotion_exam_cycle import _install_career_guard
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_CAREER = "state/reg/shinobi-career-pipeline.json"
_RULES = "game/rules/career/promotion-exams.json"
_CURSOR = 512
_CANON_STATUS = "campaign_institutional_not_future_canon"
_REGISTRATION_AUTHORITY = "active_team_leader"
_INSTALLED = False
_PROJECTION_INSTALLED = False


def _pipeline(
    repository: Any,
    writes: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    value = writes.get(_CAREER)
    if isinstance(value, Mapping):
        result = copy.deepcopy(dict(value))
    else:
        try:
            result = copy.deepcopy(repository.read_json(_CAREER))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("shinobi_career_pipeline_invalid") from exc
    if (
        not isinstance(result, dict)
        or result.get("schema") != "shinobi-career-pipeline"
        or result.get("version") != 1
        or not isinstance(result.get("history"), list)
    ):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    return result


def _load_pipeline(repository: Any) -> Dict[str, Any]:
    try:
        loaded = repository.read_json(_CAREER)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("shinobi_career_pipeline_invalid") from exc
    if (
        not isinstance(loaded, Mapping)
        or loaded.get("schema") != "shinobi-career-pipeline"
        or loaded.get("version") != 1
        or not isinstance(loaded.get("history"), list)
    ):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    return copy.deepcopy(dict(loaded))


def _validate_profile(key: str, profile: Mapping[str, Any]) -> None:
    required_strings = (
        "id",
        "institution_ref",
        "authority_ref",
        "service_village",
        "source_rank",
        "target_rank",
        "canon_status",
        "registration_authority",
    )
    if profile.get("id") != key or any(
        not isinstance(profile.get(field), str) or not profile.get(field)
        for field in required_strings
    ):
        raise CommandRejectedError("promotion_exam_rules_invalid")
    if (
        profile.get("canon_status") != _CANON_STATUS
        or profile.get("registration_authority") != _REGISTRATION_AUTHORITY
    ):
        raise CommandRejectedError("promotion_exam_rules_invalid")
    phases = profile.get("phases")
    months = profile.get("cycle_start_months")
    if (
        not isinstance(phases, list)
        or not phases
        or phases[0] != "registration"
        or any(not isinstance(value, str) or not value for value in phases)
        or len(set(phases)) != len(phases)
        or not isinstance(months, list)
        or not months
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 12
            for value in months
        )
    ):
        raise CommandRejectedError("promotion_exam_rules_invalid")


def promotion_exam_profiles(repository: Any) -> tuple[Mapping[str, Any], ...]:
    try:
        rules = repository.read_json(_RULES)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("promotion_exam_rules_invalid") from exc
    profiles = rules.get("profiles") if isinstance(rules, Mapping) else None
    if (
        not isinstance(rules, Mapping)
        or rules.get("schema") != "promotion-exam-rules"
        or rules.get("version") != 2
        or not isinstance(profiles, Mapping)
    ):
        raise CommandRejectedError("promotion_exam_rules_invalid")
    selected: list[Mapping[str, Any]] = []
    for key, profile in sorted(profiles.items()):
        if not isinstance(key, str) or not isinstance(profile, Mapping):
            raise CommandRejectedError("promotion_exam_rules_invalid")
        if profile.get("enabled") is not True:
            continue
        _validate_profile(key, profile)
        selected.append(profile)
    return tuple(selected)


def _profiles(repository: Any) -> tuple[Mapping[str, Any], ...]:
    return promotion_exam_profiles(repository)


def _phase_rows(history: Sequence[Any], profile_id: str) -> list[Mapping[str, Any]]:
    return [
        row
        for row in history
        if isinstance(row, Mapping)
        and row.get("kind") == "promotion_exam_cycle_phase"
        and row.get("profile_ref") == profile_id
        and isinstance(row.get("cycle_id"), str)
        and isinstance(row.get("phase"), str)
    ]


def _active(history: list[Any], profile_id: str) -> Optional[Mapping[str, Any]]:
    rows = _phase_rows(history, profile_id)
    if not rows:
        return None
    return None if rows[-1].get("phase") == "closed" else rows[-1]


def active_promotion_exam_cycles(
    pipeline: Mapping[str, Any],
    profiles: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    history = pipeline.get("history")
    if not isinstance(history, list):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    active: list[Mapping[str, Any]] = []
    for profile in profiles:
        profile_id = profile.get("id")
        if not isinstance(profile_id, str):
            raise CommandRejectedError("promotion_exam_rules_invalid")
        row = _active(history, profile_id)
        if row is not None:
            active.append(row)
    return tuple(active)


def registered_candidate_refs(
    pipeline: Mapping[str, Any],
    cycle_id: str,
) -> tuple[str, ...]:
    history = pipeline.get("history")
    if not isinstance(history, list):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    registered: set[str] = set()
    for row in history:
        if (
            isinstance(row, Mapping)
            and row.get("kind") == "promotion_exam_registration"
            and row.get("cycle_id") == cycle_id
        ):
            refs = row.get("candidate_refs")
            if not isinstance(refs, list) or any(
                not isinstance(ref, str) or not ref for ref in refs
            ):
                raise CommandRejectedError("shinobi_career_pipeline_invalid")
            registered.update(refs)
    return tuple(sorted(registered))


def next_cycle_phase(
    profile: Mapping[str, Any],
    pipeline: Mapping[str, Any],
    at: CampaignTime,
) -> Optional[tuple[str, str]]:
    phases = profile.get("phases")
    months = profile.get("cycle_start_months")
    profile_id = profile.get("id")
    history = pipeline.get("history")
    if (
        not isinstance(phases, list)
        or not phases
        or any(not isinstance(value, str) or not value for value in phases)
        or not isinstance(months, list)
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 12
            for value in months
        )
    ):
        raise CommandRejectedError("promotion_exam_rules_invalid")
    if not isinstance(profile_id, str) or not profile_id or not isinstance(history, list):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    current = _active(history, profile_id)
    if current is None:
        if at.month not in months:
            return None
        cycle = f"promotion_exam_cycle.{profile_id}.{at.year:04d}-{at.month:02d}"
        if any(
            isinstance(row, Mapping) and row.get("cycle_id") == cycle
            for row in history
        ):
            return None
        return cycle, phases[0]
    phase = current.get("phase")
    if phase not in phases:
        raise CommandRejectedError("promotion_exam_cycle_state_invalid")
    index = phases.index(phase)
    return None if index + 1 >= len(phases) else (str(current["cycle_id"]), phases[index + 1])


def _rank_key(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip().lower().replace("ū", "u").replace("ō", "o")
    return value if value in {"genin", "chunin", "jonin"} else None


def _person_matches_profile(person: Mapping[str, Any], profile: Mapping[str, Any]) -> bool:
    if person.get("schema") != "shinobi_character" or person.get("life_status") != "alive":
        return False
    source_rank = _rank_key(profile.get("source_rank"))
    if source_rank is None or _rank_key(person.get("official_rank_or_status")) != source_rank:
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


def _candidate_refs(value: object, *, actor_id: str) -> tuple[str, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or not 1 <= len(value) <= 16
        or any(not isinstance(ref, str) or not ref for ref in value)
    ):
        raise CommandRejectedError("promotion_exam_candidate_refs_invalid")
    candidates = tuple(sorted(set(value)))
    if len(candidates) != len(value) or actor_id in candidates:
        raise CommandRejectedError("promotion_exam_candidate_refs_invalid")
    return candidates


def _profile_for_cycle(
    profiles: Sequence[Mapping[str, Any]],
    phase_row: Mapping[str, Any],
) -> Mapping[str, Any]:
    profile_ref = phase_row.get("profile_ref")
    profile = next((row for row in profiles if row.get("id") == profile_ref), None)
    if not isinstance(profile, Mapping):
        raise CommandRejectedError("promotion_exam_cycle_state_invalid")
    return profile


def _plan_promotion_exam_registration_resolution(
    self: Any,
    command: CommandEnvelope,
    meta: Mapping[str, Any],
    current_time: CampaignTime,
) -> _BuiltPlan:
    _exact_payload(command.payload, ("cycle_id", "team_ref", "candidate_refs"), command.command_type)
    cycle_id = _stable_id(
        command.payload.get("cycle_id"),
        "promotion_exam_cycle_ref_invalid",
        prefix="promotion_exam_cycle.",
    )
    team_ref = _stable_id(
        command.payload.get("team_ref"),
        "promotion_exam_team_ref_invalid",
        prefix="team.",
    )
    candidates = _candidate_refs(
        command.payload.get("candidate_refs"),
        actor_id=command.actor_id,
    )

    pipeline = _load_pipeline(self.repository)
    profiles = promotion_exam_profiles(self.repository)
    phase_row = next(
        (
            row
            for row in active_promotion_exam_cycles(pipeline, profiles)
            if row.get("cycle_id") == cycle_id
        ),
        None,
    )
    if not isinstance(phase_row, Mapping):
        raise CommandRejectedError("promotion_exam_cycle_not_active")
    if phase_row.get("phase") != "registration":
        raise CommandRejectedError("promotion_exam_registration_closed")
    profile = _profile_for_cycle(profiles, phase_row)

    try:
        _team_path, team = self._exact_team(team_ref)
    except CommandRejectedError as exc:
        raise CommandRejectedError("promotion_exam_team_invalid") from exc
    members = team.get("member_refs") if isinstance(team, Mapping) else None
    if (
        team.get("status") != "active"
        or team.get("leader_ref") != command.actor_id
        or team.get("assignment_authority_ref") != profile.get("institution_ref")
        or not isinstance(members, list)
        or any(not isinstance(ref, str) or not ref for ref in members)
    ):
        raise CommandRejectedError("promotion_exam_registration_authority_required")
    member_set = set(members)
    if any(ref not in member_set for ref in candidates):
        raise CommandRejectedError("promotion_exam_candidate_not_team_member")

    registered = set(registered_candidate_refs(pipeline, cycle_id))
    if any(ref in registered for ref in candidates):
        raise CommandRejectedError("promotion_exam_candidate_already_registered")

    cache = _OwnerResolutionCache()
    for candidate_ref in candidates:
        try:
            _path, _digest, person = self._resolve_covered_owner_view(
                candidate_ref,
                cache=cache,
            )
        except CommandRejectedError as exc:
            raise CommandRejectedError("promotion_exam_candidate_unresolved") from exc
        if not isinstance(person, Mapping) or not _person_matches_profile(person, profile):
            raise CommandRejectedError("promotion_exam_candidate_ineligible")

    history = pipeline["history"]
    history.append(
        {
            "kind": "promotion_exam_registration",
            "at": str(current_time),
            "cycle_id": cycle_id,
            "profile_ref": profile["id"],
            "team_ref": team_ref,
            "instructor_ref": command.actor_id,
            "candidate_refs": list(candidates),
            "canon_status": _CANON_STATUS,
        }
    )
    if len(history) > _CURSOR:
        del history[:-_CURSOR]

    world_events = self._world_events()
    institution_ref = str(profile["institution_ref"])
    event_id = self._append_semantic_event(
        world_events,
        command=command,
        kind="promotion_exam_candidates_registered",
        at=current_time,
        host_refs=(institution_ref, team_ref),
        actor_refs=(command.actor_id,),
        affected_owner_refs=(_CAREER,),
        material_consequence_refs=tuple(
            f"promotion_exam_registration:{cycle_id}:{ref}" for ref in candidates
        ),
        classification="restricted",
        audience_refs=tuple(dict.fromkeys((command.actor_id, *candidates))),
        source_refs=(command.actor_id, institution_ref),
        reducer_ref="shinobi_runtime.commands.promotion_exam_scheduler.promotion_exam_registration_resolution",
    )
    writes = {
        self.meta_path: _json_bytes(
            self._meta_after(meta, command, world_time=current_time)
        ),
        _CAREER: _json_bytes(pipeline),
        **self._world_event_writes(world_events),
    }
    writes = self._prune_noop_writes(writes)
    expected_paths = tuple(sorted(writes))
    expected_pipeline = copy.deepcopy(pipeline)

    def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
        if overlay.changed_paths != expected_paths:
            raise ValueError("promotion exam registration write set changed after planning")
        self._assert_meta(
            overlay,
            manifest,
            meta_path=self.meta_path,
            command=command,
            world_time=current_time,
        )
        if overlay.read_json(_CAREER) != expected_pipeline:
            raise ValueError("promotion exam registration after-image differs from plan")
        if not any(
            path == "state/reg/world-events.json"
            or path.startswith("state/history/events/")
            for path in expected_paths
        ):
            raise ValueError("promotion exam registration event did not persist")

    return _BuiltPlan(
        code="promotion_exam_registration_resolution_ready",
        affected_refs=expected_paths,
        writes=writes,
        result={
            "command_type": command.command_type,
            "cycle_id": cycle_id,
            "profile_ref": profile["id"],
            "institution_ref": institution_ref,
            "team_ref": team_ref,
            "registered_candidate_refs": list(candidates),
            "semantic_event_id": event_id,
            "status": "registered",
        },
        validator=validate,
    )


def _install_scheduler() -> None:
    original = AutonomyCommandsMixin._apply_institution_autonomy_review
    if getattr(original, "_promotion_exam_scheduler_native", False):
        return

    @wraps(original)
    def wrapped(
        self: Any,
        *,
        institution: Dict[str, Any],
        at: CampaignTime,
        compacted: int,
        command: Any,
        policy_book: Any,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
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
        institution_id = institution.get("id")
        if not isinstance(institution_id, str):
            return base
        for profile in promotion_exam_profiles(self.repository):
            if profile.get("institution_ref") != institution_id:
                continue
            pipeline = _pipeline(self.repository, record_writes)
            state = next_cycle_phase(profile, pipeline, at)
            if state is None:
                continue
            cycle, phase = state
            profile_id = profile["id"]
            authority = profile["authority_ref"]
            history = pipeline["history"]
            history.append(
                {
                    "kind": "promotion_exam_cycle_phase",
                    "at": str(at),
                    "cycle_id": cycle,
                    "profile_ref": profile_id,
                    "phase": phase,
                    "canon_status": _CANON_STATUS,
                    "authority_ref": authority,
                }
            )
            if len(history) > _CURSOR:
                del history[:-_CURSOR]
            record_writes[_CAREER] = pipeline
            event_id = self._append_internal_event(
                world_events,
                command=command,
                identity=f"{cycle}:{phase}",
                kind="promotion_exam_cycle_phase_changed",
                at=at,
                host_refs=(institution_id,),
                affected_owner_refs=(_CAREER,),
                material_consequence_refs=(cycle, f"phase:{phase}"),
                classification="public",
                audience_refs=(command.actor_id,),
                source_refs=(institution_id, authority),
                reducer_ref="shinobi_runtime.commands.promotion_exam_scheduler",
            )
            result = dict(base)
            result["promotion_exam_cycle"] = {
                "cycle_id": cycle,
                "profile_ref": profile_id,
                "phase": phase,
                "at": str(at),
                "institution_ref": institution_id,
                "event_id": event_id,
                "public_institutional_event": True,
            }
            return result
        return base

    wrapped._promotion_exam_scheduler_native = True  # type: ignore[attr-defined]
    AutonomyCommandsMixin._apply_institution_autonomy_review = wrapped


def install_promotion_exam_projection() -> None:
    global _PROJECTION_INSTALLED
    if _PROJECTION_INSTALLED:
        return
    from shinobi_runtime.commands import campaign_runtime_planner as module

    original = module._fresh_player_facing_time_handoff
    if getattr(original, "_promotion_exam_scheduler_projection", False):
        _PROJECTION_INSTALLED = True
        return

    @wraps(original)
    def wrapped(result: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        pressures, reports, approaching = original(result)
        actions = result.get("autonomous_actions")
        if isinstance(actions, list):
            for action in actions:
                cycle = action.get("promotion_exam_cycle") if isinstance(action, Mapping) else None
                if not isinstance(cycle, Mapping) or cycle.get("public_institutional_event") is not True:
                    continue
                phase = cycle.get("phase")
                if phase == "registration":
                    pressure = "Konoha has opened registration for the current Chunin Examination cycle."
                    report = "The Academy has opened Chunin Examination registration."
                else:
                    pressure = f"Konoha's current Chunin Examination has entered {phase}." if isinstance(phase, str) and phase else None
                    report = None
                if pressure and pressure not in pressures:
                    pressures.append(pressure)
                if report and report not in reports:
                    reports.append(report)
        return pressures[:12], reports[:6], approaching[:8]

    wrapped._promotion_exam_scheduler_projection = True  # type: ignore[attr-defined]
    module._fresh_player_facing_time_handoff = wrapped
    _PROJECTION_INSTALLED = True


def _install_registration_command() -> None:
    from shinobi_runtime.commands import campaign_player_handoffs as module

    COMMAND_SPECS.setdefault(
        "promotion_exam_registration_resolution",
        CommandSpec(
            ("cycle_id", "team_ref", "candidate_refs"),
            (),
            "Register eligible exact members of a player-led team for an active institutional promotion examination cycle.",
            {
                "cycle_id": "promotion_exam_cycle.<id>",
                "team_ref": "team.<id>",
                "candidate_refs": ["char.<id>"],
            },
        ),
    )
    planner = module.CampaignCommandPlanner
    setattr(
        planner,
        "_promotion_exam_registration_resolution",
        _plan_promotion_exam_registration_resolution,
    )
    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)


def install_promotion_exam_scheduler() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_career_guard()
    _install_scheduler()
    _install_registration_command()
    _INSTALLED = True


__all__ = [
    "active_promotion_exam_cycles",
    "install_promotion_exam_projection",
    "install_promotion_exam_scheduler",
    "next_cycle_phase",
    "promotion_exam_profiles",
    "registered_candidate_refs",
]
