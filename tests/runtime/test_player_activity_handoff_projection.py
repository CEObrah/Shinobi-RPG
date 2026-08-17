from __future__ import annotations

from shinobi_runtime.api import campaign_environment
from shinobi_runtime.api.player_activity_handoff_projection import derive_activity_handoff


def _scene(**updates):
    value = {
        "scene_summary": "Team Fujin has completed its field evaluation.",
        "decision_required": None,
        "narrative": {
            "last_major_choice": "Wei evaluates Team Fujin together.",
            "last_scene_summary": "The three candidates entered field evaluation together.",
            "current_scene_type": "institutional_command",
        },
    }
    value.update(updates)
    return value


def _completed_exam_handoff() -> dict:
    return {
        "cycle_id": "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07",
        "team_ref": "team.konoha.fujin",
        "phase": "field_evaluation",
        "evaluation_open": False,
        "unevaluated_candidate_refs": [],
        "next_phase": "finals",
        "next_phase_at": "SE-0061-08-16T07:29:58",
    }


def test_protected_decision_is_always_a_stop_boundary() -> None:
    handoff = derive_activity_handoff(
        _scene(decision_required="Wei must choose whether to accept the command posting.")
    )

    assert handoff["kind"] == "protected_decision"
    assert handoff["requires_player_decision"] is True
    assert handoff["interrupts_continuation"] is True
    assert handoff["continue_without_player"] is False


def test_completed_exam_stage_becomes_standing_wait_not_scene_completion() -> None:
    handoff = derive_activity_handoff(
        _scene(promotion_exam_handoffs=[_completed_exam_handoff()])
    )

    assert handoff["kind"] == "promotion_exam"
    assert handoff["status"] == "standing_wait"
    assert handoff["continue_without_player"] is True
    assert handoff["requires_player_decision"] is False
    assert handoff["interrupts_continuation"] is False
    assert handoff["next_stage"] == "finals"
    assert handoff["next_boundary_at"] == "SE-0061-08-16T07:29:58"


def test_open_exam_evaluation_is_a_real_player_decision() -> None:
    handoff = derive_activity_handoff(
        _scene(
            promotion_exam_handoffs=[
                {
                    "cycle_id": "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07",
                    "team_ref": "team.konoha.fujin",
                    "phase": "field_evaluation",
                    "evaluation_open": True,
                    "unevaluated_candidate_refs": ["char.kai", "char.mei_arakawa"],
                    "next_phase": "finals",
                    "next_phase_at": "SE-0061-08-16T07:29:58",
                }
            ]
        )
    )

    assert handoff["kind"] == "promotion_exam"
    assert handoff["status"] == "requires_player_decision"
    assert handoff["current_stage"] == "evaluation"
    assert handoff["requires_player_decision"] is True
    assert handoff["interrupts_continuation"] is True
    assert handoff["continue_without_player"] is False


def test_declared_time_horizon_remains_an_automatic_continuation() -> None:
    handoff = derive_activity_handoff(
        _scene(
            time_continuation={
                "continuation_target": "SE-0061-08-20T07:29:58",
                "remaining_causal_work": True,
            }
        )
    )

    assert handoff["kind"] == "time_continuation"
    assert handoff["status"] == "procedural_continuation"
    assert handoff["continue_without_player"] is True
    assert handoff["interrupts_continuation"] is False
    assert handoff["target_time"] == "SE-0061-08-20T07:29:58"


def test_team_checkin_interrupts_but_does_not_pretend_a_decision_already_exists() -> None:
    handoff = derive_activity_handoff(
        _scene(
            time_continuation={"continuation_target": "SE-0061-08-20T07:29:58"},
            team_checkin_handoffs=[
                {
                    "checkin_ref": "team_checkin.example",
                    "team_ref": "team.konoha.fujin",
                    "contact_actor_ref": "char.mei_arakawa",
                    "topic_cues": ["readiness", "role rotation"],
                }
            ],
        )
    )

    assert handoff["kind"] == "team_checkin"
    assert handoff["status"] == "player_facing_event"
    assert handoff["interrupts_continuation"] is True
    assert handoff["requires_player_decision"] is False
    assert handoff["continue_without_player"] is False
    assert handoff["topic_cues"] == ["readiness", "role rotation"]


def test_available_report_interrupts_for_presentation_before_any_menu() -> None:
    scene = _scene(
        time_continuation={"continuation_target": "SE-0061-08-20T07:29:58"}
    )
    scene["narrative"]["available_reports"] = ["A Black Hound field report is ready."]

    handoff = derive_activity_handoff(scene)

    assert handoff["kind"] == "report_handoff"
    assert handoff["status"] == "player_facing_event"
    assert handoff["interrupts_continuation"] is True
    assert handoff["requires_player_decision"] is False
    assert handoff["continue_without_player"] is False


def test_fallback_scene_never_invents_an_automatic_continuation() -> None:
    handoff = derive_activity_handoff(_scene())

    assert handoff["kind"] == "scene"
    assert handoff["status"] == "scene_open"
    assert handoff["interrupts_continuation"] is False
    assert handoff["continue_without_player"] is False


def test_production_context_adds_activity_handoff_before_budget_degradation(monkeypatch) -> None:
    rich = {
        "campaign": {
            "campaign_id": "shinobi-test",
            "revision": 74,
            "world_time": "SE-0061-08-07T07:29:58",
            "state_root": "a" * 64,
            "player_id": "pc_wei_tang",
        },
        "scene": _scene(
            promotion_exam_handoffs=[_completed_exam_handoff()],
            optional_bulk=[
                {"index": index, "values": list(range(20))}
                for index in range(256)
            ],
        ),
        "player": {},
        "person_reads": {},
        "object_reads": {},
        "commands": {"supported_command_types": []},
        "narration": {},
        "context_policy": {},
    }
    monkeypatch.setattr(campaign_environment._Base, "play_context", lambda self: rich)
    operations = campaign_environment.RouteAwareCampaignOperations.__new__(
        campaign_environment.RouteAwareCampaignOperations
    )

    projected = operations.play_context()

    assert projected["scene"]["activity_handoff"]["kind"] == "promotion_exam"
    assert projected["scene"]["activity_handoff"]["continue_without_player"] is True
    assert projected["scene"]["activity_handoff"]["next_stage"] == "finals"
    assert "optional_bulk" not in projected["scene"]
    assert projected["context_policy"]["degraded_projection"] is True
