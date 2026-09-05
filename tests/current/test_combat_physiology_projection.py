from __future__ import annotations

import copy
from pathlib import Path

from shinobi_runtime.api.combat_hardening import legacy_safe_functional_penalties
from shinobi_runtime.api.physiology_projection import (
    project_person_sheet_functional_penalties,
)
from shinobi_runtime.martial_world import health


ROOT = Path(__file__).resolve().parents[2]


def _legacy_unsided_knee_sheet() -> dict:
    return {
        "person_id": "mw.person.house_tang.test",
        "view": "player_visible_identity",
        "sheet": {
            "health": {
                "status": "incapacitated",
                "injuries": [
                    {
                        "zone": "knee",
                        "structure_ref": None,
                        "side": None,
                        "cut": 14,
                        "pierce": 200,
                        "blunt": 158,
                        "penetration": 198,
                        "severity": 200,
                        "bleeding_ml_per_min": 52,
                        "fracture": 0,
                        "tendon_damage": 132,
                        "nerve_damage": 127,
                        "organ_trauma": 0,
                        "function_loss_pct": 0,
                    }
                ],
            },
            "derived_condition": {
                "functional_penalties": {
                    "leg": 0,
                    "footwork": 0,
                    "leg_left": 0,
                    "leg_right": 0,
                    "footwork_left": 0,
                    "footwork_right": 0,
                }
            },
        },
    }


def test_legacy_unsided_knee_penalty_is_visible_without_mutating_wound(monkeypatch):
    raw = _legacy_unsided_knee_sheet()
    before = copy.deepcopy(raw)
    base = health.functional_penalties
    monkeypatch.setattr(
        health,
        "functional_penalties",
        lambda wounds: legacy_safe_functional_penalties(base, wounds),
    )

    projected = project_person_sheet_functional_penalties(raw)
    penalties = projected["sheet"]["derived_condition"]["functional_penalties"]

    assert penalties["leg"] > 0
    assert penalties["footwork"] > 0
    assert penalties["leg_left"] == 0
    assert penalties["leg_right"] == 0
    assert penalties["footwork_left"] == 0
    assert penalties["footwork_right"] == 0
    assert raw == before
    assert projected["sheet"]["health"]["injuries"][0]["function_loss_pct"] == 0


def test_projection_replaces_only_derived_penalty_view(monkeypatch):
    raw = _legacy_unsided_knee_sheet()
    expected = {
        "leg": 77,
        "footwork": 77,
        "leg_left": 0,
        "leg_right": 0,
        "footwork_left": 0,
        "footwork_right": 0,
    }
    monkeypatch.setattr(health, "functional_penalties", lambda wounds: dict(expected))

    projected = project_person_sheet_functional_penalties(raw)

    assert projected["sheet"]["derived_condition"]["functional_penalties"] == expected
    assert projected["sheet"]["health"] == raw["sheet"]["health"]
    assert projected["view"] == raw["view"]
    assert projected["person_id"] == raw["person_id"]


def test_production_bootstrap_composes_physio_projection():
    text = (ROOT / "runtime/shinobi_runtime/api/campaign_entrypoint.py").read_text(
        encoding="utf-8"
    )
    assert "PhysiologyProjectedCampaignOperations" in text
    assert "class RouteReconciledCampaignOperations(PhysiologyProjectedCampaignOperations)" in text
