import json
from pathlib import Path

from shinobi_runtime.commands.development_breakthrough import DevelopmentBreakthroughMixin
from shinobi_runtime.commands.specs import COMMAND_SPECS


ROOT = Path(__file__).resolve().parents[2]


def test_breakthrough_command_is_public_and_exact() -> None:
    spec = COMMAND_SPECS["breakthrough_resolution"]
    assert spec.required_fields == ("subject_ref", "target", "evidence_event_ref", "summary")
    assert spec.optional_fields == ()


def test_breakthrough_rank_escalation_is_stepwise() -> None:
    required = DevelopmentBreakthroughMixin._breakthrough_required_rank
    assert required(160) == "B"
    assert required(169) == "B"
    assert required(170) == "A"
    assert required(179) == "A"
    assert required(180) == "S"
    assert required(184) == "S"


def test_breakthrough_ledger_is_optional_and_string_typed() -> None:
    schema = json.loads((ROOT / "game/schemas/development-bank-registry.schema.json").read_text())
    entry = schema["properties"]["entries"]["additionalProperties"]
    assert "breakthrough_event_refs" not in entry["required"]
    refs = entry["properties"]["breakthrough_event_refs"]
    assert refs["items"]["type"] == "string"
    assert refs["uniqueItems"] is True


def test_training_policy_reserves_transcendent_progression_for_specific_systems() -> None:
    mechanics = json.loads((ROOT / "game/data/mechanics/training.json").read_text())
    thresholds = mechanics["progression_thresholds"]
    assert thresholds["routine_training_ceiling"] == 160
    assert thresholds["generic_breakthrough_ceiling"] == 185
    rules = mechanics["breakthrough_rules"]
    assert rules["points_per_resolution"] == 1
    assert rules["180_to_184"].startswith("S-rank")
    assert rules["185_plus"].startswith("requires a specific exceptional subsystem")
