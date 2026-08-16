"""Deterministic exact-candidate settlement for promotion-exam stages.

The career pipeline remains the durable examination-administration owner. This
module settles Academy stage evidence from persisted exact candidate capability,
records the result, and gates later scheduled phases until the current evaluated
stage is complete. It never applies injury, promotion, or rank accounting.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import (
    _BuiltPlan,
    _OwnerResolutionCache,
    _exact_payload,
    _json_bytes,
    _stable_id,
)
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

from shinobi_runtime.commands.promotion_exam_scheduler import (
    _CANON_STATUS,
    _CAREER,
    _CURSOR,
    _candidate_refs,
    _load_pipeline,
    _profile_for_cycle,
    active_promotion_exam_cycles,
    promotion_exam_profiles,
    registered_candidate_refs,
)

_INSTALLED = False
_EVALUABLE_PHASES = frozenset(("qualification", "field_evaluation", "finals"))


def promotion_exam_evaluation_rows(
    pipeline: Mapping[str, Any],
    cycle_id: str,
    *,
    phase: str | None = None,
) -> tuple[Mapping[str, Any], ...]:
    history = pipeline.get("history")
    if not isinstance(history, list):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    rows: list[Mapping[str, Any]] = []
    for row in history:
        if (
            isinstance(row, Mapping)
            and row.get("kind") == "promotion_exam_evaluation"
            and row.get("cycle_id") == cycle_id
            and (phase is None or row.get("phase") == phase)
        ):
            candidate_ref = row.get("candidate_ref")
            outcome = row.get("outcome")
            score = row.get("score")
            threshold = row.get("threshold")
            if (
                not isinstance(candidate_ref, str)
                or not candidate_ref
                or outcome not in ("pass", "fail")
                or isinstance(score, bool)
                or not isinstance(score, int)
                or isinstance(threshold, bool)
                or not isinstance(threshold, int)
            ):
                raise CommandRejectedError("shinobi_career_pipeline_invalid")
            rows.append(row)
    return tuple(rows)


def _evaluation_config(profile: Mapping[str, Any], phase: str) -> Mapping[str, Any]:
    stages = profile.get("evaluation_stages")
    config = stages.get(phase) if isinstance(stages, Mapping) else None
    if not isinstance(config, Mapping):
        raise CommandRejectedError("promotion_exam_stage_not_evaluable")
    threshold = config.get("threshold")
    components = config.get("components")
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, int)
        or threshold < 0
        or not isinstance(components, list)
        or not components
    ):
        raise CommandRejectedError("promotion_exam_rules_invalid")
    total_weight = 0
    for component in components:
        path = component.get("path") if isinstance(component, Mapping) else None
        weight = component.get("weight") if isinstance(component, Mapping) else None
        if (
            not isinstance(path, str)
            or not path
            or any(not token for token in path.split("."))
            or isinstance(weight, bool)
            or not isinstance(weight, int)
            or weight <= 0
        ):
            raise CommandRejectedError("promotion_exam_rules_invalid")
        total_weight += weight
    if total_weight <= 0:
        raise CommandRejectedError("promotion_exam_rules_invalid")
    return config


def promotion_exam_stage_candidate_refs(
    pipeline: Mapping[str, Any],
    profile: Mapping[str, Any],
    cycle_id: str,
    phase: str,
) -> tuple[str, ...]:
    """Return entrants for a stage without fabricating missing legacy results.

    If a prior evaluable stage has recorded evidence, only its passers advance.
    If no prior-stage evidence exists at all, registered candidates remain
    entrants. This preserves already-committed cycles that crossed an old phase
    before the evaluator existed, including the revision-165 field boundary.
    """
    registered = set(registered_candidate_refs(pipeline, cycle_id))
    stages = profile.get("evaluation_stages")
    phases = profile.get("phases")
    if not isinstance(stages, Mapping) or phase not in stages or not isinstance(phases, list):
        raise CommandRejectedError("promotion_exam_stage_not_evaluable")
    evaluable = [value for value in phases if value in stages]
    if phase not in evaluable:
        raise CommandRejectedError("promotion_exam_stage_not_evaluable")
    index = evaluable.index(phase)
    if index == 0:
        return tuple(sorted(registered))
    prior_phase = evaluable[index - 1]
    prior_rows = promotion_exam_evaluation_rows(pipeline, cycle_id, phase=prior_phase)
    if not prior_rows:
        return tuple(sorted(registered))
    passed = {
        row["candidate_ref"]
        for row in prior_rows
        if row.get("outcome") == "pass" and row.get("candidate_ref") in registered
    }
    return tuple(sorted(passed))


def promotion_exam_stage_complete(
    pipeline: Mapping[str, Any],
    profile: Mapping[str, Any],
    cycle_id: str,
    phase: str,
) -> bool:
    entrants = set(promotion_exam_stage_candidate_refs(pipeline, profile, cycle_id, phase))
    evaluated = {
        row["candidate_ref"]
        for row in promotion_exam_evaluation_rows(pipeline, cycle_id, phase=phase)
    }
    return entrants.issubset(evaluated)


def _read_metric(person: Mapping[str, Any], path: str) -> int:
    value: Any = person
    for token in path.split("."):
        if not isinstance(value, Mapping) or token not in value:
            raise CommandRejectedError("promotion_exam_candidate_metric_unavailable")
        value = value[token]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CommandRejectedError("promotion_exam_candidate_metric_unavailable")
    return value


def _score_candidate(person: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[int, int, str]:
    threshold = config["threshold"]
    weighted = 0
    total_weight = 0
    for component in config["components"]:
        path = component["path"]
        weight = component["weight"]
        weighted += _read_metric(person, path) * weight
        total_weight += weight
    # Integer half-up rounding keeps the subsystem deterministic and auditable.
    score = (weighted + total_weight // 2) // total_weight
    outcome = "pass" if score >= threshold else "fail"
    return score, threshold, outcome


def _plan_promotion_exam_evaluation_resolution(
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
    candidates = _candidate_refs(command.payload.get("candidate_refs"), actor_id=command.actor_id)

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
    phase = phase_row.get("phase")
    if not isinstance(phase, str) or phase not in _EVALUABLE_PHASES:
        raise CommandRejectedError("promotion_exam_stage_not_evaluable")
    profile = _profile_for_cycle(profiles, phase_row)
    config = _evaluation_config(profile, phase)

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
        raise CommandRejectedError("promotion_exam_evaluation_authority_required")
    member_set = set(members)
    if any(ref not in member_set for ref in candidates):
        raise CommandRejectedError("promotion_exam_candidate_not_team_member")

    entrants = set(promotion_exam_stage_candidate_refs(pipeline, profile, cycle_id, phase))
    if any(ref not in entrants for ref in candidates):
        raise CommandRejectedError("promotion_exam_candidate_not_stage_entrant")
    already = {
        row["candidate_ref"]
        for row in promotion_exam_evaluation_rows(pipeline, cycle_id, phase=phase)
    }
    if any(ref in already for ref in candidates):
        raise CommandRejectedError("promotion_exam_candidate_already_evaluated")

    cache = _OwnerResolutionCache()
    results: list[dict[str, Any]] = []
    for candidate_ref in candidates:
        try:
            _path, _digest, person = self._resolve_covered_owner_view(candidate_ref, cache=cache)
        except CommandRejectedError as exc:
            raise CommandRejectedError("promotion_exam_candidate_unresolved") from exc
        if not isinstance(person, Mapping) or person.get("life_status") != "alive":
            raise CommandRejectedError("promotion_exam_candidate_unavailable")
        condition = person.get("condition")
        if isinstance(condition, Mapping) and condition.get("readiness") not in (None, "ready"):
            raise CommandRejectedError("promotion_exam_candidate_unavailable")
        score, threshold, outcome = _score_candidate(person, config)
        results.append(
            {
                "candidate_ref": candidate_ref,
                "score": score,
                "threshold": threshold,
                "outcome": outcome,
            }
        )

    history = pipeline["history"]
    institution_ref = str(profile["institution_ref"])
    evaluator_ref = str(profile["authority_ref"])
    for result in results:
        history.append(
            {
                "kind": "promotion_exam_evaluation",
                "at": str(current_time),
                "cycle_id": cycle_id,
                "profile_ref": profile["id"],
                "phase": phase,
                "team_ref": team_ref,
                "evaluator_ref": evaluator_ref,
                "candidate_ref": result["candidate_ref"],
                "score": result["score"],
                "threshold": result["threshold"],
                "outcome": result["outcome"],
                "canon_status": _CANON_STATUS,
            }
        )
    if len(history) > _CURSOR:
        del history[:-_CURSOR]

    world_events = self._world_events()
    event_id = self._append_semantic_event(
        world_events,
        command=command,
        kind="promotion_exam_stage_evaluated",
        at=current_time,
        host_refs=(institution_ref, team_ref),
        actor_refs=(command.actor_id,),
        affected_owner_refs=(_CAREER,),
        material_consequence_refs=tuple(
            f"promotion_exam_evaluation:{cycle_id}:{phase}:{row['candidate_ref']}:{row['outcome']}"
            for row in results
        ),
        classification="restricted",
        audience_refs=tuple(dict.fromkeys((command.actor_id, *(row["candidate_ref"] for row in results)))),
        source_refs=(institution_ref, evaluator_ref),
        reducer_ref="shinobi_runtime.commands.promotion_exam_evaluation.promotion_exam_evaluation_resolution",
    )
    writes = {
        self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
        _CAREER: _json_bytes(pipeline),
        **self._world_event_writes(world_events),
    }
    writes = self._prune_noop_writes(writes)
    expected_paths = tuple(sorted(writes))
    expected_pipeline = copy.deepcopy(pipeline)

    def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
        if overlay.changed_paths != expected_paths:
            raise ValueError("promotion exam evaluation write set changed after planning")
        self._assert_meta(
            overlay,
            manifest,
            meta_path=self.meta_path,
            command=command,
            world_time=current_time,
        )
        if overlay.read_json(_CAREER) != expected_pipeline:
            raise ValueError("promotion exam evaluation after-image differs from plan")
        if not any(
            path == "state/reg/world-events.json" or path.startswith("state/history/events/")
            for path in expected_paths
        ):
            raise ValueError("promotion exam evaluation event did not persist")

    return _BuiltPlan(
        code="promotion_exam_evaluation_resolution_ready",
        affected_refs=expected_paths,
        writes=writes,
        result={
            "command_type": command.command_type,
            "cycle_id": cycle_id,
            "profile_ref": profile["id"],
            "institution_ref": institution_ref,
            "team_ref": team_ref,
            "phase": phase,
            "results": results,
            "stage_complete": promotion_exam_stage_complete(pipeline, profile, cycle_id, phase),
            "semantic_event_id": event_id,
            "status": "evaluated",
        },
        validator=validate,
    )


def _install_command() -> None:
    from shinobi_runtime.commands import campaign_player_handoffs as module

    COMMAND_SPECS.setdefault(
        "promotion_exam_evaluation_resolution",
        CommandSpec(
            ("cycle_id", "team_ref", "candidate_refs"),
            (),
            "Settle deterministic Academy evaluation evidence for registered exact candidates in the active qualification, field-evaluation, or finals stage; never promote or injure them.",
            {
                "cycle_id": "promotion_exam_cycle.<id>",
                "team_ref": "team.<id>",
                "candidate_refs": ["char.<id>"],
            },
        ),
    )
    planner = module.CampaignCommandPlanner
    setattr(planner, "_promotion_exam_evaluation_resolution", _plan_promotion_exam_evaluation_resolution)
    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)


def _install_phase_gate() -> None:
    from shinobi_runtime.commands import promotion_exam_pacing as pacing

    original = pacing._next_phase_due
    if getattr(original, "_promotion_exam_evaluation_gate", False):
        return

    def gated(
        profile: Mapping[str, Any],
        pipeline: Mapping[str, Any],
        cycle: Mapping[str, Any],
    ) -> Any:
        phase = cycle.get("phase")
        cycle_id = cycle.get("cycle_id")
        stages = profile.get("evaluation_stages")
        if (
            isinstance(phase, str)
            and isinstance(cycle_id, str)
            and isinstance(stages, Mapping)
            and phase in stages
            and not promotion_exam_stage_complete(pipeline, profile, cycle_id, phase)
        ):
            return None
        return original(profile, pipeline, cycle)

    gated._promotion_exam_evaluation_gate = True  # type: ignore[attr-defined]
    pacing._next_phase_due = gated


def install_promotion_exam_evaluation() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_command()
    _install_phase_gate()
    _INSTALLED = True


__all__ = [
    "install_promotion_exam_evaluation",
    "promotion_exam_evaluation_rows",
    "promotion_exam_stage_candidate_refs",
    "promotion_exam_stage_complete",
]
