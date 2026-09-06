from __future__ import annotations

import copy

from shinobi_runtime.commands import combat_adaptation as adapt


def _person(intelligence: int = 100, *, injuries=None):
    return {
        "attributes": {"intelligence": intelligence},
        "health": {"status": "healthy", "consciousness": 100, "injuries": list(injuries or [])},
    }


def _combat():
    return {
        "status": "active",
        "elapsed_ms": 0,
        "sides": {"a": ["wei", "ally"], "b": ["enemy1", "enemy2"]},
        "combatants": {
            "wei": {"status_families": []},
            "ally": {"status_families": []},
            "enemy1": {"status_families": []},
            "enemy2": {"status_families": []},
        },
        "positions": {
            "wei": {"x_mm": 0, "y_mm": 0},
            "ally": {"x_mm": 500, "y_mm": 0},
            "enemy1": {"x_mm": 1200, "y_mm": 0},
            "enemy2": {"x_mm": 1800, "y_mm": 0},
        },
    }


def _kwargs(*, intelligence: int = 100):
    return {
        "combat": _combat(),
        "people": {
            "wei": _person(intelligence),
            "ally": _person(),
            "enemy1": _person(),
            "enemy2": _person(),
        },
        "equipment_ledger": {"schema": "test"},
        "doctrines": {},
        "player_ref": "wei",
        "social_state": {},
        "player_retinue_context": None,
        "raw_target_ref": "auto",
        "raw_action_kind": "attack",
        "raw_weapon_ref": "weapon_jian",
        "hit_zone": "auto",
        "target_structure_ref": None,
        "targeting_intent": "lethal",
        "explicit_poison_ref": None,
        "poison_auto": True,
        "explicit_qi_allocation_milli": None,
        "qi_auto": True,
        "exchange_count": None,
        "duration_seconds": None,
        "until_resolution": True,
        "rally_allies": True,
        "ally_orders": None,
        "player_improvised_weapon_state": None,
    }


def _fallback(base, **kwargs):
    return base(**kwargs)


def _install_unit_stubs(monkeypatch, visible=("enemy1", "enemy2")):
    monkeypatch.setattr(adapt, "visible_active_enemies", lambda combat, people, actor_ref: list(visible))
    monkeypatch.setattr(
        adapt,
        "_update_social_cursor",
        lambda social_cursor, events, **kwargs: copy.deepcopy(dict(social_cursor)),
    )


def _fake_result(kwargs, *, result: str, status: str = "active", projection_beats=None):
    combat_after = copy.deepcopy(kwargs["combat"])
    combat_after["elapsed_ms"] = int(combat_after.get("elapsed_ms", 0)) + 1000
    combat_after["status"] = status
    target = kwargs["raw_target_ref"] if kwargs["raw_target_ref"] not in {"", "auto"} else "enemy1"
    action = kwargs["raw_action_kind"] if kwargs["raw_action_kind"] not in {"attack", "auto"} else "thrust"
    event = {
        "actor_ref": "wei",
        "intended_ref": target,
        "action_kind": action,
        "weapon_ref": kwargs.get("raw_weapon_ref") or "weapon_jian",
        "hit_zone": kwargs.get("hit_zone") or "auto",
        "result": result,
        "decision_origin": "player_adaptive",
        "targeting_intent": "lethal",
    }
    return {
        "combat_after": combat_after,
        "people_after": copy.deepcopy(kwargs["people"]),
        "equipment_ledger_after": copy.deepcopy(kwargs["equipment_ledger"]),
        "events": [event],
        "exchanges_resolved": 1,
        "scope_stop_reason": "scope_complete",
        "continuation_required": False,
        "narrative_projection": {
            "beats": list(projection_beats or []),
            "current_visibility": {"visible_hostiles_current": 2, "visible_combat_capable": 2},
            "narration_rules": ["test"],
        },
    }


def test_intelligence_controls_replanning_latency():
    people = {"wei": _person(100), "mid": _person(70), "low": _person(20)}
    assert adapt.intelligence_adaptation_threshold(people, "wei") == 1
    assert adapt.intelligence_adaptation_threshold(people, "mid") == 2
    assert adapt.intelligence_adaptation_threshold(people, "low") == 4


def test_high_intelligence_replans_technique_after_one_clear_failure_and_preserves_resources(monkeypatch):
    _install_unit_stubs(monkeypatch, visible=("enemy1",))
    calls = []

    def base(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        if len(calls) == 1:
            return _fake_result(kwargs, result="defended_or_missed")
        return _fake_result(kwargs, result="contact", status="resolved")

    result = adapt.adaptive_standing_span(base, fallback=_fallback, **_kwargs(intelligence=100))

    assert len(calls) == 2
    assert calls[0]["raw_action_kind"] == "attack"
    assert calls[1]["raw_action_kind"] == "cut"
    assert all(call["qi_auto"] is True for call in calls)
    assert all(call["poison_auto"] is True for call in calls)
    assert all(call["explicit_qi_allocation_milli"] is None for call in calls)
    assert calls[0]["rally_allies"] is True
    assert calls[1]["rally_allies"] is False
    assert result["scope_stop_reason"] == "combat_resolved"
    assert result["exchanges_resolved"] == 2


def test_player_safe_pressure_on_wounded_ally_can_retarget_before_technique_change(monkeypatch):
    _install_unit_stubs(monkeypatch)
    kwargs = _kwargs(intelligence=100)
    kwargs["people"]["ally"]["health"]["injuries"] = [{"severity": 3}]
    calls = []

    def base(**call_kwargs):
        calls.append(copy.deepcopy(call_kwargs))
        if len(calls) == 1:
            return _fake_result(
                call_kwargs,
                result="defended_or_missed",
                projection_beats=[{
                    "actor_ref": "enemy2",
                    "target_ref": "ally",
                    "result": "defended_or_missed",
                    "kind": "action",
                }],
            )
        return _fake_result(call_kwargs, result="contact", status="resolved")

    adapt.adaptive_standing_span(base, fallback=_fallback, **kwargs)

    assert calls[1]["raw_target_ref"] == "enemy2"
    assert calls[1]["raw_action_kind"] == "attack"


def test_lower_intelligence_repeats_before_adapting(monkeypatch):
    _install_unit_stubs(monkeypatch, visible=("enemy1",))
    calls = []

    def base(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        if len(calls) < 4:
            return _fake_result(kwargs, result="defended_or_missed")
        return _fake_result(kwargs, result="contact", status="resolved")

    adapt.adaptive_standing_span(base, fallback=_fallback, **_kwargs(intelligence=60))

    assert [call["raw_action_kind"] for call in calls[:3]] == ["attack", "attack", "attack"]
    assert calls[3]["raw_action_kind"] == "cut"


def test_fully_explicit_player_tactics_bypass_adaptive_chunking(monkeypatch):
    _install_unit_stubs(monkeypatch)
    kwargs = _kwargs()
    kwargs.update({
        "raw_target_ref": "enemy1",
        "raw_action_kind": "thrust",
        "raw_weapon_ref": "weapon_jian",
        "exchange_count": 3,
        "until_resolution": False,
    })
    calls = []

    def base(**call_kwargs):
        calls.append(copy.deepcopy(call_kwargs))
        return _fake_result(call_kwargs, result="defended_or_missed")

    result = adapt.adaptive_standing_span(base, fallback=_fallback, **kwargs)

    assert len(calls) == 1
    assert calls[0]["exchange_count"] == 3
    assert calls[0]["raw_target_ref"] == "enemy1"
    assert calls[0]["raw_action_kind"] == "thrust"
    assert result["events"][0]["action_kind"] == "thrust"


def test_repeated_no_progress_stops_as_tactical_stagnation_instead_of_spinning(monkeypatch):
    _install_unit_stubs(monkeypatch, visible=("enemy1",))
    calls = []

    def base(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        return _fake_result(kwargs, result="defended_or_missed")

    result = adapt.adaptive_standing_span(base, fallback=_fallback, **_kwargs(intelligence=100))

    assert result["scope_stop_reason"] == "tactical_stagnation"
    assert result["continuation_required"] is False
    assert result["exchanges_resolved"] == 6
    assert len(calls) == 6
    assert any(call["raw_action_kind"] == "cut" for call in calls[1:])
    assert any(call["hit_zone"] in {"chest", "forearm", "knee"} for call in calls[1:])


def test_adaptive_sequence_is_deterministic(monkeypatch):
    _install_unit_stubs(monkeypatch, visible=("enemy1", "enemy2"))

    def run_once():
        calls = []

        def base(**kwargs):
            calls.append((kwargs["raw_target_ref"], kwargs["raw_action_kind"], kwargs["hit_zone"]))
            return _fake_result(kwargs, result="defended_or_missed")

        adapt.adaptive_standing_span(base, fallback=_fallback, **_kwargs(intelligence=100))
        return calls

    assert run_once() == run_once()
