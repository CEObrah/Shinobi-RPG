"""Bounded service development for active exact named shinobi without team training.

Exact-team training remains the preferred owner. This layer gives active named
shinobi who have no lawful exact-team training owner a modest service-development
opportunity at person-continuity boundaries. It composes through the existing
character development bank and training reducer; it never creates rank changes
or breakthrough-band capability.
"""
from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal
from functools import wraps
from typing import Any, Dict, Mapping, MutableMapping, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _campaign_datetime, _json_bytes
from shinobi_runtime.commands.global_team_training_load import _team_records
from shinobi_runtime.commands.paths import DEVELOPMENT_BANK_PATH
from shinobi_runtime.commands.domains.time import TimeCommandsMixin
from shinobi_runtime.reducers import TrainingInputs, settle_training
from shinobi_runtime.sim.events import CampaignTime

_MECHANICS = "game/data/mechanics/training.json"
_OWNER_INDEXES = ("state/index/owners/canon.json", "state/index/owners/char.json")
_INSTALLED = False
_SECONDS_PER_DAY = Decimal(86400)


def _policy(repository: Any) -> Mapping[str, Any]:
    try:
        mechanics = repository.read_json(_MECHANICS)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("training_mechanics_invalid") from exc
    policy = mechanics.get("named_service_development") if isinstance(mechanics, Mapping) else None
    if not isinstance(policy, Mapping):
        raise CommandRejectedError("training_mechanics_invalid")
    try:
        monthly = Decimal(str(policy.get("active_hours_per_month")))
        weekly = Decimal(str(policy.get("historical_hours_per_full_week")))
    except Exception as exc:
        raise CommandRejectedError("training_mechanics_invalid") from exc
    cycle = policy.get("target_cycle")
    tokens = policy.get("eligible_status_tokens")
    if (
        not monthly.is_finite() or monthly <= 0 or monthly > 48 * 4
        or not weekly.is_finite() or weekly <= 0 or weekly > 48
        or not isinstance(cycle, list) or not cycle
        or any(not isinstance(value, str) or not value for value in cycle)
        or not isinstance(tokens, list) or not tokens
        or any(not isinstance(value, str) or not value for value in tokens)
    ):
        raise CommandRejectedError("training_mechanics_invalid")
    return policy


def _owner_paths(repository: Any) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for index_path in _OWNER_INDEXES:
        try:
            index = repository.read_json(index_path)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("named_service_owner_index_invalid") from exc
        owners = index.get("owners") if isinstance(index, Mapping) else None
        if not isinstance(owners, Mapping):
            raise CommandRejectedError("named_service_owner_index_invalid")
        for owner_ref, path in owners.items():
            if isinstance(owner_ref, str) and isinstance(path, str) and owner_ref and path:
                result[owner_ref] = path
    return result


def _active_team_training_members(repository: Any, *, record_writes: Mapping[str, Mapping[str, Any]] | None = None) -> set[str]:
    result: set[str] = set()
    for team in _team_records(repository, record_writes=record_writes):
        training = team.get("training")
        members = team.get("member_refs")
        instructors = training.get("instructor_refs") if isinstance(training, Mapping) else None
        if (
            team.get("status") == "active"
            and isinstance(training, Mapping)
            and training.get("model_ref") == "training.team"
            and isinstance(instructors, list)
            and instructors
            and isinstance(members, list)
        ):
            result.update(ref for ref in members if isinstance(ref, str))
    return result


def _qualifying_status(person: Mapping[str, Any], policy: Mapping[str, Any]) -> bool:
    if person.get("schema") != "shinobi_character" or person.get("life_status") not in {"active", "alive"}:
        return False
    condition = person.get("condition")
    if isinstance(condition, Mapping) and condition.get("readiness") not in {None, "ready"}:
        return False
    raw = person.get("official_rank_or_status") or person.get("current_rank_or_status")
    if not isinstance(raw, str):
        career = person.get("career_state")
        raw = career.get("rank") if isinstance(career, Mapping) else None
    if not isinstance(raw, str):
        return False
    status = raw.casefold().replace("ū", "u").replace("ō", "o")
    return any(str(token).casefold() in status for token in policy["eligible_status_tokens"])


def _record_time(value: object) -> CampaignTime | None:
    if not isinstance(value, str):
        return None
    try:
        return CampaignTime.parse(value)
    except (TypeError, ValueError):
        return None


def service_start(person: Mapping[str, Any], fallback: CampaignTime, policy: Mapping[str, Any]) -> CampaignTime:
    """Earliest exact active-service boundary supported by the saved person record."""
    candidates = [fallback]
    life = person.get("life_course_state")
    if isinstance(life, Mapping):
        locations = life.get("location_history")
        if isinstance(locations, list) and locations:
            first = locations[0]
            at = _record_time(first.get("at") if isinstance(first, Mapping) else None)
            if at is not None:
                candidates.append(at)
        ranks = life.get("rank_history")
        if isinstance(ranks, list):
            qualifying: list[CampaignTime] = []
            for row in ranks:
                if not isinstance(row, Mapping):
                    continue
                rank = row.get("rank")
                if not isinstance(rank, str):
                    continue
                status = rank.casefold().replace("ū", "u").replace("ō", "o")
                if not any(str(token).casefold() in status for token in policy["eligible_status_tokens"]):
                    continue
                at = _record_time(row.get("at"))
                if at is not None:
                    qualifying.append(at)
            if qualifying:
                candidates.append(min(qualifying))
    return max(candidates)


def _target_slot(person: MutableMapping[str, Any], path: str) -> tuple[MutableMapping[str, Any], str, int] | None:
    tokens = path.split(".")
    if len(tokens) != 2:
        return None
    container = person.get(tokens[0])
    if not isinstance(container, MutableMapping):
        return None
    value = container.get(tokens[1])
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return container, tokens[1], value


def _aptitude(person: Mapping[str, Any], target: str) -> int:
    aptitude = person.get("aptitude")
    if not isinstance(aptitude, Mapping):
        return 50
    if target.startswith("martial_skills."):
        key = "physical_learning"
    elif target.startswith("attributes."):
        key = "tactical_learning"
    else:
        key = "tactical_learning"
    value = aptitude.get(key, 50)
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 200 else 50


def _rotate_hours(owner_ref: str, start: CampaignTime, reviews: int, hours_per_review: Decimal, cycle: Sequence[str]) -> Dict[str, Decimal]:
    if reviews <= 0:
        return {}
    seed = hashlib.sha256(f"{owner_ref}\x00{start}".encode()).digest()
    offset = int.from_bytes(seed[:4], "big") % len(cycle)
    result: Dict[str, Decimal] = {}
    for index in range(reviews):
        target = cycle[(offset + index) % len(cycle)]
        result[target] = result.get(target, Decimal(0)) + hours_per_review
    return result


def settle_service_development(
    person: MutableMapping[str, Any],
    entry: MutableMapping[str, Any],
    *,
    owner_ref: str,
    start: CampaignTime,
    through: CampaignTime,
    policy: Mapping[str, Any],
    historical: bool,
) -> Mapping[str, Any]:
    """Settle one bounded service-development deficit into an exact person."""
    if through <= start:
        return {"hours": "0", "outcomes": []}
    cycle = tuple(str(value) for value in policy["target_cycle"])
    if historical:
        elapsed = Decimal((_campaign_datetime(through) - _campaign_datetime(start)).total_seconds())
        reviews = int(elapsed // (_SECONDS_PER_DAY * Decimal(7)))
        hours_per_review = Decimal(str(policy["historical_hours_per_full_week"]))
    else:
        # Ordinary play never backfills arbitrary history. One continuity review
        # offers at most one configured month, reduced proportionally when some
        # of the interval was already settled by another development domain.
        elapsed_days = Decimal((_campaign_datetime(through) - _campaign_datetime(start)).total_seconds()) / _SECONDS_PER_DAY
        reviews = 1
        monthly = Decimal(str(policy["active_hours_per_month"]))
        hours_per_review = min(monthly, max(Decimal(0), monthly * elapsed_days / Decimal(30)))
    if reviews <= 0 or hours_per_review <= 0:
        return {"hours": "0", "outcomes": []}
    plan = _rotate_hours(owner_ref, start, reviews, hours_per_review, cycle)
    credits = entry.get("credits")
    if not isinstance(credits, MutableMapping):
        raise CommandRejectedError("development_bank_invalid")
    outcomes = []
    total = Decimal(0)
    for target, hours in sorted(plan.items()):
        slot = _target_slot(person, target)
        if slot is None:
            continue
        container, leaf, before = slot
        try:
            outcome = settle_training(
                TrainingInputs(
                    scheduled_hours=str(hours),
                    attendance="1",
                    available_instructor_hours=str(hours),
                    required_instructor_hours=str(hours),
                    facility_slots="1",
                    required_slots="1",
                    equipment_sets="1",
                    required_sets="1",
                    instructor_quality_factor="1",
                    facility_quality_factor="1",
                    equipment_factor="1",
                    health_factor="1",
                    recovery_factor="1",
                    relevance_factor="1",
                    difficulty_fit_factor="1",
                    aptitude=_aptitude(person, target),
                    experience_modifier="1",
                    current_value=before,
                    residual_units=credits.get(target, 0),
                    representation="exact",
                )
            )
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("named_service_training_resolution_invalid") from exc
        container[leaf] = outcome.ending_value
        credits[target] = format(outcome.residual_units, "f")
        total += hours
        outcomes.append({
            "target": target,
            "active_hours": format(hours, "f"),
            "starting_value": before,
            "ending_value": outcome.ending_value,
            "points_gained": outcome.points_gained,
            "residual_units": str(outcome.residual_units),
        })
    if outcomes:
        entry["resolved_through"] = str(through)
    return {"hours": format(total, "f"), "outcomes": outcomes}


class _BaseOverlayView:
    def __init__(self, overlay: Any, changed_paths: tuple[str, ...], base_json: Mapping[str, Mapping[str, Any]]) -> None:
        self._overlay = overlay
        self.changed_paths = changed_paths
        self._base_json = copy.deepcopy(dict(base_json))

    def read_json(self, path: str) -> Any:
        if path in self._base_json:
            return copy.deepcopy(self._base_json[path])
        return self._overlay.read_json(path)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._overlay, name)


def _decode_staged(raw: object) -> Mapping[str, Any] | None:
    if not isinstance(raw, (bytes, bytearray)):
        return None
    try:
        value = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def _install_time_service_development() -> None:
    original = TimeCommandsMixin._advance_time
    if getattr(original, "_named_service_development", False):
        return

    @wraps(original)
    def wrapped(self: Any, command: Any, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        base = original(self, command, meta, current_time)
        raw_reviews = base.result.get("person_continuity_reviews")
        reviews = raw_reviews if isinstance(raw_reviews, list) else []
        review_times = []
        for row in reviews:
            at = _record_time(row.get("at") if isinstance(row, Mapping) else None)
            if at is not None and at not in review_times:
                review_times.append(at)
        if not review_times:
            return base
        through = max(review_times)
        policy = _policy(self.repository)
        writes = dict(base.writes)
        base_json: Dict[str, Mapping[str, Any]] = {}
        staged_records: Dict[str, Mapping[str, Any]] = {}
        for path, raw in base.writes.items():
            value = _decode_staged(raw)
            if value is not None:
                staged_records[path] = value
        team_members = _active_team_training_members(self.repository, record_writes=staged_records)

        raw_bank = base.writes.get(DEVELOPMENT_BANK_PATH)
        bank_base = _decode_staged(raw_bank)
        if bank_base is None:
            try:
                bank_base = self.repository.read_json(DEVELOPMENT_BANK_PATH)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("development_bank_invalid") from exc
        if not isinstance(bank_base, Mapping):
            raise CommandRejectedError("development_bank_invalid")
        banks = copy.deepcopy(dict(bank_base))
        entries = banks.get("entries")
        if not isinstance(entries, dict):
            raise CommandRejectedError("development_bank_invalid")
        if raw_bank is not None:
            base_json[DEVELOPMENT_BANK_PATH] = bank_base

        rows = []
        for owner_ref, path in sorted(_owner_paths(self.repository).items()):
            if owner_ref == meta.get("player_id") or owner_ref in team_members:
                continue
            raw_person = base.writes.get(path)
            person_base = _decode_staged(raw_person)
            if person_base is None:
                try:
                    person_base = self.repository.read_json(path)
                except (FileNotFoundError, ValueError):
                    continue
            if not isinstance(person_base, Mapping) or not _qualifying_status(person_base, policy):
                continue
            person = copy.deepcopy(dict(person_base))
            entry = entries.get(owner_ref)
            if entry is None:
                entry = {"owner_type": "character", "resolved_through": str(service_start(person, current_time, policy)), "credits": {}}
                entries[owner_ref] = entry
            if not isinstance(entry, dict) or not isinstance(entry.get("credits"), dict):
                raise CommandRejectedError("development_bank_invalid")
            cursor = _record_time(entry.get("resolved_through"))
            if cursor is None:
                raise CommandRejectedError("development_bank_invalid")
            start = max(cursor, service_start(person, cursor, policy))
            outcome = settle_service_development(
                person,
                entry,
                owner_ref=owner_ref,
                start=start,
                through=through,
                policy=policy,
                historical=False,
            )
            if outcome["outcomes"]:
                if raw_person is not None:
                    base_json[path] = person_base
                writes[path] = _json_bytes(person)
                rows.append({"owner_ref": owner_ref, **outcome})
        if not rows:
            return base
        writes[DEVELOPMENT_BANK_PATH] = _json_bytes(banks)
        base_paths = tuple(sorted(base.writes))
        expected = {path: _decode_staged(raw) for path, raw in writes.items() if path == DEVELOPMENT_BANK_PATH or path in {row_path for row_ref, row_path in _owner_paths(self.repository).items() if any(item["owner_ref"] == row_ref for item in rows)}}

        def validate(overlay: Any, manifest: Any) -> None:
            base.validator(_BaseOverlayView(overlay, base_paths, base_json), manifest)
            for path, value in expected.items():
                if value is not None and overlay.read_json(path) != value:
                    raise ValueError("named service development after-image differs from plan")

        result = dict(base.result)
        result["named_service_development_reviews"] = rows
        return _BuiltPlan(
            code=base.code,
            affected_refs=tuple(sorted(writes)),
            writes=writes,
            result=result,
            validator=validate,
        )

    wrapped._named_service_development = True  # type: ignore[attr-defined]
    TimeCommandsMixin._advance_time = wrapped


def install_named_service_development() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_time_service_development()
    _INSTALLED = True


__all__ = [
    "install_named_service_development",
    "settle_service_development",
    "service_start",
    "_policy",
    "_owner_paths",
    "_qualifying_status",
]
