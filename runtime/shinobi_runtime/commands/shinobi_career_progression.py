"""Monthly named continuity and conserved aggregate shinobi career flow.

The extension is installed at the API boundary after command modules are fully
loaded. It annotates conserved Academy graduation flow and settles aggregate
Genin -> Chunin -> Jonin throughput on the canonical monthly continuity schedule.
Exact named rank changes remain exclusively under the ordinary evidence/authority
career reducer.
"""
from __future__ import annotations

import copy
import json
from functools import wraps
from typing import Any, Dict, Mapping, Optional

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.domains.autonomy import AutonomyCommandsMixin
from shinobi_runtime.commands.domains.time import TimeCommandsMixin
from shinobi_runtime.sim.events import CampaignTime

_CAREER_PATH = "state/reg/shinobi-career-pipeline.json"
_CAREER_RULES_PATH = "game/rules/career/progression.json"
_PPM = 1_000_000
_MAX_HISTORY = 512
_INSTALLED = False


def _validate_rules(rules: Mapping[str, Any]) -> tuple[int, int]:
    if rules.get("schema") != "shinobi-career-progression-rules":
        raise CommandRejectedError("shinobi_career_rules_invalid")
    rates = rules.get("aggregate_promotion_rates_ppm_per_review")
    if not isinstance(rates, Mapping):
        raise CommandRejectedError("shinobi_career_rules_invalid")
    genin_rate = rates.get("genin_to_chunin")
    chunin_rate = rates.get("chunin_to_jonin")
    if any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _PPM
        for value in (genin_rate, chunin_rate)
    ):
        raise CommandRejectedError("shinobi_career_rules_invalid")
    if chunin_rate >= genin_rate:
        raise CommandRejectedError("shinobi_career_rules_invalid")
    return genin_rate, chunin_rate


def _career_villages(record: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    if record.get("schema") != "shinobi-career-pipeline" or record.get("version") != 1:
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    villages = record.get("villages")
    if not isinstance(villages, dict) or not villages:
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    for village_id, row in villages.items():
        if not isinstance(village_id, str) or not isinstance(row, dict):
            raise CommandRejectedError("shinobi_career_pipeline_invalid")
        counts = row.get("rank_counts")
        credits = row.get("promotion_credit_ppm")
        if not isinstance(counts, dict) or not isinstance(credits, dict):
            raise CommandRejectedError("shinobi_career_pipeline_invalid")
        for rank in ("genin", "chunin", "jonin"):
            value = counts.get(rank)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CommandRejectedError("shinobi_career_pipeline_invalid")
        for stage in ("genin_to_chunin", "chunin_to_jonin"):
            value = credits.get(stage)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < _PPM:
                raise CommandRejectedError("shinobi_career_pipeline_invalid")
    return villages


def _history(record: Dict[str, Any]) -> list[Dict[str, Any]]:
    history = record.setdefault("history", [])
    if not isinstance(history, list) or any(not isinstance(row, dict) for row in history):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    if len(history) > _MAX_HISTORY:
        del history[:-_MAX_HISTORY]
    return history


def _total(villages: Mapping[str, Mapping[str, Any]]) -> int:
    return sum(
        row["rank_counts"]["genin"]
        + row["rank_counts"]["chunin"]
        + row["rank_counts"]["jonin"]
        for row in villages.values()
    )


def _add_academy_graduates(
    record: Dict[str, Any],
    *,
    service_pool_ref: str,
    graduates: int,
    at: CampaignTime,
    transfer_id: str,
) -> Optional[Mapping[str, Any]]:
    """Enter one conserved Academy transfer into the matching Genin cohort."""
    if graduates <= 0:
        return None
    villages = _career_villages(record)
    history = _history(record)
    if any(row.get("transfer_id") == transfer_id for row in history):
        return None
    matches = [
        (village_id, row)
        for village_id, row in villages.items()
        if row.get("service_pool_ref") == service_pool_ref
    ]
    if len(matches) != 1:
        raise CommandRejectedError("shinobi_career_service_pool_unmapped")
    village_id, row = matches[0]
    row["rank_counts"]["genin"] += graduates
    entry = {
        "kind": "academy_genin_intake",
        "at": str(at),
        "village": village_id,
        "graduates": graduates,
        "transfer_id": transfer_id,
    }
    history.append(entry)
    if len(history) > _MAX_HISTORY:
        del history[:-_MAX_HISTORY]
    return entry


def _settle_one_month(
    record: Dict[str, Any],
    *,
    rules: Mapping[str, Any],
    at: CampaignTime,
) -> Mapping[str, Any]:
    """Apply exactly one monthly aggregate promotion boundary."""
    genin_rate, chunin_rate = _validate_rules(rules)
    villages = _career_villages(record)
    before_total = _total(villages)
    promoted: Dict[str, Dict[str, int]] = {}
    for village_id in sorted(villages):
        row = villages[village_id]
        counts = row["rank_counts"]
        credits = row["promotion_credit_ppm"]
        genin0 = counts["genin"]
        chunin0 = counts["chunin"]
        jonin0 = counts["jonin"]
        genin_credit = credits["genin_to_chunin"] + genin0 * genin_rate
        chunin_credit = credits["chunin_to_jonin"] + chunin0 * chunin_rate
        genin_promotions = min(genin0, genin_credit // _PPM)
        chunin_promotions = min(chunin0, chunin_credit // _PPM)
        credits["genin_to_chunin"] = genin_credit % _PPM
        credits["chunin_to_jonin"] = chunin_credit % _PPM
        # Chunin -> Jonin uses the pre-boundary Chunin population, so a shinobi
        # newly promoted from Genin cannot skip straight to Jonin this month.
        counts["genin"] = genin0 - genin_promotions
        counts["chunin"] = chunin0 + genin_promotions - chunin_promotions
        counts["jonin"] = jonin0 + chunin_promotions
        promoted[village_id] = {
            "genin_to_chunin": genin_promotions,
            "chunin_to_jonin": chunin_promotions,
        }
    after_total = _total(villages)
    if before_total != after_total:
        raise CommandRejectedError("shinobi_career_headcount_not_conserved")
    record["last_review_at"] = str(at)
    return {
        "kind": "aggregate_rank_progression",
        "at": str(at),
        "promotions": promoted,
        "headcount_before": before_total,
        "headcount_after": after_total,
    }


def _monthly_boundaries(record: Mapping[str, Any], through: CampaignTime) -> list[CampaignTime]:
    try:
        prior = CampaignTime.parse(record.get("last_review_at"))
    except (TypeError, ValueError) as exc:
        raise CommandRejectedError("shinobi_career_pipeline_invalid") from exc
    if prior >= through:
        return []
    boundary = prior.next_month_start(through.hour, through.minute, through.second)
    result: list[CampaignTime] = []
    while boundary <= through:
        result.append(boundary)
        if len(result) > 600:
            raise CommandRejectedError("shinobi_career_review_horizon_exceeded")
        boundary = boundary.next_month_start(through.hour, through.minute, through.second)
    return result


def _install_academy_intake_annotation() -> None:
    """Expose Academy aggregate flow to the outer chronological time reducer."""
    original = AutonomyCommandsMixin._apply_institution_autonomy_review
    if getattr(original, "_academy_aggregate_genin_annotation", False):
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
        pipeline = result.get("population_pipeline") if isinstance(result, Mapping) else None
        if not isinstance(pipeline, Mapping):
            return result
        graduates = pipeline.get("graduates")
        transfer_id = pipeline.get("graduation_transfer_id")
        if (
            isinstance(graduates, bool)
            or not isinstance(graduates, int)
            or graduates <= 0
            or not isinstance(transfer_id, str)
            or not transfer_id
        ):
            return result
        institution_id = institution.get("id")
        if not isinstance(institution_id, str):
            raise CommandRejectedError("institution_autonomy_invalid")
        assignment = policy_book.institution_assignment(institution_id)
        if assignment.get("kind") != "academy_pipeline":
            return result
        service_pool_ref = assignment.get("service_pool_id")
        if not isinstance(service_pool_ref, str) or not service_pool_ref:
            raise CommandRejectedError("institution_autonomy_policy_invalid")
        enriched = dict(result)
        enriched["aggregate_career_intake"] = {
            "at": str(at),
            "service_pool_ref": service_pool_ref,
            "graduates": graduates,
            "transfer_id": transfer_id,
            "scheduler_priority": 90,
        }
        return enriched

    wrapped._academy_aggregate_genin_annotation = True  # type: ignore[attr-defined]
    AutonomyCommandsMixin._apply_institution_autonomy_review = wrapped


class _BaseOverlayView:
    """Present the exact base career after-image to the base-plan validator.

    The aggregate career layer may lawfully refine the same career owner that
    base autonomous work already changed at the monthly boundary. The base
    validator must therefore see the base plan's own pre-extension image, while
    the outer validator separately proves the final composed career image.
    """

    def __init__(
        self,
        overlay: Any,
        changed_paths: tuple[str, ...],
        *,
        base_json: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._overlay = overlay
        self.changed_paths = changed_paths
        self._base_json = copy.deepcopy(dict(base_json or {}))

    def read_json(self, path: str) -> Any:
        if path in self._base_json:
            return copy.deepcopy(self._base_json[path])
        return self._overlay.read_json(path)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._overlay, name)


def _install_time_career_progression() -> None:
    original = TimeCommandsMixin._advance_time
    if getattr(original, "_aggregate_career_progression", False):
        return

    @wraps(original)
    def wrapped(
        self: Any,
        command: Any,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        base = original(self, command, meta, current_time)
        raw_reviews = base.result.get("person_continuity_reviews")
        raw_actions = base.result.get("autonomous_actions")
        reviews = raw_reviews if isinstance(raw_reviews, list) else []
        actions = raw_actions if isinstance(raw_actions, list) else []
        intake_actions: list[Dict[str, Any]] = []
        for action in actions:
            intake = action.get("aggregate_career_intake") if isinstance(action, Mapping) else None
            if isinstance(intake, Mapping):
                intake_actions.append(dict(intake))
        if not reviews and not intake_actions:
            return base

        raw_base_career = base.writes.get(_CAREER_PATH)
        try:
            if raw_base_career is None:
                loaded = self.repository.read_json(_CAREER_PATH)
            elif isinstance(raw_base_career, (bytes, bytearray)):
                loaded = json.loads(bytes(raw_base_career).decode("utf-8"))
            else:
                raise ValueError("invalid staged career image")
            rules = self.repository.read_json(_CAREER_RULES_PATH)
        except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CommandRejectedError("shinobi_career_pipeline_invalid") from exc
        if not isinstance(loaded, dict) or not isinstance(rules, Mapping):
            raise CommandRejectedError("shinobi_career_pipeline_invalid")

        # When the base time reducer already changed the career owner, compose
        # from that exact staged after-image. Reloading committed state here
        # would discard the base reducer's lawful change and create two competing
        # after-images for one transaction owner.
        base_career_image = (
            copy.deepcopy(loaded) if raw_base_career is not None else None
        )
        career = copy.deepcopy(loaded)
        _career_villages(career)
        _validate_rules(rules)

        through: Optional[CampaignTime] = None
        for review in reviews:
            raw_at = review.get("at") if isinstance(review, Mapping) else None
            if not isinstance(raw_at, str):
                continue
            try:
                candidate = CampaignTime.parse(raw_at)
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("person_continuity_registry_invalid") from exc
            if through is None or candidate > through:
                through = candidate

        timeline: list[tuple[CampaignTime, int, str, Mapping[str, Any]]] = []
        if through is not None:
            for boundary in _monthly_boundaries(career, through):
                # Production continuity priority is 96. Academy institution
                # reviews use 90, so same-timestamp graduates enter Genin first.
                timeline.append((boundary, 96, "promotion", {}))
        for intake in intake_actions:
            raw_at = intake.get("at")
            if not isinstance(raw_at, str):
                raise CommandRejectedError("shinobi_career_intake_invalid")
            try:
                at = CampaignTime.parse(raw_at)
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("shinobi_career_intake_invalid") from exc
            timeline.append((at, 90, "intake", intake))
        timeline.sort(key=lambda item: (item[0], item[1], item[2]))

        career_reviews: list[Mapping[str, Any]] = []
        for at, _priority, kind, payload in timeline:
            if kind == "intake":
                service_pool_ref = payload.get("service_pool_ref")
                graduates = payload.get("graduates")
                transfer_id = payload.get("transfer_id")
                if (
                    not isinstance(service_pool_ref, str)
                    or isinstance(graduates, bool)
                    or not isinstance(graduates, int)
                    or not isinstance(transfer_id, str)
                ):
                    raise CommandRejectedError("shinobi_career_intake_invalid")
                entry = _add_academy_graduates(
                    career,
                    service_pool_ref=service_pool_ref,
                    graduates=graduates,
                    at=at,
                    transfer_id=transfer_id,
                )
                if entry is not None:
                    career_reviews.append(entry)
                continue
            entry = _settle_one_month(career, rules=rules, at=at)
            history = _history(career)
            history.append(dict(entry))
            if len(history) > _MAX_HISTORY:
                del history[:-_MAX_HISTORY]
            career_reviews.append(entry)

        if not career_reviews:
            return base
        writes = dict(base.writes)
        writes[_CAREER_PATH] = _json_bytes(career)
        base_paths = tuple(sorted(base.writes))
        base_json = (
            {_CAREER_PATH: base_career_image}
            if base_career_image is not None
            else {}
        )
        expected = copy.deepcopy(career)

        def validate(overlay: Any, manifest: Any) -> None:
            base.validator(
                _BaseOverlayView(overlay, base_paths, base_json=base_json),
                manifest,
            )
            staged = overlay.read_json(_CAREER_PATH)
            if staged != expected:
                raise ValueError("shinobi career after-image differs from plan")
            villages = _career_villages(staged)
            if any(
                value < 0
                for row in villages.values()
                for value in row["rank_counts"].values()
            ):
                raise ValueError("shinobi career after-image has negative rank count")

        result = dict(base.result)
        result["shinobi_career_reviews"] = [dict(item) for item in career_reviews]
        return _BuiltPlan(
            code=base.code,
            affected_refs=tuple(sorted(writes)),
            writes=writes,
            result=result,
            validator=validate,
        )

    wrapped._aggregate_career_progression = True  # type: ignore[attr-defined]
    TimeCommandsMixin._advance_time = wrapped


def install_shinobi_career_progression() -> None:
    """Install the production monthly continuity and career extensions once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_academy_intake_annotation()
    _install_time_career_progression()
    _INSTALLED = True


__all__ = [
    "install_shinobi_career_progression",
    "_add_academy_graduates",
    "_settle_one_month",
    "_monthly_boundaries",
]
