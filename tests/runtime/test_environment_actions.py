from __future__ import annotations

from shinobi_runtime import environment_actions as module
from shinobi_runtime.environment_actions import environment_action_profile
from shinobi_runtime.sim.events import CampaignTime


class _Repository:
    def read_json(self, path):
        assert path == "game/data/mechanics/environment-actions.json"
        return {
            "schema": "environment-action-rules",
            "version": 1,
            "interval_sample_count": 5,
            "actions": {
                "investigation.locate_scene": {
                    "minimum_factor_milli": 700,
                    "maximum_factor_milli": 1200,
                    "channels": {
                        "visibility_milli": {"weight": 300, "polarity": "direct"},
                        "track_evidence_milli": {"weight": 500, "polarity": "direct"},
                        "scent_tracking_milli": {"weight": 200, "polarity": "direct"},
                    },
                }
            },
        }


def test_authored_action_averages_derived_channels_across_interval(monkeypatch) -> None:
    calls = []

    def fake_snapshot(_repository, *, world_time, location_ref):
        calls.append((world_time, location_ref))
        return {
            "mechanical_effects": {
                "visibility_milli": 800,
                "track_evidence_milli": 1200,
                "scent_tracking_milli": 1000,
            }
        }

    monkeypatch.setattr(module, "environment_snapshot", fake_snapshot)
    profile = environment_action_profile(
        _Repository(),
        start_time=CampaignTime.parse("SE-0061-08-07T08:00:00"),
        end_time=CampaignTime.parse("SE-0061-08-07T12:00:00"),
        place_ref="place.konoha.training_ground_3",
        action_key="investigation.locate_scene",
    )

    assert profile["factor_milli"] == 1040
    assert profile["sample_count"] == 5
    assert profile["channels"] == [
        "scent_tracking_milli",
        "track_evidence_milli",
        "visibility_milli",
    ]
    assert len(calls) == 5
    assert all(place == "place.konoha.training_ground_3" for _time, place in calls)


def test_unlisted_action_is_neutral_without_sampling_weather(monkeypatch) -> None:
    monkeypatch.setattr(
        module,
        "environment_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not sample")),
    )
    profile = environment_action_profile(
        _Repository(),
        start_time=CampaignTime.parse("SE-0061-08-07T08:00:00"),
        end_time=CampaignTime.parse("SE-0061-08-07T12:00:00"),
        place_ref="place.konoha",
        action_key="investigation.examine_scene.records",
    )

    assert profile == {
        "action_key": "investigation.examine_scene.records",
        "applied": False,
        "factor_milli": 1000,
        "sample_count": 0,
        "channels": [],
    }
