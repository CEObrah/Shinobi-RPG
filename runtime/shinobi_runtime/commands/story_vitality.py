"""Player-facing salience for consequential downtime results.

The causal reducers remain the only authority for what actually happens. This
module only widens the set of already-committed consequences that count as a
story handoff so long-running downtime does not silently skip House promotions
or a matured Sword Manor recruitment response window.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Mapping

_INSTALLED = False
_OUTREACH_PREFIX = "commitment.outreach."


def _house_promotion_rows(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = result.get("house_rostered_promotion_reviews")
    if not isinstance(raw, list):
        return []
    return [
        row
        for row in raw
        if isinstance(row, Mapping) and row.get("promoted") is True
    ]


def _outreach_review_rows(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = result.get("commitment_reviews")
    if not isinstance(raw, list):
        return []
    return [
        row
        for row in raw
        if isinstance(row, Mapping)
        and isinstance(row.get("commitment_id"), str)
        and str(row.get("commitment_id")).startswith(_OUTREACH_PREFIX)
        and row.get("status") == "overdue"
    ]


def _house_training_progressed(result: Mapping[str, Any]) -> bool:
    raw = result.get("house_rostered_individual_progression")
    if not isinstance(raw, list):
        return False
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        outcomes = row.get("outcomes")
        if isinstance(outcomes, Mapping):
            for outcome in outcomes.values():
                if isinstance(outcome, Mapping):
                    gained = outcome.get("points_gained")
                    if isinstance(gained, int) and not isinstance(gained, bool) and gained > 0:
                        return True
        technique = row.get("technique_result")
        if isinstance(technique, Mapping):
            gained = technique.get("points_gained")
            if isinstance(gained, int) and not isinstance(gained, bool) and gained > 0:
                return True
    return False


def _result_has_story_event(result: Mapping[str, Any]) -> bool:
    return bool(_house_promotion_rows(result) or _outreach_review_rows(result))


def install_story_vitality() -> None:
    """Install one shared salience extension for time handoffs and event seeking."""

    global _INSTALLED
    if _INSTALLED:
        return

    from shinobi_runtime.commands import campaign_runtime_planner as runtime_module
    from shinobi_runtime.commands import downtime_until_event as downtime_module

    original_projection = runtime_module._fresh_player_facing_time_handoff
    if not getattr(original_projection, "_story_vitality_projection", False):
        @wraps(original_projection)
        def projection_wrapped(
            result: Mapping[str, Any],
        ) -> tuple[list[str], list[str], list[str]]:
            pressures, reports, approaching = original_projection(result)

            promotions = _house_promotion_rows(result)
            if promotions:
                count = len(promotions)
                pressure = (
                    f"House Tang has completed an operating review with {count} "
                    f"rostered standing promotion{'s' if count != 1 else ''}."
                )
                if pressure not in pressures:
                    pressures.append(pressure)
                details = []
                for row in promotions[:6]:
                    member = row.get("member_ref")
                    source = row.get("from")
                    target = row.get("to")
                    if all(isinstance(value, str) and value for value in (member, source, target)):
                        details.append(f"{member}: {source} -> {target}")
                report = "House Tang promotion review completed."
                if details:
                    report += " " + "; ".join(details) + "."
                if report not in reports:
                    reports.append(report)

            outreach = _outreach_review_rows(result)
            if outreach:
                pressure = (
                    "Sword Manor's external recruitment response window has matured; "
                    "the voluntary applicant pools are ready for lawful review."
                )
                if pressure not in pressures:
                    pressures.append(pressure)
                report = (
                    "House Tang recruitment outreach has reached its scheduled response review. "
                    "No applicant is accepted until the authorized intake review is resolved."
                )
                if report not in reports:
                    reports.append(report)

            if _house_training_progressed(result):
                summary = (
                    "House Tang's standing field-readiness cycle has produced persisted "
                    "rostered training progress."
                )
                if summary not in approaching:
                    approaching.append(summary)

            return pressures[:12], reports[:6], approaching[:8]

        projection_wrapped._story_vitality_projection = True  # type: ignore[attr-defined]
        runtime_module._fresh_player_facing_time_handoff = projection_wrapped

    original_event_classifier = downtime_module._player_facing_event
    if not getattr(original_event_classifier, "_story_vitality_classifier", False):
        @wraps(original_event_classifier)
        def event_classifier_wrapped(result: Mapping[str, Any]) -> bool:
            return original_event_classifier(result) or _result_has_story_event(result)

        event_classifier_wrapped._story_vitality_classifier = True  # type: ignore[attr-defined]
        downtime_module._player_facing_event = event_classifier_wrapped

    _INSTALLED = True


__all__ = [
    "install_story_vitality",
    "_house_promotion_rows",
    "_outreach_review_rows",
    "_result_has_story_event",
]
