import json
from pathlib import Path

import pytest

from shinobi_runtime.commands.domains.missions import (
    _world_event_execution_has_durable_provenance,
)
from shinobi_runtime.store.template_validation import RegisteredTemplateValidator

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parents[2]


def _event(execution: dict) -> dict:
    return {
        "id": "event.test.rebaseline",
        "kind": "test_event",
        "status": "resolved",
        "timing": {
            "scheduled_for": "SE-0061-07-01T07:00:00",
            "occurred_at": "SE-0061-07-01T07:00:00",
            "started_at": "SE-0061-07-01T07:00:00",
            "ended_at": "SE-0061-07-01T07:00:00",
        },
        "host_refs": ["institution.test"],
        "actor_refs": [],
        "place_refs": [],
        "causal_refs": [],
        "affected_owner_refs": [],
        "material_consequence_refs": ["baseline:test"],
        "visibility": {
            "classification": "restricted",
            "witness_refs": [],
            "audience_refs": [],
            "knowledge_refs": [],
            "route_refs": [],
        },
        "provenance": {
            "source_kind": "test",
            "source_refs": ["institution.test"],
            "archetype_ref": None,
            "recorded_at": "SE-0061-07-01T07:00:00",
        },
        "execution": execution,
        "supersedes_ref": None,
        "superseded_by_ref": None,
    }


def test_rebaselined_archive_event_accepts_compact_baseline_provenance():
    schema = json.loads(
        (ROOT / "game/schemas/world-event-archive.schema.json").read_text(encoding="utf-8")
    )
    template = json.loads(
        (ROOT / "runtime/contracts/templates/world-event-archive.template.json").read_text(
            encoding="utf-8"
        )
    )
    owner = {
        "schema": "world-event-archive",
        "owner_id": "history.events.999999",
        "owner_type": "world_event_archive",
        "segment_index": 999999,
        "created_at": "SE-0061-07-01T07:00:00",
        "event_count": 1,
        "events": [
            _event(
                {
                    "reducer_ref": "shinobi_runtime.test",
                    "baseline_ref": "baseline.rebaseline.1",
                }
            )
        ],
    }

    jsonschema.Draft202012Validator(schema).validate(owner)
    RegisteredTemplateValidator._validate_document(owner, template, label="rebaseline fixture")


def test_world_event_evidence_accepts_baseline_or_transaction_provenance_only():
    assert _world_event_execution_has_durable_provenance(
        {"reducer_ref": "x", "baseline_ref": "baseline.rebaseline.1"}
    )
    assert _world_event_execution_has_durable_provenance(
        {
            "reducer_ref": "x",
            "transaction_ref": "tx.gameplay.abc",
            "receipt_refs": ["receipt.gameplay.abc"],
        }
    )
    assert not _world_event_execution_has_durable_provenance({"reducer_ref": "x"})
    assert not _world_event_execution_has_durable_provenance(
        {"reducer_ref": "x", "transaction_ref": "tx.gameplay.abc", "receipt_refs": []}
    )
