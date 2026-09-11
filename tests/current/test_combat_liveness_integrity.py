from __future__ import annotations

from shinobi_runtime.commands.jianghu_extended import _combat_resolution_progress


def _combat(*, actor_x: int = 0, enemy_x: int = 3000) -> dict:
    return {
        "status": "active",
        "sides": {"side_a": ["actor"], "side_b": ["enemy"]},
        "combatants": {"actor": {"status_families": []}, "enemy": {"status_families": []}},
        "positions": {
            "actor": {"x_mm": actor_x, "y_mm": 0, "zone_ref": "road"},
            "enemy": {"x_mm": enemy_x, "y_mm": 0, "zone_ref": "road"},
        },
    }


def test_parallel_withdrawal_motion_is_not_fake_resolution_progress() -> None:
    before = _combat(actor_x=0, enemy_x=3000)
    after = _combat(actor_x=50000, enemy_x=53000)
    events = [{"actor_ref": "actor", "result": "withdrawal_in_progress", "movement": {"distance_mm": 50000}}]
    assert _combat_resolution_progress(before, after, events) is False


def test_real_contact_pressure_reduction_is_resolution_progress() -> None:
    before = _combat(actor_x=0, enemy_x=3000)
    after = _combat(actor_x=0, enemy_x=10000)
    assert _combat_resolution_progress(before, after, [{"result": "withdrawal_in_progress"}]) is True


def test_material_wound_is_resolution_progress_even_without_geometry_change() -> None:
    before = _combat(actor_x=0, enemy_x=3000)
    after = _combat(actor_x=0, enemy_x=3000)
    events = [{"result": "contact", "damage": {"wound": {"severity": 30, "bleeding_ml_per_min": 0, "function_loss_pct": 0}}}]
    assert _combat_resolution_progress(before, after, events) is True
