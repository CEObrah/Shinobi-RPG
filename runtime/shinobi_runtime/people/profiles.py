"""Persistent lightweight person profiles inside bounded identity registries.

A saved profile is individual authority. Cohorts and training sections may
summarize those people for scheduling and simulation, but changing a cohort
never rerolls an established person.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Dict, Mapping, MutableMapping, Sequence

PROFILE_SCHEMA = "person-core-registry"


def numeric_paths(registry: Mapping[str, Any]) -> tuple[str, ...]:
    if registry.get("schema") != PROFILE_SCHEMA:
        raise ValueError("rostered progression registry schema invalid")
    paths = registry.get("profile_numeric_paths")
    if not isinstance(paths, list) or not paths or any(not isinstance(x, str) or not x for x in paths) or len(paths) != len(set(paths)):
        raise ValueError("rostered progression numeric path table invalid")
    return tuple(paths)


def profile_entry_for(registry: Mapping[str, Any], person_ref: str) -> Mapping[str, Any] | None:
    paths = numeric_paths(registry)
    people = registry.get("profiles")
    if not isinstance(people, Mapping):
        raise ValueError("rostered progression profiles invalid")
    entry = people.get(person_ref)
    if entry is None:
        return None
    if not isinstance(entry, Mapping) or entry.get("person_ref") != person_ref:
        raise ValueError("rostered progression entry invalid")
    values = entry.get("numeric_values")
    if not isinstance(values, list) or len(values) != len(paths) or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)) for v in values):
        raise ValueError("rostered progression numeric values invalid")
    return entry


def numeric_map(registry: Mapping[str, Any], entry: Mapping[str, Any]) -> Dict[str, float]:
    paths = numeric_paths(registry)
    values = entry.get("numeric_values")
    if not isinstance(values, list) or len(values) != len(paths):
        raise ValueError("rostered progression numeric values invalid")
    return {path: float(value) for path, value in zip(paths, values)}


def set_numeric(registry: Mapping[str, Any], entry: MutableMapping[str, Any], path: str, value: float) -> None:
    paths = numeric_paths(registry)
    try:
        index = paths.index(path)
    except ValueError as exc:
        raise ValueError(f"unregistered rostered numeric path: {path}") from exc
    values = entry.get("numeric_values")
    if not isinstance(values, list) or len(values) != len(paths):
        raise ValueError("rostered progression numeric values invalid")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError("rostered progression numeric value invalid")
    values[index] = round(float(value), 9)


def apply_rostered_profile(registry: Mapping[str, Any], entry: Mapping[str, Any]) -> Mapping[str, Any]:
    institutional = entry.get("institutional_progression")
    categories = entry.get("category_values")
    if not isinstance(institutional, Mapping) or not isinstance(categories, Mapping):
        raise ValueError("rostered progression payload invalid")
    return {
        "representation": "rostered_individual",
        "cohort_ref": entry.get("cohort_ref"),
        "numeric_values": numeric_map(registry, entry),
        "category_values": copy.deepcopy(dict(categories)),
        "institutional_progression": copy.deepcopy(dict(institutional)),
    }


def numeric_summary(values: Sequence[float]) -> Mapping[str, Any]:
    if not values:
        raise ValueError("numeric summary requires values")
    nums = [float(value) for value in values]
    if any(not math.isfinite(value) for value in nums):
        raise ValueError("numeric summary contains non-finite value")
    mean = sum(nums) / len(nums)
    spread = math.sqrt(sum((value - mean) ** 2 for value in nums) / len(nums))
    return {"count": len(nums), "mean": round(mean, 9), "sd": round(spread, 9), "min": round(min(nums), 9), "max": round(max(nums), 9)}


def category_counts(entries: Sequence[Mapping[str, Any]]) -> Mapping[str, int]:
    counts: Dict[str, int] = {}
    for entry in entries:
        categories = entry.get("category_values")
        if not isinstance(categories, Mapping):
            raise ValueError("rostered category values invalid")
        for group, values in categories.items():
            if not isinstance(group, str) or not isinstance(values, list):
                raise ValueError("rostered category values invalid")
            for value in values:
                if not isinstance(value, str) or not value:
                    raise ValueError("rostered category label invalid")
                key = f"{group}:{value}"
                counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def refresh_section_summary(registry: Mapping[str, Any], cohort: MutableMapping[str, Any]) -> None:
    refs = cohort.get("roster_refs")
    profile = cohort.get("cohort_profile")
    people = registry.get("profiles")
    if not isinstance(refs, list) or not isinstance(profile, MutableMapping) or not isinstance(people, Mapping):
        raise ValueError("rostered training section invalid")
    if not refs:
        cohort["aggregate_count"] = 0
        profile["numeric_distributions"] = {}
        profile["category_counts"] = {}
        return
    entries = [profile_entry_for(registry, ref) for ref in refs]
    if any(entry is None for entry in entries):
        raise ValueError("rostered training section references unprofiled person")
    typed = [entry for entry in entries if isinstance(entry, Mapping)]
    distributions = profile.get("numeric_distributions")
    if not isinstance(distributions, MutableMapping):
        raise ValueError("rostered training section distributions invalid")
    # The summary is a cache over saved individual capability paths.  Rebuild
    # it rather than preserving stale cohort-authority fields such as age.
    distributions.clear()
    for path in numeric_paths(registry):
        distributions[path] = dict(numeric_summary([numeric_map(registry, entry)[path] for entry in typed]))
    profile["category_counts"] = dict(category_counts(typed))
    cohort["aggregate_count"] = len(refs)


def record_rostered_field_evidence(
    entry: MutableMapping[str, Any],
    *,
    evidence_ref: str,
    kind: str,
    exchanges: int = 0,
    method_ref: str | None = None,
    evidence_units: float = 1.0,
    domains: Sequence[str] = (),
    at: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> None:
    """Accumulate bounded field evidence on an individual-lite profile.

    Evidence is not a stat award. It lives in the profile's existing residual
    development bank until later institutional training consolidates part of it.
    One evidence_ref is counted once so representation retries cannot farm growth.
    """
    institutional = entry.get("institutional_progression")
    if not isinstance(institutional, MutableMapping):
        raise ValueError("rostered institutional progression invalid")
    residuals = institutional.get("development_residual_units")
    if not isinstance(residuals, MutableMapping):
        raise ValueError("rostered development residuals invalid")
    history = institutional.setdefault("service_history", [])
    if not isinstance(history, list):
        raise ValueError("rostered service history invalid")
    if any(isinstance(row, Mapping) and row.get("evidence_ref") == evidence_ref for row in history):
        return
    units = max(1.0, float(evidence_units))
    if kind == "combat":
        residuals["field.combat_events"] = float(residuals.get("field.combat_events", 0)) + 1.0
        residuals["field.combat_exchanges"] = float(residuals.get("field.combat_exchanges", 0)) + float(max(1, exchanges))
    elif kind == "mission":
        residuals["field.mission_events"] = float(residuals.get("field.mission_events", 0)) + units
    else:
        raise ValueError("unsupported rostered field evidence kind")
    if isinstance(method_ref, str) and method_ref:
        key = f"field.method.{method_ref}"
        residuals[key] = float(residuals.get(key, 0)) + float(max(1, exchanges or 1))
    row: Dict[str, Any] = {
        "evidence_ref": evidence_ref,
        "kind": kind,
        "method_ref": method_ref,
        "exchanges": max(0, int(exchanges)),
        "evidence_units": units,
        "domains": sorted({str(x) for x in domains if isinstance(x, str) and x}),
    }
    if isinstance(at, str) and at:
        row["at"] = at
    if isinstance(details, Mapping):
        for key, value in details.items():
            if isinstance(key, str) and key not in row:
                row[key] = copy.deepcopy(value)
    history.append(row)
    del history[:-48]


def update_standing(entry: MutableMapping[str, Any], *, standing: str, technical_tier: str, package_ref: str, at: str, reason: str) -> None:
    institutional = entry.get("institutional_progression")
    categories = entry.get("category_values")
    if not isinstance(institutional, MutableMapping) or not isinstance(categories, MutableMapping):
        raise ValueError("rostered institutional progression invalid")
    previous = institutional.get("standing")
    institutional["standing"] = standing
    institutional["technical_tier"] = technical_tier
    institutional["training_package_refs"] = [package_ref]
    history = institutional.setdefault("promotion_history", [])
    if not isinstance(history, list):
        raise ValueError("promotion history invalid")
    history.append({"at": at, "from": previous, "to": standing, "reason": reason})
    del history[:-24]
    categories["rank"] = [standing]
    categories["package"] = [package_ref]


__all__ = ["PROFILE_SCHEMA", "apply_rostered_profile", "category_counts", "numeric_map", "numeric_paths", "numeric_summary", "profile_entry_for", "record_rostered_field_evidence", "refresh_section_summary", "set_numeric", "update_standing"]
