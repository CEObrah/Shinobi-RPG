import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.runtime_stability import _normalize_faction_review_event
from shinobi_runtime.sim.events import CampaignTime, ScheduledEvent


class _Repository:
    def __init__(self, owner):
        self.owner = owner

    def read_json(self, path):
        assert path == "state/reg/factions/faction-akatsuki.json"
        return self.owner


def _event(payload):
    return ScheduledEvent.build(
        due_at=CampaignTime.parse("SE-0061-06-18T07:00:00"),
        priority=40,
        event_id="event.test.legacy-faction-review",
        kind="faction.periodic_review",
        source_host="host.faction.faction.akatsuki",
        target_host="host.faction.faction.akatsuki",
        payload=payload,
        dedupe_key="faction.akatsuki:periodic-review",
        visibility="world_only",
        requires_player=False,
    )


def _owner(faction_id="faction.akatsuki"):
    return {
        "schema": "faction-owner",
        "faction": {
            "id": faction_id,
            "status": "active",
            "plan_state": {"status": "active"},
        },
    }


def test_verified_legacy_identity_is_promoted_to_faction_id():
    event = _event(
        {
            "identity": "faction.akatsuki",
            "owner_ref": "state/reg/factions/faction-akatsuki.json",
        }
    )
    normalized = _normalize_faction_review_event(event, _Repository(_owner()))

    assert normalized.payload["identity"] == "faction.akatsuki"
    assert normalized.payload["faction_id"] == "faction.akatsuki"
    assert normalized.event_id == event.event_id
    assert normalized.fingerprint != event.fingerprint


def test_current_canonical_faction_review_is_left_unchanged():
    event = _event(
        {
            "faction_id": "faction.akatsuki",
            "owner_ref": "state/reg/factions/faction-akatsuki.json",
        }
    )
    normalized = _normalize_faction_review_event(event, _Repository(_owner()))

    assert normalized is event


def test_legacy_identity_must_match_referenced_faction_owner():
    event = _event(
        {
            "identity": "faction.konoha",
            "owner_ref": "state/reg/factions/faction-akatsuki.json",
        }
    )

    with pytest.raises(CommandRejectedError) as exc_info:
        _normalize_faction_review_event(event, _Repository(_owner()))

    assert exc_info.value.code == "faction_owner_invalid"


def test_explicit_faction_id_must_match_referenced_faction_owner():
    event = _event(
        {
            "faction_id": "faction.konoha",
            "owner_ref": "state/reg/factions/faction-akatsuki.json",
        }
    )

    with pytest.raises(CommandRejectedError) as exc_info:
        _normalize_faction_review_event(event, _Repository(_owner()))

    assert exc_info.value.code == "faction_owner_invalid"
