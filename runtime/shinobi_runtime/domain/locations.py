"""Generic location graph, containment, and strategic route-anchor resolution."""

from __future__ import annotations

from typing import Any, Mapping, Optional


_SETTLEMENT_KINDS = frozenset((
    "hidden_village", "distributed_hidden_village", "former_hidden_village",
    "town", "civilian_settlement", "capital",
))


class LocationGraph:
    """Bounded location topology with independent containment and movement anchors.

    ``parent_location_ref`` describes physical/administrative containment. It is
    deliberately not a movement shortcut. ``route_anchor_ref`` alone collapses a
    local site to the node used by strategic travel. This keeps a building from
    becoming a province while preserving ports, passes, bridges, and other sites
    that are themselves legitimate strategic nodes.
    """

    def __init__(self, record: Mapping[str, Any]) -> None:
        payload = record.get("payload") if isinstance(record, Mapping) else None
        if not isinstance(payload, Mapping):
            raise ValueError("location graph payload missing")
        places = payload.get("places")
        routes = payload.get("routes")
        if not isinstance(places, list) or not isinstance(routes, list):
            raise ValueError("location graph places/routes invalid")
        place_rows = [item for item in places if isinstance(item, Mapping)]
        place_ids = [item.get("id") for item in place_rows]
        if any(not isinstance(ref, str) or not ref for ref in place_ids):
            raise ValueError("location graph place id invalid")
        if len(set(place_ids)) != len(place_ids):
            raise ValueError("location graph duplicate place id")
        self._places = {str(item["id"]): item for item in place_rows}
        self.routes = tuple(item for item in routes if isinstance(item, Mapping))
        self._validate_hierarchy()

    def _validate_hierarchy(self) -> None:
        for place_id, place in self._places.items():
            parent = place.get("parent_location_ref")
            if parent is not None:
                if not isinstance(parent, str) or not parent or parent == place_id or parent not in self._places:
                    raise ValueError("location graph parent invalid")
                child_country = place.get("country_id")
                parent_country = self._places[parent].get("country_id")
                if child_country is not None and parent_country is not None and child_country != parent_country:
                    raise ValueError("location graph parent country mismatch")
            anchor = place.get("route_anchor_ref")
            if anchor is not None and (
                not isinstance(anchor, str) or not anchor or anchor == place_id or anchor not in self._places
            ):
                raise ValueError("location graph route anchor invalid")

        for place_id in self._places:
            seen: set[str] = set()
            current = place_id
            while True:
                parent = self._places[current].get("parent_location_ref")
                if not isinstance(parent, str) or not parent:
                    break
                if parent in seen or parent == place_id:
                    raise ValueError("location graph containment cycle")
                seen.add(parent)
                current = parent

    def anchor(self, location_ref: str) -> str:
        place = self._places.get(location_ref)
        if place is None:
            return location_ref
        anchor = place.get("route_anchor_ref")
        return anchor if isinstance(anchor, str) and anchor else location_ref

    def place(self, location_ref: str) -> Optional[Mapping[str, Any]]:
        return self._places.get(location_ref)

    def parent(self, location_ref: str) -> Optional[str]:
        place = self._places.get(location_ref)
        parent = place.get("parent_location_ref") if isinstance(place, Mapping) else None
        return parent if isinstance(parent, str) and parent else None

    def ancestors(self, location_ref: str) -> tuple[str, ...]:
        if location_ref not in self._places:
            return ()
        result: list[str] = []
        current = location_ref
        while True:
            parent = self.parent(current)
            if parent is None:
                return tuple(result)
            result.append(parent)
            current = parent

    def country(self, location_ref: str) -> Optional[str]:
        place = self._places.get(location_ref)
        if not isinstance(place, Mapping):
            return None
        country = place.get("country_id")
        if isinstance(country, str) and country:
            return country
        for ancestor in self.ancestors(location_ref):
            value = self._places[ancestor].get("country_id")
            if isinstance(value, str) and value:
                return value
        return None

    def settlement(self, location_ref: str) -> Optional[str]:
        chain = (location_ref, *self.ancestors(location_ref))
        for ref in chain:
            place = self._places.get(ref)
            if isinstance(place, Mapping) and place.get("kind") in _SETTLEMENT_KINDS:
                return ref
        return None

    def hierarchy(self, location_ref: str) -> Mapping[str, Optional[str]]:
        place = self._places.get(location_ref)
        if not isinstance(place, Mapping):
            return {"place_ref": location_ref, "parent_ref": None, "settlement_ref": None, "country_id": None, "route_anchor_ref": location_ref}
        return {
            "place_ref": location_ref,
            "parent_ref": self.parent(location_ref),
            "settlement_ref": self.settlement(location_ref),
            "country_id": self.country(location_ref),
            "route_anchor_ref": self.anchor(location_ref),
        }
