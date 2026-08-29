from __future__ import annotations

import copy

from shinobi_runtime.api.transition_operations import (
    _normalize_superseded_activity_handoff,
)


def _context(*, kind: str = "hostile_contact", event_id: str = "contact:test", active_combat_ref: str | None = "combat:contact:test"):
    scene = {
        "active_combat_ref": active_combat_ref,
        "activity_handoff": {
            "event_id": event_id,
            "kind": kind,
            "requires_player_decision": True,
            "interrupts_continuation": True,
        },
    }
    return {"scene": scene, "object_reads": {"supported_ref_prefixes": []}}


def test_matching_hostile_contact_handoff_is_superseded_by_exact_active_combat():
    source = _context()
    before = copy.deepcopy(source)

    result = _normalize_superseded_activity_handoff(source)

    handoff = result["scene"]["activity_handoff"]
    assert handoff["requires_player_decision"] is False
    assert handoff["interrupts_continuation"] is True
    assert handoff["handoff_status"] == "superseded_by_active_combat"
    assert handoff["superseded_by_ref"] == "combat:contact:test"
    assert source == before


def test_nonmatching_active_combat_does_not_retire_hostile_contact_choice():
    source = _context(active_combat_ref="combat:other-contact")

    result = _normalize_superseded_activity_handoff(source)

    handoff = result["scene"]["activity_handoff"]
    assert handoff["requires_player_decision"] is True
    assert "handoff_status" not in handoff
    assert "superseded_by_ref" not in handoff


def test_hostile_contact_without_active_combat_remains_an_unresolved_handoff():
    source = _context(active_combat_ref=None)

    result = _normalize_superseded_activity_handoff(source)

    handoff = result["scene"]["activity_handoff"]
    assert handoff["requires_player_decision"] is True
    assert handoff["interrupts_continuation"] is True
    assert "handoff_status" not in handoff


def test_non_hostile_handoff_is_never_normalized_by_combat_projection():
    source = _context(kind="mission_report")

    result = _normalize_superseded_activity_handoff(source)

    assert result["scene"]["activity_handoff"] == source["scene"]["activity_handoff"]


def test_matching_requires_exact_contact_identity_not_merely_any_active_combat():
    source = _context(event_id="contact:one", active_combat_ref="combat:contact:two")

    result = _normalize_superseded_activity_handoff(source)

    assert result["scene"]["activity_handoff"]["requires_player_decision"] is True
