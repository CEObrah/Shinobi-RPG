"""One-time guarded repair for missed named-shinobi development and qualification.

The campaign reached the Chunin qualification boundary before all active named
shinobi had a lawful service-development fallback. This repair settles only the
historical deficit through the original qualification timestamp, preserves the
known Team Fujin session that happened afterward, and deterministically
recomputes qualification from reconstructed pre-session candidate views.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _exact_payload, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.named_service_development import (
    _owner_paths,
    _policy,
    _qualifying_status,
    service_start,
    settle_service_development,
)
from shinobi_runtime.commands.paths import DEVELOPMENT_BANK_PATH, WORLD_EVENT_REGISTRY_PATH
from shinobi_runtime.commands.promotion_exam_evaluation import (
    _evaluation_config,
    _score_candidate_details,
    promotion_exam_evaluation_rows,
)
from shinobi_runtime.commands.promotion_exam_scheduler import (
    _CAREER,
    promotion_exam_profiles,
    registered_candidate_refs,
)
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_REPAIR_ID = "repair.named_shinobi_training_and_chunin_qualification.2026-08-17.v1"
_EXAM_AT = CampaignTime.parse("SE-0061-08-05T07:29:58")
_REQUIRED_CURRENT_TIME = CampaignTime.parse("SE-0061-08-05T11:29:58")
_FUJIN_PRE_SESSION_CURSOR = "SE-0061-08-03T11:00:00"
_FUJIN_POST_EXAM_DELTA = {
    "char.kai": ("operational_skills", "team_coordination", 1, 107),
    "char.mei_arakawa": ("operational_skills", "team_coordination", 1, 110),
    "char.riku_hyuga": ("operational_skills", "team_coordination", 0, 103),
}
_EXPECTED_OLD_ROWS = 42
_EXPECTED_OLD_PASSES = 26
_EXPECTED_OLD_FAILS = 16
_EXPECTED_THRESHOLD = 60
_INSTALLED = False


def _load_json(repository: Any, path: str, code: str) -> Dict[str, Any]:
    try:
        value = repository.read_json(path)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError(code) from exc
    if not isinstance(value, Mapping):
        raise CommandRejectedError(code)
    return copy.deepcopy(dict(value))


def _qualification_cycle(pipeline: Mapping[str, Any]) -> tuple[str, list[Mapping[str, Any]]]:
    history = pipeline.get("history")
    if not isinstance(history, list):
        raise CommandRejectedError("named_training_exam_repair_pipeline_invalid")
    rows = [
        row for row in history
        if isinstance(row, Mapping)
        and row.get("kind") == "promotion_exam_evaluation"
        and row.get("phase") == "qualification"
        and row.get("at") == str(_EXAM_AT)
    ]
    cycles = {row.get("cycle_id") for row in rows if isinstance(row.get("cycle_id"), str)}
    if len(cycles) != 1:
        raise CommandRejectedError("named_training_exam_repair_source_not_exact")
    cycle_id = next(iter(cycles))
    rows = list(promotion_exam_evaluation_rows(pipeline, cycle_id, phase="qualification"))
    if (
        len(rows) != _EXPECTED_OLD_ROWS
        or sum(row.get("outcome") == "pass" for row in rows) != _EXPECTED_OLD_PASSES
        or sum(row.get("outcome") == "fail" for row in rows) != _EXPECTED_OLD_FAILS
        or any(row.get("threshold") != _EXPECTED_THRESHOLD for row in rows)
        or any(row.get("repair_id") == _REPAIR_ID for row in rows)
    ):
        raise CommandRejectedError("named_training_exam_repair_source_not_exact")
    return cycle_id, rows


def _reverse_known_post_exam_delta(owner_ref: str, person: Dict[str, Any]) -> None:
    spec = _FUJIN_POST_EXAM_DELTA.get(owner_ref)
    if spec is None:
        return
    container_name, leaf, delta, expected_current = spec
    container = person.get(container_name)
    if not isinstance(container, dict) or container.get(leaf) != expected_current:
        raise CommandRejectedError("named_training_exam_repair_post_exam_delta_changed")
    container[leaf] = expected_current - delta


def _repair(
    self: Any,
    command: CommandEnvelope,
    meta: Mapping[str, Any],
    current_time: CampaignTime,
) -> _BuiltPlan:
    _exact_payload(command.payload, ("repair_id",), "campaign_named_training_exam_repair")
    if command.payload["repair_id"] != _REPAIR_ID:
        raise CommandRejectedError("named_training_exam_repair_id_invalid")
    if command.actor_id != meta.get("player_id"):
        raise CommandRejectedError("named_training_exam_repair_actor_invalid")
    if current_time != _REQUIRED_CURRENT_TIME:
        raise CommandRejectedError("named_training_exam_repair_world_time_changed")

    pipeline = _load_json(self.repository, _CAREER, "named_training_exam_repair_pipeline_invalid")
    cycle_id, old_rows = _qualification_cycle(pipeline)
    old_by_candidate = {str(row["candidate_ref"]): row for row in old_rows}
    registered = set(registered_candidate_refs(pipeline, cycle_id))
    if registered != set(old_by_candidate):
        raise CommandRejectedError("named_training_exam_repair_candidate_set_changed")

    profiles = promotion_exam_profiles(self.repository)
    profile_ref = old_rows[0].get("profile_ref")
    profile = next((row for row in profiles if row.get("id") == profile_ref), None)
    if not isinstance(profile, Mapping):
        raise CommandRejectedError("named_training_exam_repair_profile_invalid")
    config = _evaluation_config(profile, "qualification")
    policy = _policy(self.repository)

    banks = _load_json(self.repository, DEVELOPMENT_BANK_PATH, "development_bank_invalid")
    entries = banks.get("entries")
    if not isinstance(entries, dict):
        raise CommandRejectedError("development_bank_invalid")

    writes: Dict[str, bytes] = {}
    pre_exam_views: Dict[str, Dict[str, Any]] = {}
    catchup_rows = []
    changed_people = 0
    total_hours = 0.0

    for owner_ref, path in sorted(_owner_paths(self.repository).items()):
        try:
            person = self.repository.read_json(path)
        except (FileNotFoundError, ValueError):
            continue
        if not isinstance(person, Mapping):
            continue
        current_person = copy.deepcopy(dict(person))
        if owner_ref in registered:
            pre_person = copy.deepcopy(current_person)
            _reverse_known_post_exam_delta(owner_ref, pre_person)
            pre_exam_views[owner_ref] = pre_person
        if owner_ref == command.actor_id or not _qualifying_status(current_person, policy):
            continue

        entry = entries.get(owner_ref)
        if entry is None:
            # A character absent from the historical development bank has no
            # attested exact backlog. Start prospectively rather than invent it.
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("credits"), dict):
            raise CommandRejectedError("development_bank_invalid")
        try:
            cursor = CampaignTime.parse(entry.get("resolved_through"))
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("development_bank_invalid") from exc

        # Candidate scoring uses a separate pre-exam copy. For the three Fujin
        # genin, current bank credit includes the later 07:29:58-11:29:58
        # session; reset only the scoring copy to the last attested pre-session
        # cursor. It cannot create historical catch-up because less than a full
        # week remained before qualification.
        if owner_ref in registered:
            pre_entry = copy.deepcopy(entry)
            if owner_ref in _FUJIN_POST_EXAM_DELTA:
                pre_entry["resolved_through"] = _FUJIN_PRE_SESSION_CURSOR
            try:
                pre_cursor = CampaignTime.parse(pre_entry.get("resolved_through"))
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("development_bank_invalid") from exc
            pre_start = max(pre_cursor, service_start(pre_exam_views[owner_ref], pre_cursor, policy))
            settle_service_development(
                pre_exam_views[owner_ref],
                pre_entry,
                owner_ref=owner_ref,
                start=pre_start,
                through=_EXAM_AT,
                policy=policy,
                historical=True,
            )

        start = max(cursor, service_start(current_person, cursor, policy))
        outcome = settle_service_development(
            current_person,
            entry,
            owner_ref=owner_ref,
            start=start,
            through=_EXAM_AT,
            policy=policy,
            historical=True,
        )
        if outcome["outcomes"]:
            writes[path] = _json_bytes(current_person)
            changed_people += 1
            total_hours += float(outcome["hours"])
            catchup_rows.append({
                "owner_ref": owner_ref,
                "from": str(start),
                "through": str(_EXAM_AT),
                "active_hours": outcome["hours"],
                "targets": [row["target"] for row in outcome["outcomes"]],
            })

    missing_views = sorted(registered.difference(pre_exam_views))
    if missing_views:
        raise CommandRejectedError("named_training_exam_repair_candidate_unresolved")

    new_results: Dict[str, Dict[str, Any]] = {}
    for candidate_ref in sorted(registered):
        new_results[candidate_ref] = _score_candidate_details(pre_exam_views[candidate_ref], config)

    history = pipeline.get("history")
    if not isinstance(history, list):
        raise CommandRejectedError("named_training_exam_repair_pipeline_invalid")
    replaced = 0
    for index, row in enumerate(history):
        if not (
            isinstance(row, Mapping)
            and row.get("kind") == "promotion_exam_evaluation"
            and row.get("cycle_id") == cycle_id
            and row.get("phase") == "qualification"
            and row.get("candidate_ref") in new_results
        ):
            continue
        candidate_ref = str(row["candidate_ref"])
        details = new_results[candidate_ref]
        new_row = dict(row)
        new_row.update({
            "score": details["score"],
            "threshold": details["threshold"],
            "outcome": details["outcome"],
            "scoring_model": details["scoring_model"],
            "scoring_version": details["scoring_version"],
            "lane_scores": details["lane_scores"],
            "repair_id": _REPAIR_ID,
            "repaired_at": str(current_time),
            "superseded_score": row.get("score"),
            "superseded_outcome": row.get("outcome"),
        })
        history[index] = new_row
        replaced += 1
    if replaced != len(registered):
        raise CommandRejectedError("named_training_exam_repair_evaluation_count_changed")

    new_passes = sorted(ref for ref, row in new_results.items() if row["outcome"] == "pass")
    new_fails = sorted(ref for ref, row in new_results.items() if row["outcome"] == "fail")
    history.append({
        "kind": "promotion_exam_evaluation_repair",
        "at": str(current_time),
        "effective_at": str(_EXAM_AT),
        "cycle_id": cycle_id,
        "phase": "qualification",
        "repair_id": _REPAIR_ID,
        "reason": "missed named-shinobi service development before qualification",
        "old_pass_count": _EXPECTED_OLD_PASSES,
        "old_fail_count": _EXPECTED_OLD_FAILS,
        "new_pass_count": len(new_passes),
        "new_fail_count": len(new_fails),
        "historical_people_caught_up": changed_people,
        "historical_active_hours": total_hours,
    })

    writes[DEVELOPMENT_BANK_PATH] = _json_bytes(banks)
    writes[_CAREER] = _json_bytes(pipeline)

    world_events = self._world_events()
    event_id = self._append_semantic_event(
        world_events,
        command=command,
        kind="campaign_repair_applied",
        at=current_time,
        host_refs=(str(profile.get("institution_ref")),),
        actor_refs=(command.actor_id,),
        affected_owner_refs=(DEVELOPMENT_BANK_PATH, _CAREER),
        material_consequence_refs=(
            f"named_shinobi_catchup:{changed_people}",
            f"historical_training_hours:{total_hours:.3f}",
            f"qualification_passes:{_EXPECTED_OLD_PASSES}->{len(new_passes)}",
            f"qualification_fails:{_EXPECTED_OLD_FAILS}->{len(new_fails)}",
        ),
        classification="restricted",
        audience_refs=(command.actor_id,),
        source_refs=(_REPAIR_ID, cycle_id),
        reducer_ref="shinobi_runtime.commands.campaign_named_training_exam_repair",
    )
    writes[self.meta_path] = _json_bytes(self._meta_after(meta, command, world_time=current_time))
    writes.update(self._world_event_writes(world_events))
    writes = self._prune_noop_writes(writes)
    expected_paths = tuple(sorted(writes))
    expected_json = {
        path: copy.deepcopy(value)
        for path, value in (
            (DEVELOPMENT_BANK_PATH, banks),
            (_CAREER, pipeline),
        )
    }

    def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
        if overlay.changed_paths != expected_paths:
            raise ValueError("named training/exam repair write set changed after planning")
        self._assert_meta(
            overlay,
            manifest,
            meta_path=self.meta_path,
            command=command,
            world_time=current_time,
        )
        for path, expected in expected_json.items():
            if overlay.read_json(path) != expected:
                raise ValueError("named training/exam repair core owner mismatch")
        staged_events = overlay.read_json(WORLD_EVENT_REGISTRY_PATH).get("events", [])
        if not any(isinstance(item, Mapping) and item.get("id") == event_id for item in staged_events):
            raise ValueError("named training/exam repair semantic event missing")

    return _BuiltPlan(
        code="campaign_named_training_exam_repair_ready",
        affected_refs=expected_paths,
        writes=writes,
        result={
            "command_type": command.command_type,
            "repair_id": _REPAIR_ID,
            "status": "repaired",
            "world_time": str(current_time),
            "effective_exam_time": str(_EXAM_AT),
            "historical_people_caught_up": changed_people,
            "historical_active_hours": total_hours,
            "old_pass_count": _EXPECTED_OLD_PASSES,
            "old_fail_count": _EXPECTED_OLD_FAILS,
            "new_pass_count": len(new_passes),
            "new_fail_count": len(new_fails),
            "passes": [
                {"candidate_ref": ref, "score": new_results[ref]["score"]}
                for ref in sorted(new_passes, key=lambda value: (-new_results[value]["score"], value))
            ],
            "fails": [
                {"candidate_ref": ref, "score": new_results[ref]["score"]}
                for ref in sorted(new_fails, key=lambda value: (-new_results[value]["score"], value))
            ],
            "catchup": catchup_rows,
        },
        validator=validate,
    )


def install_campaign_named_training_exam_repair() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_environment as module

    COMMAND_SPECS.setdefault(
        "campaign_named_training_exam_repair",
        CommandSpec(
            ("repair_id",),
            (),
            "Settle the bounded named-shinobi development deficit through the original Chunin qualification boundary and recompute qualification from the repaired historical view.",
            {"repair_id": _REPAIR_ID},
            availability="ooc_dev_guarded_repair_only",
        ),
    )
    planner = module.CampaignCommandPlanner
    setattr(planner, "_campaign_named_training_exam_repair", _repair)
    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)
    _INSTALLED = True


__all__ = [
    "install_campaign_named_training_exam_repair",
    "_REPAIR_ID",
]
