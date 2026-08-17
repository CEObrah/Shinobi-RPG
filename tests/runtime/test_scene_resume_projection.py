import json

from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.mission_context_index import blank_mission_context_index
from shinobi_runtime.commands.scene_resume_projection import (
    _current_mission_refs,
    _repair_terminal_commitment_projection,
    normalize_scene_resume_plan,
)

PATH = "state/scene.json"


def _scene():
    return {
        "schema": "scene",
        "scene_id": "scene_old",
        "scene_summary": "Current July scene.",
        "world_time": "SE-0061-07-02T07:00:00",
        "narrative": {"last_scene_summary": "Stale February scene.", "last_major_choice": "Old choice."},
    }


def _plan(scene):
    return _BuiltPlan("ready", (PATH,), {PATH: _json_bytes(scene)}, {}, lambda _o, _m: None)


def test_refreshes_scene_identity_and_summary_cache():
    plan = normalize_scene_resume_plan(_plan(_scene()), PATH, command_type="advance_time", command_mode="gameplay")
    scene = json.loads(plan.writes[PATH])
    assert scene["scene_id"].startswith("scene_resume_")
    assert scene["scene_id"] != "scene_old"
    assert scene["narrative"]["last_scene_summary"] == scene["scene_summary"]


def test_passive_advance_preserves_last_major_choice():
    plan = normalize_scene_resume_plan(_plan(_scene()), PATH, command_type="advance_until_event", command_mode="gameplay")
    scene = json.loads(plan.writes[PATH])
    assert scene["narrative"]["last_major_choice"] == "Old choice."


def test_consequential_gameplay_refreshes_last_major_choice():
    plan = normalize_scene_resume_plan(_plan(_scene()), PATH, command_type="training_resolution", command_mode="gameplay")
    scene = json.loads(plan.writes[PATH])
    assert scene["narrative"]["last_major_choice"] == scene["scene_summary"]


def test_missing_player_mission_route_is_valid_empty_set():
    index = blank_mission_context_index()
    assert _current_mission_refs(index, "pc_wei_tang") == []


def test_terminal_commitment_clears_stale_decision_and_backend_pressure_text():
    scene = {
        "schema": "scene",
        "active_combat": False,
        "decision_required": (
            "The boundary commitment.team_fujin.exam_priority.0061-07-22 "
            "requires an explicit player response."
        ),
        "time_passage_allowed": False,
        "observable_pressures": [
            "Konoha's current Chunin Examination has entered qualification.",
            "A known world pressure has materially changed.",
        ],
        "narrative": {
            "promises_and_threats": [
                "commitment.team_fujin.exam_priority.0061-07-22",
                "commitment.team_fujin.permanent_training.0061-07-30",
            ],
            "approaching_consequences": [
                "A known strategic pressure has reached crisis-level consequences."
            ],
        },
    }
    changed = _repair_terminal_commitment_projection(
        scene,
        {
            "commitment.team_fujin.exam_priority.0061-07-22": "completed",
            "commitment.team_fujin.permanent_training.0061-07-30": "active",
        },
    )

    assert changed is True
    assert scene["decision_required"] is None
    assert scene["time_passage_allowed"] is True
    assert scene["observable_pressures"] == [
        "Konoha's current Chunin Examination has entered qualification."
    ]
    assert scene["narrative"]["promises_and_threats"] == [
        "commitment.team_fujin.permanent_training.0061-07-30"
    ]
    assert "approaching_consequences" not in scene["narrative"]
