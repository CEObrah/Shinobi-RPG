"""Player-facing copper/tael presentation over integer copper accounting."""
from __future__ import annotations

from typing import Any

_COPPER_PER_TAEL = 1000


def copper_breakdown(value: Any) -> dict[str, int | str]:
    copper = max(0, int(value))
    taels, remainder = divmod(copper, _COPPER_PER_TAEL)
    if taels and remainder:
        text = f"{taels:,} taels, {remainder:,} copper"
    elif taels:
        text = f"{taels:,} taels"
    else:
        text = f"{remainder:,} copper"
    return {"copper": copper, "taels": taels, "remainder_copper": remainder, "display": text}


def format_copper(value: Any) -> str:
    return str(copper_breakdown(value)["display"])


__all__ = ["copper_breakdown", "format_copper"]
