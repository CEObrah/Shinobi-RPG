#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

try:
    import jsonschema
except ImportError:
    jsonschema = None

from test_templates import validate_doc


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "state/canon/pressures.json"
SCHEMA_PATH = ROOT / "game/schemas/canon-pressure-registry.schema.json"
TEMPLATE_PATH = ROOT / "runtime/contracts/templates/canon-pressure-registry.template.json"
SCHEDULER_PATH = ROOT / "state/time/causal-scheduler.json"
META_PATH = ROOT / "state/meta.json"
REDUCER_REF = "runtime/contracts/system-contracts/canon_pressures.json"
ACTIVE_STATUSES = {"active", "active_hidden", "latent_active"}
TIME_RE = re.compile(r"SE-(-?\d+)-(\d+)-(\d+)T(\d+):(\d+):(\d+)")

# Immutable migrated identity and conditional stakes. Status and executable state
# may change through play, but only with append-only chronology and causal sources.
ORIGIN = {
    "pressure_konoha_academy_graduation": {
        "title": "Konoha Academy graduation preparation",
        "host_ref": "faction_konoha",
        "status": "active",
        "stakes": {"ordinary graduation", "security incident", "Mizuki plot divergence"},
        "visibility": None,
    },
    "pressure_mizuki_forbidden_scroll": {
        "title": "Mizuki forbidden-scroll conspiracy",
        "host_ref": "canon_mizuki",
        "status": "latent_active",
        "stakes": {"attempt proceeds", "detected early", "aborted", "transformed"},
        "visibility": None,
    },
    "pressure_wave_gato_bridge": {
        "title": "Gato monopoly and Wave bridge conflict",
        "host_ref": "canon_gato",
        "status": "active",
        "stakes": {"bridge suppression", "external shinobi contract", "local resistance"},
        "visibility": None,
    },
    "pressure_oto_konoha_infiltration": {
        "title": "Oto infiltration preparation",
        "host_ref": "canon_orochimaru",
        "status": "active_hidden",
        "stakes": {"continued preparation", "counterintelligence detection", "plan revision"},
        "visibility": "hidden",
    },
    "pressure_suna_funding": {
        "title": "Suna mission-income and daimyo funding crisis",
        "host_ref": "faction_suna",
        "status": "active",
        "stakes": {"austerity", "external alliance", "mission competition"},
        "visibility": None,
    },
    "pressure_kiri_transition": {
        "title": "Kiri internal reform and violent institutional remnants",
        "host_ref": "faction_kiri",
        "status": "active",
        "stakes": {"reform gains", "purge backlash", "regional autonomy"},
        "visibility": None,
    },
    "pressure_akatsuki_intelligence": {
        "title": "Akatsuki long-horizon intelligence collection",
        "host_ref": "canon_nagato",
        "status": "active_hidden",
        "stakes": {"new intelligence", "recruitment", "target reprioritization"},
        "visibility": "hidden",
    },
    "pressure_great_village_mission_market": {
        "title": "Great-village competition for missions and influence",
        "host_ref": "world_mission_market",
        "status": "active",
        "stakes": {"contract shifts", "diplomatic pressure", "border incidents"},
        "visibility": None,
    },
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_time(value: str | None) -> int | None:
    if not isinstance(value, str):
        return None
    match = TIME_RE.fullmatch(value)
    if not match:
        return None
    year, month, day, hour, minute, second = map(int, match.groups())
    return (((((year * 372) + ((month - 1) * 31) + day - 1) * 24 + hour) * 60 + minute) * 60 + second)


def scheduler_hosts(scheduler: dict) -> dict[str, dict]:
    hosts = scheduler.get("hosts", {})
    return hosts if isinstance(hosts, dict) else {}


def structural_errors(registry: dict) -> list[str]:
    errors = []
    if jsonschema is not None:
        schema = read_json(SCHEMA_PATH)
        errors.extend(
            f"schema:{error.message}"
            for error in jsonschema.Draft202012Validator(schema).iter_errors(registry)
        )
    validate_doc("canon-pressure-registry", registry, read_json(TEMPLATE_PATH), errors)
    return errors


def semantic_errors(registry: dict, scheduler: dict, meta: dict) -> list[str]:
    errors = []
    if registry.get("schema") != "canon-pressure-registry":
        errors.append("registry_schema")
    if registry.get("owner_id") != "world_world_pressures":
        errors.append("registry_owner_id")
    if registry.get("authority") is not True:
        errors.append("registry_not_authority")
    if registry.get("canon_rule") != "canon is trajectory, not forced result":
        errors.append("canon_rule_drift")
    if meta.get("time") != scheduler.get("world_time"):
        errors.append(f"meta_scheduler_time_drift:{meta.get('time')}:{scheduler.get('world_time')}")

    world_key = parse_time(scheduler.get("world_time"))
    hosts = scheduler_hosts(scheduler)

    pressures = registry.get("pressures", {})
    missing_origins = sorted(set(ORIGIN) - set(pressures))
    if missing_origins:
        errors.append(f"migrated_front_missing:{missing_origins}")

    for pressure_id, front in pressures.items():
        if front.get("id") != pressure_id:
            errors.append(f"front_key_id_mismatch:{pressure_id}:{front.get('id')}")
        origin = ORIGIN.get(pressure_id)
        if origin:
            if front.get("title") != origin["title"]:
                errors.append(f"migrated_title_drift:{pressure_id}")
            if front.get("host_ref") != origin["host_ref"]:
                errors.append(f"migrated_host_drift:{pressure_id}")
            if not origin["stakes"].issubset(set(front.get("stakes", []))):
                errors.append(f"migrated_stakes_lost:{pressure_id}")
            classification = (front.get("visibility") or {}).get("classification")
            basis_refs = (front.get("visibility") or {}).get("basis_refs", [])
            if classification != origin["visibility"] and not basis_refs:
                errors.append(f"visibility_changed_without_basis:{pressure_id}")

        if (front.get("constraints") or {}).get("canon_forcing") is not False:
            errors.append(f"canon_forcing_front:{pressure_id}")

        executable_detail = any(
            (
                front.get("goal"),
                front.get("current_step"),
                front.get("actors"),
                front.get("resources"),
                front.get("opposition"),
                (front.get("constraints") or {}).get("refs"),
            )
        )
        if executable_detail and not (front.get("source_refs") or front.get("evidence_refs")):
            errors.append(f"unsupported_executable_detail:{pressure_id}")

        for knowledge_kind in ("player_refs", "npc_refs"):
            for ref in (front.get("knowledge") or {}).get(knowledge_kind, []):
                rel = str(ref).split("#", 1)[0]
                if not rel.startswith("state/") or not (ROOT / rel).exists():
                    errors.append(f"unresolved_knowledge_ref:{pressure_id}:{knowledge_kind}:{ref}")

        chronology = front.get("chronology", [])
        seen_entries = set()
        prior_time = None
        prior_status = origin["status"] if origin else None
        for entry in chronology:
            entry_id = entry.get("entry_id")
            if entry_id in seen_entries:
                errors.append(f"duplicate_chronology_entry:{pressure_id}:{entry_id}")
            seen_entries.add(entry_id)
            at_key = parse_time(entry.get("at"))
            if at_key is None:
                errors.append(f"bad_chronology_time:{pressure_id}:{entry_id}")
            elif prior_time is not None and at_key < prior_time:
                errors.append(f"chronology_reverse:{pressure_id}:{entry_id}")
            elif world_key is not None and at_key > world_key:
                errors.append(f"chronology_in_future:{pressure_id}:{entry_id}")
            if prior_status is not None and entry.get("status_before") != prior_status:
                errors.append(f"chronology_status_gap:{pressure_id}:{entry_id}")
            prior_time = at_key if at_key is not None else prior_time
            prior_status = entry.get("status_after")
        if origin and front.get("status") != origin["status"]:
            if not chronology:
                errors.append(f"status_changed_without_chronology:{pressure_id}")
            elif chronology[-1].get("status_after") != front.get("status"):
                errors.append(f"chronology_current_status_drift:{pressure_id}")

        if front.get("status") in ACTIVE_STATUSES:
            boundary = front.get("next_boundary") or {}
            expected_host_id = f"host.canon_pressure.{pressure_id}"
            host_id = boundary.get("host_ref")
            if host_id != expected_host_id:
                errors.append(f"front_host_drift:{pressure_id}:{host_id}")
                continue
            host = hosts.get(host_id)
            if not isinstance(host, dict):
                errors.append(f"active_front_host_missing:{pressure_id}:{host_id}")
                continue
            state = host.get("state") or {}
            metadata = host.get("metadata") or {}
            if host.get("authority_kind") != "canon_pressure":
                errors.append(f"active_front_authority_kind:{pressure_id}:{host.get('authority_kind')}")
            if host.get("owner_ref") != "state/canon/pressures.json":
                errors.append(f"active_front_owner_ref:{pressure_id}:{host.get('owner_ref')}")
            if metadata.get("pressure_id") != pressure_id:
                errors.append(f"front_host_identity_drift:{pressure_id}:{metadata.get('pressure_id')}")
            if boundary.get("settled_through") != state.get("resolved_through"):
                errors.append(f"front_cursor_drift:{pressure_id}")
            if boundary.get("due_at") != state.get("next_due"):
                errors.append(f"front_due_drift:{pressure_id}")
            due_key = parse_time(boundary.get("due_at"))
            settled_key = parse_time(boundary.get("settled_through"))
            if due_key is None or (world_key is not None and due_key <= world_key):
                errors.append(f"active_front_bad_due:{pressure_id}:{boundary.get('due_at')}")
            if settled_key is None or (world_key is not None and settled_key > world_key):
                errors.append(f"active_front_bad_cursor:{pressure_id}:{boundary.get('settled_through')}")
            if front.get("reducer_ref") != REDUCER_REF or not (ROOT / REDUCER_REF).exists():
                errors.append(f"active_front_reducer:{pressure_id}:{front.get('reducer_ref')}")

    for host_id, host in hosts.items():
        if host_id.startswith("host.canon_pressure."):
            pressure_id = (host.get("metadata") or {}).get("pressure_id")
            if pressure_id not in pressures:
                errors.append(f"scheduler_orphan_pressure_id:{pressure_id}")
    return errors


def routing_errors() -> list[str]:
    errors = []
    repository_map = read_json(ROOT / "runtime/contracts/repository-map.json")
    for route in ("canon_pressure_known_id", "canon_pressure_registry"):
        if repository_map.get("route_index", {}).get(route) != "canon":
            errors.append(f"front_route_index:{route}")
    route_shard = read_json(ROOT / "runtime/contracts/repository-routes/canon.json")
    for route in ("canon_pressure_known_id", "canon_pressure_registry"):
        spec = route_shard.get("routes", {}).get(route, {})
        if spec.get("r") != ["state/canon/pressures.json"] or spec.get("w") != ["state/canon/pressures.json"]:
            errors.append(f"front_route_target:{route}")
    owner_shard = read_json(ROOT / "state/index/owners/world.json")
    if owner_shard.get("owners", {}).get("world_world_pressures") != "state/canon/pressures.json":
        errors.append("front_owner_index")
    if (ROOT / "state/world/world-pressures.json").exists():
        errors.append("legacy_pressure_authority_still_present")
    system_index = read_json(ROOT / "runtime/contracts/system-contract-index.json")
    if system_index.get("systems", {}).get("canon_pressures") != REDUCER_REF:
        errors.append("front_system_contract_index")
    return errors


def main() -> int:
    registry = read_json(REGISTRY_PATH)
    scheduler = read_json(SCHEDULER_PATH)
    meta = read_json(META_PATH)
    failures = structural_errors(registry)
    failures.extend(semantic_errors(registry, scheduler, meta))
    failures.extend(routing_errors())

    key_mismatch = copy.deepcopy(registry)
    first_id = next(iter(key_mismatch["pressures"]))
    key_mismatch["pressures"][first_id]["id"] = "pressure_wrong_id"
    if not any("front_key_id_mismatch" in error for error in semantic_errors(key_mismatch, scheduler, meta)):
        failures.append("key_mismatch_fixture_not_rejected")

    due_drift = copy.deepcopy(registry)
    due_drift["pressures"][first_id]["next_boundary"]["due_at"] = "SE-9999-01-01T00:00:00"
    if not any("front_due_drift" in error for error in semantic_errors(due_drift, scheduler, meta)):
        failures.append("due_drift_fixture_not_rejected")

    forcing = copy.deepcopy(registry)
    forcing["pressures"][first_id]["constraints"]["canon_forcing"] = True
    if not any("canon_forcing_front" in error for error in semantic_errors(forcing, scheduler, meta)):
        failures.append("canon_forcing_fixture_not_rejected")

    invented = copy.deepcopy(registry)
    invented["pressures"][first_id]["goal"] = "Unsupported fixture goal"
    if not any("unsupported_executable_detail" in error for error in semantic_errors(invented, scheduler, meta)):
        failures.append("unsupported_detail_fixture_not_rejected")

    knowledge = copy.deepcopy(registry)
    knowledge["pressures"][first_id]["knowledge"]["player_refs"] = ["model_memory"]
    if not any("unresolved_knowledge_ref" in error for error in semantic_errors(knowledge, scheduler, meta)):
        failures.append("knowledge_fixture_not_rejected")

    status = copy.deepcopy(registry)
    status["pressures"][first_id]["status"] = "resolved"
    if not any("status_changed_without_chronology" in error for error in semantic_errors(status, scheduler, meta)):
        failures.append("status_chronology_fixture_not_rejected")

    if failures:
        print(f"CANON PRESSURE TEST FAILED {len(failures)}")
        for failure in failures[:250]:
            print("-", failure)
        return 1

    active = sum(front.get("status") in ACTIVE_STATUSES for front in registry["pressures"].values())
    print("CANON PRESSURE TEST OK")
    print(
        f"pressures={len(registry['pressures'])} active={active} "
        f"scheduler_hosts={len(scheduler.get('hosts', {}))} negative_fixtures=6"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
