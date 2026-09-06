from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_ooc_dev_workflow_runs_reaction_window_and_timing_regressions() -> None:
    workflow = (ROOT / ".github/workflows/ooc-dev-check.yml").read_text(encoding="utf-8")

    assert "tests/current/test_combat_reaction_window_causality.py" in workflow
    assert "tests/current/test_combat_reaction_timing_integrity.py" in workflow
    assert "tests/current/test_combat_defensive_movement_integrity.py" in workflow
    assert "Changed-owner regressions" in workflow
    assert "python tools/test_changed.py" in workflow


def test_changed_owner_gate_routes_combat_integrity_modules_to_combat_suite() -> None:
    source = (ROOT / "tools/test_changed.py").read_text(encoding="utf-8")

    assert 'path.startswith("runtime/shinobi_runtime/api/combat_")' in source
    assert 'path.startswith("runtime/shinobi_runtime/commands/combat_")' in source
    assert '"tests/current/test_combat_reaction_window_causality.py"' in source
    assert '"tests/current/test_combat_reaction_timing_integrity.py"' in source
