"""Guarded repair for omitted scheduled NPC finalist attendance."""
from __future__ import annotations

import copy
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _exact_payload, _json_bytes, _stable_id
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.commands import promotion_exam_finals as finals
from shinobi_runtime.commands import promotion_exam_scheduler as scheduler
from shinobi_runtime.commands.promotion_exam_attendance import stage_npc_finalists
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_COMMAND = "campaign_promotion_exam_attendance_repair"
_INSTALLED = False


def _repair(self: Any, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
    _exact_payload(command.payload, ("cycle_id",), command.command_type)
    cycle_id = _stable_id(command.payload.get("cycle_id"), "promotion_exam_attendance_repair_cycle_invalid", prefix="promotion_exam_cycle.")
    player_id = meta.get("player_id")
    if not isinstance(player_id, str) or command.actor_id != player_id:
        raise CommandRejectedError("promotion_exam_attendance_repair_actor_invalid")
    pipeline = scheduler._load_pipeline(self.repository)
    profiles = scheduler.promotion_exam_profiles(self.repository)
    cycle = next((row for row in scheduler.active_promotion_exam_cycles(pipeline, profiles) if row.get("cycle_id") == cycle_id), None)
    if not isinstance(cycle, Mapping) or cycle.get("phase") != "finals":
        raise CommandRejectedError("promotion_exam_attendance_repair_not_unstarted_finals")
    if finals.promotion_exam_bout_rows(pipeline, cycle_id):
        raise CommandRejectedError("promotion_exam_attendance_repair_bout_evidence_exists")
    profile = scheduler._profile_for_cycle(profiles, cycle)
    staged_records: dict[str, dict[str, Any]] = {}
    attendance = stage_npc_finalists(
        self,
        pipeline=pipeline,
        profile=profile,
        cycle_id=cycle_id,
        at=current_time,
        player_id=player_id,
        record_writes=staged_records,
    )
    if not attendance:
        raise CommandRejectedError("promotion_exam_attendance_repair_nothing_to_repair")

    world_events = self._world_events()
    event_id = self._append_semantic_event(
        world_events,
        command=command,
        kind="campaign_repair_applied",
        at=current_time,
        host_refs=(str(profile["institution_ref"]), cycle_id),
        actor_refs=(player_id,),
        causal_refs=(cycle_id,),
        place_refs=tuple(sorted({row["to_location_ref"] for row in attendance})),
        affected_owner_refs=tuple(sorted(staged_records)),
        material_consequence_refs=tuple(
            f"promotion_exam_attendance_reconciled:{row['candidate_ref']}:{row['to_location_ref']}" for row in attendance
        ),
        classification="restricted",
        audience_refs=(player_id,),
        source_refs=(str(profile["institution_ref"]),),
        reducer_ref="shinobi_runtime.commands.campaign_promotion_exam_attendance_repair",
    )
    writes = {
        self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
        **{path: _json_bytes(record) for path, record in staged_records.items()},
        **self._world_event_writes(world_events),
    }
    writes = self._prune_noop_writes(writes)
    expected_paths = tuple(sorted(writes))
    expected_records = copy.deepcopy(staged_records)

    def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
        if overlay.changed_paths != expected_paths:
            raise ValueError("promotion exam attendance repair write set changed after planning")
        self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
        for path, expected in expected_records.items():
            if overlay.read_json(path) != expected:
                raise ValueError("promotion exam attendance repair after-image differs from plan")

    return _BuiltPlan(
        code="campaign_promotion_exam_attendance_repair_ready",
        affected_refs=expected_paths,
        writes=writes,
        result={
            "command_type": command.command_type,
            "cycle_id": cycle_id,
            "status": "repaired",
            "attendance": attendance,
            "semantic_event_id": event_id,
            "world_time": str(current_time),
        },
        validator=validate,
    )


def install_campaign_promotion_exam_attendance_repair() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_environment as module

    COMMAND_SPECS.setdefault(
        _COMMAND,
        CommandSpec(
            ("cycle_id",),
            (),
            "Repair omitted scheduled local attendance for non-player exact finalists before any finals bout has settled.",
            {"cycle_id": "promotion_exam_cycle.<id>"},
            availability="ooc_dev_guarded_repair_only",
        ),
    )
    planner = module.CampaignCommandPlanner
    setattr(planner, "_" + _COMMAND, _repair)
    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)
    _INSTALLED = True


__all__ = ["install_campaign_promotion_exam_attendance_repair"]
