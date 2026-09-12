"""Durable GM-private frontiers for consequential NPC human judgment.

The scheduler may preserve a bounded decision envelope at the exact campaign
frontier where objective causality made the choice relevant.  Python is allowed
to calculate facts, constraints, feasibility and advisory scores; it does not
select the meaningful human intent.  ChatGPT resolves one of the currently
validated options through the dedicated semantic command, which then commits
the hard consequence without advancing campaign time.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping, Sequence

from .handoffs import classify_handoff


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _option_ref(kind: str, actor_ref: str, owner_ref: str, payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        (kind + "\0" + actor_ref + "\0" + owner_ref + "\0" + _canonical(payload)).encode("utf-8")
    ).hexdigest()[:20]
    return f"npc-option:{digest}"


def build_npc_judgment_event(
    *, judgment_kind: str, at_iso: str, actor_ref: str = "", owner_ref: str = "",
    options: Sequence[Mapping[str, Any]], decision_context: Mapping[str, Any] | None = None,
    source_event_id: str = "",
) -> dict[str, Any]:
    """Build one stable same-frontier scheduler event without choosing an option."""
    kind = str(judgment_kind or "").strip()
    actor = str(actor_ref or "").strip()
    owner = str(owner_ref or actor or "").strip()
    if not kind or not at_iso or not owner:
        raise ValueError("npc judgment identity incomplete")
    normalized: list[dict[str, Any]] = []
    for raw in options:
        if not isinstance(raw, Mapping):
            continue
        payload = copy.deepcopy(dict(raw))
        payload.pop("option_ref", None)
        option_ref = _option_ref(kind, actor, owner, payload)
        normalized.append({"option_ref": option_ref, **payload})
    if not normalized:
        raise ValueError("npc judgment requires at least one feasible option")
    normalized.sort(key=lambda row: str(row["option_ref"]))
    context = copy.deepcopy(dict(decision_context or {}))
    identity = {
        "judgment_kind": kind,
        "actor_ref": actor,
        "owner_ref": owner,
        "source_event_id": str(source_event_id or ""),
        "options": normalized,
        "decision_context": context,
        "due_at": str(at_iso),
    }
    digest = hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()[:24]
    return {
        "event_id": f"npc_judgment_due:{digest}",
        "kind": "npc_judgment_due",
        "due_at": str(at_iso),
        "owner_ref": owner,
        "actor_ref": actor,
        "judgment_kind": kind,
        "decision_ref": f"npc-judgment:{digest}",
        "source_event_id": str(source_event_id or ""),
        "requires_gm_judgment": True,
        "requires_player_decision": False,
        "decision_context": context,
        "options": normalized,
    }


def judgment_handoff(event: Mapping[str, Any]) -> dict[str, Any]:
    """Return the GM-private handoff projection for one pending judgment event."""
    if str(event.get("kind") or "") != "npc_judgment_due":
        raise ValueError("not an npc judgment event")
    row = copy.deepcopy(dict(event))
    row["handoff"] = classify_handoff(row)
    return row


def selected_option(event: Mapping[str, Any], option_ref: str) -> dict[str, Any]:
    """Resolve only an exact currently offered option reference."""
    if str(event.get("kind") or "") != "npc_judgment_due" or event.get("requires_gm_judgment") is not True:
        raise ValueError("npc judgment event invalid")
    ref = str(option_ref or "")
    for raw in event.get("options", []) if isinstance(event.get("options"), Sequence) else []:
        if isinstance(raw, Mapping) and str(raw.get("option_ref") or "") == ref:
            return copy.deepcopy(dict(raw))
    raise ValueError("npc judgment option unavailable")


_PLAYER_AUTHORED_REF_KEYS = frozenset({
    "participant_ref", "participant_refs",
    "attacker_ref", "attacker_refs",
    "escort_ref", "escort_refs",
    "leader_ref", "leader_refs",
    "commander_ref", "commander_refs",
    "worker_ref", "worker_refs",
    "entrant_ref", "entrant_refs",
    "volunteer_ref", "volunteer_refs",
})


def option_authors_player_intent(
    *, judgment_kind: str, option: Mapping[str, Any], player_ref: str,
) -> bool:
    """Return whether an NPC judgment option would volunteer/control the player.

    NPCs may lawfully choose actions *targeting* Wei, so generic ``*_ref``
    scanning is intentionally forbidden.  This guard follows semantic actor /
    participant fields only.  A few judgment kinds use the generic
    ``person_ref`` field for the person whose voluntary status would change and
    therefore receive an explicit kind-specific check.
    """
    player = str(player_ref or "")
    if not player or not isinstance(option, Mapping):
        return False

    def contains_player(value: Any) -> bool:
        if isinstance(value, str):
            return value == player
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return any(contains_player(item) for item in value)
        return False

    def scan(value: Any) -> bool:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if str(key) in _PLAYER_AUTHORED_REF_KEYS and contains_player(nested):
                    return True
                if scan(nested):
                    return True
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return any(scan(item) for item in value)
        return False

    if scan(option):
        return True
    if str(judgment_kind or "") in {
        "faction_office_appointment", "civic_office_appointment",
        "faction_membership_departure", "retinue_assignment_policy",
    }:
        return str(option.get("person_ref") or "") == player
    return False


def institutional_office_judgment_events(
    *, faction_ref: str, at_iso: str, office_result: Mapping[str, Any],
    succession: Mapping[str, Any] | None = None, source_event_id: str = "",
) -> list[dict[str, Any]]:
    """Convert current vacancy evidence into bounded GM-authored appointment events."""
    claims = {
        str(row.get("person_ref") or ""): copy.deepcopy(dict(row))
        for row in (succession.get("claimant_options", []) if isinstance(succession, Mapping) else [])
        if isinstance(row, Mapping) and row.get("person_ref")
    }
    out: list[dict[str, Any]] = []
    for vacancy in office_result.get("appointment_judgments", []) if isinstance(office_result, Mapping) else []:
        if not isinstance(vacancy, Mapping):
            continue
        office = str(vacancy.get("office") or "")
        if not office:
            continue
        options: list[dict[str, Any]] = []
        for raw in vacancy.get("options", []) if isinstance(vacancy.get("options"), Sequence) else []:
            if not isinstance(raw, Mapping) or not raw.get("person_ref"):
                continue
            row = copy.deepcopy(dict(raw))
            ref = str(row.get("person_ref") or "")
            payload = {"intent": "appoint", "office": office, **row}
            if office == "leader" and ref in claims:
                payload["succession_claim"] = claims[ref]
            options.append(payload)
        options.append({"intent": "leave_vacant", "office": office})
        event = build_npc_judgment_event(
            judgment_kind="faction_office_appointment", at_iso=at_iso,
            actor_ref=faction_ref, owner_ref=faction_ref, options=options,
            decision_context={
                "office": office,
                "relevant_skill": str(vacancy.get("relevant_skill") or ""),
                "recognized_claimants": list(claims.values()) if office == "leader" else [],
            },
            source_event_id=source_event_id,
        )
        out.append(event)
    return out


def remove_pending_judgment(schedule: Mapping[str, Any], event_id: str) -> dict[str, Any]:
    out = copy.deepcopy(dict(schedule))
    one_off = out.get("one_off")
    if not isinstance(one_off, dict) or str(event_id) not in one_off:
        raise ValueError("npc judgment event missing")
    one_off.pop(str(event_id), None)
    return out


__all__ = [
    "build_npc_judgment_event", "judgment_handoff", "selected_option", "institutional_office_judgment_events",
    "option_authors_player_intent", "remove_pending_judgment",
]
