import copy

import shinobi_runtime.commands.combat_adaptation as adaptation


def _person(ref, *, intelligence=100, doctrine=None, qi=150, qi_control=78, current_qi_milli=99_935):
    row = {
        "person_id": ref,
        "attributes": {"intelligence": intelligence},
        "health": {"status": "healthy", "consciousness": 100, "injuries": []},
        "qi": qi,
        "qi_control": qi_control,
        "current_qi_milli": current_qi_milli,
    }
    if doctrine:
        row["combat_doctrine_ref"] = doctrine
    return row


def _combat():
    return {
        "status": "active",
        "elapsed_ms": 0,
        "sides": {"a": ["wei"], "b": ["enemy", "enemy.2"]},
        "combatants": {
            "wei": {"status_families": []},
            "enemy": {"status_families": []},
            "enemy.2": {"status_families": []},
        },
        "player_combat_tallies": {"wei": {"confirmed_defeats": 0, "confirmed_kills": 0}},
    }


def test_spatial_and_no_contact_misses_are_definite_tactical_failures():
    assert adaptation.definite_tactical_failure({"result": "miss_no_spatial_intersection"}) is True
    assert adaptation.definite_tactical_failure({"result": "no_contact"}) is True


def test_adaptation_changes_geometry_and_same_discipline_before_generic_retarget(monkeypatch):
    monkeypatch.setattr(adaptation, "visible_active_enemies", lambda *args, **kwargs: ["enemy", "enemy.2"])
    candidates = adaptation.adaptive_override_candidates(
        original_kwargs={
            "raw_target_ref": "auto",
            "raw_action_kind": "attack",
            "raw_weapon_ref": "auto",
            "hit_zone": "auto",
            "target_structure_ref": None,
        },
        combat=_combat(),
        people={"wei": _person("wei"), "enemy": _person("enemy"), "enemy.2": _person("enemy.2")},
        player_ref="wei",
        previous_event={"intended_ref": "enemy", "action_kind": "thrust", "hit_zone": "neck"},
        previous_projection={},
    )
    assert candidates[0] == {"_adaptive_movement_intent": "lateral"}
    assert candidates[1] == {"raw_action_kind": "cut"}
    first_target_switch = next(i for i, row in enumerate(candidates) if "raw_target_ref" in row)
    assert first_target_switch > 1


def test_adaptive_movement_context_is_scoped_to_one_internal_exchange():
    from shinobi_runtime.api.combat_tactical_movement_integrity import _MOVEMENT_CONTEXT

    assert _MOVEMENT_CONTEXT.get() is None

    def resolver(**kwargs):
        assert kwargs.get("_adaptive_movement_intent") is None
        assert _MOVEMENT_CONTEXT.get() == {"actor_ref": "wei", "movement_intent": "lateral"}
        return {"ok": True}

    result = adaptation._resolve_with_optional_movement(
        resolver,
        attempt={"_adaptive_movement_intent": "lateral"},
        player_ref="wei",
    )
    assert result == {"ok": True}
    assert _MOVEMENT_CONTEXT.get() is None


def test_auto_poison_is_suppressed_after_first_wasted_projectile_dose_and_spatial_misses_stagnate(monkeypatch):
    monkeypatch.setattr(adaptation, "visible_active_enemies", lambda *args, **kwargs: ["enemy"])
    monkeypatch.setattr(adaptation, "_update_social_cursor", lambda state, events, **kwargs: copy.deepcopy(state))
    calls = []

    def resolver(**kwargs):
        calls.append({"poison_auto": kwargs.get("poison_auto"), "explicit_poison_ref": kwargs.get("explicit_poison_ref")})
        combat = copy.deepcopy(kwargs["combat"])
        combat["elapsed_ms"] = int(combat.get("elapsed_ms", 0)) + 1000
        event = {
            "actor_ref": "wei",
            "action_kind": "hidden_weapon_throw",
            "intended_ref": "enemy",
            "hit_zone": "neck",
            "result": "miss_no_spatial_intersection",
            "poison_ref": "cardiotoxic" if kwargs.get("poison_auto") else None,
            "resource_commit": {"poison_dose_consumed": bool(kwargs.get("poison_auto"))},
        }
        return {
            "combat_after": combat,
            "people_after": copy.deepcopy(kwargs["people"]),
            "equipment_ledger_after": copy.deepcopy(kwargs["equipment_ledger"]),
            "events": [event],
            "exchanges_resolved": 1,
            "narrative_projection": {"beats": [], "narration_rules": []},
            "combat_information": {},
        }

    result = adaptation.adaptive_standing_span(
        resolver,
        fallback=lambda base, **kwargs: base(**kwargs),
        combat=_combat(),
        people={"wei": _person("wei", doctrine=None), "enemy": _person("enemy"), "enemy.2": _person("enemy.2")},
        equipment_ledger={},
        player_ref="wei",
        social_state={},
        raw_target_ref="auto",
        raw_action_kind="attack",
        raw_weapon_ref="auto",
        hit_zone="auto",
        target_structure_ref=None,
        targeting_intent="lethal",
        explicit_poison_ref=None,
        poison_auto=True,
        explicit_qi_allocation_milli=None,
        qi_auto=True,
        exchange_count=None,
        duration_seconds=None,
        until_resolution=True,
    )
    assert result["scope_stop_reason"] == "tactical_stagnation"
    assert result["exchanges_resolved"] == 6
    assert calls[0]["poison_auto"] is True
    assert all(row["poison_auto"] is False for row in calls[1:])


def test_explicit_poison_is_never_suppressed_by_adaptive_history(monkeypatch):
    monkeypatch.setattr(adaptation, "visible_active_enemies", lambda *args, **kwargs: ["enemy"])
    monkeypatch.setattr(adaptation, "_update_social_cursor", lambda state, events, **kwargs: copy.deepcopy(state))
    calls = []

    def resolver(**kwargs):
        calls.append((kwargs.get("explicit_poison_ref"), kwargs.get("poison_auto")))
        combat = copy.deepcopy(kwargs["combat"])
        combat["elapsed_ms"] = int(combat.get("elapsed_ms", 0)) + 1000
        return {
            "combat_after": combat,
            "people_after": copy.deepcopy(kwargs["people"]),
            "equipment_ledger_after": copy.deepcopy(kwargs["equipment_ledger"]),
            "events": [{
                "actor_ref": "wei", "action_kind": "hidden_weapon_throw", "intended_ref": "enemy",
                "hit_zone": "neck", "result": "miss_no_spatial_intersection", "poison_ref": "cardiotoxic",
                "resource_commit": {"poison_dose_consumed": True},
            }],
            "exchanges_resolved": 1,
            "narrative_projection": {"beats": [], "narration_rules": []},
            "combat_information": {},
        }

    adaptation.adaptive_standing_span(
        resolver,
        fallback=lambda base, **kwargs: base(**kwargs),
        combat=_combat(), people={"wei": _person("wei"), "enemy": _person("enemy"), "enemy.2": _person("enemy.2")},
        equipment_ledger={}, player_ref="wei", social_state={}, raw_target_ref="auto",
        raw_action_kind="attack", raw_weapon_ref="auto", hit_zone="auto", target_structure_ref=None,
        targeting_intent="lethal", explicit_poison_ref="cardiotoxic", poison_auto=False,
        explicit_qi_allocation_milli=None, qi_auto=False, exchange_count=None, duration_seconds=None,
        until_resolution=True,
    )
    assert calls
    assert all(poison == "cardiotoxic" and auto is False for poison, auto in calls)


def test_stored_and_span_override_wei_doctrines_can_escalate_qi_only_for_delegated_lethal_pursuit():
    for doctrine_ref in (
        "doctrine.tang_wei.precision_function_denial",
        "doctrine.tang_wei.precision_function_denial.lethal_pursuit",
    ):
        people = {
            "wei": _person(
                "wei",
                doctrine=doctrine_ref,
                qi=150,
                qi_control=78,
                current_qi_milli=99_935,
            )
        }
        allocation = adaptation._adaptive_qi_allocation(
            people=people,
            player_ref="wei",
            failure_streak=1,
            threshold=1,
            targeting_intent="lethal",
            until_resolution=True,
        )
        assert allocation
        assert set(allocation) <= {"movement", "body", "sensing"}
        assert sum(allocation.values()) <= adaptation.safe_flow_milli_per_second(150, 78)

        assert adaptation._adaptive_qi_allocation(
            people=people,
            player_ref="wei",
            failure_streak=1,
            threshold=1,
            targeting_intent="disable",
            until_resolution=True,
        ) is None
        assert adaptation._adaptive_qi_allocation(
            people=people,
            player_ref="wei",
            failure_streak=1,
            threshold=1,
            targeting_intent="lethal",
            until_resolution=False,
        ) is None

    unrelated = {"wei": _person("wei", doctrine="doctrine.someone_else")}
    assert adaptation._adaptive_qi_allocation(
        people=unrelated,
        player_ref="wei",
        failure_streak=1,
        threshold=1,
        targeting_intent="lethal",
        until_resolution=True,
    ) is None


def test_whole_span_combat_information_uses_tally_delta_and_full_event_stream():
    before = _combat()
    before["player_combat_tallies"]["wei"] = {"confirmed_defeats": 2, "confirmed_kills": 1}
    after = copy.deepcopy(before)
    after["player_combat_tallies"]["wei"] = {"confirmed_defeats": 3, "confirmed_kills": 2}
    info = adaptation._normalize_span_combat_information(
        initial_combat=before,
        final_combat=after,
        events=[{"actor_ref": "enemy.2", "result": "withdrew_from_combat"}],
        player_ref="wei",
        current_information={"visible_hostiles_current": 4, "observed_escaped": 0},
    )
    assert info["player_confirmed_defeats_this_resolution"] == 1
    assert info["player_confirmed_kills_this_resolution"] == 1
    assert info["player_confirmed_defeats_encounter"] == 3
    assert info["player_confirmed_kills_encounter"] == 2
    assert info["confirmed_hostile_withdrawals_this_resolution"] == 1
    assert info["observed_escaped"] == 1
    assert info["visible_hostiles_current"] == 4
