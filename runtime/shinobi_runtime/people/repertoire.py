"""Small helpers for exact-person technique repertoire state.

`method_mastery` owns learned/learning technique values. A technique remains
non-field-usable while its ref is present in `latent_or_locked_techniques`.
This avoids persisting a second list that merely duplicates mastery-map keys.
"""

from __future__ import annotations

from typing import Any, FrozenSet, Mapping


def field_usable_method_refs(record: Mapping[str, Any]) -> FrozenSet[str]:
    repertoire = record.get("repertoire")
    if not isinstance(repertoire, Mapping):
        raise ValueError("technique repertoire must be an object")
    mastery = repertoire.get("method_mastery")
    latent = repertoire.get("latent_or_locked_techniques")
    if not isinstance(mastery, Mapping) or not isinstance(latent, list):
        raise ValueError("technique repertoire state is invalid")
    if any(not isinstance(ref, str) or not ref for ref in mastery):
        raise ValueError("technique mastery refs must be non-empty strings")
    if any(not isinstance(ref, str) or not ref for ref in latent):
        raise ValueError("latent technique refs must be non-empty strings")
    latent_refs = set(latent)
    return frozenset(ref for ref in mastery if ref not in latent_refs)
