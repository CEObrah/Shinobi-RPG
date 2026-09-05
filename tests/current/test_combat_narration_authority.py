from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REFERENCE_DIR = ROOT / "plugins/shinobi-rpg/skill/shinobi-game-master/references"
COMBAT_ENTRY = REFERENCE_DIR / "combat.md"
COMBAT_BASE = REFERENCE_DIR / "combat-base.md"


def test_combat_entry_requires_full_base_contract_before_incident_overrides():
    entry = COMBAT_ENTRY.read_text(encoding="utf-8")
    base = COMBAT_BASE.read_text(encoding="utf-8")

    assert "Immediately read `references/combat-base.md` in full" in entry
    assert "# Exact Combat" in base
    assert "## Spatial authority" in base
    assert "## Standing orders and player control" in base


def test_combat_narrative_material_beats_outrank_compatibility_projection():
    entry = COMBAT_ENTRY.read_text(encoding="utf-8")

    assert "`combat_narrative`" in entry
    assert "`material_beats`" in entry
    assert "primary scene spine" in entry
    assert "`narrative_projection` is a secondary compatibility/fallback surface" in entry
    assert "must never replace, reorder, or erase material beats" in entry


def test_exchange_boundary_and_repeated_failure_do_not_manufacture_player_menu():
    entry = COMBAT_ENTRY.read_text(encoding="utf-8")

    assert "not a player-decision boundary" in entry
    assert "Repeated mechanically similar exchanges must be compressed" in entry
    assert "Do not create a \"sharper choice\" merely because a tactic is currently ineffective" in entry
    assert "Mechanical frustration is not itself a protected decision" in entry
