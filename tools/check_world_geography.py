#!/usr/bin/env python3
"""Validate current operational place containment and route-scope invariants."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "runtime"))

from shinobi_runtime.domain.locations import LocationGraph  # noqa: E402

WORLD_PATH = ROOT / "state/world/routes-and-settlements.json"
ROUTE_SCOPES = {"strategic", "local_access"}


def fail(message: str) -> None:
    raise SystemExit(f"WORLD GEOGRAPHY INVALID: {message}")


def main() -> int:
    record = json.loads(WORLD_PATH.read_text(encoding="utf-8"))
    payload = record.get("payload")
    if not isinstance(payload, dict):
        fail("payload_missing")
    try:
        graph = LocationGraph(record)
    except ValueError as exc:
        fail(str(exc))

    places = payload.get("places")
    routes = payload.get("routes")
    if not isinstance(places, list) or not isinstance(routes, list):
        fail("places_or_routes_missing")
    place_ids = {row.get("id") for row in places if isinstance(row, dict)}
    route_ids: set[str] = set()

    for route in routes:
        if not isinstance(route, dict):
            fail("route_not_object")
        route_id = route.get("id")
        if not isinstance(route_id, str) or not route_id:
            fail("route_id_invalid")
        if route_id in route_ids:
            fail(f"duplicate_route:{route_id}")
        route_ids.add(route_id)
        source = route.get("from")
        destination = route.get("to")
        if source not in place_ids or destination not in place_ids:
            fail(f"route_endpoint_missing:{route_id}")
        scope = route.get("scope")
        if scope not in ROUTE_SCOPES:
            fail(f"route_scope_invalid:{route_id}:{scope}")
        classification = route.get("knowledge_classification")
        if not isinstance(classification, str) or not classification:
            fail(f"route_classification_missing:{route_id}")

        source_anchor = graph.anchor(str(source))
        destination_anchor = graph.anchor(str(destination))
        if scope == "strategic":
            if source_anchor != source or destination_anchor != destination:
                fail(f"strategic_route_targets_local_site:{route_id}")
        else:
            # Local access may connect a movement anchor to one of its local
            # children, including a harbor/pass that is itself also a valid
            # strategic endpoint. Otherwise both endpoints must collapse to the
            # same strategic anchor.
            same_anchor = source_anchor == destination_anchor
            direct_parent_child = graph.parent(str(source)) == destination or graph.parent(str(destination)) == source
            if not same_anchor and not direct_parent_child:
                fail(f"local_access_crosses_strategic_anchors:{route_id}")

    print(f"WORLD GEOGRAPHY OK: {len(place_ids)} places; {len(route_ids)} routes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
