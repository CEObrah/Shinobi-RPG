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
from shinobi_runtime.deployment_freshness import inspect_deployment_freshness
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

    Mapping keys are sanitized too. Multiple hidden IDs in one mapping receive
    local, non-stable public aliases so pagination can preserve every value
    without exposing identity or collapsing keys.
    """

    if isinstance(value, str):
        return _REDACTED_OPPOSING_REF if value in opposing_refs else value
    if isinstance(value, list):
        return [_sanitize_opposing_refs(item, opposing_refs) for item in value]
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if key in opposing_refs:
                public_key = _REDACTED_OPPOSING_REF
                alias_index = 2
                while public_key in out:
                    public_key = f"{_REDACTED_OPPOSING_REF}_{alias_index}"
                    alias_index += 1
            else:
                public_key = key
                if public_key in out:
                    raise ValueError("mapping keys collide after safe string projection")
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


def _compact_mapping(value: object, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: value.get(key)
        for key in keys
        if value.get(key) not in (None, "", [], {})
    }


def _combat_narrative_beat(event: Mapping[str, Any]) -> dict[str, Any] | None:
    result = str(event.get("result") or "")
    resource = event.get("resource_commit") if isinstance(event.get("resource_commit"), Mapping) else {}
    qi = event.get("qi") if isinstance(event.get("qi"), Mapping) else {}
    damage = event.get("damage") if isinstance(event.get("damage"), Mapping) else {}
    wound = damage.get("wound") if isinstance(damage.get("wound"), Mapping) else {}
    physiology = event.get("physiology") if isinstance(event.get("physiology"), Mapping) else {}
    actual_ref = event.get("actual_ref")
    intended_ref = event.get("intended_ref")
    material = bool(
        result in {
            "contact", "mount_contact", "mount_disabled", "escaped", "dead", "incapacitated",
            "action_interrupted_before_commitment", "action_disrupted_after_commitment_before_release",
            "action_interrupted_by_defense_before_commitment", "action_disrupted_by_defense_after_commitment",
        }
        or wound
        or bool(resource.get("poison_dose_consumed"))
        or (isinstance(actual_ref, str) and isinstance(intended_ref, str) and actual_ref != intended_ref)
        or str(physiology.get("status") or "") in {"dead", "incapacitated", "unconscious"}
    )
    if not material:
        return None
    beat: dict[str, Any] = {}
    for key in (
        "actor_ref", "intended_ref", "actual_ref", "action_kind", "weapon_ref", "poison_ref",
        "hit_zone", "target_structure_ref", "result",
    ):
        value = event.get(key)
        if value not in (None, "", [], {}):
            beat[key] = value
    for time_key in ("contact_at_ms", "release_at_ms", "commit_at_ms", "start_at_ms"):
        value = event.get(time_key)
        if isinstance(value, int) and not isinstance(value, bool):
            beat["at_ms"] = value
            break
    approach = _compact_mapping(event.get("approach"), ("reason", "moved", "distance_mm", "remaining_mm", "required_mm"))
    if approach:
        beat["approach"] = approach
    defense = _compact_mapping(event.get("defense"), ("response", "detected", "reason", "reaction_delay_ms", "recovery_ms"))
    if defense:
        beat["defense"] = defense
    if wound:
        beat["wound"] = _compact_mapping(
            wound,
            (
                "zone", "structure_ref", "side", "severity", "bleeding_ml_per_min", "fracture",
                "tendon_damage", "nerve_damage", "organ_trauma", "function_loss_pct", "pain",
            ),
        )
    contact = _compact_mapping(event.get("contact"), ("channel", "zone", "structure_ref", "contact_kind", "penetration", "impact"))
    if contact:
        beat["contact"] = contact
    if resource:
        compact_resource = _compact_mapping(resource, ("ok", "projectile_ref", "poison_ref", "poison_dose_consumed"))
        if compact_resource:
            beat["resource_commit"] = compact_resource
    qi_spent = qi.get("current_qi_milli_spent")
    if isinstance(qi_spent, int) and not isinstance(qi_spent, bool) and qi_spent > 0:
        beat["qi_milli_spent"] = qi_spent
    fatigue = event.get("fatigue") if isinstance(event.get("fatigue"), Mapping) else {}
    fatigue_added = fatigue.get("added_milli")
    if isinstance(fatigue_added, int) and not isinstance(fatigue_added, bool) and fatigue_added > 0:
        beat["fatigue_milli_added"] = fatigue_added
    poison = _compact_mapping(event.get("poison"), ("poison_ref", "burden_added", "current_burden", "burden_after"))
    if poison:
        beat["poison_effect"] = poison
    return beat


def _combat_narrative_summary(raw_events: list[Any], opposing_refs: frozenset[str]) -> dict[str, Any]:
    material: list[dict[str, Any]] = []
    routine_counts: dict[str, int] = {}
    resource_summary = {
        "projectiles_committed": 0,
        "poison_doses_consumed": 0,
        "qi_milli_spent": 0,
        "fatigue_milli_added": 0,
    }
    for raw in raw_events:
        if not isinstance(raw, Mapping):
            continue
        action_kind = str(raw.get("action_kind") or "unknown")
        result = str(raw.get("result") or "unknown")
        routine_key = f"{action_kind}:{result}"
        routine_counts[routine_key] = routine_counts.get(routine_key, 0) + 1
        resource = raw.get("resource_commit") if isinstance(raw.get("resource_commit"), Mapping) else {}
        if resource.get("ok") is True and isinstance(resource.get("projectile_ref"), str):
            resource_summary["projectiles_committed"] += 1
        if resource.get("poison_dose_consumed") is True:
            resource_summary["poison_doses_consumed"] += 1
        qi = raw.get("qi") if isinstance(raw.get("qi"), Mapping) else {}
        spent = qi.get("current_qi_milli_spent")
        if isinstance(spent, int) and not isinstance(spent, bool) and spent > 0:
            resource_summary["qi_milli_spent"] += spent
        fatigue = raw.get("fatigue") if isinstance(raw.get("fatigue"), Mapping) else {}
        added = fatigue.get("added_milli")
        if isinstance(added, int) and not isinstance(added, bool) and added > 0:
            resource_summary["fatigue_milli_added"] += added
        beat = _combat_narrative_beat(raw)
        if beat is not None:
            material.append(beat)

    material_limit = 96
    safe_beats = _sanitize_opposing_refs(material[:material_limit], opposing_refs)
    if not isinstance(safe_beats, list):
        safe_beats = []
    counts = [
        {"event_kind": key, "count": count}
        for key, count in sorted(routine_counts.items())
    ]
    return {
        "source": "complete_current_transition_receipt",
        "event_count": len(raw_events),
        "material_event_count": len(material),
        "material_beats": safe_beats,
        "material_beats_truncated": len(material) > material_limit,
        "omitted_material_beat_count": max(0, len(material) - material_limit),
        "event_kind_counts": counts,
        "resource_summary": resource_summary,
        "narration_rule": (
            "Use material_beats as the primary chronological scene spine. Routine counts summarize repeated no-change work. "
            "Raw event pages remain exact evidence for audit, negative claims, or detail that the compact spine does not establish."
        ),
    }


def _trim_optional_combat_narrative(narrative: dict[str, Any]) -> bool:
    """Deterministically shrink only the optional duplicate narrative spine."""

    beats = narrative.get("material_beats")
    if not isinstance(beats, list) or not beats:
        return False
    retained = len(beats) // 2
    del beats[retained:]
    material_count = narrative.get("material_event_count")
    if isinstance(material_count, int) and not isinstance(material_count, bool):
        narrative["material_beats_truncated"] = material_count > len(beats)
        narrative["omitted_material_beat_count"] = max(0, material_count - len(beats))
    else:
        narrative["material_beats_truncated"] = True
    return True


def _event_page_sizes(remaining: int) -> list[int]:
    """Return deterministic descending candidate sizes for an adaptive exact page."""

    if remaining <= 0:
        return [0]
    size = min(_TRANSITION_EVENT_PAGE, remaining)
    sizes: list[int] = []
    while True:
        sizes.append(size)
        if size == 1:
            break
        size = max(1, size // 2)
    return sizes


def current_transition_projection(
    *,
    receipt: IdempotencyReceipt | None,
    campaign_id: str,
    revision: int,
    object_ref: str,
    event_offset: int,
    combat_opposing_person_refs: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Build one bounded, player-safe page of the exact current-revision receipt.

    Event count is not a response-size invariant: exact combat events vary greatly
    in structural richness. Pagination therefore treats 16 as a maximum page
    width and deterministically reduces the exact chronological slice until the
    public JSON envelope accepts it. The cursor always advances by the number of
    exact events actually returned, so no event is skipped or duplicated.
    """

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
        try:
            validate_bounded_json(payload, label="game object projection", allow_float=True)
        except ValueError as exc:
            raise OperationError(503, "current_transition_projection_out_of_bounds") from exc
        return {
            "object_ref": object_ref,
            "view": "current_committed_transition",
            "object": payload,
        }

    opposing_refs = combat_opposing_person_refs or frozenset()
    original_command = thaw_json(receipt.command) if isinstance(receipt.command, Mapping) else None

    try:
        command_record = _sanitize_opposing_refs(original_command, opposing_refs) if original_command is not None else None
        result_metadata = _sanitize_opposing_refs(result, opposing_refs)
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
            try:
                validate_bounded_json(payload, label="game object projection", allow_float=True)
            except ValueError as bounds_exc:
                raise OperationError(503, "current_transition_projection_out_of_bounds") from bounds_exc
            return {
                "object_ref": object_ref,
                "view": "current_committed_transition",
                "object": payload,
            }
        raise OperationError(503, "current_transition_redaction_invalid") from exc

    if (
        _contains_exact_ref(command_record, opposing_refs)
        or _contains_exact_ref(result_metadata, opposing_refs)
    ):
        raise OperationError(503, "current_transition_redaction_invalid")

    command_redacted = original_command is not None and command_record != original_command
    last_bounds_error: ValueError | None = None
    remaining = len(raw_events) - event_offset

    for page_size in _event_page_sizes(remaining):
        end = event_offset + page_size
        try:
            events = _sanitize_opposing_refs(raw_events[event_offset:end], opposing_refs)
        except ValueError as exc:
            if is_combat:
                if event_offset:
                    raise OperationError(422, "current_transition_event_cursor_invalid") from exc
                raise OperationError(503, "current_transition_redaction_invalid") from exc
            raise OperationError(503, "current_transition_redaction_invalid") from exc

        if _contains_exact_ref(events, opposing_refs):
            raise OperationError(503, "current_transition_redaction_invalid")

        combat_narrative = (
            _combat_narrative_summary(raw_events, opposing_refs)
            if is_combat and event_offset == 0
            else None
        )
        next_ref = f"transition:current:{end}" if end < len(raw_events) else None
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
            "combat_narrative": combat_narrative,
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

        while True:
            try:
                validate_bounded_json(payload, label="game object projection", allow_float=True)
                return {
                    "object_ref": object_ref,
                    "view": "current_committed_transition",
                    "object": payload,
                }
            except ValueError as exc:
                last_bounds_error = exc
                if isinstance(combat_narrative, dict) and _trim_optional_combat_narrative(combat_narrative):
                    continue
                break

    raise OperationError(503, "current_transition_projection_out_of_bounds") from last_bounds_error


class TransitionAwareCampaignOperations(ParleyAwareCampaignOperations):
    """Production operations plus bounded current-transition re-entry evidence."""

    def _require_fresh_deployment_for_write(self) -> None:
        freshness = inspect_deployment_freshness(self.repository.root)
        if not freshness.healthy:
            raise OperationError(503, "deployment_source_stale")

    def preview_command(self, command):
        self._require_fresh_deployment_for_write()
        return super().preview_command(command)

    def execute_command(self, command):
        self._require_fresh_deployment_for_write()
        return super().execute_command(command)

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
