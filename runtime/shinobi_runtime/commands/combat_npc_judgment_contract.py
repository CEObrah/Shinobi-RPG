"""Wire compatibility for LLM-authored exact-combat NPC judgments.

The exact combat resolver intentionally consumes one strict canonical shape.
This module accepts a narrow set of harmless no-op aliases at the command
boundary without selecting or changing any meaningful NPC combat choice.
"""
from __future__ import annotations

from typing import Any, Mapping


ATTACK_JUDGMENT_WIRE_CONTRACT = {
    "qi_allocation_milli": {
        "canonical_no_qi": {},
        "accepted_no_qi_aliases": [0],
        "rule": "Only scalar 0 is a compatibility alias. Positive scalar Qi remains invalid because the runtime may not choose allocation channels for the GM.",
    },
    "poison_ref": {
        "canonical_no_poison": None,
        "accepted_no_poison_aliases": ["", "none"],
        "rule": "Case-insensitive 'none' is a compatibility alias for null. Non-null poison selection remains GM-authored and exact-combat validated.",
    },
}


def normalize_jianghu_combat_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Canonicalize representation-only aliases in NPC attack judgments.

    This function never chooses intent, target, weapon, action, hit zone, Qi
    channels, poison, or movement. Values that would require such a choice are
    deliberately left untouched so the strict exact-combat validator rejects
    them normally.
    """

    judgments = payload.get("npc_judgments")
    if not isinstance(judgments, Mapping):
        return payload

    normalized_judgments: dict[str, Any] | None = None
    for actor_ref, raw_judgment in judgments.items():
        if not isinstance(raw_judgment, Mapping):
            continue
        if str(raw_judgment.get("intent") or "").strip().lower() != "attack":
            continue

        row = dict(raw_judgment)
        changed = False

        qi_allocation = row.get("qi_allocation_milli")
        if isinstance(qi_allocation, int) and not isinstance(qi_allocation, bool) and qi_allocation == 0:
            row["qi_allocation_milli"] = {}
            changed = True

        poison_ref = row.get("poison_ref")
        if isinstance(poison_ref, str) and poison_ref.strip().lower() == "none":
            row["poison_ref"] = None
            changed = True

        if not changed:
            continue
        if normalized_judgments is None:
            normalized_judgments = dict(judgments)
        normalized_judgments[str(actor_ref)] = row

    if normalized_judgments is None:
        return payload

    normalized_payload = dict(payload)
    normalized_payload["npc_judgments"] = normalized_judgments
    return normalized_payload
