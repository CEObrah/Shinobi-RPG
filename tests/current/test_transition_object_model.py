from shinobi_runtime.api.models import GameObjectResponse


def test_current_committed_transition_is_a_valid_game_object_view():
    response = GameObjectResponse(
        object_ref="transition:current",
        view="current_committed_transition",
        object={
            "available": False,
            "campaign_id": "campaign.test",
            "committed_revision": 8,
            "event_count": 0,
            "event_offset": 0,
            "events": [],
            "next_object_ref": None,
            "reason": "no_runtime_receipt_for_current_revision",
        },
    )
    assert response.view == "current_committed_transition"
