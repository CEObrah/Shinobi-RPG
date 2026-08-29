"""Current-revision transition recovery for interrupted player-facing narration.

Transaction receipts are immutable runtime evidence, not campaign authority.  This
projection exposes only the receipt that produced the campaign's *current*
revision, in bounded chronological event pages, so a fresh/re-entered GM can
recover how current state was reached without browsing arbitrary history.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.api.parley_operations import ParleyAwareCampaignOperations
from shinobi_runtime.tx.canonical import thaw_json
from shinobi_runtime.tx.errors import DirtyRepositoryError, LockUnavailableError
from shinobi_runtime.tx.receipts import IdempotencyReceipt


_CURRENT_TRANSITION_RE = re.compile(r"^transition:current(?::(?P<offset>[0-9]+))?$")
_TRANSITION_EVENT_PAGE = 16
_MAX_EVENT_OFFSET = 1_000_000


def current_transition_projection(
    *,
    receipt: IdempotencyReceipt | None,
    campaign_id: str,
    revision: int,
    object_ref: str,
    event_offset: int,
) -> dict[str, Any]:
    """Build one bounded page of the exact current-revision receipt."""

    if receipt is None:
        if event_offset:
            raise OperationError(422, "current_transition_event_cursor_invalid")
        return {
            "object_ref": object_ref,
            "view": "current_committed_transition",
            "object": {
                "available": False,
                "campaign_id": campaign_id,
                "committed_revision": revision,
                "event_count": 0,
                "event_offset": 0,
                "events": [],
                "next_object_ref": None,
                "reason": "no_runtime_receipt_for_current_revision",
            },
        }

    if receipt.campaign_id != campaign_id or receipt.committed_revision != revision:
        raise OperationError(503, "current_transition_receipt_mismatch")

    result = thaw_json(receipt.result)
    if not isinstance(result, dict):
        raise OperationError(503, "current_transition_result_invalid")
    raw_events = result.pop("events", [])
    if raw_events is None:
        raw_events = []
    if not isinstance(raw_events, list):
        raise OperationError(503, "current_transition_events_invalid")
    if event_offset > len(raw_events):
        raise OperationError(422, "current_transition_event_cursor_invalid")

    end = min(len(raw_events), event_offset + _TRANSITION_EVENT_PAGE)
    next_ref = f"transition:current:{end}" if end < len(raw_events) else None
    command_record = thaw_json(receipt.command) if isinstance(receipt.command, Mapping) else None
    payload: dict[str, Any] = {
        "available": True,
        "campaign_id": receipt.campaign_id,
        "committed_revision": receipt.committed_revision,
        "request_id": receipt.request_id,
        "transaction_id": receipt.transaction_id,
        "committed_at": receipt.committed_at,
        "command": command_record,
        "command_recoverable": command_record is not None,
        "result_metadata": result,
        "event_count": len(raw_events),
        "event_offset": event_offset,
        "events": raw_events[event_offset:end],
        "next_object_ref": next_ref,
        "recovery_semantics": (
            "current_revision_transition_only; event pages preserve original order; "
            "receipt evidence does not replace refreshed current state"
        ),
    }
    validate_bounded_json(payload, label="game object projection", allow_float=True)
    return {
        "object_ref": object_ref,
        "view": "current_committed_transition",
        "object": payload,
    }


class TransitionAwareCampaignOperations(ParleyAwareCampaignOperations):
    """Production operations plus bounded current-transition re-entry evidence."""

    def play_context(self) -> Mapping[str, Any]:
        base = dict(super().play_context())
        object_reads = (
            dict(base.get("object_reads", {}))
            if isinstance(base.get("object_reads"), Mapping)
            else {}
        )
        prefixes = [
            str(value)
            for value in object_reads.get("supported_ref_prefixes", [])
            if isinstance(value, str)
        ]
        if "transition:current" not in prefixes:
            prefixes.append("transition:current")
        object_reads["supported_ref_prefixes"] = prefixes
        object_reads["current_transition_ref"] = "transition:current"
        object_reads["current_transition_use"] = (
            "Use only to recover the committed transition that produced the current revision when "
            "conversation/tool interruption removed its receipt from context. Follow next_object_ref "
            "until null when full event chronology is needed. This is not arbitrary history access."
        )
        base["object_reads"] = object_reads
        try:
            validate_bounded_json(base, label="play context", allow_float=True)
        except ValueError as exc:
            raise OperationError(503, "play_context_out_of_bounds") from exc
        return base

    def inspect_game_object(self, object_ref: str) -> Mapping[str, Any]:
        match = _CURRENT_TRANSITION_RE.fullmatch(str(object_ref or ""))
        if match is None:
            return super().inspect_game_object(object_ref)
        event_offset = int(match.group("offset") or 0)
        if event_offset < 0 or event_offset > _MAX_EVENT_OFFSET:
            raise OperationError(422, "current_transition_event_cursor_invalid")

        try:
            with self._locked():
                self.coordinator.git.assert_pristine()
                before = self._read_fingerprint()
                meta = self.repository.read_json("state/meta.json")
                if not isinstance(meta, Mapping) or meta.get("game") != "jianghu":
                    raise OperationError(503, "campaign_state_invalid")
                campaign_id = str(meta.get("campaign_id") or "")
                revision = int(meta.get("revision", -1))
                if not campaign_id or revision < 0:
                    raise OperationError(503, "campaign_state_invalid")
                receipt = self.coordinator.receipts.get_campaign_revision(
                    campaign_id, revision
                )
                scheduler = self.repository.read_json("state/martial-world/scheduler.json")
                freshness = (
                    {"settled_through": scheduler.get("settled_through")}
                    if isinstance(scheduler, Mapping)
                    else None
                )
                self._require_read_only(before, "current_transition_read_mutated_campaign")
        except OperationError:
            raise
        except (LockUnavailableError, DirtyRepositoryError) as exc:
            raise OperationError(503, "campaign_unavailable") from exc
        except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
            raise OperationError(503, "current_transition_receipt_invalid") from exc

        projection = current_transition_projection(
            receipt=receipt,
            campaign_id=campaign_id,
            revision=revision,
            object_ref=object_ref,
            event_offset=event_offset,
        )
        if freshness is not None:
            projection["causal_freshness"] = freshness
        return projection


__all__ = ["TransitionAwareCampaignOperations", "current_transition_projection"]
