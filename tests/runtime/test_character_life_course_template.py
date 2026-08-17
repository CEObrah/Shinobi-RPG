"""Structural regression for reducer-written exact-character life-course history."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.store.template_validation import RegisteredTemplateValidator

ROOT = Path(__file__).resolve().parents[2]


def test_status_history_accepts_canonical_career_history_strings() -> None:
    repo = RepositoryStore(ROOT)
    validator = RegisteredTemplateValidator(repo)
    value = repo.read_json("state/char/naruto.json")
    life = value.setdefault("life_course_state", {})
    history = life.setdefault("status_history", [])
    assert isinstance(history, list)
    history.append(
        "SE-0061-03-01T07:00:00: graduate: Genin: qualified Academy cycle"
    )

    validator._validate_document(
        value,
        validator.templates["shinobi_character"],
        label="state/char/naruto.json",
    )

    invalid = copy.deepcopy(value)
    invalid["life_course_state"]["status_history"].append({"not": "a string"})
    with pytest.raises(ValueError, match="array item type"):
        validator._validate_document(
            invalid,
            validator.templates["shinobi_character"],
            label="state/char/naruto.json",
        )
