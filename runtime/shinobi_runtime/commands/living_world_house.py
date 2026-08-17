from __future__ import annotations

import copy
import json
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Mapping, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _campaign_datetime, _json_bytes
from shinobi_runtime.reducers import TrainingInputs, settle_training
from shinobi_runtime.sim.events import CampaignTime


_HOUSE_TRAINING_POLICY_PATH = "game/data/house/training-policies.json"
_THREE = Decimal("0.001")


class _BasePlanOverlayView:
    """Present the base time-plan after-image to its original validator.

    Living-world House settlement is an intentional production extension layered
    over the generic time reducer.  The generic validator still validates the
    complete base plan exactly as it was built; this proxy hides only the House
    after-images that this extension deterministically refines afterward.  The
    extension then validates its own final after-images separately.
    """

    def __init__(self, overlay: Any, *, base_paths: Sequence[str], base_json: Mapping[str, Any]):
        self._overlay = overlay
        self._base_paths = tuple(base_paths)
        self._base_json = dict(base_json)

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return self._base_paths

    def read_json(self, path: str) -> Any:
        if path in self._base_json:
            return copy.deepcopy(self._base_json[path])
        return self._overlay.read_json(path)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._overlay, name)


class LivingWorldHouseMixin:
    """Lazy, representation-neutral House cohort progression.

    Global campaign time is allowed to move ahead of a House/cohort development
    cursor.  A cursor itself is never a clock mirror: before it advances, every
    eligible scheduled training window in the unresolved interval is settled
    through the normal training law.  Sparse roster identities inherit their
    changing capability from the cohort profile, so routine training updates the
    cohort once rather than fanning out twenty-seven identical person writes.
    """

    def _house_training_policy(self, house_id: str) -> Mapping[str, Any] | None:
        try:
            registry = self.repository.read_json(_HOUSE_TRAINING_POLICY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("house_training_policy_invalid") from exc
        policies = registry.get("policies") if isinstance(registry, Mapping) else None
        if not isinstance(policies, Mapping):
            raise CommandRejectedError("house_training_policy_invalid")
        policy = policies.get(house_id)
        if policy is None:
            return None
        if not isinstance(policy, Mapping):
            raise CommandRejectedError("house_training_policy_invalid")
        self._house_training_envelope(policy)
        return policy

    @staticmethod
    def _house_training_envelope(policy: Mapping[str, Any]) -> tuple[int, int, int]:
        cap = policy.get("weekly_active_hours_cap")
        shared = policy.get("shared_core_active_hours_per_week")
        supplemental = policy.get("supplemental_individual_active_hours_per_week")
        rule = policy.get("supplemental_rule")
        schedule = policy.get("daily_active_hours")
        values = (cap, shared, supplemental)
        if (
            any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values)
            or not isinstance(cap, int)
            or cap <= 0
            or cap > 48
            or shared + supplemental != cap
            or not isinstance(schedule, list)
            or len(schedule) != 7
            or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 24 for value in schedule)
            or sum(schedule) != cap
            or 0 not in schedule
            or not isinstance(rule, str)
            or not rule.strip()
        ):
            raise CommandRejectedError("house_training_policy_invalid")
        return cap, shared, supplemental

    @classmethod
    def _scheduled_house_training_components(
        cls, start: CampaignTime, end: CampaignTime, policy: Mapping[str, Any]
    ) -> tuple[Decimal, Decimal, Decimal]:
        total = cls._scheduled_house_training_hours(start, end, policy)
        cap, shared_per_week, _supplemental_per_week = cls._house_training_envelope(policy)
        if total <= 0:
            return Decimal(0), Decimal(0), Decimal(0)
        shared = (total * Decimal(shared_per_week) / Decimal(cap)).quantize(
            _THREE, rounding=ROUND_HALF_UP
        )
        supplemental = total - shared
        return total, shared, supplemental

    @staticmethod
    def _policy_milli(
        factors: Mapping[str, Any], key: str, *, capped: bool = True, default: int = 1000
    ) -> Decimal:
        raw = factors.get(key, default)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise CommandRejectedError("house_training_policy_invalid")
        if capped and raw > 1000:
            raise CommandRejectedError("house_training_policy_invalid")
        if not capped and raw > 2000:
            raise CommandRejectedError("house_training_policy_invalid")
        return Decimal(raw) / Decimal(1000)

    @staticmethod
    def _scheduled_house_training_hours(
        start: CampaignTime, end: CampaignTime, policy: Mapping[str, Any]
    ) -> Decimal:
        if end < start:
            raise CommandRejectedError("house_development_cursor_invalid")
        if end == start:
            return Decimal(0)
        try:
            anchor = CampaignTime.parse(policy.get("cycle_anchor"))
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("house_training_policy_invalid") from exc
        schedule = policy.get("daily_active_hours")
        offset = policy.get("training_window_start_seconds", 0)
        if (
            not isinstance(schedule, list)
            or not schedule
            or len(schedule) > 31
            or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 24 for value in schedule)
            or isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
            or offset >= 86400
        ):
            raise CommandRejectedError("house_training_policy_invalid")
        anchor_dt = _campaign_datetime(anchor)
        start_dt = _campaign_datetime(start)
        end_dt = _campaign_datetime(end)
        day_seconds = 86400
        first_index = int((start_dt - anchor_dt).total_seconds() // day_seconds) - 1
        last_index = int((end_dt - anchor_dt).total_seconds() // day_seconds) + 1
        total_seconds = 0
        for day_index in range(first_index, last_index + 1):
            hours = schedule[day_index % len(schedule)]
            if hours <= 0:
                continue
            window_start = anchor_dt + timedelta(days=day_index, seconds=offset)
            window_end = window_start + timedelta(hours=hours)
            overlap_start = max(start_dt, window_start)
            overlap_end = min(end_dt, window_end)
            if overlap_end > overlap_start:
                total_seconds += int((overlap_end - overlap_start).total_seconds())
        return (Decimal(total_seconds) / Decimal(3600)).quantize(_THREE, rounding=ROUND_HALF_UP)

    @staticmethod
    def _distribution_value(distributions: Mapping[str, Any], key: str, field: str) -> Decimal:
        row = distributions.get(key)
        value = row.get(field) if isinstance(row, Mapping) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CommandRejectedError("house_cohort_development_invalid")
        return Decimal(str(value))

    @staticmethod
    def _bump_distribution(row: Dict[str, Any], points: int) -> None:
        if points <= 0:
            return
        for key in ("mean", "min", "max"):
            value = row.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CommandRejectedError("house_cohort_development_invalid")
            row[key] = float((Decimal(str(value)) + Decimal(points)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))

    def _settle_house_progression(
        self,
        house: Dict[str, Any],
        *,
        through: CampaignTime,
    ) -> Mapping[str, Any]:
        house_id = house.get("id")
        cohorts = house.get("cohorts")
        if not isinstance(house_id, str) or not house_id or not isinstance(cohorts, list):
            raise CommandRejectedError("house_owner_invalid")
        policy = self._house_training_policy(house_id)
        progressed: list[Mapping[str, Any]] = []
        unresolved_profiles = []
        for cohort in cohorts:
            profile = cohort.get("cohort_profile") if isinstance(cohort, Mapping) else None
            development = profile.get("development") if isinstance(profile, Mapping) else None
            if isinstance(development, Mapping):
                unresolved_profiles.append((cohort, profile, development))
        if not unresolved_profiles:
            return {"house_id": house_id, "through": str(through), "cohorts": [], "policy": "none_required"}
        if policy is None:
            if any(CampaignTime.parse(development.get("resolved_through")) < through for _cohort, _profile, development in unresolved_profiles):
                raise CommandRejectedError("house_training_policy_missing")
            return {"house_id": house_id, "through": str(through), "cohorts": [], "policy": "none"}
        curricula = policy.get("curricula")
        if not isinstance(curricula, Mapping):
            raise CommandRejectedError("house_training_policy_invalid")

        for cohort, profile, development in unresolved_profiles:
            try:
                prior = CampaignTime.parse(development.get("resolved_through"))
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("house_cohort_development_invalid") from exc
            if prior > through:
                raise CommandRejectedError("house_development_cursor_invalid")
            if prior == through:
                continue
            training_label = cohort.get("training")
            curriculum = curricula.get(training_label) if isinstance(training_label, str) else None
            if not isinstance(curriculum, Mapping):
                raise CommandRejectedError("house_training_curriculum_missing")
            targets = curriculum.get("targets")
            factors = curriculum.get("factors_milli")
            if not isinstance(targets, list) or not targets or not isinstance(factors, Mapping):
                raise CommandRejectedError("house_training_policy_invalid")
            total_weight = sum(
                target.get("weight_milli", 0)
                for target in targets
                if isinstance(target, Mapping)
                and isinstance(target.get("weight_milli"), int)
                and not isinstance(target.get("weight_milli"), bool)
            )
            if total_weight != 1000 or len(targets) > 12:
                raise CommandRejectedError("house_training_policy_invalid")
            active_hours, shared_hours, supplemental_hours = self._scheduled_house_training_components(
                prior, through, policy
            )
            distributions = profile.get("numeric_distributions")
            credits = development.get("credits")
            if not isinstance(distributions, dict) or not isinstance(credits, dict):
                raise CommandRejectedError("house_cohort_development_invalid")

            # Shared hours follow the authored curriculum. Supplemental hours are
            # routed to the cohort's weakest represented curriculum lane. Exact
            # rostered members perform the same selection from their own current
            # sheets in LivingWorldHouseExactMixin, so the aggregate projection
            # reflects individualized gap work without creating duplicate people.
            validated_targets: list[Mapping[str, Any]] = []
            weakest_target: str | None = None
            weakest_mean: Decimal | None = None
            for target in targets:
                if not isinstance(target, Mapping):
                    raise CommandRejectedError("house_training_policy_invalid")
                target_key = target.get("target")
                aptitude_key = target.get("aptitude")
                weight = target.get("weight_milli")
                if (
                    not isinstance(target_key, str)
                    or not target_key
                    or not isinstance(aptitude_key, str)
                    or not aptitude_key
                    or isinstance(weight, bool)
                    or not isinstance(weight, int)
                    or weight <= 0
                    or not isinstance(distributions.get(target_key), dict)
                ):
                    raise CommandRejectedError("house_training_policy_invalid")
                mean = self._distribution_value(distributions, target_key, "mean")
                if weakest_mean is None or (mean, target_key) < (weakest_mean, weakest_target or ""):
                    weakest_mean = mean
                    weakest_target = target_key
                validated_targets.append(target)

            hours_by_target: Dict[str, Decimal] = {}
            for target in validated_targets:
                target_key = str(target["target"])
                weight = int(target["weight_milli"])
                scheduled = (shared_hours * Decimal(weight) / Decimal(1000)).quantize(
                    _THREE, rounding=ROUND_HALF_UP
                )
                hours_by_target[target_key] = hours_by_target.get(target_key, Decimal(0)) + scheduled
            if weakest_target is not None and supplemental_hours > 0:
                hours_by_target[weakest_target] = hours_by_target.get(weakest_target, Decimal(0)) + supplemental_hours

            target_results: Dict[str, Any] = {}
            for target in validated_targets:
                if not isinstance(target, Mapping):
                    raise CommandRejectedError("house_training_policy_invalid")
                target_key = target.get("target")
                aptitude_key = target.get("aptitude")
                weight = target.get("weight_milli")
                if (
                    not isinstance(target_key, str)
                    or not target_key
                    or not isinstance(aptitude_key, str)
                    or not aptitude_key
                    or isinstance(weight, bool)
                    or not isinstance(weight, int)
                    or weight <= 0
                ):
                    raise CommandRejectedError("house_training_policy_invalid")
                target_row = distributions.get(target_key)
                if not isinstance(target_row, dict):
                    raise CommandRejectedError("house_training_target_missing")
                scheduled = hours_by_target.get(target_key, Decimal(0))
                aptitude_mean = self._distribution_value(distributions, aptitude_key, "mean")
                current_mean = self._distribution_value(distributions, target_key, "mean")
                aptitude = int(aptitude_mean.to_integral_value(rounding=ROUND_HALF_UP))
                current_value = int(current_mean.to_integral_value(rounding=ROUND_HALF_UP))
                residual = credits.get(target_key, 0)
                if isinstance(residual, bool) or not isinstance(residual, (int, float)) or residual < 0:
                    raise CommandRejectedError("house_cohort_development_invalid")
                outcome = settle_training(
                    TrainingInputs(
                        scheduled_hours=str(scheduled),
                        attendance=str(self._policy_milli(factors, "attendance_milli")),
                        available_instructor_hours=str(scheduled),
                        required_instructor_hours=str(scheduled),
                        facility_slots="1",
                        required_slots="1",
                        equipment_sets="1",
                        required_sets="1",
                        instructor_quality_factor=str(self._policy_milli(factors, "instructor_quality_milli", capped=False)),
                        facility_quality_factor=str(self._policy_milli(factors, "facility_quality_milli")),
                        equipment_factor=str(self._policy_milli(factors, "equipment_milli")),
                        health_factor=str(self._policy_milli(factors, "health_milli")),
                        recovery_factor=str(self._policy_milli(factors, "recovery_milli")),
                        relevance_factor=str(self._policy_milli(factors, "relevance_milli")),
                        difficulty_fit_factor=str(self._policy_milli(factors, "difficulty_fit_milli")),
                        aptitude=aptitude,
                        experience_modifier=str(self._policy_milli(factors, "experience_milli", capped=False)),
                        current_value=current_value,
                        residual_units=residual,
                        representation="rostered_cohort",
                    )
                )
                self._bump_distribution(target_row, outcome.points_gained)
                credits[target_key] = float(outcome.residual_units)
                target_results[target_key] = outcome.to_record()
            development["resolved_through"] = str(through)
            provenance = profile.setdefault("provenance", [])
            if not isinstance(provenance, list):
                raise CommandRejectedError("house_cohort_development_invalid")
            marker = f"house_training_settled:{prior}->{through}:active_hours={active_hours}"
            provenance.append(marker)
            del provenance[:-24]
            progressed.append(
                {
                    "cohort_id": cohort.get("id"),
                    "from": str(prior),
                    "through": str(through),
                    "active_hours": str(active_hours),
                    "shared_core_hours": str(shared_hours),
                    "supplemental_individual_hours": str(supplemental_hours),
                    "supplemental_focus": weakest_target,
                    "targets": target_results,
                }
            )
        return {
            "house_id": house_id,
            "through": str(through),
            "policy": str(policy.get("id") or house_id),
            "cohorts": progressed,
        }

    def _apply_house_progression_to_time_plan(self, plan: _BuiltPlan) -> _BuiltPlan:
        reviews = plan.result.get("house_reviews") if isinstance(plan.result, Mapping) else None
        if not isinstance(reviews, list) or not reviews:
            return plan
        writes = dict(plan.writes)
        base_json: Dict[str, Any] = {}
        final_json: Dict[str, Any] = {}
        summaries: list[Mapping[str, Any]] = []
        for review in reviews:
            if not isinstance(review, str) or "@" not in review or "x" not in review:
                raise CommandRejectedError("house_review_result_invalid")
            owner_ref, suffix = review.rsplit("@", 1)
            due_text = suffix.rsplit("x", 1)[0]
            try:
                through = CampaignTime.parse(due_text)
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("house_review_result_invalid") from exc
            raw = writes.get(owner_ref)
            if raw is None:
                raise CommandRejectedError("house_review_result_invalid")
            try:
                house = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                raise CommandRejectedError("house_owner_invalid") from exc
            if not isinstance(house, dict) or house.get("schema") != "house":
                raise CommandRejectedError("house_owner_invalid")
            base_json[owner_ref] = copy.deepcopy(house)
            summary = self._settle_house_progression(house, through=through)
            summaries.append(summary)
            writes[owner_ref] = _json_bytes(house)
            final_json[owner_ref] = house
        original_validator = plan.validator
        base_paths = tuple(sorted(plan.writes))
        expected_paths = tuple(sorted(writes))

        def validate(overlay: Any, manifest: Any) -> None:
            if original_validator is not None:
                original_validator(
                    _BasePlanOverlayView(overlay, base_paths=base_paths, base_json=base_json),
                    manifest,
                )
            if overlay.changed_paths != expected_paths:
                raise ValueError("living-world House progression changed write set after planning")
            for owner_ref, expected in final_json.items():
                if overlay.read_json(owner_ref) != expected:
                    raise ValueError("House progression after-image differs from settled plan")
                for cohort in expected.get("cohorts", []):
                    profile = cohort.get("cohort_profile") if isinstance(cohort, Mapping) else None
                    development = profile.get("development") if isinstance(profile, Mapping) else None
                    if isinstance(development, Mapping):
                        cursor = CampaignTime.parse(development.get("resolved_through"))
                        if cursor > CampaignTime.parse(expected.get("operating_process", {}).get("last_review")):
                            raise ValueError("House development cursor advanced beyond reviewed time")

        result = dict(plan.result)
        result["house_progression_reviews"] = [dict(summary) for summary in summaries]
        return _BuiltPlan(
            code=plan.code,
            affected_refs=expected_paths,
            writes=writes,
            result=result,
            validator=validate,
        )


__all__ = ["LivingWorldHouseMixin"]
