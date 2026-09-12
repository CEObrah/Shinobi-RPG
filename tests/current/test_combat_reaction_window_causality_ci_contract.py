from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_ooc_dev_workflow_runs_reaction_window_and_timing_regressions() -> None:
    workflow_path = ROOT / ".github/workflows/ooc-dev-check.yml"
    if workflow_path.is_file():
        workflow = workflow_path.read_text(encoding="utf-8")
        assert "tests/current/test_combat_gameplay_certification.py" in workflow
        assert "tools/combat_balance_lab.py" in workflow
        assert "tests/current/test_combat_reaction_window_causality.py" in workflow
        assert "tests/current/test_combat_reaction_timing_integrity.py" in workflow
        assert "tests/current/test_combat_defensive_movement_integrity.py" in workflow
        assert "tests/current/test_combat_tactical_movement_integrity.py" in workflow
        assert "tests/current/test_combat_tactical_movement_blocked.py" in workflow
        assert "Changed-owner regressions" in workflow
        assert "python tools/test_changed.py" in workflow
        return

    # Local/uploaded release ZIPs intentionally exclude GitHub-only workflow
    # metadata.  The packaged release gate must still carry the same mandatory
    # behavioral certification and changed-owner routing rather than making a
    # GitHub file an accidental runtime/package dependency.
    release = (ROOT / "tools/verify_release.sh").read_text(encoding="utf-8")
    changed = (ROOT / "tools/test_changed.py").read_text(encoding="utf-8")
    assert "python tools/combat_balance_lab.py" in release
    assert "python tools/test_changed.py" in release
    assert "python tools/run_pytest_shards.py" in release
    assert '"tests/current/test_combat_gameplay_certification.py"' in changed
    assert '"tests/current/test_combat_reaction_window_causality.py"' in changed
    assert '"tests/current/test_combat_reaction_timing_integrity.py"' in changed
    assert '"tests/current/test_combat_tactical_movement_integrity.py"' in changed
    assert '"tests/current/test_combat_tactical_movement_blocked.py"' in changed


def test_changed_owner_gate_routes_combat_integrity_modules_to_combat_suite() -> None:
    source = (ROOT / "tools/test_changed.py").read_text(encoding="utf-8")

    assert 'path.startswith("runtime/shinobi_runtime/api/combat_")' in source
    assert 'path.startswith("runtime/shinobi_runtime/commands/combat_")' in source
    assert '"tests/current/test_combat_gameplay_certification.py"' in source
    assert '"tests/current/test_combat_reaction_window_causality.py"' in source
    assert '"tests/current/test_combat_reaction_timing_integrity.py"' in source
    assert '"tests/current/test_combat_tactical_movement_integrity.py"' in source
    assert '"tests/current/test_combat_tactical_movement_blocked.py"' in source
