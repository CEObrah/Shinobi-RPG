from shinobi_runtime.commands.world_front_progression import _player_visible_front_handoff


def test_front_phase_update_alone_does_not_leak_abstract_pressure():
    result = {
        "world_front_updates": [
            {
                "front_id": "pressure_oto_konoha_infiltration",
                "phase_before": "operational",
                "phase_after": "crisis",
                "player_visible": True,
            }
        ]
    }
    assert _player_visible_front_handoff(result) == ([], [], [])


def test_actual_player_report_delivery_surfaces_a_concrete_report_handoff():
    result = {
        "autonomous_actions": [
            {
                "kind": "information_report",
                "player_report_deliveries": [{"delivery_id": "delivery.world_front.test"}],
            }
        ]
    }
    assert _player_visible_front_handoff(result) == (
        [],
        ["A sourced operational report addressed to Wei is ready for review."],
        [],
    )
