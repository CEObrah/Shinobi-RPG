"""Identity-preserving cohort placement and deterministic selection."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from shinobi_runtime.sim import CounterRNG


def _unique_ids(values: Iterable[str], name: str) -> Tuple[str, ...]:
    result = tuple(sorted(values))
    if any(not isinstance(value, str) or not value for value in result):
        raise ValueError(f"{name} requires non-empty IDs")
    if len(result) != len(set(result)):
        raise ValueError(f"{name} contains duplicate IDs")
    return result


@dataclass(frozen=True)
class CohortRoster:
    cohort_id: str
    exact_member_ids: Tuple[str, ...]
    rostered_member_ids: Tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.cohort_id, str) or not self.cohort_id:
            raise ValueError("cohort_id must be non-empty")
        object.__setattr__(
            self, "exact_member_ids", _unique_ids(self.exact_member_ids, "exact members")
        )
        object.__setattr__(
            self,
            "rostered_member_ids",
            _unique_ids(self.rostered_member_ids, "rostered members"),
        )
        overlap = set(self.exact_member_ids) & set(self.rostered_member_ids)
        if overlap:
            raise ValueError(f"people occupy two cohort representations: {sorted(overlap)}")

    @property
    def represented_count(self) -> int:
        return len(self.exact_member_ids) + len(self.rostered_member_ids)

    @property
    def cohort_backed_count(self) -> int:
        return len(self.rostered_member_ids)

    def select_rostered(self, count: int, rng: CounterRNG) -> Tuple[str, ...]:
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("selection count must be a non-negative integer")
        if count > len(self.rostered_member_ids):
            raise ValueError("selection exceeds rostered cohort")
        available = list(self.rostered_member_ids)
        selected = []
        for _ in range(count):
            index = rng.randbelow(len(available))
            selected.append(available.pop(index))
        return tuple(selected)

    def materialize(self, person_id: str) -> "CohortRoster":
        if person_id not in self.rostered_member_ids:
            raise ValueError("only a rostered cohort person can materialize")
        return CohortRoster(
            cohort_id=self.cohort_id,
            exact_member_ids=self.exact_member_ids + (person_id,),
            rostered_member_ids=tuple(
                value for value in self.rostered_member_ids if value != person_id
            ),
        )


def _stable_slot_order(cohort_id: str, namespace: str, count: int) -> Tuple[int, ...]:
    return tuple(
        sorted(
            range(count),
            key=lambda slot: hashlib.sha256(
                f"{cohort_id}\x00{namespace}\x00{slot}".encode("utf-8")
            ).digest(),
        )
    )


def _moment_values(summary: Mapping[str, Any]) -> Tuple[float, ...]:
    """Construct bounded values with the saved population moments.

    A cohort summary is authoritative.  This deterministic construction merely
    assigns its already-saved mean, population spread, minimum, and maximum to
    stable roster slots.  It does not sample or reroll people.
    """

    count = summary.get("count")
    values = tuple(summary.get(key) for key in ("mean", "sd", "min", "max"))
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count <= 0
        or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in values
        )
    ):
        raise ValueError("numeric distribution summary is invalid")
    mean, spread, minimum, maximum = map(float, values)
    if not all(math.isfinite(value) for value in (mean, spread, minimum, maximum)):
        raise ValueError("numeric distribution contains a non-finite value")
    if spread < 0 or minimum > mean or mean > maximum:
        raise ValueError("numeric distribution bounds/moments are inconsistent")
    if count == 1:
        if spread > 0 or abs(minimum - maximum) > 1e-9:
            raise ValueError("one-person distribution cannot have spread")
        return (mean,)
    if spread <= 1e-12:
        if max(abs(mean - minimum), abs(mean - maximum)) > 1e-6:
            raise ValueError("zero-spread distribution has unequal bounds")
        return tuple(mean for _ in range(count))

    target_sum = count * mean
    target_squares = count * (spread * spread + mean * mean)
    remaining_count = count - 2
    remaining_sum = target_sum - minimum - maximum
    remaining_squares = target_squares - minimum * minimum - maximum * maximum
    candidates = []

    # A bounded distribution with two known extrema can express the remaining
    # two moments with at most three distinct interior values.  Enumerating how
    # many additional extrema are present makes the construction exact for the
    # compact cohort summaries used by the campaign.
    for lower_count in range(remaining_count + 1):
        for upper_count in range(remaining_count - lower_count + 1):
            interior_count = remaining_count - lower_count - upper_count
            interior_sum = (
                remaining_sum
                - lower_count * minimum
                - upper_count * maximum
            )
            interior_squares = (
                remaining_squares
                - lower_count * minimum * minimum
                - upper_count * maximum * maximum
            )
            interiors = []
            if interior_count == 0:
                if abs(interior_sum) > 1e-5 or abs(interior_squares) > 1e-4:
                    continue
            elif interior_count == 1:
                value = interior_sum
                if (
                    value < minimum - 1e-6
                    or value > maximum + 1e-6
                    or abs(value * value - interior_squares) > 1e-4
                ):
                    continue
                interiors = [value]
            else:
                discriminant = (
                    interior_count * interior_squares
                    - interior_sum * interior_sum
                ) / (interior_count - 1)
                if discriminant < -1e-4:
                    continue
                root = math.sqrt(max(0.0, discriminant))
                roots = (
                    (interior_sum + root) / interior_count,
                    (interior_sum - root) / interior_count,
                )
                for repeated in roots:
                    final = interior_sum - (interior_count - 1) * repeated
                    if (
                        minimum - 1e-6 <= repeated <= maximum + 1e-6
                        and minimum - 1e-6 <= final <= maximum + 1e-6
                    ):
                        candidate = (
                            [minimum]
                            + [minimum] * lower_count
                            + [repeated] * (interior_count - 1)
                            + [final]
                            + [maximum] * upper_count
                            + [maximum]
                        )
                        actual_mean = sum(candidate) / count
                        actual_spread = math.sqrt(
                            sum((value - actual_mean) ** 2 for value in candidate)
                            / count
                        )
                        if (
                            abs(actual_mean - mean) <= 1e-5
                            and abs(actual_spread - spread) <= 1e-5
                        ):
                            candidates.append(candidate)
                continue

            candidate = (
                [minimum]
                + [minimum] * lower_count
                + interiors
                + [maximum] * upper_count
                + [maximum]
            )
            if len(candidate) == count:
                candidates.append(candidate)

    if not candidates:
        raise ValueError("saved numeric moments cannot be assigned to roster slots")
    chosen = min(
        candidates,
        key=lambda candidate: (
            sum(
                1
                for value in candidate
                if abs(value - minimum) <= 1e-7
                or abs(value - maximum) <= 1e-7
            ),
            tuple(round(value, 12) for value in candidate),
        ),
    )
    return tuple(round(value, 9) for value in chosen)


def _slot_categories(
    *,
    cohort_id: str,
    slot: int,
    count: int,
    category_counts: Mapping[str, Any],
) -> Mapping[str, Tuple[str, ...]]:
    grouped: Dict[str, Dict[str, int]] = {}
    for tag, amount in category_counts.items():
        if (
            not isinstance(tag, str)
            or ":" not in tag
            or isinstance(amount, bool)
            or not isinstance(amount, int)
            or amount < 0
            or amount > count
        ):
            raise ValueError("cohort category count is invalid")
        group, value = tag.split(":", 1)
        grouped.setdefault(group, {})[value] = amount

    result: Dict[str, Tuple[str, ...]] = {}
    for group, counts in sorted(grouped.items()):
        total = sum(counts.values())
        assigned = []
        if total <= count:
            labels = [
                value
                for value, amount in sorted(counts.items())
                for _ in range(amount)
            ] + [None] * (count - total)
            order = _stable_slot_order(cohort_id, f"category:{group}", count)
            label_by_slot = {target: labels[index] for index, target in enumerate(order)}
            if label_by_slot[slot] is not None:
                assigned.append(label_by_slot[slot])
        else:
            # Multi-valued dimensions conserve every category independently.
            for value, amount in sorted(counts.items()):
                selected = set(
                    _stable_slot_order(
                        cohort_id,
                        f"category:{group}:{value}",
                        count,
                    )[:amount]
                )
                if slot in selected:
                    assigned.append(value)
        result[group] = tuple(assigned)
    return result


def cohort_slot_baseline(
    *,
    cohort_id: str,
    profile: Mapping[str, Any],
    slot: int,
    expected_count: Optional[int] = None,
) -> Mapping[str, Any]:
    """Resolve one reproducible full-sheet baseline from a cohort authority."""

    if not isinstance(cohort_id, str) or not cohort_id:
        raise ValueError("cohort_id must be non-empty")
    if not isinstance(profile, Mapping) or profile.get("representation") != "house_cohort":
        raise ValueError("rostered person requires a House cohort profile")
    distributions = profile.get("numeric_distributions")
    categories = profile.get("category_counts")
    if not isinstance(distributions, Mapping) or not isinstance(categories, Mapping):
        raise ValueError("cohort profile lacks distributions/categories")
    counts = {summary.get("count") for summary in distributions.values() if isinstance(summary, Mapping)}
    if len(counts) != 1:
        raise ValueError("cohort numeric distributions disagree on cardinality")
    count = next(iter(counts))
    if expected_count is not None and count != expected_count:
        raise ValueError("cohort profile count differs from roster cardinality")
    if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < count:
        raise ValueError("cohort slot is outside the represented population")

    numeric_values = {}
    for name, summary in sorted(distributions.items()):
        values = _moment_values(summary)
        order = _stable_slot_order(cohort_id, f"numeric:{name}", count)
        rank_by_slot = {target: rank for rank, target in enumerate(order)}
        numeric_values[name] = values[rank_by_slot[slot]]

    source_hash = hashlib.sha256(
        json.dumps(
            profile,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "representation": "rostered_cohort",
        "cohort_ref": cohort_id,
        "cohort_slot": slot,
        "source_profile_sha256": source_hash,
        "numeric_values": numeric_values,
        "category_values": {
            key: list(values)
            for key, values in _slot_categories(
                cohort_id=cohort_id,
                slot=slot,
                count=count,
                category_counts=categories,
            ).items()
        },
        "development": dict(profile.get("development") or {}),
    }
