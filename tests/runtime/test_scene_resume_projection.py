import json

from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.scene_resume_projection import normalize_scene_resume_plan

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
