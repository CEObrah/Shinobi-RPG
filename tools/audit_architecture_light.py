#!/usr/bin/env python3
"""Fast architecture/invariant audit without the broad test suite.

This checker targets state-model brittleness: duplicate authority, orphan
mechanics/events, invalid world topology, and broken aggregate/exact accounting.
It is intentionally read-only and bounded to known registries.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from shinobi_runtime.commands.planner import RepositoryCommandPlanner  # noqa: E402
from shinobi_runtime.commands.specs import COMMAND_SPECS  # noqa: E402

ERRORS: list[str] = []
WARNINGS: list[str] = []
METRICS: dict[str, object] = {}


def error(message: str) -> None:
    ERRORS.append(message)


def warn(message: str) -> None:
    WARNINGS.append(message)


def load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def json_integrity() -> None:
    count = 0
    for root in (ROOT / "game", ROOT / "state", ROOT / "runtime" / "contracts"):
        for path in root.rglob("*.json"):
            count += 1
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                error(f"json_invalid:{path.relative_to(ROOT)}:{exc}")
    METRICS["json_files_checked"] = count


def command_surface() -> None:
    for name, spec in COMMAND_SPECS.items():
        if not hasattr(RepositoryCommandPlanner, "_" + name):
            error(f"command_handler_missing:{name}")
        if spec.variants:
            for action, variant in spec.variants.items():
                if "action" not in variant.required_fields:
                    error(f"command_variant_action_missing:{name}:{action}")
                allowed = set(spec.required_fields) | set(spec.optional_fields)
                if not set(variant.required_fields).issubset(allowed) or not set(variant.optional_fields).issubset(allowed):
                    error(f"command_variant_field_not_in_internal_shape:{name}:{action}")
    METRICS["semantic_commands"] = len(COMMAND_SPECS)


def world_topology_and_modules() -> tuple[set[str], set[str]]:
    world = load("state/world/routes-and-settlements.json")
    payload = world.get("payload", {})
    places = payload.get("places", [])
    routes = payload.get("routes", [])
    if not isinstance(places, list) or not isinstance(routes, list):
        error("world_registry_shape_invalid")
        return set(), set()
    place_ids = [row.get("id") for row in places if isinstance(row, dict)]
    route_ids = [row.get("id") for row in routes if isinstance(row, dict)]
    if len(place_ids) != len(set(place_ids)):
        error("place_ids_duplicate")
    if len(route_ids) != len(set(route_ids)):
        error("route_ids_duplicate")
    pset, rset = set(place_ids), set(route_ids)
    adjacency: dict[str, set[str]] = defaultdict(set)
    for place in places:
        if not isinstance(place, dict):
            error("place_record_invalid")
            continue
        anchor = place.get("route_anchor_ref")
        if anchor is not None and anchor not in pset:
            error(f"place_anchor_unresolved:{place.get('id')}:{anchor}")
        modules = place.get("mechanical_modules")
        if modules is None:
            continue
        if not isinstance(modules, dict):
            error(f"mechanical_modules_invalid:{place.get('id')}")
            continue
        allowed_modules = {"training", "medical", "custody"}
        extra = set(modules) - allowed_modules
        if extra:
            error(f"orphan_mechanical_module:{place.get('id')}:{sorted(extra)}")
        training = modules.get("training")
        if training is not None:
            if not isinstance(training, dict) or set(training) != {"capacity_slots", "quality_milli", "supported_categories"}:
                error(f"training_module_shape_invalid:{place.get('id')}")
            else:
                categories = training.get("supported_categories")
                consumed = {"martial", "chakra", "technique", "stealth", "covert", "anbu", "tracking", "survival", "medical", "combat", "team_drill"}
                if not isinstance(categories, list) or any(category not in consumed for category in categories):
                    error(f"training_module_orphan_category:{place.get('id')}:{categories}")
        medical = modules.get("medical")
        if medical is not None:
            if not isinstance(medical, dict) or set(medical) != {"quality_milli", "specialties"}:
                error(f"medical_module_shape_invalid:{place.get('id')}")
            else:
                specialties = medical.get("specialties")
                consumed_specialties = {"stabilization", "surgery", "ocular_surgery"}
                if not isinstance(specialties, list) or any(value not in consumed_specialties for value in specialties):
                    error(f"medical_module_orphan_specialty:{place.get('id')}:{specialties}")
        custody = modules.get("custody")
        if custody is not None:
            if not isinstance(custody, dict) or set(custody) != {"capacity_slots", "security_milli"}:
                error(f"custody_module_shape_invalid:{place.get('id')}")
            elif (
                isinstance(custody.get("capacity_slots"), bool)
                or not isinstance(custody.get("capacity_slots"), int)
                or custody.get("capacity_slots") <= 0
                or isinstance(custody.get("security_milli"), bool)
                or not isinstance(custody.get("security_milli"), int)
                or not 0 <= custody.get("security_milli") <= 1000
            ):
                error(f"custody_module_value_invalid:{place.get('id')}")
    route_endpoints: set[str] = set()
    for route in routes:
        if not isinstance(route, dict):
            error("route_record_invalid")
            continue
        a, b = route.get("from"), route.get("to")
        if a not in pset or b not in pset or a == b:
            error(f"route_endpoint_invalid:{route.get('id')}:{a}:{b}")
            continue
        route_endpoints.update((a, b))
        adjacency[a].add(b); adjacency[b].add(a)
    if route_endpoints:
        start = next(iter(route_endpoints)); seen = {start}; q = deque([start])
        while q:
            node = q.popleft()
            for nxt in adjacency[node]:
                if nxt not in seen:
                    seen.add(nxt); q.append(nxt)
        if seen != route_endpoints:
            error(f"strategic_route_graph_disconnected:{len(route_endpoints-seen)}")
    METRICS.update({"places": len(pset), "routes": len(rset), "mechanical_places": sum(1 for p in places if isinstance(p, dict) and p.get("mechanical_modules"))})
    return pset, rset


def forces_and_formations() -> tuple[dict[str, dict], dict[str, dict]]:
    forces: dict[str, dict] = {}
    for path in (ROOT / "state/force").glob("*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        force_id = row.get("id")
        if isinstance(force_id, str):
            forces[force_id] = row
        availability = row.get("availability")
        if not isinstance(availability, dict) or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in availability.values()):
            error(f"force_availability_invalid:{force_id}")
        elif sum(availability.values()) != row.get("total"):
            error(f"force_availability_not_conserved:{force_id}")
    formations: dict[str, dict] = {}
    represented = Counter()
    for path in (ROOT / "state/formation").glob("*.json"):
        owner = json.loads(path.read_text(encoding="utf-8"))
        for row in owner.get("formations", []):
            if not isinstance(row, dict):
                error(f"formation_record_invalid:{path.name}")
                continue
            ref, force_ref = row.get("id"), row.get("force_ref")
            if not isinstance(ref, str) or ref in formations:
                error(f"formation_id_invalid_or_duplicate:{ref}")
                continue
            formations[ref] = row
            if force_ref not in forces:
                error(f"formation_force_unresolved:{ref}:{force_ref}")
                continue
            total = row.get("personnel_total")
            components = row.get("components")
            command = row.get("command_personnel")
            if isinstance(total, bool) or not isinstance(total, int) or total <= 0 or not isinstance(components, list) or not isinstance(command, dict):
                error(f"formation_headcount_shape_invalid:{ref}")
                continue
            component_total = 0
            for component in components:
                if not isinstance(component, dict):
                    error(f"formation_component_invalid:{ref}")
                    continue
                banned = {"readiness", "morale", "cohesion", "resolution_scale"} & set(component)
                if banned:
                    error(f"formation_component_inherited_state:{ref}:{sorted(banned)}")
                count = component.get("count")
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    error(f"formation_component_count_invalid:{ref}")
                else:
                    component_total += count
            command_count = command.get("count")
            if isinstance(command_count, bool) or not isinstance(command_count, int) or command_count < 0 or component_total + command_count != total:
                error(f"formation_component_headcount_mismatch:{ref}:{component_total}+{command_count}!={total}")
            objective = row.get("operational_objective")
            if not isinstance(objective, dict) or set(objective) != {"kind", "target_refs", "lethal_authorized"}:
                error(f"formation_objective_invalid:{ref}")
            represented[force_ref] += total
    for force_ref, count in represented.items():
        deployed = forces[force_ref].get("availability", {}).get("deployed")
        if isinstance(deployed, int) and count > deployed:
            error(f"formation_representation_exceeds_deployed:{force_ref}:{count}>{deployed}")
    METRICS.update({"forces": len(forces), "formations": len(formations), "formation_personnel_explicit": sum(represented.values())})
    return forces, formations


def teams(formations: dict[str, dict]) -> None:
    count = 0
    for path in (ROOT / "state/team").glob("*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("schema") != "exact-team":
            continue
        count += 1
        members = row.get("member_refs")
        embedded = row.get("embedded_member_refs", [])
        assignment = row.get("current_assignment_ref")
        if not isinstance(members, list) or not isinstance(embedded, list) or not set(embedded).issubset(set(members)):
            error(f"team_embedding_invalid:{row.get('id')}")
        if assignment is not None:
            if assignment not in formations:
                error(f"team_assignment_formation_unresolved:{row.get('id')}:{assignment}")
            if len(embedded) > formations.get(assignment, {}).get("personnel_total", -1):
                error(f"team_embedding_exceeds_formation:{row.get('id')}")
    METRICS["exact_teams"] = count


def conflict_custody_zoom(pset: set[str], rset: set[str], formations: dict[str, dict], forces: dict[str, dict]) -> None:
    conflict = load("state/conflict/registry.json")
    conflicts = conflict.get("records", {})
    if not isinstance(conflicts, dict):
        error("conflict_registry_invalid")
        conflicts = {}
    world = load("state/world/routes-and-settlements.json")
    places = world.get("payload", {}).get("places", [])
    routes = world.get("payload", {}).get("routes", [])
    place_by_id = {p.get("id"): p for p in places if isinstance(p, dict) and isinstance(p.get("id"), str)}
    route_by_id = {r.get("id"): r for r in routes if isinstance(r, dict) and isinstance(r.get("id"), str)}
    def anchor(ref: str) -> str:
        row = place_by_id.get(ref, {})
        value = row.get("route_anchor_ref") or row.get("parent_location_ref")
        return value if isinstance(value, str) and value else ref
    for cref, record in conflicts.items():
        fronts = record.get("fronts") if isinstance(record, dict) else None
        sides = set(record.get("side_refs", ())) if isinstance(record, dict) and isinstance(record.get("side_refs"), list) else set()
        if not isinstance(fronts, dict):
            error(f"conflict_fronts_invalid:{cref}"); continue
        assigned: set[str] = set()
        for fref, front in fronts.items():
            if not isinstance(front, dict): error(f"front_invalid:{cref}:{fref}"); continue
            front_places = front.get("place_refs", [])
            front_routes = front.get("route_refs", [])
            if any(ref not in pset for ref in front_places): error(f"front_place_unresolved:{cref}:{fref}")
            if any(ref not in rset for ref in front_routes): error(f"front_route_unresolved:{cref}:{fref}")
            control_ref = front.get("control_ref")
            if control_ref is not None and control_ref not in sides:
                error(f"front_controller_not_conflict_side:{cref}:{fref}:{control_ref}")
            # Active front geography must describe one connected strategic area.
            anchors = {anchor(ref) for ref in front_places if isinstance(ref, str)}
            selected = [route_by_id[ref] for ref in front_routes if ref in route_by_id]
            if not anchors and not selected:
                error(f"front_geography_empty:{cref}:{fref}")
            elif selected:
                nodes = set(anchors)
                adjacency: dict[str, set[str]] = defaultdict(set)
                route_nodes: set[str] = set()
                for route in selected:
                    left, right = route.get("from"), route.get("to")
                    if isinstance(left, str) and isinstance(right, str):
                        nodes.update((left, right)); route_nodes.update((left, right))
                        adjacency[left].add(right); adjacency[right].add(left)
                if anchors - route_nodes:
                    error(f"front_geography_disconnected:{cref}:{fref}")
                elif nodes:
                    start = next(iter(nodes)); seen = {start}; q = deque([start])
                    while q:
                        node = q.popleft()
                        for nxt in adjacency[node]:
                            if nxt not in seen:
                                seen.add(nxt); q.append(nxt)
                    if seen != nodes:
                        error(f"front_geography_disconnected:{cref}:{fref}")
            elif len(anchors) > 1:
                error(f"front_geography_disconnected:{cref}:{fref}")
            for formation_ref in front.get("formation_refs", []):
                if formation_ref not in formations: error(f"front_formation_unresolved:{cref}:{fref}:{formation_ref}")
                if formation_ref in assigned and front.get("status") == "active": error(f"formation_multi_front_assignment:{formation_ref}")
                assigned.add(formation_ref)
            route_state = front.get("route_state", {})
            if not isinstance(route_state, dict) or any(ref not in front_routes for ref in route_state):
                error(f"front_route_state_outside_front:{cref}:{fref}")
            elif any(isinstance(row, dict) and row.get("controller_ref") is not None and row.get("controller_ref") not in sides for row in route_state.values()):
                error(f"front_route_controller_not_conflict_side:{cref}:{fref}")
            occupations = front.get("occupations", {})
            if not isinstance(occupations, dict):
                error(f"front_occupations_invalid:{cref}:{fref}")
            else:
                for place_ref, occupation in occupations.items():
                    if place_ref not in front_places or not isinstance(occupation, dict) or occupation.get("place_ref") != place_ref:
                        error(f"front_occupation_place_invalid:{cref}:{fref}:{place_ref}")
                    elif occupation.get("controller_ref") not in sides:
                        error(f"front_occupation_controller_not_conflict_side:{cref}:{fref}:{place_ref}")
    custody = load("state/reg/custody.json")
    records = custody.get("records", {})
    if not isinstance(records, dict): error("custody_registry_invalid"); records = {}
    occupied = Counter()
    captured_by_force = Counter()
    place_by_id = {p.get("id"): p for p in world.get("payload", {}).get("places", []) if isinstance(p, dict)}
    for ref, row in records.items():
        if not isinstance(row, dict): error(f"custody_record_invalid:{ref}"); continue
        count = row.get("count", 0)
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0: error(f"custody_count_invalid:{ref}"); continue
        if row.get("status") == "detained":
            place_ref = row.get("place_ref"); occupied[place_ref] += count
            force_ref = row.get("force_ref")
            if isinstance(force_ref, str): captured_by_force[force_ref] += count
    for place_ref, count in occupied.items():
        modules = place_by_id.get(place_ref, {}).get("mechanical_modules", {})
        capacity = modules.get("custody", {}).get("capacity_slots") if isinstance(modules, dict) else None
        if not isinstance(capacity, int) or count > capacity: error(f"custody_capacity_exceeded:{place_ref}:{count}>{capacity}")
    for force_ref, count in captured_by_force.items():
        captured = forces.get(force_ref, {}).get("availability", {}).get("captured_or_missing")
        if not isinstance(captured, int) or count > captured: error(f"custody_exceeds_force_capture:{force_ref}:{count}>{captured}")
    zoom = load("state/reg/combat-zoom.json")
    pending = zoom.get("pending_by_actor", {})
    if not isinstance(pending, dict): error("combat_zoom_registry_invalid"); pending = {}
    for actor_ref, combat_ref in pending.items():
        op = ROOT / "state/operation" / (re.sub(r"[^a-z0-9._-]", "_", str(combat_ref)) + ".json")
        if not op.exists(): error(f"combat_zoom_parent_missing:{actor_ref}:{combat_ref}"); continue
        record = json.loads(op.read_text(encoding="utf-8"))
        pending_refs = record.get("outcome", {}).get("pending_named_actor_refs", [])
        if record.get("status") != "awaiting_named_zoom" or actor_ref not in pending_refs:
            error(f"combat_zoom_parent_mismatch:{actor_ref}:{combat_ref}")
    METRICS.update({"conflicts": len(conflicts), "custody_records": len(records), "pending_combat_zoom_actors": len(pending)})


def scheduler_consumers() -> None:
    scheduler = load("state/time/causal-scheduler.json")
    events = scheduler.get("events", [])
    queued = {row.get("kind") for row in events if isinstance(row, dict) and isinstance(row.get("kind"), str)}
    consumers = {
        "person.recovery.periodic_review", "faction.periodic_review", "team.periodic_review",
        "canon_pressure.periodic_review", "economy.periodic_review", "world_registry.periodic_review",
        "house.periodic_review", "person_continuity.periodic_review", "commitment.due",
        "population.periodic_review", "scene.player_boundary", "mission.boundary",
    }
    orphan = sorted(queued - consumers)
    if orphan: error(f"scheduler_event_without_consumer:{orphan}")
    METRICS.update({"scheduler_hosts": len(scheduler.get("hosts", {})), "queued_events": len(events), "queued_event_kinds": sorted(queued)})


def duplicate_and_history_sweep() -> None:
    for root in (ROOT / "state", ROOT / "runtime" / "contracts", ROOT / "runtime" / "shinobi_runtime"):
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in (".json", ".py"):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "field_usable_techniques" in text:
                error(f"duplicate_repertoire_authority_present:{path.relative_to(ROOT)}")
    banned_rule_patterns = [
        re.compile(r"\bdeprecated\b", re.I), re.compile(r"\bmigration\b", re.I),
        re.compile(r"\blegacy\b", re.I), re.compile(r"\bversion(?:ed|ing|s)?\b", re.I),
        re.compile(r"\bv\d+(?:\.\d+)*\b", re.I),
    ]
    for path in (ROOT / "game/rules").rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for pattern in banned_rule_patterns:
            if pattern.search(text):
                error(f"gameplay_history_language:{path.relative_to(ROOT)}:{pattern.pattern}")
    for retired in ("state/unit", "state/unit-capability", "state/unit-kernel", "state/tactical-team"):
        if (ROOT / retired).exists(): error(f"retired_unit_authority_present:{retired}")
    for path in (ROOT / "game/schemas").glob("*.json"):
        if re.search(r"(?:^|[-_.])v\d+(?:[-_.]|$)", path.name, re.I):
            error(f"versioned_gameplay_schema_filename:{path.name}")


def project_mechanics() -> None:
    mechanics = load("game/data/mechanics/institution-projects.json")
    for name, rule in mechanics.get("project_types", {}).items():
        if not isinstance(rule, dict): error(f"project_rule_invalid:{name}"); continue
        if rule.get("module_kind") not in {"training", "medical", "custody"}:
            error(f"project_orphan_module_kind:{name}:{rule.get('module_kind')}")
        if rule.get("module_kind") == "custody" and isinstance(rule.get("create_defaults"), dict):
            defaults = rule["create_defaults"]
            if not isinstance(defaults.get("security_milli"), int):
                error(f"custody_project_security_missing:{name}")
        defaults = rule.get("create_defaults")
        if rule.get("module_kind") == "medical" and isinstance(defaults, dict):
            specialties = defaults.get("specialties", [])
            if not isinstance(specialties, list) or any(value not in {"stabilization", "surgery", "ocular_surgery"} for value in specialties):
                error(f"medical_project_orphan_specialty:{name}:{specialties}")
        if rule.get("module_kind") == "training" and isinstance(defaults, dict):
            categories = defaults.get("supported_categories", [])
            consumed = {"martial", "chakra", "technique", "stealth", "covert", "anbu", "tracking", "survival", "medical", "combat", "team_drill"}
            if not isinstance(categories, list) or any(value not in consumed for value in categories):
                error(f"training_project_orphan_category:{name}:{categories}")


def main() -> int:
    json_integrity()
    command_surface()
    pset, rset = world_topology_and_modules()
    forces, formations = forces_and_formations()
    teams(formations)
    conflict_custody_zoom(pset, rset, formations, forces)
    scheduler_consumers()
    duplicate_and_history_sweep()
    project_mechanics()
    METRICS["mutable_state_bytes"] = sum(p.stat().st_size for p in (ROOT / "state").rglob("*.json"))
    print(json.dumps({"ok": not ERRORS, "errors": ERRORS, "warnings": WARNINGS, "metrics": METRICS}, indent=2, sort_keys=True))
    return 0 if not ERRORS else 1


if __name__ == "__main__":
    raise SystemExit(main())
