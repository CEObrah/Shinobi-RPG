from __future__ import annotations

import copy
from functools import wraps
from typing import Any, Dict, Mapping, Optional

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.domains.autonomy import AutonomyCommandsMixin
from shinobi_runtime.commands.promotion_exam_cycle import _install_career_guard
from shinobi_runtime.sim.events import CampaignTime

_CAREER = "state/reg/shinobi-career-pipeline.json"
_RULES = "game/rules/career/promotion-exams.json"
_CURSOR = 512
_CANON_STATUS = "campaign_institutional_not_future_canon"
_INSTALLED = False


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


def _validate_profile(key: str, profile: Mapping[str, Any]) -> None:
    required_strings = (
        "id",
        "institution_ref",
        "authority_ref",
        "service_village",
        "source_rank",
        "target_rank",
        "canon_status",
    )
    if profile.get("id") != key or any(
        not isinstance(profile.get(field), str) or not profile.get(field)
        for field in required_strings
    ):
        raise CommandRejectedError("promotion_exam_rules_invalid")
    if profile.get("canon_status") != _CANON_STATUS:
        raise CommandRejectedError("promotion_exam_rules_invalid")
    phases = profile.get("phases")
    months = profile.get("cycle_start_months")
    if (
        not isinstance(phases, list)
        or not phases
        or any(not isinstance(value, str) or not value for value in phases)
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


def _profiles(repository: Any) -> tuple[Mapping[str, Any], ...]:
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


def _active(history: list[Any], profile_id: str) -> Optional[Mapping[str, Any]]:
    rows = [
        row
        for row in history
        if isinstance(row, Mapping)
        and row.get("kind") == "promotion_exam_cycle_phase"
        and row.get("profile_ref") == profile_id
        and isinstance(row.get("cycle_id"), str)
    ]
    if not rows:
        return None
    return None if rows[-1].get("phase") == "closed" else rows[-1]


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
        for profile in _profiles(self.repository):
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
            sources = [institution_id, authority]
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
                source_refs=tuple(sources),
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


def install_promotion_exam_scheduler() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_career_guard()
    _install_scheduler()
    _INSTALLED = True


__all__ = ["install_promotion_exam_scheduler", "next_cycle_phase"]
