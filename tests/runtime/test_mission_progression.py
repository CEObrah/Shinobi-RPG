from __future__ import annotations

import json

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.mission_progression import (
    _evidence_already_used,
    _mission_objective_progress_resolution,
    _normalize_routine_mission_handoff,
    _team_doctrine_modifier,
    install_mission_progression,
)
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.reducers.missions import Mission, MissionObjective
from shinobi_runtime.sim.events import CampaignTime


class _Repo:
    def __init__(self):
        self.world_events = {
            "owner_id": "registry.world_events",
            "owner_type": "world_event_registry",
            "events": [
                {
                    "id": "event.evidence",
                    "kind": "information_claim_created",
                    "status": "resolved",
                    "causal_refs": ["mission.test"],
                    "material_consequence_refs": ["information:claim.test"],
                    "timing": {"occurred_at": "SE-0061-08-07T08:00:00"},
                    "execution": {"baseline_ref": "baseline.test"},
                }
            ],
            "archive_refs": [],
        }
        self.rules = {
            "schema": "mission-progress-rules",
            "version": 1,
            "base_progress_milli_by_event_kind": {"information_claim_created": 220},
            "objective_mode": {"investigate": "reconnaissance"},
            "doctrine": {
                "familiarity_bonus_floor": 50,
                "familiarity_bonus_per_point_milli": 4,
                "matching_mission_mode_bonus_milli": 100,
                "complete_role_coverage_bonus_milli": 50,
                "maximum_total_multiplier_milli": 1300,
            },
            "maximum_nonterminal_progress_milli": 900,
        }
        self.team = {
            "id": "team.test",
            "member_refs": ["pc_wei_tang", "char.a"],
            "doctrine_ref": "team.test.doctrine",
        }
        self.doctrine = {
            "id": "team.test.doctrine",
            "status": "active",
            "familiarity": {"pc_wei_tang": 100, "char.a": 100},
            "roles": {"pc_wei_tang": "lead", "char.a": "recon"},
            "mission_modes": [{"mode": "reconnaissance", "directive": "verify"}],
        }

    def read_json(self, path):
        if path == "game/data/mechanics/mission-progress.json":
            return self.rules
        if path == "state/reg/world-events.json":
            return self.world_events
        if path == "state/team/test.json":
            return self.team
        if path == "state/team/doctrine/test.json":
            return self.doctrine
        raise FileNotFoundError(path)

    def digest(self, path):
        return "digest:" + path


class _Owner:
    def __init__(self, mission: Mission):
        self.operation_ref = "team.test"
        self.mission = mission
        self.opened_at = CampaignTime.parse("SE-0061-08-01T07:00:00")

    def with_mission(self, mission: Mission, *, effective_at):
        result = _Owner(mission)
        result.opened_at = self.opened_at
        return result


class _Planner:
    scene_path = "state/scene.json"

    def __init__(self):
        self.repository = _Repo()
        self.owner = _Owner(
            Mission(
                mission_id="mission.test",
                state="active",
                participant_refs=("pc_wei_tang", "char.a"),
                objectives=(
                    MissionObjective(
                        objective_id="objective.investigate",
                        kind="investigate",
                        required=True,
                    ),
                ),
            )
        )

    def _read_mission(self, _mission_id, *, actor_id, current_time):
        assert actor_id == "pc_wei_tang"
        return "state/mission/mission.test.json", self.owner

    def _mission_objective_evidence(self, **kwargs):
        assert kwargs["target_status"] == "in_progress"
        assert kwargs["evidence_event_id"] == "event.evidence"
        return "event.evidence", "digest:state/reg/world-events.json"

    def _world_events(self):
        return self.repository.world_events

    def _world_event_record_and_digest(self, event_id, *, registry):
        assert registry is self.repository.world_events
        assert event_id == "event.evidence"
        return registry["events"][0], "digest:state/reg/world-events.json"

    def _exact_team(self, team_ref):
        assert team_ref == "team.test"
        return "state/team/test.json", self.repository.team

    def _resolve_covered_owner_view(self, doctrine_ref, *, cache):
        assert doctrine_ref == "team.test.doctrine"
        return "state/team/doctrine/test.json", "digest:doctrine", self.repository.doctrine

    def _mission_progress_built_plan(
        self,
        *,
        owner,
        result,
        material_consequence_refs,
        **kwargs,
    ):
        self.owner = owner
        event_id = "event.mission_objective_progressed.test"
        registry = {
            **self.repository.world_events,
            "events": [
                *self.repository.world_events["events"],
                {
                    "id": event_id,
                    "kind": "mission_objective_progressed",
                    "material_consequence_refs": list(material_consequence_refs),
                },
            ],
        }
        return _BuiltPlan(
            "mission_objective_progress_ready",
            ("state/scene.json", "state/reg/world-events.json"),
            {
                "state/scene.json": _json_bytes({
                    "schema": "scene",
                    "active_combat": False,
                    "time_passage_allowed": True,
                    "decision_required": None,
                }),
                "state/reg/world-events.json": _json_bytes(registry),
            },
            {**result, "semantic_event_id": event_id},
            lambda _overlay, _manifest: None,
        )


def _command():
    return CommandEnvelope(
        campaign_id="shinobi-test",
        request_id="mission-progress-test",
        actor_id="pc_wei_tang",
        command_type="mission_objective_progress_resolution",
        expected_revision=10,
        submitted_at="2026-08-17T00:00:00Z",
        payload={
            "mission_id": "mission.test",
            "objective_id": "objective.investigate",
            "evidence_event_id": "event.evidence",
        },
        mode="gameplay",
    )


def test_progress_command_is_runtime_derived_not_caller_percentage() -> None:
    install_mission_progression()
    spec = COMMAND_SPECS["mission_objective_progress_resolution"]
    assert spec.required_fields == ("mission_id", "objective_id", "evidence_event_id")
    assert "progress_milli" not in spec.required_fields


def test_high_familiarity_matching_doctrine_improves_real_evidence() -> None:
    planner = _Planner()
    multiplier, profile, guarded = _team_doctrine_modifier(
        planner,
        planner.owner,
        "investigate",
        planner.repository.rules,
    )
    assert multiplier == 1300
    assert profile == {
        "doctrine_applied": True,
        "matching_mission_mode": True,
        "complete_role_coverage": True,
        "coordination_band": "high",
    }
    assert guarded["state/team/test.json"] == "digest:state/team/test.json"
    assert guarded["state/team/doctrine/test.json"] == "digest:doctrine"


def test_one_evidence_event_cannot_be_farmed_twice() -> None:
    repo = _Repo()
    token = "mission_progress_evidence:mission.test:objective.investigate:event.evidence"
    assert _evidence_already_used(repo, token) is False
    repo.world_events["events"].append({"material_consequence_refs": [token]})
    assert _evidence_already_used(repo, token) is True


def test_progress_resolution_caps_below_terminal_success_and_marks_history() -> None:
    planner = _Planner()
    plan = _mission_objective_progress_resolution(
        planner,
        _command(),
        {},
        CampaignTime.parse("SE-0061-08-07T08:01:00"),
    )

    objective = planner.owner.mission.objective_by_id["objective.investigate"]
    assert objective.status == "in_progress"
    assert objective.progress_milli == 286  # 220 * 1.3
    assert objective.progress_milli < 1000
    assert plan.result["progress_delta_milli"] == 286
    registry = json.loads(plan.writes["state/reg/world-events.json"].decode("utf-8"))
    progress_event = next(
        row for row in registry["events"]
        if row.get("id") == "event.mission_objective_progressed.test"
    )
    assert progress_event["kind"] == "mission_objective_progressed"
    assert any(
        value.startswith("mission_progress_evidence:")
        for value in progress_event["material_consequence_refs"]
    )


def test_progress_at_nonterminal_cap_requires_terminal_evidence() -> None:
    planner = _Planner()
    current = planner.owner.mission.objective_by_id["objective.investigate"]
    capped = MissionObjective(
        objective_id=current.objective_id,
        kind=current.kind,
        required=current.required,
        status="in_progress",
        progress_milli=900,
    )
    planner.owner = _Owner(
        Mission(
            mission_id="mission.test",
            state="active",
            participant_refs=("pc_wei_tang", "char.a"),
            objectives=(capped,),
        )
    )
    with pytest.raises(CommandRejectedError, match="mission_progress_requires_terminal_evidence"):
        _mission_objective_progress_resolution(
            planner,
            _command(),
            {},
            CampaignTime.parse("SE-0061-08-07T08:01:00"),
        )


def test_routine_mission_result_does_not_manufacture_hard_decision() -> None:
    base = _BuiltPlan(
        "mission_objective_update_ready",
        ("state/scene.json",),
        {
            "state/scene.json": _json_bytes({
                "schema": "scene",
                "active_combat": False,
                "time_passage_allowed": False,
                "decision_required": "Objective changed. Choose again.",
            })
        },
        {},
        lambda _overlay, _manifest: None,
    )
    command = CommandEnvelope(
        campaign_id="shinobi-test",
        request_id="mission-update-test",
        actor_id="pc_wei_tang",
        command_type="mission_objective_update",
        expected_revision=10,
        submitted_at="2026-08-17T00:00:00Z",
        payload={},
        mode="gameplay",
    )
    normalized = _normalize_routine_mission_handoff(base, command, "state/scene.json")
    scene = json.loads(normalized.writes["state/scene.json"].decode("utf-8"))
    assert scene["decision_required"] is None
    assert scene["time_passage_allowed"] is True
