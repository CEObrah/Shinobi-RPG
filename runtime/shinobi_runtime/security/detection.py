"""Deterministic route-crossing detection for persisted security sectors.

Security coverage is not omniscience. A protected route creates a detection
opportunity only for a caller that has already established a materially risky
crossing (for example a foreign military formation or contraband shipment).
Successful detection creates exact evidence, an exact alarm, and a routed
information claim. Failed detection creates no knowledge.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, Mapping, MutableMapping, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.information import InformationStore
from shinobi_runtime.reducers import InformationClaim, deliver_claim
from shinobi_runtime.sim.events import CampaignTime

SECURITY_PATH = "state/reg/security-networks.json"


def decode_security_information_records(encoded_writes: Mapping[str, bytes]) -> Dict[str, Dict[str, Any]]:
    """Decode only security/information after-images already staged by a base plan."""
    staged: Dict[str, Dict[str, Any]] = {}
    for path, payload in encoded_writes.items():
        if path != SECURITY_PATH and path != "state/reg/information-deliveries.json" and not path.startswith("state/reg/information/"):
            continue
        try:
            value = json.loads(payload.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CommandRejectedError("security_detection_staged_record_invalid") from exc
        if not isinstance(value, dict):
            raise CommandRejectedError("security_detection_staged_record_invalid")
        staged[path] = value
    return staged


def _roll(*parts: object) -> int:
    raw = "\x00".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") % 1000


def apply_route_security_detection(
    owner: Any,
    *,
    command: Any,
    at: CampaignTime,
    route_ref: str,
    subject_ref: str,
    crossing_ref: str,
    world_events: Dict[str, Any],
    staged_records: MutableMapping[str, Dict[str, Any]],
    intrusion: bool,
    concealment_milli: int = 0,
    subject_owner_refs: Sequence[str] = (),
) -> list[Dict[str, Any]]:
    """Evaluate all active sectors protecting one exact route crossing.

    The crossing owner decides whether the movement is materially risky; this
    function does not infer hostility from hidden truth. Sector effectiveness is
    deterministic for the exact crossing identity, preventing reroll exploits.
    """
    if not intrusion or route_ref == "route_local":
        return []
    if not isinstance(route_ref, str) or not route_ref or not isinstance(subject_ref, str) or not subject_ref or not isinstance(crossing_ref, str) or not crossing_ref:
        raise CommandRejectedError("security_detection_crossing_invalid")
    if isinstance(concealment_milli, bool) or not isinstance(concealment_milli, int) or not 0 <= concealment_milli <= 1000:
        raise CommandRejectedError("security_detection_concealment_invalid")

    raw = staged_records.get(SECURITY_PATH)
    if raw is None:
        try:
            loaded = owner.repository.read_json(SECURITY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("security-network-registry_invalid") from exc
        if not isinstance(loaded, dict):
            raise CommandRejectedError("security-network-registry_invalid")
        raw = copy.deepcopy(loaded)
        staged_records[SECURITY_PATH] = raw
    sectors = raw.get("sectors")
    alarms = raw.get("alarms")
    detections = raw.setdefault("detections", {})
    if not isinstance(sectors, Mapping) or not isinstance(alarms, dict) or not isinstance(detections, dict):
        raise CommandRejectedError("security-network-registry_invalid")

    information = InformationStore(owner.repository, staged_records)
    results: list[Dict[str, Any]] = []
    crossing_owners = {ref for ref in subject_owner_refs if isinstance(ref, str) and ref}

    for sector_ref, sector in sorted(sectors.items()):
        if not isinstance(sector_ref, str) or not isinstance(sector, Mapping):
            continue
        if sector.get("status") not in ("active", "degraded"):
            continue
        route_refs = sector.get("route_refs")
        if not isinstance(route_refs, list) or route_ref not in route_refs:
            continue
        authorized = {ref for ref in sector.get("authorized_owner_refs", []) if isinstance(ref, str) and ref}
        if crossing_owners and authorized.intersection(crossing_owners):
            continue
        coverage = sector.get("coverage_milli")
        detection = sector.get("detection_milli")
        if any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000 for value in (coverage, detection)):
            raise CommandRejectedError("security-network-registry_invalid")
        effective = (coverage * detection) // 1000
        if sector.get("status") == "degraded":
            effective //= 2
        effective = max(0, min(1000, effective - concealment_milli))
        roll = _roll(sector_ref, crossing_ref, subject_ref, route_ref)
        if effective <= 0 or roll >= effective:
            continue

        digest = hashlib.sha256(f"{sector_ref}\x00{crossing_ref}\x00{subject_ref}".encode()).hexdigest()[:20]
        detection_ref = f"detection.security.{digest}"
        existing = detections.get(detection_ref)
        if isinstance(existing, Mapping):
            results.append(dict(existing))
            continue
        owner_ref = sector.get("owner_ref")
        place_ref = sector.get("place_ref")
        classification = str(sector.get("classification") or "restricted")
        if not isinstance(owner_ref, str) or not owner_ref or not isinstance(place_ref, str) or not place_ref:
            raise CommandRejectedError("security-network-registry_invalid")
        event_id = owner._append_internal_event(
            world_events,
            command=command,
            identity=detection_ref,
            kind="security_detection",
            at=at,
            host_refs=(owner_ref, sector_ref),
            place_refs=(place_ref,),
            causal_refs=(crossing_ref,),
            affected_owner_refs=(SECURITY_PATH,),
            material_consequence_refs=(detection_ref, subject_ref),
            classification=classification,
            audience_refs=(owner_ref,),
            source_refs=(sector_ref,),
            reducer_ref="shinobi_runtime.security.detection.apply_route_security_detection",
        )
        alarm_ref = f"alarm.auto.{digest}"
        alarms[alarm_ref] = {
            "id": alarm_ref,
            "sector_ref": sector_ref,
            "subject_ref": subject_ref,
            "evidence_ref": event_id,
            "status": "open",
            "opened_at": str(at),
            "resolved_at": None,
            "recipient_refs": [owner_ref],
            "classification": classification,
        }
        detections[detection_ref] = {
            "id": detection_ref,
            "sector_ref": sector_ref,
            "route_ref": route_ref,
            "subject_ref": subject_ref,
            "crossing_ref": crossing_ref,
            "detected_at": str(at),
            "effective_detection_milli": effective,
            "roll_milli": roll,
            "evidence_event_ref": event_id,
            "alarm_ref": alarm_ref,
        }
        claim_id = f"claim.security.{digest}"
        claim = InformationClaim(
            claim_id=claim_id,
            subject_ref=subject_ref,
            source_ref=sector_ref,
            collected_at=at,
            epistemic_kind="observation",
            confidence_milli=max(600, min(950, 600 + effective // 3)),
            evidence_refs=(event_id,),
        )
        claim_record = {
            "claim_id": claim.claim_id,
            "subject_ref": claim.subject_ref,
            "source_ref": claim.source_ref,
            "collected_at": str(claim.collected_at),
            "epistemic_kind": claim.epistemic_kind,
            "confidence_milli": claim.confidence_milli,
            "evidence_refs": list(claim.evidence_refs),
        }
        try:
            information.add_claim(claim_record)
            information.grant(sector_ref, claim_id)
            delivery = deliver_claim(
                claim,
                delivery_id=f"delivery.security.{digest}",
                sender_ref=sector_ref,
                recipient_ref=owner_ref,
                channel="automatic_security_alarm",
                delivered_at=at,
                channel_confidence_milli=950,
            )
            information.add_delivery(dict(delivery.to_record()))
            information.grant(owner_ref, claim_id)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("information_registry_invalid") from exc
        results.append(dict(detections[detection_ref]))
    return results
