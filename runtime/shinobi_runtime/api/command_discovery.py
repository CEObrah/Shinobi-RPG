"""Compact semantic-command discovery for the public MCP handoff.

The runtime may keep rich command descriptors internally for validation and
preview construction. Live ChatGPT context receives only command names grouped
by player intent plus material availability overrides. The selected command's
full descriptor is retrieved separately through get_command_contract.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from shinobi_runtime.api.models import validate_bounded_json


_DOMAIN_PREFIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("time", ("advance_time", "scene_boundary", "downtime")),
    ("missions", ("mission_",)),
    ("training", ("training_", "breakthrough_", "technique_learning", "team_training", "team_development")),
    ("teams", ("team_",)),
    ("travel", ("travel_", "formation_movement")),
    ("combat", ("combat_", "battlefield_", "formation_combat", "conflict_", "special_combat_state_")),
    ("population", ("population_", "recruitment_", "person_materialization", "person_exactification")),
    ("medical", ("medical_", "recovery_")),
    ("social", ("relationship_", "reputation_", "family_", "career_", "office_", "institution_affiliation", "promotion_exam_")),
    ("economy", ("asset_", "purchase_", "service_", "inventory_", "commerce_", "manufacturing_")),
    ("information", ("information_", "investigation_", "security_network_")),
    ("institutions", ("institution_", "governance_", "legal_", "commitment_", "custody_")),
    ("diplomacy", ("diplomacy_",)),
    ("forces", ("formation_", "force_", "command_")),
    ("special", ("research_", "seal_", "summon_", "puppet_", "ocular_", "biological_", "jinchuriki_")),
)

_EXAM_RESULTS_PREFIX = "exam-results:"
_MAX_CONTEXT_PERSON_SUGGESTIONS = 32
_MAX_CONTEXT_TEAM_SUGGESTIONS = 32
_CORE_SCENE_FIELDS = (
    "scene_id",
    "world_time",
    "location_id",
    "active_combat",
    "time_passage_allowed",
    "freeform_actions_allowed",
    "scene_summary",
    "decision_required",
    "pending_combat_zoom_ref",
    "known_clock_boundaries",
    "observable_pressures",
    "causal_refs",
    "narrative",
    "scene_cast",
    "scene_vitality",
    "activity_handoff",
    "time_continuation",
    "promotion_exam_handoffs",
    "team_checkin_handoffs",
)


def command_domain(command_type: str) -> str:
    for domain, prefixes in _DOMAIN_PREFIXES:
        if any(command_type == prefix or command_type.startswith(prefix) for prefix in prefixes):
            return domain
    return "other"


def compact_commands(command_surface: Mapping[str, Any]) -> dict[str, Any]:
    supported = [str(x) for x in command_surface.get("supported_command_types", []) if isinstance(x, str)]
    supported_set = set(supported)
    grouped: dict[str, list[str]] = {}
    for command_type in sorted(supported_set):
        grouped.setdefault(command_domain(command_type), []).append(command_type)

    overrides: dict[str, str] = {}
    existing_overrides = command_surface.get("availability_overrides")
    if isinstance(existing_overrides, Mapping):
        overrides.update(
            {
                str(command_type): availability
                for command_type, availability in existing_overrides.items()
                if isinstance(command_type, str)
                and command_type in supported_set
                and isinstance(availability, str)
            }
        )
    records = command_surface.get("command_types")
    if isinstance(records, Mapping):
        for command_type in supported:
            record = records.get(command_type)
            if not isinstance(record, Mapping):
                continue
            availability = record.get("availability")
            if isinstance(availability, str) and availability not in {
                "subject_to_domain_authority_and_state",
                "available",
            }:
                overrides[command_type] = availability

    result: dict[str, Any] = {
        "supported_command_types": sorted(supported_set),
        "intent_domains": grouped,
        "availability_overrides": dict(sorted(overrides.items())),
        "contract_lookup": "Call get_command_contract for the one selected command before preview.",
    }
    for key in (
        "active_mission_owner_ids",
        "known_unsupported_intents",
        "limits",
        "availability_scope",
        "temporarily_available_command_types",
        "hidden_internal_command_types",
    ):
        if key in command_surface:
            result[key] = command_surface[key]
    return result


def _exam_results_ref(cycle_id: str, phase: str, offset: int = 0) -> str:
    return f"{_EXAM_RESULTS_PREFIX}{cycle_id}:{phase}:{offset}"


def _compact_promotion_exam_handoffs(scene: dict[str, Any], compacted_fields: list[str]) -> None:
    handoffs = scene.get("promotion_exam_handoffs")
    if not isinstance(handoffs, list):
        return
    compacted: list[Any] = []
    for row in handoffs:
        if not isinstance(row, Mapping):
            compacted.append(row)
            continue
        updated = dict(row)
        cycle_id = updated.get("cycle_id")
        existing_refs = updated.get("public_stage_result_read_refs")
        read_refs: dict[str, str] = {
            phase: ref
            for phase, ref in existing_refs.items()
            if isinstance(phase, str) and isinstance(ref, str)
        } if isinstance(existing_refs, Mapping) else {}

        summaries = updated.get("public_stage_result_summaries")
        if isinstance(summaries, Mapping) and isinstance(cycle_id, str):
            for phase, summary in summaries.items():
                if (
                    isinstance(phase, str)
                    and isinstance(summary, Mapping)
                    and isinstance(summary.get("candidate_count"), int)
                    and summary.get("candidate_count", 0) > 0
                ):
                    read_refs.setdefault(phase, _exam_results_ref(cycle_id, phase))

        # Compatibility with older rich projections: remove embedded rows and
        # reconstruct exact first-page refs from any settled rows that remain.
        public_results = updated.pop("public_stage_results", None)
        if isinstance(public_results, Mapping) and isinstance(cycle_id, str):
            for phase, rows in public_results.items():
                if isinstance(phase, str) and isinstance(rows, list) and rows:
                    read_refs.setdefault(phase, _exam_results_ref(cycle_id, phase))
        if public_results is not None:
            compacted_fields.append("scene.promotion_exam_handoffs.public_stage_results")

        for legacy_field in (
            "public_stage_results_truncated",
            "public_stage_results_projection_limit",
        ):
            if legacy_field in updated:
                updated.pop(legacy_field, None)
                compacted_fields.append(f"scene.promotion_exam_handoffs.{legacy_field}")

        updated["public_stage_results_in_context"] = False
        if read_refs:
            updated["public_stage_result_read_refs"] = dict(sorted(read_refs.items()))
        compacted.append(updated)
    scene["promotion_exam_handoffs"] = compacted


def _compact_narration(result: dict[str, Any], compacted_fields: list[str]) -> None:
    narration = result.get("narration")
    if not isinstance(narration, Mapping):
        return
    updated = dict(narration)
    modules = updated.pop("modules", None)
    if isinstance(modules, list):
        module_ids = [
            row.get("module_id")
            for row in modules
            if isinstance(row, Mapping) and isinstance(row.get("module_id"), str)
        ]
        if module_ids:
            updated["module_ids"] = module_ids
        compacted_fields.append("narration.modules.guidance")
    result["narration"] = updated


def _compact_person_reads(result: dict[str, Any], compacted_fields: list[str]) -> None:
    person_reads = result.get("person_reads")
    if not isinstance(person_reads, Mapping):
        return
    updated = dict(person_reads)
    ids = updated.get("suggested_owner_ids")
    if isinstance(ids, list) and len(ids) > _MAX_CONTEXT_PERSON_SUGGESTIONS:
        updated["suggested_owner_ids"] = ids[:_MAX_CONTEXT_PERSON_SUGGESTIONS]
        updated["suggested_ids_truncated"] = True
        compacted_fields.append("person_reads.suggested_owner_ids")
    result["person_reads"] = updated


def _compact_object_reads(result: dict[str, Any], compacted_fields: list[str]) -> None:
    object_reads = result.get("object_reads")
    if not isinstance(object_reads, Mapping):
        return
    updated = dict(object_reads)
    prefixes = updated.get("supported_ref_prefixes")
    if isinstance(prefixes, list) and _EXAM_RESULTS_PREFIX not in prefixes:
        updated["supported_ref_prefixes"] = list(prefixes) + [_EXAM_RESULTS_PREFIX]
    use = updated.get("use")
    if isinstance(use, str) and "promotion exam results page" not in use:
        updated["use"] = use + ", or one promotion exam results page from an advertised exam-results ref"

    team_refs = updated.get("suggested_exact_team_refs")
    if isinstance(team_refs, list) and len(team_refs) > _MAX_CONTEXT_TEAM_SUGGESTIONS:
        updated["suggested_exact_team_refs"] = team_refs[:_MAX_CONTEXT_TEAM_SUGGESTIONS]
        updated["exact_team_refs_truncated"] = True
        compacted_fields.append("object_reads.suggested_exact_team_refs")
    result["object_reads"] = updated


def _fits_wire_budget(value: Mapping[str, Any]) -> bool:
    try:
        validate_bounded_json(value, label="compact play context", allow_float=True)
    except ValueError:
        return False
    return True


def _degrade_scene_for_wire_budget(result: dict[str, Any], compacted_fields: list[str]) -> None:
    scene = result.get("scene")
    if not isinstance(scene, Mapping):
        return
    omitted = [key for key in scene if key not in _CORE_SCENE_FIELDS]
    if not omitted:
        return
    result["scene"] = {
        key: copy.deepcopy(scene[key])
        for key in _CORE_SCENE_FIELDS
        if key in scene
    }
    policy = result.get("context_policy")
    updated_policy = dict(policy) if isinstance(policy, Mapping) else {}
    updated_policy["degraded_projection"] = True
    updated_policy["omitted_scene_fields"] = sorted(omitted)
    result["context_policy"] = updated_policy
    compacted_fields.extend(f"scene.{key}" for key in omitted)


def compact_play_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Return the public wire handoff and enforce its global transport budget.

    Internal campaign projections may be richer than the MCP response. Bulk
    command descriptors, narration module prose, and institution-wide exam
    result tables are rehydratable detail rather than mandatory turn context.
    A final deterministic scene degradation is a last-resort transport safety
    valve: it preserves core/actionable handoff fields and explicitly reports
    every omitted scene field instead of failing the entire live turn.

    The operation is idempotent: production operations compact once at their
    public boundary and transports may defensively apply it again without
    dropping availability overrides, routing hints, or prior compaction markers.
    """

    result = copy.deepcopy(dict(context))
    policy = result.get("context_policy")
    compacted_fields = [
        field
        for field in (
            policy.get("compacted_fields", []) if isinstance(policy, Mapping) else []
        )
        if isinstance(field, str)
    ]
    commands = result.get("commands")
    if isinstance(commands, Mapping):
        result["commands"] = compact_commands(commands)
        if "command_types" in commands:
            compacted_fields.append("commands.command_types")

    scene = result.get("scene")
    if isinstance(scene, dict):
        _compact_promotion_exam_handoffs(scene, compacted_fields)
    _compact_narration(result, compacted_fields)
    _compact_person_reads(result, compacted_fields)
    _compact_object_reads(result, compacted_fields)

    policy = result.get("context_policy")
    updated_policy = dict(policy) if isinstance(policy, Mapping) else {}
    updated_policy["wire_projection"] = "compact_player_visible_handoff"
    updated_policy["compacted_fields"] = sorted(set(compacted_fields))
    result["context_policy"] = updated_policy

    if not _fits_wire_budget(result):
        _degrade_scene_for_wire_budget(result, compacted_fields)
        policy = result.get("context_policy")
        updated_policy = dict(policy) if isinstance(policy, Mapping) else {}
        updated_policy["compacted_fields"] = sorted(set(compacted_fields))
        result["context_policy"] = updated_policy

    validate_bounded_json(result, label="compact play context", allow_float=True)
    return result


__all__ = ["command_domain", "compact_commands", "compact_play_context"]
