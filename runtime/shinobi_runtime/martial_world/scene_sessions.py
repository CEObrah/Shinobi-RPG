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
SCENE_FACT_KINDS = frozenset({
    "local_action", "object_state", "positioning", "visible_reaction",
    "shared_premise", "incidental_detail",
})
IMPROVISED_PROP_FORMS = frozenset({"small_rigid", "short_rigid", "long_rigid", "heavy_rigid", "sharp_fragment"})
IMPROVISED_PROP_MATERIALS = frozenset({"wood", "bamboo", "ceramic", "stone", "metal", "bone"})
IMPROVISED_PROP_CONDITIONS = frozenset({"intact", "worn", "cracked", "broken_piece"})
CLOSE_REASONS = frozenset({
    "completed", "player_left", "hard_interruption", "skipped_to_conclusion",
    "superseded", "cancelled",
})
INTERACTION_ACTIONS = frozenset({
    "present", "request", "petition", "report", "ask", "offer", "decline", "comply",
    "withdraw", "proceed", "seek_contact", "speak",
})
RESPONSE_BEARING_ACTIONS = frozenset({"ask", "request", "petition", "offer", "present", "report", "speak"})
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")


def _expects_response(action: str, explicit: object) -> bool:
    """Apply a human-friendly default while honoring an explicit caller choice."""
    if isinstance(explicit, bool):
        return explicit
    return action in RESPONSE_BEARING_ACTIONS


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
    threads = [str(x) for x in row.get("open_thread_refs", row.get("open_question_refs", [])) if isinstance(x, str)]
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
        "open_thread_refs": threads[-16:],
        "open_thread_count": len(threads),
        "open_threads_truncated": len(threads) > 16,
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
        "open_thread_refs": [],
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
    action = str(out.get("action") or "")
    has_statement = isinstance(out.get("player_statement"), str) and bool(out.get("player_statement"))
    expects_response = _expects_response(action, out.get("expects_response"))
    out.setdefault("expects_response", expects_response if has_statement else False)
    out.setdefault("thread_kind", "question" if action == "ask" else ("conversation" if expects_response and has_statement else None))
    out.setdefault("thread_status", "open" if expects_response and has_statement and out.get("scene_session_ref") else "not_applicable")
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


def abandon_session_threads(ledger: Mapping[str, Any], *, session_ref: str, at: str) -> tuple[dict[str, Any], int]:
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


def abandon_session_questions(ledger: Mapping[str, Any], *, session_ref: str, at: str) -> tuple[dict[str, Any], int]:
    # Compatibility alias: questions are now one subtype of generic response-bearing threads.
    return abandon_session_threads(ledger, session_ref=session_ref, at=at)

def resolve_thread(ledger: Mapping[str, Any], *, thread_ref: str, response_ref: str, at: str) -> tuple[dict[str, Any], bool]:
    out = copy.deepcopy(dict(ledger)); changed = False; rows = []
    for raw in out.get("attempts", []):
        if not isinstance(raw, Mapping): continue
        row = normalized_attempt(raw)
        if row.get("attempt_ref") == thread_ref and row.get("thread_status") == "open":
            row["thread_status"] = "answered" if row.get("thread_kind") == "question" else "responded"
            row["resolved_at"] = str(at); row["response_ref"] = response_ref; changed = True
        rows.append(row)
    out["attempts"] = rows
    return trim_interaction_ledger(out), changed

def resolve_question(ledger: Mapping[str, Any], *, question_ref: str, response_ref: str, at: str) -> tuple[dict[str, Any], bool]:
    return resolve_thread(ledger, thread_ref=question_ref, response_ref=response_ref, at=at)


def active_scene_thread_page(
    read_json: Callable[[str], Any], *, cursor: str | None = None, limit: int = 16,
) -> dict[str, Any]:
    """Page every unresolved player-authored thread in the exact active scene."""
    try:
        offset = 0 if cursor in (None, "") else int(str(cursor))
    except ValueError as exc:
        raise ValueError("jianghu_scene_thread_cursor_invalid") from exc
    if offset < 0 or isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 64:
        raise ValueError("jianghu_scene_thread_page_invalid")
    session = active_scene_session(read_json)
    if not isinstance(session, Mapping):
        raise ValueError("jianghu_scene_thread_session_unavailable")
    session_ref = str(session.get("session_ref") or "")
    refs = [
        str(ref) for ref in session.get("open_thread_refs", session.get("open_question_refs", []))
        if isinstance(ref, str) and ref
    ]
    page_refs = refs[offset:offset + limit]
    wanted = set(page_refs)
    rows_by_ref: dict[str, dict[str, Any]] = {}
    ledger = interaction_ledger(read_json)
    for raw in ledger.get("attempts", []):
        if not isinstance(raw, Mapping):
            continue
        row = normalized_attempt(raw)
        ref = str(row.get("attempt_ref") or "")
        if ref not in wanted:
            continue
        if row.get("thread_status", "open") == "open" and row.get("scene_session_ref") == session_ref:
            rows_by_ref[ref] = row
    threads = [rows_by_ref[ref] for ref in page_refs if ref in rows_by_ref]
    next_offset = offset + len(page_refs)
    return {
        "session_ref": session_ref,
        "cursor": str(offset),
        "count": len(refs),
        "returned": len(threads),
        "truncated": next_offset < len(refs),
        "next_cursor": str(next_offset) if next_offset < len(refs) else None,
        "threads": threads,
        "authority": False,
        "mechanical_consequence_authority": False,
    }


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


def normalize_improvised_prop(value: object) -> dict[str, str] | None:
    """Validate one bounded mundane-prop classification.

    The caller supplies no mass, reach, damage, value, or other mechanical
    result.  Those are derived only if a later mechanical resolver lawfully
    promotes this already-established scene object.
    """
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) - {"form", "material", "condition"}:
        raise ValueError("jianghu_scene_improvised_prop_invalid")
    form = value.get("form")
    material = value.get("material")
    condition = value.get("condition", "intact")
    if form not in IMPROVISED_PROP_FORMS:
        raise ValueError("jianghu_scene_improvised_prop_form_unsupported")
    if material not in IMPROVISED_PROP_MATERIALS:
        raise ValueError("jianghu_scene_improvised_prop_material_unsupported")
    if condition not in IMPROVISED_PROP_CONDITIONS:
        raise ValueError("jianghu_scene_improvised_prop_condition_unsupported")
    return {
        "kind": "mundane_improvised_prop",
        "form": str(form),
        "material": str(material),
        "condition": str(condition),
    }


def scene_history_record(
    read_json: Callable[[str], Any], record_ref: str, *, session_ref: str | None = None, max_shards: int = 8
) -> dict[str, Any] | None:
    """Resolve one exact recent scene-history record without a repository scan."""
    record_ref = safe_ref(record_ref, "jianghu_scene_history_record_ref_invalid")
    wanted_session = safe_ref(session_ref, "jianghu_scene_ref_invalid") if session_ref is not None else None
    head = history_head(read_json)
    recent = head.get("recent", []) if isinstance(head.get("recent"), list) else []
    for row in reversed(recent):
        if not isinstance(row, Mapping):
            continue
        try:
            ref = _history_record_ref(row)
        except ValueError:
            continue
        if ref == record_ref and (wanted_session is None or str(row.get("session_ref")) == wanted_session):
            return copy.deepcopy(dict(row))
    shard_ref = head.get("latest_shard_ref") if isinstance(head.get("latest_shard_ref"), str) else None
    seen: set[str] = set()
    for _ in range(max(1, min(int(max_shards), 32))):
        if not shard_ref or shard_ref in seen:
            break
        seen.add(shard_ref)
        shard = history_shard(read_json, shard_ref)
        if not isinstance(shard, Mapping):
            break
        records = shard.get("records", []) if isinstance(shard.get("records"), list) else []
        for row in reversed(records):
            if not isinstance(row, Mapping):
                continue
            try:
                ref = _history_record_ref(row)
            except ValueError:
                continue
            if ref == record_ref and (wanted_session is None or str(row.get("session_ref")) == wanted_session):
                return copy.deepcopy(dict(row))
        shard_ref = shard.get("previous_shard_ref") if isinstance(shard.get("previous_shard_ref"), str) else None
    return None


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


def _history_record_ref(row: Mapping[str, Any]) -> str:
    for key in ("speech_ref", "fact_ref"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    raise ValueError("jianghu_scene_history_record_ref_invalid")


def append_scene_history_record(read_json: Callable[[str], Any], *, row: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Append attributed speech or a reversible scene fact to bounded history."""
    head = history_head(read_json)
    recent = [copy.deepcopy(dict(x)) for x in head.get("recent", []) if isinstance(x, Mapping)]
    record_ref = _history_record_ref(row)
    if any(_history_record_ref(x) == record_ref for x in recent if isinstance(x, Mapping) and (x.get("speech_ref") or x.get("fact_ref"))):
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
    if any(_history_record_ref(x) == record_ref for x in records if isinstance(x, Mapping) and (x.get("speech_ref") or x.get("fact_ref"))):
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


def append_attributed_speech(read_json: Callable[[str], Any], *, row: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    # Compatibility wrapper retained for existing callers.
    if not isinstance(row.get("speech_ref"), str):
        raise ValueError("jianghu_scene_speech_ref_invalid")
    return append_scene_history_record(read_json, row=row)


def inspect_history_object(read_json: Callable[[str], Any], object_ref: str) -> dict[str, Any] | None:
    if object_ref == "scene_history_head": return history_head(read_json)
    if object_ref.startswith("scene_history_"): return history_shard(read_json, object_ref)
    return None


__all__ = [
    "SESSION_PATH", "ATTEMPT_LEDGER_PATH", "HISTORY_HEAD_PATH", "SESSION_KINDS", "SPEECH_KINDS", "SCENE_FACT_KINDS",
    "CLOSE_REASONS", "INTERACTION_ACTIONS", "RESPONSE_BEARING_ACTIONS", "active_scene_session", "session_projection",
    "new_session_record", "close_session_record", "close_active_session_writes", "interaction_ledger", "trim_interaction_ledger",
    "abandon_session_questions", "abandon_session_threads", "resolve_question", "resolve_thread", "active_scene_thread_page", "recent_scene_history", "scene_history_record", "normalize_improvised_prop", "append_attributed_speech", "append_scene_history_record",
    "inspect_history_object", "safe_ref", "bounded_text",
]
