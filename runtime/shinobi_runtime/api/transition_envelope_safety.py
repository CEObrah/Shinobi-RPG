"""Production safety for bounded current-transition combat recovery.

The current-transition projection intentionally returns an exact chronological
page of receipt events and, on the first combat page, an optional compact
narrative spine. Rich combat receipts can make those two views of the same
transition exceed the public game-object JSON envelope even though the exact
page alone is valid. This installer preserves the exact event page and trims
only the optional material-beat spine until the projection fits.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _trim_optional_combat_narrative(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    narrative = value.get("combat_narrative")
    if not isinstance(narrative, dict):
        return False
    beats = narrative.get("material_beats")
    if not isinstance(beats, list) or not beats:
        return False

    retained = max(0, len(beats) // 2)
    del beats[retained:]

    material_count = narrative.get("material_event_count")
    if isinstance(material_count, int) and not isinstance(material_count, bool):
        narrative["material_beats_truncated"] = material_count > len(beats)
        narrative["omitted_material_beat_count"] = max(0, material_count - len(beats))
    else:
        narrative["material_beats_truncated"] = True
    return True


def install_production_transition_envelope_safety() -> None:
    """Bound only optional combat narrative duplication in production reads.

    The base validator remains authoritative for the envelope. We first validate
    unchanged. Only when a first-page combat projection is too large do we
    progressively halve ``combat_narrative.material_beats``. Exact ``events``,
    command/result metadata, pagination, and identity redaction are untouched.
    If the exact page still cannot fit after the optional spine reaches zero,
    the original validation error is preserved rather than weakening limits.
    """

    from shinobi_runtime.api import transition_operations as transition_module

    current = transition_module.validate_bounded_json
    if getattr(current, "_shinobi_transition_envelope_safety", False):
        return

    original_validate = current

    def bounded_validate(value, *, label: str, allow_float: bool = False):
        try:
            return original_validate(value, label=label, allow_float=allow_float)
        except ValueError as original_error:
            if label != "game object projection" or not isinstance(value, Mapping):
                raise
            narrative = value.get("combat_narrative")
            if not isinstance(narrative, Mapping):
                raise

            while _trim_optional_combat_narrative(value):
                try:
                    return original_validate(value, label=label, allow_float=allow_float)
                except ValueError:
                    continue

            try:
                return original_validate(value, label=label, allow_float=allow_float)
            except ValueError:
                raise original_error

    bounded_validate._shinobi_transition_envelope_safety = True
    transition_module.validate_bounded_json = bounded_validate


__all__ = ["install_production_transition_envelope_safety"]
