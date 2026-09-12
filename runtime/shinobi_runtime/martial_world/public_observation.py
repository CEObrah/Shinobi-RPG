"""Ephemeral public observation for consequential Jianghu information.

Speech and casual observation are not save-game objects.  These helpers decide
who can plausibly hear or notice something at a real public site.  Only a later
real action, such as a pursuit movement, earns persistent state.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Mapping

_SITE_AUDIBILITY = {
    "inn": 620,
    "tea_house": 560,
    "wine_shop": 650,
    "market": 360,
    "caravan_yard": 500,
    "guild_hall": 520,
    "gambling_house": 680,
    "stable": 430,
}


def _permille(*parts: object) -> int:
    raw = "|".join(str(x) for x in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:4], "big") % 1000


def hears_public_disclosure(
    listener: Mapping[str, Any], *, speaker_ref: str, site_type: str,
    at: datetime, disclosure_ref: str,
) -> bool:
    """Whether one actually present listener catches ordinary conversational speech.

    This is intentionally not a rumor record. Perception and intelligence improve
    the chance of catching useful words in a noisy public venue; deterministic
    hashing makes the same scene replay identically.
    """
    base = int(_SITE_AUDIBILITY.get(str(site_type), 280))
    attrs = listener.get("attributes", {}) if isinstance(listener.get("attributes"), Mapping) else {}
    perception = max(0, int(attrs.get("perception", 0)))
    intelligence = max(0, int(attrs.get("intelligence", 0)))
    threshold = max(80, min(950, base + perception * 3 + intelligence - 180))
    block = at.hour // 2
    return _permille(listener.get("person_id", ""), speaker_ref, site_type, at.date().isoformat(), block, disclosure_ref) < threshold


def disclosure_credibility_milli(
    listener: Mapping[str, Any], *, speaker_ref: str, claimed_value_cash: int,
    at: datetime, disclosure_ref: str,
) -> int:
    """Return how much of an overheard value claim a listener is willing to price in.

    A claim can be true, exaggerated, or a lie.  The listener never receives the
    hidden true cargo value from this function.  High intelligence narrows, but
    does not remove, uncertainty.
    """
    attrs = listener.get("attributes", {}) if isinstance(listener.get("attributes"), Mapping) else {}
    intelligence = max(0, int(attrs.get("intelligence", 0)))
    perception = max(0, int(attrs.get("perception", 0)))
    quality = min(220, intelligence + perception)
    floor = min(850, 260 + quality * 2)
    roll = _permille("credibility", listener.get("person_id", ""), speaker_ref, claimed_value_cash, at.isoformat(), disclosure_ref)
    return max(100, min(950, floor + roll * max(0, 950 - floor) // 1000))


__all__ = ["disclosure_credibility_milli", "hears_public_disclosure"]
