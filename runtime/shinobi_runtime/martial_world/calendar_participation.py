"""Player-visible and exact-observed participation in recurring world events.

Calendar rows themselves are deterministic static occurrences, not persistent
save objects.  Unobserved crowds remain aggregate.  When the player is actually
at an eligible site, persistent people who would attend are derived from their
real settlement, health, availability, role, and a stable attendance decision.
That gives the scene exact interactable NPCs without materializing thousands of
festival civilians or maintaining daily schedules.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any, Mapping, Sequence

from .events import active_calendar_occurrences, calendar_event_occurrence
from .social_presence import person_settlement


def calendar_event_ref(occurrence: Mapping[str, Any]) -> str:
    event_id = str(occurrence.get("event_id") or "")
    event_date = str(occurrence.get("date") or "")
    if not event_id or not event_date:
        raise ValueError("calendar occurrence missing identity")
    return f"calendar:{event_id}:{event_date}"


def parse_calendar_event_ref(event_ref: str) -> tuple[str, date]:
    if not isinstance(event_ref, str) or not event_ref.startswith("calendar:"):
        raise ValueError("calendar event ref invalid")
    body = event_ref[len("calendar:"):]
    try:
        event_id, raw_date = body.rsplit(":", 1)
        day = date.fromisoformat(raw_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("calendar event ref invalid") from exc
    if not event_id:
        raise ValueError("calendar event ref invalid")
    return event_id, day


def occurrence_for_ref(event_ref: str) -> dict[str, Any] | None:
    event_id, day = parse_calendar_event_ref(event_ref)
    row = calendar_event_occurrence(event_id, day.year)
    if row is None or str(row.get("date") or "") != day.isoformat():
        return None
    return row


def occurrence_active_at(occurrence: Mapping[str, Any], at: datetime) -> bool:
    try:
        start = date.fromisoformat(str(occurrence.get("date") or ""))
        end = date.fromisoformat(str(occurrence.get("ends_on") or occurrence.get("date") or ""))
    except ValueError:
        return False
    return start <= at.date() <= end


def _site_place(site_ref: str, sites: Mapping[str, Any]) -> str:
    site = sites.get(site_ref)
    return str(site.get("parent_place_ref") or "") if isinstance(site, Mapping) else ""


def _eligible_site_refs(
    occurrence: Mapping[str, Any], *, current_place_ref: str,
    player_faction_ref: str, player_faction_headquarters: str,
    sites: Mapping[str, Any],
) -> list[str]:
    participation = occurrence.get("participation", {})
    if not isinstance(participation, Mapping):
        return []
    scope = str(participation.get("scope") or "")
    host_place = str(occurrence.get("host_place_id") or "")
    if scope == "host_place" and (not host_place or current_place_ref != host_place):
        return []
    if scope == "systemic":
        return []
    allowed_types = {
        str(x) for x in participation.get("site_types", [])
        if isinstance(x, str) and x
    } if isinstance(participation.get("site_types"), list) else set()
    if scope == "own_faction_headquarters":
        if not player_faction_ref or not player_faction_headquarters:
            return []
        site = sites.get(player_faction_headquarters)
        if not isinstance(site, Mapping):
            return []
        if str(site.get("parent_place_ref") or "") != current_place_ref:
            return []
        if allowed_types and str(site.get("site_type") or "") not in allowed_types:
            return []
        return [player_faction_headquarters]
    rows: list[str] = []
    for site_ref, site in sites.items():
        if not isinstance(site_ref, str) or not isinstance(site, Mapping):
            continue
        if str(site.get("parent_place_ref") or "") != current_place_ref:
            continue
        if allowed_types and str(site.get("site_type") or "") not in allowed_types:
            continue
        rows.append(site_ref)
    return sorted(rows)


def active_event_opportunities(
    *, at: datetime, player_site_ref: str, player_faction_ref: str,
    player_faction_headquarters: str, sites: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project active calendar events and exact local participation surfaces."""
    current_place = _site_place(player_site_ref, sites)
    out: list[dict[str, Any]] = []
    for occurrence in active_calendar_occurrences(at.date()):
        participation = occurrence.get("participation", {})
        if not isinstance(participation, Mapping):
            continue
        modes = [str(x) for x in participation.get("player_modes", []) if isinstance(x, str)] if isinstance(participation.get("player_modes"), list) else []
        command_hints = [str(x) for x in participation.get("command_hints", []) if isinstance(x, str)] if isinstance(participation.get("command_hints"), list) else []
        scope = str(participation.get("scope") or "")
        eligible = _eligible_site_refs(
            occurrence, current_place_ref=current_place,
            player_faction_ref=player_faction_ref,
            player_faction_headquarters=player_faction_headquarters,
            sites=sites,
        )
        local_available = player_site_ref in eligible
        row: dict[str, Any] = {
            "event_ref": calendar_event_ref(occurrence),
            "event_id": str(occurrence.get("event_id") or ""),
            "display_name": str(occurrence.get("display_name") or occurrence.get("event_id") or ""),
            "starts_on": str(occurrence.get("date") or ""),
            "ends_on": str(occurrence.get("ends_on") or occurrence.get("date") or ""),
            "scope": scope,
            "player_modes": modes,
            "command_hints": command_hints,
            "local_available": bool(local_available),
        }
        host = occurrence.get("host_place_id")
        if isinstance(host, str) and host:
            row["host_place_id"] = host
        if eligible:
            row["eligible_site_refs"] = eligible[:32]
        if local_available and "jianghu_calendar_event_resolution" in command_hints:
            row["calendar_event_command_available"] = True
        effect = occurrence.get("simulation_effect")
        if isinstance(effect, str) and effect:
            row["simulation_effect"] = effect
        out.append(row)
    return out


def _stable_milli(*parts: object) -> int:
    raw = "|".join(str(x) for x in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:4], "big") % 1000


def _martial_capability(person: Mapping[str, Any]) -> int:
    martial = person.get("martial_skills", {}) if isinstance(person.get("martial_skills"), Mapping) else {}
    return max((max(0, int(value)) for value in martial.values() if isinstance(value, int) and not isinstance(value, bool)), default=0)


def person_attends_calendar_event(
    person: Mapping[str, Any], *, occurrence: Mapping[str, Any], site_ref: str,
    site: Mapping[str, Any], faction_headquarters: str, sites: Mapping[str, Any],
    at: datetime, unavailable_refs: set[str] | frozenset[str] = frozenset(),
    player_faction_ref: str = "",
) -> bool:
    """Whether one exact persistent person attends an observed calendar event."""
    if not occurrence_active_at(occurrence, at):
        return False
    pid = str(person.get("person_id") or "")
    if not pid or pid in unavailable_refs:
        return False
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    if health.get("status") in {"dead", "incapacitated"} or int(health.get("consciousness", 100)) <= 0:
        return False
    participation = occurrence.get("participation", {})
    if not isinstance(participation, Mapping):
        return False
    allowed_types = {
        str(x) for x in participation.get("site_types", [])
        if isinstance(x, str) and x
    } if isinstance(participation.get("site_types"), list) else set()
    if allowed_types and str(site.get("site_type") or "") not in allowed_types:
        return False
    parent = str(site.get("parent_place_ref") or "")
    if not parent or person_settlement(person, faction_headquarters=faction_headquarters, sites=sites) != parent:
        return False
    scope = str(participation.get("scope") or "")
    if scope == "host_place" and str(occurrence.get("host_place_id") or "") != parent:
        return False
    if scope == "own_faction_headquarters" and (
        not player_faction_ref or str(person.get("faction_ref") or "") != player_faction_ref
    ):
        return False
    event_id = str(occurrence.get("event_id") or "")
    martial_events = {
        "regional_martial_tournament", "great_jianghu_tournament", "midyear_junior_tournament",
        "winter_martial_lectures", "lantern_city_martial_exhibitions", "winter_school_challenge_meets",
    }
    if event_id in martial_events and _martial_capability(person) <= 0:
        return False
    if event_id in {"spring_escort_exchange", "autumn_escort_exchange"}:
        prof = person.get("professional_skills", {}) if isinstance(person.get("professional_skills"), Mapping) else {}
        martial = person.get("martial_skills", {}) if isinstance(person.get("martial_skills"), Mapping) else {}
        useful = max(
            int(prof.get("commerce", 0)), int(prof.get("administration", 0)),
            int(martial.get("command", 0)), int(martial.get("stealth_scouting", 0)), _martial_capability(person),
        )
        if useful <= 0:
            return False
    base = max(0, min(1000, int(participation.get("npc_attendance_milli", 0))))
    if base <= 0:
        return False
    grade = str(person.get("membership_grade") or "")
    grade_bonus = {"elder": 140, "elite": 100, "senior": 70, "full": 30, "junior": 20}.get(grade, 0)
    offices = {str(x).split(":", 1)[0] for x in person.get("standing_offices", []) if isinstance(x, str)}
    office_bonus = 100 if offices & {"leader", "deputy_leader", "chief_martial_instructor", "field_commander"} else 0
    threshold = min(950, base + grade_bonus + office_bonus)
    return _stable_milli(event_id, occurrence.get("date"), pid, site_ref) < threshold


def derived_calendar_event_attendance(
    *, occurrence: Mapping[str, Any], site_ref: str, site: Mapping[str, Any],
    faction_people: Sequence[tuple[str, Sequence[Mapping[str, Any]]]],
    faction_headquarters: Mapping[str, str], sites: Mapping[str, Any], at: datetime,
    unavailable_refs: set[str] | frozenset[str] = frozenset(),
    exclude_refs: set[str] | frozenset[str] = frozenset(),
    player_faction_ref: str = "", civic_people: Sequence[Mapping[str, Any]] = (),
    limit: int | None = None,
) -> list[str]:
    rows: list[tuple[int, str]] = []
    for faction_ref, people in faction_people:
        hq = str(faction_headquarters.get(faction_ref) or "")
        for person in people:
            if not isinstance(person, Mapping):
                continue
            pid = str(person.get("person_id") or "")
            if not pid or pid in exclude_refs:
                continue
            if person_attends_calendar_event(
                person, occurrence=occurrence, site_ref=site_ref, site=site,
                faction_headquarters=hq, sites=sites, at=at,
                unavailable_refs=unavailable_refs, player_faction_ref=player_faction_ref,
            ):
                grade = str(person.get("membership_grade") or "")
                standing = {"elder": 5, "elite": 4, "senior": 3, "full": 2, "junior": 1}.get(grade, 0)
                rows.append((-standing, pid))
    for person in civic_people:
        if not isinstance(person, Mapping):
            continue
        pid = str(person.get("person_id") or "")
        if not pid or pid in exclude_refs:
            continue
        if person_attends_calendar_event(
            person, occurrence=occurrence, site_ref=site_ref, site=site,
            faction_headquarters=str(person.get("home_place_ref") or ""), sites=sites, at=at,
            unavailable_refs=unavailable_refs, player_faction_ref=player_faction_ref,
        ):
            rank = {"imperial": 8, "high_official": 7, "noble": 6, "regional_official": 5, "local_elite": 4}.get(str(person.get("social_rank") or ""), 3)
            rows.append((-rank, pid))
    rows.sort()
    result = [pid for _standing, pid in rows]
    return result if limit is None else result[:max(0, int(limit))]


__all__ = [
    "active_event_opportunities",
    "calendar_event_ref",
    "derived_calendar_event_attendance",
    "occurrence_active_at",
    "occurrence_for_ref",
    "parse_calendar_event_ref",
    "person_attends_calendar_event",
]
