"""Conserved aggregate population transfers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Tuple


def _normalized_dimensions(
    dimensions: Mapping[str, Mapping[str, int]], total: int, label: str
) -> Dict[str, Dict[str, int]]:
    if not isinstance(dimensions, Mapping):
        raise TypeError(f"{label} dimensions must be an object")
    normalized: Dict[str, Dict[str, int]] = {}
    for dimension, categories in dimensions.items():
        if not isinstance(dimension, str) or not dimension:
            raise ValueError(f"{label} dimension names must be non-empty")
        if not isinstance(categories, Mapping) or not categories:
            raise ValueError(f"{label} dimension {dimension} must have categories")
        values: Dict[str, int] = {}
        for category, count in categories.items():
            if not isinstance(category, str) or not category:
                raise ValueError(f"{label} category names must be non-empty")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(f"{label} category counts must be non-negative integers")
            values[category] = count
        if sum(values.values()) != total:
            raise ValueError(
                f"{label} dimension {dimension} sums to {sum(values.values())}, expected {total}"
            )
        normalized[dimension] = dict(sorted(values.items()))
    return normalized


@dataclass(frozen=True)
class PopulationPool:
    pool_id: str
    total: int
    dimensions: Mapping[str, Mapping[str, int]]

    def __post_init__(self) -> None:
        if not isinstance(self.pool_id, str) or not self.pool_id:
            raise ValueError("pool_id must be non-empty")
        if isinstance(self.total, bool) or not isinstance(self.total, int) or self.total < 0:
            raise ValueError("pool total must be a non-negative integer")
        object.__setattr__(
            self,
            "dimensions",
            _normalized_dimensions(self.dimensions, self.total, self.pool_id),
        )


@dataclass(frozen=True)
class PopulationTransfer:
    transfer_id: str
    source_pool_id: str
    destination_pool_id: str
    count: int
    selected_dimensions: Mapping[str, Mapping[str, int]]
    selection_mode: str

    def __post_init__(self) -> None:
        for value in (self.transfer_id, self.source_pool_id, self.destination_pool_id):
            if not isinstance(value, str) or not value:
                raise ValueError("transfer and pool IDs must be non-empty")
        if self.source_pool_id == self.destination_pool_id:
            raise ValueError("population transfer requires distinct pools")
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count <= 0:
            raise ValueError("transfer count must be a positive integer")
        if self.selection_mode not in ("explicit_selection", "neutral_proportional"):
            raise ValueError("unsupported population selection mode")
        object.__setattr__(
            self,
            "selected_dimensions",
            _normalized_dimensions(
                self.selected_dimensions, self.count, self.transfer_id
            ),
        )


def _largest_remainder(categories: Mapping[str, int], total: int, count: int) -> Dict[str, int]:
    if total <= 0 or count > total:
        raise ValueError("neutral selection exceeds source population")
    floors = {
        category: (value * count) // total
        for category, value in categories.items()
    }
    remaining = count - sum(floors.values())
    order = sorted(
        categories,
        key=lambda category: (
            -((categories[category] * count) % total),
            category,
        ),
    )
    for category in order[:remaining]:
        floors[category] += 1
    return dict(sorted(floors.items()))


def neutral_proportional_selection(
    source: PopulationPool, count: int
) -> Mapping[str, Mapping[str, int]]:
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("selection count must be a positive integer")
    return {
        dimension: _largest_remainder(categories, source.total, count)
        for dimension, categories in source.dimensions.items()
    }


def apply_transfer(
    source: PopulationPool,
    destination: PopulationPool,
    transfer: PopulationTransfer,
) -> Tuple[PopulationPool, PopulationPool]:
    if transfer.source_pool_id != source.pool_id:
        raise ValueError("transfer source ID does not match source pool")
    if transfer.destination_pool_id != destination.pool_id:
        raise ValueError("transfer destination ID does not match destination pool")
    if transfer.count > source.total:
        raise ValueError("transfer exceeds source population")
    if set(source.dimensions) != set(destination.dimensions):
        raise ValueError("source and destination dimensions differ")
    if set(transfer.selected_dimensions) != set(source.dimensions):
        raise ValueError("transfer does not cover every population dimension")

    source_after: Dict[str, Dict[str, int]] = {}
    destination_after: Dict[str, Dict[str, int]] = {}
    for dimension in sorted(source.dimensions):
        selected = transfer.selected_dimensions[dimension]
        if set(selected) - set(source.dimensions[dimension]):
            raise ValueError(f"unknown selected category in {dimension}")
        destination_categories = set(destination.dimensions[dimension]) | set(selected)
        source_categories = set(source.dimensions[dimension]) | set(selected)
        source_values: Dict[str, int] = {}
        for category in sorted(source_categories):
            before = source.dimensions[dimension].get(category, 0)
            removed = selected.get(category, 0)
            if removed > before:
                raise ValueError(f"selected {dimension}:{category} exceeds source")
            source_values[category] = before - removed
        destination_values = {
            category: destination.dimensions[dimension].get(category, 0)
            + selected.get(category, 0)
            for category in sorted(destination_categories)
        }
        source_after[dimension] = source_values
        destination_after[dimension] = destination_values

    return (
        PopulationPool(source.pool_id, source.total - transfer.count, source_after),
        PopulationPool(
            destination.pool_id,
            destination.total + transfer.count,
            destination_after,
        ),
    )


def materialize_member(source: PopulationPool) -> Tuple[PopulationPool, Mapping[str, Mapping[str, int]]]:
    """Select one already-existing human for persistent representation.

    Materialization changes representation, not physical population.  The pool
    continues to count the same human; callers move one slot from anonymous to
    rostered representation and keep this physical PopulationPool unchanged.
    """
    if source.total <= 0:
        raise ValueError("cannot materialize from an empty population pool")
    selected = neutral_proportional_selection(source, 1)
    return source, selected
