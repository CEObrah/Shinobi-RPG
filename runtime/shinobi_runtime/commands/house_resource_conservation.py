"""Shared-resource guard for parallel House cohort development.

The House progression layer may settle several cohorts over the same scheduled
window.  This mixin keeps that useful parallel settlement while preventing each
cohort from independently assuming the full instructor pool and facility.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError


class HouseResourceConservationMixin:
    """Scale parallel House training by real shared instructor/facility capacity."""

    _house_parallel_resource_factor = Decimal("1")
    _house_parallel_resource_snapshot: Mapping[str, Any] | None = None

    def _house_resource_snapshot(self, house: Mapping[str, Any]) -> Mapping[str, Any]:
        home = house.get("home")
        cohorts = house.get("cohorts")
        if not isinstance(home, str) or not isinstance(cohorts, list):
            raise CommandRejectedError("house_owner_invalid")

        unresolved = []
        total_slots = 0
        instructor_candidates: list[str] = []
        leadership = house.get("leadership")
        if isinstance(leadership, Mapping):
            instructor_candidates.extend(
                value for value in leadership.values() if isinstance(value, str) and value
            )
        for cohort in cohorts:
            if not isinstance(cohort, Mapping):
                continue
            profile = cohort.get("cohort_profile")
            development = profile.get("development") if isinstance(profile, Mapping) else None
            if not isinstance(development, Mapping):
                continue
            count = cohort.get("aggregate_count")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise CommandRejectedError("house_cohort_development_invalid")
            if count <= 0:
                continue
            unresolved.append(str(cohort.get("id") or "cohort"))
            total_slots += count
            role = str(cohort.get("role") or "").lower()
            if "instructor" in role or "command" in role:
                for key in ("members", "roster_refs"):
                    refs = cohort.get(key)
                    if isinstance(refs, list):
                        instructor_candidates.extend(
                            ref for ref in refs if isinstance(ref, str) and ref
                        )

        available_instructors: list[str] = []
        for ref in dict.fromkeys(instructor_candidates):
            try:
                _path, record = self._resolve_actor_for_write(ref)
            except CommandRejectedError:
                continue
            if (
                record.get("life_status") in ("active", "alive")
                and record.get("current_location_id") == home
                and record.get("condition", {}).get("readiness") not in ("dead", "captured", "incapacitated")
            ):
                available_instructors.append(ref)

        required_instructors = len(unresolved)
        instructor_factor = (
            Decimal("1")
            if required_instructors <= 0
            else min(Decimal("1"), Decimal(len(available_instructors)) / Decimal(required_instructors))
        )

        facility_capacity = 0
        if total_slots > 0:
            try:
                slots, _quality = self._training_facility_capacity(
                    home,
                    required_slots=1,
                    base_quality_factor="1",
                    required_categories=(),
                    module_required=True,
                )
                facility_capacity = int(Decimal(str(slots)))
            except (CommandRejectedError, ValueError, ArithmeticError):
                facility_capacity = 0
        facility_factor = (
            Decimal("1")
            if total_slots <= 0
            else min(Decimal("1"), Decimal(max(0, facility_capacity)) / Decimal(total_slots))
        )
        factor = min(instructor_factor, facility_factor)
        return {
            "home": home,
            "parallel_cohort_refs": unresolved,
            "required_instructors": required_instructors,
            "available_instructor_refs": available_instructors,
            "required_facility_slots": total_slots,
            "available_facility_slots": facility_capacity,
            "resource_factor_milli": int((factor * Decimal(1000)).to_integral_value(rounding=ROUND_HALF_UP)),
        }

    def _policy_milli(
        self,
        factors: Mapping[str, Any],
        key: str,
        *,
        capped: bool = True,
        default: int = 1000,
    ) -> Decimal:
        value = super()._policy_milli(factors, key, capped=capped, default=default)
        if key == "attendance_milli":
            value *= self._house_parallel_resource_factor
        return value

    def _settle_house_progression(self, house: dict[str, Any], *, through: Any) -> Mapping[str, Any]:
        snapshot = self._house_resource_snapshot(house)
        factor = Decimal(snapshot["resource_factor_milli"]) / Decimal(1000)
        previous_factor = self._house_parallel_resource_factor
        previous_snapshot = self._house_parallel_resource_snapshot
        self._house_parallel_resource_factor = factor
        self._house_parallel_resource_snapshot = snapshot
        try:
            result = super()._settle_house_progression(house, through=through)
        finally:
            self._house_parallel_resource_factor = previous_factor
            self._house_parallel_resource_snapshot = previous_snapshot
        enriched = dict(result)
        enriched["parallel_resource_conservation"] = dict(snapshot)
        return enriched


__all__ = ["HouseResourceConservationMixin"]
