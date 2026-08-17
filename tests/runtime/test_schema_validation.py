from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("jsonschema")

from shinobi_runtime.store import RegisteredSchemaValidator, RepositoryStore


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


def make_validator(tmp_path: Path) -> RegisteredSchemaValidator:
    write_json(
        tmp_path / "game/schemas/registry.json",
        {"test-owner": "test-owner.schema.json"},
    )
    write_json(
        tmp_path / "game/schemas/test-owner.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["schema", "value"],
            "properties": {
                "schema": {"const": "test-owner"},
                "value": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
    )
    return RegisteredSchemaValidator(RepositoryStore(tmp_path))


def test_registered_schema_validator_checks_every_staged_schema_object(tmp_path: Path):
    validator = make_validator(tmp_path)
    validator.validate_overlay(
        FakeOverlay(
            {
                "state/owner.json": {
                    "schema": "test-owner",
                    "value": 3,
                }
            }
        ),
        ("state/owner.json",),
    )

    with pytest.raises(ValueError, match="schema validation failed"):
        validator.validate_overlay(
            FakeOverlay(
                {
                    "state/owner.json": {
                        "schema": "test-owner",
                        "value": -1,
                    }
                }
            ),
            ("state/owner.json",),
        )
    with pytest.raises(ValueError, match="unregistered schema"):
        validator.validate_overlay(
            FakeOverlay(
                {
                    "state/owner.json": {
                        "schema": "invented-owner",
                    }
                }
            ),
            ("state/owner.json",),
        )


def test_registered_schema_validator_requires_state_owner_schema(tmp_path: Path):
    validator = make_validator(tmp_path)

    with pytest.raises(ValueError, match="registered top-level schema"):
        validator.validate_overlay(
            FakeOverlay({"state/owner.json": {"value": 3}}),
            ("state/owner.json",),
        )

    # Structural metadata can contain schema-free JSON; the mandatory owner
    # envelope applies specifically to staged campaign-state files.
    validator.validate_overlay(
        FakeOverlay({"runtime/contracts/metadata.json": {"value": 3}}),
        ("runtime/contracts/metadata.json",),
    )


def test_registered_schema_validator_skips_deletions_and_non_json(tmp_path: Path):
    validator = make_validator(tmp_path)
    validator.validate_overlay(
        FakeOverlay({"state/deleted.json": None, "state/note.txt": {"bad": True}}),
        ("state/deleted.json", "state/note.txt"),
    )
