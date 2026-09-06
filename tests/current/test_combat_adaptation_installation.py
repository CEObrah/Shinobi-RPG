from __future__ import annotations

import copy

from shinobi_runtime.commands import combat_span_safety as safety


def _prepare_installer(monkeypatch, *, base_span):
    import shinobi_runtime.commands.jianghu_extended as extended
    from shinobi_runtime.martial_world import exact_combat as exact

    monkeypatch.setattr(extended, "_resolve_player_combat_span", base_span)
    monkeypatch.setattr(extended, "_production_combat_span_safety_installed", False, raising=False)
    monkeypatch.setattr(exact, "_production_defense_timing_safety_installed", True, raising=False)
    return extended


def test_production_adaptation_preserves_improvised_weapon_span(monkeypatch):
    base_calls = []
    bounded_calls = []

    def base_span(**kwargs):
        base_calls.append(dict(kwargs))
        return {"scope_stop_reason": "scope_complete"}

    def bounded(base_resolver, **kwargs):
        bounded_calls.append(dict(kwargs))
        return base_resolver(**kwargs)

    def adaptive(*args, **kwargs):
        raise AssertionError("improvised weapon span must not enter adaptive chunking")

    extended = _prepare_installer(monkeypatch, base_span=base_span)
    monkeypatch.setattr(safety, "bounded_standing_span", bounded)
    monkeypatch.setattr(safety, "adaptive_standing_span", adaptive)

    safety.install_production_combat_span_safety()
    result = extended._resolve_player_combat_span(
        combat={"status": "active", "elapsed_ms": 0},
        until_resolution=True,
        player_improvised_weapon_state={"fact_ref": "scene:staff"},
    )

    assert result["scope_stop_reason"] == "scope_complete"
    assert len(bounded_calls) == 1
    assert len(base_calls) == 1
    assert base_calls[0]["player_improvised_weapon_state"] == {"fact_ref": "scene:staff"}


def test_production_adaptation_reuses_canonical_stagnation_checkpoint(monkeypatch):
    def base_span(**kwargs):
        raise AssertionError("adaptive stub should own this delegated span")

    def adaptive(base_resolver, **kwargs):
        return {
            "scope_stop_reason": "tactical_stagnation",
            "continuation_required": True,
            "narrative_projection": {"scope_stop_reason": "tactical_stagnation"},
        }

    extended = _prepare_installer(monkeypatch, base_span=base_span)
    monkeypatch.setattr(safety, "adaptive_standing_span", adaptive)

    safety.install_production_combat_span_safety()
    result = extended._resolve_player_combat_span(
        combat={"status": "active", "elapsed_ms": 0},
        until_resolution=True,
        player_improvised_weapon_state=None,
    )

    assert result["scope_stop_reason"] == "stagnation_checkpoint"
    assert result["continuation_required"] is False
    assert result["narrative_projection"]["scope_stop_reason"] == "stagnation_checkpoint"


def test_production_span_normalizes_whole_resolution_counters_for_improvised_path(monkeypatch):
    initial_combat = {
        "status": "active",
        "elapsed_ms": 0,
        "sides": {"a": ["wei"], "b": ["enemy"]},
        "player_combat_tallies": {"wei": {"confirmed_defeats": 2, "confirmed_kills": 1}},
    }

    def base_span(**kwargs):
        combat_after = copy.deepcopy(kwargs["combat"])
        combat_after["elapsed_ms"] = 1_000
        combat_after["player_combat_tallies"] = {
            "wei": {"confirmed_defeats": 3, "confirmed_kills": 2}
        }
        return {
            "scope_stop_reason": "scope_complete",
            "combat_after": combat_after,
            "events": [{"actor_ref": "enemy", "result": "withdrew_from_combat"}],
            "exchanges_resolved": 2,
            "combat_information": {
                "player_confirmed_defeats_this_resolution": 0,
                "player_confirmed_kills_this_resolution": 0,
                "confirmed_hostile_withdrawals_this_resolution": 0,
                "observed_escaped": 0,
                "visible_hostiles_current": 0,
            },
        }

    extended = _prepare_installer(monkeypatch, base_span=base_span)
    safety.install_production_combat_span_safety()
    result = extended._resolve_player_combat_span(
        combat=initial_combat,
        player_ref="wei",
        until_resolution=True,
        player_improvised_weapon_state={"fact_ref": "scene:staff"},
    )

    info = result["combat_information"]
    assert info["player_confirmed_defeats_this_resolution"] == 1
    assert info["player_confirmed_kills_this_resolution"] == 1
    assert info["player_confirmed_defeats_encounter"] == 3
    assert info["player_confirmed_kills_encounter"] == 2
    assert info["confirmed_hostile_withdrawals_this_resolution"] == 1
    assert info["observed_escaped"] == 1
    assert info["visible_hostiles_current"] == 0
