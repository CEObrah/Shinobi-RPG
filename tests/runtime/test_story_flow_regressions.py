from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from shinobi_runtime.api.contracts import CommandPlan
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.api import preview_validation


ROOT = Path(__file__).resolve().parents[2]


def test_scene_contract_allows_null_only_for_absent_player_decision():
    schema = json.loads((ROOT / "game/schemas/scene.schema.json").read_text(encoding="utf-8"))
    template = json.loads(
        (ROOT / "runtime/contracts/templates/scene.template.json").read_text(encoding="utf-8")
    )

    assert schema["properties"]["decision_required"]["type"] == ["null", "string"]
    assert template["type_contracts"]["/decision_required"] == ["null", "string"]

    temporal = json.loads(
        (ROOT / "runtime/contracts/temporal-settlement.json").read_text(encoding="utf-8")
    )
    assert temporal["frontier_invariants"]["internal_boundary_never_sets_decision_required"] is True
    assert temporal["frontier_invariants"]["decision_required_means_hard_player_decision_only"] is True


def _plan(validator):
    return CommandPlan(
        transaction_id="tx.gameplay.preview-test",
        created_at="2026-08-13T16:00:00Z",
        writes={"state/meta.json": b"{}"},
        result={},
        validator=validator,
    )


class _Validator:
    def __init__(self, calls, label, error=None):
        self.calls = calls
        self.label = label
        self.error = error

    def validate_overlay(self, overlay, paths):
        self.calls.append(self.label)
        if self.error is not None:
            raise self.error


class _Operations:
    def __init__(self, plan, *, schema_error=None, template_error=None):
        self.calls = []
        self.repository = object()
        self.command_planner = SimpleNamespace(plan=lambda command: plan)
        self.schema_validator = _Validator(self.calls, "schema", schema_error)
        self.template_validator = _Validator(self.calls, "template", template_error)
        self.coordinator = SimpleNamespace(
            git=SimpleNamespace(assert_pristine=lambda: self.calls.append("pristine")),
            planner=SimpleNamespace(plan=lambda command, **kwargs: SimpleNamespace(paths=("state/meta.json",))),
        )

    def _locked(self):
        return nullcontext()

    def _require_command_base(self, command):
        self.calls.append("base")

    def _read_fingerprint(self):
        return ("head", "root")

    def _require_read_only(self, before, code):
        self.calls.append(code)


def test_ready_preview_dry_runs_schema_template_and_domain_validator(monkeypatch):
    calls = []

    def domain_validator(overlay, manifest):
        calls.append("domain")

    operations = _Operations(_plan(domain_validator))
    monkeypatch.setattr(preview_validation, "StagedOverlay", lambda repository, manifest: object())

    preview_validation._validate_ready_plan(operations, object())

    assert operations.calls == [
        "base",
        "pristine",
        "planner_mutated_campaign",
        "schema",
        "template",
        "preview_validation_mutated_campaign",
    ]
    assert calls == ["domain"]


def test_ready_preview_rejects_schema_invalid_after_image_before_attestation(monkeypatch):
    operations = _Operations(
        _plan(lambda overlay, manifest: None),
        schema_error=ValueError("schema validation failed"),
    )
    monkeypatch.setattr(preview_validation, "StagedOverlay", lambda repository, manifest: object())

    with pytest.raises(OperationError) as captured:
        preview_validation._validate_ready_plan(operations, object())

    assert captured.value.status_code == 409
    assert captured.value.code == "preview_schema_validation_failed"
    assert "template" not in operations.calls


def test_ready_preview_rejects_template_invalid_after_image_before_attestation(monkeypatch):
    operations = _Operations(
        _plan(lambda overlay, manifest: None),
        template_error=ValueError("template validation failed"),
    )
    monkeypatch.setattr(preview_validation, "StagedOverlay", lambda repository, manifest: object())

    with pytest.raises(OperationError) as captured:
        preview_validation._validate_ready_plan(operations, object())

    assert captured.value.status_code == 409
    assert captured.value.code == "preview_template_validation_failed"


def test_ready_preview_rejects_domain_invalid_after_image_before_attestation(monkeypatch):
    def invalid_domain(overlay, manifest):
        raise ValueError("domain after-image mismatch")

    operations = _Operations(_plan(invalid_domain))
    monkeypatch.setattr(preview_validation, "StagedOverlay", lambda repository, manifest: object())

    with pytest.raises(OperationError) as captured:
        preview_validation._validate_ready_plan(operations, object())

    assert captured.value.status_code == 409
    assert captured.value.code == "preview_plan_validation_failed"
