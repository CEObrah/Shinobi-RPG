from __future__ import annotations

from typing import Any

import pytest

from shinobi_runtime.commands import team_checkin_records as records


class _Repo:
    def __init__(self, values: dict[str, Any]):
        self.values = values

    def read_json(self, path: str) -> Any:
        if path not in self.values:
            raise FileNotFoundError(path)
        return self.values[path]


def _event(*, event_id: str, kind: str, material: list[str], causal: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": event_id,
        "kind": kind,
        "status": "resolved",
        "timing": {
            "scheduled_for": "SE-0061-06-26T21:15:00",
            "occurred_at": "SE-0061-06-26T21:15:00",
            "started_at": "SE-0061-06-26T21:15:00",
            "ended_at": "SE-0061-06-26T21:15:00",
        },
        "host_refs": ["team.blackhound"],
        "actor_refs": ["canon_hayama_shirakumo"],
        "place_refs": [],
        "causal_refs": causal or [],
        "affected_owner_refs": [],
        "material_consequence_refs": material,
        "visibility": {
            "classification": "restricted",
            "witness_refs": ["canon_hayama_shirakumo"],
            "audience_refs": ["pc_wei_tang"],
            "knowledge_refs": [],
            "route_refs": [],
        },
        "provenance": {
            "source_kind": "autonomous_host_review",
            "source_refs": ["canon_hayama_shirakumo"],
            "archetype_ref": None,
            "recorded_at": "SE-0061-06-26T21:15:00",
        },
        "execution": {
            "reducer_ref": "test",
            "transaction_ref": "tx.test",
            "receipt_refs": ["receipt.test"],
        },
        "supersedes_ref": None,
        "superseded_by_ref": None,
    }


def test_snapshotted_checkin_projects_exact_agenda_and_stays_unhandled() -> None:
    event_id = "event.player_led_team_checkin_ready.abc123"
    event = _event(
        event_id=event_id,
        kind="player_led_team_checkin_ready",
        material=[
            "player_led_team_checkin:team.blackhound:canon_hayama_shirakumo",
            *records.snapshot_refs("Black Hound", ["pursuit", "containment", "readiness, equipment, and the next training block"]),
        ],
    )
    repo = _Repo({
        "state/reg/world-events.json": {"events": [event], "archive_refs": []},
    })
    result = records.project_team_checkin(repo, "team_checkin.abc123", "pc_wei_tang")
    assert result["team_name"] == "Black Hound"
    assert result["contact_actor_ref"] == "canon_hayama_shirakumo"
    assert result["topic_cues"] == ["pursuit", "containment", "readiness, equipment, and the next training block"]
    assert result["snapshot_basis"] == "event_snapshot"
    assert result["handled"] is False


def test_handled_event_is_derived_from_causal_chain() -> None:
    ready_id = "event.player_led_team_checkin_ready.abc123"
    ready = _event(
        event_id=ready_id,
        kind="player_led_team_checkin_ready",
        material=[*records.snapshot_refs("Black Hound", ["pursuit"])],
    )
    handled = _event(
        event_id="event.player_led_team_checkin_handled.def456",
        kind="player_led_team_checkin_handled",
        material=["team_checkin_handling:discussed:team_checkin.abc123"],
        causal=[ready_id],
    )
    handled["actor_refs"] = ["pc_wei_tang"]
    handled["visibility"]["witness_refs"] = ["pc_wei_tang"]
    repo = _Repo({
        "state/reg/world-events.json": {"events": [ready, handled], "archive_refs": []},
    })
    result = records.project_team_checkin(repo, "team_checkin.abc123", "pc_wei_tang")
    assert result["handled"] is True
    assert result["handling"] == "discussed"
    assert result["handled_event_ref"] == "event.player_led_team_checkin_handled.def456"


def test_ready_event_can_be_read_from_archive() -> None:
    ready = _event(
        event_id="event.player_led_team_checkin_ready.abc123",
        kind="player_led_team_checkin_ready",
        material=[*records.snapshot_refs("Black Hound", ["pursuit"])],
    )
    repo = _Repo({
        "state/reg/world-events.json": {"events": [], "archive_refs": ["state/history/events/segment-000001.json"]},
        "state/history/events/segment-000001.json": {"events": [ready]},
    })
    result = records.project_team_checkin(repo, "team_checkin.abc123", "pc_wei_tang")
    assert result["source_event_ref"] == ready["id"]
    assert result["topic_cues"] == ["pursuit"]


def test_legacy_ready_event_reconstructs_current_generated_agenda(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Policies:
        def __init__(self, repository: Any):
            self.repository = repository

        def team_profile(self, team_type: str) -> dict[str, Any]:
            assert team_type == "special_mission_cell"
            return {"training_focus": ["pursuit", "containment"]}

    monkeypatch.setattr(records, "AutonomousPolicyBook", _Policies)
    ready = _event(
        event_id="event.player_led_team_checkin_ready.abc123",
        kind="player_led_team_checkin_ready",
        material=["player_led_team_checkin:team.blackhound:canon_hayama_shirakumo"],
    )
    repo = _Repo({
        "state/reg/world-events.json": {"events": [ready], "archive_refs": []},
        "team.blackhound": {
            "id": "team.blackhound",
            "name": "Black Hound",
            "team_type": "special_mission_cell",
            "current_assignment_ref": None,
        },
    })
    result = records.project_team_checkin(repo, "team_checkin.abc123", "pc_wei_tang")
    assert result["snapshot_basis"] == "legacy_reconstructed"
    assert result["topic_cues"] == ["pursuit", "containment", "readiness, equipment, and the next training block"]


def test_guessed_or_nonvisible_checkin_fails_closed() -> None:
    repo = _Repo({"state/reg/world-events.json": {"events": [], "archive_refs": []}})
    with pytest.raises(ValueError, match="team_checkin_not_player_visible"):
        records.project_team_checkin(repo, "team_checkin.unknown", "pc_wei_tang")
