"""Deterministic scheduling primitives for the Shinobi campaign runtime."""

from .catchup import (
    CatchUpEngine,
    CatchUpResult,
    EventOutcome,
    PlayerInterrupt,
    SettlementError,
)
from .events import CampaignDate, CampaignTime, EventConflict, EventQueue, ScheduledEvent
from .hosts import HostState
from .rng import CounterRNG, DrawReceipt
from .recurrence import RecurrenceError, boundaries_through, next_due

__all__ = [
    "CampaignDate",
    "CampaignTime",
    "CatchUpEngine",
    "CatchUpResult",
    "CounterRNG",
    "DrawReceipt",
    "EventConflict",
    "EventOutcome",
    "EventQueue",
    "HostState",
    "PlayerInterrupt",
    "RecurrenceError",
    "ScheduledEvent",
    "SettlementError",
    "boundaries_through",
    "next_due",
]
