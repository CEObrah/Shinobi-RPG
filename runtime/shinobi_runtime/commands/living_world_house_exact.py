from __future__ import annotations

import copy
import json
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache, _json_bytes
from shinobi_runtime.commands.living_world_house import _BasePlanOverlayView
from shinobi_runtime.commands.paths import DEVELOPMENT_BANK_PATH
from shinobi_runtime.reducers import TrainingInputs, settle_training
from shinobi_runtime.sim.events import CampaignTime


_THREE = Decimal("0.001")


class LivingWorldHouseExactMixin:
    """Settle exact House cohort members without double-crediting sparse cohorts."""

    def _house_exact_member_cursor(
        self,
        record: Mapping[str, Any],
        member_ref: str,
        entries: Mapping[str, Any],
    ) -> CampaignTime:
        entry = entries.get(member_ref)
        raw = entry.get("resolved_through") if isinstance(entry, Mapping) else None
        if not isinstance(raw, str):
            if record.get("schema") == "shinobi_character":
                development = record.get("development")
                raw = development.get("last_settled_at") if isinstance(development, Mapping) else None
            elif record.get("schema") == "person":
                raw = record.get("resolved_through")
        try:
            return CampaignTime.parse(raw)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("house_exact_development_cursor_invalid") from exc

    @staticmethod
    def _exact_member_owner_type(record: Mapping[str, Any]) -> str:
        return "character" if record.get("schema") == "shinobi_character" else "person"

    def _settle_exact_house_members(
        self,
        *,
        house: Mapping[str, Any],
        through: CampaignTime,
        record_writes: Dict[str, bytes],
    ) -> list[Mapping[str, Any]]:
        house_id = house.get("id")
        if not isinstance(house_id, str):
            raise CommandRejectedError("house_owner_invalid")
        policy = self._house_training_policy(house_id)
        if policy is None:
            return []
        curricula = policy.get("curricula")
        if not isinstance(curricula, Mapping):
            raise CommandRejectedError("house_training_policy_invalid")
        external = set(x for x in house.get("externally_assigned_members", []) if isinstance(x, str)) if isinstance(house.get("externally_assigned_members"), list) else set()
        try:
            bank = json.loads(record_writes[DEVELOPMENT_BANK_PATH].decode("utf-8")) if DEVELOPMENT_BANK_PATH in record_writes else copy.deepcopy(self.repository.read_json(DEVELOPMENT_BANK_PATH))
        except (FileNotFoundError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CommandRejectedError("development_bank_invalid") from exc
        entries = bank.get("entries") if isinstance(bank, dict) else None
        if not isinstance(entries, dict):
            raise CommandRejectedError("development_bank_invalid")
        results: list[Mapping[str, Any]] = []
        touched_bank = False
        for cohort in house.get("cohorts", []):
            if not isinstance(cohort, Mapping):
                continue
            members = [x for x in cohort.get("members", []) if isinstance(x, str)] if isinstance(cohort.get("members"), list) else []
            if not members:
                continue
            label = cohort.get("training")
            curriculum = curricula.get(label) if isinstance(label, str) else None
            if not isinstance(curriculum, Mapping):
                raise CommandRejectedError("house_training_curriculum_missing")
            factors = curriculum.get("factors_milli")
            targets = curriculum.get("targets")
            if not isinstance(factors, Mapping) or not isinstance(targets, list):
                raise CommandRejectedError("house_training_policy_invalid")
            for member_ref in members:
                if member_ref in external:
                    continue
                try:
                    path, _digest, view = self._resolve_covered_owner_view(member_ref, cache=_OwnerResolutionCache())
                except CommandRejectedError:
                    continue
                if not isinstance(view, Mapping) or view.get("schema") not in ("shinobi_character", "person"):
                    continue
                if path in record_writes:
                    try:
                        record = json.loads(record_writes[path].decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise CommandRejectedError("house_exact_member_invalid") from exc
                else:
                    record = copy.deepcopy(dict(view))
                prior = self._house_exact_member_cursor(record, member_ref, entries)
                if prior > through:
                    raise CommandRejectedError("house_exact_development_cursor_invalid")
                if prior == through:
                    continue
                active_hours, shared_hours, supplemental_hours = self._scheduled_house_training_components(
                    prior, through, policy
                )
                entry = entries.get(member_ref)
                if entry is None:
                    entry = {"owner_type": self._exact_member_owner_type(record), "resolved_through": str(prior), "credits": {}}
                    entries[member_ref] = entry
                if not isinstance(entry, dict) or not isinstance(entry.get("credits"), dict):
                    raise CommandRejectedError("development_bank_invalid")
                if record.get("schema") == "shinobi_character":
                    development = record.get("development")
                    if isinstance(development, dict):
                        development.pop("last_settled_at", None)
                health_factor, recovery_factor = self._health_recovery_factor(record)
                outcomes: Dict[str, Any] = {}

                resolved_targets: list[tuple[Mapping[str, Any], str, Dict[str, Any], str, int]] = []
                weakest: tuple[int, str] | None = None
                for target in targets:
                    if not isinstance(target, Mapping):
                        raise CommandRejectedError("house_training_policy_invalid")
                    target_key, weight = target.get("target"), target.get("weight_milli")
                    if not isinstance(target_key, str) or isinstance(weight, bool) or not isinstance(weight, int) or weight <= 0:
                        raise CommandRejectedError("house_training_policy_invalid")
                    logical = target_key[6:] if target_key.startswith("stats.") else target_key
                    if record.get("schema") == "person":
                        parts = logical.split(".")
                        stats = record.get("stats")
                        container = stats.get(parts[0]) if len(parts) == 2 and isinstance(stats, dict) else None
                        if not isinstance(container, dict) or isinstance(container.get(parts[1]), bool) or not isinstance(container.get(parts[1]), int):
                            raise CommandRejectedError("house_exact_training_target_invalid")
                        leaf, starting_value = parts[1], int(container[parts[1]])
                    else:
                        container, leaf, starting_value = self._training_target(record, logical)
                    resolved_targets.append((target, logical, container, leaf, int(starting_value)))
                    candidate = (int(starting_value), logical)
                    if weakest is None or candidate < weakest:
                        weakest = candidate

                hours_by_target: Dict[str, Decimal] = {}
                for target, logical, _container, _leaf, _starting_value in resolved_targets:
                    weight = int(target["weight_milli"])
                    scheduled = (shared_hours * Decimal(weight) / Decimal(1000)).quantize(
                        _THREE, rounding=ROUND_HALF_UP
                    )
                    hours_by_target[logical] = hours_by_target.get(logical, Decimal(0)) + scheduled
                if weakest is not None and supplemental_hours > 0:
                    hours_by_target[weakest[1]] = hours_by_target.get(weakest[1], Decimal(0)) + supplemental_hours

                for target, logical, container, leaf, starting_value in resolved_targets:
                    aptitude = self._training_aptitude(record, logical)
                    scheduled = hours_by_target.get(logical, Decimal(0))
                    residual = entry["credits"].get(logical, 0)
                    outcome = settle_training(TrainingInputs(
                        scheduled_hours=str(scheduled), attendance=str(self._policy_milli(factors, "attendance_milli")),
                        available_instructor_hours=str(scheduled), required_instructor_hours=str(scheduled), facility_slots="1", required_slots="1",
                        equipment_sets="1", required_sets="1", instructor_quality_factor=str(self._policy_milli(factors, "instructor_quality_milli", capped=False)),
                        facility_quality_factor=str(self._policy_milli(factors, "facility_quality_milli")), equipment_factor=str(self._policy_milli(factors, "equipment_milli")),
                        health_factor=health_factor, recovery_factor=recovery_factor, relevance_factor=str(self._policy_milli(factors, "relevance_milli")),
                        difficulty_fit_factor=str(self._policy_milli(factors, "difficulty_fit_milli")), aptitude=aptitude,
                        experience_modifier=str(self._policy_milli(factors, "experience_milli", capped=False)), current_value=starting_value,
                        residual_units=residual, representation="exact",
                    ))
                    container[leaf] = outcome.ending_value
                    entry["credits"][logical] = float(outcome.residual_units)
                    outcomes[logical] = {"starting_value": starting_value, "ending_value": outcome.ending_value, "points_gained": outcome.points_gained, "residual_units": str(outcome.residual_units)}
                entry["resolved_through"] = str(through)
                if record.get("schema") == "person":
                    record["resolved_through"] = str(through)
                record_writes[path] = _json_bytes(record)
                touched_bank = True
                results.append({
                    "member_ref": member_ref,
                    "cohort_id": cohort.get("id"),
                    "from": str(prior),
                    "through": str(through),
                    "active_hours": str(active_hours),
                    "shared_core_hours": str(shared_hours),
                    "supplemental_individual_hours": str(supplemental_hours),
                    "supplemental_focus": weakest[1] if weakest is not None else None,
                    "outcomes": outcomes,
                })
        if touched_bank:
            record_writes[DEVELOPMENT_BANK_PATH] = _json_bytes(bank)
        return results

    def _apply_house_progression_to_time_plan(self, plan: _BuiltPlan) -> _BuiltPlan:
        plan = super()._apply_house_progression_to_time_plan(plan)
        summaries = plan.result.get("house_progression_reviews") if isinstance(plan.result, Mapping) else None
        if not isinstance(summaries, list) or not summaries:
            return plan
        writes = dict(plan.writes)
        exact_results: list[Mapping[str, Any]] = []
        for summary in summaries:
            if not isinstance(summary, Mapping):
                continue
            house_id = summary.get("house_id")
            through_raw = summary.get("through")
            if not isinstance(house_id, str) or not isinstance(through_raw, str):
                raise CommandRejectedError("house_progression_result_invalid")
            through = CampaignTime.parse(through_raw)
            house_path = next((path for path, raw in writes.items() if path.startswith("state/house/") and isinstance(raw, (bytes, bytearray)) and json.loads(raw.decode("utf-8")).get("id") == house_id), None)
            if house_path is None:
                raise CommandRejectedError("house_progression_result_invalid")
            house = json.loads(writes[house_path].decode("utf-8"))
            exact_results.extend(self._settle_exact_house_members(house=house, through=through, record_writes=writes))
        if not exact_results:
            return plan
        base_paths = tuple(sorted(plan.writes))
        expected_paths = tuple(sorted(writes))
        prior_validator = plan.validator
        refined_paths = tuple(
            sorted(
                path
                for path, raw in writes.items()
                if plan.writes.get(path) != raw
            )
        )
        base_json = {
            path: json.loads(plan.writes[path].decode("utf-8"))
            for path in refined_paths
            if path in plan.writes
        }
        expected_json = {
            path: json.loads(writes[path].decode("utf-8"))
            for path in refined_paths
        }

        def validate(overlay: Any, manifest: Any) -> None:
            if prior_validator is not None:
                prior_validator(
                    _BasePlanOverlayView(
                        overlay,
                        base_paths=base_paths,
                        base_json=base_json,
                    ),
                    manifest,
                )
            if overlay.changed_paths != expected_paths:
                raise ValueError("exact House progression changed write set after planning")
            for path, expected in expected_json.items():
                if overlay.read_json(path) != expected:
                    raise ValueError("exact House progression after-image differs from settled plan")

        result = dict(plan.result)
        result["house_exact_member_progression"] = [dict(row) for row in exact_results]
        return _BuiltPlan(code=plan.code, affected_refs=expected_paths, writes=writes, result=result, validator=validate)


__all__ = ["LivingWorldHouseExactMixin"]