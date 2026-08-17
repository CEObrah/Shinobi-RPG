from __future__ import annotations

from types import SimpleNamespace

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands import campaign_investigation_environment as module
from shinobi_runtime.commands.campaign_investigation_environment import (
    _adjust_quality,
    _environment_context,
)
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.sim.events import CampaignTime


class _Briefing:
    def to_record(self):
        return {
            "objective_kind": "investigate",
            "subject_kind": "place",
            "subject_ref": "place.konoha.training_ground_3",
        }


class _Planner:
    repository = object()

    def __init__(self, *, scene_location="place.konoha.training_ground_3"):
        self.scene_location = scene_location
        objective = SimpleNamespace(kind="investigate")
        self.owner = SimpleNamespace(
            mission=SimpleNamespace(objective_by_id={"objective.test": objective}),
            briefing=_Briefing(),
        )

    def _scene_base(self, _current_time):
        return {"location_id": self.scene_location}

    def _investigation_mechanics(self):
        return {"schema": "investigation-mechanics"}

    def _read_mission(self, mission_ref, *, actor_id, current_time):
        assert mission_ref == "mission.test"
        assert actor_id == "pc_wei_tang"
        return "state/mission/mission.test.json", self.owner

    def _matching_profile(self, mechanics, objective_kind, brief):
        assert objective_kind == "investigate"
        assert brief["subject_ref"] == "place.konoha.training_ground_3"
        return "profile.test", {
            "locate_scene": {
                "skill_paths": ["attributes.awareness", "operational_skills.tracking"]
            },
            "examine_scene": {
                "roles": {
                    "perimeter": {"skills": ["attributes.awareness", "operational_skills.tracking"]},
                    "records": {"skills": ["attributes.intelligence", "operational_skills.investigation"]},
                    "witnesses": {"skills": ["attributes.presence", "operational_skills.investigation"]},
                    "synthesis": {"skills": ["attributes.intelligence", "operational_skills.tactics"]},
                }
            },
        }


def _command(action: str, *, place_ref="place.konoha.training_ground_3") -> CommandEnvelope:
    payload = {
        "action": action,
        "mission_ref": "mission.test",
        "objective_id": "objective.test",
        "place_ref": place_ref,
        "target_time": "SE-0061-08-07T12:00:00",
        "participant_refs": ["pc_wei_tang"],
    }
    if action == "examine_scene":
        payload["case_ref"] = "investigation.test"
    return CommandEnvelope(
        campaign_id="shinobi-test",
        request_id="investigation-environment-test",
        actor_id="pc_wei_tang",
        command_type="investigation_resolution",
        expected_revision=1,
        submitted_at="2026-08-17T00:00:00Z",
        payload=payload,
        mode="gameplay",
    )


def test_locate_scene_maps_authored_environment_to_its_skill_lane(monkeypatch) -> None:
    monkeypatch.setattr(
        module,
        "environment_action_profile",
        lambda _repository, **kwargs: {
            "action_key": kwargs["action_key"],
            "applied": True,
            "factor_milli": 800,
            "sample_count": 5,
            "channels": ["visibility_milli"],
        },
    )
    profiles, summaries = _environment_context(
        _Planner(),
        _command("locate_scene"),
        CampaignTime.parse("SE-0061-08-07T08:00:00"),
    )

    key = ("attributes.awareness", "operational_skills.tracking")
    assert profiles[key]["factor_milli"] == 800
    assert summaries[0]["action_key"] == "investigation.locate_scene"
    assert _adjust_quality(100, profiles[key]) == 80


def test_examine_scene_applies_only_authored_perimeter_policy(monkeypatch) -> None:
    seen = []

    def fake_profile(_repository, **kwargs):
        seen.append(kwargs["action_key"])
        applied = kwargs["action_key"] == "investigation.examine_scene.perimeter"
        return {
            "action_key": kwargs["action_key"],
            "applied": applied,
            "factor_milli": 750 if applied else 1000,
            "sample_count": 5 if applied else 0,
            "channels": ["track_evidence_milli"] if applied else [],
        }

    monkeypatch.setattr(module, "environment_action_profile", fake_profile)
    profiles, summaries = _environment_context(
        _Planner(),
        _command("examine_scene"),
        CampaignTime.parse("SE-0061-08-07T08:00:00"),
    )

    perimeter = ("attributes.awareness", "operational_skills.tracking")
    records = ("attributes.intelligence", "operational_skills.investigation")
    witnesses = ("attributes.presence", "operational_skills.investigation")
    synthesis = ("attributes.intelligence", "operational_skills.tactics")
    assert profiles[perimeter]["factor_milli"] == 750
    assert profiles[records]["applied"] is False
    assert profiles[witnesses]["applied"] is False
    assert profiles[synthesis]["applied"] is False
    assert [row["action_key"] for row in summaries] == [
        "investigation.examine_scene.perimeter"
    ]
    assert set(seen) == {
        "investigation.examine_scene.perimeter",
        "investigation.examine_scene.records",
        "investigation.examine_scene.witnesses",
        "investigation.examine_scene.synthesis",
    }


def test_wrong_scene_or_command_place_is_rejected_before_weather_sampling(monkeypatch) -> None:
    called = False

    def fake_profile(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("weather must not be sampled for a wrong investigation site")

    monkeypatch.setattr(module, "environment_action_profile", fake_profile)
    with pytest.raises(CommandRejectedError, match="investigation_scene_location_required"):
        _environment_context(
            _Planner(scene_location="place.konoha"),
            _command("locate_scene"),
            CampaignTime.parse("SE-0061-08-07T08:00:00"),
        )
    assert called is False

    with pytest.raises(CommandRejectedError, match="investigation_scene_location_required"):
        _environment_context(
            _Planner(),
            _command("locate_scene", place_ref="place.konoha"),
            CampaignTime.parse("SE-0061-08-07T08:00:00"),
        )
    assert called is False
