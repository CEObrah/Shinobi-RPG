"""Current-revision transition recovery for interrupted player-facing narration.

Transaction receipts are immutable runtime evidence, not campaign authority. This
projection exposes only the receipt that produced the campaign's *current*
revision, in bounded chronological event pages, so a fresh/re-entered GM can
recover how current state was reached without browsing arbitrary history.

Combat receipt recovery is also a knowledge boundary. Exact combat receipts may
contain hidden opposing person IDs used by deterministic mechanics. Those IDs
must never become player-visible merely because narration is being reconstructed.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.api.parley_operations import ParleyAwareCampaignOperations
from shinobi_runtime.tx.canonical import thaw_json
from shinobi_runtime.tx.errors import DirtyRepositoryError, LockUnavailableError
from shinobi_runtime.tx.receipts import IdempotencyReceipt


_CURRENT_TRANSITION_RE = re.compile(r"^transition:current(?::(?P<offset>[0-9]+))?$")
_TRANSITION_EVENT_PAGE = 16
_MAX_EVENT_OFFSET = 1_000_000
_REDACTED_OPPOSING_REF = "opposing_combatant"
_COMBAT_SAFE_METADATA_KEYS = (
    "command_type",
    "combat_ref",
    "combat_status",
    "exchanges_resolved",
    "scope_stop_reason",
    "continuation_required",
)


def _normalize_superseded_activity_handoff(context: Mapping[str, Any]) -> dict[str, Any]:
    """Retire only the initial hostile-contact choice once its combat is active.

    ``state/scene.json`` is presentation continuity and may retain the route
    frontier that originally woke the player. Once exact combat owns that same
    contact, the old question ("what do you do about this contact?") is no
    longer the current decision. Keep the handoff for provenance and keep its
    interruption flag, but stop advertising the superseded choice as unresolved.
    """

    out = dict(context)
    scene = out.get("scene")
    if not isinstance(scene, Mapping):
        return out
    handoff = scene.get("activity_handoff")
    if not isinstance(handoff, Mapping):
        return out
    if str(handoff.get("kind") or "") != "hostile_contact":
        return out

    event_id = str(handoff.get("event_id") or "")
    active_combat_ref = str(scene.get("active_combat_ref") or "")
    if not event_id or not active_combat_ref:
        return out
    if active_combat_ref != f"combat:{event_id}":
        return out

    updated_handoff = dict(handoff)
    updated_handoff["requires_player_decision"] = False
    updated_handoff["handoff_status"] = "superseded_by_active_combat"
    updated_handoff["superseded_by_ref"] = active_combat_ref
    updated_scene = dict(scene)
    updated_scene["activity_handoff"] = updated_handoff
    out["scene"] = updated_scene
    return out


def _combat_opposing_person_refs(
    *,
    read_json: Callable[[str], Any],
    receipt: IdempotencyReceipt | None,
    player_id: str,
) -> frozenset[str] | None:
    """Return exact opposing refs only for output redaction, never for exposure.

    ``frozenset()`` means the current transition is not combat (or has no
    opposing side). ``None`` means a combat transition was detected but the
    player side could not be resolved safely, so combat-detail recovery must
    fail closed instead of returning raw receipt identities.
    """

    if receipt is None:
        return frozenset()
    result = thaw_json(receipt.result)
    if not isinstance(result, Mapping):
        return None
    if str(result.get("command_type") or "") != "jianghu_combat_resolution":
        return frozenset()
    combat_ref = str(result.get("combat_ref") or "")
    if not combat_ref or not player_id:
        return None
    try:
        state = read_json("state/martial-world/combats.json")
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        return None
    combats = state.get("combats", {}) if isinstance(state, Mapping) else {}
    combat = combats.get(combat_ref) if isinstance(combats, Mapping) else None
    sides = combat.get("sides", {}) if isinstance(combat, Mapping) else {}
    if not isinstance(sides, Mapping):
        return None

    player_side: str | None = None
    for side_ref, raw_members in sides.items():
        members = raw_members if isinstance(raw_members, list) else []
        if player_id in members:
            player_side = str(side_ref)
            break
    if player_side is None:
        return None

    opposing: set[str] = set()
    for side_ref, raw_members in sides.items():
        if str(side_ref) == player_side or not isinstance(raw_members, list):
            continue
        opposing.update(
            str(ref) for ref in raw_members if isinstance(ref, str) and ref
        )
    return frozenset(opposing)


def _sanitize_opposing_refs(value: Any, opposing_refs: frozenset[str]) -> Any:
    """Recursively replace exact opposing person refs in player-facing evidence.

    Mapping keys are sanitized too. If two hidden exact IDs would collapse onto
    the same public key, fail closed rather than silently discard one value.
    """

    if isinstance(value, str):
        return _REDACTED_OPPOSING_REF if value in opposing_refs else value
    if isinstance(value, list):
        return [_sanitize_opposing_refs(item, opposing_refs) for item in value]
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            public_key = _REDACTED_OPPOSING_REF if key in opposing_refs else key
            if public_key in out:
                raise ValueError("opposing identity redaction would collapse mapping keys")
            out[public_key] = _sanitize_opposing_refs(item, opposing_refs)
        return out
    return value


def _contains_exact_ref(value: Any, refs: frozenset[str]) -> bool:
    if not refs:
        return False
    if isinstance(value, str):
        return value in refs
    if isinstance(value, list):
        return any(_contains_exact_ref(item, refs) for item in value)
    if isinstance(value, Mapping):
        return any(
            key in refs or _contains_exact_ref(item, refs)
            for key, item in value.items()
            if isinstance(key, str)
        )
    return False


def _safe_combat_metadata(result: Mapping[str, Any]) -> dict[str, Any]:
    """Minimal metadata safe even when opposing identity redaction is unavailable."""

    out: dict[str, Any] = {}
    for key in _COMBAT_SAFE_METADATA_KEYS:
        value = result.get(key)
        if value is None or isinstance(value, (str, int, bool)):
            if value not in (None, ""):
                out[key] = value
    return out


def current_transition_projection(
    *,
    receipt: IdempotencyReceipt | None,
    campaign_id: str,
    revision: int,
    object_ref: str,
    event_offset: int,
    combat_opposing_person_refs: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Build one bounded, player-safe page of the exact current-revision receipt."""

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

    is_combat = str(result.get("command_type") or "") == "jianghu_combat_resolution"
    if is_combat and combat_opposing_person_refs is None:
        if event_offset:
            raise OperationError(422, "current_transition_event_cursor_invalid")
        payload: dict[str, Any] = {
            "available": True,
            "campaign_id": receipt.campaign_id,
            "committed_revision": receipt.committed_revision,
            "request_id": receipt.request_id,
            "transaction_id": receipt.transaction_id,
            "committed_at": receipt.committed_at,
            "command": None,
            "command_recoverable": False,
            "command_withheld": isinstance(receipt.command, Mapping),
            "result_metadata": _safe_combat_metadata(result),
            "event_count": len(raw_events),
            "event_offset": 0,
            "events": [],
            "events_withheld": bool(raw_events),
            "next_object_ref": None,
            "event_identity_semantics": "combat_identity_redaction_unavailable",
            "recovery_semantics": (
                "current_revision_transition_only; combat detail withheld because hidden opposing "
                "identity redaction could not be established safely; refreshed current state remains authoritative"
            ),
        }
        validate_bounded_json(payload, label="game object projection", allow_float=True)
        return {
            "object_ref": object_ref,
            "view": "current_committed_transition",
            "object": payload,
        }

    opposing_refs = combat_opposing_person_refs or frozenset()
    end = min(len(raw_events), event_offset + _TRANSITION_EVENT_PAGE)
    next_ref = f"transition:current:{end}" if end < len(raw_events) else None
    original_command = thaw_json(receipt.command) if isinstance(receipt.command, Mapping) else None

    try:
        command_record = _sanitize_opposing_refs(original_command, opposing_refs) if original_command is not None else None
        result_metadata = _sanitize_opposing_refs(result, opposing_refs)
        events = _sanitize_opposing_refs(raw_events[event_offset:end], opposing_refs)
    except ValueError as exc:
        if is_combat:
            if event_offset:
                raise OperationError(422, "current_transition_event_cursor_invalid") from exc
            payload = {
                "available": True,
                "campaign_id": receipt.campaign_id,
                "committed_revision": receipt.committed_revision,
                "request_id": receipt.request_id,
                "transaction_id": receipt.transaction_id,
                "committed_at": receipt.committed_at,
                "command": None,
                "command_recoverable": False,
                "command_withheld": isinstance(receipt.command, Mapping),
                "result_metadata": _safe_combat_metadata(result),
                "event_count": len(raw_events),
                "event_offset": 0,
                "events": [],
                "events_withheld": bool(raw_events),
                "next_object_ref": None,
                "event_identity_semantics": "combat_identity_redaction_failed_closed",
                "recovery_semantics": (
                    "current_revision_transition_only; combat detail withheld because safe opposing "
                    "identity redaction failed; refreshed current state remains authoritative"
                ),
            }
            validate_bounded_json(payload, label="game object projection", allow_float=True)
            return {
                "object_ref": object_ref,
                "view": "current_committed_transition",
                "object": payload,
            }
        raise OperationError(503, "current_transition_redaction_invalid") from exc

    if (
        _contains_exact_ref(command_record, opposing_refs)
        or _contains_exact_ref(result_metadata, opposing_refs)
        or _contains_exact_ref(events, opposing_refs)
    ):
        raise OperationError(503, "current_transition_redaction_invalid")

    command_redacted = original_command is not None and command_record != original_command
    payload = {
        "available": True,
        "campaign_id": receipt.campaign_id,
        "committed_revision": receipt.committed_revision,
        "request_id": receipt.request_id,
        "transaction_id": receipt.transaction_id,
        "committed_at": receipt.committed_at,
        "command": command_record,
        "command_recoverable": command_record is not None and not command_redacted,
        "command_redacted": command_redacted,
        "result_metadata": result_metadata,
        "event_count": len(raw_events),
        "event_offset": event_offset,
        "events": events,
        "events_withheld": False,
        "next_object_ref": next_ref,
        "event_identity_semantics": (
            "opposing_exact_person_refs_redacted"
            if is_combat
            else "not_applicable"
        ),
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
        base = _normalize_superseded_activity_handoff(super().play_context())
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
            "until null when full player-safe event chronology is needed. This is not arbitrary history access, "
            "and hidden opposing combat identities remain redacted."
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
                player_id = str(meta.get("player_id") or "")
                revision = int(meta.get("revision", -1))
                if not campaign_id or not player_id or revision < 0:
                    raise OperationError(503, "campaign_state_invalid")
                receipt = self.coordinator.receipts.get_campaign_revision(
                    campaign_id, revision
                )
                combat_opposing_person_refs = _combat_opposing_person_refs(
                    read_json=self.repository.read_json,
                    receipt=receipt,
                    player_id=player_id,
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
            combat_opposing_person_refs=combat_opposing_person_refs,
        )
        if freshness is not None:
            projection["causal_freshness"] = freshness
        return projection


__all__ = [
    "TransitionAwareCampaignOperations",
    "_combat_opposing_person_refs",
    "_normalize_superseded_activity_handoff",
    "current_transition_projection",
]
