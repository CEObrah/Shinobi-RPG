#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    jsonschema = None

from test_templates import validate_doc


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "game/schemas/world-event-registry.schema.json"
TEMPLATE_PATH = ROOT / "runtime/contracts/templates/world-event-registry.template.json"
REGISTRY_PATH = ROOT / "state/reg/world-events.json"
FIXTURE_PATH = ROOT / "tests/world-event-registry-fixtures.json"
TIME_RE = re.compile(r"SE-(-?\d+)-(\d+)-(\d+)T(\d+):(\d+):(\d+)")
EVENT_ID_RE = re.compile(r"event\.[a-z0-9][a-z0-9._-]*")
KIND_RE = re.compile(r"[a-z][a-z0-9._-]*")
TERMINAL_STATUSES = {"resolved", "failed", "cancelled", "superseded"}
STATUSES = {"scheduled", "active"} | TERMINAL_STATUSES


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def registry_with(events: list[dict]) -> dict:
    return {
        "schema": "world-event-registry",
        "owner_id": "registry.world_events",
        "owner_type": "world_event_registry",
        "segment_limit": 128,
        "archived_event_count": 0,
        "archive_refs": [],
        "next_archive_seq": 1,
        "events": events,
        "archetype_catalog_ref": "game/data/content/world-event-archetypes.json",
    }


def nonempty_string(value) -> bool:
    return isinstance(value, str) and bool(value)


def focused_contract_errors(registry: dict) -> list[str]:
    """Dependency-free assertions for the registered rich event contract."""
    errors = []
    if registry.get("schema") != "world-event-registry":
        errors.append("registry_schema")
    if not nonempty_string(registry.get("owner_id")):
        errors.append("registry_owner_id")
    if registry.get("owner_type") != "world_event_registry":
        errors.append("registry_owner_type")
    events = registry.get("events")
    if not isinstance(events, list):
        return errors + ["events_not_array"]

    required = {
        "id", "kind", "status", "timing", "host_refs", "actor_refs",
        "place_refs", "causal_refs", "affected_owner_refs",
        "material_consequence_refs", "visibility", "provenance", "execution",
        "supersedes_ref", "superseded_by_ref",
    }
    array_fields = {
        "host_refs", "actor_refs", "place_refs", "causal_refs",
        "affected_owner_refs", "material_consequence_refs",
    }
    for position, event in enumerate(events):
        label = event.get("id", f"position:{position}") if isinstance(event, dict) else f"position:{position}"
        if not isinstance(event, dict):
            errors.append(f"event_not_object:{label}")
            continue
        missing = sorted(required - set(event))
        if missing:
            errors.append(f"event_missing_fields:{label}:{missing}")
        if not nonempty_string(event.get("id")) or not EVENT_ID_RE.fullmatch(event.get("id", "")):
            errors.append(f"event_id:{label}")
        if not nonempty_string(event.get("kind")) or not KIND_RE.fullmatch(event.get("kind", "")):
            errors.append(f"event_kind:{label}")
        status = event.get("status")
        if status not in STATUSES:
            errors.append(f"event_status:{label}:{status}")

        for field in array_fields:
            values = event.get(field)
            if not isinstance(values, list) or any(not nonempty_string(value) for value in values):
                errors.append(f"event_ref_array:{label}:{field}")
            elif len(values) != len(set(values)):
                errors.append(f"event_duplicate_ref:{label}:{field}")

        timing = event.get("timing")
        timing_keys = {"scheduled_for", "occurred_at", "started_at", "ended_at"}
        if not isinstance(timing, dict) or not timing_keys.issubset(timing):
            errors.append(f"event_timing_shape:{label}")
            timing = {}
        for field in timing_keys:
            value = timing.get(field)
            if value is not None and (not isinstance(value, str) or TIME_RE.fullmatch(value) is None):
                errors.append(f"event_time:{label}:{field}:{value}")

        visibility = event.get("visibility")
        visibility_keys = {"classification", "witness_refs", "audience_refs", "knowledge_refs", "route_refs"}
        if not isinstance(visibility, dict) or not visibility_keys.issubset(visibility):
            errors.append(f"event_visibility_shape:{label}")
        else:
            if visibility.get("classification") not in {"public", "restricted", "secret"}:
                errors.append(f"event_visibility_classification:{label}")
            for field in visibility_keys - {"classification"}:
                values = visibility.get(field)
                if not isinstance(values, list) or any(not nonempty_string(value) for value in values):
                    errors.append(f"event_visibility_refs:{label}:{field}")
                elif len(values) != len(set(values)):
                    errors.append(f"event_visibility_duplicate_ref:{label}:{field}")

        provenance = event.get("provenance")
        provenance_keys = {"source_kind", "source_refs", "archetype_ref", "recorded_at"}
        if not isinstance(provenance, dict) or not provenance_keys.issubset(provenance):
            errors.append(f"event_provenance_shape:{label}")
        else:
            source_kind = provenance.get("source_kind")
            if not nonempty_string(source_kind) or not KIND_RE.fullmatch(source_kind):
                errors.append(f"event_source_kind:{label}")
            source_refs = provenance.get("source_refs")
            if not isinstance(source_refs, list) or not source_refs or any(not nonempty_string(value) for value in source_refs):
                errors.append(f"event_source_refs:{label}")
            elif len(source_refs) != len(set(source_refs)):
                errors.append(f"event_duplicate_source_ref:{label}")
            archetype_ref = provenance.get("archetype_ref")
            if archetype_ref is not None and not nonempty_string(archetype_ref):
                errors.append(f"event_archetype_ref:{label}")
            if TIME_RE.fullmatch(str(provenance.get("recorded_at", ""))) is None:
                errors.append(f"event_recorded_at:{label}")

        execution = event.get("execution")
        execution_keys = {"reducer_ref", "transaction_ref", "receipt_refs"}
        if not isinstance(execution, dict) or not execution_keys.issubset(execution):
            errors.append(f"event_execution_shape:{label}")
        else:
            for field in execution_keys - {"receipt_refs"}:
                if not nonempty_string(execution.get(field)):
                    errors.append(f"event_execution_ref:{label}:{field}")
            receipt_refs = execution.get("receipt_refs")
            if not isinstance(receipt_refs, list) or not receipt_refs or any(not nonempty_string(value) for value in receipt_refs):
                errors.append(f"event_receipt_refs:{label}")
            elif len(receipt_refs) != len(set(receipt_refs)):
                errors.append(f"event_duplicate_receipt_ref:{label}")

        for field in ("supersedes_ref", "superseded_by_ref"):
            value = event.get(field)
            if value is not None and not nonempty_string(value):
                errors.append(f"event_supersession_ref:{label}:{field}")
        if status == "superseded":
            if not nonempty_string(event.get("superseded_by_ref")):
                errors.append(f"superseded_event_without_successor:{label}")
        elif event.get("superseded_by_ref") is not None:
            errors.append(f"non_superseded_event_has_successor:{label}")

        if status == "scheduled":
            if not nonempty_string(timing.get("scheduled_for")):
                errors.append(f"scheduled_event_without_due_time:{label}")
            if any(timing.get(field) is not None for field in ("occurred_at", "started_at", "ended_at")):
                errors.append(f"scheduled_event_has_material_time:{label}")
            if event.get("affected_owner_refs") or event.get("material_consequence_refs"):
                errors.append(f"scheduled_event_has_consequences:{label}")
        elif status == "active":
            if not nonempty_string(timing.get("started_at")):
                errors.append(f"active_event_without_start:{label}")
            if timing.get("occurred_at") is not None or timing.get("ended_at") is not None:
                errors.append(f"active_event_has_terminal_time:{label}")
        elif status in TERMINAL_STATUSES:
            if timing.get("occurred_at") is None and timing.get("ended_at") is None:
                errors.append(f"terminal_event_without_outcome_time:{label}")
            if not event.get("material_consequence_refs"):
                errors.append(f"terminal_event_without_material_consequence:{label}")

        summary = event.get("display_summary")
        if summary is not None and (not nonempty_string(summary) or len(summary) > 280):
            errors.append(f"event_display_summary:{label}")
    return errors


def structural_errors(registry: dict, schema: dict, template: dict) -> list[str]:
    errors = focused_contract_errors(registry)
    if jsonschema is not None:
        errors.extend(
            error.message
            for error in jsonschema.Draft202012Validator(schema).iter_errors(registry)
        )
    validate_doc("world-event-registry", registry, template, errors)
    return errors


def parse_time(value: str | None) -> int | None:
    if value is None:
        return None
    match = TIME_RE.fullmatch(value)
    if not match:
        return None
    year, month, day, hour, minute, second = map(int, match.groups())
    days = year * 372 + (month - 1) * 31 + day - 1
    return (((days * 24) + hour) * 60 + minute) * 60 + second


def semantic_errors(registry: dict) -> list[str]:
    errors = []
    seen_ids = set()
    for position, event in enumerate(registry.get("events", [])):
        event_id = event.get("id", f"position:{position}")
        if event_id in seen_ids:
            errors.append(f"duplicate_event_id:{event_id}")
        seen_ids.add(event_id)

        if not (
            event.get("host_refs")
            or event.get("actor_refs")
            or event.get("place_refs")
        ):
            errors.append(f"event_without_causal_context:{event_id}")

        timing = event.get("timing") or {}
        scheduled = parse_time(timing.get("scheduled_for"))
        occurred = parse_time(timing.get("occurred_at"))
        started = parse_time(timing.get("started_at"))
        ended = parse_time(timing.get("ended_at"))
        recorded = parse_time((event.get("provenance") or {}).get("recorded_at"))

        if scheduled is not None and started is not None and scheduled > started:
            errors.append(f"event_started_before_schedule:{event_id}")
        if scheduled is not None and occurred is not None and scheduled > occurred:
            errors.append(f"event_occurred_before_schedule:{event_id}")
        if started is not None and occurred is not None and started > occurred:
            errors.append(f"event_occurred_before_start:{event_id}")
        if started is not None and ended is not None and started > ended:
            errors.append(f"event_ended_before_start:{event_id}")
        if occurred is not None and ended is not None and occurred > ended:
            errors.append(f"event_ended_before_occurrence:{event_id}")

        status = event.get("status")
        if status == "scheduled" and recorded is not None and scheduled is not None and recorded > scheduled:
            errors.append(f"scheduled_event_recorded_after_due_time:{event_id}")
        if status == "active" and recorded is not None and started is not None and recorded < started:
            errors.append(f"active_event_recorded_before_start:{event_id}")
        if status in TERMINAL_STATUSES:
            last_material_time = max(
                value for value in (occurred, ended, started) if value is not None
            )
            if recorded is not None and recorded < last_material_time:
                errors.append(f"terminal_event_recorded_before_outcome:{event_id}")
            if not event.get("affected_owner_refs"):
                errors.append(f"terminal_event_without_affected_owner:{event_id}")
    return errors


def mutate(record: dict, case: dict) -> dict:
    result = copy.deepcopy(record)
    path = case["path"]
    target = result
    for key in path[:-1]:
        target = target[key]
    operation = case["operation"]
    if operation == "delete":
        target.pop(path[-1], None)
    elif operation in {"add", "replace"}:
        target[path[-1]] = copy.deepcopy(case.get("value"))
    else:
        raise ValueError(f"unknown fixture mutation operation: {operation}")
    return result


def main() -> int:
    schema = read_json(SCHEMA_PATH)
    template = read_json(TEMPLATE_PATH)
    fixtures = read_json(FIXTURE_PATH)
    failures = []

    template_index = read_json(ROOT / "runtime/contracts/template-index.json")
    template_shard_ref = (template_index.get("shards") or {}).get("w")
    template_shard = read_json(ROOT / template_shard_ref) if template_shard_ref else {}
    template_entry = (template_shard.get("templates") or {}).get("world-event-registry")
    if template_entry != {
        "path": "runtime/contracts/templates/world-event-registry.template.json",
        "source_schema": "game/schemas/world-event-registry.schema.json",
        "scope": "mutable_state",
    }:
        failures.append(f"world_event_template_index:{template_entry}")

    blank_index = read_json(ROOT / "runtime/contracts/blank-owner-index.json")
    blank_ref = (blank_index.get("owners") or {}).get("world-event-registry")
    if blank_ref != "runtime/contracts/blank-owners/world-event-registry.blank.json":
        failures.append(f"world_event_blank_index:{blank_ref}")
    elif read_json(ROOT / blank_ref).get("events") != []:
        failures.append("world_event_blank_events_not_empty")

    system_index = read_json(ROOT / "runtime/contracts/system-contract-index.json")
    contract_ref = (system_index.get("systems") or {}).get("world_state")
    contract = read_json(ROOT / contract_ref) if contract_ref else {}
    if "world-event-registry" not in contract.get("owner_templates", []):
        failures.append("world_event_missing_world_state_contract")
    if "tools/test_world_events.py" not in contract.get("validators", []):
        failures.append("world_event_validator_missing_from_contract")

    schema_registry = read_json(ROOT / "game/schemas/registry.json")
    if schema_registry.get("world-event-registry") != "world-event-registry.schema.json":
        failures.append("world_event_schema_registry")

    valid_records = fixtures.get("valid_records", [])
    if not valid_records:
        failures.append("no_valid_fixture_records_examined")
    records_by_id = {record.get("id"): record for record in valid_records}
    if len(records_by_id) != len(valid_records):
        failures.append("duplicate_valid_fixture_id")

    valid_registry = registry_with(copy.deepcopy(valid_records))
    valid_structural = structural_errors(valid_registry, schema, template)
    valid_semantic = semantic_errors(valid_registry) if not valid_structural else []
    if valid_structural:
        failures.append("valid_fixture_structural:" + " | ".join(valid_structural))
    if valid_semantic:
        failures.append("valid_fixture_semantic:" + " | ".join(valid_semantic))

    invalid_mutations = fixtures.get("invalid_mutations", [])
    if not invalid_mutations:
        failures.append("no_invalid_mutation_fixtures_examined")
    for case in invalid_mutations:
        base = records_by_id.get(case.get("base_record_id"))
        if base is None:
            failures.append(f"fixture_missing_base:{case.get('id')}:{case.get('base_record_id')}")
            continue
        candidate = registry_with([mutate(base, case)])
        structural = structural_errors(candidate, schema, template)
        semantic = semantic_errors(candidate) if not structural else []
        expected = case.get("expected_failure")
        if expected == "schema" and not structural:
            failures.append(f"invalid_fixture_not_rejected_by_schema:{case.get('id')}")
        elif expected == "semantic" and (structural or not semantic):
            failures.append(
                f"invalid_fixture_wrong_semantic_result:{case.get('id')}:"
                f"structural={structural}:semantic={semantic}"
            )

    invalid_registries = fixtures.get("invalid_registries", [])
    if not invalid_registries:
        failures.append("no_invalid_registry_fixtures_examined")
    for case in invalid_registries:
        records = [copy.deepcopy(records_by_id.get(record_id)) for record_id in case.get("record_ids", [])]
        if not records or any(record is None for record in records):
            failures.append(f"invalid_registry_missing_record:{case.get('id')}")
            continue
        candidate = registry_with(records)
        structural = structural_errors(candidate, schema, template)
        semantic = semantic_errors(candidate) if not structural else []
        if case.get("expected_failure") == "semantic" and (structural or not semantic):
            failures.append(
                f"invalid_registry_wrong_semantic_result:{case.get('id')}:"
                f"structural={structural}:semantic={semantic}"
            )

    current = read_json(REGISTRY_PATH)
    current_structural = structural_errors(current, schema, template)
    current_semantic = semantic_errors(current) if not current_structural else []
    if current_structural:
        failures.append("current_registry_structural:" + " | ".join(current_structural))
    if current_semantic:
        failures.append("current_registry_semantic:" + " | ".join(current_semantic))

    if failures:
        print(f"WORLD EVENT REGISTRY TEST FAILED {len(failures)}")
        for failure in failures:
            print("-", failure)
        return 1

    print("WORLD EVENT REGISTRY TEST OK")
    print(
        f"valid_fixtures={len(valid_records)} invalid_mutations={len(invalid_mutations)} "
        f"invalid_registries={len(invalid_registries)} current_events={len(current.get('events', []))} "
        f"jsonschema={'enabled' if jsonschema is not None else 'optional-not-installed'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
