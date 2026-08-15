"""Player-safe world-front report reads and meaningful report handoffs.

World-front reports are player knowledge only after a persisted delivery. New
reports receive a front-specific claim instead of granting an unrelated source
claim, while the read layer can recover the context of legacy deliveries from
their player-visible semantic delivery event.
"""

from __future__ import annotations

import copy
import hashlib
from functools import wraps
from typing import Any, Dict, Mapping, Optional

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.operations import CampaignOperations, OperationError
from shinobi_runtime.commands.core import _OwnerResolutionCache
from shinobi_runtime.commands.world_front_rules import front_phase, policy, pressure_registry
from shinobi_runtime.information import InformationStore
from shinobi_runtime.reducers import deliver_claim


_GENERIC_REPORT = "An authorized operational report about a developing world concern has reached you."
_MAX_RECENT_REPORT_REFS = 6
_MAX_ARCHIVE_SEGMENTS_FOR_LEGACY_CONTEXT = 4
_INSTALLED = False


def _briefing_for_front(repository: Any, front_ref: str) -> Optional[Mapping[str, Any]]:
    rules = policy(repository)
    fronts = rules.get("fronts")
    config = fronts.get(front_ref) if isinstance(fronts, Mapping) else None
    registry = pressure_registry(repository)
    pressures = registry.get("pressures")
    pressure = pressures.get(front_ref) if isinstance(pressures, Mapping) else None
    if not isinstance(config, Mapping) or not isinstance(pressure, Mapping):
        return None
    title = pressure.get("title")
    if not isinstance(title, str) or not title:
        return None
    phase = front_phase(pressure, rules)
    phase_text = {
        "latent": "being watched but not yet assessed as materially developing",
        "developing": "developing",
        "operational": "producing sustained operational consequences",
        "crisis": "at crisis-level operational pressure",
        "resolved": "resolved",
    }.get(phase, phase)
    concerns = pressure.get("stakes")
    visible_concerns = [value for value in concerns or [] if isinstance(value, str) and value][:3]
    return {
        "front_ref": front_ref,
        "title": title,
        "phase": phase,
        "summary": f"{title} is assessed as {phase_text}.",
        "operational_concerns": visible_concerns,
    }


def _event_world_front_for_delivery(repository: Any, delivery_ref: str, player_id: str) -> Optional[str]:
    try:
        registry = repository.read_json("state/reg/world-events.json")
    except (FileNotFoundError, ValueError):
        return None
    if not isinstance(registry, Mapping):
        return None
    sources: list[Mapping[str, Any]] = [registry]
    archive_refs = registry.get("archive_refs")
    if isinstance(archive_refs, list):
        paths = [value for value in archive_refs if isinstance(value, str)]
        for path in reversed(paths[-_MAX_ARCHIVE_SEGMENTS_FOR_LEGACY_CONTEXT:]):
            try:
                archive = repository.read_json(path)
            except (FileNotFoundError, ValueError):
                continue
            if isinstance(archive, Mapping):
                sources.append(archive)
    for source in sources:
        events = source.get("events")
        if not isinstance(events, list):
            continue
        for event in reversed(events):
            if not isinstance(event, Mapping) or event.get("kind") != "world_front_information_delivered":
                continue
            material = event.get("material_consequence_refs")
            if not isinstance(material, list) or delivery_ref not in material:
                continue
            visibility = event.get("visibility")
            audiences = visibility.get("audience_refs") if isinstance(visibility, Mapping) else None
            witnesses = visibility.get("witness_refs") if isinstance(visibility, Mapping) else None
            if player_id not in (audiences or []) and player_id not in (witnesses or []):
                continue
            host_refs = event.get("host_refs")
            if not isinstance(host_refs, list):
                continue
            return next((ref for ref in host_refs if isinstance(ref, str) and ref.startswith("pressure_")), None)
    return None


def _recent_player_report_refs(repository: Any, player_id: str) -> list[str]:
    try:
        routing = repository.read_json("state/reg/information-deliveries.json")
    except (FileNotFoundError, ValueError):
        return []
    recent = routing.get("recent_delivery_refs") if isinstance(routing, Mapping) else None
    if not isinstance(recent, list):
        return []
    information = InformationStore(repository)
    result: list[str] = []
    for delivery_ref in reversed(recent):
        if not isinstance(delivery_ref, str) or not delivery_ref.startswith("delivery.world_front."):
            continue
        try:
            delivery = information.delivery(delivery_ref)
        except ValueError:
            continue
        if not isinstance(delivery, Mapping) or delivery.get("recipient_ref") != player_id:
            continue
        result.append(delivery_ref)
        if len(result) >= _MAX_RECENT_REPORT_REFS:
            break
    result.reverse()
    return result


def _project_delivery(repository: Any, delivery_ref: str, player_id: str) -> Mapping[str, Any]:
    information = InformationStore(repository)
    try:
        delivery = information.delivery(delivery_ref)
    except ValueError as exc:
        raise OperationError(503, "information_registry_invalid") from exc
    if not isinstance(delivery, Mapping) or player_id not in {delivery.get("sender_ref"), delivery.get("recipient_ref")}:
        raise OperationError(404, "object_not_player_visible")
    claim_id = delivery.get("claim_id")
    if not isinstance(claim_id, str):
        raise OperationError(503, "information_delivery_invalid")
    try:
        claim = information.claim(claim_id)
    except ValueError as exc:
        raise OperationError(503, "information_registry_invalid") from exc
    if not isinstance(claim, Mapping):
        raise OperationError(503, "information_delivery_invalid")
    subject = claim.get("subject_ref")
    front_ref = subject if isinstance(subject, str) and subject.startswith("pressure_") else None
    if front_ref is None:
        front_ref = _event_world_front_for_delivery(repository, delivery_ref, player_id)
    briefing = _briefing_for_front(repository, front_ref) if isinstance(front_ref, str) else None
    result: Dict[str, Any] = {
        "delivery": {
            key: delivery.get(key)
            for key in (
                "delivery_id", "claim_id", "sender_ref", "recipient_ref", "channel",
                "delivered_at", "resulting_epistemic_kind", "resulting_confidence_milli",
                "evidence_refs",
            )
            if key in delivery
        },
        "claim": {
            key: claim.get(key)
            for key in (
                "claim_id", "subject_ref", "source_ref", "collected_at", "epistemic_kind",
                "confidence_milli", "evidence_refs",
            )
            if key in claim
        },
    }
    if isinstance(briefing, Mapping):
        result["briefing"] = dict(briefing)
    return result


def _install_front_specific_delivery_claim() -> None:
    from shinobi_runtime.commands import world_front_progression as module

    original = module._deliver_player_front_report
    if getattr(original, "_front_specific_player_claim", False):
        return

    @wraps(original)
    def wrapped(owner: Any, *, result: Mapping[str, Any], decision: Any, front_ref: str, assignment: Mapping[str, Any], at: Any, command: Any, world_events: Dict[str, Any], record_writes: Dict[str, Dict[str, Any]]) -> list[Mapping[str, Any]]:
        if command.mode != "gameplay" or result.get("kind") != "information_report" or result.get("skipped") is not None:
            return []
        config = module._front_report_config(assignment, front_ref)
        if config is None:
            return []
        source_claim_id = result.get("claim_id")
        if not isinstance(source_claim_id, str):
            return []
        information = InformationStore(owner.repository, record_writes)
        try:
            source_record = information.claim(source_claim_id)
        except ValueError as exc:
            raise module.CommandRejectedError("information_registry_invalid") from exc
        if not isinstance(source_record, Mapping):
            raise module.CommandRejectedError("world_front_player_report_claim_missing")
        sender_ref = decision.actor_ref
        try:
            sender_known = information.holder_knows(sender_ref, source_claim_id)
        except ValueError as exc:
            raise module.CommandRejectedError("information_registry_invalid") from exc
        if not sender_known:
            raise module.CommandRejectedError("world_front_player_report_sender_unknown")
        recipient_ref = command.actor_id
        try:
            owner._resolve_covered_owner(recipient_ref, cache=_OwnerResolutionCache())
        except module.CommandRejectedError as exc:
            raise module.CommandRejectedError("world_front_player_report_recipient_invalid") from exc
        source_claim = module._claim_from_record(source_record)
        claim_digest = hashlib.sha256(
            f"{source_claim_id}\x00{sender_ref}\x00{recipient_ref}\x00{front_ref}\x00front-briefing".encode("utf-8")
        ).hexdigest()[:24]
        report_claim_id = f"claim.world_front.{claim_digest}"
        report_record = {
            "claim_id": report_claim_id,
            "subject_ref": front_ref,
            "source_ref": sender_ref,
            "collected_at": str(at),
            "epistemic_kind": "report",
            "confidence_milli": source_claim.confidence_milli,
            "evidence_refs": list(dict.fromkeys([source_claim_id, *source_claim.evidence_refs])),
        }
        try:
            existing_claim = information.claim(report_claim_id)
            if existing_claim is None:
                information.add_claim(report_record)
            else:
                report_record = dict(existing_claim)
            information.grant(sender_ref, report_claim_id)
            report_claim = module._claim_from_record(report_record)
            delivery_digest = hashlib.sha256(
                f"{report_claim_id}\x00{sender_ref}\x00{recipient_ref}\x00{front_ref}".encode("utf-8")
            ).hexdigest()[:24]
            delivery_id = f"delivery.world_front.{delivery_digest}"
            existing_delivery = information.delivery(delivery_id)
            if existing_delivery is None:
                delivery = deliver_claim(
                    report_claim,
                    delivery_id=delivery_id,
                    sender_ref=sender_ref,
                    recipient_ref=recipient_ref,
                    channel=str(config["channel"]),
                    delivered_at=at,
                    channel_confidence_milli=int(config["channel_confidence_milli"]),
                )
                delivery_record = dict(delivery.to_record())
                information.add_delivery(delivery_record)
            else:
                delivery_record = dict(existing_delivery)
            information.grant(recipient_ref, report_claim_id)
        except (TypeError, ValueError) as exc:
            raise module.CommandRejectedError("world_front_player_report_delivery_invalid") from exc
        source_event = result.get("event_id")
        causal_refs = [source_claim_id, report_claim_id]
        if isinstance(source_event, str):
            causal_refs.append(source_event)
        event_id = owner._append_internal_event(
            world_events,
            command=command,
            identity=delivery_id,
            kind="world_front_information_delivered",
            at=at,
            host_refs=(front_ref, str(decision.payload.get("faction_id"))),
            actor_refs=(sender_ref, recipient_ref),
            causal_refs=tuple(causal_refs),
            affected_owner_refs=information.affected_paths,
            material_consequence_refs=(delivery_id,),
            classification=str(config.get("classification", "restricted")),
            audience_refs=(recipient_ref,),
            knowledge_refs=(report_claim_id,),
            source_refs=(sender_ref, source_claim_id),
            reducer_ref="shinobi_runtime.reducers.information.deliver_claim",
        )
        briefing = _briefing_for_front(owner.repository, front_ref)
        return [{
            **delivery_record,
            "world_front_ref": front_ref,
            "source_claim_id": source_claim_id,
            "semantic_event_id": event_id,
            "briefing": dict(briefing) if isinstance(briefing, Mapping) else None,
        }]

    wrapped._front_specific_player_claim = True
    module._deliver_player_front_report = wrapped


def _install_time_handoff_projection() -> None:
    from shinobi_runtime.commands import campaign_runtime_planner as module

    original = module._fresh_player_facing_time_handoff
    if getattr(original, "_detailed_player_report_projection", False):
        return

    @wraps(original)
    def wrapped(result: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        pressures, reports, approaching = original(result)
        reports = [message for message in reports if message != _GENERIC_REPORT]
        actions = result.get("autonomous_actions")
        if isinstance(actions, list):
            for action in actions:
                if not isinstance(action, Mapping):
                    continue
                deliveries = action.get("player_report_deliveries")
                if not isinstance(deliveries, list):
                    continue
                for delivery in deliveries:
                    briefing = delivery.get("briefing") if isinstance(delivery, Mapping) else None
                    if not isinstance(briefing, Mapping):
                        continue
                    summary = briefing.get("summary")
                    concerns = briefing.get("operational_concerns")
                    if not isinstance(summary, str):
                        continue
                    message = f"Mission Office operational briefing: {summary}"
                    if isinstance(concerns, list) and concerns:
                        message += " Operational concerns: " + ", ".join(str(value) for value in concerns) + "."
                    if message not in reports:
                        reports.append(message)
        return pressures[:12], reports[:6], approaching[:8]

    wrapped._detailed_player_report_projection = True
    module._fresh_player_facing_time_handoff = wrapped


def _install_api_report_reads() -> None:
    original_play_context = CampaignOperations.play_context
    if not getattr(original_play_context, "_player_report_reads", False):
        @wraps(original_play_context)
        def play_context(self: CampaignOperations) -> Mapping[str, Any]:
            response = copy.deepcopy(original_play_context(self))
            campaign = response.get("campaign") if isinstance(response, Mapping) else None
            player_id = campaign.get("player_id") if isinstance(campaign, Mapping) else None
            if not isinstance(player_id, str):
                return response
            report_refs = _recent_player_report_refs(self.repository, player_id)
            briefings = []
            for ref in report_refs:
                try:
                    projected = _project_delivery(self.repository, ref, player_id)
                except OperationError:
                    continue
                briefing = projected.get("briefing") if isinstance(projected, Mapping) else None
                delivery = projected.get("delivery") if isinstance(projected, Mapping) else None
                if isinstance(briefing, Mapping) and isinstance(delivery, Mapping):
                    briefings.append((ref, dict(briefing), dict(delivery)))
            scene = response.get("scene") if isinstance(response, dict) else None
            narrative = scene.get("narrative") if isinstance(scene, dict) else None
            if isinstance(narrative, dict) and briefings:
                existing = narrative.get("available_reports")
                messages = [value for value in existing or [] if isinstance(value, str) and value != _GENERIC_REPORT]
                for _ref, briefing, _delivery in briefings[-_MAX_RECENT_REPORT_REFS:]:
                    summary = briefing.get("summary")
                    concerns = briefing.get("operational_concerns")
                    if isinstance(summary, str):
                        message = f"Mission Office operational briefing: {summary}"
                        if isinstance(concerns, list) and concerns:
                            message += " Operational concerns: " + ", ".join(str(value) for value in concerns) + "."
                        if message not in messages:
                            messages.append(message)
                narrative["available_reports"] = messages[-6:]
                scene["player_handoff"] = {
                    "kind": "delivered_report",
                    "report_refs": [ref for ref, _briefing, _delivery in briefings[-_MAX_RECENT_REPORT_REFS:]],
                    "presentation_rule": "Present the report before offering grounded response options; a null decision_required does not erase this meaningful event handoff.",
                }
            object_reads = response.get("object_reads") if isinstance(response, dict) else None
            if isinstance(object_reads, dict):
                prefixes = object_reads.get("supported_ref_prefixes")
                if isinstance(prefixes, list) and "delivery." not in prefixes:
                    prefixes.append("delivery.")
                object_reads["suggested_report_refs"] = report_refs
                object_reads["report_ref_count"] = len(report_refs)
                object_reads["use"] = str(object_reads.get("use") or "") + "; inspect delivery.<id> for a player-addressed information delivery or operational briefing"
            validate_bounded_json(response, label="play context", allow_float=True)
            return response

        play_context._player_report_reads = True
        CampaignOperations.play_context = play_context

    original_inspect = CampaignOperations.inspect_game_object
    if not getattr(original_inspect, "_player_report_reads", False):
        @wraps(original_inspect)
        def inspect_game_object(self: CampaignOperations, object_ref: str) -> Mapping[str, Any]:
            if not object_ref.startswith("delivery."):
                return original_inspect(self, object_ref)
            try:
                with self._locked():
                    self.coordinator.git.assert_pristine()
                    before = self._read_fingerprint()
                    meta = self.repository.read_json(self.coordinator.meta_path)
                    player_id = meta.get("player_id") if isinstance(meta, Mapping) else None
                    if not isinstance(player_id, str):
                        raise OperationError(503, "object_access_policy_invalid")
                    result = _project_delivery(self.repository, object_ref, player_id)
                    self._require_read_only(before, "object_inspection_mutated_campaign")
            except OperationError:
                raise
            except Exception as exc:
                raise OperationError(503, "object_inspection_invalid") from exc
            response = {"object_ref": object_ref, "view": "information_delivery", "object": result}
            try:
                validate_bounded_json(response, label="game object projection", allow_float=True)
            except ValueError as exc:
                raise OperationError(503, "object_projection_out_of_bounds") from exc
            return response

        inspect_game_object._player_report_reads = True
        CampaignOperations.inspect_game_object = inspect_game_object


def install_player_report_projection() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_front_specific_delivery_claim()
    _install_time_handoff_projection()
    _install_api_report_reads()
    _INSTALLED = True


__all__ = [
    "install_player_report_projection",
    "_briefing_for_front",
    "_event_world_front_for_delivery",
    "_project_delivery",
    "_recent_player_report_refs",
]
