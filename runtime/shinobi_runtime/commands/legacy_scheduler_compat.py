"""Strict compatibility normalization for legacy persisted scheduler events.

This module repairs old faction periodic-review records that predate the
``faction_id`` payload field.  It does not create new scheduler authority or
weaken current event validation: the legacy identity must agree with the event
host, dedupe key, and canonical faction-owner path before it is promoted into
the current payload shape.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

_INSTALLED = False


def normalize_legacy_faction_review_record(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a current-shape record for one authenticated legacy faction review.

    Records that are unrelated, or already contain ``faction_id``, pass through
    unchanged.  A legacy faction review with conflicting routing metadata fails
    closed rather than inferring an identity from an untrusted single field.
    """

    if not isinstance(record, Mapping) or record.get("kind") != "faction.periodic_review":
        return record
    payload = record.get("payload")
    if not isinstance(payload, Mapping):
        return record
    if isinstance(payload.get("faction_id"), str):
        return record

    identity = payload.get("identity")
    owner_ref = payload.get("owner_ref")
    source_host = record.get("source_host")
    target_host = record.get("target_host")
    dedupe_key = record.get("dedupe_key")
    if not isinstance(identity, str) or not identity.startswith("faction."):
        return record

    expected_host = f"host.faction.{identity}"
    expected_dedupe = f"faction.periodic_review:{identity}"
    expected_owner = f"state/reg/factions/{identity.replace('.', '-').replace('_', '-')}.json"
    if (
        source_host != expected_host
        or target_host != expected_host
        or dedupe_key != expected_dedupe
        or owner_ref != expected_owner
    ):
        raise ValueError("legacy faction scheduler identity mismatch")

    normalized = copy.deepcopy(dict(record))
    normalized_payload = dict(normalized["payload"])
    normalized_payload["faction_id"] = identity
    normalized["payload"] = normalized_payload
    return normalized


def install_legacy_scheduler_compat() -> None:
    """Install campaign-only compatibility before scheduler state is loaded."""

    global _INSTALLED
    if _INSTALLED:
        return

    from shinobi_runtime.sim.events import ScheduledEvent

    original = ScheduledEvent.from_record.__func__

    def from_record(cls, record: Mapping[str, Any]):
        return original(cls, normalize_legacy_faction_review_record(record))

    ScheduledEvent.from_record = classmethod(from_record)
    _INSTALLED = True


__all__ = ["install_legacy_scheduler_compat", "normalize_legacy_faction_review_record"]
