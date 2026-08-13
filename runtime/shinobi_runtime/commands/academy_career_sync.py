"""Exact-person synchronization for aggregate Academy graduation.

Academy institutions graduate conserved population in aggregate. Some members of
that aggregate pool are already materialized exact characters. This extension
keeps those two representations synchronized without creating extra people or
forcing a particular team composition.
"""
from __future__ import annotations

import copy
import re
from functools import wraps
from typing import Any, Dict, Mapping, Optional

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _OwnerResolutionCache
from shinobi_runtime.commands.domains.autonomy import AutonomyCommandsMixin
from shinobi_runtime.commands.paths import POPULATION_REGISTRY_PATH as _POPULATION_REGISTRY_PATH
from shinobi_runtime.sim.events import CampaignTime

_BIRTH_DATE = re.compile(r"^SE-([0-9]{4})-([0-9]{2})-([0-9]{2})$")
_TERMINAL_READINESS = {"dead", "captured", "incapacitated"}


def _age_years(birth_date: str, at: CampaignTime) -> Optional[int]:
    match = _BIRTH_DATE.fullmatch(birth_date)
    if match is None:
        return None
    year, month, day = (int(value) for value in match.groups())
    age = at.year - year
    if (at.month, at.day) < (month, day):
        age -= 1
    return age if age >= 0 else None


def _apply_graduation_career_state(
    subject: Dict[str, Any],
    *,
    at: CampaignTime,
    reason: str,
) -> str:
    """Apply the durable fields used by the public career graduation reducer."""
    if subject.get("schema") != "shinobi_character":
        raise CommandRejectedError("academy_graduate_not_character")
    previous = subject.get("official_rank_or_status")
    subject["official_rank_or_status"] = "Genin"

    career = subject.get("career_state")
    if career is None:
        career = {}
        subject["career_state"] = career
    if not isinstance(career, dict):
        raise CommandRejectedError("career_state_invalid")
    career["rank"] = "Genin"
    career["current_rank_or_status"] = "Genin"
    career["promotion_eligible"] = False

    life = subject.get("life_course_state")
    if life is None:
        life = {
            "rank_history": [],
            "status_history": [],
            "injury_events": [],
            "relationship_events": [],
            "location_history": [],
        }
        subject["life_course_state"] = life
    if not isinstance(life, dict):
        raise CommandRejectedError("career_history_invalid")
    rank_history = life.setdefault("rank_history", [])
    if not isinstance(rank_history, list):
        raise CommandRejectedError("career_history_invalid")
    rank_history.append({"at": str(at), "rank": "Genin", "reason": reason})
    status_history = life.setdefault("status_history", [])
    if not isinstance(status_history, list):
        raise CommandRejectedError("career_history_invalid")
    status_history.append(f"{at}: graduate: Genin: {reason}")
    return str(previous)


def _eligible_exact_graduates(
    planner: Any,
    *,
    rostered_refs: list[str],
    at: CampaignTime,
    minimum_age: int,
    record_writes: Dict[str, Dict[str, Any]],
) -> list[tuple[tuple[int, int, int], str, str, Dict[str, Any]]]:
    eligible: list[tuple[tuple[int, int, int], str, str, Dict[str, Any]]] = []
    for person_ref in rostered_refs:
        if not isinstance(person_ref, str) or not person_ref:
            continue
        try:
            path, _digest, view = planner._resolve_covered_owner_view(
                person_ref, cache=_OwnerResolutionCache()
            )
        except CommandRejectedError:
            continue
        if not isinstance(view, Mapping) or view.get("schema") != "shinobi_character":
            continue
        subject = record_writes.get(path)
        if subject is None:
            subject = copy.deepcopy(dict(view))
        if not isinstance(subject, dict):
            continue
        official = str(subject.get("official_rank_or_status") or "").lower()
        career = subject.get("career_state")
        if not isinstance(career, Mapping):
            continue
        career_rank = str(career.get("current_rank_or_status") or career.get("rank") or "").lower()
        if "academy" not in official or "academy" not in career_rank:
            continue
        if career.get("promotion_eligible") is not True:
            continue
        if subject.get("life_status") not in ("active", "alive"):
            continue
        condition = subject.get("condition")
        readiness = condition.get("readiness") if isinstance(condition, Mapping) else None
        if isinstance(readiness, str) and readiness.lower() in _TERMINAL_READINESS:
            continue
        birth_date = subject.get("birth_date")
        if not isinstance(birth_date, str):
            continue
        match = _BIRTH_DATE.fullmatch(birth_date)
        age = _age_years(birth_date, at)
        if match is None or age is None or age < minimum_age:
            continue
        birth_key = tuple(int(value) for value in match.groups())
        eligible.append((birth_key, person_ref, path, subject))
    # Oldest qualified cadets consume finite administrative graduation slots
    # first; stable ref breaks exact-date ties without using hidden exam rolls.
    eligible.sort(key=lambda row: (row[0], row[1]))
    return eligible


def _synchronize_exact_graduates(
    planner: Any,
    *,
    institution: Dict[str, Any],
    at: CampaignTime,
    command: Any,
    policy_book: Any,
    world_events: Dict[str, Any],
    record_writes: Dict[str, Dict[str, Any]],
    result: Mapping[str, Any],
) -> Mapping[str, Any]:
    pipeline_raw = result.get("population_pipeline")
    if not isinstance(pipeline_raw, Mapping):
        return result
    graduation = pipeline_raw.get("graduates")
    graduation_id = pipeline_raw.get("graduation_transfer_id")
    if (
        isinstance(graduation, bool)
        or not isinstance(graduation, int)
        or graduation <= 0
        or not isinstance(graduation_id, str)
        or not graduation_id
    ):
        return result

    institution_id = institution.get("id")
    if not isinstance(institution_id, str):
        raise CommandRejectedError("institution_autonomy_invalid")
    assignment = policy_book.institution_assignment(institution_id)
    if assignment.get("kind") != "academy_pipeline":
        return result
    academy_id = assignment.get("academy_pool_id")
    service_id = assignment.get("service_pool_id")
    force_ref = assignment.get("force_ref")
    if not all(isinstance(value, str) and value for value in (academy_id, service_id, force_ref)):
        raise CommandRejectedError("institution_autonomy_policy_invalid")

    population = record_writes.get(_POPULATION_REGISTRY_PATH)
    if not isinstance(population, dict):
        raise CommandRejectedError("population_registry_invalid")
    pools = population.get("pools")
    transfers = population.get("transfers")
    if not isinstance(pools, dict) or not isinstance(transfers, list):
        raise CommandRejectedError("population_registry_invalid")
    academy_record = pools.get(academy_id)
    service_record = pools.get(service_id)
    if not isinstance(academy_record, dict) or not isinstance(service_record, dict):
        raise CommandRejectedError("institution_autonomy_population_invalid")

    academy_rep = academy_record.get("representation")
    service_rep = service_record.get("representation")
    if not isinstance(academy_rep, dict) or not isinstance(service_rep, dict):
        raise CommandRejectedError("population_representation_invalid")
    academy_refs = academy_rep.get("rostered_person_refs")
    service_refs = service_rep.get("rostered_person_refs")
    if not isinstance(academy_refs, list) or not isinstance(service_refs, list):
        raise CommandRejectedError("population_representation_invalid")

    age_distribution = (
        service_record.get("profile", {}).get("numeric_distributions", {}).get("age_years", {})
        if isinstance(service_record.get("profile"), Mapping)
        else {}
    )
    minimum_age_raw = age_distribution.get("min") if isinstance(age_distribution, Mapping) else None
    if isinstance(minimum_age_raw, bool) or not isinstance(minimum_age_raw, (int, float)):
        raise CommandRejectedError("institution_autonomy_population_invalid")
    minimum_age = max(0, int(minimum_age_raw))

    candidates = _eligible_exact_graduates(
        planner,
        rostered_refs=[ref for ref in academy_refs if isinstance(ref, str)],
        at=at,
        minimum_age=minimum_age,
        record_writes=record_writes,
    )
    chosen = candidates[:graduation]
    if not chosen:
        enriched = dict(result)
        pipeline = dict(pipeline_raw)
        pipeline["exact_graduate_refs"] = []
        pipeline["anonymous_graduates"] = graduation
        enriched["population_pipeline"] = pipeline
        return enriched

    exact_refs = [row[1] for row in chosen]
    exact_paths = [row[2] for row in chosen]
    exact_count = len(exact_refs)
    academy_anonymous = academy_rep.get("anonymous_count")
    service_anonymous = service_rep.get("anonymous_count")
    if (
        isinstance(academy_anonymous, bool)
        or not isinstance(academy_anonymous, int)
        or academy_anonymous < 0
        or isinstance(service_anonymous, bool)
        or not isinstance(service_anonymous, int)
        or service_anonymous < exact_count
    ):
        raise CommandRejectedError("population_representation_invalid")

    academy_set = set(exact_refs)
    academy_refs[:] = [ref for ref in academy_refs if ref not in academy_set]
    for person_ref in exact_refs:
        if person_ref not in service_refs:
            service_refs.append(person_ref)
    service_refs.sort()
    academy_refs.sort()
    academy_rep["rostered_count"] = len(academy_refs)
    service_rep["rostered_count"] = len(service_refs)
    # Base Academy settlement first represented every graduation slot as
    # anonymous. Reclassify the selected exact identities inside those same
    # slots; pool totals and force totals do not change a second time.
    academy_rep["anonymous_count"] = academy_anonymous + exact_count
    service_rep["anonymous_count"] = service_anonymous - exact_count

    if academy_rep["anonymous_count"] + academy_rep["rostered_count"] != academy_record.get("count"):
        raise CommandRejectedError("population_representation_invalid")
    if service_rep["anonymous_count"] + service_rep["rostered_count"] != service_record.get("count"):
        raise CommandRejectedError("population_representation_invalid")

    reason = f"Qualified through the {institution_id} graduation cycle."
    consequence_refs: list[str] = [graduation_id]
    for _birth, person_ref, path, subject in chosen:
        previous = _apply_graduation_career_state(subject, at=at, reason=reason)
        record_writes[path] = subject
        consequence_refs.append(f"career:{person_ref}:{previous}->Genin")

    transfer_row = next(
        (
            row
            for row in reversed(transfers)
            if isinstance(row, dict) and row.get("id") == graduation_id
        ),
        None,
    )
    if transfer_row is None:
        raise CommandRejectedError("institution_autonomy_population_invalid")
    transfer_row["materialized_person_ids"] = list(exact_refs)
    transfer_row["method"] = "neutral_proportional_with_rostered_identity_sync"
    transfer_row["selection_note"] = (
        "Graduation conserves one shared cohort flow. Qualified rostered Academy identities consume existing graduation slots; "
        "remaining slots stay anonymous, so no extra person or force headcount is created."
    )

    affected = [_POPULATION_REGISTRY_PATH, *exact_paths]
    try:
        force_path, _digest, _force_view = planner._resolve_covered_owner_view(
            force_ref, cache=_OwnerResolutionCache()
        )
    except CommandRejectedError:
        force_path = None
    if isinstance(force_path, str):
        affected.append(force_path)
    leader = institution.get("leader_id")
    actor_refs = tuple(
        dict.fromkeys(
            [ref for ref in ([leader] if isinstance(leader, str) else []) + exact_refs if isinstance(ref, str)]
        )
    )
    graduation_event_id = planner._append_internal_event(
        world_events,
        command=command,
        identity=f"{institution_id}:{at}:exact-graduation",
        kind="academy_exact_graduation_recorded",
        at=at,
        host_refs=(institution_id, force_ref),
        actor_refs=actor_refs,
        affected_owner_refs=tuple(sorted(set(affected))),
        material_consequence_refs=tuple(consequence_refs),
        classification="public",
        audience_refs=tuple(exact_refs),
        source_refs=(institution_id,),
    )

    enriched = dict(result)
    pipeline = dict(pipeline_raw)
    pipeline["exact_graduate_refs"] = list(exact_refs)
    pipeline["anonymous_graduates"] = graduation - exact_count
    pipeline["exact_graduation_event_id"] = graduation_event_id
    enriched["population_pipeline"] = pipeline
    return enriched


def install_academy_career_sync() -> None:
    """Install the exact-graduate synchronization around the generic Academy reducer."""
    original = AutonomyCommandsMixin._apply_institution_autonomy_review
    if getattr(original, "_academy_exact_graduation_sync", False):
        return

    @wraps(original)
    def wrapped(
        self: Any,
        *,
        institution: Dict[str, Any],
        at: CampaignTime,
        compacted: int,
        command: Any,
        policy_book: Any,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Mapping[str, Any]:
        result = original(
            self,
            institution=institution,
            at=at,
            compacted=compacted,
            command=command,
            policy_book=policy_book,
            world_events=world_events,
            record_writes=record_writes,
        )
        return _synchronize_exact_graduates(
            self,
            institution=institution,
            at=at,
            command=command,
            policy_book=policy_book,
            world_events=world_events,
            record_writes=record_writes,
            result=result,
        )

    wrapped._academy_exact_graduation_sync = True  # type: ignore[attr-defined]
    AutonomyCommandsMixin._apply_institution_autonomy_review = wrapped
