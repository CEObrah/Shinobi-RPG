from __future__ import annotations

from types import SimpleNamespace

import pytest

from shinobi_runtime.commands import combat_span_safety as safety


def _person(*, doctrine: str | None = None, status: str = "ready", consciousness: int = 100) -> dict:
    person = {
        "health": {"status": status, "consciousness": consciousness},
    }
    if doctrine is not None:
        person["combat_doctrine_ref"] = doctrine
    return person


def _combat_for_targeting() -> dict:
    return {
        "sides": {
            "side_a": ["wei"],
            "side_b": ["near", "far", "escaped"],
        },
        "positions": {
            "wei": {"x_mm": 0, "y_mm": 0},
            "near": {"x_mm": 2_000, "y_mm": 0, "stance": "braced"},
            "far": {"x_mm": 70_000, "y_mm": 0, "stance": "disengaging"},
            "escaped": {"x_mm": 500, "y_mm": 0, "stance": "disengaging"},
        },
        "combatants": {
            "wei": {"observed_refs": []},
            "near": {"status_families": []},
            "far": {"status_families": []},
            "escaped": {"status_families": ["escaped"]},
        },
    }


def test_rapid_lethal_target_prefers_nearest_lawful_active_enemy_after_base_observation_refresh(monkeypatch):
    combat = _combat_for_targeting()
    people = {
        "wei": _person(doctrine="doctrine.tang_wei.precision_function_denial.lethal_pursuit"),
        "near": _person(),
        "far": _person(),
        "escaped": _person(),
    }

    def base_selector(**kwargs):
        # Stand in for exact combat's lawful memory refresh. Generic tactical
        # pressure would choose the far retreating target in this regression.
        kwargs["combat"]["combatants"]["wei"]["observed_refs"] = ["near", "far", "escaped"]
        return "far"

    monkeypatch.setattr(
        safety, "currently_visible_enemies",
        lambda _combat, *, actor_ref, enemy_refs, people: ["near", "far"],
    )
    selected = safety.rapid_lethal_target_for(
        base_selector,
        combat=combat,
        people=people,
        actor_ref="wei",
        martial_familiarity={},
    )

    assert selected == "near"


def test_non_rapid_doctrine_retains_base_target_selection_exactly():
    combat = _combat_for_targeting()
    people = {
        "wei": _person(doctrine="doctrine.tang_wei.precision_function_denial"),
        "near": _person(),
        "far": _person(),
        "escaped": _person(),
    }

    def base_selector(**kwargs):
        kwargs["combat"]["combatants"]["wei"]["observed_refs"] = ["near", "far"]
        return "far"

    assert safety.rapid_lethal_target_for(
        base_selector,
        combat=combat,
        people=people,
        actor_ref="wei",
        martial_familiarity={},
    ) == "far"


def test_rapid_lethal_target_never_reintroduces_unobserved_or_inactive_enemy():
    combat = _combat_for_targeting()
    combat["combatants"]["wei"]["observed_refs"] = ["far"]
    people = {
        "wei": _person(doctrine="doctrine.tang_wei.precision_function_denial.lethal_pursuit"),
        "near": _person(),
        "far": _person(),
        "escaped": _person(),
    }

    def base_selector(**kwargs):
        return "far"

    assert safety.rapid_lethal_target_for(
        base_selector,
        combat=combat,
        people=people,
        actor_ref="wei",
        martial_familiarity={},
    ) == "far"


def test_until_resolution_selects_largest_candidate_within_simulated_time_frontier():
    calls: list[int] = []

    def base_resolver(**kwargs):
        frontier = int(kwargs["frontier_exchanges"])
        calls.append(frontier)
        start = int(kwargs["combat"]["elapsed_ms"])
        # Model the incident class: each pursuit exchange can consume two minutes.
        return {
            "combat_after": {"status": "active", "elapsed_ms": start + frontier * 120_000},
            "exchanges_resolved": frontier,
            "scope_stop_reason": "execution_frontier",
            "continuation_required": True,
        }

    result = safety.bounded_standing_span(
        base_resolver,
        combat={"status": "active", "elapsed_ms": 10_000},
        until_resolution=True,
    )

    assert calls == [16, 8, 4, 2]
    assert result["exchanges_resolved"] == 2
    assert result["combat_after"]["elapsed_ms"] - 10_000 == 240_000
    assert result["continuation_required"] is True


def test_until_resolution_accepts_resolved_candidate_when_it_is_inside_time_frontier():
    def base_resolver(**kwargs):
        frontier = int(kwargs["frontier_exchanges"])
        return {
            "combat_after": {"status": "resolved", "elapsed_ms": 240_000},
            "exchanges_resolved": min(frontier, 3),
            "scope_stop_reason": "combat_resolved",
            "continuation_required": False,
        }

    result = safety.bounded_standing_span(
        base_resolver,
        combat={"status": "active", "elapsed_ms": 0},
        until_resolution=True,
    )

    assert result["combat_after"]["status"] == "resolved"
    assert result["scope_stop_reason"] == "combat_resolved"


def test_single_exchange_beyond_time_frontier_fails_closed():
    calls: list[int] = []

    def base_resolver(**kwargs):
        frontier = int(kwargs["frontier_exchanges"])
        calls.append(frontier)
        start = int(kwargs["combat"]["elapsed_ms"])
        return {
            "combat_after": {"status": "active", "elapsed_ms": start + frontier * 360_000},
            "exchanges_resolved": frontier,
        }

    with pytest.raises(ValueError, match="single combat exchange exceeds"):
        safety.bounded_standing_span(
            base_resolver,
            combat={"status": "active", "elapsed_ms": 0},
            until_resolution=True,
        )

    assert calls[-1] == 1


def test_finite_nonstanding_scope_is_not_rechunked():
    calls: list[dict] = []

    def base_resolver(**kwargs):
        calls.append(dict(kwargs))
        return {"combat_after": {"status": "active", "elapsed_ms": 30_000}}

    safety.bounded_standing_span(
        base_resolver,
        combat={"status": "active", "elapsed_ms": 0},
        duration_seconds=30,
        until_resolution=False,
    )

    assert len(calls) == 1
    assert "frontier_exchanges" not in calls[0]


def test_production_installer_wraps_selector_and_span_once(monkeypatch):
    import shinobi_runtime.commands.jianghu_extended as extended

    base_target_calls = []
    base_span_calls = []

    def fake_target(**kwargs):
        base_target_calls.append(kwargs)
        return "enemy"

    def fake_span(**kwargs):
        base_span_calls.append(kwargs)
        start = int(kwargs["combat"].get("elapsed_ms", 0))
        return {
            "combat_after": {"status": "active", "elapsed_ms": start + 1_000},
            "exchanges_resolved": int(kwargs.get("frontier_exchanges", 1)),
        }

    monkeypatch.setattr(extended, "default_target_for", fake_target)
    monkeypatch.setattr(extended, "_resolve_player_combat_span", fake_span)
    monkeypatch.setattr(extended, "_production_combat_span_safety_installed", False, raising=False)

    safety.install_production_combat_span_safety()
    first_target = extended.default_target_for
    first_span = extended._resolve_player_combat_span
    safety.install_production_combat_span_safety()

    assert extended.default_target_for is first_target
    assert extended._resolve_player_combat_span is first_span

    people = {
        "wei": _person(doctrine="doctrine.tang_wei.precision_function_denial"),
        "enemy": _person(),
    }
    combat = {
        "sides": {"a": ["wei"], "b": ["enemy"]},
        "positions": {"wei": {"x_mm": 0, "y_mm": 0}, "enemy": {"x_mm": 1_000, "y_mm": 0}},
        "combatants": {"wei": {"observed_refs": ["enemy"]}, "enemy": {"status_families": []}},
        "elapsed_ms": 0,
        "status": "active",
    }
    assert extended.default_target_for(
        combat=combat, people=people, actor_ref="wei", martial_familiarity={}
    ) == "enemy"
    result = extended._resolve_player_combat_span(
        combat=combat,
        until_resolution=True,
    )
    assert result["exchanges_resolved"] == 16
    assert base_target_calls
    assert base_span_calls
