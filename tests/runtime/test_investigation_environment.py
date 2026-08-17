from __future__ import annotations

from shinobi_runtime.commands import campaign_investigation_environment as module
from shinobi_runtime.commands.campaign_investigation_environment import (
    _adjust_quality,
    _environment_context,
)
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.sim.events import CampaignTime


class _Planner:
    repository = object()

    def _scene_base(self, _current_time):
        return {"location_id": "place.konoha.training_ground_3"}

    def _load(self):
        return {"profiles": {}}

    def _objective_context(self, mission_ref, objective_id, actor_id, current_time):
        assert mission_ref == "mission.test"
        assert objective_id == "objective.test"
        assert actor_id == "pc_wei_tang"
        return object(), object(), object()

    def _profile_for_objective(self, mechanics, brief):
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


def _command(action: str) -> CommandEnvelope:
    payload = {
        "action": action,
        "mission_ref": "mission.test",
        "objective_id": "objective.test",
        "target_time": "SE-0061-08-07T12:00:00",
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
