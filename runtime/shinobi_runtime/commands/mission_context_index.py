"""Bounded current-mission routing projection.

Exact mission owners remain authority under state/mission/mission.*.json.  This
index contains only current participant routing and briefing cues, so historical
mission cardinality never affects ordinary play-context retrieval.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from shinobi_runtime.commands.mission_owner import MissionOwner

MISSION_CONTEXT_INDEX_PATH = "state/mission/context-index.json"
MISSION_CONTEXT_SCHEMA = "mission-context-index"
CURRENT_MISSION_STATES = frozenset(("offered", "accepted", "active", "resolving"))


def blank_mission_context_index() -> dict[str, Any]:
    return {
        "schema": MISSION_CONTEXT_SCHEMA,
        "authority": False,
        "current_by_participant": {},
        "briefing_by_participant": {},
        "current_mission_count": 0,
    }


def validate_mission_context_index(record: Mapping[str, Any]) -> None:
    if (
        not isinstance(record, Mapping)
        or record.get("schema") != MISSION_CONTEXT_SCHEMA
        or record.get("authority") is not False
    ):
        raise ValueError("mission_context_index_invalid")
    for field in ("current_by_participant", "briefing_by_participant"):
        mapping = record.get(field)
        if not isinstance(mapping, Mapping):
            raise ValueError("mission_context_index_invalid")
        for participant, refs in mapping.items():
            if (
                not isinstance(participant, str)
                or not participant
                or not isinstance(refs, list)
                or any(
                    not isinstance(ref, str) or not ref.startswith("mission.")
                    for ref in refs
                )
                or refs != sorted(set(refs))
            ):
                raise ValueError("mission_context_index_invalid")
    count = record.get("current_mission_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("mission_context_index_invalid")
    unique = {
        ref
        for refs in record["current_by_participant"].values()
        for ref in refs
    }
    if count != len(unique):
        raise ValueError("mission_context_index_invalid")
    briefing = record["briefing_by_participant"]
    current = record["current_by_participant"]
    for participant, refs in briefing.items():
        if not set(refs).issubset(set(current.get(participant, []))):
            raise ValueError("mission_context_index_invalid")


def _remove(index: dict[str, Any], participant: str, mission_id: str) -> None:
    for field in ("current_by_participant", "briefing_by_participant"):
        mapping = index[field]
        refs = mapping.get(participant)
        if not isinstance(refs, list):
            continue
        mapping[participant] = [ref for ref in refs if ref != mission_id]
        if not mapping[participant]:
            mapping.pop(participant, None)


def _add(mapping: dict[str, Any], participant: str, mission_id: str) -> None:
    refs = mapping.setdefault(participant, [])
    if mission_id not in refs:
        refs.append(mission_id)
        refs.sort()


def apply_owner(index: dict[str, Any], owner: MissionOwner, *, prior: MissionOwner | None = None) -> None:
    mission_id = owner.mission_id
    participants = set(owner.mission.participant_refs)
    if prior is not None:
        participants.update(prior.mission.participant_refs)
    for participant in participants:
        _remove(index, participant, mission_id)
    if owner.mission.state in CURRENT_MISSION_STATES:
        for participant in owner.mission.participant_refs:
            _add(index["current_by_participant"], participant, mission_id)
            if owner.briefing is not None:
                _add(index["briefing_by_participant"], participant, mission_id)
    index["current_mission_count"] = len(
        {
            ref
            for refs in index["current_by_participant"].values()
            for ref in refs
        }
    )


def build_from_repository(repository: Any) -> dict[str, Any]:
    index = blank_mission_context_index()
    directory = repository.resolve("state/mission")
    if directory.is_dir():
        for path in sorted(directory.glob("mission.*.json")):
            relative = path.relative_to(repository.root).as_posix()
            owner = MissionOwner.from_record(repository.read_json(relative))
            apply_owner(index, owner)
    validate_mission_context_index(index)
    return index


def load_or_build(repository: Any) -> dict[str, Any]:
    raw = repository.read_optional_bytes(MISSION_CONTEXT_INDEX_PATH)
    if raw is None:
        return build_from_repository(repository)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("mission_context_index_invalid") from exc
    validate_mission_context_index(value)
    return copy.deepcopy(value)


def reconcile_mission_writes(repository: Any, writes: Mapping[str, bytes]) -> dict[str, Any] | None:
    mission_paths = sorted(
        path
        for path in writes
        if path.startswith("state/mission/mission.") and path.endswith(".json")
    )
    if not mission_paths:
        return None
    index = load_or_build(repository)
    for path in mission_paths:
        try:
            after_record = json.loads(writes[path].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("mission_context_index_invalid") from exc
        after = MissionOwner.from_record(after_record)
        prior = None
        raw_prior = repository.read_optional_bytes(path)
        if raw_prior is not None:
            try:
                prior = MissionOwner.from_record(json.loads(raw_prior.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError("mission_context_index_invalid") from exc
        apply_owner(index, after, prior=prior)
    validate_mission_context_index(index)
    return index


def participant_current_refs(record: Mapping[str, Any], participant: str) -> tuple[str, ...]:
    validate_mission_context_index(record)
    refs = record["current_by_participant"].get(participant, [])
    return tuple(refs)


def participant_briefing_refs(record: Mapping[str, Any], participant: str) -> tuple[str, ...]:
    validate_mission_context_index(record)
    refs = record["briefing_by_participant"].get(participant, [])
    return tuple(refs)


__all__ = [
    "MISSION_CONTEXT_INDEX_PATH",
    "MISSION_CONTEXT_SCHEMA",
    "CURRENT_MISSION_STATES",
    "blank_mission_context_index",
    "validate_mission_context_index",
    "build_from_repository",
    "load_or_build",
    "reconcile_mission_writes",
    "participant_current_refs",
    "participant_briefing_refs",
]
