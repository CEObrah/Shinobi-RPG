from __future__ import annotations

import copy
import json
from pathlib import Path

from shinobi_runtime.combat.capabilities import project_component, select_method

ROOT = Path(__file__).resolve().parents[2]


def _mechanics() -> dict:
    return json.loads((ROOT / "game/data/mechanics/formation-resolution.json").read_text())


def _iron_components() -> dict[str, dict]:
    record = json.loads((ROOT / "state/formation/force-iron-samurai.json").read_text())
    formation = next(row for row in record["formations"] if row["id"] == "formation.iron.samurai.1")
    return {row["role"]: row for row in formation["components"]}


def test_land_of_iron_sword_specialization_is_explicit_and_close_range_specific() -> None:
    mechanics = _mechanics()
    assault = _iron_components()["assault"]
    state = assault["capability_state"]
    assert state["methods"]["sword"] > state["fundamentals"]["combat"]
    assert "sword" in state["equipment_methods"]

    close_profile, _spread, _initiative, close_method = project_component(
        state, role="assault", action="attack", range_band=0, mechanics=mechanics
    )
    assert close_method == "sword"

    no_sword = copy.deepcopy(state)
    no_sword["methods"]["sword"] = 0
    degraded_profile, _spread, _initiative, degraded_method = project_component(
        no_sword, role="assault", action="attack", range_band=0, mechanics=mechanics
    )
    assert degraded_method != "sword"
    assert close_profile.offense > degraded_profile.offense

    # Sword skill does not leak into a long-range universal power bonus.
    long_profile, _spread, _initiative, long_method = project_component(
        state, role="assault", action="attack", range_band=3, mechanics=mechanics
    )
    no_sword_long, _spread, _initiative, no_sword_long_method = project_component(
        no_sword, role="assault", action="attack", range_band=3, mechanics=mechanics
    )
    assert long_method == no_sword_long_method
    assert long_profile == no_sword_long


def test_iron_archers_use_bow_without_turning_medics_or_swordsmen_into_archers() -> None:
    mechanics = _mechanics()
    components = _iron_components()
    ranged = components["ranged_control"]["capability_state"]
    assault = components["assault"]["capability_state"]
    method, score = select_method(ranged, role="ranged_control", range_band=2, mechanics=mechanics)
    assert method == "bow"
    assert score > 0
    assault_method, _score = select_method(assault, role="assault", range_band=0, mechanics=mechanics)
    assert assault_method == "sword"


def test_noncombat_unknown_fields_do_not_change_combat_projection() -> None:
    mechanics = _mechanics()
    state = _iron_components()["assault"]["capability_state"]
    baseline = project_component(state, role="assault", action="attack", range_band=0, mechanics=mechanics)
    altered = copy.deepcopy(state)
    altered["administration"] = 200
    altered["merchant_accounting"] = 200
    assert project_component(altered, role="assault", action="attack", range_band=0, mechanics=mechanics) == baseline


def test_equipment_readiness_changes_only_equipment_dependent_method_expression() -> None:
    mechanics = _mechanics()
    state = _iron_components()["assault"]["capability_state"]
    full, *_ = project_component(state, role="assault", action="attack", range_band=0, mechanics=mechanics)
    damaged = copy.deepcopy(state)
    damaged["equipment_readiness_milli"] = 500
    reduced, *_ = project_component(damaged, role="assault", action="attack", range_band=0, mechanics=mechanics)
    assert reduced.offense < full.offense
    assert damaged["fundamentals"] == state["fundamentals"]
    assert damaged["methods"] == state["methods"]
