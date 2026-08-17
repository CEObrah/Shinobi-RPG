"""Knowledge-gated access to non-public strategic routes.

Route existence is world truth. Public routes are common knowledge; any route
whose ``knowledge_classification`` is not ``public`` requires an exact
information claim held by the acting subject before it may be exposed or used.
This module never grants route knowledge merely from repository visibility.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shinobi_runtime.information import InformationStore


def route_is_public(route: Mapping[str, Any]) -> bool:
    return route.get("knowledge_classification", "public") == "public"


def actor_knows_route(repository: Any, actor_ref: str, route: Mapping[str, Any]) -> bool:
    """Return whether ``actor_ref`` may lawfully know/use one exact route."""

    route_ref = route.get("id")
    if not isinstance(route_ref, str) or not route_ref:
        return False
    if route_is_public(route):
        return True
    try:
        return bool(
            InformationStore(repository).holder_subject_claim_refs(
                actor_ref, route_ref, limit=1
            )
        )
    except ValueError:
        return False


def known_nonpublic_routes_for_connection(
    repository: Any,
    actor_ref: str,
    graph: Any,
    *,
    origin_id: str,
    destination_id: str,
) -> set[str]:
    """Resolve only non-public routes relevant to one requested connection.

    The caller already loaded the bounded world route registry. Knowledge reads
    are then exact subject-shard lookups only for matching endpoint candidates,
    never a lifetime information scan.
    """

    origin_anchor = graph.anchor(origin_id)
    destination_anchor = graph.anchor(destination_id)
    known: set[str] = set()
    if origin_anchor == destination_anchor:
        return known
    for route in graph.routes:
        if not isinstance(route, Mapping) or route_is_public(route):
            continue
        route_ref = route.get("id")
        if not isinstance(route_ref, str) or not route_ref:
            continue
        if origin_anchor not in (route.get("from"), route.get("to")):
            continue
        if destination_anchor not in (route.get("from"), route.get("to")):
            continue
        if actor_knows_route(repository, actor_ref, route):
            known.add(route_ref)
    return known


__all__ = [
    "actor_knows_route",
    "known_nonpublic_routes_for_connection",
    "route_is_public",
]
