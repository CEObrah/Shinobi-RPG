import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_autonomous_team_training_defaults_are_bounded_and_rotating() -> None:
    mechanics = json.loads((ROOT / "game/data/mechanics/training.json").read_text())
    profiles = mechanics["autonomous_team_training"]
    assert profiles["standard_mission_team"]["active_hours_per_week"] == 8
    assert profiles["special_mission_cell"]["active_hours_per_week"] == 12
    for profile in profiles.values():
        assert 0 < profile["active_hours_per_week"] <= 48
        assert len(profile["target_cycle"]) >= 3
        assert len(profile["target_cycle"]) == len(set(profile["target_cycle"]))


def test_legendary_and_god_tier_bands_are_not_routine_training_targets() -> None:
    mechanics = json.loads((ROOT / "game/data/mechanics/training.json").read_text())
    thresholds = mechanics["progression_thresholds"]
    assert thresholds["routine_training_ceiling"] == 160
    assert thresholds["legendary_band_starts"] == 170
    assert thresholds["transcendent_band_starts"] == 185
    assert thresholds["god_tier_starts"] == 200
