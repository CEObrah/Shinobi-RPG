import json

from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.downtime_until_event import (
    _meaningful,
    _staged_player_facing_event,
    _stop_kind,
    install_downtime_until_event,
)
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.commands.downtime_vitality import normalize_time_handoff_plan
from shinobi_runtime.api.scene_vitality import apply_scene_vitality_handoff


def _plan(*, interrupted: bool, decision_required: object, reports=()):
    scene = {
        "schema": "scene",
        "world_time": "SE-0061-06-11T07:00:00",
        "time_passage_allowed": not interrupted,
        "decision_required": decision_required,
        "observable_pressures": [],
        "narrative": {"available_reports": list(reports)},
    }
    return _BuiltPlan(
        code="advance_time_ready",
        affected_refs=("state/scene.json",),
        writes={"state/scene.json": _json_bytes(scene)},
        result={"interrupted": interrupted, "world_time": scene["world_time"]},
        validator=lambda _overlay, _manifest: None,
    )


def test_advance_until_event_is_registered():
    install_downtime_until_event()
    assert "advance_until_event" in COMMAND_SPECS
    assert "advance_until_event" in RepositoryCommandPlanner.COMMAND_TYPES
    assert callable(getattr(RepositoryCommandPlanner, "_advance_until_event", None))


def test_internal_boundary_is_not_a_story_stop():
    result = {"interrupted": False, "autonomous_actions": [], "team_reviews": []}
    assert _stop_kind(result) == "internal_boundary"
    assert _meaningful(result) is False


def test_hard_interrupt_is_always_a_stop():
    result = {"interrupted": True, "autonomous_actions": [], "team_reviews": []}
    assert _stop_kind(result) == "hard_decision"
    assert _meaningful(result) is True


def test_delivered_report_is_a_soft_player_facing_stop():
    result = {
        "interrupted": False,
        "autonomous_actions": [
            {"kind": "information_report", "player_report_deliveries": [{"delivery_id": "delivery.test"}]}
        ],
    }
    assert _stop_kind(result) == "player_facing_event"
    assert _meaningful(result) is True


def test_fresh_scene_report_is_an_extensible_soft_stop_signal():
    plan = _plan(interrupted=False, decision_required=None, reports=("A report arrived.",))
    assert _staged_player_facing_event(plan, "state/scene.json") is True


def test_non_interrupting_time_handoff_clears_stale_decision_marker():
    plan = _plan(
        interrupted=False,
        decision_required="The prior unresolved decision surface remains.",
    )
    normalized = normalize_time_handoff_plan(plan, "state/scene.json")
    scene = json.loads(normalized.writes["state/scene.json"].decode("utf-8"))
    assert scene["decision_required"] is None
    assert scene["time_passage_allowed"] is True
    assert normalized.result["player_boundary_kind"] == "none"


def test_hard_time_handoff_preserves_required_decision():
    plan = _plan(interrupted=True, decision_required="Choose whether to accept.")
    normalized = normalize_time_handoff_plan(plan, "state/scene.json")
    scene = json.loads(normalized.writes["state/scene.json"].decode("utf-8"))
    assert scene["decision_required"] == "Choose whether to accept."
    assert scene["time_passage_allowed"] is False


def test_open_live_projection_never_exports_stale_hard_decision():
    payload = {
        "scene": {
            "time_passage_allowed": True,
            "decision_required": "Stale decision marker",
        }
    }
    normalized = apply_scene_vitality_handoff(payload, scene_cast={})
    assert normalized["scene"]["decision_required"] is None


def test_closed_live_projection_preserves_real_hard_decision():
    payload = {
        "scene": {
            "time_passage_allowed": False,
            "decision_required": "Real protected choice",
        }
    }
    normalized = apply_scene_vitality_handoff(payload, scene_cast={})
    assert normalized["scene"]["decision_required"] == "Real protected choice"
