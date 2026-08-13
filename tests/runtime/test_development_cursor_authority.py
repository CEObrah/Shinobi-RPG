"""Regression tests for single-authority exact-character development cursors."""
from __future__ import annotations

import copy
import json
from typing import Any, Mapping

from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.commands.core import _json_bytes
from shinobi_runtime.commands.development_cursor_authority import DevelopmentCursorAuthorityMixin
from shinobi_runtime.commands.paths import DEVELOPMENT_BANK_PATH


OWNER_REF = "char.cursor_fixture"
OWNER_PATH = "state/char/cursor-fixture.json"


class _FixtureRepository:
    def __init__(self, records: Mapping[str, Any]) -> None:
        self.records = copy.deepcopy(dict(records))

    def read_json(self, path: str):
        return copy.deepcopy(self.records[path])


class _TerminalPruner:
    def _prune_noop_writes(self, writes):
        return dict(writes)


class _CursorFixturePlanner(DevelopmentCursorAuthorityMixin, _TerminalPruner):
    def __init__(self, repository: _FixtureRepository) -> None:
        self.repository = repository

    def _resolve_covered_owner_view(self, owner_ref: str, *, cache):
        assert owner_ref == OWNER_REF
        return OWNER_PATH, "fixture-digest", self.repository.read_json(OWNER_PATH)


def _fixture():
    character = {
        "schema": "shinobi_character",
        "owner_id": OWNER_REF,
        "owner_type": "character",
        "development": {
            "current_training": [],
            "last_settled_at": "SE-0061-01-01T00:00:00",
            "weighted_adaptation": None,
        },
        "operational_skills": {"leadership": 50},
    }
    bank = {
        "schema": "development-bank-registry",
        "entries": {
            OWNER_REF: {
                "owner_type": "character",
                "resolved_through": "SE-0061-01-01T00:00:00",
                "credits": {"operational_skills.leadership": 0},
            }
        },
    }
    return character, bank


def test_changed_character_bank_entry_retires_legacy_character_cursor() -> None:
    character, before_bank = _fixture()
    repo = _FixtureRepository({OWNER_PATH: character, DEVELOPMENT_BANK_PATH: before_bank})
    after_bank = copy.deepcopy(before_bank)
    after_bank["entries"][OWNER_REF]["resolved_through"] = "SE-0061-01-02T00:00:00"
    after_bank["entries"][OWNER_REF]["credits"]["operational_skills.leadership"] = 1.25

    writes = _CursorFixturePlanner(repo)._prune_noop_writes(
        {DEVELOPMENT_BANK_PATH: _json_bytes(after_bank)}
    )

    assert OWNER_PATH in writes
    after_character = json.loads(writes[OWNER_PATH].decode("utf-8"))
    assert "last_settled_at" not in after_character["development"]
    assert after_character["operational_skills"]["leadership"] == 50
    assert json.loads(writes[DEVELOPMENT_BANK_PATH].decode("utf-8")) == after_bank


def test_existing_character_mutation_is_preserved_while_legacy_cursor_is_removed() -> None:
    character, before_bank = _fixture()
    repo = _FixtureRepository({OWNER_PATH: character, DEVELOPMENT_BANK_PATH: before_bank})
    after_bank = copy.deepcopy(before_bank)
    after_bank["entries"][OWNER_REF]["resolved_through"] = "SE-0061-01-02T00:00:00"
    changed_character = copy.deepcopy(character)
    changed_character["operational_skills"]["leadership"] = 51

    writes = _CursorFixturePlanner(repo)._prune_noop_writes(
        {
            DEVELOPMENT_BANK_PATH: _json_bytes(after_bank),
            OWNER_PATH: _json_bytes(changed_character),
        }
    )

    after_character = json.loads(writes[OWNER_PATH].decode("utf-8"))
    assert after_character["operational_skills"]["leadership"] == 51
    assert "last_settled_at" not in after_character["development"]


def test_unchanged_bank_does_not_expand_the_write_set() -> None:
    character, bank = _fixture()
    repo = _FixtureRepository({OWNER_PATH: character, DEVELOPMENT_BANK_PATH: bank})
    writes = _CursorFixturePlanner(repo)._prune_noop_writes(
        {DEVELOPMENT_BANK_PATH: _json_bytes(bank)}
    )
    assert OWNER_PATH not in writes


def test_production_planner_composes_cursor_authority_guard() -> None:
    assert issubclass(CampaignCommandPlanner, DevelopmentCursorAuthorityMixin)
