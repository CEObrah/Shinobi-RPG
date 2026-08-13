"""Deterministic character-lite capability variation for rostered House intake.

The intake reducer creates stable person cores and one cohort baseline. This
extension preserves that scalable representation while replacing zero-spread
capability summaries with deterministic, moment-consistent cohort distributions.
The existing person-sheet resolver then assigns each roster slot stable
individual values without materializing hundreds of heavyweight exact sheets.
"""
from __future__ import annotations

import json
import math
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.campaign_house_intake import CampaignCommandPlanner as _Base
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.people.cohorts import _moment_values
from shinobi_runtime.sim.events import CampaignTime

_RECRUITMENT_POLICY_PATH = "game/rules/recruitment/policies.json"
_DEFAULT_RELATIVE_SPAN_MILLI = 80
_DEFAULT_MINIMUM_SPAN = 2.0
_DEFAULT_MAXIMUM_SPAN = 6.0
_FIXED_PREFIXES = ("stats.resources.",)
_FIXED_PATHS = frozenset(("age_years",))
_NEUTRAL_INTAKE_APTITUDES = {
    "aptitude.academic_learning": 100.0,
    "aptitude.chakra_learning": 100.0,
    "aptitude.genjutsu_learning": 100.0,
    "aptitude.medical_learning": 100.0,
    "aptitude.nature_transformation_learning": 100.0,
    "aptitude.physical_learning": 100.0,
    "aptitude.sensory_learning": 100.0,
    "aptitude.social_learning": 100.0,
    "aptitude.tactical_learning": 100.0,
    "aptitude.technical_learning": 100.0,
}


def _varied_distribution(
    *,
    path: str,
    count: int,
    mean: float,
    policy: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("intake distribution count invalid")
    if isinstance(mean, bool) or not isinstance(mean, (int, float)) or not math.isfinite(float(mean)):
        raise ValueError("intake distribution mean invalid")
    fixed = path in _FIXED_PATHS or any(path.startswith(prefix) for prefix in _FIXED_PREFIXES)
    if count == 1 or fixed or float(mean) == 0.0:
        value = float(mean)
        return {"count": count, "mean": value, "sd": 0.0, "min": value, "max": value}

    variation = policy.get("cohort_variation") if isinstance(policy, Mapping) else None
    relative = (
        variation.get("relative_span_milli", _DEFAULT_RELATIVE_SPAN_MILLI)
        if isinstance(variation, Mapping)
        else _DEFAULT_RELATIVE_SPAN_MILLI
    )
    minimum_span = (
        variation.get("minimum_span", _DEFAULT_MINIMUM_SPAN)
        if isinstance(variation, Mapping)
        else _DEFAULT_MINIMUM_SPAN
    )
    maximum_span = (
        variation.get("maximum_span", _DEFAULT_MAXIMUM_SPAN)
        if isinstance(variation, Mapping)
        else _DEFAULT_MAXIMUM_SPAN
    )
    extra_fixed = variation.get("fixed_numeric_paths", []) if isinstance(variation, Mapping) else []
    if (
        isinstance(relative, bool)
        or not isinstance(relative, int)
        or not 0 <= relative <= 500
        or isinstance(minimum_span, bool)
        or not isinstance(minimum_span, (int, float))
        or isinstance(maximum_span, bool)
        or not isinstance(maximum_span, (int, float))
        or minimum_span < 0
        or maximum_span < minimum_span
        or not isinstance(extra_fixed, list)
        or any(not isinstance(value, str) or not value for value in extra_fixed)
    ):
        raise ValueError("intake cohort variation policy invalid")
    if path in extra_fixed:
        value = float(mean)
        return {"count": count, "mean": value, "sd": 0.0, "min": value, "max": value}

    center = float(mean)
    span = max(float(minimum_span), abs(center) * float(relative) / 1000.0)
    span = min(float(maximum_span), span)
    if center > 0:
        span = min(span, center)
    if span <= 1e-9:
        return {"count": count, "mean": center, "sd": 0.0, "min": center, "max": center}
    minimum = center - span
    maximum = center + span
    spread = span * math.sqrt(2.0 / count)
    summary = {
        "count": count,
        "mean": center,
        "sd": spread,
        "min": minimum,
        "max": maximum,
    }
    _moment_values(summary)
    return summary


class _OverlaySubstitution:
    def __init__(self, overlay: Any, *, changed_paths: tuple[str, ...], substitutions: Mapping[str, Any]):
        self._overlay = overlay
        self.changed_paths = changed_paths
        self._substitutions = substitutions

    def read_json(self, path: str) -> Any:
        if path in self._substitutions:
            return self._substitutions[path]
        return self._overlay.read_json(path)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._overlay, name)


class CampaignCommandPlanner(_Base):
    """Production planner with persistent per-slot character-lite intake stats."""

    def _institution_intake_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        plan = super()._institution_intake_resolution(command, meta, current_time)
        cohort_ref = plan.result.get("cohort_ref")
        institution_ref = plan.result.get("institution_ref")
        policy_ref = plan.result.get("policy_ref")
        accepted_count = plan.result.get("accepted_count")
        if (
            not all(isinstance(value, str) and value for value in (cohort_ref, institution_ref, policy_ref))
            or isinstance(accepted_count, bool)
            or not isinstance(accepted_count, int)
            or accepted_count <= 0
        ):
            raise CommandRejectedError("institution_intake_profile_invalid")
        house_path, _house = self._growth_house(institution_ref)
        raw_house = plan.writes.get(house_path)
        if raw_house is None:
            raise CommandRejectedError("institution_intake_profile_invalid")
        try:
            original_house = json.loads(raw_house.decode("utf-8"))
            varied_house = json.loads(raw_house.decode("utf-8"))
            policy_registry = self.repository.read_json(_RECRUITMENT_POLICY_PATH)
        except (UnicodeDecodeError, json.JSONDecodeError, FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("institution_intake_profile_invalid") from exc
        policies = policy_registry.get("policies") if isinstance(policy_registry, Mapping) else None
        policy = policies.get(policy_ref) if isinstance(policies, Mapping) else None
        cohorts = varied_house.get("cohorts") if isinstance(varied_house, dict) else None
        if not isinstance(policy, Mapping) or not isinstance(cohorts, list):
            raise CommandRejectedError("institution_intake_profile_invalid")
        matches = [row for row in cohorts if isinstance(row, dict) and row.get("id") == cohort_ref]
        if len(matches) != 1:
            raise CommandRejectedError("institution_intake_profile_invalid")
        profile = matches[0].get("cohort_profile")
        distributions = profile.get("numeric_distributions") if isinstance(profile, dict) else None
        if not isinstance(distributions, dict):
            raise CommandRejectedError("institution_intake_profile_invalid")

        # New intake cohorts need learning aptitudes as well as current skills;
        # otherwise normal cohort training cannot select the appropriate learning
        # rate. Neutral 100 baselines are varied per stable roster slot below.
        for path, mean in _NEUTRAL_INTAKE_APTITUDES.items():
            distributions.setdefault(
                path,
                {
                    "count": accepted_count,
                    "mean": mean,
                    "sd": 0.0,
                    "min": mean,
                    "max": mean,
                },
            )
        for path, summary in list(distributions.items()):
            if not isinstance(path, str) or not isinstance(summary, Mapping):
                raise CommandRejectedError("institution_intake_profile_invalid")
            try:
                distributions[path] = dict(
                    _varied_distribution(
                        path=path,
                        count=summary.get("count"),
                        mean=summary.get("mean"),
                        policy=policy,
                    )
                )
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("institution_intake_profile_invalid") from exc

        writes = dict(plan.writes)
        writes[house_path] = _json_bytes(varied_house)
        original_validator = plan.validator
        original_paths = tuple(sorted(plan.writes))

        def validate(overlay: Any, manifest: Any) -> None:
            original_validator(
                _OverlaySubstitution(
                    overlay,
                    changed_paths=original_paths,
                    substitutions={house_path: original_house},
                ),
                manifest,
            )
            if overlay.read_json(house_path) != varied_house:
                raise ValueError("institution intake varied House after-image mismatch")
            for row in distributions.values():
                _moment_values(row)

        result = dict(plan.result)
        result["character_lite_profiles"] = "stable_slot_specific_cohort_baseline"
        result["individualized_numeric_profiles"] = True
        result["individualized_learning_aptitudes"] = True
        return _BuiltPlan(
            code=plan.code,
            affected_refs=plan.affected_refs,
            writes=writes,
            result=result,
            validator=validate,
        )


__all__ = ["CampaignCommandPlanner", "_varied_distribution"]
