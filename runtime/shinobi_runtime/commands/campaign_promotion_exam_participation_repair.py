"""Guarded reconciliation for omitted non-player Chunin Exam participation.

The repair is intentionally narrow: it is available only during an already-open
finals phase before any bout evidence exists. Reconstructed registration and
stage-evaluation rows use the cycle's original scheduled phase timestamps rather
than the repair transaction time, so the durable career pipeline keeps lawful
chronology while the separate repair semantic event records when reconciliation
was actually applied.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _exact_payload, _json_bytes, _stable_id
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.commands import promotion_exam_finals as finals
from shinobi_runtime.commands import promotion_exam_pacing as pacing
from shinobi_runtime.commands import promotion_exam_scheduler as scheduler
from shinobi_runtime.commands.promotion_exam_integrity import (
    _append_npc_evaluations,
    _append_npc_registrations,
    eligible_npc_team_registrations,
    team_safe_finals_state,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_COMMAND = "campaign_promotion_exam_participation_repair"
_CAREER = "state/reg/shinobi-career-pipeline.json"
_INSTALLED = False
_REQUIRED_REPAIR_PHASES = (
    "registration",
    "qualification",
    "field_evaluation",
    "finals",
)


def _repair_phase_times(
    schedule: Mapping[str, str],
    *,
    current_time: CampaignTime,
) -> Mapping[str, CampaignTime]:
    parsed: dict[str, CampaignTime] = {}
    for phase in _REQUIRED_REPAIR_PHASES:
        raw = schedule.get(phase)
        if not isinstance(raw, str):
            raise CommandRejectedError("promotion_exam_cycle_state_invalid")
        try:
            parsed[phase] = CampaignTime.parse(raw)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("promotion_exam_cycle_state_invalid") from exc
    ordered = [parsed[phase] for phase in _REQUIRED_REPAIR_PHASES]
    if ordered != sorted(ordered) or len(set(ordered)) != len(ordered):
        raise CommandRejectedError("promotion_exam_cycle_state_invalid")
    if parsed["finals"] > current_time:
        raise CommandRejectedError("promotion_exam_repair_before_finals")
    return parsed


def _repair(self: Any, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
    _exact_payload(command.payload, ("cycle_id",), command.command_type)
    cycle_id = _stable_id(command.payload.get("cycle_id"), "promotion_exam_repair_cycle_invalid", prefix="promotion_exam_cycle.")
    player_id = meta.get("player_id")
    if not isinstance(player_id, str) or command.actor_id != player_id:
        raise CommandRejectedError("promotion_exam_repair_actor_invalid")

    pipeline = scheduler._load_pipeline(self.repository)
    profiles = scheduler.promotion_exam_profiles(self.repository)
    cycle = next((row for row in scheduler.active_promotion_exam_cycles(pipeline, profiles) if row.get("cycle_id") == cycle_id), None)
    if not isinstance(cycle, Mapping) or cycle.get("phase") != "finals":
        raise CommandRejectedError("promotion_exam_repair_not_unstarted_finals")
    profile = scheduler._profile_for_cycle(profiles, cycle)
    if finals.promotion_exam_bout_rows(pipeline, cycle_id):
        raise CommandRejectedError("promotion_exam_repair_bout_evidence_exists")

    schedule = pacing.promotion_exam_schedule_for_cycle(
        self.repository,
        cycle_id=cycle_id,
    )
    repair_times = _repair_phase_times(schedule, current_time=current_time)

    before_pipeline = copy.deepcopy(pipeline)
    registrations = eligible_npc_team_registrations(
        self,
        profile=profile,
        pipeline=pipeline,
        cycle_id=cycle_id,
        player_id=player_id,
    )
    added_candidates = _append_npc_registrations(
        pipeline,
        profile=profile,
        cycle_id=cycle_id,
        at=repair_times["registration"],
        registrations=registrations,
        repair=True,
    )
    added_set = set(added_candidates)
    stage_results: dict[str, list[dict[str, Any]]] = {}
    if added_set:
        qualification = _append_npc_evaluations(
            self,
            pipeline=pipeline,
            profile=profile,
            cycle_id=cycle_id,
            phase="qualification",
            at=repair_times["qualification"],
            player_id=player_id,
            only_candidates=added_set,
            repair=True,
        )
        stage_results["qualification"] = qualification
        qualification_passers = {
            row["candidate_ref"]
            for row in qualification
            if row.get("outcome") == "pass"
        }
        stage_results["field_evaluation"] = _append_npc_evaluations(
            self,
            pipeline=pipeline,
            profile=profile,
            cycle_id=cycle_id,
            phase="field_evaluation",
            at=repair_times["field_evaluation"],
            player_id=player_id,
            only_candidates=qualification_passers,
            repair=True,
        )

    if pipeline == before_pipeline:
        raise CommandRejectedError("promotion_exam_repair_nothing_to_reconcile")

    refreshed = team_safe_finals_state(pipeline, profile, cycle_id)
    world_events = self._world_events()
    event_id = self._append_semantic_event(
        world_events,
        command=command,
        kind="campaign_repair_applied",
        at=current_time,
        host_refs=(str(profile["institution_ref"]), cycle_id),
        actor_refs=(player_id,),
        causal_refs=(cycle_id,),
        affected_owner_refs=(_CAREER,),
        material_consequence_refs=tuple(
            [
                f"promotion_exam_registration_reconciled:{ref}:basis:{repair_times['registration']}"
                for ref in added_candidates
            ]
            + [
                f"promotion_exam_evaluation_reconciled:{phase}:{row['candidate_ref']}:{row['outcome']}:basis:{repair_times[phase]}"
                for phase, rows in stage_results.items()
                for row in rows
            ]
        ),
        classification="restricted",
        audience_refs=(player_id,),
        source_refs=(str(profile["institution_ref"]), str(profile["authority_ref"])),
        reducer_ref="shinobi_runtime.commands.campaign_promotion_exam_participation_repair",
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
            raise ValueError("promotion exam participation repair write set changed after planning")
        self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
        if overlay.read_json(_CAREER) != expected_pipeline:
            raise ValueError("promotion exam participation repair after-image differs from plan")

    return _BuiltPlan(
        code="campaign_promotion_exam_participation_repair_ready",
        affected_refs=expected_paths,
        writes=writes,
        result={
            "command_type": command.command_type,
            "cycle_id": cycle_id,
            "status": "reconciled",
            "registered_team_refs": [row["team_ref"] for row in registrations],
            "registered_candidate_refs": added_candidates,
            "evaluation_results": stage_results,
            "reconciled_evidence_times": {
                phase: str(repair_times[phase])
                for phase in ("registration", "qualification", "field_evaluation")
            },
            "finals_candidate_refs": list(refreshed.get("candidate_refs", ())),
            "finals_open_bouts": list(refreshed.get("open_bouts", ())),
            "finals_complete": bool(refreshed.get("complete")),
            "finals_champion_ref": refreshed.get("champion_ref"),
            "finals_co_finalist_refs": list(refreshed.get("co_finalist_refs", ())),
            "semantic_event_id": event_id,
            "world_time": str(current_time),
        },
        validator=validate,
    )


def install_campaign_promotion_exam_participation_repair() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_environment as module

    COMMAND_SPECS.setdefault(
        _COMMAND,
        CommandSpec(
            ("cycle_id",),
            (),
            "Reconcile omitted non-player exact-team Chunin Exam participation for an active finals phase only when no finals bout has settled, preserving the original phase chronology.",
            {"cycle_id": "promotion_exam_cycle.<id>"},
            availability="ooc_dev_guarded_repair_only",
        ),
    )
    planner = module.CampaignCommandPlanner
    setattr(planner, "_" + _COMMAND, _repair)
    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)
    _INSTALLED = True


__all__ = [
    "_repair_phase_times",
    "install_campaign_promotion_exam_participation_repair",
]
