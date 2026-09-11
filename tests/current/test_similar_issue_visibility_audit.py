from __future__ import annotations

import copy
import json
from pathlib import Path

import shinobi_runtime.martial_world.combat_simulation as simulation
import shinobi_runtime.martial_world.exact_combat as exact
from shinobi_runtime.commands import combat_span_safety as safety

ROOT = Path(__file__).resolve().parents[2]


def _base_person(ref: str, faction: str, *, doctrine: str | None = None) -> dict:
    base = json.loads((ROOT / "state/martial-world/people/house_tang.json").read_text())["people"][0]
    row = copy.deepcopy(base)
    row["person_id"] = ref
    row["faction_ref"] = faction
    row["health"] = {
        "status": "ready", "injuries": [], "blood_lost_ml": 0,
        "shock": 0, "consciousness": 100,
    }
    row["fatigue_milli"] = 0
    if doctrine is not None:
        row["combat_doctrine_ref"] = doctrine
    else:
        row.pop("combat_doctrine_ref", None)
    return row


def _ledger(*refs: str) -> dict:
    return {
        "schema": "jianghu-equipment-ledger-1.0",
        "policy_assignments": {},
        "person_loadouts": {ref: {"items": {}, "condition_milli": {}} for ref in refs},
    }


def _wall() -> dict:
    return {
        "obstacle_ref": "wall", "shape": "rectangle",
        "min_x_mm": 1_000, "max_x_mm": 2_000,
        "min_y_mm": -900, "max_y_mm": 900,
        "height_mm": 3_000, "blocks_los": True, "blocks_movement": True,
    }


def test_rapid_lethal_override_cannot_select_remembered_enemy_now_hidden_by_los():
    people = {
        "wei": _base_person("wei", "a", doctrine="doctrine.tang_wei.precision_function_denial.lethal_pursuit"),
        "hidden": _base_person("hidden", "b"),
        "visible": _base_person("visible", "b"),
    }
    combat = exact.initialize_combat(
        combat_ref="visibility-rapid", side_a_refs=["wei"], side_b_refs=["hidden", "visible"],
        people=people, zone_ref="z", started_at="x", objective={"kind": "eliminate"},
        equipment_ledger=_ledger(*people), obstacles=[_wall()],
    )
    combat["positions"]["wei"].update(x_mm=0, y_mm=0)
    combat["positions"]["hidden"].update(x_mm=3_000, y_mm=0)
    combat["positions"]["visible"].update(x_mm=0, y_mm=8_000)
    combat["combatants"]["wei"]["observed_refs"] = ["hidden", "visible"]

    assert exact.currently_visible_enemies(
        combat, actor_ref="wei", enemy_refs=["hidden", "visible"], people=people,
    ) == ["visible"]

    selected = safety.rapid_lethal_target_for(
        lambda **_kwargs: "visible",
        combat=combat, people=people, actor_ref="wei", martial_familiarity={},
    )
    assert selected == "visible"


def test_narrative_projection_uses_current_visibility_not_cumulative_enemy_memory():
    people = {
        "pc": _base_person("pc", "a"),
        "hidden": _base_person("hidden", "b"),
        "visible": _base_person("visible", "b"),
    }
    combat = exact.initialize_combat(
        combat_ref="visibility-projection", side_a_refs=["pc"], side_b_refs=["hidden", "visible"],
        people=people, zone_ref="z", started_at="x", objective={"kind": "eliminate"},
        equipment_ledger=_ledger(*people), obstacles=[_wall()],
    )
    combat["positions"]["pc"].update(x_mm=0, y_mm=0)
    combat["positions"]["hidden"].update(x_mm=3_000, y_mm=0)
    combat["positions"]["visible"].update(x_mm=0, y_mm=8_000)
    combat["combatants"]["pc"]["observed_refs"] = ["hidden", "visible"]
    after = copy.deepcopy(combat)

    projection = exact._combat_narrative_projection(
        combat_before=combat, combat_after=after,
        people_before=people, people_after=copy.deepcopy(people),
        events=[
            {"actor_ref": "hidden", "result": "withdrawal_declared", "declared_at_ms": 10},
            {"actor_ref": "visible", "result": "withdrawal_declared", "declared_at_ms": 20},
        ],
        player_ref="pc",
        combat_information={"visible_hostiles_current": 1, "observed_combat_capable_remaining": 1},
    )
    actors = [row.get("actor_ref") for row in projection["beats"]]
    assert "visible" in actors
    assert "hidden" not in actors


def test_autonomous_combat_selects_only_currently_visible_targets(monkeypatch):
    people = {
        "driver": _base_person("driver", "a"),
        "hidden": _base_person("hidden", "b"),
        "visible": _base_person("visible", "b"),
    }
    ledger = _ledger(*people)
    combat = {
        "status": "active", "elapsed_ms": 0,
        "sides": {"side_a": ["driver"], "side_b": ["hidden", "visible"]},
        "positions": {
            "driver": {"x_mm": 0, "y_mm": 0},
            "hidden": {"x_mm": 1_000, "y_mm": 0},
            "visible": {"x_mm": 8_000, "y_mm": 0},
        },
        "combatants": {
            "driver": {"status_families": [], "observed_refs": ["hidden", "visible"]},
            "hidden": {"status_families": [], "observed_refs": ["driver"]},
            "visible": {"status_families": [], "observed_refs": ["driver"]},
        },
    }
    monkeypatch.setattr(simulation, "initialize_combat", lambda **_kwargs: copy.deepcopy(combat))
    monkeypatch.setattr(
        simulation, "currently_visible_enemies",
        lambda _combat, *, actor_ref, enemy_refs, people: ["visible"] if actor_ref == "driver" else [],
    )
    monkeypatch.setattr(simulation, "default_action_for", lambda **_kwargs: ("unarmed_strike", "body_unarmed"))
    monkeypatch.setattr(
        simulation, "automatic_resource_policy",
        lambda **_kwargs: {"poison_ref": None, "qi_allocation_milli": {}, "qi_reserve_milli": 0},
    )
    selected: list[str] = []

    def fake_resolve_exchange(**kwargs):
        selected.append(str(kwargs["player_target_ref"]))
        after = copy.deepcopy(kwargs["combat"])
        after["status"] = "resolved"
        after["winner_side"] = "side_a"
        return {
            "combat_after": after,
            "people_after": kwargs["people"],
            "equipment_ledger_after": kwargs["equipment_ledger"],
            "events": [],
        }

    monkeypatch.setattr(simulation, "resolve_exchange", fake_resolve_exchange)
    result = simulation.simulate_exact_combat(
        combat_ref="autonomous-current-visibility", side_a_refs=["driver"], side_b_refs=["hidden", "visible"],
        people=people, equipment_ledger=ledger, doctrines={}, zone_ref="z", started_at="0061-01-01T00:00:00",
        objective={"kind": "eliminate"}, targeting_intent="disable", max_exchanges=1,
    )
    assert selected == ["visible"]
    assert result["resolved"] is True


def test_exact_activity_predicate_excludes_escaped_and_unconscious_participants():
    person = _base_person("p", "a")
    assert exact.combatant_active(person, {"status_families": []}) is True
    assert exact.combatant_active(person, {"status_families": ["escaped"]}) is False
    unconscious = copy.deepcopy(person)
    unconscious["health"]["consciousness"] = 0
    assert exact.combatant_active(unconscious, {"status_families": []}) is False


def test_vengeance_witness_gate_uses_fresh_visibility_authority():
    source = (ROOT / "runtime/shinobi_runtime/commands/jianghu_extended.py").read_text()
    block = source[source.index("vengeance_created:list[str]=[]"):source.index("return self._combine_time_plan", source.index("vengeance_created:list[str]=[]"))]
    assert "currently_visible_enemies(" in block
    assert "killer_ref not in observed" not in block


def test_current_contact_persists_through_clear_los_but_breaks_on_occlusion():
    people = {
        "wei": _base_person("wei", "a"),
        "enemy": _base_person("enemy", "b"),
    }
    combat = exact.initialize_combat(
        combat_ref="visibility-current-contact", side_a_refs=["wei"], side_b_refs=["enemy"],
        people=people, zone_ref="z", started_at="x", objective={"kind": "eliminate"},
        equipment_ledger=_ledger(*people),
    )
    combat["positions"]["wei"].update(x_mm=0, y_mm=0)
    combat["positions"]["enemy"].update(x_mm=25_000, y_mm=0)
    combat["combatants"]["enemy"]["concealment_milli"] = 1000

    # Mutual start establishes a real current contact. Continuous clear LOS
    # keeps it acquired even after time advances.
    assert exact.currently_visible_enemies(
        combat, actor_ref="wei", enemy_refs=["enemy"], people=people,
    ) == ["enemy"]
    combat["elapsed_ms"] = 500
    exact._observe_visible_enemies(combat, actor_ref="wei", enemy_refs=["enemy"], people=people, at_ms=500)
    assert combat["combatants"]["wei"]["current_contact_refs"] == ["enemy"]

    # Breaking LOS clears current contact but leaves cumulative memory.
    combat["obstacles"] = [_wall()]
    combat["positions"]["enemy"].update(x_mm=3_000, y_mm=0)
    exact._observe_visible_enemies(combat, actor_ref="wei", enemy_refs=["enemy"], people=people, at_ms=600)
    assert combat["combatants"]["wei"]["observed_refs"] == ["enemy"]
    assert combat["combatants"]["wei"]["current_contact_refs"] == []

    # Removing the wall does not magically promote memory back to sight; the
    # high-concealment target must be freshly detected again.
    combat["obstacles"] = []
    assert exact.currently_visible_enemies(
        combat, actor_ref="wei", enemy_refs=["enemy"], people=people,
    ) == []



def test_default_target_for_never_uses_remembered_hidden_exact_position():
    people = {
        "wei": _base_person("wei", "a"),
        "hidden": _base_person("hidden", "b"),
        "visible": _base_person("visible", "b"),
    }
    combat = exact.initialize_combat(
        combat_ref="visibility-default-target", side_a_refs=["wei"], side_b_refs=["hidden", "visible"],
        people=people, zone_ref="z", started_at="x", objective={"kind": "eliminate"},
        equipment_ledger=_ledger(*people), obstacles=[_wall()],
    )
    combat["positions"]["wei"].update(x_mm=0, y_mm=0)
    combat["positions"]["hidden"].update(x_mm=3_000, y_mm=0)
    combat["positions"]["visible"].update(x_mm=0, y_mm=8_000)
    combat["combatants"]["wei"]["observed_refs"] = ["hidden", "visible"]

    assert exact.default_target_for(combat=combat, people=people, actor_ref="wei") == "visible"


def test_default_target_for_fails_closed_when_only_remembered_enemy_is_hidden():
    people = {"wei": _base_person("wei", "a"), "hidden": _base_person("hidden", "b")}
    combat = exact.initialize_combat(
        combat_ref="visibility-default-target-none", side_a_refs=["wei"], side_b_refs=["hidden"],
        people=people, zone_ref="z", started_at="x", objective={"kind": "eliminate"},
        equipment_ledger=_ledger(*people), obstacles=[_wall()],
    )
    combat["positions"]["wei"].update(x_mm=0, y_mm=0)
    combat["positions"]["hidden"].update(x_mm=3_000, y_mm=0)
    combat["combatants"]["wei"]["observed_refs"] = ["hidden"]

    import pytest
    with pytest.raises(ValueError, match="currently visible"):
        exact.default_target_for(combat=combat, people=people, actor_ref="wei")


def test_team_replan_cannot_use_hidden_enemy_current_coordinate():
    people = {
        "ally": _base_person("ally", "a"),
        "hidden": _base_person("hidden", "b"),
        "visible": _base_person("visible", "b"),
    }
    combat = exact.initialize_combat(
        combat_ref="visibility-team-plan", side_a_refs=["ally"], side_b_refs=["hidden", "visible"],
        people=people, zone_ref="z", started_at="x", objective={"kind": "eliminate"},
        equipment_ledger=_ledger(*people), obstacles=[_wall()],
    )
    combat["positions"]["ally"].update(x_mm=0, y_mm=0)
    combat["positions"]["hidden"].update(x_mm=3_000, y_mm=0)
    combat["positions"]["visible"].update(x_mm=0, y_mm=8_000)
    combat["combatants"]["ally"]["observed_refs"] = ["hidden", "visible"]

    plan = exact._refresh_team_plan(combat, side="side_a", people=people, doctrine={})
    refs = {str(plan.get("primary_threat_ref") or "")}
    for row in (plan.get("assignments") or {}).values():
        if isinstance(row, dict) and row.get("target_ref"):
            refs.add(str(row["target_ref"]))
    refs.discard("")
    assert refs <= {"visible"}


def test_social_attribution_code_does_not_use_cumulative_observation_as_attack_identity():
    command_source = (ROOT / "runtime/shinobi_runtime/commands/jianghu_extended.py").read_text()
    simulation_source = (ROOT / "runtime/shinobi_runtime/martial_world/combat_simulation.py").read_text()
    assert "detected_this_attack" in command_source
    assert "freshly_visible" in command_source
    assert "detected_this_attack" in simulation_source
    assert "actor_ref in observed" not in simulation_source


def test_attack_detection_does_not_use_exact_encounter_memory_as_current_sensory_evidence():
    from shinobi_runtime.combat.models import (
        ActionProfile, CapabilityProfile, CombatIntent, InformationState,
        Participant, PersonnelState, PositionState,
    )
    from shinobi_runtime.combat.physical_defense import detect_attack

    cap = CapabilityProfile(
        offense=50, defense=50, control=50, mobility=50,
        perception=10, stealth=0, capture=20, escape=50, reaction=10,
    )
    personnel = PersonnelState(total=1, active=1)
    attack = ActionProfile(
        method_ref="concealed_thrust", effect_kind="physical", delivery="direct",
        startup_ms=0, external_contact=True, speed_score=100,
        effect_parameters={"physical_reach_m": 1.2},
    )
    attacker = Participant(
        participant_ref="attacker", authoritative_owner_ref="attacker", side_ref="a",
        sequence=0, representation="exact", capability=cap, personnel=personnel,
        position=PositionState(zone_ref="z", x_mm=1000, y_mm=0, facing_mdeg=180000),
        information=InformationState(observed_refs=("defender",)),
        intent=CombatIntent(action="attack", target_refs=("defender",)),
        initiative=50, readiness=50, morale=50, cohesion=50, action_profile=attack,
    )

    def defender(observed_refs):
        return Participant(
            participant_ref="defender", authoritative_owner_ref="defender", side_ref="b",
            sequence=0, representation="exact", capability=cap, personnel=personnel,
            position=PositionState(zone_ref="z", x_mm=0, y_mm=0, facing_mdeg=0),
            information=InformationState(observed_refs=observed_refs),
            intent=CombatIntent(action="hold"), initiative=50, readiness=50,
            morale=50, cohesion=50,
        )

    remembered = defender(("attacker",))
    unknown = defender(())
    remembered_result = detect_attack(
        attacker=attacker, defender=remembered,
        attacker_position=attacker.position, defender_position=remembered.position,
        attacker_capability=cap, defender_capability=cap, profile=attack,
        line_of_sight=False,
    )
    unknown_result = detect_attack(
        attacker=attacker, defender=unknown,
        attacker_position=attacker.position, defender_position=unknown.position,
        attacker_capability=cap, defender_capability=cap, profile=attack,
        line_of_sight=False,
    )
    assert remembered_result[:2] == unknown_result[:2]
    assert remembered_result[0] is False


def test_reinforcement_arrival_does_not_seed_full_enemy_roster_before_detection(monkeypatch):
    people = {
        "pc": _base_person("pc", "a"),
        "reserve": _base_person("reserve", "a"),
        "hidden": _base_person("hidden", "b"),
    }
    combat = exact.initialize_combat(
        combat_ref="visibility-reinforcement-arrival",
        side_a_refs=["pc", "reserve"], side_b_refs=["hidden"],
        people=people, zone_ref="z", started_at="x", objective={"kind": "eliminate"},
        equipment_ledger=_ledger(*people), obstacles=[_wall()],
        reinforcement_delays_ms={"reserve": 500},
    )
    combat["elapsed_ms"] = 500
    combat["positions"]["reserve"].update(x_mm=0, y_mm=0)
    combat["positions"]["hidden"].update(x_mm=3_000, y_mm=0)
    assert combat["combatants"]["reserve"]["observed_refs"] == []

    captured: list[str] = []

    def stop_before_team_detection(out, *, side, people, doctrine):
        captured.extend(out["combatants"]["reserve"].get("observed_refs", []))
        raise RuntimeError("captured reinforcement activation")

    monkeypatch.setattr(exact, "_refresh_team_plan", stop_before_team_detection)

    import pytest
    with pytest.raises(RuntimeError, match="captured reinforcement activation"):
        exact.resolve_exchange(
            combat=combat, people=people, equipment_ledger=_ledger(*people), doctrines={},
            player_ref="pc", player_action_kind="hold", player_target_ref="hidden",
            player_weapon_ref="body_unarmed",
        )
    assert captured == []


def test_physical_defense_receives_live_contact_not_cumulative_encounter_memory(monkeypatch):
    """Remembering an attacker is not the same as currently tracking them."""
    import pytest

    people = {
        "attacker": _base_person("attacker", "a"),
        "defender": _base_person("defender", "b"),
    }
    gear = _ledger(*people)
    combat = exact.initialize_combat(
        combat_ref="visibility-defense-contact",
        side_a_refs=["attacker"], side_b_refs=["defender"],
        people=people, zone_ref="z", started_at="x",
        objective={"kind": "eliminate", "target_refs": ["defender"]},
        equipment_ledger=gear,
    )
    combat["positions"]["attacker"].update(x_mm=0, y_mm=0)
    combat["positions"]["defender"].update(x_mm=900, y_mm=0)
    combat["combatants"]["defender"]["observed_refs"] = ["attacker"]
    combat["combatants"]["defender"]["current_contact_refs"] = []

    original_observe = exact._observe_visible_enemies
    original_visible = exact._currently_visible_enemies
    monkeypatch.setattr(
        exact, "_observe_visible_enemies",
        lambda combat, actor_ref, enemy_refs, people, at_ms: [] if actor_ref == "defender" else original_observe(
            combat, actor_ref=actor_ref, enemy_refs=enemy_refs, people=people, at_ms=at_ms,
        ),
    )
    monkeypatch.setattr(
        exact, "_currently_visible_enemies",
        lambda combat, actor_ref, enemy_refs, people: [] if actor_ref == "defender" else original_visible(
            combat, actor_ref=actor_ref, enemy_refs=enemy_refs, people=people,
        ),
    )

    class DefenseProbe(RuntimeError):
        pass

    def probe_defense(**kwargs):
        assert kwargs["defender"].information.observed_refs == ()
        raise DefenseProbe("live-contact projection reached defense resolver")

    monkeypatch.setattr(exact, "select_physical_defense", probe_defense)
    with pytest.raises(DefenseProbe, match="live-contact projection"):
        exact.resolve_exchange(
            combat=combat, people=people, equipment_ledger=gear, doctrines={},
            player_ref="attacker", player_action_kind="unarmed_strike",
            player_target_ref="defender", player_weapon_ref="body_unarmed",
            player_hit_zone="chest", player_targeting_intent="disable",
        )
