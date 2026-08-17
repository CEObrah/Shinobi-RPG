"""Regression coverage for narration fallback after temporal scene handoff cleanup."""

from pathlib import Path
import json

import pytest

from shinobi_runtime.narration import select_narration_modules


ROOT = Path(__file__).resolve().parents[2]


def _router():
    return json.loads((ROOT / "runtime/contracts/narration-router.json").read_text(encoding="utf-8"))


def test_missing_scene_type_uses_declared_default_primary() -> None:
    selection = select_narration_modules(_router(), scene_type=None)
    assert selection.primary_id == _router()["default_primary"]
    assert selection.scene_type_matched is False
    assert selection.secondary_id is None


def test_missing_scene_type_still_allows_exact_pressure_override() -> None:
    selection = select_narration_modules(
        _router(),
        scene_type=None,
        pressures=("battlefield_command",),
    )
    assert selection.primary_id == "command_large_war"
    assert selection.secondary_id == _router()["default_primary"]
    assert selection.scene_type_matched is False


def test_explicit_blank_scene_type_remains_invalid() -> None:
    with pytest.raises(ValueError):
        select_narration_modules(_router(), scene_type="")
