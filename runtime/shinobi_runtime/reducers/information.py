"""Structured information delivery without truth/knowledge conflation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from shinobi_runtime.sim.events import CampaignTime


@dataclass(frozen=True)
class InformationClaim:
    claim_id: str
    subject_ref: str
    source_ref: str
    collected_at: CampaignTime
    epistemic_kind: str
    confidence_milli: int
    fact_ref: Optional[str] = None
    evidence_refs: Tuple[str, ...] = ()
    expires_at: Optional[CampaignTime] = None

    def __post_init__(self) -> None:
        for field in ("claim_id", "subject_ref", "source_ref"):
            if not isinstance(getattr(self, field), str) or not getattr(self, field):
                raise ValueError(f"{field} must be non-empty")
        if self.epistemic_kind not in ("observation", "report", "inference", "rumor"):
            raise ValueError("unsupported epistemic kind")
        if (
            isinstance(self.confidence_milli, bool)
            or not isinstance(self.confidence_milli, int)
            or not 0 <= self.confidence_milli <= 1000
        ):
            raise ValueError("confidence_milli must be in 0..1000")
        if self.expires_at is not None and self.expires_at < self.collected_at:
            raise ValueError("claim expiry precedes collection")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("claim contains duplicate evidence references")


@dataclass(frozen=True)
class InformationDelivery:
    delivery_id: str
    claim_id: str
    sender_ref: str
    recipient_ref: str
    channel: str
    delivered_at: CampaignTime
    resulting_epistemic_kind: str
    resulting_confidence_milli: int
    evidence_refs: Tuple[str, ...]

    def to_record(self) -> Mapping[str, Any]:
        return {
            "delivery_id": self.delivery_id,
            "claim_id": self.claim_id,
            "sender_ref": self.sender_ref,
            "recipient_ref": self.recipient_ref,
            "channel": self.channel,
            "delivered_at": str(self.delivered_at),
            "resulting_epistemic_kind": self.resulting_epistemic_kind,
            "resulting_confidence_milli": self.resulting_confidence_milli,
            "evidence_refs": list(self.evidence_refs),
        }


def deliver_claim(
    claim: InformationClaim,
    *,
    delivery_id: str,
    sender_ref: str,
    recipient_ref: str,
    channel: str,
    delivered_at: CampaignTime,
    channel_confidence_milli: int = 1000,
) -> InformationDelivery:
    for name, value in (
        ("delivery_id", delivery_id),
        ("sender_ref", sender_ref),
        ("recipient_ref", recipient_ref),
        ("channel", channel),
    ):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be non-empty")
    if delivered_at < claim.collected_at:
        raise ValueError("information cannot arrive before collection")
    if claim.expires_at is not None and delivered_at > claim.expires_at:
        raise ValueError("expired information requires an explicit stale-claim path")
    if (
        isinstance(channel_confidence_milli, bool)
        or not isinstance(channel_confidence_milli, int)
        or not 0 <= channel_confidence_milli <= 1000
    ):
        raise ValueError("channel confidence must be in 0..1000")
    resulting = (claim.confidence_milli * channel_confidence_milli + 500) // 1000
    resulting_kind = "report" if claim.epistemic_kind == "observation" else claim.epistemic_kind
    return InformationDelivery(
        delivery_id=delivery_id,
        claim_id=claim.claim_id,
        sender_ref=sender_ref,
        recipient_ref=recipient_ref,
        channel=channel,
        delivered_at=delivered_at,
        resulting_epistemic_kind=resulting_kind,
        resulting_confidence_milli=resulting,
        evidence_refs=claim.evidence_refs,
    )
