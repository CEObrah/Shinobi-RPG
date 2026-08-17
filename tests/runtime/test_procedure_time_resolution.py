from __future__ import annotations

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands import downtime_until_event as module
from shinobi_runtime.commands.core import _BuiltPlan
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.downtime_until_event import (
    _procedure_time_resolution,
    install_downtime_until_event,
)
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.sim.events import CampaignTime


class _Repository:
    def read_json(self, path: str):
        assert path == "game/data/mechanics/procedure-time.json"
        return {
            "schema": "procedure-time-rules",
            "version": 1,
            "procedures": {
                "examination_evaluation": {
                    "duration_seconds": 3600,
                },
                "brief_exchange": {
                    "duration_seconds": 300,
                },
            },
        }


class _Planner:
    repository = _Repository()
    scene_path = "state/scene.json"

    def __init__(self, *, scene_id: str = "scene.current") -> None:
        self.scene = {
            "schema": "scene",
            "scene_id": scene_id,
            "active_combat": False,
            "time_passage_allowed": True,
            "decision_required": None,
        }

    def _scene_base(self, _current_time):
        return dict(self.scene)


def _command(kind: str = "examination_evaluation", scene_id: str = "scene.current") -> CommandEnvelope:
    return CommandEnvelope(
        campaign_id="shinobi-test",
        request_id="procedure-test",
        actor_id="pc_wei_tang",
        command_type="procedure_time_resolution",
        expected_revision=10,
        submitted_at="2026-08-17T00:00:00Z",
        payload={"scene_id": scene_id, "procedure_kind": kind},
        mode="gameplay",
    )


def _base_plan(world_time: str, *, stop_kind: str, continuation: str | None = None) -> _BuiltPlan:
    result = {
        "world_time": world_time,
        "advance_until_event": {
            "stop_kind": stop_kind,
        },
    }
    if continuation is not None:
        result["continuation_required"] = True
        result["continuation_target"] = continuation
    return _BuiltPlan(
        code="advance_until_event",
        affected_refs=("state/meta.json",),
        writes={"state/meta.json": b"{}"},
        result=result,
        validator=lambda _overlay, _manifest: None,
    )


def test_procedure_time_command_is_registered_without_caller_duration() -> None:
    install_downtime_until_event()

    assert "procedure_time_resolution" in COMMAND_SPECS
    spec = COMMAND_SPECS["procedure_time_resolution"]
    assert spec.required_fields == ("scene_id", "procedure_kind")
    assert "duration" not in spec.required_fields
    assert "procedure_time_resolution" in RepositoryCommandPlanner.COMMAND_TYPES
    assert callable(getattr(RepositoryCommandPlanner, "_procedure_time_resolution", None))


def test_procedure_uses_authored_duration_and_causal_event_seeking(monkeypatch) -> None:
    planner = _Planner()
    current = CampaignTime.parse("SE-0061-08-07T07:29:58")
    expected_target = current.add_seconds(3600)
    seen = {}

    def fake_advance(_self, inner, _meta, _current):
        seen["target_time"] = inner.payload["target_time"]
        return _base_plan(str(expected_target), stop_kind="internal_boundary")

    monkeypatch.setattr(module, "_advance_until_event", fake_advance)
    plan = _procedure_time_resolution(planner, _command(), {}, current)

    assert seen["target_time"] == str(expected_target)
    assert plan.result["procedure_time"] == {
        "procedure_kind": "examination_evaluation",
        "scene_id": "scene.current",
        "start_time": str(current),
        "authored_duration_seconds": 3600,
        "target_time": str(expected_target),
        "reached_time": str(expected_target),
        "completed": True,
        "stop_kind": "internal_boundary",
    }
    assert "continuation_required" not in plan.result


def test_internal_scheduler_boundary_preserves_exact_original_procedure_target(monkeypatch) -> None:
    planner = _Planner()
    current = CampaignTime.parse("SE-0061-08-07T07:29:58")
    target = current.add_seconds(3600)
    boundary = current.add_seconds(600)

    monkeypatch.setattr(
        module,
        "_advance_until_event",
        lambda *_args, **_kwargs: _base_plan(
            str(boundary),
            stop_kind="internal_boundary",
            continuation=str(target),
        ),
    )
    plan = _procedure_time_resolution(planner, _command(), {}, current)

    assert plan.result["procedure_time"]["completed"] is False
    assert plan.result["continuation_required"] is True
    assert plan.result["continuation_target"] == str(target)


def test_soft_player_facing_event_interrupts_procedure_without_becoming_continuation(monkeypatch) -> None:
    planner = _Planner()
    current = CampaignTime.parse("SE-0061-08-07T07:29:58")
    reached = current.add_seconds(900)

    monkeypatch.setattr(
        module,
        "_advance_until_event",
        lambda *_args, **_kwargs: _base_plan(
            str(reached),
            stop_kind="player_facing_event",
            continuation=str(current.add_seconds(3600)),
        ),
    )
    plan = _procedure_time_resolution(planner, _command(), {}, current)

    assert plan.result["procedure_time"]["completed"] is False
    assert plan.result["procedure_time"]["stop_kind"] == "player_facing_event"
    assert "continuation_required" not in plan.result
    assert "continuation_target" not in plan.result


def test_stale_scene_or_protected_decision_cannot_consume_procedure_time(monkeypatch) -> None:
    planner = _Planner(scene_id="scene.fresh")
    current = CampaignTime.parse("SE-0061-08-07T07:29:58")
    called = False

    def fake_advance(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("time settlement should not be reached")

    monkeypatch.setattr(module, "_advance_until_event", fake_advance)
    with pytest.raises(CommandRejectedError, match="procedure_scene_stale"):
        _procedure_time_resolution(planner, _command(scene_id="scene.stale"), {}, current)
    assert called is False

    planner.scene["scene_id"] = "scene.current"
    planner.scene["decision_required"] = "Wei must choose."
    with pytest.raises(CommandRejectedError, match="procedure_time_not_available"):
        _procedure_time_resolution(planner, _command(), {}, current)
    assert called is False
