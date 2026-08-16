"""Rules helpers for hosted inter-village Chunin Examination participation."""
from __future__ import annotations

import copy
import heapq
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _OwnerResolutionCache
from shinobi_runtime.commands import promotion_exam_attendance as attendance
from shinobi_runtime.commands import promotion_exam_finals as finals
from shinobi_runtime.commands import promotion_exam_integrity as integrity
from shinobi_runtime.commands import promotion_exam_scheduler as scheduler
from shinobi_runtime.sim.events import CampaignTime

_ROUTES = "state/world/routes-and-settlements.json"
_ORIGINAL_ELIGIBLE = None
_ORIGINAL_STAGE_FINALISTS = None


def bind_originals(*, eligible: Any, stage_finalists: Any) -> None:
    global _ORIGINAL_ELIGIBLE, _ORIGINAL_STAGE_FINALISTS
    if _ORIGINAL_ELIGIBLE is None:
        _ORIGINAL_ELIGIBLE = eligible
    if _ORIGINAL_STAGE_FINALISTS is None:
        _ORIGINAL_STAGE_FINALISTS = stage_finalists


def hosted_config(profile: Mapping[str, Any]) -> Mapping[str, Any] | None:
    config = profile.get("hosted_exam")
    if config is None:
        return None
    if not isinstance(config, Mapping):
        raise CommandRejectedError("promotion_exam_rules_invalid")
    host_village = config.get("host_village")
    host_arrival = config.get("host_arrival_place_ref")
    villages = config.get("participating_villages")
    delegations = config.get("foreign_delegations", [])
    if (
        not isinstance(host_village, str)
        or not host_village
        or not isinstance(host_arrival, str)
        or not host_arrival
        or not isinstance(villages, list)
        or not villages
        or any(not isinstance(value, str) or not value for value in villages)
        or len({value.lower() for value in villages}) != len(villages)
        or host_village.lower() not in {value.lower() for value in villages}
        or not isinstance(delegations, list)
    ):
        raise CommandRejectedError("promotion_exam_rules_invalid")
    for row in delegations:
        if not isinstance(row, Mapping):
            raise CommandRejectedError("promotion_exam_rules_invalid")
        required = ("delegation_ref", "service_village", "selection_authority_ref", "instructor_ref")
        if any(not isinstance(row.get(field), str) or not row.get(field) for field in required):
            raise CommandRejectedError("promotion_exam_rules_invalid")
        pool = row.get("candidate_pool_refs")
        if (
            not isinstance(pool, list)
            or not pool
            or any(not isinstance(ref, str) or not ref for ref in pool)
            or len(set(pool)) != len(pool)
            or row["service_village"].lower() not in {value.lower() for value in villages}
            or row["service_village"].lower() == host_village.lower()
        ):
            raise CommandRejectedError("promotion_exam_rules_invalid")
    return config


def _affiliation_matches(person: Mapping[str, Any], village: str) -> bool:
    affiliation = person.get("village_or_affiliation")
    return isinstance(affiliation, str) and village.lower() in affiliation.lower()


def person_matches_hosted_profile(person: Mapping[str, Any], profile: Mapping[str, Any]) -> bool:
    if person.get("schema") != "shinobi_character" or person.get("life_status") not in ("alive", "active"):
        return False
    source_rank = scheduler._rank_key(profile.get("source_rank"))
    if source_rank is None or scheduler._rank_key(person.get("official_rank_or_status")) != source_rank:
        return False
    config = hosted_config(profile)
    if config is None:
        village = profile.get("service_village")
        if not isinstance(village, str) or not _affiliation_matches(person, village):
            return False
    elif not any(_affiliation_matches(person, str(village)) for village in config["participating_villages"]):
        return False
    career = person.get("career_state")
    return isinstance(career, Mapping) and career.get("promotion_eligible") is True


def foreign_delegations(profile: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    config = hosted_config(profile)
    if config is None:
        return ()
    return tuple(row for row in config.get("foreign_delegations", ()) if isinstance(row, Mapping))


def delegation_by_team(profile: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(row["delegation_ref"]): row for row in foreign_delegations(profile)}


def candidate_home_location(person: Mapping[str, Any]) -> str | None:
    life = person.get("life_course_state")
    deployment = life.get("deployment") if isinstance(life, Mapping) else None
    home = deployment.get("home_location_id") if isinstance(deployment, Mapping) else None
    if isinstance(home, str) and home:
        return home
    current = person.get("current_location_id")
    return current if isinstance(current, str) and current else None


def arrival_lead_days(profile: Mapping[str, Any]) -> float:
    offsets = profile.get("phase_offsets_days")
    if not isinstance(offsets, Mapping):
        raise CommandRejectedError("promotion_exam_rules_invalid")
    registration = offsets.get("registration")
    qualification = offsets.get("qualification")
    if (
        isinstance(registration, bool)
        or not isinstance(registration, (int, float))
        or isinstance(qualification, bool)
        or not isinstance(qualification, (int, float))
        or qualification <= registration
    ):
        raise CommandRejectedError("promotion_exam_rules_invalid")
    return float(qualification) - float(registration)


def _route_graph(repository: Any) -> Mapping[str, list[tuple[str, float]]]:
    try:
        world = repository.read_json(_ROUTES)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("promotion_exam_attendance_route_invalid") from exc
    payload = world.get("payload") if isinstance(world, Mapping) else None
    routes = payload.get("routes") if isinstance(payload, Mapping) else None
    if not isinstance(routes, list):
        raise CommandRejectedError("promotion_exam_attendance_route_invalid")
    graph: dict[str, list[tuple[str, float]]] = {}
    for row in routes:
        if not isinstance(row, Mapping) or row.get("status") == "closed":
            continue
        start, end, band = row.get("from"), row.get("to"), row.get("travel_days_band")
        if (
            not isinstance(start, str)
            or not isinstance(end, str)
            or not isinstance(band, list)
            or len(band) != 2
            or isinstance(band[0], bool)
            or not isinstance(band[0], (int, float))
            or band[0] < 0
        ):
            continue
        weight = float(band[0])
        graph.setdefault(start, []).append((end, weight))
        graph.setdefault(end, []).append((start, weight))
    return graph


def minimum_route_days(repository: Any, from_location: str, to_location: str) -> float | None:
    start = attendance._place_anchor(repository, from_location)
    target = attendance._place_anchor(repository, to_location)
    if start == target:
        return 0.0
    graph = _route_graph(repository)
    queue: list[tuple[float, str]] = [(0.0, start)]
    best: dict[str, float] = {start: 0.0}
    while queue:
        cost, node = heapq.heappop(queue)
        if node == target:
            return cost
        if cost != best.get(node):
            continue
        for neighbor, weight in graph.get(node, ()):
            candidate = cost + weight
            if candidate < best.get(neighbor, float("inf")):
                best[neighbor] = candidate
                heapq.heappush(queue, (candidate, neighbor))
    return None


def append_location(subject: dict[str, Any], *, at: CampaignTime, location_ref: str, reason: str) -> None:
    subject["current_location_id"] = location_ref
    life = subject.get("life_course_state")
    if not isinstance(life, dict):
        raise CommandRejectedError("promotion_exam_candidate_location_invalid")
    history = life.get("location_history")
    changes = life.get("location_changes", 0)
    if not isinstance(history, list) or isinstance(changes, bool) or not isinstance(changes, int) or changes < 0:
        raise CommandRejectedError("promotion_exam_candidate_location_invalid")
    history.append({"at": str(at), "location_id": location_ref, "reason": reason})
    if len(history) > 128:
        del history[:-128]
    life["location_changes"] = changes + 1


def eligible_hosted_registrations(
    self: Any,
    *,
    profile: Mapping[str, Any],
    pipeline: Mapping[str, Any],
    cycle_id: str,
    player_id: str,
) -> list[dict[str, Any]]:
    if _ORIGINAL_ELIGIBLE is None:
        raise CommandRejectedError("promotion_exam_hosted_extension_invalid")
    result = list(_ORIGINAL_ELIGIBLE(self, profile=profile, pipeline=pipeline, cycle_id=cycle_id, player_id=player_id))
    config = hosted_config(profile)
    if config is None:
        return result
    registered = set(scheduler.registered_candidate_refs(pipeline, cycle_id))
    for row in result:
        registered.update(row.get("candidate_refs", ()))
    cache = _OwnerResolutionCache()
    host_place = str(config["host_arrival_place_ref"])
    lead_days = arrival_lead_days(profile)
    for delegation in foreign_delegations(profile):
        village = str(delegation["service_village"])
        candidates: list[str] = []
        for candidate_ref in delegation["candidate_pool_refs"]:
            if candidate_ref == player_id or candidate_ref in registered:
                continue
            try:
                _path, _digest, person = self._resolve_covered_owner_view(candidate_ref, cache=cache)
            except CommandRejectedError:
                continue
            if not isinstance(person, Mapping):
                continue
            condition = person.get("condition")
            home = candidate_home_location(person)
            route_days = minimum_route_days(self.repository, home, host_place) if isinstance(home, str) else None
            if (
                person_matches_hosted_profile(person, profile)
                and _affiliation_matches(person, village)
                and (not isinstance(condition, Mapping) or condition.get("readiness") in (None, "ready"))
                and route_days is not None
                and route_days <= lead_days
            ):
                candidates.append(candidate_ref)
        if candidates:
            result.append(
                {
                    "team_ref": str(delegation["delegation_ref"]),
                    "instructor_ref": str(delegation["instructor_ref"]),
                    "candidate_refs": sorted(set(candidates)),
                }
            )
            registered.update(candidates)
    return result


def stage_hosted_finalists(
    self: Any,
    *,
    pipeline: Mapping[str, Any],
    profile: Mapping[str, Any],
    cycle_id: str,
    at: CampaignTime,
    player_id: str,
    record_writes: dict[str, dict[str, Any]],
    allow_cross_country_reconciliation: bool = False,
) -> list[dict[str, Any]]:
    if _ORIGINAL_STAGE_FINALISTS is None:
        raise CommandRejectedError("promotion_exam_hosted_extension_invalid")
    config = hosted_config(profile)
    if config is None:
        return _ORIGINAL_STAGE_FINALISTS(
            self,
            pipeline=pipeline,
            profile=profile,
            cycle_id=cycle_id,
            at=at,
            player_id=player_id,
            record_writes=record_writes,
        )
    finals_config = profile.get("finals_format")
    venue_ref = finals_config.get("venue_ref") if isinstance(finals_config, Mapping) else None
    if not isinstance(venue_ref, str) or not venue_ref:
        raise CommandRejectedError("promotion_exam_finals_rules_invalid")
    venue_anchor = attendance._place_anchor(self.repository, venue_ref)
    team_by_candidate, instructor_by_candidate = integrity._registration_team_map(pipeline, cycle_id)
    delegation_map = delegation_by_team(profile)
    cache = _OwnerResolutionCache()
    staged: list[dict[str, Any]] = []
    for candidate_ref in finals.promotion_exam_finals_candidate_refs(pipeline, cycle_id):
        if instructor_by_candidate.get(candidate_ref) == player_id:
            continue
        try:
            path, _digest, view = self._resolve_covered_owner_view(candidate_ref, cache=cache)
        except CommandRejectedError as exc:
            raise CommandRejectedError("promotion_exam_candidate_unresolved") from exc
        subject = record_writes.get(path)
        if subject is None:
            subject = copy.deepcopy(dict(view)) if isinstance(view, Mapping) else None
        if not isinstance(subject, dict) or subject.get("life_status") not in ("active", "alive"):
            continue
        current_location = subject.get("current_location_id")
        if not isinstance(current_location, str) or not current_location:
            raise CommandRejectedError("promotion_exam_candidate_location_invalid")
        if current_location == venue_ref:
            continue
        same_host = attendance._place_anchor(self.repository, current_location) == venue_anchor
        delegation = delegation_map.get(team_by_candidate.get(candidate_ref, ""))
        if not same_host:
            if delegation is None or not allow_cross_country_reconciliation:
                raise CommandRejectedError("promotion_exam_finalist_not_locally_reachable")
            if minimum_route_days(self.repository, current_location, venue_ref) is None:
                raise CommandRejectedError("promotion_exam_finalist_not_host_reachable")
            reason = "guarded reconciliation of omitted hosted inter-village Chunin Examination travel"
        else:
            reason = "scheduled local attendance for hosted Chunin Examination finals"
        append_location(subject, at=at, location_ref=venue_ref, reason=reason)
        record_writes[path] = subject
        staged.append({"candidate_ref": candidate_ref, "from_location_ref": current_location, "to_location_ref": venue_ref, "path": path})
    return staged


__all__ = [
    "append_location",
    "arrival_lead_days",
    "bind_originals",
    "candidate_home_location",
    "delegation_by_team",
    "eligible_hosted_registrations",
    "foreign_delegations",
    "hosted_config",
    "minimum_route_days",
    "person_matches_hosted_profile",
    "stage_hosted_finalists",
]
