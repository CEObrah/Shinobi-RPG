"""Canonical Shinobi Era campaign timestamps.

The live Jianghu scheduler owns causal advancement in ``martial_world``.  This
module intentionally contains only the timestamp value object shared by command
and world-frontier code; event queues and scheduler storage are not separate
runtime authorities.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta


_CAMPAIGN_TIME = re.compile(
    r"^SE-(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})$"
)


@dataclass(frozen=True, order=True)
class CampaignTime:
    """A validated, lexically stable Shinobi Era timestamp."""

    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int

    def __post_init__(self) -> None:
        if self.year <= 0:
            raise ValueError("campaign time has no year zero")
        datetime(
            self.year,
            self.month,
            self.day,
            self.hour,
            self.minute,
            self.second,
        )

    @classmethod
    def parse(cls, value: str) -> "CampaignTime":
        if not isinstance(value, str):
            raise TypeError("campaign time must be text")
        match = _CAMPAIGN_TIME.fullmatch(value)
        if match is None:
            raise ValueError(f"invalid canonical campaign time: {value!r}")
        parsed = cls(*(int(match.group(name)) for name in (
            "year", "month", "day", "hour", "minute", "second"
        )))
        if str(parsed) != value:
            raise ValueError(f"noncanonical campaign time: {value!r}")
        return parsed

    def __str__(self) -> str:
        return (
            f"SE-{self.year:04d}-{self.month:02d}-{self.day:02d}"
            f"T{self.hour:02d}:{self.minute:02d}:{self.second:02d}"
        )

    def add_seconds(self, seconds: int) -> "CampaignTime":
        if isinstance(seconds, bool) or not isinstance(seconds, int):
            raise TypeError("seconds must be an integer")
        value = datetime(
            self.year,
            self.month,
            self.day,
            self.hour,
            self.minute,
            self.second,
        ) + timedelta(seconds=seconds)
        return CampaignTime(
            value.year,
            value.month,
            value.day,
            value.hour,
            value.minute,
            value.second,
        )

    def next_month_start(
        self,
        hour: int = 0,
        minute: int = 0,
        second: int = 0,
    ) -> "CampaignTime":
        year, month = self.year, self.month + 1
        if month == 13:
            year, month = year + 1, 1
        return CampaignTime(year, month, 1, hour, minute, second)

    def next_month_end(
        self,
        hour: int = 0,
        minute: int = 0,
        second: int = 0,
    ) -> "CampaignTime":
        start = self.next_month_start(hour, minute, second)
        day = calendar.monthrange(start.year, start.month)[1]
        return CampaignTime(start.year, start.month, day, hour, minute, second)
