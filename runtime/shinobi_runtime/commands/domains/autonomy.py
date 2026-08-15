"""Causal time settlement and bounded autonomous-world command support."""

from __future__ import annotations

import copy
import hashlib
import math
import re
from typing import (
    Any,
    Dict,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.autonomy import (
    AutonomousPolicyBook,
    review_team,
)
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.mission_owner import MissionOwner, mission_owner_path
from shinobi_runtime.commands.core import _OwnerResolutionCache
from shinobi_runtime.commands.constants import TERMINAL_MISSION_STATES as _TERMINAL_MISSION_STATES
from shinobi_runtime.combat.capabilities import consolidate_training, weighted_state
from shinobi_runtime.commands.paths import (
    AUTONOMY_POLICY_PATH as _AUTONOMY_POLICY_PATH,
    COMMITMENT_REGISTRY_PATH as _COMMITMENT_REGISTRY_PATH,
    DEMOGRAPHY_POLICY_PATH as _DEMOGRAPHY_POLICY_PATH,
    POPULATION_REGISTRY_PATH as _POPULATION_REGISTRY_PATH,
)
from shinobi_runtime.reducers import (
    InformationClaim,
    PopulationTransfer,
    apply_transfer,
    deliver_claim,
    neutral_proportional_selection,
)
from shinobi_runtime.reducers.missions import (
    MissionTransitionError, ObjectiveDependencyError, SettlementConflictError, derive_mission_outcome, settle_mission, transition_mission, update_objective,
)
from shinobi_runtime.reducers import Mission, MissionObjective
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.information import InformationStore
from shinobi_runtime.sim.scheduler import (
    CausalSchedulerRegistry,
    SchedulerHost,
    one_shot_event,
)
from shinobi_runtime.sim.hosts import HostState



class AutonomyCommandsMixin:
    def _autonomy_policy_book(self) -> AutonomousPolicyBook:
        try:
            record = self.repository.read_json(_AUTONOMY_POLICY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("autonomy_policy_invalid") from exc
        try:
            return AutonomousPolicyBook.from_record(record)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("autonomy_policy_invalid") from exc
    def _settle_population_demography(
        self,
        registry: Dict[str, Any],
        *,
        at: CampaignTime,
        compacted_years: int,
        policy_ref: str,
    ) -> Mapping[str, Any]:
        try:
            policy_registry = self.repository.read_json(_DEMOGRAPHY_POLICY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("population_demography_policy_invalid") from exc
        if (
            not isinstance(policy_registry, Mapping)
            or policy_registry.get("policy_id") != policy_ref
        ):
            raise CommandRejectedError("population_demography_policy_invalid")
        birth_rate = policy_registry.get("annual_births_per_1000")
        death_rate = policy_registry.get("annual_deaths_per_1000")
        birth_categories = policy_registry.get("birth_dimension_categories")
        applies = policy_registry.get("applies_to_categories")
        if (
            any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (birth_rate, death_rate))
            or not isinstance(birth_categories, Mapping)
            or not isinstance(applies, list)
            or any(not isinstance(v, str) for v in applies)
            or compacted_years <= 0
        ):
            raise CommandRejectedError("population_demography_policy_invalid")
        pools = registry.get("pools")
        history = registry.get("demographic_reviews")
        if not isinstance(pools, dict) or not isinstance(history, list):
            raise CommandRejectedError("population_registry_invalid")
        results = []
        for pool_id in sorted(pools):
            record = pools[pool_id]
            if not isinstance(record, dict):
                raise CommandRejectedError("population_pool_invalid")
            before_total = record.get("count")
            if isinstance(before_total, bool) or not isinstance(before_total, int) or before_total < 0:
                raise CommandRejectedError("population_pool_invalid")
            demographic_flow = record.get("category") in applies
            total_births = 0
            total_deaths = 0
            for _ in range(compacted_years):
                current_pool = self._pool_reducer_view(pool_id, record)
                total = current_pool.total
                representation = record.get("representation")
                if not isinstance(representation, dict):
                    raise CommandRejectedError("population_representation_invalid")
                anonymous_count = representation.get("anonymous_count")
                rostered_count = representation.get("rostered_count")
                if (
                    isinstance(anonymous_count, bool) or not isinstance(anonymous_count, int) or anonymous_count < 0
                    or isinstance(rostered_count, bool) or not isinstance(rostered_count, int) or rostered_count < 0
                    or anonymous_count + rostered_count != total
                ):
                    raise CommandRejectedError("population_representation_invalid")
                # Aggregate demography only acts on anonymous representation.
                # Persistent identities die through explicit person/health events.
                deaths = min(anonymous_count, (total * death_rate) // 1000) if demographic_flow else 0
                births = (total * birth_rate) // 1000 if demographic_flow else 0
                profile = record.get("profile")
                if not isinstance(profile, dict):
                    raise CommandRejectedError("population_pool_invalid")
                dimensions = profile.get("dimension_counts")
                if not isinstance(dimensions, dict) or not dimensions:
                    raise CommandRejectedError("population_pool_invalid")
                if deaths:
                    selected = neutral_proportional_selection(current_pool, deaths)
                    for dim_name, values in dimensions.items():
                        if not isinstance(values, dict):
                            raise CommandRejectedError("population_pool_invalid")
                        for category, removed in selected[dim_name].items():
                            values[category] = values.get(category, 0) - removed
                            if values[category] < 0:
                                raise CommandRejectedError("population_demography_invalid")
                after_deaths = total - deaths

                # Age-band progression is analytic at the population host.  It
                # deliberately avoids per-person annual updates while ensuring
                # decades of elapsed world time actually move people through
                # life stages.  Fractions approximate uniform ages within each
                # band and preserve exact headcount.
                age_band = dimensions.get("age_band")
                if isinstance(age_band, dict):
                    child = int(age_band.get("child", 0))
                    adolescent = int(age_band.get("adolescent", 0))
                    adult = int(age_band.get("adult", 0))
                    elder = int(age_band.get("elder", 0))
                    child_out = min(child, max(0, child // 12))
                    adolescent_out = min(adolescent, max(0, adolescent // 6))
                    adult_out = min(adult, max(0, adult // 47))
                    age_band["child"] = child - child_out
                    age_band["adolescent"] = adolescent + child_out - adolescent_out
                    age_band["adult"] = adult + adolescent_out - adult_out
                    age_band["elder"] = elder + adult_out

                for dim_name, values in dimensions.items():
                    target_category = birth_categories.get(dim_name)
                    if births and not isinstance(target_category, str):
                        raise CommandRejectedError("population_demography_policy_invalid")
                    if births:
                        values[target_category] = values.get(target_category, 0) + births
                after_total = after_deaths + births
                record["count"] = after_total
                representation["anonymous_count"] = anonymous_count - deaths + births
                # rostered_count and rostered_person_refs remain unchanged;
                # exact-person death/life transitions are not invented by aggregate demography.
                category_counts = profile.get("category_counts")
                if not isinstance(category_counts, dict):
                    raise CommandRejectedError("population_pool_invalid")
                category_counts[record.get("category")] = after_total
                numeric = profile.get("numeric_distributions")
                if not isinstance(numeric, dict):
                    raise CommandRejectedError("population_pool_invalid")
                for metric, distribution in numeric.items():
                    if not isinstance(distribution, dict):
                        raise CommandRejectedError("population_pool_invalid")
                    old_mean = distribution.get("mean")
                    old_sd = distribution.get("sd")
                    if (
                        isinstance(old_mean, bool) or not isinstance(old_mean, (int, float))
                        or isinstance(old_sd, bool) or not isinstance(old_sd, (int, float))
                    ):
                        raise CommandRejectedError("population_pool_invalid")
                    if metric == "age_years" and after_total > 0:
                        survivor_mean = float(old_mean) + 1.0
                        survivor_second = float(old_sd) ** 2 + survivor_mean ** 2
                        new_mean = (survivor_mean * after_deaths) / after_total
                        new_second = (survivor_second * after_deaths) / after_total
                        new_var = max(0.0, new_second - new_mean**2)
                        distribution["mean"] = round(new_mean, 6)
                        distribution["sd"] = round(math.sqrt(new_var), 6)
                        old_min = distribution.get("min")
                        old_max = distribution.get("max")
                        distribution["min"] = 0 if births else ((old_min + 1) if isinstance(old_min, (int, float)) and not isinstance(old_min, bool) else 0)
                        if isinstance(old_max, (int, float)) and not isinstance(old_max, bool):
                            distribution["max"] = old_max + 1
                    distribution["count"] = after_total
                # Every dimension must still exactly partition the population.
                for values in dimensions.values():
                    if not isinstance(values, Mapping) or sum(values.values()) != after_total:
                        raise CommandRejectedError("population_demography_conservation_failed")
                record["status"] = "active" if after_total > 0 else "exhausted"
                total_births += births
                total_deaths += deaths
            record["last_changed_at"] = str(at)
            results.append({
                "pool_id": pool_id,
                "before": before_total,
                "births": total_births,
                "deaths": total_deaths,
                "after": record["count"],
                "aged_years": compacted_years,
            })
        review = {
            "id": f"demography.{at.year:04d}.{at.month:02d}.{at.day:02d}.{len(history)+1:04d}",
            "at": str(at),
            "policy_ref": policy_ref,
            "compacted_years": compacted_years,
            "pool_results": results,
        }
        history.append(review)
        del history[:-64]
        return review
    @staticmethod
    def _scaled_count_map(values: Mapping[str, Any], target: int) -> Dict[str, int]:
        """Scale an integer partition to an exact target without losing conservation."""
        if isinstance(target, bool) or not isinstance(target, int) or target < 0:
            raise CommandRejectedError("formation_scale_invalid")
        clean = [(str(k), int(v)) for k, v in values.items() if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v >= 0]
        if not clean:
            return {}
        total = sum(v for _k, v in clean)
        if total <= 0:
            out = {k: 0 for k, _v in clean}
            if target:
                out[clean[0][0]] = target
            return out
        raw = [(k, target * v / total) for k, v in clean]
        out = {k: int(amount) for k, amount in raw}
        remainder = target - sum(out.values())
        order = sorted(raw, key=lambda item: (-(item[1] - int(item[1])), item[0]))
        for index in range(remainder):
            out[order[index % len(order)][0]] += 1
        return out
    @classmethod
    def _scaled_operational_formation(
        cls,
        template: Mapping[str, Any],
        *,
        formation_id: str,
        name: str,
        target_personnel: int,
    ) -> Dict[str, Any]:
        """Create a larger aggregate formation from a saved 25-person template.

        This changes representation only.  Manpower conservation is handled by
        the force availability partition in the caller.  Capability
        distributions are inherited while all count partitions are rescaled to
        the new exact headcount.
        """
        if target_personnel < 2:
            raise CommandRejectedError("formation_scale_invalid")
        clone = copy.deepcopy(dict(template))
        clone["id"] = formation_id
        clone["name"] = name
        clone["personnel_total"] = target_personnel
        clone["authorized_personnel"] = target_personnel
        clone["role"] = "operational_ready"
        clone["activity_summary"] = None
        clone["lifecycle_origin"] = "mobilized"
        clone["operational_memory"] = {
            "engagements_total": 0, "aggregate_personnel_committed_total": 0,
            "killed_total": 0, "wounded_total": 0, "incapacitated_total": 0,
            "captured_total": 0, "victories_total": 0, "defeats_total": 0,
            "contested_total": 0, "replacements_total": 0,
            "last_combat_ref": None, "last_result_at": None, "last_outcome": None,
            "last_loss_milli": 0, "last_reconstituted_at": None,
            "inherited_from_ref": str(template.get("id")) if template.get("id") else None,
            "inherited_experience_milli": min(1000, int((template.get("operational_memory") or {}).get("engagements_total", 0) or 0) * 100),
        }

        command = clone.get("command_personnel")
        template_command_count = command.get("count") if isinstance(command, Mapping) else 0
        if not isinstance(template_command_count, int) or isinstance(template_command_count, bool) or template_command_count < 0:
            template_command_count = 0
        # Roughly one command/specialist cadre member per 25 personnel, bounded
        # so larger formations gain coordination staff without turning the
        # command cadre into a separate army.
        command_count = min(target_personnel - 1, max(template_command_count, target_personnel // 25))
        if isinstance(command, dict):
            command["count"] = command_count
            groups = command.get("groups")
            if isinstance(groups, list) and groups:
                group_counts = cls._scaled_count_map(
                    {str(i): (g.get("count", 0) if isinstance(g, Mapping) else 0) for i, g in enumerate(groups)},
                    command_count,
                )
                for i, group in enumerate(groups):
                    if not isinstance(group, dict):
                        continue
                    group_count = group_counts.get(str(i), 0)
                    group["count"] = group_count
                    ranks = group.get("rank_distribution")
                    if isinstance(ranks, Mapping):
                        group["rank_distribution"] = cls._scaled_count_map(ranks, group_count)
                    condition = group.get("condition")
                    if isinstance(condition, Mapping):
                        group["condition"] = cls._scaled_count_map(condition, group_count)

        components = clone.get("components")
        if not isinstance(components, list) or not components:
            raise CommandRejectedError("formation_scale_invalid")
        component_target = target_personnel - command_count
        component_counts = cls._scaled_count_map(
            {str(i): (row.get("count", 0) if isinstance(row, Mapping) else 0) for i, row in enumerate(components)},
            component_target,
        )
        for i, row in enumerate(components):
            if not isinstance(row, dict):
                raise CommandRejectedError("formation_scale_invalid")
            count = component_counts.get(str(i), 0)
            row["count"] = count
            row["id"] = f"{formation_id}.component.{i+1:02d}"
            ranks = row.get("rank_distribution")
            if isinstance(ranks, Mapping):
                row["rank_distribution"] = cls._scaled_count_map(ranks, count)
            condition = row.get("condition")
            if isinstance(condition, Mapping):
                row["condition"] = cls._scaled_count_map(condition, count)
        if command_count + sum(row.get("count", 0) for row in components if isinstance(row, Mapping)) != target_personnel:
            raise CommandRejectedError("formation_scale_invalid")
        return clone
    @classmethod
    def _resize_formation_strength(cls, formation: Dict[str, Any], target_personnel: int) -> None:
        """Resize only conserved headcount partitions of an existing formation.

        Doctrine, training, role, mission and capability distributions remain
        attached to the same formation identity.  This is used after casualties
        and during reconstitution so operational representation tracks the force
        availability partition rather than becoming stale.
        """
        if isinstance(target_personnel, bool) or not isinstance(target_personnel, int) or target_personnel < 0:
            raise CommandRejectedError("formation_strength_invalid")
        formation["personnel_total"] = target_personnel
        command = formation.get("command_personnel")
        if isinstance(command, dict):
            current = command.get("count", 0)
            if isinstance(current, bool) or not isinstance(current, int) or current < 0:
                current = 0
            target_command = min(target_personnel, max(0, min(current, max(1, target_personnel // 25) if target_personnel else 0)))
            command["count"] = target_command
            groups = command.get("groups")
            if isinstance(groups, list):
                group_counts = cls._scaled_count_map(
                    {str(i): (row.get("count", 0) if isinstance(row, Mapping) else 0) for i, row in enumerate(groups)},
                    target_command,
                )
                for i, row in enumerate(groups):
                    if not isinstance(row, dict):
                        continue
                    count = group_counts.get(str(i), 0)
                    row["count"] = count
                    if isinstance(row.get("rank_distribution"), Mapping):
                        row["rank_distribution"] = cls._scaled_count_map(row["rank_distribution"], count)
                    if isinstance(row.get("condition"), Mapping):
                        row["condition"] = cls._scaled_count_map(row["condition"], count)
        else:
            target_command = 0

        components = formation.get("components")
        if not isinstance(components, list):
            raise CommandRejectedError("formation_strength_invalid")
        target_components = max(0, target_personnel - target_command)
        component_counts = cls._scaled_count_map(
            {str(i): (row.get("count", 0) if isinstance(row, Mapping) else 0) for i, row in enumerate(components)},
            target_components,
        )
        for i, row in enumerate(components):
            if not isinstance(row, dict):
                raise CommandRejectedError("formation_strength_invalid")
            count = component_counts.get(str(i), 0)
            row["count"] = count
            if isinstance(row.get("rank_distribution"), Mapping):
                row["rank_distribution"] = cls._scaled_count_map(row["rank_distribution"], count)
            if isinstance(row.get("condition"), Mapping):
                row["condition"] = cls._scaled_count_map(row["condition"], count)
        if target_personnel == 0:
            formation["role"] = "depleted"
            formation["readiness"] = 0
            formation["cohesion"] = 0
            formation["morale"] = max(0, min(100, int(formation.get("morale", 0)) // 2))
    @staticmethod
    def _reserve_capability_state(row: Mapping[str, Any], *, equipment_methods: Sequence[str] = ()) -> Dict[str, Any]:
        fundamentals = row.get("fundamentals")
        methods = row.get("methods")
        experience = row.get("experience")
        if not isinstance(fundamentals, Mapping) or not isinstance(methods, Mapping) or isinstance(experience, bool) or not isinstance(experience, int):
            raise CommandRejectedError("force_reserve_capability_invalid")
        return {
            "source_capability_ref": "force_reserve",
            "fundamentals": {str(k): int(v) for k, v in fundamentals.items()},
            "methods": {str(k): int(v) for k, v in methods.items()},
            "equipment_methods": sorted({str(v) for v in equipment_methods if isinstance(v, str)}),
            "intake_fundamental_bias": {str(k): 0 for k in fundamentals},
            "intake_method_bias": {str(k): 0 for k in methods},
            "spread": max(1, min(50, int(row.get("spread", 14)))),
            "experience": max(0, min(200, experience)),
            "development_evidence": {"combat_exchanges": 0, "mission_events": 0, "training_hours": 0, "last_event_ref": None},
        }

    def _reserved_availability_claims(self, force_ref: str) -> Dict[str, int]:
        """Active force assignments reserve personnel inside availability classes."""
        try:
            assignments = self.repository.read_json("state/org/assignments.json")
        except (FileNotFoundError, ValueError):
            return {}
        records = assignments.get("records") if isinstance(assignments, Mapping) else None
        if not isinstance(records, list):
            return {}
        reserved: Dict[str, int] = {}
        for record in records:
            if (
                not isinstance(record, Mapping)
                or record.get("status", "active") != "active"
                or record.get("source_owner") != force_ref
            ):
                continue
            source = record.get("source_availability_class")
            count = record.get("allocated_count")
            if not isinstance(source, str) or isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                continue
            reserved[source] = reserved.get(source, 0) + count
        return reserved

    @classmethod
    def _reserve_draw(cls, force: Dict[str, Any], availability_class: str, count: int) -> Dict[str, Any]:
        reserve = force.get("reserve_capability")
        row = reserve.get(availability_class) if isinstance(reserve, Mapping) else None
        if not isinstance(row, dict):
            raise CommandRejectedError("force_reserve_capability_invalid")
        row_count = row.get("count")
        if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < count or count <= 0:
            raise CommandRejectedError("force_reserve_capability_insufficient")
        state = cls._reserve_capability_state(row)
        row["count"] = row_count - count
        return state

    @staticmethod
    def _reserve_remove(force: Dict[str, Any], availability_class: str, state: Mapping[str, Any], count: int) -> None:
        """Remove a known capability contribution from an aggregate reserve row.

        This is used only when a previously known contribution is moved between
        reserve classes.  It preserves weighted sufficient statistics instead of
        resetting a class to a generic template.
        """
        if count <= 0:
            return
        reserve = force.get("reserve_capability")
        row = reserve.get(availability_class) if isinstance(reserve, Mapping) else None
        if not isinstance(row, dict):
            raise CommandRejectedError("force_reserve_capability_invalid")
        old_count = row.get("count")
        fundamentals = row.get("fundamentals")
        methods = row.get("methods")
        outgoing_f = state.get("fundamentals")
        outgoing_m = state.get("methods")
        outgoing_exp = state.get("experience")
        if (
            isinstance(old_count, bool) or not isinstance(old_count, int) or old_count < count
            or not isinstance(fundamentals, dict) or not isinstance(methods, dict)
            or not isinstance(outgoing_f, Mapping) or not isinstance(outgoing_m, Mapping)
            or isinstance(outgoing_exp, bool) or not isinstance(outgoing_exp, int)
        ):
            raise CommandRejectedError("force_reserve_capability_invalid")
        old_spread = row.get("spread", 14)
        outgoing_spread = state.get("spread", 14)
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (old_spread, outgoing_spread)):
            raise CommandRejectedError("force_reserve_capability_invalid")
        old_fundamentals = dict(fundamentals)
        old_methods = dict(methods)
        remaining = old_count - count
        if remaining > 0:
            for key in fundamentals:
                outgoing = outgoing_f.get(key)
                if isinstance(outgoing, bool) or not isinstance(outgoing, int):
                    raise CommandRejectedError("force_reserve_capability_invalid")
                numerator = int(fundamentals[key]) * old_count - outgoing * count
                fundamentals[key] = max(0, min(200, (numerator + remaining // 2) // remaining))
            for key in methods:
                outgoing = outgoing_m.get(key)
                if isinstance(outgoing, bool) or not isinstance(outgoing, int):
                    raise CommandRejectedError("force_reserve_capability_invalid")
                numerator = int(methods[key]) * old_count - outgoing * count
                methods[key] = max(0, min(200, (numerator + remaining // 2) // remaining))
            old_exp = row.get("experience", 0)
            if isinstance(old_exp, bool) or not isinstance(old_exp, int):
                raise CommandRejectedError("force_reserve_capability_invalid")
            row["experience"] = max(0, min(200, (old_exp * old_count - outgoing_exp * count + remaining // 2) // remaining))
            # Invert a pooled second-moment approximation across the shared
            # capability dimensions so known transfers do not silently erase
            # or duplicate heterogeneity.
            dims = list(fundamentals) + list(methods)
            var_sum = 0
            for key in fundamentals:
                old_mean = int(old_fundamentals[key])
                out_mean = int(outgoing_f[key])
                rem_mean = int(fundamentals[key])
                second = (old_count * (old_spread * old_spread + old_mean * old_mean) - count * (outgoing_spread * outgoing_spread + out_mean * out_mean)) // remaining
                var_sum += max(1, second - rem_mean * rem_mean)
            for key in methods:
                old_mean = int(old_methods[key])
                out_mean = int(outgoing_m[key])
                rem_mean = int(methods[key])
                second = (old_count * (old_spread * old_spread + old_mean * old_mean) - count * (outgoing_spread * outgoing_spread + out_mean * out_mean)) // remaining
                var_sum += max(1, second - rem_mean * rem_mean)
            from math import isqrt
            row["spread"] = max(1, min(50, isqrt(max(1, var_sum // max(1, len(dims))))))
        row["count"] = remaining

    @staticmethod
    def _reserve_add(force: Dict[str, Any], availability_class: str, state: Mapping[str, Any], count: int) -> None:
        if count <= 0:
            return
        reserve = force.get("reserve_capability")
        row = reserve.get(availability_class) if isinstance(reserve, Mapping) else None
        if not isinstance(row, dict):
            raise CommandRejectedError("force_reserve_capability_invalid")
        old_count = row.get("count")
        if isinstance(old_count, bool) or not isinstance(old_count, int) or old_count < 0:
            raise CommandRejectedError("force_reserve_capability_invalid")
        try:
            if old_count > 0:
                combined = weighted_state(((AutonomyCommandsMixin._reserve_capability_state(row), old_count), (state, count)))
            else:
                combined = copy.deepcopy(dict(state))
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("force_reserve_capability_invalid") from exc
        row["fundamentals"] = dict(combined["fundamentals"])
        row["methods"] = dict(combined["methods"])
        row["spread"] = max(1, min(50, int(combined.get("spread", 14))))
        row["experience"] = max(0, min(200, int(combined.get("experience", 0))))
        row["count"] = old_count + count

    @staticmethod
    def _adapt_reserve_to_component(component: Mapping[str, Any], reserve_state: Mapping[str, Any]) -> Dict[str, Any]:
        current = component.get("capability_state")
        if not isinstance(current, Mapping):
            raise CommandRejectedError("formation_capability_state_invalid")
        incoming_f = reserve_state.get("fundamentals")
        incoming_m = reserve_state.get("methods")
        f_bias = current.get("intake_fundamental_bias")
        m_bias = current.get("intake_method_bias")
        if not isinstance(incoming_f, Mapping) or not isinstance(incoming_m, Mapping) or not isinstance(f_bias, Mapping) or not isinstance(m_bias, Mapping):
            raise CommandRejectedError("formation_capability_state_invalid")
        fundamentals = {
            key: max(0, min(200, int(incoming_f.get(key, 0)) + int(f_bias.get(key, 0))))
            for key in current.get("fundamentals", {})
        }
        methods = {
            key: max(0, min(200, int(incoming_m.get(key, 0)) + int(m_bias.get(key, 0))))
            for key in current.get("methods", {})
        }
        return {
            "source_capability_ref": current.get("source_capability_ref", "force_reserve"),
            "fundamentals": fundamentals,
            "methods": methods,
            "equipment_methods": list(current.get("equipment_methods", ())),
            "equipment_readiness_milli": max(0, min(1000, int(current.get("equipment_readiness_milli", 1000)))),
            "intake_fundamental_bias": dict(f_bias),
            "intake_method_bias": dict(m_bias),
            "spread": max(1, min(50, int(reserve_state.get("spread", current.get("spread", 14))))),
            "experience": max(0, min(200, int(reserve_state.get("experience", 0)))),
            "development_evidence": {"combat_exchanges": 0, "mission_events": 0, "training_hours": 0, "last_event_ref": None},
        }

    @staticmethod
    def _formation_capability_composition(formation: Mapping[str, Any]) -> Dict[str, Any]:
        rows = []
        for component in formation.get("components", ()): 
            if not isinstance(component, Mapping):
                continue
            count = component.get("count")
            state = component.get("capability_state")
            if isinstance(count, int) and not isinstance(count, bool) and count > 0 and isinstance(state, Mapping):
                rows.append((state, count))
        if not rows:
            raise CommandRejectedError("formation_capability_state_invalid")
        return weighted_state(rows)

    @staticmethod
    def _validate_reserve_counts(force: Mapping[str, Any]) -> None:
        availability = force.get("availability")
        reserve = force.get("reserve_capability")
        if not isinstance(availability, Mapping) or not isinstance(reserve, Mapping):
            raise CommandRejectedError("force_reserve_capability_invalid")
        for key, value in availability.items():
            if key == "deployed":
                continue
            row = reserve.get(key)
            if not isinstance(row, Mapping) or row.get("count") != value:
                raise CommandRejectedError("force_reserve_capability_drift")


    def _apply_autonomous_formation_action(
        self,
        *,
        kind: str,
        owner_identity: str,
        actor: str,
        payload: Mapping[str, Any],
        at: CampaignTime,
        command: CommandEnvelope,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
        classification: str,
    ) -> Mapping[str, Any]:
        """Apply one bounded force-formation lifecycle action.

        Factions and institutions share this exact implementation.  The saved
        force remains manpower authority; the formation registry is only the
        operational representation of personnel currently marked deployed.
        """

        def load(path: str) -> Dict[str, Any]:
            if path in record_writes:
                return record_writes[path]
            try:
                value = self.repository.read_json(path)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("autonomous_owner_invalid") from exc
            if not isinstance(value, dict):
                raise CommandRejectedError("autonomous_owner_invalid")
            record_writes[path] = copy.deepcopy(value)
            return record_writes[path]

        path = payload.get("formation_registry_ref")
        force_ref = payload.get("force_ref")
        if not isinstance(path, str) or not isinstance(force_ref, str):
            return {"kind": kind, "skipped": "no_force_neighborhood"}
        registry = load(path)
        formations = registry.get("formations")
        if not isinstance(formations, list) or not formations:
            return {"kind": kind, "skipped": "no_formations"}
        try:
            force_path, _digest, force_view = self._resolve_covered_owner_view(
                force_ref, cache=_OwnerResolutionCache()
            )
        except CommandRejectedError:
            return {"kind": kind, "skipped": "force_unresolved"}
        if not isinstance(force_view, Mapping) or force_view.get("schema") != "force":
            return {"kind": kind, "skipped": "force_unresolved"}
        force = load(force_path)
        force_total = force.get("total")
        availability = force.get("availability")
        if not isinstance(force_total, int) or isinstance(force_total, bool) or not isinstance(availability, dict):
            raise CommandRejectedError("autonomous_force_invalid")
        self._validate_reserve_counts(force)
        reserved_claims = self._reserved_availability_claims(force_ref)
        for source_class, reserved_count in reserved_claims.items():
            current = availability.get(source_class)
            if isinstance(current, bool) or not isinstance(current, int) or current < reserved_count:
                raise CommandRejectedError("force_assignment_reservation_drift")
        represented = sum(
            row.get("personnel_total", 0)
            for row in formations
            if isinstance(row, Mapping)
            and isinstance(row.get("personnel_total"), int)
            and not isinstance(row.get("personnel_total"), bool)
        )
        deployed = availability.get("deployed")
        if not isinstance(deployed, int) or isinstance(deployed, bool):
            raise CommandRejectedError("autonomous_force_invalid")
        if represented > deployed:
            return {
                "kind": kind,
                "skipped": "formation_force_overrepresentation",
                "represented": represented,
                "deployed": deployed,
            }

        stable = int.from_bytes(
            hashlib.sha256(f"{owner_identity}\x00{at}\x00{kind}".encode()).digest()[:8],
            "big",
        )

        def normalized_ref(value: Any) -> Optional[str]:
            if not isinstance(value, str) or not value:
                return None
            return value.split(":", 1)[1] if value.startswith("formation:") else value

        def exact_formation(value: Any) -> Optional[Dict[str, Any]]:
            wanted = normalized_ref(value)
            if wanted is None:
                return None
            for row in formations:
                if isinstance(row, dict) and row.get("id") == wanted:
                    return row
            return None

        requested_ref = normalized_ref(payload.get("formation_ref"))
        if requested_ref is not None:
            template = exact_formation(requested_ref)
            if template is None:
                return {"kind": kind, "skipped": "formation_not_found"}
        else:
            candidates = [row for row in formations if isinstance(row, dict)]
            if not candidates:
                raise CommandRejectedError("formation_registry_invalid")
            def need_score(row: Mapping[str, Any]) -> tuple[int, int, str]:
                readiness = int(row.get("readiness", 50) or 50)
                cohesion = int(row.get("cohesion", 50) or 50)
                morale = int(row.get("morale", 50) or 50)
                memory = row.get("operational_memory") if isinstance(row.get("operational_memory"), Mapping) else {}
                recent_loss = int(memory.get("last_loss_milli", 0) or 0)
                inherited_experience = int(memory.get("inherited_experience_milli", 0) or 0)
                replacement_burden = int(memory.get("replacements_total", 0) or 0)
                health = readiness + cohesion + morale + inherited_experience // 100 - min(25, replacement_burden // 10)
                return (health, -recent_loss, str(row.get("id", "")))
            if kind == "formation_drill":
                template = min(candidates, key=need_score)
            elif kind == "formation_expand":
                template = max(candidates, key=lambda row: (need_score(row)[0] - max(0, -need_score(row)[1]) // 20, str(row.get("id", ""))))
            else:
                ordered = sorted(candidates, key=lambda row: str(row.get("id", "")))
                template = ordered[stable % len(ordered)]

        if kind == "formation_drill":
            step = max(1, min(4, int(payload.get("compacted_reviews", 1))))
            for key, ceiling in (("readiness", 95), ("cohesion", 95), ("morale", 92)):
                value = template.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    template[key] = min(ceiling, value + step)
            familiarity = template.get("doctrine_familiarity", 50)
            if isinstance(familiarity, int) and not isinstance(familiarity, bool):
                template["doctrine_familiarity"] = min(200, familiarity + step)
            for component in template.get("components", ()):
                if not isinstance(component, dict) or not isinstance(component.get("capability_state"), Mapping):
                    continue
                component["capability_state"] = consolidate_training(
                    component["capability_state"], hours=4 * step
                )
            consequence = str(template.get("id"))
            event_kind = "formation_readiness_drill"

        elif kind == "formation_expand":
            requested_size = payload.get("formation_size")
            max_operational = payload.get("max_operational_personnel")
            if isinstance(requested_size, bool) or not isinstance(requested_size, int) or requested_size <= 0:
                requested_size = int(template.get("personnel_total", 25))
            if isinstance(max_operational, bool) or not isinstance(max_operational, int) or max_operational <= 0:
                max_operational = force_total
            remaining_cap = max(0, min(force_total, max_operational) - represented)
            if remaining_cap <= 0:
                return {"kind": kind, "skipped": "operational_representation_cap_reached"}
            sources = ("ready_24h", "mobilizable_7d", "mobilizable_30d")
            available_to_mobilize = sum(
                max(0, availability.get(key, 0) - reserved_claims.get(key, 0))
                for key in sources
                if isinstance(availability.get(key, 0), int)
                and not isinstance(availability.get(key, 0), bool)
            )
            multiplier = max(1, min(4, int(payload.get("compacted_reviews", 1))))
            target = min(remaining_cap, available_to_mobilize, requested_size * multiplier)
            if target < 2:
                return {"kind": kind, "skipped": "insufficient_mobilizable_personnel"}
            suffix = hashlib.sha256(
                f"{owner_identity}\x00{at}\x00{len(formations)}".encode()
            ).hexdigest()[:10]
            owner_slug = re.sub(r"[^a-z0-9]+", ".", str(owner_identity).lower()).strip(".")
            formation_id = f"formation.mobilized.{owner_slug}.{suffix}"
            clone = self._scaled_operational_formation(
                template,
                formation_id=formation_id,
                name=f"{str(owner_identity)} Operational Formation {len(formations)+1}",
                target_personnel=target,
            )
            clone["lifecycle_origin"] = "mobilized"
            template_familiarity = template.get("doctrine_familiarity", 60)
            if isinstance(template_familiarity, int) and not isinstance(template_familiarity, bool):
                clone["doctrine_familiarity"] = max(25, min(template_familiarity, 55))
            mobilization_anchor_ref = force.get("mobilization_anchor_ref")
            if not isinstance(mobilization_anchor_ref, str) or not mobilization_anchor_ref.startswith("place."):
                raise CommandRejectedError("formation_mobilization_anchor_missing")
            graph = self._location_graph()
            if graph.place(mobilization_anchor_ref) is None:
                raise CommandRejectedError("formation_mobilization_anchor_invalid")
            clone["location_ref"] = mobilization_anchor_ref
            mobilize_remaining = target
            incoming_rows = []
            for source in sources:
                if mobilize_remaining <= 0:
                    break
                current = availability.get(source, 0)
                if not isinstance(current, int) or isinstance(current, bool) or current <= 0:
                    continue
                free_current = max(0, current - reserved_claims.get(source, 0))
                if free_current <= 0:
                    continue
                moved = min(free_current, mobilize_remaining)
                incoming_rows.append((self._reserve_draw(force, source, moved), moved))
                availability[source] -= moved
                availability["deployed"] += moved
                mobilize_remaining -= moved
            if mobilize_remaining != 0 or sum(availability.values()) != force_total:
                raise CommandRejectedError("autonomous_force_mobilization_invalid")
            incoming_state = weighted_state(incoming_rows)
            for component in clone.get("components", ()):
                if isinstance(component, dict) and isinstance(component.get("capability_state"), Mapping):
                    component["capability_state"] = self._adapt_reserve_to_component(component, incoming_state)
            formations.append(clone)
            consequence = clone["id"]
            event_kind = "formation_created"

        elif kind == "formation_reconstitute":
            target_formation = template
            current_strength = int(target_formation.get("personnel_total", 0))
            desired = payload.get("target_personnel")
            if isinstance(desired, bool) or not isinstance(desired, int) or desired <= 0:
                desired = target_formation.get("authorized_personnel")
            if isinstance(desired, bool) or not isinstance(desired, int) or desired <= 0:
                desired = payload.get("formation_size")
            if isinstance(desired, bool) or not isinstance(desired, int) or desired <= 0:
                desired = max(2, current_strength)
            max_operational = payload.get("max_operational_personnel")
            if isinstance(max_operational, bool) or not isinstance(max_operational, int) or max_operational <= 0:
                max_operational = force_total
            remaining_cap = max(0, min(force_total, max_operational) - represented)
            deficit = max(0, desired - current_strength)
            if deficit <= 0:
                return {"kind": kind, "skipped": "formation_at_or_above_target", "formation_ref": target_formation.get("id")}
            sources = ("ready_24h", "mobilizable_7d", "mobilizable_30d")
            available_to_mobilize = sum(
                max(0, availability.get(key, 0) - reserved_claims.get(key, 0)) for key in sources
                if isinstance(availability.get(key, 0), int) and not isinstance(availability.get(key, 0), bool)
            )
            add_count = min(deficit, remaining_cap, available_to_mobilize)
            if add_count <= 0:
                return {"kind": kind, "skipped": "insufficient_reconstitution_manpower", "formation_ref": target_formation.get("id")}
            old_components = [
                (int(row.get("count", 0)), copy.deepcopy(row.get("capability_state")))
                for row in target_formation.get("components", ()) if isinstance(row, Mapping)
            ]
            remaining = add_count
            incoming_rows = []
            for source in sources:
                if remaining <= 0:
                    break
                current = availability.get(source, 0)
                if not isinstance(current, int) or isinstance(current, bool) or current <= 0:
                    continue
                free_current = max(0, current - reserved_claims.get(source, 0))
                if free_current <= 0:
                    continue
                moved = min(free_current, remaining)
                incoming_rows.append((self._reserve_draw(force, source, moved), moved))
                availability[source] -= moved
                availability["deployed"] += moved
                remaining -= moved
            if remaining != 0:
                raise CommandRejectedError("autonomous_force_reconstitution_invalid")
            incoming_state = weighted_state(incoming_rows)
            strength_target = current_strength + add_count
            prior_familiarity = target_formation.get("doctrine_familiarity", 50)
            if isinstance(prior_familiarity, int) and not isinstance(prior_familiarity, bool) and strength_target > 0:
                target_formation["doctrine_familiarity"] = max(0, min(200, (prior_familiarity * current_strength + 40 * add_count) // strength_target))
            self._resize_formation_strength(target_formation, strength_target)
            for index, component in enumerate(target_formation.get("components", ())):
                if not isinstance(component, dict) or index >= len(old_components):
                    continue
                old_count, old_state = old_components[index]
                new_count = int(component.get("count", 0))
                added = max(0, new_count - old_count)
                if added <= 0 or not isinstance(old_state, Mapping):
                    continue
                replacement_state = self._adapt_reserve_to_component(component, incoming_state)
                component["capability_state"] = weighted_state(((old_state, old_count), (replacement_state, added))) if old_count > 0 else replacement_state
            target_formation["role"] = "operational_ready" if strength_target >= desired else "reconstituting"
            memory = target_formation.get("operational_memory")
            if not isinstance(memory, dict):
                memory = {
                    "engagements_total": 0, "aggregate_personnel_committed_total": 0,
                    "killed_total": 0, "wounded_total": 0, "incapacitated_total": 0,
                    "captured_total": 0, "victories_total": 0, "defeats_total": 0,
                    "contested_total": 0, "replacements_total": 0,
                    "last_combat_ref": None, "last_result_at": None, "last_outcome": None,
                    "last_loss_milli": 0, "last_reconstituted_at": None, "inherited_from_ref": None,
                    "inherited_experience_milli": 0,
                }
                target_formation["operational_memory"] = memory
            memory["replacements_total"] = int(memory.get("replacements_total", 0)) + add_count
            memory["last_reconstituted_at"] = str(at)
            for key, ceiling in (("readiness", 90), ("cohesion", 90), ("morale", 88)):
                value = target_formation.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    target_formation[key] = min(ceiling, value + max(1, add_count // max(25, desired // 10 or 1)))
            if sum(availability.values()) != force_total:
                raise CommandRejectedError("autonomous_force_reconstitution_invalid")
            consequence = str(target_formation.get("id"))
            event_kind = "formation_reconstituted"

        elif kind == "formation_split":
            current_strength = template.get("personnel_total")
            split_count = payload.get("split_personnel")
            if (
                isinstance(current_strength, bool) or not isinstance(current_strength, int)
                or isinstance(split_count, bool) or not isinstance(split_count, int)
                or split_count <= 0 or split_count >= current_strength
            ):
                return {"kind": kind, "skipped": "formation_split_invalid"}
            suffix = hashlib.sha256(
                f"{template.get('id')}\x00{at}\x00split\x00{split_count}".encode()
            ).hexdigest()[:10]
            child_id = f"formation.split.{suffix}"
            child = copy.deepcopy(template)
            child["id"] = child_id
            child["name"] = f"{template.get('name', template.get('id'))} Detachment"
            child["lifecycle_origin"] = "split"
            child["operational_memory"] = {
                "engagements_total": 0, "aggregate_personnel_committed_total": 0,
                "killed_total": 0, "wounded_total": 0, "incapacitated_total": 0,
                "captured_total": 0, "victories_total": 0, "defeats_total": 0,
                "contested_total": 0, "replacements_total": 0,
                "last_combat_ref": None, "last_result_at": None, "last_outcome": None,
                "last_loss_milli": 0, "last_reconstituted_at": None,
                "inherited_from_ref": str(template.get("id")) if template.get("id") else None,
                "inherited_experience_milli": min(1000, int((template.get("operational_memory") or {}).get("engagements_total", 0) or 0) * 100 + int((template.get("operational_memory") or {}).get("inherited_experience_milli", 0) or 0) // 2),
            }
            self._resize_formation_strength(template, current_strength - split_count)
            self._resize_formation_strength(child, split_count)
            template["authorized_personnel"] = current_strength - split_count
            child["authorized_personnel"] = split_count
            for index, row in enumerate(child.get("components", []), start=1):
                if isinstance(row, dict):
                    row["id"] = f"{child_id}.component.{index:02d}"
            formations.append(child)
            consequence = child_id
            event_kind = "formation_split"

        elif kind == "formation_merge":
            secondary = exact_formation(payload.get("secondary_formation_ref"))
            if secondary is None or secondary is template:
                return {"kind": kind, "skipped": "formation_merge_target_invalid"}
            if secondary.get("force_ref") != template.get("force_ref"):
                return {"kind": kind, "skipped": "formation_merge_force_mismatch"}
            def profile(row: Mapping[str, Any]) -> tuple[Any, ...]:
                components = row.get("components") if isinstance(row.get("components"), list) else []
                component_profile = tuple(
                    (
                        item.get("troop_type"), item.get("role"), item.get("tendency_profile_ref"),
                    )
                    for item in components if isinstance(item, Mapping)
                )
                return (row.get("doctrine_ref"), row.get("training_ref"), component_profile)
            if profile(template) != profile(secondary):
                return {"kind": kind, "skipped": "formation_merge_incompatible"}
            primary_count = int(template.get("personnel_total", 0))
            secondary_count = int(secondary.get("personnel_total", 0))
            total_count = primary_count + secondary_count
            if primary_count <= 0 or secondary_count <= 0 or total_count > force_total:
                return {"kind": kind, "skipped": "formation_merge_invalid"}
            merged_component_states = []
            primary_components = template.get("components", [])
            secondary_components = secondary.get("components", [])
            if len(primary_components) != len(secondary_components):
                return {"kind": kind, "skipped": "formation_merge_incompatible"}
            for a_component, b_component in zip(primary_components, secondary_components):
                if not isinstance(a_component, Mapping) or not isinstance(b_component, Mapping):
                    return {"kind": kind, "skipped": "formation_merge_incompatible"}
                a_count = int(a_component.get("count", 0))
                b_count = int(b_component.get("count", 0))
                if a_count > 0 and b_count > 0:
                    merged_component_states.append(weighted_state(((a_component["capability_state"], a_count), (b_component["capability_state"], b_count))))
                elif a_count > 0:
                    merged_component_states.append(copy.deepcopy(a_component["capability_state"]))
                else:
                    merged_component_states.append(copy.deepcopy(b_component["capability_state"]))
            a_familiarity = template.get("doctrine_familiarity", 50)
            b_familiarity = secondary.get("doctrine_familiarity", 50)
            if isinstance(a_familiarity, int) and not isinstance(a_familiarity, bool) and isinstance(b_familiarity, int) and not isinstance(b_familiarity, bool):
                template["doctrine_familiarity"] = (a_familiarity * primary_count + b_familiarity * secondary_count) // total_count
            for key in ("readiness", "morale", "cohesion"):
                a = template.get(key)
                b = secondary.get(key)
                if isinstance(a, int) and not isinstance(a, bool) and isinstance(b, int) and not isinstance(b, bool):
                    merged = (a * primary_count + b * secondary_count) // total_count
                    if key == "cohesion":
                        merged = max(0, merged - 5)
                    template[key] = merged
            self._resize_formation_strength(template, total_count)
            for component, merged_state in zip(template.get("components", ()), merged_component_states):
                if isinstance(component, dict):
                    component["capability_state"] = merged_state
            template["authorized_personnel"] = int(template.get("authorized_personnel", primary_count)) + int(secondary.get("authorized_personnel", secondary_count))
            # Merge only a bounded lineage/experience signal from the absorbed unit.
            # Historical engagements/casualties remain on their original formation
            # records and are never copied into the surviving formation's totals.
            primary_memory = template.get("operational_memory")
            if not isinstance(primary_memory, dict):
                primary_memory = {
                    "engagements_total": 0, "aggregate_personnel_committed_total": 0,
                    "killed_total": 0, "wounded_total": 0, "incapacitated_total": 0,
                    "captured_total": 0, "victories_total": 0, "defeats_total": 0,
                    "contested_total": 0, "replacements_total": 0,
                    "last_combat_ref": None, "last_result_at": None, "last_outcome": None,
                    "last_loss_milli": 0, "last_reconstituted_at": None,
                    "inherited_from_ref": None, "inherited_experience_milli": 0,
                }
                template["operational_memory"] = primary_memory
            secondary_memory = secondary.get("operational_memory") if isinstance(secondary.get("operational_memory"), Mapping) else {}
            secondary_lineage_experience = min(
                1000,
                int(secondary_memory.get("inherited_experience_milli", 0) or 0)
                + min(1000, int(secondary_memory.get("engagements_total", 0) or 0) * 100),
            )
            inherited_now = int(primary_memory.get("inherited_experience_milli", 0) or 0)
            inherited_from_secondary = (secondary_lineage_experience * secondary_count) // max(1, total_count)
            primary_memory["inherited_experience_milli"] = min(1000, inherited_now + inherited_from_secondary)
            formations[:] = [row for row in formations if row is not secondary]
            consequence = str(template.get("id"))
            event_kind = "formation_merged"

        elif kind == "formation_release":
            if requested_ref is not None:
                release = template
            else:
                # Autonomous hosts may only demobilize formations they themselves
                # mobilized.  Other mobilized formations can belong to a live
                # operation or another authority using the same conserved force;
                # selecting them here would let a periodic review destroy an
                # unrelated in-flight command. Explicit lifecycle commands still
                # release a requested formation through their normal authority check.
                owner_slug = re.sub(r"[^a-z0-9]+", ".", str(owner_identity).lower()).strip(".")
                owner_prefix = f"formation.mobilized.{owner_slug}."
                releasable = [
                    row for row in formations
                    if (
                        isinstance(row, dict)
                        and row.get("lifecycle_origin") == "mobilized"
                        and isinstance(row.get("id"), str)
                        and row.get("id", "").startswith(owner_prefix)
                    )
                ]
                if not releasable:
                    return {"kind": kind, "skipped": "no_owned_mobilized_formation_to_release"}
                release = releasable[stable % len(releasable)]
            release_id = str(release.get("id"))
            release_capability = self._formation_capability_composition(release)
            headcount = release.get("personnel_total")
            if isinstance(headcount, bool) or not isinstance(headcount, int) or headcount <= 0:
                raise CommandRejectedError("autonomous_force_invalid")
            formations[:] = [row for row in formations if row is not release]
            if availability.get("deployed", 0) < headcount:
                raise CommandRejectedError("autonomous_force_demobilization_invalid")
            availability["deployed"] -= headcount
            availability["ready_24h"] = int(availability.get("ready_24h", 0)) + headcount
            self._reserve_add(force, "ready_24h", release_capability, headcount)
            if sum(availability.values()) != force_total:
                raise CommandRejectedError("autonomous_force_demobilization_invalid")
            consequence = release_id
            event_kind = "formation_released"

        else:
            return {"kind": kind, "skipped": "unsupported_formation_action"}

        self._validate_reserve_counts(force)
        event_id = self._append_internal_event(
            world_events,
            command=command,
            identity=f"{owner_identity}:{at}:{kind}",
            kind=event_kind,
            at=at,
            host_refs=(str(owner_identity), str(force_ref)),
            actor_refs=(actor,),
            affected_owner_refs=(path, force_path),
            material_consequence_refs=(consequence,),
            classification=classification,
            audience_refs=(),
            source_refs=(actor,),
        )
        return {"kind": kind, "event_id": event_id, "formation_ref": consequence}
    def _autonomous_participant_score(self, person_ref: str, objective_kind: str) -> int:
        """Return a bounded owner-local capability score for routine offscreen work.

        This is deliberately analytical rather than a miniature combat engine.
        It reads only the declared mission participant and uses skills relevant
        to the objective.  Major contacts/battles must still wake the normal
        combat/operation systems.
        """
        try:
            _path, _digest, view = self._resolve_covered_owner_view(
                person_ref, cache=_OwnerResolutionCache()
            )
        except CommandRejectedError:
            return 35
        if not isinstance(view, Mapping):
            return 35

        if view.get("schema") == "person":
            root = view.get("stats") if isinstance(view.get("stats"), Mapping) else {}
            health = view.get("health") if isinstance(view.get("health"), Mapping) else {}
            condition_label = health.get("status")
        else:
            root = view
            condition = view.get("condition") if isinstance(view.get("condition"), Mapping) else {}
            condition_label = condition.get("readiness")

        def section(name: str) -> Mapping[str, Any]:
            value = root.get(name) if isinstance(root, Mapping) else None
            return value if isinstance(value, Mapping) else {}

        attrs = section("attributes")
        martial = section("martial_skills")
        ops = section("operational_skills")
        chakra = section("chakra_dimensions")

        def val(container: Mapping[str, Any], key: str) -> int:
            raw = container.get(key, 0)
            return max(0, min(200, raw if isinstance(raw, int) and not isinstance(raw, bool) else 0))

        profiles = {
            "observe": ((attrs, "awareness"), (ops, "investigation"), (chakra, "sensing"), (martial, "stealth")),
            "identify": ((attrs, "awareness"), (attrs, "intelligence"), (ops, "investigation"), (ops, "tracking")),
            "investigate": ((ops, "investigation"), (attrs, "awareness"), (attrs, "intelligence"), (ops, "tracking")),
            "protect": ((ops, "tactics"), (ops, "team_coordination"), (attrs, "awareness"), (attrs, "endurance")),
            "escort": ((ops, "team_coordination"), (ops, "survival"), (attrs, "awareness"), (martial, "movement")),
            "deliver": ((ops, "survival"), (martial, "movement"), (attrs, "awareness"), (ops, "infiltration")),
            "recover": ((ops, "investigation"), (ops, "tracking"), (martial, "movement"), (attrs, "awareness")),
            "rescue": ((ops, "tactics"), (ops, "team_coordination"), (martial, "movement"), (chakra, "control")),
            "secure": ((ops, "tactics"), (ops, "traps"), (attrs, "awareness"), (attrs, "endurance")),
            "capture": ((martial, "grappling"), (ops, "tactics"), (chakra, "control"), (martial, "movement")),
            "restrain": ((martial, "grappling"), (chakra, "control"), (ops, "team_coordination"), (attrs, "coordination")),
            "sabotage": ((ops, "infiltration"), (ops, "traps"), (martial, "stealth"), (attrs, "intelligence")),
            "conceal": ((martial, "stealth"), (chakra, "suppression"), (ops, "infiltration"), (attrs, "composure")),
        }
        selected = profiles.get(
            objective_kind,
            ((ops, "tactics"), (attrs, "awareness"), (attrs, "intelligence"), (ops, "team_coordination")),
        )
        values = [val(container, key) for container, key in selected]
        score = sum(values) // max(1, len(values))
        penalty = {
            "fatigued": 10, "injured": 20, "incapacitated": 60, "captured": 60, "dead": 100,
            "wounded": 20, "critical": 45,
        }.get(str(condition_label), 0)
        return max(0, min(100, score - penalty))
    def _autonomous_mission_wake_reasons(self, owner: MissionOwner) -> Tuple[str, ...]:
        """Return material reasons an offscreen mission must not be score-compressed."""
        reasons: set[str] = set()
        rank = owner.mission_rank
        if rank in ("A", "S"):
            reasons.add("high_rank")
        irreversible_kinds = {"capture", "destroy", "eliminate", "rescue"}
        if any(obj.kind in irreversible_kinds for obj in owner.mission.objectives):
            # These objectives may still compact for anonymous routine actors;
            # exact strategic identities below force the wake.
            exact_sensitive = True
        else:
            exact_sensitive = False
        important_roles = {
            "kage", "jinchuriki", "commander", "clan_head", "daimyo", "heir",
            "player_character", "household_head",
        }
        for ref in owner.mission.participant_refs:
            try:
                _path, _digest, view = self._resolve_covered_owner_view(ref, cache=_OwnerResolutionCache())
            except CommandRejectedError:
                continue
            if not isinstance(view, Mapping):
                continue
            roles = view.get("roles")
            role_set = {str(value).lower() for value in roles} if isinstance(roles, list) else set()
            if role_set & important_roles:
                reasons.add("strategic_named_person")
            if exact_sensitive and view.get("schema") in ("shinobi_character", "person"):
                condition = view.get("condition")
                readiness = condition.get("readiness") if isinstance(condition, Mapping) else None
                if readiness not in ("dead", "captured"):
                    reasons.add("irreversible_named_person_objective")
        terms = getattr(owner.mission, "settlement_terms", ())
        if terms:
            # Resource-bearing or otherwise declared settlement belongs to the
            # normal mission settlement path, which performs escrow/inventory
            # conservation.  Score-only autonomy never marks those terms paid.
            reasons.add("declared_settlement_terms")
        for term in terms:
            record = term.to_record() if hasattr(term, "to_record") else term
            if isinstance(record, Mapping) and any(
                isinstance(record.get(key), str) and record.get(key)
                for key in ("item_ref", "asset_ref", "unique_asset_ref")
            ):
                reasons.add("unique_asset")
        return tuple(sorted(reasons))
    def _autonomous_mission_resolution_score(
        self, participant_refs: Sequence[str], objective_kind: str
    ) -> int:
        scores = [self._autonomous_participant_score(ref, objective_kind) for ref in participant_refs]
        if not scores:
            return 0
        scores.sort(reverse=True)
        # The strongest operative drives a small mission, while additional
        # members add bounded coordination/support rather than linear power.
        lead = scores[0]
        support = sum(scores[1:]) // max(1, len(scores[1:])) if len(scores) > 1 else lead
        team_bonus = min(10, max(0, len(scores) - 1) * 2)
        return max(0, min(100, (lead * 2 + support) // 3 + team_bonus))
    def _apply_autonomous_decision(
        self,
        *,
        decision: Any,
        at: CampaignTime,
        command: CommandEnvelope,
        scheduler: CausalSchedulerRegistry,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
        faction_record: Dict[str, Any],
    ) -> Mapping[str, Any]:
        """Apply one bounded internal decision through existing state authorities.

        The policy book can only name a small declared neighborhood.  This
        method never enumerates a directory to discover things to mutate.
        """

        kind = decision.kind
        actor = decision.actor_ref
        payload = decision.payload

        def load(path: str) -> Dict[str, Any]:
            if path in record_writes:
                return record_writes[path]
            try:
                value = self.repository.read_json(path)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("autonomous_owner_invalid") from exc
            if not isinstance(value, dict):
                raise CommandRejectedError("autonomous_owner_invalid")
            record_writes[path] = copy.deepcopy(value)
            return record_writes[path]

        faction_id = payload.get("faction_id")
        classification = payload.get("classification") if isinstance(payload.get("classification"), str) else "restricted"

        if kind == "routine_summary":
            event_id = self._append_internal_event(
                world_events, command=command, identity=f"{faction_id}:{at}:routine", kind="autonomous_routine_activity_compacted",
                at=at, host_refs=(str(faction_id),), actor_refs=(actor,),
                material_consequence_refs=(f"compacted_reviews:{payload.get('routine_review_count', payload.get('compacted_reviews', 1))}",),
                classification=classification, audience_refs=(), source_refs=(actor,),
            )
            return {"kind": kind, "event_id": event_id}

        if kind == "team_form":
            spec = payload.get("team_creation")
            if not isinstance(spec, Mapping):
                return {"kind": kind, "skipped": "no_team_creation_policy"}
            team_id = spec.get("team_id")
            candidates = spec.get("candidate_refs")
            leader = spec.get("leader_ref")
            authority_ref = spec.get("assignment_authority_ref")
            if (
                not isinstance(team_id, str)
                or not isinstance(candidates, list)
                or not candidates
                or not isinstance(leader, str)
                or not isinstance(authority_ref, str)
            ):
                raise CommandRejectedError("autonomous_team_policy_invalid")
            try:
                path, team = self._register_exact_team_state(
                    team_id=team_id,
                    name=str(spec.get("name") or team_id),
                    team_type=str(spec.get("team_type") or "temporary_task_force"),
                    parent_institution_ref=(
                        str(spec.get("parent_institution_ref"))
                        if isinstance(spec.get("parent_institution_ref"), str)
                        else str(faction_id)
                    ),
                    assignment_authority_ref=authority_ref,
                    leader_ref=leader,
                    member_refs=candidates,
                    roles={ref: ("team_leader" if ref == leader else "operative") for ref in candidates if isinstance(ref, str)},
                    classification=str(spec.get("classification") or classification),
                    at=at,
                    basis=f"Autonomous lawful organization by {faction_id} under registered policy authority {authority_ref}.",
                    scheduler=scheduler,
                    record_writes=record_writes,
                )
            except CommandRejectedError as exc:
                if str(exc) == "team_already_exists":
                    return {"kind": kind, "skipped": "team_already_exists", "team_id": team_id}
                raise
            faction = faction_record.get("faction")
            plan_state = faction.get("plan_state") if isinstance(faction, dict) else None
            if not isinstance(plan_state, dict):
                raise CommandRejectedError("faction_owner_invalid")
            formed_refs = plan_state.setdefault("autonomous_team_refs", [])
            if not isinstance(formed_refs, list):
                raise CommandRejectedError("faction_owner_invalid")
            if team_id not in formed_refs:
                formed_refs.append(team_id)
                formed_refs.sort()
            event_id = self._append_internal_event(
                world_events, command=command, identity=f"{team_id}:{at}:formed", kind="exact_team_formed", at=at,
                host_refs=(str(faction_id), team_id), actor_refs=tuple(candidates), affected_owner_refs=(path,),
                material_consequence_refs=(team_id,), classification=classification, audience_refs=(), source_refs=(actor,),
            )
            return {"kind": kind, "event_id": event_id, "team_id": team_id}

        if kind in ("formation_drill", "formation_expand", "formation_release"):
            return self._apply_autonomous_formation_action(
                kind=kind, owner_identity=str(faction_id), actor=actor, payload=payload, at=at,
                command=command, world_events=world_events, record_writes=record_writes,
                classification=classification,
            )

        if kind == "information_report":
            information = InformationStore(self.repository, record_writes)
            suffix = hashlib.sha256(f"{faction_id}\x00{at}\x00{decision.reason}".encode()).hexdigest()[:18]
            claim_id = f"claim.autonomy.{suffix}"
            claim_record = {
                "claim_id": claim_id,
                "subject_ref": str(faction_id),
                "source_ref": actor,
                "collected_at": str(at),
                "epistemic_kind": "report",
                "confidence_milli": 700,
                "evidence_refs": [],
            }
            try:
                information.add_claim(claim_record)
                information.grant(actor, claim_id)
                claim = InformationClaim(
                    claim_id=claim_id,
                    subject_ref=str(faction_id),
                    source_ref=actor,
                    collected_at=at,
                    epistemic_kind="report",
                    confidence_milli=700,
                    evidence_refs=(),
                )
                delivery_id = f"delivery.autonomy.{suffix}"
                delivery = deliver_claim(
                    claim,
                    delivery_id=delivery_id,
                    sender_ref=actor,
                    recipient_ref=str(faction_id),
                    channel="institutional_report",
                    delivered_at=at,
                    channel_confidence_milli=900,
                )
                information.add_delivery(dict(delivery.to_record()))
                information.grant(str(faction_id), claim_id)
            except ValueError as exc:
                raise CommandRejectedError("information_registry_invalid") from exc
            event_id = self._append_internal_event(
                world_events, command=command, identity=claim_id, kind="autonomous_information_report", at=at,
                host_refs=(str(faction_id),), actor_refs=(actor,), affected_owner_refs=information.affected_paths,
                material_consequence_refs=(claim_id,), classification=classification, audience_refs=(actor, str(faction_id)), knowledge_refs=(claim_id,), source_refs=(actor,),
            )
            return {"kind": kind, "event_id": event_id, "claim_id": claim_id, "delivery_id": delivery_id}

        if kind == "mission_advance":
            plan_state = faction_record.get("faction", {}).get("plan_state")
            refs = plan_state.get("autonomous_mission_refs") if isinstance(plan_state, dict) else None
            if not isinstance(refs, list) or not refs:
                return {"kind": kind, "skipped": "no_active_autonomous_mission"}
            mission_id = next((value for value in refs if isinstance(value, str) and value), None)
            if mission_id is None:
                return {"kind": kind, "skipped": "no_active_autonomous_mission"}
            path = mission_owner_path(mission_id)
            try:
                existing = record_writes.get(path)
                owner = MissionOwner.from_record(existing if existing is not None else self.repository.read_json(path))
            except (FileNotFoundError, TypeError, ValueError) as exc:
                raise CommandRejectedError("autonomous_mission_invalid") from exc
            if owner.mission.state in _TERMINAL_MISSION_STATES:
                refs[:] = [value for value in refs if value != mission_id]
                return {"kind": kind, "skipped": "mission_already_terminal", "mission_id": mission_id}
            objective = owner.mission.objectives[0]
            wake_reasons = self._autonomous_mission_wake_reasons(owner)
            if wake_reasons:
                wake_refs = plan_state.setdefault("wake_required_mission_refs", []) if isinstance(plan_state, dict) else []
                already = mission_id in wake_refs if isinstance(wake_refs, list) else False
                if isinstance(wake_refs, list) and not already:
                    wake_refs.append(mission_id)
                    wake_refs.sort()
                    event_id = self._append_internal_event(
                        world_events, command=command, identity=f"{mission_id}:{at}:wake",
                        kind="autonomous_mission_wake_required", at=at,
                        host_refs=(str(faction_id), mission_id), actor_refs=owner.mission.participant_refs,
                        affected_owner_refs=(path,), material_consequence_refs=tuple(wake_reasons),
                        classification=classification, audience_refs=(), source_refs=(actor,),
                    )
                else:
                    event_id = None
                return {
                    "kind": kind, "mission_id": mission_id,
                    "skipped": "high_salience_wake_required",
                    "wake_reasons": list(wake_reasons), "event_id": event_id,
                }
            mission_score = self._autonomous_mission_resolution_score(
                owner.mission.participant_refs, objective.kind
            )
            difficulty = payload.get("mission_difficulty", 60)
            if isinstance(difficulty, bool) or not isinstance(difficulty, int):
                difficulty = 60
            difficulty = max(20, min(95, difficulty))
            succeeded = mission_score >= difficulty
            event_kind = "autonomous_mission_succeeded" if succeeded else "autonomous_mission_failed"
            event_id = self._append_internal_event(
                world_events, command=command, identity=f"{mission_id}:{at}:resolution", kind=event_kind, at=at,
                host_refs=(str(faction_id), mission_id), actor_refs=owner.mission.participant_refs, affected_owner_refs=(path,),
                material_consequence_refs=(mission_id,), classification=classification, audience_refs=(), source_refs=(actor,),
            )
            try:
                progressed = update_objective(
                    owner.mission, objective.objective_id, "succeeded" if succeeded else "failed",
                    progress_milli=1000 if succeeded else max(objective.progress_milli, 500), resolution_ref=event_id,
                )
                resolving = transition_mission(progressed, "resolving")
                terminal = derive_mission_outcome(resolving)
                settled = settle_mission(terminal, "autonomy." + hashlib.sha256(f"{mission_id}\x00{at}".encode()).hexdigest()[:20]).mission
            except (MissionTransitionError, ObjectiveDependencyError, SettlementConflictError, ValueError) as exc:
                raise CommandRejectedError("autonomous_mission_resolution_invalid") from exc
            owner = owner.with_mission(settled, effective_at=at)
            record_writes[path] = dict(owner.to_record())
            refs[:] = [value for value in refs if value != mission_id]
            wake_refs = plan_state.get("wake_required_mission_refs") if isinstance(plan_state, dict) else None
            if isinstance(wake_refs, list):
                wake_refs[:] = [value for value in wake_refs if value != mission_id]

            information = InformationStore(self.repository, record_writes)
            claim_id = f"claim.{mission_id}.result"
            claim_record = {
                "claim_id": claim_id, "subject_ref": mission_id, "source_ref": event_id, "collected_at": str(at),
                "epistemic_kind": "report", "confidence_milli": 850, "evidence_refs": [event_id],
            }
            holders = sorted(set(list(owner.mission.participant_refs) + [actor, str(faction_id)]))
            try:
                information.add_claim(claim_record)
                for holder in holders:
                    information.grant(holder, claim_id)
            except ValueError as exc:
                raise CommandRejectedError("information_registry_invalid") from exc
            return {
                "kind": kind, "event_id": event_id, "mission_id": mission_id,
                "outcome": owner.mission.state, "claim_id": claim_id, "knowledge_holders": holders,
                "objective_kind": objective.kind, "capability_score": mission_score, "difficulty": difficulty,
            }

        if kind == "mission_generate":
            plan_state = faction_record.get("faction", {}).get("plan_state")
            if not isinstance(plan_state, dict):
                raise CommandRejectedError("faction_owner_invalid")
            open_refs = plan_state.setdefault("autonomous_mission_refs", [])
            if not isinstance(open_refs, list):
                raise CommandRejectedError("faction_owner_invalid")
            open_refs[:] = [value for value in open_refs if isinstance(value, str) and value]
            participants = [ref for ref in payload.get("mission_participant_refs", []) if isinstance(ref, str)]
            if not participants:
                participants = [actor]
            suffix = hashlib.sha256(f"{faction_id}\x00{at}\x00mission".encode()).hexdigest()[:18]
            mission_id = f"mission.autonomy.{suffix}"
            path = mission_owner_path(mission_id)
            if path in record_writes or self.repository.read_optional_bytes(path) is not None:
                return {"kind": kind, "skipped": "mission_already_recorded", "mission_id": mission_id}
            event_id = self._append_internal_event(
                world_events, command=command, identity=mission_id, kind="autonomous_mission_started", at=at,
                host_refs=(str(faction_id), mission_id), actor_refs=tuple(participants), affected_owner_refs=(path,),
                material_consequence_refs=(mission_id,), classification=classification, audience_refs=(), source_refs=(actor,),
            )
            objective_id = "objective." + suffix
            objective_cycle = [
                value for value in payload.get("mission_objective_cycle", [])
                if isinstance(value, str) and value
            ]
            if objective_cycle:
                objective_kind = objective_cycle[
                    int.from_bytes(hashlib.sha256(f"{faction_id}\x00{at}\x00objective".encode()).digest()[:8], "big")
                    % len(objective_cycle)
                ]
            else:
                objective_kind = "investigate"
            try:
                objective = MissionObjective(
                    objective_id=objective_id, kind=objective_kind, required=True, status="in_progress",
                    progress_milli=100, resolution_ref=None,
                )
            except ValueError as exc:
                raise CommandRejectedError("autonomous_mission_objective_invalid") from exc
            mission = Mission(
                mission_id=mission_id, state="active", participant_refs=tuple(participants),
                objectives=(objective,), settlement_terms=(),
            )
            difficulty = max(20, min(95, int(payload.get("mission_difficulty", 60))))
            mission_rank = "D" if difficulty < 35 else "C" if difficulty < 50 else "B" if difficulty < 70 else "A" if difficulty < 85 else "S"
            try:
                funding_holder_ref = self._funding_holder_for(str(faction_id))
            except CommandRejectedError:
                funding_holder_ref = str(faction_id)
            owner = MissionOwner(
                mission=mission, issuer_ref=str(faction_id), authority_ref=actor,
                mission_rank=mission_rank, funding_holder_ref=funding_holder_ref, escrow_holder_ref=None,
                opened_at=at, authorized_at=at, starts_at=at, deadline_at=None, next_due_at=None,
                operation_ref=str(faction_id), closed_at=None,
            )
            record_writes[path] = dict(owner.to_record())
            if mission_id not in open_refs:
                open_refs.append(mission_id)
                open_refs.sort()
            return {
                "kind": kind, "event_id": event_id, "mission_id": mission_id, "state": "active",
                "objective_kind": objective.kind,
                "difficulty": difficulty, "mission_rank": mission_rank,
            }

        if kind == "institution_priority_review":
            faction = faction_record.get("faction")
            plan_state = faction.get("plan_state") if isinstance(faction, dict) else None
            if not isinstance(plan_state, dict):
                raise CommandRejectedError("faction_owner_invalid")
            priorities = plan_state.setdefault("autonomous_priority_refs", [])
            completed = plan_state.setdefault("completed_priority_refs", [])
            if not isinstance(priorities, list) or not isinstance(completed, list):
                raise CommandRejectedError("faction_owner_invalid")
            completed_project = None
            if priorities:
                completed_project = priorities.pop(0)
                if isinstance(completed_project, str) and completed_project not in completed:
                    completed.append(completed_project)
                    del completed[:-16]
            suffix = hashlib.sha256(f"{faction_id}\x00{at}\x00project".encode()).hexdigest()[:16]
            priority_id = f"priority.autonomy.{suffix}"
            priorities.append(priority_id)
            event_id = self._append_internal_event(
                world_events, command=command, identity=f"{faction_id}:{at}:priority", kind="institution_priority_reviewed", at=at,
                host_refs=(str(faction_id),), actor_refs=(actor,),
                material_consequence_refs=tuple(x for x in (completed_project, priority_id) if isinstance(x, str)),
                classification=classification, audience_refs=(), source_refs=(actor,),
            )
            return {"kind": kind, "event_id": event_id, "completed_priority": completed_project, "priority_id": priority_id}

        return {"kind": kind, "skipped": "unsupported_autonomy_decision"}
    def _apply_institution_autonomy_review(
        self,
        *,
        institution: Dict[str, Any],
        at: CampaignTime,
        compacted: int,
        command: CommandEnvelope,
        policy_book: AutonomousPolicyBook,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Mapping[str, Any]:
        """Advance one bundled institution without creating a per-institution tick file."""
        institution_id = institution.get("id")
        if not isinstance(institution_id, str) or not institution_id:
            raise CommandRejectedError("institution_autonomy_invalid")
        settlement = institution.get("settlement")
        if not isinstance(settlement, dict):
            return {"institution_id": institution_id, "skipped": "no_settlement_contract"}

        # Bundled institutions and clans own typed causal work rather than
        # rotating opaque priority strings. Exact work lives in state/operation;
        # active_goal_ids remains the small routing projection on the registry.
        institution_work = self._review_bundled_institution_operation(
            institution=institution, at=at, command=command, world_events=world_events,
            record_writes=record_writes,
        )
        completed_goal = institution_work.get("completed_operation_ref") if isinstance(institution_work, Mapping) else None
        priority_id = institution_work.get("operation_ref") if isinstance(institution_work, Mapping) else None

        assignment = policy_book.institution_assignment(institution_id)
        pipeline_result: Optional[Mapping[str, Any]] = None
        if assignment.get("kind") == "academy_pipeline":
            population = record_writes.get(_POPULATION_REGISTRY_PATH)
            if population is None:
                try:
                    loaded = self.repository.read_json(_POPULATION_REGISTRY_PATH)
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("population_registry_invalid") from exc
                if not isinstance(loaded, dict):
                    raise CommandRejectedError("population_registry_invalid")
                population = copy.deepcopy(loaded)
                record_writes[_POPULATION_REGISTRY_PATH] = population
            pools = population.get("pools")
            transfers = population.get("transfers")
            if not isinstance(pools, dict) or not isinstance(transfers, list):
                raise CommandRejectedError("population_registry_invalid")
            source_id = assignment.get("source_pool_id")
            academy_id = assignment.get("academy_pool_id")
            service_id = assignment.get("service_pool_id")
            force_ref = assignment.get("force_ref")
            if not all(isinstance(x, str) and x for x in (source_id, academy_id, service_id, force_ref)):
                raise CommandRejectedError("institution_autonomy_policy_invalid")
            source_record = pools.get(source_id)
            academy_record = pools.get(academy_id)
            service_record = pools.get(service_id)
            if not all(isinstance(x, dict) for x in (source_record, academy_record, service_record)):
                raise CommandRejectedError("institution_autonomy_population_invalid")
            source_pool = self._pool_reducer_view(source_id, source_record)
            academy_pool = self._pool_reducer_view(academy_id, academy_record)
            academy_before = academy_pool.total
            source_representation = source_record.get("representation")
            academy_representation = academy_record.get("representation")
            if not isinstance(source_representation, Mapping) or not isinstance(academy_representation, Mapping):
                raise CommandRejectedError("population_representation_invalid")
            source_anonymous = source_representation.get("anonymous_count")
            academy_anonymous = academy_representation.get("anonymous_count")
            if (
                isinstance(source_anonymous, bool) or not isinstance(source_anonymous, int) or source_anonymous < 0
                or isinstance(academy_anonymous, bool) or not isinstance(academy_anonymous, int) or academy_anonymous < 0
            ):
                raise CommandRejectedError("population_representation_invalid")
            intake_rate = assignment.get("youth_intake_per_review", 0)
            graduation_rate = assignment.get("service_graduates_per_review", 0)
            if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (intake_rate, graduation_rate)):
                raise CommandRejectedError("institution_autonomy_policy_invalid")
            periods = max(1, min(240, compacted))
            intake = min(source_anonymous, intake_rate * periods)
            intake_id = None
            if intake:
                selected = neutral_proportional_selection(source_pool, intake)
                transfer = PopulationTransfer(
                    transfer_id=f"autonomy.intake.{suffix}",
                    source_pool_id=source_id,
                    destination_pool_id=academy_id,
                    count=intake,
                    selected_dimensions=selected,
                    selection_mode="neutral_proportional",
                )
                source_pool, academy_pool = apply_transfer(source_pool, academy_pool, transfer)
                self._transfer_population_representation(source_record, academy_record, intake)
                intake_id = transfer.transfer_id
                transfers.append({
                    "id": transfer.transfer_id, "at": str(at), "source_pool_id": source_id,
                    "destination_ref": academy_id, "requested_count": intake, "accepted": intake, "rejected": 0,
                    "authority_ref": institution_id, "authority_basis": "autonomous_academy_intake",
                    "policy_ref": "autonomy.academy_pipeline", "method": "neutral_proportional",
                    "accepted_profile": {"numeric_distributions": {}, "category_counts": {"academy": intake}, "dimension_counts": {k: dict(v) for k, v in selected.items()}, "tags": ["academy_intake"]},
                    "materialized_person_ids": [], "source_removed": intake, "destination_added": intake,
                    "selection_note": "Bounded institutional intake from an already-existing youth population; no person was created.",
                })
                self._trim_population_transfer_history(transfers)
            graduation = min(academy_anonymous + intake, graduation_rate * periods)
            graduation_id = None
            if graduation:
                service_pool = self._pool_reducer_view(service_id, service_record)
                selected = neutral_proportional_selection(academy_pool, graduation)
                transfer = PopulationTransfer(
                    transfer_id=f"autonomy.graduation.{suffix}",
                    source_pool_id=academy_id,
                    destination_pool_id=service_id,
                    count=graduation,
                    selected_dimensions=selected,
                    selection_mode="neutral_proportional",
                )
                academy_pool, service_pool = apply_transfer(academy_pool, service_pool, transfer)
                self._transfer_population_representation(academy_record, service_record, graduation)
                graduation_id = transfer.transfer_id
                transfers.append({
                    "id": transfer.transfer_id, "at": str(at), "source_pool_id": academy_id,
                    "destination_ref": service_id, "requested_count": graduation, "accepted": graduation, "rejected": 0,
                    "authority_ref": institution_id, "authority_basis": "autonomous_academy_graduation",
                    "policy_ref": "autonomy.academy_pipeline", "method": "neutral_proportional",
                    "accepted_profile": {"numeric_distributions": {}, "category_counts": {"shinobi_service": graduation}, "dimension_counts": {k: dict(v) for k, v in selected.items()}, "tags": ["academy_graduation"]},
                    "materialized_person_ids": [], "source_removed": graduation, "destination_added": graduation,
                    "selection_note": "Graduates moved from Academy population into living shinobi service; service force headcount is updated in the same atomic transaction.",
                })
                self._trim_population_transfer_history(transfers)
                try:
                    force_path, _digest, force_view = self._resolve_covered_owner_view(force_ref, cache=_OwnerResolutionCache())
                except CommandRejectedError as exc:
                    raise CommandRejectedError("institution_autonomy_force_invalid") from exc
                force = record_writes.get(force_path)
                if force is None:
                    force = copy.deepcopy(dict(force_view))
                    record_writes[force_path] = force
                if force.get("schema") != "force" or force.get("population_pool_id") != service_id:
                    raise CommandRejectedError("institution_autonomy_force_invalid")
                availability = force.get("availability")
                troop_pools = force.get("troop_pools")
                if not isinstance(availability, dict) or not isinstance(troop_pools, list):
                    raise CommandRejectedError("institution_autonomy_force_invalid")
                force["total"] = int(force.get("total", 0)) + graduation
                availability["training_or_instruction"] = int(availability.get("training_or_instruction", 0)) + graduation
                training_pool = next((row for row in troop_pools if isinstance(row, dict) and row.get("role") == "training_instruction"), None)
                if isinstance(training_pool, dict) and isinstance(training_pool.get("count"), int):
                    training_pool["count"] += graduation
                if sum(availability.values()) != force["total"]:
                    raise CommandRejectedError("institution_autonomy_force_conservation_failed")
                if service_pool.total != force["total"]:
                    raise CommandRejectedError("institution_autonomy_population_force_drift")
                self._persist_population_pool_record(service_record, service_pool, at=at)
            self._persist_population_pool_record(source_record, source_pool, at=at)
            self._persist_population_pool_record(academy_record, academy_pool, at=at)
            pipeline_result = {
                "intake": intake,
                "graduates": graduation,
                "intake_transfer_id": intake_id,
                "graduation_transfer_id": graduation_id,
                "force_ref": force_ref,
            }

        service_training_result: Optional[Mapping[str, Any]] = None
        if assignment.get("kind") == "service_training_pipeline":
            force_ref = assignment.get("force_ref")
            fraction = assignment.get("completion_fraction_milli", 50)
            training_hours = assignment.get("training_hours_per_review", 24)
            if (
                not isinstance(force_ref, str) or not force_ref
                or isinstance(fraction, bool) or not isinstance(fraction, int) or not 1 <= fraction <= 1000
                or isinstance(training_hours, bool) or not isinstance(training_hours, int) or training_hours <= 0
            ):
                raise CommandRejectedError("institution_autonomy_policy_invalid")
            try:
                force_path, _digest, force_view = self._resolve_covered_owner_view(
                    force_ref, cache=_OwnerResolutionCache()
                )
            except CommandRejectedError as exc:
                raise CommandRejectedError("institution_autonomy_force_invalid") from exc
            force = record_writes.get(force_path)
            if force is None:
                force = copy.deepcopy(dict(force_view))
                record_writes[force_path] = force
            if force.get("schema") != "force":
                raise CommandRejectedError("institution_autonomy_force_invalid")
            availability = force.get("availability")
            troop_pools = force.get("troop_pools")
            reserve = force.get("reserve_capability")
            total = force.get("total")
            if (
                not isinstance(availability, dict)
                or not isinstance(troop_pools, list)
                or not isinstance(reserve, dict)
                or isinstance(total, bool) or not isinstance(total, int) or total < 0
            ):
                raise CommandRejectedError("institution_autonomy_force_invalid")
            self._validate_reserve_counts(force)
            training_count = availability.get("training_or_instruction")
            ready_count = availability.get("ready_24h")
            if any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in (training_count, ready_count)
            ):
                raise CommandRejectedError("institution_autonomy_force_invalid")

            # Settle missed reviews as repeated institutional throughput against
            # the remaining training pool. This bounds transaction work without
            # imposing a fictional headcount ceiling or creating new people.
            remaining = training_count
            completed = 0
            for _ in range(max(1, min(240, compacted))):
                if remaining <= 0:
                    break
                step = min(remaining, max(1, remaining * fraction // 1000))
                completed += step
                remaining -= step

            service_event_ref = None
            if completed > 0:
                source_row = reserve.get("training_or_instruction")
                if not isinstance(source_row, Mapping):
                    raise CommandRejectedError("force_reserve_capability_invalid")
                source_state = self._reserve_capability_state(source_row)
                developed_state = consolidate_training(source_state, hours=training_hours)
                self._reserve_remove(force, "training_or_instruction", source_state, completed)
                self._reserve_add(force, "ready_24h", developed_state, completed)
                availability["training_or_instruction"] -= completed
                availability["ready_24h"] += completed

                training_pool = next(
                    (row for row in troop_pools if isinstance(row, dict) and row.get("role") == "training_instruction"),
                    None,
                )
                field_pool = next(
                    (row for row in troop_pools if isinstance(row, dict) and row.get("role") == "field_ready"),
                    None,
                )
                if not isinstance(training_pool, dict) or not isinstance(field_pool, dict):
                    raise CommandRejectedError("institution_autonomy_force_invalid")
                training_pool_count = training_pool.get("count")
                field_pool_count = field_pool.get("count")
                if (
                    isinstance(training_pool_count, bool) or not isinstance(training_pool_count, int)
                    or isinstance(field_pool_count, bool) or not isinstance(field_pool_count, int)
                    or training_pool_count < completed or field_pool_count < 0
                ):
                    raise CommandRejectedError("institution_autonomy_force_invalid")
                training_pool["count"] -= completed
                field_pool["count"] += completed
                service_event_ref = self._append_internal_event(
                    world_events,
                    command=command,
                    identity=f"{institution_id}:{at}:service-training",
                    kind="service_training_cycle_completed",
                    at=at,
                    host_refs=(institution_id, force_ref),
                    actor_refs=(institution_id,),
                    affected_owner_refs=(force_path,),
                    material_consequence_refs=(force_ref,),
                    classification="restricted",
                    audience_refs=(),
                    source_refs=(institution_id,),
                )

            if sum(availability.values()) != total:
                raise CommandRejectedError("institution_autonomy_force_conservation_failed")
            self._validate_reserve_counts(force)
            service_training_result = {
                "force_ref": force_ref,
                "completed": completed,
                "source_class": "training_or_instruction",
                "destination_class": "ready_24h",
                "event_ref": service_event_ref,
                "net_force_growth": 0,
            }

        military_result: Optional[Mapping[str, Any]] = None
        if assignment.get("kind") == "military_command":
            force_ref = assignment.get("force_ref")
            formation_path = assignment.get("formation_registry_ref")
            if not isinstance(force_ref, str) or not isinstance(formation_path, str):
                raise CommandRejectedError("institution_autonomy_policy_invalid")
            try:
                force_path, _digest, force_view = self._resolve_covered_owner_view(
                    force_ref, cache=_OwnerResolutionCache()
                )
            except CommandRejectedError as exc:
                raise CommandRejectedError("institution_autonomy_force_invalid") from exc
            force = record_writes.get(force_path)
            if force is None:
                force = copy.deepcopy(dict(force_view))
                record_writes[force_path] = force
            registry = record_writes.get(formation_path)
            if registry is None:
                try:
                    registry = copy.deepcopy(self.repository.read_json(formation_path))
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("formation_registry_unresolved") from exc
                record_writes[formation_path] = registry
            formations = registry.get("formations") if isinstance(registry, dict) else None
            availability = force.get("availability") if isinstance(force, dict) else None
            if not isinstance(formations, list) or not isinstance(availability, Mapping):
                raise CommandRejectedError("institution_autonomy_force_invalid")
            deployed = availability.get("deployed")
            target_operational = assignment.get("target_operational_personnel")
            formation_size = assignment.get("formation_size")
            if any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in (deployed, target_operational, formation_size)
            ):
                raise CommandRejectedError("institution_autonomy_policy_invalid")
            represented = sum(
                row.get("personnel_total", 0)
                for row in formations
                if isinstance(row, Mapping)
                and isinstance(row.get("personnel_total"), int)
                and not isinstance(row.get("personnel_total"), bool)
            )
            if represented > deployed:
                raise CommandRejectedError("institution_autonomy_force_overrepresented")

            # Aggregate recovery is host-level.  It moves recovering personnel
            # back into readiness without waking individual soldiers.
            medical = availability.get("medical_or_recovery", 0)
            recovery_fraction = assignment.get("medical_recovery_fraction_milli", 250)
            if isinstance(medical, bool) or not isinstance(medical, int) or medical < 0:
                raise CommandRejectedError("institution_autonomy_force_invalid")
            if isinstance(recovery_fraction, bool) or not isinstance(recovery_fraction, int) or not 0 <= recovery_fraction <= 1000:
                raise CommandRejectedError("institution_autonomy_policy_invalid")
            recovered = 0
            if medical > 0 and recovery_fraction > 0:
                periods = max(1, min(24, compacted))
                recover_milli = min(1000, recovery_fraction * periods)
                recovered = max(1, medical * recover_milli // 1000)
                recovered = min(medical, recovered)
                availability["medical_or_recovery"] -= recovered
                availability["ready_24h"] = int(availability.get("ready_24h", 0)) + recovered

            recovering_formations = [
                row for row in formations
                if isinstance(row, dict)
                and isinstance(row.get("personnel_total"), int)
                and isinstance(row.get("authorized_personnel"), int)
                and row.get("personnel_total", 0) < row.get("authorized_personnel", 0)
            ]
            tolerance = max(25, formation_size // 2)
            selected_recovery = None
            if recovering_formations and int(availability.get("ready_24h", 0)) > 0:
                selected_recovery = max(
                    recovering_formations,
                    key=lambda row: (int(row.get("authorized_personnel", 0)) - int(row.get("personnel_total", 0)), str(row.get("id", ""))),
                )
                action = "formation_reconstitute"
            elif represented + tolerance < target_operational:
                action = "formation_expand"
            elif represented > target_operational + tolerance:
                action = "formation_release"
            else:
                action = "formation_drill"
            actor = institution.get("leader_id")
            if not isinstance(actor, str) or not actor:
                actor = institution_id
            military_payload = {
                "force_ref": force_ref,
                "formation_registry_ref": formation_path,
                "formation_size": formation_size,
                "formation_ref": None if selected_recovery is None else selected_recovery.get("id"),
                "target_personnel": None if selected_recovery is None else selected_recovery.get("authorized_personnel"),
                "max_operational_personnel": assignment.get("max_operational_personnel", target_operational),
                "compacted_reviews": compacted,
            }
            military_result = self._apply_autonomous_formation_action(
                kind=action,
                owner_identity=institution_id,
                actor=actor,
                payload=military_payload,
                at=at,
                command=command,
                world_events=world_events,
                record_writes=record_writes,
                classification="restricted",
            )
            military_result = {**dict(military_result), "medical_recovered": recovered}

        event_id = self._append_internal_event(
            world_events,
            command=command,
            identity=f"{institution_id}:{at}:institution",
            kind="institution_autonomy_reviewed",
            at=at,
            host_refs=(institution_id,),
            affected_owner_refs=(_POPULATION_REGISTRY_PATH,) if pipeline_result else (),
            material_consequence_refs=tuple(x for x in (completed_goal, priority_id) if isinstance(x, str)),
            classification="restricted",
            audience_refs=(),
            source_refs=(institution_id,),
        )
        return {
            "institution_id": institution_id,
            "event_id": event_id,
            "completed_goal": completed_goal,
            "priority_id": priority_id,
            "institutional_work": institution_work,
            "population_pipeline": pipeline_result,
            "service_training": service_training_result,
            "military_lifecycle": military_result,
        }
    def _apply_team_autonomy_review(
        self,
        *,
        owner_ref: str,
        at: CampaignTime,
        compacted: int,
        command: CommandEnvelope,
        scheduler: CausalSchedulerRegistry,
        policy_book: AutonomousPolicyBook,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Mapping[str, Any]:
        if owner_ref in record_writes:
            team = record_writes[owner_ref]
        else:
            try:
                loaded = self.repository.read_json(owner_ref)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("team_owner_invalid") from exc
            if not isinstance(loaded, dict):
                raise CommandRejectedError("team_owner_invalid")
            team = copy.deepcopy(loaded)
            record_writes[owner_ref] = team
        if team.get("leader_ref") == command.actor_id:
            return {"team_id": team.get("id"), "skipped": "player_led_team_requires_player_choice"}
        decisions = review_team(team_record=team, at=at, compacted_reviews=compacted, policy_book=policy_book)
        if not decisions:
            return {"team_id": team.get("id"), "skipped": "inactive_team"}
        decision = decisions[0]
        payload = decision.payload
        doctrine_ref = team.get("doctrine_ref")
        if not isinstance(doctrine_ref, str) or not doctrine_ref:
            doctrine = self._generic_team_doctrine(
                team, at=at, doctrine_identity=str(payload.get("doctrine_identity")), motto=str(payload.get("motto")), training_focus=payload.get("training_focus", ()),
            )
            doctrine_ref = doctrine["id"]
            doctrine_path = self._team_doctrine_path(str(team.get("id")))
            record_writes[doctrine_path] = doctrine
            team["doctrine_ref"] = doctrine_ref
            team_index = record_writes.get("state/index/owners/team.json")
            if team_index is None:
                team_index = copy.deepcopy(self.repository.read_json("state/index/owners/team.json"))
                record_writes["state/index/owners/team.json"] = team_index
            team_index.setdefault("owners", {})[doctrine_ref] = doctrine_path
            event_kind = "team_doctrine_adopted"
        else:
            try:
                resolved_path, _digest, doctrine_view = self._resolve_covered_owner_view(
                    doctrine_ref, cache=_OwnerResolutionCache()
                )
            except CommandRejectedError as exc:
                raise CommandRejectedError("team_doctrine_invalid") from exc
            if (
                not isinstance(doctrine_view, Mapping)
                or doctrine_view.get("schema") != "team-doctrine"
                or doctrine_view.get("team_id") != team.get("id")
            ):
                raise CommandRejectedError("team_doctrine_invalid")
            # Existing doctrine refs are authority-resolved. Do not reconstruct a
            # filename from the team id: authored doctrines may use a different
            # stable registered path than the generated default.
            doctrine_path = resolved_path
            if doctrine_path in record_writes:
                doctrine = record_writes[doctrine_path]
            else:
                try:
                    doctrine = copy.deepcopy(self.repository.read_json(doctrine_path))
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("team_doctrine_invalid") from exc
                record_writes[doctrine_path] = doctrine
            training = doctrine.get("training")
            if isinstance(training, dict):
                focus = [str(v) for v in payload.get("training_focus", []) if isinstance(v, str) and v]
                if focus:
                    training["shared_drills"] = focus
            event_kind = "team_doctrine_reviewed"

        members = [value for value in team.get("member_refs", []) if isinstance(value, str)]
        training = doctrine.get("training") if isinstance(doctrine, dict) else None
        training_commitment_id = None
        routine_familiarity_gain = 0
        if isinstance(training, dict):
            next_due = scheduler.hosts.get("host.team." + str(team.get("id")))
            next_due_at = next_due.state.next_due if next_due is not None else None
            if command.actor_id in members and next_due_at is not None:
                commitments = record_writes.get(_COMMITMENT_REGISTRY_PATH)
                if commitments is None:
                    commitments = copy.deepcopy(self.repository.read_json(_COMMITMENT_REGISTRY_PATH))
                    record_writes[_COMMITMENT_REGISTRY_PATH] = commitments
                records = commitments.get("records") if isinstance(commitments, dict) else None
                if not isinstance(records, list):
                    raise CommandRejectedError("commitment_registry_invalid")
                training_commitment_id = "commitment.team_training." + hashlib.sha256(
                    f"{team.get('id')}\x00{next_due_at}".encode()
                ).hexdigest()[:20]
                active_same = any(
                    isinstance(row, Mapping)
                    and row.get("host_ref") == team.get("id")
                    and row.get("kind") == "obligation"
                    and row.get("status") == "active"
                    and isinstance(row.get("summary"), str)
                    and row.get("summary", "").startswith("Team training:")
                    for row in records
                )
                if not active_same:
                    records.append({
                        "id": training_commitment_id,
                        "kind": "obligation",
                        "subject_ref": str(team.get("leader_ref")),
                        "target_ref": command.actor_id,
                        "host_ref": str(team.get("id")),
                        "created_at": str(at),
                        "due_at": str(next_due_at),
                        "status": "active",
                        "summary": "Team training: " + ", ".join(str(v) for v in training.get("shared_drills", [])[:4]),
                        "visibility": str(team.get("classification", "restricted")),
                        "authority_basis": "exact_team_training_obligation",
                    })
                    self._trim_terminal_records(
                        records,
                        terminal_statuses={"completed", "cancelled", "failed"},
                        limit=self.MAX_TERMINAL_COMMITMENT_HISTORY,
                    )
                    host_id = "host." + training_commitment_id
                    if host_id not in scheduler.hosts:
                        scheduler.add_host(SchedulerHost(
                            state=HostState(
                                host_id=host_id, kind="commitment", resolved_through=at,
                                safe_through=next_due_at.add_seconds(-1), handler_ref="causal.scheduler",
                                rng_namespace=training_commitment_id, next_due=next_due_at,
                            ),
                            authority_kind="commitment", owner_ref=_COMMITMENT_REGISTRY_PATH,
                            metadata={"commitment_id": training_commitment_id},
                        ))
                        scheduler.upsert_event(one_shot_event(
                            kind="commitment.due", identity=training_commitment_id, source_host=host_id, target_host=host_id,
                            due_at=next_due_at, payload={"commitment_id": training_commitment_id}, priority=30,
                            visibility="player_known", requires_player=True,
                        ))
            else:
                familiarity = doctrine.get("familiarity")
                if isinstance(familiarity, dict):
                    routine_familiarity_gain = min(12, max(1, compacted * 2))
                    for member in members:
                        current = familiarity.get(member, 0)
                        if isinstance(current, int) and not isinstance(current, bool):
                            familiarity[member] = min(100, current + routine_familiarity_gain)

        event_id = self._append_internal_event(
            world_events, command=command, identity=f"{team.get('id')}:{at}:development", kind=event_kind, at=at,
            host_refs=(str(team.get("id")),), actor_refs=(str(team.get("leader_ref")),),
            affected_owner_refs=(owner_ref, doctrine_path), material_consequence_refs=(str(doctrine_ref),),
            classification=str(team.get("classification", "restricted")), audience_refs=(), source_refs=(str(team.get("leader_ref")),),
        )
        return {
            "team_id": team.get("id"), "event_id": event_id, "doctrine_ref": doctrine_ref,
            "compacted_reviews": compacted, "training_commitment_id": training_commitment_id,
            "routine_familiarity_gain": routine_familiarity_gain,
        }

