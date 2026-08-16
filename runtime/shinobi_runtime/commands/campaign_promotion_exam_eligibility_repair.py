"""Guarded repair for stale exact-Genin promotion eligibility in an active exam."""
from __future__ import annotations

import copy
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _exact_payload, _json_bytes, _stable_id
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.commands import promotion_exam_finals as finals
from shinobi_runtime.commands import promotion_exam_scheduler as scheduler
from shinobi_runtime.commands.promotion_exam_service_eligibility import review_npc_team_eligibility
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_COMMAND = "campaign_promotion_exam_eligibility_repair"
_INSTALLED = False


def _registration_opened_at(pipeline: Mapping[str, Any], cycle_id: str) -> CampaignTime:
    history = pipeline.get("history")
    if not isinstance(history, list):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    rows = [
        row
        for row in history
        if isinstance(row, Mapping)
        and row.get("kind") == "promotion_exam_cycle_phase"
        and row.get("cycle_id") == cycle_id
        and row.get("phase") == "registration"
        and isinstance(row.get("at"), str)
    ]
    if len(rows) != 1:
        raise CommandRejectedError("promotion_exam_cycle_state_invalid")
    try:
        return CampaignTime.parse(str(rows[0]["at"]))
    except (TypeError, ValueError) as exc:
        raise CommandRejectedError("promotion_exam_cycle_state_invalid") from exc


def _repair(self: Any, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
    _exact_payload(command.payload, ("cycle_id",), command.command_type)
    cycle_id = _stable_id(command.payload.get("cycle_id"), "promotion_exam_eligibility_repair_cycle_invalid", prefix="promotion_exam_cycle.")
    player_id = meta.get("player_id")
    if not isinstance(player_id, str) or command.actor_id != player_id:
        raise CommandRejectedError("promotion_exam_eligibility_repair_actor_invalid")
    pipeline = scheduler._load_pipeline(self.repository)
    profiles = scheduler.promotion_exam_profiles(self.repository)
    cycle = next((row for row in scheduler.active_promotion_exam_cycles(pipeline, profiles) if row.get("cycle_id") == cycle_id), None)
    if not isinstance(cycle, Mapping) or cycle.get("phase") != "finals":
        raise CommandRejectedError("promotion_exam_eligibility_repair_not_unstarted_finals")
    if finals.promotion_exam_bout_rows(pipeline, cycle_id):
        raise CommandRejectedError("promotion_exam_eligibility_repair_bout_evidence_exists")
    profile = scheduler._profile_for_cycle(profiles, cycle)
    eligibility_basis_at = _registration_opened_at(pipeline, cycle_id)

    staged_records: dict[str, dict[str, Any]] = {}
    reviewed = review_npc_team_eligibility(
        self,
        profile=profile,
        at=eligibility_basis_at,
        player_id=player_id,
        record_writes=staged_records,
    )
    if not reviewed:
        raise CommandRejectedError("promotion_exam_eligibility_repair_nothing_to_repair")

    world_events = self._world_events()
    event_id = self._append_semantic_event(
        world_events,
        command=command,
        kind="campaign_repair_applied",
        at=current_time,
        host_refs=(str(profile["institution_ref"]), cycle_id),
        actor_refs=(player_id,),
        causal_refs=(cycle_id,),
        affected_owner_refs=tuple(sorted(staged_records)),
        material_consequence_refs=tuple(
            f"promotion_eligible:{row['candidate_ref']}:false->true:basis:{eligibility_basis_at}" for row in reviewed
        ),
        classification="restricted",
        audience_refs=(player_id,),
        source_refs=(str(profile["institution_ref"]), str(profile["authority_ref"])),
        reducer_ref="shinobi_runtime.commands.campaign_promotion_exam_eligibility_repair",
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
            raise ValueError("promotion exam eligibility repair write set changed after planning")
        self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
        for path, expected in expected_records.items():
            if overlay.read_json(path) != expected:
                raise ValueError("promotion exam eligibility repair after-image differs from plan")

    return _BuiltPlan(
        code="campaign_promotion_exam_eligibility_repair_ready",
        affected_refs=expected_paths,
        writes=writes,
        result={
            "command_type": command.command_type,
            "cycle_id": cycle_id,
            "status": "repaired",
            "eligibility_basis_at": str(eligibility_basis_at),
            "eligible_candidate_refs": [row["candidate_ref"] for row in reviewed],
            "eligible_team_refs": sorted({row["team_ref"] for row in reviewed}),
            "semantic_event_id": event_id,
            "world_time": str(current_time),
        },
        validator=validate,
    )


def install_campaign_promotion_exam_eligibility_repair() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_environment as module

    COMMAND_SPECS.setdefault(
        _COMMAND,
        CommandSpec(
            ("cycle_id",),
            (),
            "Repair stale exact-Genin promotion eligibility only when the authored service threshold was already satisfied at this cycle's registration opening and no finals bout has settled.",
            {"cycle_id": "promotion_exam_cycle.<id>"},
            availability="ooc_dev_guarded_repair_only",
        ),
    )
    planner = module.CampaignCommandPlanner
    setattr(planner, "_" + _COMMAND, _repair)
    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)
    _INSTALLED = True


__all__ = ["install_campaign_promotion_exam_eligibility_repair"]
