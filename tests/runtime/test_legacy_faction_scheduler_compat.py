import pytest

from shinobi_runtime.commands.legacy_scheduler_compat import (
    normalize_legacy_faction_review_record,
)


def _legacy_row(*, identity="faction.akatsuki"):
    slug = identity.replace(".", "-").replace("_", "-")
    host = f"host.faction.{identity}"
    return {
        "event_id": "evt.causal.bootstrap.faction_akatsuki",
        "kind": "faction.periodic_review",
        "due_at": "SE-0061-06-18T07:00:00",
        "priority": 70,
        "source_host": host,
        "target_host": host,
        "payload": {
            "identity": identity,
            "owner_ref": f"state/reg/factions/{slug}.json",
            "recurrence": {
                "accrual_mode": "boundary_only",
                "interval_seconds": 604800,
                "kind": "fixed_interval",
            },
        },
        "dedupe_key": f"faction.periodic_review:{identity}",
        "visibility": "hidden",
        "requires_player": False,
        "causation_id": None,
        "correlation_id": None,
    }


def test_legacy_faction_review_promotes_authenticated_identity_without_mutating_source():
    row = _legacy_row()
    normalized = normalize_legacy_faction_review_record(row)

    assert "faction_id" not in row["payload"]
    assert normalized["payload"]["faction_id"] == "faction.akatsuki"


def test_current_faction_review_passes_through_unchanged():
    row = _legacy_row(identity="faction.konoha_mission_office")
    row["payload"]["faction_id"] = row["payload"]["identity"]

    assert normalize_legacy_faction_review_record(row) is row


@pytest.mark.parametrize("field,value", [
    ("target_host", "host.faction.faction.other"),
    ("source_host", "host.faction.faction.other"),
    ("dedupe_key", "faction.periodic_review:faction.other"),
])
def test_conflicting_legacy_routing_fails_closed(field, value):
    row = _legacy_row()
    row[field] = value

    with pytest.raises(ValueError, match="identity mismatch"):
        normalize_legacy_faction_review_record(row)


def test_conflicting_legacy_owner_fails_closed():
    row = _legacy_row()
    row["payload"]["owner_ref"] = "state/reg/factions/faction-gato-company.json"

    with pytest.raises(ValueError, match="identity mismatch"):
        normalize_legacy_faction_review_record(row)
