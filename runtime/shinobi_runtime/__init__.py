"""Deterministic runtime primitives for the Shinobi campaign.

The package is deliberately independent from narration and model APIs.  Runtime
callers must resolve intent before invoking these primitives and may narrate only
after the corresponding transaction has been durably committed.
"""

__version__ = "0.1.0"

