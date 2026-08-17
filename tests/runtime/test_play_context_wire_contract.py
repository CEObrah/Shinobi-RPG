from __future__ import annotations

from shinobi_runtime.api import campaign_environment
from shinobi_runtime.api.command_discovery import compact_play_context
from shinobi_runtime.api.models import validate_bounded_json


def _rich_context() -> dict:
    return {
        "campaign": {
            "campaign_id": "shinobi-test",
            "revision": 74,
            "world_time": "SE-0061-08-07T07:29:58",
            "state_root": "a" * 64,
            "player_id": "pc_wei_tang",
        },
        "scene": {
            "scene_id": "scene.test",
            "world_time": "SE-0061-08-07T07:29:58",
            "location_id": "place.test",
            "active_combat": False,
            "time_passage_allowed": True,
            "freeform_actions_allowed": True,
            "observable_pressures": [],
            "narrative": {},
        },
        "player": {"name": "Wei Tang"},
        "person_reads": {
            "suggested_owner_ids": [f"person.{index:03d}" for index in range(40)],
            "total_permitted_ids": 40,
            "suggested_ids_truncated": False,
        },
        "object_reads": {
            "supported_ref_prefixes": ["team."],
            "use": "inspect one bounded object",
            "suggested_exact_team_refs": [f"team.test.{index:03d}" for index in range(80)],
            "exact_team_ref_count": 80,
            "exact_team_refs_truncated": False,
        },
        "commands": {
            "supported_command_types": ["advance_time", "travel_resolution"],
            "command_types": {
                "advance_time": {"availability": "blocked_by_scene_decision"},
                "travel_resolution": {"availability": "available"},
            },
        },
        "narration": {
            "primary_module_id": "social_village_institution",
            "modules": [
                {
                    "module_id": "social_village_institution",
                    "guidance": "x" * 4096,
                }
            ],
        },
        "context_policy": {"projection": "player_visible_bounded_handoff"},
    }


def test_compact_play_context_is_idempotent_and_preserves_material_overrides() -> None:
    once = compact_play_context(_rich_context())
    twice = compact_play_context(once)

    assert twice == once
    assert once["commands"]["availability_overrides"] == {
        "advance_time": "blocked_by_scene_decision"
    }
    assert len(once["person_reads"]["suggested_owner_ids"]) == 32
    assert len(once["object_reads"]["suggested_exact_team_refs"]) == 32
    assert once["object_reads"]["exact_team_ref_count"] == 80
    assert once["object_reads"]["exact_team_refs_truncated"] is True
    assert "narration.modules.guidance" in once["context_policy"]["compacted_fields"]
    assert "object_reads.suggested_exact_team_refs" in once["context_policy"]["compacted_fields"]
    validate_bounded_json(once, label="compact play context", allow_float=True)


def test_production_operations_expose_compact_context_before_transport(monkeypatch) -> None:
    rich = _rich_context()
    monkeypatch.setattr(campaign_environment._Base, "play_context", lambda self: rich)
    operations = campaign_environment.RouteAwareCampaignOperations.__new__(
        campaign_environment.RouteAwareCampaignOperations
    )

    projected = operations.play_context()

    assert "command_types" not in projected["commands"]
    assert projected["commands"]["availability_overrides"] == {
        "advance_time": "blocked_by_scene_decision"
    }
    assert "modules" not in projected["narration"]
    assert projected["context_policy"]["wire_projection"] == (
        "compact_player_visible_handoff"
    )
    validate_bounded_json(projected, label="compact play context", allow_float=True)
