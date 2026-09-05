from __future__ import annotations

from typing import Any, Mapping

from shinobi_runtime.api import combat_liveness_integrity as integrity


def _combat(*, actor_x: int = 0, enemy_x: int = 3000) -> dict[str, Any]:
    return {
        "status": "active",
        "sides": {"side_a": ["actor"], "side_b": ["enemy"]},
        "combatants": {
            "actor": {"status_families": []},
            "enemy": {"status_families": []},
        },
        "positions": {
            "actor": {
                "x_mm": actor_x,
                "y_mm": 0,
                "zone_ref": "road",
                "vx_mmps": 0,
                "vy_mmps": 0,
                "stance": "disengaging",
            },
            "enemy": {
                "x_mm": enemy_x,
                "y_mm": 0,
                "zone_ref": "road",
                "vx_mmps": 0,
                "vy_mmps": 0,
                "stance": "disengaging",
            },
        },
        "_pending_actions": {},
    }


def test_retreat_corridor_maximizes_hostile_separation_instead_of_lowest_angle():
    combat = _combat(enemy_x=3000)
    positions = combat["positions"]

    def base_corridors(_positions, *, actor_ref, **_kwargs):
        assert actor_ref == "actor"
        return (
            {"angle_mdeg": 0, "end_x_mm": 5000, "end_y_mm": 0},
            {"angle_mdeg": 180000, "end_x_mm": -5000, "end_y_mm": 0},
        )

    seen: list[tuple[dict[str, int], ...]] = []

    def base_disengage(**_kwargs: Any) -> Mapping[str, Any]:
        rows = integrity.separating_retreat_corridors(
            base_corridors,
            positions,
            actor_ref="actor",
            body_refs=["actor", "enemy"],
        )
        seen.append(rows)
        return {"moved": False, "escaped": False, "movement": {}}

    integrity.disengage_with_integrity(
        base_disengage,
        combat=combat,
        actor_ref="actor",
    )

    assert seen == [({"angle_mdeg": 180000, "end_x_mm": -5000, "end_y_mm": 0},)]


def test_stationary_stale_pending_target_does_not_veto_escape_after_clearance():
    combat = _combat(actor_x=0, enemy_x=8000)
    combat["_pending_actions"] = {
        "enemy": {
            "target_ref": "actor",
            "action_kind": "bow_shot",
        }
    }

    def base(**kwargs: Any) -> Mapping[str, Any]:
        kwargs["combat"]["positions"]["actor"]["x_mm"] = -5000
        return {
            "moved": True,
            "escaped": False,
            "reason": "retreat_contested_by_committed_melee",
            "movement": {
                "start_ms": 1000,
                "duration_ms": 1200,
                "nearest_enemy_mm": 13000,
            },
        }

    result = integrity.disengage_with_integrity(
        base,
        combat=combat,
        actor_ref="actor",
        start_ms=1000,
        duration_ms=1200,
    )

    assert result["escaped"] is True
    assert result["reason"] == "cleared_opponent_reach"
    assert "escaped" in combat["combatants"]["actor"]["status_families"]
    assert combat["combatants"]["actor"]["escaped_at_ms"] == 2200


def test_real_melee_pursuit_still_blocks_escape_reclassification():
    combat = _combat(actor_x=0, enemy_x=8000)
    combat["_pending_actions"] = {
        "enemy": {
            "target_ref": "actor",
            "action_kind": "thrust",
        }
    }

    def base(**_kwargs: Any) -> Mapping[str, Any]:
        return {
            "moved": True,
            "escaped": False,
            "reason": "retreat_contested_by_committed_melee",
            "movement": {"start_ms": 1000, "duration_ms": 1200, "nearest_enemy_mm": 8000},
        }

    result = integrity.disengage_with_integrity(
        base,
        combat=combat,
        actor_ref="actor",
        start_ms=1000,
        duration_ms=1200,
    )

    assert result["escaped"] is False
    assert "escaped" not in combat["combatants"]["actor"]["status_families"]


def _standing_result(after: Mapping[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "scope_stop_reason": "execution_frontier",
        "continuation_required": True,
        "combat_after": after,
        "events": events,
        "narrative_projection": {"scope_stop_reason": "execution_frontier", "narration_rules": []},
    }


def test_parallel_withdrawal_motion_does_not_fake_resolution_progress():
    before = _combat(actor_x=0, enemy_x=3000)
    after = _combat(actor_x=50000, enemy_x=53000)

    def base(**_kwargs: Any) -> Mapping[str, Any]:
        return _standing_result(
            after,
            [{"actor_ref": "actor", "result": "withdrawal_in_progress", "movement": {"distance_mm": 50000}}],
        )

    result = integrity.resolution_progress_guard(
        base,
        combat=before,
        until_resolution=True,
        exchange_count=None,
        duration_seconds=None,
    )

    assert result["scope_stop_reason"] == "stagnation_checkpoint"
    assert result["continuation_required"] is False


def test_support_approach_alone_does_not_keep_global_combat_autocontinuing():
    before = _combat(actor_x=0, enemy_x=3000)
    after = _combat(actor_x=0, enemy_x=3000)

    def base(**_kwargs: Any) -> Mapping[str, Any]:
        return _standing_result(
            after,
            [{
                "actor_ref": "medic",
                "action_kind": "ally_support",
                "result": "support_treatment_approach",
                "movement": {"distance_mm": 500},
            }],
        )

    result = integrity.resolution_progress_guard(
        base,
        combat=before,
        until_resolution=True,
        exchange_count=None,
        duration_seconds=None,
    )

    assert result["scope_stop_reason"] == "stagnation_checkpoint"
    assert result["continuation_required"] is False


def test_real_contact_pressure_reduction_allows_bounded_continuation():
    before = _combat(actor_x=0, enemy_x=3000)
    after = _combat(actor_x=0, enemy_x=10000)

    def base(**_kwargs: Any) -> Mapping[str, Any]:
        return _standing_result(after, [{"result": "withdrawal_in_progress"}])

    result = integrity.resolution_progress_guard(
        base,
        combat=before,
        until_resolution=True,
        exchange_count=None,
        duration_seconds=None,
    )

    assert result["scope_stop_reason"] == "execution_frontier"
    assert result["continuation_required"] is True


def test_material_wound_allows_bounded_continuation_even_without_separation_change():
    before = _combat(actor_x=0, enemy_x=3000)
    after = _combat(actor_x=0, enemy_x=3000)

    def base(**_kwargs: Any) -> Mapping[str, Any]:
        return _standing_result(
            after,
            [{
                "result": "contact",
                "damage": {
                    "wound": {
                        "severity": 25,
                        "bleeding_ml_per_min": 0,
                        "function_loss_pct": 0,
                        "fracture": 0,
                        "tendon_damage": 0,
                        "nerve_damage": 0,
                        "organ_trauma": 0,
                    }
                },
            }],
        )

    result = integrity.resolution_progress_guard(
        base,
        combat=before,
        until_resolution=True,
        exchange_count=None,
        duration_seconds=None,
    )

    assert result["scope_stop_reason"] == "execution_frontier"
    assert result["continuation_required"] is True


def test_coarse_wound_merge_preserves_trauma_derived_function_loss_floor():
    def base(_existing: Mapping[str, Any], _incoming: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "zone": "knee",
            "structure_ref": None,
            "cut": 116,
            "pierce": 200,
            "blunt": 200,
            "penetration": 200,
            "fracture": 0,
            "tendon_damage": 200,
            "nerve_damage": 200,
            "functional_effects": {},
            "function_loss_pct": 0,
        }

    result = integrity.merge_current_wound_with_integrity(base, {}, {})

    assert result["function_loss_pct"] == 100
