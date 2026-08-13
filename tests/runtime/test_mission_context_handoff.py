import json
from types import SimpleNamespace

from shinobi_runtime.api import campaign_stable_operations as stable
from shinobi_runtime.store import RepositoryStore


def _command_descriptor(availability: str) -> dict[str, str]:
    return {"description": "test", "availability": availability}


def test_apply_current_mission_handoff_replaces_stale_terminal_routing():
    payload = {
        "campaign": {"player_id": "pc_wei_tang"},
        "commands": {
            "active_mission_owner_ids": ["mission.completed"],
            "command_types": {
                "mission_transition": _command_descriptor("available"),
                "mission_objective_update": _command_descriptor(
                    "requires_persisted_terminal_world_event_evidence"
                ),
                "mission_derive_and_settle": _command_descriptor("available"),
            },
        },
        "context_policy": {
            "truncated_fields": [
                "commands.active_mission_owner_ids",
                "narrative.known_clues",
            ]
        },
    }

    projected = stable._apply_current_mission_handoff(
        payload,
        mission_ids=("mission.current",),
        briefing_ids=("mission.current",),
        missions_truncated=False,
    )

    assert projected["commands"]["active_mission_owner_ids"] == ["mission.current"]
    assert projected["mission_reads"]["operational_brief_owner_ids"] == [
        "mission.current"
    ]
    assert "Inspect the exact mission owner" in projected["mission_reads"]["use"]
    assert projected["commands"]["command_types"]["mission_transition"][
        "availability"
    ] == "available"
    assert projected["commands"]["command_types"]["mission_objective_update"][
        "availability"
    ] == "requires_persisted_terminal_world_event_evidence"
    assert projected["context_policy"]["truncated_fields"] == [
        "narrative.known_clues"
    ]


def test_apply_current_mission_handoff_blocks_mission_commands_without_current_work():
    payload = {
        "commands": {
            "active_mission_owner_ids": ["mission.completed"],
            "command_types": {
                "mission_transition": _command_descriptor("available"),
                "mission_objective_update": _command_descriptor(
                    "requires_persisted_terminal_world_event_evidence"
                ),
                "mission_derive_and_settle": _command_descriptor("available"),
            },
        },
        "context_policy": {"truncated_fields": []},
    }

    projected = stable._apply_current_mission_handoff(
        payload,
        mission_ids=(),
        briefing_ids=(),
        missions_truncated=False,
    )

    assert projected["commands"]["active_mission_owner_ids"] == []
    assert projected["mission_reads"]["operational_brief_owner_ids"] == []
    for command_name in (
        "mission_transition",
        "mission_objective_update",
        "mission_derive_and_settle",
    ):
        assert projected["commands"]["command_types"][command_name][
            "availability"
        ] == "no_mission_owner"


def test_current_player_mission_context_filters_terminal_before_context_cap(
    tmp_path,
    monkeypatch,
):
    mission_directory = tmp_path / "state" / "mission"
    mission_directory.mkdir(parents=True)

    records = []
    for index in range(20):
        records.append(
            {
                "mission_id": f"mission.aaa.terminal.{index:02d}",
                "state": "succeeded",
                "participant_refs": ["pc_wei_tang"],
                "briefing": True,
            }
        )
    for index in range(18):
        records.append(
            {
                "mission_id": f"mission.zzz.current.{index:02d}",
                "state": "active",
                "participant_refs": ["pc_wei_tang"],
                "briefing": index in (0, 17),
            }
        )
    records.append(
        {
            "mission_id": "mission.hidden",
            "state": "active",
            "participant_refs": ["person.hidden"],
            "briefing": True,
        }
    )

    for record in records:
        path = mission_directory / f"{record['mission_id']}.json"
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    class FakeMissionOwner:
        @staticmethod
        def from_record(record):
            return SimpleNamespace(
                mission=SimpleNamespace(
                    participant_refs=tuple(record["participant_refs"]),
                    state=record["state"],
                ),
                mission_id=record["mission_id"],
                briefing=object() if record["briefing"] else None,
            )

    monkeypatch.setattr(stable, "MissionOwner", FakeMissionOwner)
    operations = stable.RouteAwareCampaignOperations.__new__(
        stable.RouteAwareCampaignOperations
    )
    operations.repository = RepositoryStore(tmp_path)

    mission_ids, briefing_ids, truncated = operations._current_player_mission_context(
        "pc_wei_tang"
    )

    assert len(mission_ids) == 16
    assert all(
        mission_id.startswith("mission.zzz.current.") for mission_id in mission_ids
    )
    assert "mission.zzz.current.00" in mission_ids
    assert "mission.zzz.current.17" not in mission_ids
    assert briefing_ids == ("mission.zzz.current.00",)
    assert truncated is True
