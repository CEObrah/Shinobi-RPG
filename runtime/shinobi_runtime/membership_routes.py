"""Deterministic derived routing for exact-team and House memberships.

The exact team/House owners remain authority. These shards are authority:false
indexes that let hot reads answer "which teams contain this person?", "which
teams belong to this parent?", "which teams contain service members from this
village?", and "which Houses contain this person?" without scanning global
registries or every House owner.
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Sequence

SCHEMA = "membership-route-shard"
INDEX_SCHEMA = "membership-route-index"
INDEX_PATH = "state/reg/membership-routes.json"
ROOT = "state/reg/membership-routes"
_BUCKET_HEX = 2
_ROUTE_MAPS = {
    "team_member": "team_member_routes",
    "team_parent": "team_parent_routes",
    "team_service": "team_service_routes",
    "team_assignment": "team_assignment_routes",
    "house_member": "house_member_routes",
}


def _bucket(kind: str, key: str) -> str:
    if kind not in _ROUTE_MAPS or not isinstance(key, str) or not key:
        raise ValueError("membership route key invalid")
    return hashlib.sha256(f"{kind}\x00{key}".encode("utf-8")).hexdigest()[:_BUCKET_HEX]


def route_path(kind: str, key: str) -> str:
    return f"{ROOT}/{_bucket(kind, key)}.json"


def _blank(bucket: str) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "bucket": bucket,
        "authority": False,
        "team_member_routes": {},
        "team_parent_routes": {},
        "team_service_routes": {},
        "team_assignment_routes": {},
        "house_member_routes": {},
    }


def _validate(record: Mapping[str, Any], bucket: str) -> Dict[str, Any]:
    if (
        record.get("schema") != SCHEMA
        or record.get("bucket") != bucket
        or record.get("authority") is not False
    ):
        raise ValueError("membership route shard invalid")
    result = copy.deepcopy(dict(record))
    for map_name in _ROUTE_MAPS.values():
        mapping = result.get(map_name)
        if not isinstance(mapping, dict):
            raise ValueError("membership route shard invalid")
        for key, refs in mapping.items():
            if (
                not isinstance(key, str)
                or not key
                or not isinstance(refs, list)
                or any(not isinstance(ref, str) or not ref for ref in refs)
                or refs != sorted(set(refs))
            ):
                raise ValueError("membership route shard invalid")
    return result


def _validate_index(record: Mapping[str, Any]) -> None:
    if record.get("schema") != INDEX_SCHEMA or record.get("authority") is not False or record.get("storage_version") != 1:
        raise ValueError("membership route index invalid")


def _read(reader: Any, kind: str, key: str) -> Dict[str, Any]:
    index_raw = reader.read_optional_bytes(INDEX_PATH)
    if index_raw is None:
        raise ValueError("membership route index missing")
    index = reader.read_json(INDEX_PATH)
    if not isinstance(index, Mapping):
        raise ValueError("membership route index invalid")
    _validate_index(index)
    bucket = _bucket(kind, key)
    path = f"{ROOT}/{bucket}.json"
    raw = reader.read_optional_bytes(path)
    if raw is None:
        return _blank(bucket)
    record = reader.read_json(path)
    if not isinstance(record, Mapping):
        raise ValueError("membership route shard invalid")
    return _validate(record, bucket)


def refs_for(reader: Any, kind: str, key: str) -> tuple[str, ...]:
    record = _read(reader, kind, key)
    refs = record[_ROUTE_MAPS[kind]].get(key, [])
    if not isinstance(refs, list):
        raise ValueError("membership route shard invalid")
    return tuple(refs)


def team_refs_for_member(reader: Any, person_ref: str) -> tuple[str, ...]:
    return refs_for(reader, "team_member", person_ref)


def team_refs_for_parent(reader: Any, parent_ref: str) -> tuple[str, ...]:
    return refs_for(reader, "team_parent", parent_ref)


def team_refs_for_service(reader: Any, service_village: str) -> tuple[str, ...]:
    return refs_for(reader, "team_service", service_village)


def team_refs_for_assignment(reader: Any, assignment_ref: str) -> tuple[str, ...]:
    return refs_for(reader, "team_assignment", assignment_ref)


def house_refs_for_member(reader: Any, person_ref: str) -> tuple[str, ...]:
    return refs_for(reader, "house_member", person_ref)


def _staged(
    reader: Any,
    writes: MutableMapping[str, Dict[str, Any]],
    *,
    kind: str,
    key: str,
) -> tuple[str, Dict[str, Any]]:
    if INDEX_PATH not in writes:
        raw = reader.read_optional_bytes(INDEX_PATH)
        if raw is None:
            writes[INDEX_PATH] = {"schema": INDEX_SCHEMA, "authority": False, "storage_version": 1}
        else:
            index = reader.read_json(INDEX_PATH)
            if not isinstance(index, Mapping):
                raise ValueError("membership route index invalid")
            _validate_index(index)
    bucket = _bucket(kind, key)
    path = f"{ROOT}/{bucket}.json"
    existing = writes.get(path)
    if existing is not None:
        return path, _validate(existing, bucket)
    raw = reader.read_optional_bytes(path)
    if raw is None:
        record = _blank(bucket)
    else:
        loaded = reader.read_json(path)
        if not isinstance(loaded, Mapping):
            raise ValueError("membership route shard invalid")
        record = _validate(loaded, bucket)
    writes[path] = record
    return path, record


def stage_route_change(
    reader: Any,
    writes: MutableMapping[str, Dict[str, Any]],
    *,
    kind: str,
    key: str,
    add_refs: Iterable[str] = (),
    remove_refs: Iterable[str] = (),
) -> str:
    path, record = _staged(reader, writes, kind=kind, key=key)
    mapping = record[_ROUTE_MAPS[kind]]
    current = set(mapping.get(key, []))
    current.difference_update(ref for ref in remove_refs if isinstance(ref, str) and ref)
    current.update(ref for ref in add_refs if isinstance(ref, str) and ref)
    if current:
        mapping[key] = sorted(current)
    else:
        mapping.pop(key, None)
    writes[path] = record
    return path


def stage_team_change(
    reader: Any,
    writes: MutableMapping[str, Dict[str, Any]],
    *,
    team_ref: str,
    before_members: Sequence[str] = (),
    after_members: Sequence[str] = (),
    before_parent: Optional[str] = None,
    after_parent: Optional[str] = None,
    before_services: Sequence[str] = (),
    after_services: Sequence[str] = (),
    before_assignment: Optional[str] = None,
    after_assignment: Optional[str] = None,
) -> tuple[str, ...]:
    touched: set[str] = set()
    before_member_set = {ref for ref in before_members if isinstance(ref, str) and ref}
    after_member_set = {ref for ref in after_members if isinstance(ref, str) and ref}
    for person_ref in sorted(before_member_set | after_member_set):
        touched.add(stage_route_change(
            reader, writes, kind="team_member", key=person_ref,
            add_refs=(team_ref,) if person_ref in after_member_set else (),
            remove_refs=(team_ref,) if person_ref not in after_member_set else (),
        ))
    parent_keys = {ref for ref in (before_parent, after_parent) if isinstance(ref, str) and ref}
    for parent_ref in sorted(parent_keys):
        touched.add(stage_route_change(
            reader, writes, kind="team_parent", key=parent_ref,
            add_refs=(team_ref,) if parent_ref == after_parent else (),
            remove_refs=(team_ref,) if parent_ref != after_parent else (),
        ))
    before_service_set = {ref for ref in before_services if isinstance(ref, str) and ref}
    after_service_set = {ref for ref in after_services if isinstance(ref, str) and ref}
    for service_ref in sorted(before_service_set | after_service_set):
        touched.add(stage_route_change(
            reader, writes, kind="team_service", key=service_ref,
            add_refs=(team_ref,) if service_ref in after_service_set else (),
            remove_refs=(team_ref,) if service_ref not in after_service_set else (),
        ))
    assignment_keys = {ref for ref in (before_assignment, after_assignment) if isinstance(ref, str) and ref}
    for assignment_ref in sorted(assignment_keys):
        touched.add(stage_route_change(
            reader, writes, kind="team_assignment", key=assignment_ref,
            add_refs=(team_ref,) if assignment_ref == after_assignment else (),
            remove_refs=(team_ref,) if assignment_ref != after_assignment else (),
        ))
    return tuple(sorted(touched))


def stage_house_change(
    reader: Any,
    writes: MutableMapping[str, Dict[str, Any]],
    *,
    house_ref: str,
    before_members: Sequence[str] = (),
    after_members: Sequence[str] = (),
) -> tuple[str, ...]:
    touched: set[str] = set()
    before_set = {ref for ref in before_members if isinstance(ref, str) and ref}
    after_set = {ref for ref in after_members if isinstance(ref, str) and ref}
    for person_ref in sorted(before_set | after_set):
        touched.add(stage_route_change(
            reader, writes, kind="house_member", key=person_ref,
            add_refs=(house_ref,) if person_ref in after_set else (),
            remove_refs=(house_ref,) if person_ref not in after_set else (),
        ))
    return tuple(sorted(touched))
