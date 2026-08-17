"""Deterministic action modifiers derived from current environment.

The environment remains read-only. Static action policy explicitly decides which
derived channels can affect which activities; unlisted actions are neutral.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from shinobi_runtime.environment import environment_snapshot
from shinobi_runtime.sim.events import CampaignTime

_RULES_PATH = "game/data/mechanics/environment-actions.json"


def _seconds_between(start: CampaignTime, end: CampaignTime) -> int:
    if end < start:
        raise ValueError("environment action interval ends before it starts")
    a = datetime(start.year, start.month, start.day, start.hour, start.minute, start.second)
    b = datetime(end.year, end.month, end.day, end.hour, end.minute, end.second)
    return int((b - a).total_seconds())


def _rules(repository: Any) -> Mapping[str, Any]:
    record = repository.read_json(_RULES_PATH)
    if (
        not isinstance(record, Mapping)
        or record.get("schema") != "environment-action-rules"
        or record.get("version") != 1
        or not isinstance(record.get("actions"), Mapping)
    ):
        raise ValueError("environment action rules are invalid")
    return record


def environment_action_profile(
    repository: Any,
    *,
    start_time: CampaignTime,
    end_time: CampaignTime,
    place_ref: str,
    action_key: str,
) -> dict[str, Any]:
    """Return one bounded deterministic modifier profile for an authored action."""

    if not isinstance(place_ref, str) or not place_ref or not isinstance(action_key, str) or not action_key:
        raise ValueError("environment action input is invalid")
    rules = _rules(repository)
    actions = rules["actions"]
    config = actions.get(action_key)
    if config is None:
        return {
            "action_key": action_key,
            "applied": False,
            "factor_milli": 1000,
            "sample_count": 0,
            "channels": [],
        }
    if not isinstance(config, Mapping):
        raise ValueError("environment action config is invalid")
    channels = config.get("channels")
    minimum = config.get("minimum_factor_milli")
    maximum = config.get("maximum_factor_milli")
    sample_count = rules.get("interval_sample_count")
    if (
        not isinstance(channels, Mapping)
        or not channels
        or isinstance(minimum, bool)
        or not isinstance(minimum, int)
        or isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or minimum <= 0
        or maximum < minimum
        or isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
        or not 2 <= sample_count <= 16
    ):
        raise ValueError("environment action config is invalid")

    channel_specs: list[tuple[str, int, str]] = []
    for channel, raw in sorted(channels.items()):
        if not isinstance(channel, str) or not isinstance(raw, Mapping):
            raise ValueError("environment action channel is invalid")
        weight = raw.get("weight")
        polarity = raw.get("polarity")
        if (
            isinstance(weight, bool)
            or not isinstance(weight, int)
            or weight <= 0
            or polarity not in ("direct", "inverse")
        ):
            raise ValueError("environment action channel is invalid")
        channel_specs.append((channel, weight, str(polarity)))

    total_seconds = _seconds_between(start_time, end_time)
    sample_times: list[CampaignTime] = []
    for index in range(sample_count):
        offset = (total_seconds * index) // (sample_count - 1)
        candidate = start_time.add_seconds(offset)
        if not sample_times or candidate != sample_times[-1]:
            sample_times.append(candidate)

    sample_factors: list[int] = []
    for sample_time in sample_times:
        snapshot = environment_snapshot(
            repository,
            world_time=str(sample_time),
            location_ref=place_ref,
        )
        effects = snapshot.get("mechanical_effects") if isinstance(snapshot, Mapping) else None
        if not isinstance(effects, Mapping):
            raise ValueError("environment action snapshot lacks mechanical effects")
        weighted = 0
        total_weight = 0
        for channel, weight, polarity in channel_specs:
            value = effects.get(channel)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("environment action channel is unavailable")
            adjusted = value if polarity == "direct" else max(0, 2000 - value)
            weighted += adjusted * weight
            total_weight += weight
        if total_weight <= 0:
            raise ValueError("environment action weights are invalid")
        sample_factors.append((weighted + total_weight // 2) // total_weight)

    factor = (sum(sample_factors) + len(sample_factors) // 2) // len(sample_factors)
    factor = max(minimum, min(maximum, factor))
    return {
        "action_key": action_key,
        "applied": True,
        "factor_milli": factor,
        "sample_count": len(sample_times),
        "channels": [channel for channel, _weight, _polarity in channel_specs],
        "sampled_from": str(start_time),
        "sampled_through": str(end_time),
    }


__all__ = ["environment_action_profile"]
