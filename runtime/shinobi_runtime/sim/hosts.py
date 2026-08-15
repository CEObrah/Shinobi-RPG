"""Causal host state used by bounded lazy catch-up."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .events import CampaignTime


@dataclass
class HostState:
    host_id: str
    kind: str
    resolved_through: CampaignTime
    safe_through: CampaignTime
    handler_ref: str
    rng_namespace: str
    next_due: Optional[CampaignTime] = None

    def __post_init__(self) -> None:
        if not self.host_id or not self.kind or not self.handler_ref:
            raise ValueError("host_id, kind, and handler_ref must be non-empty")
        if self.safe_through < self.resolved_through:
            raise ValueError("safe_through cannot precede resolved_through")
        if self.next_due is not None and self.next_due <= self.resolved_through:
            raise ValueError("next_due must be later than resolved_through")

    def advance_resolved(self, reached: CampaignTime) -> None:
        if reached < self.resolved_through:
            raise ValueError("host resolution cannot move backward")
        self.resolved_through = reached
        if self.safe_through < reached:
            self.safe_through = reached

    def extend_safe_horizon(self, reached: CampaignTime) -> None:
        if reached < self.safe_through:
            raise ValueError("safe horizon cannot move backward")
        self.safe_through = reached

    def is_safe_to(self, target: CampaignTime) -> bool:
        # A saved safe horizon cannot hide a known, still-unsettled deadline.
        # ``next_due`` is part of the host's causal frontier: if it falls on or
        # before the requested target, the host is not closed through that
        # target even when a handler accidentally returned a later
        # ``safe_through`` value.
        return (
            self.safe_through >= target
            and (self.next_due is None or self.next_due > target)
        )

    def to_record(self) -> Mapping[str, Any]:
        return {
            "host_id": self.host_id,
            "kind": self.kind,
            "resolved_through": str(self.resolved_through),
            "safe_through": str(self.safe_through),
            "handler_ref": self.handler_ref,
            "rng_namespace": self.rng_namespace,
            "next_due": None if self.next_due is None else str(self.next_due),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "HostState":
        if not isinstance(record, Mapping):
            raise TypeError("host record must be an object")
        allowed = {
            "host_id", "kind", "resolved_through", "safe_through",
            "handler_ref", "rng_namespace", "next_due",
        }
        unknown = set(record) - allowed
        if unknown:
            raise ValueError(f"unknown host fields: {sorted(unknown)}")
        next_due = record.get("next_due")
        return cls(
            host_id=record.get("host_id"),
            kind=record.get("kind"),
            resolved_through=CampaignTime.parse(record.get("resolved_through")),
            safe_through=CampaignTime.parse(record.get("safe_through")),
            handler_ref=record.get("handler_ref"),
            rng_namespace=record.get("rng_namespace"),
            next_due=None if next_due is None else CampaignTime.parse(next_due),
        )
