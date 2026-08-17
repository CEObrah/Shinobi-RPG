from __future__ import annotations

from shinobi_runtime.api.command_discovery import command_domain, compact_play_context
from shinobi_runtime.api.models import validate_bounded_json


def test_promotion_exam_registration_is_a_social_command():
    assert command_domain("promotion_exam_registration_resolution") == "social"


def test_compact_play_context_removes_rehydratable_bulk_and_stays_in_wire_budget():
    cycle_id = "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07"
    public_rows = [
        {
            "candidate_ref": f"char.candidate_{index:03d}",
            "candidate_name": f"Candidate {index}",
            "village": "Konoha",
            "team_ref": f"team.konoha.{index // 3:03d}",
            "score": 100 + index,
            "threshold": 80,
            "outcome": "pass",
        }
        for index in range(84)
    ]
    command_types = {
        f"command_{index:03d}": {
            "availability": "available",
            "input_guidance": {
                "description": "x" * 200,
                "nested": {"more": "y" * 200},
            },
        }
        for index in range(80)
    }
    context = {
        "campaign": {
            "campaign_id": "shinobi-test",
            "revision": 74,
            "world_time": "SE-0061-08-07T07:29:58",
            "state_root": "a" * 64,
            "player_id": "pc_wei_tang",
        },
        "scene": {
            "scene_id": "scene.sword_manor",
            "world_time": "SE-0061-08-07T07:29:58",
            "location_id": "place.sword_manor",
            "observable_pressures": ["Exam evaluation is active."],
            "promotion_exam_handoffs": [
                {
                    "cycle_id": cycle_id,
                    "phase": "field_evaluation",
                    "public_stage_results": {
                        "qualification": public_rows[:42],
                        "field_evaluation": public_rows[42:],
                    },
                    "public_stage_result_summaries": {
                        "qualification": {"candidate_count": 42, "pass_count": 29, "fail_count": 13},
                        "field_evaluation": {"candidate_count": 42, "pass_count": 30, "fail_count": 12},
                    },
                    "public_stage_result_count": 84,
                }
            ],
        },
        "player": {"name": "Wei Tang"},
        "person_reads": {
            "suggested_owner_ids": [f"char.house_{index:03d}" for index in range(80)],
            "total_permitted_ids": 80,
            "suggested_ids_truncated": False,
        },
        "object_reads": {
            "supported_ref_prefixes": ["team.", "mission."],
            "use": "inspect one bounded object",
        },
        "commands": {
            "supported_command_types": sorted(command_types),
            "command_types": command_types,
        },
        "narration": {
            "primary_module_id": "institutional",
            "modules": [
                {"module_id": "institutional", "guidance": "z" * 8192},
                {"module_id": "social", "guidance": "q" * 8192},
            ],
        },
        "context_policy": {"projection": "player_visible_bounded_handoff"},
    }

    # This is a legal internal assembly but intentionally larger than the public
    # wire context should be.
    validate_bounded_json(context, label="play context", allow_float=True)
    compact = compact_play_context(context)
    validate_bounded_json(compact, label="compact play context", allow_float=True)

    assert "command_types" not in compact["commands"]
    assert compact["narration"]["module_ids"] == ["institutional", "social"]
    assert "modules" not in compact["narration"]
    assert len(compact["person_reads"]["suggested_owner_ids"]) == 32
    handoff = compact["scene"]["promotion_exam_handoffs"][0]
    assert "public_stage_results" not in handoff
    assert handoff["public_stage_results_in_context"] is False
    assert handoff["public_stage_result_read_refs"] == {
        "qualification": f"exam-results:{cycle_id}:qualification:0",
        "field_evaluation": f"exam-results:{cycle_id}:field_evaluation:0",
    }
    assert "exam-results:" in compact["object_reads"]["supported_ref_prefixes"]
    assert "commands.command_types" in compact["context_policy"]["compacted_fields"]
