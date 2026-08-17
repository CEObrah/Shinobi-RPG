from __future__ import annotations

from shinobi_runtime.commands.team_checkin_records import (
    checkin_ref_for_event,
    project_team_checkin,
    snapshot_refs,
)


class _Repository:
    def __init__(self, event):
        self.event = event

    def read_json(self, path):
        assert path == "state/reg/world-events.json"
        return {
            "schema": "world-event-registry",
            "events": [self.event],
            "archive_refs": [],
        }


def test_event_snapshot_preserves_topics_ownership_and_observable_contact_mode() -> None:
    event_id = "event.player_led_team_checkin_ready.abc123"
    material = [
        "player_led_team_checkin:team.konoha.fujin:char.mei_arakawa",
        *snapshot_refs(
            "Team Fujin",
            ["latest mission lessons, delegated ownership, and follow-through"],
            ownership_cues=["team_can_own_follow_through"],
            contact_mode="direct_concise",
        ),
    ]
    event = {
        "id": event_id,
        "kind": "player_led_team_checkin_ready",
        "host_refs": ["team.konoha.fujin"],
        "actor_refs": ["char.mei_arakawa"],
        "material_consequence_refs": material,
        "timing": {"occurred_at": "SE-0061-08-07T08:00:00"},
        "visibility": {
            "audience_refs": ["pc_wei_tang"],
            "witness_refs": [],
        },
    }

    projected = project_team_checkin(
        _Repository(event),
        checkin_ref_for_event(event_id),
        "pc_wei_tang",
    )

    assert projected["team_name"] == "Team Fujin"
    assert projected["topic_cues"] == [
        "latest mission lessons, delegated ownership, and follow-through"
    ]
    assert projected["ownership_cues"] == ["team_can_own_follow_through"]
    assert projected["contact_mode"] == "direct_concise"
    assert projected["snapshot_basis"] == "event_snapshot"
