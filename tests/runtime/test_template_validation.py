from __future__ import annotations

import json
from pathlib import Path

import pytest

from shinobi_runtime.store import RegisteredTemplateValidator, RepositoryStore
from shinobi_runtime.store.template_validation import TemplateValidationError


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


class FakeOverlay:
    def __init__(self, values):
        self.values = values

    def read_optional_bytes(self, path):
        value = self.values.get(path)
        return None if value is None else json.dumps(value).encode("utf-8")

    def read_json(self, path):
        return self.values[path]


def make_validator(tmp_path: Path) -> RegisteredTemplateValidator:
    write_json(
        tmp_path / "runtime/contracts/template-index.json",
        {
            "schema": "template-index",
            "shards": {"t": "runtime/contracts/template-index-shards/t.json"},
        },
    )
    write_json(
        tmp_path / "runtime/contracts/template-index-shards/t.json",
        {
            "schema": "template-index-shard",
            "prefix": "t",
            "templates": {
                "test-owner": {
                    "path": "runtime/contracts/templates/test-owner.template.json",
                    "scope": "mutable_state",
                }
            },
        },
    )
    write_json(
        tmp_path / "runtime/contracts/templates/test-owner.template.json",
        {
            "schema": "file-template",
            "target_schema": "test-owner",
            "scope": "mutable_state",
            "unknown_key_policy": "reject",
            "required_top_level_keys": ["schema", "values"],
            "object_contracts": {
                "": {
                    "mode": "closed",
                    "allowed_keys": ["schema", "values"],
                },
                "/values/*": {
                    "mode": "closed",
                    "allowed_keys": ["name"],
                },
            },
            "type_contracts": {
                "/schema": ["string"],
                "/values": ["array"],
                "/values/*": ["object"],
                "/values/*/name": ["string"],
            },
            "array_contracts": {"/values": {"item_types": ["object"]}},
        },
    )
    return RegisteredTemplateValidator(RepositoryStore(tmp_path))


def test_registered_template_validator_accepts_exact_owner_shape(tmp_path: Path):
    validator = make_validator(tmp_path)
    validator.validate_overlay(
        FakeOverlay(
            {
                "state/test.json": {
                    "schema": "test-owner",
                    "values": [{"name": "exact"}],
                }
            }
        ),
        ("state/test.json",),
    )


@pytest.mark.parametrize(
    "owner, message, schema_id, reason",
    (
        (
            {"schema": "test-owner", "values": [], "invented": True},
            "unregistered keys",
            "test-owner",
            "unregistered_keys",
        ),
        (
            {"schema": "test-owner", "values": [3]},
            "array item type",
            "test-owner",
            "array_item_type",
        ),
        (
            {"schema": "missing-owner", "values": []},
            "no structural template",
            "missing-owner",
            "missing_template",
        ),
        (
            {"schema": "test-owner"},
            "missing required structural key",
            "test-owner",
            "missing_required_key",
        ),
    ),
)
def test_registered_template_validator_rejects_shape_drift_with_safe_metadata(
    tmp_path: Path,
    owner,
    message: str,
    schema_id: str,
    reason: str,
):
    validator = make_validator(tmp_path)

    with pytest.raises(TemplateValidationError, match=message) as caught:
        validator.validate_overlay(
            FakeOverlay({"state/test.json": owner}),
            ("state/test.json",),
        )

    assert isinstance(caught.value, ValueError)
    assert caught.value.schema_id == schema_id
    assert caught.value.reason == reason


def test_registered_template_validator_classifies_missing_template_id(tmp_path: Path):
    validator = make_validator(tmp_path)

    with pytest.raises(TemplateValidationError) as caught:
        validator.validate_overlay(
            FakeOverlay({"state/test.json": {"values": []}}),
            ("state/test.json",),
        )

    assert caught.value.schema_id is None
    assert caught.value.reason == "missing_template_id"


def test_registered_template_validator_skips_deletion(tmp_path: Path):
    validator = make_validator(tmp_path)
    validator.validate_overlay(
        FakeOverlay({"state/deleted.json": None}),
        ("state/deleted.json",),
    )
