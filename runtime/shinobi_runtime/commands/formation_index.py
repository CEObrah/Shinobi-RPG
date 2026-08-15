"""Deterministic formation-ID routing projection."""
from __future__ import annotations

import copy
import json
from typing import Any, Mapping

FORMATION_INDEX_PATH = "state/formation/index.json"


def validate_formation_index(record: Mapping[str, Any]) -> None:
    if (
        not isinstance(record, Mapping)
        or record.get("schema") != "formation-registry-index"
        or record.get("authority") is not False
        or not isinstance(record.get("registries"), Mapping)
        or not isinstance(record.get("formation_routes"), Mapping)
    ):
        raise ValueError("formation_registry_index_invalid")
    registries = record["registries"]
    for force_ref, path in registries.items():
        if not isinstance(force_ref, str) or not force_ref or not isinstance(path, str) or not path:
            raise ValueError("formation_registry_index_invalid")
    for formation_ref, route in record["formation_routes"].items():
        if (
            not isinstance(formation_ref, str)
            or not formation_ref.startswith("formation.")
            or not isinstance(route, Mapping)
            or set(route) != {"force_ref", "registry_path"}
            or not isinstance(route.get("force_ref"), str)
            or not isinstance(route.get("registry_path"), str)
            or registries.get(route["force_ref"]) != route["registry_path"]
        ):
            raise ValueError("formation_registry_index_invalid")


def build_routes(repository: Any, base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if base is None:
        base = repository.read_json(FORMATION_INDEX_PATH)
    record = copy.deepcopy(dict(base))
    if record.get("schema") != "formation-registry-index" or record.get("authority") is not False:
        raise ValueError("formation_registry_index_invalid")
    registries = record.get("registries")
    if not isinstance(registries, Mapping):
        raise ValueError("formation_registry_index_invalid")
    routes: dict[str, Any] = {}
    for force_ref, path in sorted(registries.items()):
        if not isinstance(force_ref, str) or not isinstance(path, str):
            raise ValueError("formation_registry_index_invalid")
        registry = repository.read_json(path)
        formations = registry.get("formations") if isinstance(registry, Mapping) else None
        if registry.get("schema") != "formation-registry" or registry.get("force_ref") != force_ref or not isinstance(formations, list):
            raise ValueError("formation_registry_invalid")
        for row in formations:
            formation_ref = row.get("id") if isinstance(row, Mapping) else None
            if not isinstance(formation_ref, str) or not formation_ref.startswith("formation.") or row.get("force_ref") != force_ref:
                raise ValueError("formation_registry_invalid")
            if formation_ref in routes:
                raise ValueError("formation_route_conflict")
            routes[formation_ref] = {"force_ref": force_ref, "registry_path": path}
    record["formation_routes"] = dict(sorted(routes.items()))
    validate_formation_index(record)
    return record


def reconcile_formation_writes(repository: Any, writes: Mapping[str, bytes]) -> dict[str, Any] | None:
    changed_paths = sorted(
        path for path in writes
        if path.startswith("state/formation/")
        and path.endswith(".json")
        and path != FORMATION_INDEX_PATH
    )
    if not changed_paths:
        return None
    try:
        raw = writes.get(FORMATION_INDEX_PATH)
        if raw is not None:
            index = json.loads(raw.decode("utf-8"))
        else:
            index = repository.read_json(FORMATION_INDEX_PATH)
    except (UnicodeDecodeError, json.JSONDecodeError, FileNotFoundError, ValueError) as exc:
        raise ValueError("formation_registry_index_invalid") from exc
    index = copy.deepcopy(dict(index))
    if "formation_routes" not in index:
        index = build_routes(repository, index)
    validate_formation_index(index)
    routes = dict(index["formation_routes"])
    registries = index["registries"]
    for path in changed_paths:
        force_refs = [force_ref for force_ref, registered in registries.items() if registered == path]
        if len(force_refs) != 1:
            raise ValueError("formation_registry_index_invalid")
        force_ref = force_refs[0]
        routes = {
            formation_ref: route
            for formation_ref, route in routes.items()
            if route.get("registry_path") != path
        }
        try:
            registry = json.loads(writes[path].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("formation_registry_invalid") from exc
        formations = registry.get("formations") if isinstance(registry, Mapping) else None
        if registry.get("schema") != "formation-registry" or registry.get("force_ref") != force_ref or not isinstance(formations, list):
            raise ValueError("formation_registry_invalid")
        for row in formations:
            formation_ref = row.get("id") if isinstance(row, Mapping) else None
            if not isinstance(formation_ref, str) or not formation_ref.startswith("formation.") or row.get("force_ref") != force_ref:
                raise ValueError("formation_registry_invalid")
            existing = routes.get(formation_ref)
            if existing is not None:
                raise ValueError("formation_route_conflict")
            routes[formation_ref] = {"force_ref": force_ref, "registry_path": path}
    index["formation_routes"] = dict(sorted(routes.items()))
    validate_formation_index(index)
    return index


__all__ = ["FORMATION_INDEX_PATH", "validate_formation_index", "build_routes", "reconcile_formation_writes"]
