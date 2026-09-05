from __future__ import annotations

import copy

from shinobi_runtime.api.combat_hardening import install_combat_simulation_hardening
from shinobi_runtime.people import repository as people_repository


def test_person_sheet_resolver_uses_live_hardened_health_projection(monkeypatch):
    """The public person-sheet projection must not retain a pre-install function alias."""

    wound = {
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
        "functional_effects": {},
        "function_loss_pct": 0,
        "pain": 200,
        "treated": False,
    }
    person = {
        "person_id": "mw.person.house_tang.test",
        "faction_ref": None,
        "health": {"injuries": [wound]},
    }

    monkeypatch.setattr(
        people_repository,
        "roster_person",
        lambda _repository, _person_id: ("ignored", {}, 0, copy.deepcopy(person)),
    )
    resolver = people_repository.RepositoryPersonSheetResolver(object())
    monkeypatch.setattr(
        resolver,
        "_project_institutional_training",
        lambda row: copy.deepcopy(dict(row)),
    )
    monkeypatch.setattr(resolver, "_standing_retinues_for_player", lambda _person_id: [])

    install_combat_simulation_hardening()
    sheet = resolver("mw.person.house_tang.test")

    assert sheet is not None
    penalties = sheet["derived_condition"]["functional_penalties"]
    assert max(penalties["leg"], penalties["footwork"]) > 0
    # The legacy wound has no lawful left/right side; the fallback must not invent one.
    assert penalties["leg_left"] == 0
    assert penalties["leg_right"] == 0
    assert penalties["footwork_left"] == 0
    assert penalties["footwork_right"] == 0
