"""Presentation-only Jianghu scene sessions and attributed speech history.

These owners preserve continuity for live conversations without becoming a
second mechanics engine.  They may record who was in an established scene,
which player questions remain open, and what a named participant was attributed
as saying.  They never move bodies, grant authority, spend resources, reveal
hidden truth, or prove that an attributed statement was objectively correct.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from typing import Any, Callable

SESSION_PATH = "state/martial-world/scene-session.json"
ATTEMPT_LEDGER_PATH = "state/martial-world/interaction-attempts.json"
HISTORY_HEAD_PATH = "state/martial-world/scene-history-head.json"
HISTORY_DIR = "state/martial-world/scene-history"
HISTORY_HEAD_LIMIT = 48
HISTORY_SHARD_LIMIT = 256
RECENT_RESOLVED_ATTEMPT_LIMIT = 128

SESSION_KINDS = frozenset({
    "conversation", "house_council", "mission_briefing", "audience",
    "family_discussion", "negotiation", "training_review", "examination",
    "command_conference", "interview",
})
SPEECH_KINDS = frozenset({
    "clarification", "opinion", "inference", "question", "advice",
    "objection", "observation", "nonbinding_proposal", "nonbinding_response",
})
CLOSE_REASONS = frozenset({
    "completed", "player_left", "hard_interruption", "skipped_to_conclusion",
    "superseded", "cancelled",
})
INTERACTION_ACTIONS = frozenset({
    "present", "request", "report", "ask", "offer", "decline", "comply",
    "withdraw", "proceed", "seek_contact",
})
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")


def _optional(read_json: Callable[[str], Any], path: str) -> Any:
    try:
        return read_json(path)
    except (FileNotFoundError, KeyError):
        return None


def safe_ref(value: object, code: str) -> str:
    if not isinstance(value, str) or not _SAFE_REF.fullmatch(value):
        raise ValueError(code)
    return value


def bounded_text(value: object, code: str, maximum: int, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(c in value for c in ("\x00", "\r")):
        raise ValueError(code)
    return value.strip()


def active_scene_session(read_json: Callable[[str], Any]) -> dict[str, Any] | None:
    row = _optional(read_json, SESSION_PATH)
    if not isinstance(row, Mapping) or row.get("schema") != "jianghu-scene-session-1.0" or row.get("status") != "active":
        return None
    return copy.deepcopy(dict(row))


def session_projection(read_json: Callable[[str], Any]) -> dict[str, Any] | None:
    row = active_scene_session(read_json)
    if row is None:
        return None
    questions = [str(x) for x in row.get("open_question_refs", []) if isinstance(x, str)]
    return {
        "session_ref": row.get("session_ref"),
        "kind": row.get("kind"),
        "status": "active",
        "location_ref": row.get("location_ref"),
        "process_ref": row.get("process_ref"),
        "participant_refs": list(row.get("participant_refs", [])),
        "started_at": row.get("started_at"),
        "soft_end_at": row.get("soft_end_at"),
        "purpose": row.get("purpose"),
        "agenda": list(row.get("agenda", [])),
        "open_question_refs": questions[-16:],
        "open_question_count": len(questions),
        "open_questions_truncated": len(questions) > 16,
        "authority": False,
        "mechanical_consequence_authority": False,
    }


def new_session_record(*, session_ref: str, kind: str, location_ref: str, participant_refs: Sequence[str], at: str,
                       process_ref: str | None = None, purpose: str | None = None, agenda: Sequence[str] = (),
                       soft_end_at: str | None = None) -> dict[str, Any]:
    if kind not in SESSION_KINDS:
        raise ValueError("jianghu_scene_kind_invalid")
    participants = list(dict.fromkeys(safe_ref(x, "jianghu_scene_participant_invalid") for x in participant_refs))
    if not participants or len(participants) > 128:
        raise ValueError("jianghu_scene_participant_invalid")
    if len(agenda) > 32:
        raise ValueError("jianghu_scene_agenda_invalid")
    return {
        "schema": "jianghu-scene-session-1.0",
        "authority": False,
        "mechanical_consequence_authority": False,
        "session_ref": safe_ref(session_ref, "jianghu_scene_ref_invalid"),
        "kind": kind,
        "status": "active",
        "location_ref": safe_ref(location_ref, "jianghu_scene_location_invalid"),
        "process_ref": safe_ref(process_ref, "jianghu_scene_process_invalid") if process_ref else None,
        "participant_refs": participants,
        "started_at": str(at),
        "soft_end_at": str(soft_end_at) if soft_end_at else None,
        "purpose": bounded_text(purpose, "jianghu_scene_purpose_invalid", 1000, optional=True),
        "agenda": [bounded_text(x, "jianghu_scene_agenda_invalid", 500) for x in agenda],
        "open_question_refs": [],
        "closed_at": None,
        "close_reason": None,
        "last_updated_at": str(at),
    }


def close_session_record(session: Mapping[str, Any], *, at: str, reason: str) -> dict[str, Any]:
    if reason not in CLOSE_REASONS:
        raise ValueError("jianghu_scene_close_reason_invalid")
    out = copy.deepcopy(dict(session))
    out["status"] = "closed"
    out["closed_at"] = str(at)
    out["close_reason"] = reason
    out["last_updated_at"] = str(at)
    return out


def close_active_session_writes(
    read_json: Callable[[str], Any], *, at: str, reason: str
) -> dict[str, Mapping[str, Any]]:
    """Return non-mechanical scene-close writes for one hard/procedural boundary.

    This helper never moves a person or settles a consequence. It only closes the
    reversible conversation and abandons unresolved conversational threads so a
    travel/combat/time command cannot leave a stale live scene behind.
    """
    session = active_scene_session(read_json)
    if session is None:
        return {}
    ledger = interaction_ledger(read_json)
    ledger, _ = abandon_session_questions(
        ledger, session_ref=str(session.get("session_ref") or ""), at=str(at)
    )
    return {
        SESSION_PATH: close_session_record(session, at=str(at), reason=reason),
        ATTEMPT_LEDGER_PATH: ledger,
    }


def normalized_attempt(row: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(row))
    out.setdefault("scene_session_ref", None)
    out.setdefault("topic", None)
    out.setdefault("scopes", [])
    is_q = out.get("action") == "ask" and isinstance(out.get("player_statement"), str) and bool(out.get("player_statement"))
    out.setdefault("thread_status", "open" if is_q and out.get("scene_session_ref") else "not_applicable")
    out.setdefault("resolved_at", None)
    out.setdefault("response_ref", None)
    return out


def interaction_ledger(read_json: Callable[[str], Any]) -> dict[str, Any]:
    row = _optional(read_json, ATTEMPT_LEDGER_PATH)
    if isinstance(row, Mapping) and row.get("schema") == "jianghu-interaction-attempt-ledger-1.0":
        out = copy.deepcopy(dict(row))
        out["attempts"] = [normalized_attempt(x) for x in out.get("attempts", []) if isinstance(x, Mapping)]
        return out
    return {
        "schema": "jianghu-interaction-attempt-ledger-1.0",
        "authority": False,
        "mechanical_consequence_authority": False,
        "total_recorded": 0,
        "attempts": [],
    }


def trim_interaction_ledger(ledger: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(ledger))
    rows = [normalized_attempt(x) for x in out.get("attempts", []) if isinstance(x, Mapping)]
    open_rows = [x for x in rows if x.get("thread_status") == "open"]
    closed_rows = [x for x in rows if x.get("thread_status") != "open"]
    out["attempts"] = open_rows + closed_rows[-RECENT_RESOLVED_ATTEMPT_LIMIT:]
    return out


def abandon_session_questions(ledger: Mapping[str, Any], *, session_ref: str, at: str) -> tuple[dict[str, Any], int]:
    out = copy.deepcopy(dict(ledger)); changed = 0
    rows = []
    for raw in out.get("attempts", []):
        if not isinstance(raw, Mapping):
            continue
        row = normalized_attempt(raw)
        if row.get("scene_session_ref") == session_ref and row.get("thread_status") == "open":
            row["thread_status"] = "abandoned_with_scene_close"; row["resolved_at"] = str(at); changed += 1
        rows.append(row)
    out["attempts"] = rows
    return trim_interaction_ledger(out), changed


def resolve_question(ledger: Mapping[str, Any], *, question_ref: str, response_ref: str, at: str) -> tuple[dict[str, Any], bool]:
    out = copy.deepcopy(dict(ledger)); changed = False; rows = []
    for raw in out.get("attempts", []):
        if not isinstance(raw, Mapping): continue
        row = normalized_attempt(raw)
        if row.get("attempt_ref") == question_ref and row.get("thread_status") == "open":
            row["thread_status"] = "answered"; row["resolved_at"] = str(at); row["response_ref"] = response_ref; changed = True
        rows.append(row)
    out["attempts"] = rows
    return trim_interaction_ledger(out), changed


def history_head(read_json: Callable[[str], Any]) -> dict[str, Any]:
    row = _optional(read_json, HISTORY_HEAD_PATH)
    if isinstance(row, Mapping) and row.get("schema") == "jianghu-scene-history-head-1.0":
        return copy.deepcopy(dict(row))
    return {
        "schema": "jianghu-scene-history-head-1.0", "authority": False,
        "mechanical_consequence_authority": False, "total_recorded": 0,
        "latest_shard_ref": None, "recent": [],
    }


def recent_scene_history(read_json: Callable[[str], Any], limit: int = 8) -> list[dict[str, Any]]:
    head = history_head(read_json)
    rows = [copy.deepcopy(dict(x)) for x in head.get("recent", []) if isinstance(x, Mapping)]
    return rows[-max(1, min(int(limit), HISTORY_HEAD_LIMIT)):]


def _period(at: str) -> str:
    text = str(at).removeprefix("SE-")
    match = re.match(r"^(\d{4})-(\d{2})", text)
    return f"{match.group(1)}-{match.group(2)}" if match else "unknown"


def shard_ref_for(at: str, part: int = 1) -> str:
    return f"scene_history_{_period(at)}_part_{part:04d}"


def shard_path(ref: str) -> str:
    safe_ref(ref, "jianghu_scene_history_ref_invalid")
    if not ref.startswith("scene_history_"):
        raise ValueError("jianghu_scene_history_ref_invalid")
    return f"{HISTORY_DIR}/{ref}.json"


def history_shard(read_json: Callable[[str], Any], ref: str) -> dict[str, Any] | None:
    row = _optional(read_json, shard_path(ref))
    if isinstance(row, Mapping) and row.get("schema") == "jianghu-scene-history-shard-1.0":
        return copy.deepcopy(dict(row))
    return None


def append_attributed_speech(read_json: Callable[[str], Any], *, row: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    head = history_head(read_json)
    recent = [copy.deepcopy(dict(x)) for x in head.get("recent", []) if isinstance(x, Mapping)]
    speech_ref = str(row.get("speech_ref") or "")
    if any(x.get("speech_ref") == speech_ref for x in recent):
        return {}
    latest = head.get("latest_shard_ref") if isinstance(head.get("latest_shard_ref"), str) else None
    period = _period(str(row.get("at") or ""))
    if latest and latest.startswith(f"scene_history_{period}_part_"):
        current_ref = latest
        shard = history_shard(read_json, current_ref)
    else:
        current_ref = shard_ref_for(str(row.get("at") or ""), 1)
        shard = None
    if shard is None:
        shard = {
            "schema": "jianghu-scene-history-shard-1.0", "authority": False,
            "mechanical_consequence_authority": False, "shard_ref": current_ref,
            "previous_shard_ref": latest if latest != current_ref else None, "records": [],
        }
    records = [copy.deepcopy(dict(x)) for x in shard.get("records", []) if isinstance(x, Mapping)]
    if any(str(x.get("speech_ref") or "") == speech_ref for x in records):
        return {}
    if len(records) >= HISTORY_SHARD_LIMIT:
        match = re.search(r"_part_(\d+)$", current_ref)
        next_part = int(match.group(1)) + 1 if match else 2
        previous = current_ref; current_ref = shard_ref_for(str(row.get("at") or ""), next_part)
        shard = {
            "schema": "jianghu-scene-history-shard-1.0", "authority": False,
            "mechanical_consequence_authority": False, "shard_ref": current_ref,
            "previous_shard_ref": previous, "records": [],
        }
        records = []
    records.append(copy.deepcopy(dict(row))); shard["records"] = records
    recent.append(copy.deepcopy(dict(row))); head["recent"] = recent[-HISTORY_HEAD_LIMIT:]
    head["total_recorded"] = int(head.get("total_recorded", 0)) + 1
    head["latest_shard_ref"] = current_ref
    return {HISTORY_HEAD_PATH: head, shard_path(current_ref): shard}


def inspect_history_object(read_json: Callable[[str], Any], object_ref: str) -> dict[str, Any] | None:
    if object_ref == "scene_history_head": return history_head(read_json)
    if object_ref.startswith("scene_history_"): return history_shard(read_json, object_ref)
    return None


__all__ = [
    "SESSION_PATH", "ATTEMPT_LEDGER_PATH", "HISTORY_HEAD_PATH", "SESSION_KINDS", "SPEECH_KINDS",
    "CLOSE_REASONS", "INTERACTION_ACTIONS", "active_scene_session", "session_projection",
    "new_session_record", "close_session_record", "close_active_session_writes", "interaction_ledger", "trim_interaction_ledger",
    "abandon_session_questions", "resolve_question", "recent_scene_history", "append_attributed_speech",
    "inspect_history_object", "safe_ref", "bounded_text",
]
