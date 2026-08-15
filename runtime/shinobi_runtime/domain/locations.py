"""Generic location graph and route-anchor resolution."""

from __future__ import annotations

from typing import Any, Mapping, Optional


class LocationGraph:
    def __init__(self, record: Mapping[str, Any]) -> None:
        payload = record.get("payload") if isinstance(record, Mapping) else None
        if not isinstance(payload, Mapping):
            raise ValueError("location graph payload missing")
        places = payload.get("places")
        routes = payload.get("routes")
        if not isinstance(places, list) or not isinstance(routes, list):
            raise ValueError("location graph places/routes invalid")
        self._places = {
            item.get("id"): item
            for item in places
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        }
        self.routes = tuple(item for item in routes if isinstance(item, Mapping))

    def anchor(self, location_ref: str) -> str:
        place = self._places.get(location_ref)
        if place is None:
            return location_ref
        anchor = place.get("route_anchor_ref") or place.get("parent_location_ref")
        return anchor if isinstance(anchor, str) and anchor else location_ref

    def place(self, location_ref: str) -> Optional[Mapping[str, Any]]:
        return self._places.get(location_ref)
