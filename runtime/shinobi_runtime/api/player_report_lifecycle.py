"""Lifecycle hardening for delivered player reports.

The base report projection establishes player-safe readable deliveries. This
layer makes that projection durable across long campaigns: handled deliveries
stop reappearing as fresh handoffs, legacy delivery provenance is searched
across the complete event archive, and briefing phase is reconstructed as of the
delivery time instead of drifting with later world-front progress.
"""
from __future__ import annotations

import copy
from functools import wraps
from typing import Any, Mapping, Optional

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.operations import CampaignOperations, OperationError
from shinobi_runtime.commands.world_front_rules import policy, pressure_registry

_INSTALLED = False
_TERMINAL = frozenset(("completed", "resolved", "failed", "cancelled", "abandoned", "superseded"))


def _handled_report_refs(repository: Any) -> set[str]:
    try:
        scene = repository.read_json("state/scene.json")
    except (FileNotFoundError, ValueError):
        return set()
    narrative = scene.get("narrative") if isinstance(scene, Mapping) else None
    raw = narrative.get("handled_report_refs") if isinstance(narrative, Mapping) else None
    if not isinstance(raw, list):
        return set()
    return {value for value in raw if isinstance(value, str) and value.startswith("delivery.")}


def _full_event_world_front_for_delivery(repository: Any, delivery_ref: str, player_id: str) -> Optional[str]:
    try:
        registry = repository.read_json("state/reg/world-events.json")
    except (FileNotFoundError, ValueError):
        return None
    if not isinstance(registry, Mapping):
        return None

    sources: list[Mapping[str, Any]] = [registry]
    archive_refs = registry.get("archive_refs")
    if isinstance(archive_refs, list):
        for path in reversed([value for value in archive_refs if isinstance(value, str)]):
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


def _phase_from_evidence_count(count: int, rules: Mapping[str, Any]) -> str:
    thresholds = rules.get("phase_thresholds")
    if not isinstance(thresholds, Mapping):
        return "latent"
    developing = int(thresholds.get("developing_evidence", 1))
    operational = int(thresholds.get("operational_evidence", 3))
    crisis = int(thresholds.get("crisis_evidence", 6))
    if count < developing:
        return "latent"
    if count < operational:
        return "developing"
    if count < crisis:
        return "operational"
    return "crisis"


def _phase_as_of(pressure: Mapping[str, Any], rules: Mapping[str, Any], as_of: str) -> Optional[str]:
    chronology = pressure.get("chronology")
    if not isinstance(chronology, list):
        return None
    relevant = [
        row for row in chronology
        if isinstance(row, Mapping)
        and isinstance(row.get("at"), str)
        and row.get("at") <= as_of
    ]
    relevant.sort(key=lambda row: str(row.get("at")))
    if relevant:
        status_after = relevant[-1].get("status_after")
        if status_after in _TERMINAL:
            return "resolved"
    evidence_count = sum(1 for row in relevant if row.get("kind") == "committed_domain_evidence")
    return _phase_from_evidence_count(evidence_count, rules)


def _briefing_for_front_as_of(repository: Any, front_ref: str, as_of: str) -> Optional[Mapping[str, Any]]:
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
    phase = _phase_as_of(pressure, rules, as_of)
    concerns = pressure.get("stakes")
    visible_concerns = [value for value in concerns or [] if isinstance(value, str) and value][:3]
    if phase is None:
        return {
            "front_ref": front_ref,
            "title": title,
            "phase": None,
            "summary": f"Operational briefing concerning {title}.",
            "operational_concerns": visible_concerns,
            "as_of": as_of,
        }
    phase_text = {
        "latent": "being watched but not yet assessed as materially developing",
        "developing": "developing",
        "operational": "producing sustained operational consequences",
        "crisis": "at crisis-level operational pressure",
        "resolved": "resolved",
    }.get(phase, phase)
    return {
        "front_ref": front_ref,
        "title": title,
        "phase": phase,
        "summary": f"{title} is assessed as {phase_text}.",
        "operational_concerns": visible_concerns,
        "as_of": as_of,
    }


def _report_message(briefing: Mapping[str, Any]) -> Optional[str]:
    summary = briefing.get("summary")
    if not isinstance(summary, str):
        return None
    message = f"Mission Office operational briefing: {summary}"
    concerns = briefing.get("operational_concerns")
    if isinstance(concerns, list) and concerns:
        message += " Operational concerns: " + ", ".join(str(value) for value in concerns) + "."
    return message


def install_player_report_lifecycle() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from shinobi_runtime.api import player_report_projection as module

    original_recent = module._recent_player_report_refs
    original_project = module._project_delivery

    def recent_unhandled(repository: Any, player_id: str) -> list[str]:
        handled = _handled_report_refs(repository)
        return [ref for ref in original_recent(repository, player_id) if ref not in handled]

    def project_as_delivered(repository: Any, delivery_ref: str, player_id: str) -> Mapping[str, Any]:
        projected = copy.deepcopy(original_project(repository, delivery_ref, player_id))
        claim = projected.get("claim") if isinstance(projected, Mapping) else None
        delivery = projected.get("delivery") if isinstance(projected, Mapping) else None
        subject = claim.get("subject_ref") if isinstance(claim, Mapping) else None
        front_ref = subject if isinstance(subject, str) and subject.startswith("pressure_") else None
        if front_ref is None:
            front_ref = _full_event_world_front_for_delivery(repository, delivery_ref, player_id)
        delivered_at = delivery.get("delivered_at") if isinstance(delivery, Mapping) else None
        if isinstance(front_ref, str) and isinstance(delivered_at, str):
            briefing = _briefing_for_front_as_of(repository, front_ref, delivered_at)
            if isinstance(briefing, Mapping):
                projected["briefing"] = dict(briefing)
        return projected

    module._event_world_front_for_delivery = _full_event_world_front_for_delivery
    module._recent_player_report_refs = recent_unhandled
    module._project_delivery = project_as_delivered

    prior_play_context = CampaignOperations.play_context
    if not getattr(prior_play_context, "_player_report_lifecycle", False):
        @wraps(prior_play_context)
        def play_context(self: CampaignOperations) -> Mapping[str, Any]:
            response = copy.deepcopy(prior_play_context(self))
            campaign = response.get("campaign") if isinstance(response, Mapping) else None
            player_id = campaign.get("player_id") if isinstance(campaign, Mapping) else None
            if not isinstance(player_id, str):
                return response
            all_refs = original_recent(self.repository, player_id)
            handled = _handled_report_refs(self.repository)
            handled_messages: set[str] = set()
            for ref in all_refs:
                if ref not in handled:
                    continue
                try:
                    projected = project_as_delivered(self.repository, ref, player_id)
                except OperationError:
                    continue
                briefing = projected.get("briefing") if isinstance(projected, Mapping) else None
                if isinstance(briefing, Mapping):
                    message = _report_message(briefing)
                    if isinstance(message, str):
                        handled_messages.add(message)
            scene = response.get("scene") if isinstance(response, dict) else None
            narrative = scene.get("narrative") if isinstance(scene, dict) else None
            if isinstance(narrative, dict) and handled_messages:
                available = narrative.get("available_reports")
                if isinstance(available, list):
                    narrative["available_reports"] = [
                        value for value in available
                        if isinstance(value, str) and value not in handled_messages
                    ]
            object_reads = response.get("object_reads") if isinstance(response, dict) else None
            if isinstance(object_reads, dict):
                object_reads["suggested_report_refs"] = list(all_refs)
                object_reads["report_ref_count"] = len(all_refs)
                object_reads["unhandled_report_refs"] = [ref for ref in all_refs if ref not in handled]
                object_reads["handled_report_refs"] = [ref for ref in all_refs if ref in handled]
            validate_bounded_json(response, label="play context", allow_float=True)
            return response

        play_context._player_report_lifecycle = True
        CampaignOperations.play_context = play_context

    _INSTALLED = True


__all__ = [
    "install_player_report_lifecycle",
    "_briefing_for_front_as_of",
    "_full_event_world_front_for_delivery",
    "_handled_report_refs",
    "_phase_as_of",
]
