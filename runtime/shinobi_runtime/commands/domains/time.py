"""Causal time settlement and bounded autonomous-world command support."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import (
    Any,
    Dict,
    Iterable,
    Mapping,
    Optional,
    Tuple,
)

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.autonomy import review_faction
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.core import (
    _BuiltPlan,
    _campaign_datetime,
    _exact_payload,
    _json_bytes,
)
from shinobi_runtime.commands.constants import ACTIVE_PRESSURE_STATUSES as _ACTIVE_PRESSURE_STATUSES
from shinobi_runtime.commands.paths import (
    COMMITMENT_REGISTRY_PATH as _COMMITMENT_REGISTRY_PATH,
    CONFLICT_REGISTRY_PATH as _CONFLICT_REGISTRY_PATH,
    COMBAT_ZOOM_REGISTRY_PATH as _COMBAT_ZOOM_REGISTRY_PATH,
    ECONOMY_WORLD_PATH as _ECONOMY_WORLD_PATH,
    INVENTORY_REGISTRY_PATH as _INVENTORY_REGISTRY_PATH,
    PERSON_CONTINUITY_PATH as _PERSON_CONTINUITY_PATH,
    POPULATION_REGISTRY_PATH as _POPULATION_REGISTRY_PATH,
    RECOVERY_POLICY_PATH as _RECOVERY_POLICY_PATH,
    ROUTES_PATH as _ROUTES_PATH,
    WORLD_EVENT_REGISTRY_PATH as _WORLD_EVENT_REGISTRY_PATH,
)
from shinobi_runtime.diplomacy import treaty_obligation_policy
from shinobi_runtime.reducers import (
    PopulationPool, PopulationTransfer, apply_transfer, neutral_proportional_selection,
    settle_recovery,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import (
    CausalSchedulerRegistry,
    one_shot_event,
    settle_scheduler,
)
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


_GOVERNANCE_PATH = "state/reg/governance.json"
_DIPLOMACY_PATH = "state/reg/diplomacy.json"
_GOVERNANCE_MECHANICS_PATH = "game/data/mechanics/governance.json"
_LIVING_WORLD_POLICY_PATH = "game/rules/autonomy/living-world.json"


class TimeCommandsMixin:
    @staticmethod
    def _replace_host_events(
        scheduler: CausalSchedulerRegistry,
        host_id: str,
        events: Iterable[Any],
    ) -> None:
        retained = [
            event for event in scheduler.queue.snapshot() if event.target_host != host_id
        ]
        retained.extend(events)
        scheduler.queue.replace(retained)
        wrapper = scheduler.hosts.get(host_id)
        if wrapper is not None:
            due = min(
                (event.due_at for event in scheduler.queue.snapshot() if event.target_host == host_id),
                default=None,
            )
            wrapper.state.next_due = due
            if due is not None and wrapper.state.safe_through >= due:
                wrapper.state.safe_through = due.add_seconds(-1)
    @staticmethod
    def _population_pool_view(pool_id: str, record: Mapping[str, Any]) -> PopulationPool:
        profile = record.get("profile")
        dimensions = profile.get("dimension_counts") if isinstance(profile, Mapping) else None
        count = record.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0 or not isinstance(dimensions, Mapping):
            raise CommandRejectedError("governance_population_invalid")
        try:
            return PopulationPool(pool_id, count, dimensions)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("governance_population_invalid") from exc

    def _settlement_population_transfer(
        self, population: Dict[str, Any], *, source_pool_id: str, destination_pool_id: str,
        count: int, at: CampaignTime, jurisdiction_ref: str, direction: str,
    ) -> int:
        pools = population.get("pools")
        transfers = population.get("transfers")
        if not isinstance(pools, dict) or not isinstance(transfers, list):
            raise CommandRejectedError("population_registry_invalid")
        source_record = pools.get(source_pool_id)
        destination_record = pools.get(destination_pool_id)
        if not isinstance(source_record, dict) or not isinstance(destination_record, dict):
            return 0
        if source_record.get("linked_force_ref") is not None or destination_record.get("linked_force_ref") is not None:
            return 0
        source_rep = source_record.get("representation")
        destination_rep = destination_record.get("representation")
        if not isinstance(source_rep, dict) or not isinstance(destination_rep, dict):
            raise CommandRejectedError("population_representation_invalid")
        source_anonymous = source_rep.get("anonymous_count")
        destination_anonymous = destination_rep.get("anonymous_count")
        if (
            isinstance(source_anonymous, bool) or not isinstance(source_anonymous, int) or source_anonymous < 0
            or isinstance(destination_anonymous, bool) or not isinstance(destination_anonymous, int) or destination_anonymous < 0
        ):
            raise CommandRejectedError("population_representation_invalid")
        count = min(count, source_anonymous)
        if count <= 0:
            return 0
        source = self._population_pool_view(source_pool_id, source_record)
        destination = self._population_pool_view(destination_pool_id, destination_record)
        try:
            selected = neutral_proportional_selection(source, count)
            transfer_id = f"migration.{jurisdiction_ref}.{str(at).replace(':','').replace('-','')}.{direction}"
            transfer = PopulationTransfer(
                transfer_id=transfer_id, source_pool_id=source_pool_id, destination_pool_id=destination_pool_id,
                count=count, selected_dimensions=selected, selection_mode="neutral_proportional",
            )
            source_after, destination_after = apply_transfer(source, destination, transfer)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("governance_population_migration_invalid") from exc
        for record, after in ((source_record, source_after), (destination_record, destination_after)):
            record["count"] = after.total
            profile = record.get("profile")
            if not isinstance(profile, dict):
                raise CommandRejectedError("governance_population_invalid")
            profile["dimension_counts"] = {name: dict(values) for name, values in after.dimensions.items()}
            profile["category_counts"] = {str(record.get("category", "population")): after.total}
            numeric = profile.get("numeric_distributions")
            if isinstance(numeric, dict):
                for row in numeric.values():
                    if isinstance(row, dict) and isinstance(row.get("count"), int) and not isinstance(row.get("count"), bool):
                        row["count"] = after.total
            record["last_changed_at"] = str(at)
            record["status"] = "exhausted" if after.total == 0 else "active"
        source_rep["anonymous_count"] = source_anonymous - count
        destination_rep["anonymous_count"] = destination_anonymous + count
        transfers.append({
            "id": transfer_id, "at": str(at), "source_pool_id": source_pool_id,
            "destination_ref": destination_pool_id, "requested_count": count, "accepted": count, "rejected": 0,
            "authority_ref": str(destination_record.get("owner_ref") or jurisdiction_ref),
            "authority_basis": f"governance:{jurisdiction_ref}:aggregate_civil_migration",
            "policy_ref": None, "governance_jurisdiction_ref": jurisdiction_ref,
            "method": "aggregate_settlement_migration",
            "accepted_profile": {
                "numeric_distributions": {},
                "category_counts": {str(source_record.get("category", "population")): count},
                "dimension_counts": {name: dict(values) for name, values in selected.items()},
                "tags": ["settlement_migration", direction],
            },
            "materialized_person_ids": [], "source_removed": count, "destination_added": count,
            "selection_note": "Deterministic neutral proportional migration between conserved aggregate population pools.",
        })
        if hasattr(self, "_trim_population_transfer_history"):
            self._trim_population_transfer_history(transfers)
        return count

    def _settle_governed_civil_economies(
        self,
        governance: Dict[str, Any],
        population: Mapping[str, Any],
        holders: Dict[str, Any],
        finance: Mapping[str, Any],
        *,
        at: CampaignTime,
        compacted_months: int,
    ) -> list[Mapping[str, Any]]:
        """Settle aggregate founded-settlement economy without minting currency."""
        try:
            mechanics = self.repository.read_json(_GOVERNANCE_MECHANICS_PATH)
            routes = self.repository.read_json(_ROUTES_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("governance_civil_economy_invalid") from exc
        rules = mechanics.get("civil_economy") if isinstance(mechanics, Mapping) else None
        jurisdictions = governance.get("jurisdictions") if isinstance(governance, Mapping) else None
        pools = population.get("pools") if isinstance(population, Mapping) else None
        accounts = finance.get("accounts") if isinstance(finance, Mapping) else None
        places = routes.get("payload", {}).get("places") if isinstance(routes, Mapping) else None
        if not isinstance(rules, Mapping) or not isinstance(jurisdictions, dict) or not isinstance(pools, Mapping) or not isinstance(accounts, Mapping) or not isinstance(places, list):
            raise CommandRejectedError("governance_civil_economy_invalid")
        if isinstance(compacted_months, bool) or not isinstance(compacted_months, int) or compacted_months <= 0:
            raise CommandRejectedError("governance_civil_economy_invalid")
        ints = {}
        for key in (
            "workforce_fallback_milli", "adolescent_workforce_milli",
            "monthly_activity_per_worker_ryo", "monthly_consumption_per_resident_ryo",
            "base_productivity_milli", "integration_productivity_weight_milli",
            "food_activity_share_milli", "food_need_share_milli",
            "service_activity_share_milli", "service_need_share_milli",
            "tax_arrears_catchup_milli", "market_memory_milli", "market_service_bonus_milli",
            "migration_attractiveness_threshold_milli", "migration_food_floor_milli",
            "migration_service_floor_milli", "monthly_migration_rate_milli",
            "monthly_migration_cap", "severe_shortage_food_milli",
            "shortage_integration_penalty_milli", "shortage_resistance_gain_milli",
            "infrastructure_integration_penalty_milli", "infrastructure_resistance_gain_milli",
            "surplus_integration_gain_milli", "stability_resistance_recovery_milli",
            "civic_investment_per_resident_ryo", "civic_investment_effect_milli_per_month",
            "food_support_decay_milli_per_month", "service_investment_decay_milli_per_month",
        ):
            value = rules.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CommandRejectedError("governance_civil_economy_invalid")
            ints[key] = value
        references = rules.get("reference_population_by_status")
        if not isinstance(references, Mapping):
            raise CommandRejectedError("governance_civil_economy_invalid")
        country_by_place = {
            row.get("id"): row.get("country_id")
            for row in places if isinstance(row, Mapping) and isinstance(row.get("id"), str)
        }
        private_by_country = {
            row.get("scope_ref"): account_ref
            for account_ref, row in accounts.items()
            if isinstance(account_ref, str) and isinstance(row, Mapping)
            and row.get("kind") == "private_economy" and isinstance(row.get("scope_ref"), str)
        }
        reviews: list[Mapping[str, Any]] = []
        diplomacy_cache: Optional[Mapping[str, Any]] = None
        for jurisdiction_ref in sorted(jurisdictions):
            row = jurisdictions[jurisdiction_ref]
            if not isinstance(row, dict):
                raise CommandRejectedError("governance_registry_invalid")
            pool_ref = row.get("population_pool_ref")
            treasury_ref = row.get("treasury_holder_ref")
            if not isinstance(pool_ref, str) or not isinstance(treasury_ref, str):
                continue
            pool = pools.get(pool_ref)
            if not isinstance(pool, Mapping) or pool.get("category") != "settlement_resident":
                continue
            resident_count = pool.get("count")
            if isinstance(resident_count, bool) or not isinstance(resident_count, int) or resident_count < 0:
                raise CommandRejectedError("governance_population_invalid")
            profile = pool.get("profile")
            dimensions = profile.get("dimension_counts") if isinstance(profile, Mapping) else None
            age_band = dimensions.get("age_band") if isinstance(dimensions, Mapping) else None
            if isinstance(age_band, Mapping):
                adult = age_band.get("adult", 0)
                adolescent = age_band.get("adolescent", 0)
                if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (adult, adolescent)):
                    raise CommandRejectedError("governance_population_invalid")
                workforce = adult + (adolescent * ints["adolescent_workforce_milli"]) // 1000
            else:
                workforce = (resident_count * ints["workforce_fallback_milli"]) // 1000
            integration = row.get("integration_milli", 0)
            resistance = row.get("resistance_milli", 0)
            tax_milli = row.get("tax_milli", 0)
            if any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 1000 for v in (integration, resistance, tax_milli)):
                raise CommandRejectedError("governance_registry_invalid")
            productivity = min(1000, ints["base_productivity_milli"] + (integration * ints["integration_productivity_weight_milli"]) // 1000)
            gross_month = (workforce * ints["monthly_activity_per_worker_ryo"] * productivity) // 1000
            consumption_month = resident_count * ints["monthly_consumption_per_resident_ryo"]
            gross = gross_month * compacted_months
            consumption = consumption_month * compacted_months
            surplus = max(0, gross - consumption)
            shortage = max(0, consumption - gross)
            previous = row.get("civil_economy")
            old_market = previous.get("local_market_milli", 0) if isinstance(previous, Mapping) else 0
            if isinstance(old_market, bool) or not isinstance(old_market, int) or not 0 <= old_market <= 1000:
                raise CommandRejectedError("governance_civil_economy_invalid")
            food_activity = (gross_month * ints["food_activity_share_milli"]) // 1000
            food_need = (consumption_month * ints["food_need_share_milli"]) // 1000
            service_activity = (gross_month * ints["service_activity_share_milli"]) // 1000
            service_need = (consumption_month * ints["service_need_share_milli"]) // 1000
            old_food_support = previous.get("food_support_milli", 0) if isinstance(previous, Mapping) else 0
            old_infrastructure_capacity = previous.get("infrastructure_capacity_milli", 0) if isinstance(previous, Mapping) else 0
            old_service_investment = previous.get("service_investment_milli", 0) if isinstance(previous, Mapping) else 0
            if any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 1000 for v in (old_food_support, old_infrastructure_capacity, old_service_investment)):
                raise CommandRejectedError("governance_civil_economy_invalid")
            food_support = max(0, old_food_support - ints["food_support_decay_milli_per_month"] * compacted_months)
            service_investment = max(0, old_service_investment - ints["service_investment_decay_milli_per_month"] * compacted_months)
            infrastructure_capacity = old_infrastructure_capacity
            raw_food_security = 1000 if food_need == 0 else min(1000, (food_activity * 1000) // food_need)
            food_security = min(1000, raw_food_security + food_support)
            raw_service_capacity = 1000 if service_need == 0 else min(1000, (service_activity * 1000) // service_need)
            service_capacity = min(1000, raw_service_capacity + (old_market * ints["market_service_bonus_milli"]) // 1000 + service_investment)
            surplus_ratio = 0 if gross_month <= 0 else min(1000, (max(0, gross_month - consumption_month) * 1000) // gross_month)
            market_signal = min(1000, (service_capacity * 650 + surplus_ratio * 350) // 1000)
            new_weight = min(1000, max(0, 1000 - ints["market_memory_milli"]) * compacted_months)
            local_market = max(0, min(1000, (old_market * (1000 - new_weight) + market_signal * new_weight) // 1000))
            reference_population = references.get(row.get("status"), max(1, resident_count))
            if isinstance(reference_population, bool) or not isinstance(reference_population, int) or reference_population <= 0:
                raise CommandRejectedError("governance_civil_economy_invalid")
            raw_infrastructure_pressure = min(1000, max(0, ((resident_count - reference_population) * 1000) // reference_population))
            infrastructure_pressure = max(0, raw_infrastructure_pressure - infrastructure_capacity)
            shortage_ratio = 0 if consumption_month <= 0 else min(1000, (max(0, consumption_month - gross_month) * 1000) // consumption_month)
            integration_delta = (
                ((surplus_ratio * ints["surplus_integration_gain_milli"]) // 1000)
                - ((shortage_ratio * ints["shortage_integration_penalty_milli"]) // 1000)
                - ((infrastructure_pressure * ints["infrastructure_integration_penalty_milli"]) // 1000)
            ) * compacted_months
            resistance_delta = (
                ((shortage_ratio * ints["shortage_resistance_gain_milli"]) // 1000)
                + ((infrastructure_pressure * ints["infrastructure_resistance_gain_milli"]) // 1000)
                - (ints["stability_resistance_recovery_milli"] if shortage_ratio == 0 and infrastructure_pressure < 250 else 0)
            ) * compacted_months
            row["integration_milli"] = max(0, min(1000, integration + integration_delta))
            row["resistance_milli"] = max(0, min(1000, resistance + resistance_delta))
            integration_delta = row["integration_milli"] - integration
            resistance_delta = row["resistance_milli"] - resistance
            effective_integration = row["integration_milli"]
            effective_resistance = row["resistance_milli"]
            attractiveness = max(0, min(1000, (
                effective_integration * 300 + food_security * 250 + service_capacity * 200
                + (1000 - effective_resistance) * 150 + local_market * 150
                - infrastructure_pressure * 150
            ) // 1000))
            country_ref = country_by_place.get(row.get("place_ref"))
            private_holder_ref = private_by_country.get(country_ref) if isinstance(country_ref, str) else None
            old_arrears = previous.get("tax_arrears_ryo", 0) if isinstance(previous, Mapping) else 0
            if isinstance(old_arrears, bool) or not isinstance(old_arrears, int) or old_arrears < 0:
                raise CommandRejectedError("governance_civil_economy_invalid")
            tax_due = (gross * tax_milli) // 1000
            catchup = min(old_arrears, (tax_due * ints["tax_arrears_catchup_milli"]) // 1000) if tax_due else 0
            paid = 0
            if isinstance(private_holder_ref, str):
                source = holders.get(private_holder_ref)
                treasury = holders.get(treasury_ref)
                if not isinstance(source, dict) or not isinstance(treasury, dict):
                    raise CommandRejectedError("governance_civil_economy_account_invalid")
                available = source.get("currency.ryo", 0)
                treasury_balance = treasury.get("currency.ryo", 0)
                if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (available, treasury_balance)):
                    raise CommandRejectedError("governance_civil_economy_account_invalid")
                paid = min(available, tax_due + catchup)
                source["currency.ryo"] = available - paid
                treasury["currency.ryo"] = treasury_balance + paid
            current_paid = min(paid, tax_due)
            arrears_paid = max(0, paid - tax_due)
            tax_arrears = max(0, old_arrears - arrears_paid) + (tax_due - current_paid)

            migration_source_ref = previous.get("migration_source_pool_ref") if isinstance(previous, Mapping) else None
            migration_agreement_ref = previous.get("migration_agreement_ref") if isinstance(previous, Mapping) else None
            net_migration = 0
            if isinstance(migration_source_ref, str) and migration_source_ref != pool_ref:
                source_record = pools.get(migration_source_ref)
                lawful_migration = False
                if isinstance(source_record, Mapping):
                    source_owner = source_record.get("owner_ref")
                    lawful_migration = source_owner in (row.get("sovereign_ref"), row.get("administration_ref"))
                    if not lawful_migration and isinstance(migration_agreement_ref, str):
                        if diplomacy_cache is None:
                            try:
                                loaded_diplomacy = self.repository.read_json("state/reg/diplomacy.json")
                            except (FileNotFoundError, ValueError):
                                loaded_diplomacy = {}
                            diplomacy_cache = loaded_diplomacy if isinstance(loaded_diplomacy, Mapping) else {}
                        agreement = diplomacy_cache.get("agreements", {}).get(migration_agreement_ref) if isinstance(diplomacy_cache.get("agreements"), Mapping) else None
                        parties = agreement.get("party_refs", []) if isinstance(agreement, Mapping) else []
                        lawful_migration = bool(
                            isinstance(agreement, Mapping) and agreement.get("status") == "active"
                            and agreement.get("agreement_type") == "migration"
                            and row.get("sovereign_ref") in parties and source_owner in parties
                        )
                if lawful_migration:
                    monthly_base = max(1, (max(1, resident_count) * ints["monthly_migration_rate_milli"]) // 1000)
                    migration_capacity = min(ints["monthly_migration_cap"] * compacted_months, monthly_base * compacted_months)
                    if (
                        attractiveness >= ints["migration_attractiveness_threshold_milli"]
                        and food_security >= ints["migration_food_floor_milli"]
                        and service_capacity >= ints["migration_service_floor_milli"]
                        and infrastructure_pressure < 800
                    ):
                        moved = self._settlement_population_transfer(
                            population, source_pool_id=migration_source_ref, destination_pool_id=pool_ref,
                            count=migration_capacity, at=at, jurisdiction_ref=jurisdiction_ref, direction="inbound",
                        )
                        net_migration += moved
                    elif food_security < ints["severe_shortage_food_milli"] and resident_count > 1:
                        moved = self._settlement_population_transfer(
                            population, source_pool_id=pool_ref, destination_pool_id=migration_source_ref,
                            count=min(migration_capacity, max(1, resident_count // 20)), at=at,
                            jurisdiction_ref=jurisdiction_ref, direction="outbound",
                        )
                        net_migration -= moved
            resident_count_end = pools.get(pool_ref, {}).get("count", resident_count) if isinstance(pools.get(pool_ref), Mapping) else resident_count
            if isinstance(resident_count_end, bool) or not isinstance(resident_count_end, int) or resident_count_end < 0:
                raise CommandRejectedError("governance_population_invalid")
            if food_security < 600:
                development_priority = "food_security"
            elif infrastructure_pressure >= 350:
                development_priority = "housing_infrastructure"
            elif service_capacity < 600 and resident_count_end >= 50:
                development_priority = "market_services"
            else:
                development_priority = None
            development_need = max(1000 - food_security, infrastructure_pressure, 1000 - service_capacity)
            civic_investment_ryo = 0
            civic_investment_priority = None
            if development_priority is not None and development_need > 0 and isinstance(private_holder_ref, str):
                source = holders.get(private_holder_ref)
                treasury = holders.get(treasury_ref)
                if not isinstance(source, dict) or not isinstance(treasury, dict):
                    raise CommandRejectedError("governance_civil_economy_account_invalid")
                private_balance = source.get("currency.ryo", 0)
                treasury_balance = treasury.get("currency.ryo", 0)
                if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (private_balance, treasury_balance)):
                    raise CommandRejectedError("governance_civil_economy_account_invalid")
                full_budget = resident_count_end * ints["civic_investment_per_resident_ryo"] * compacted_months
                desired_budget = (full_budget * development_need) // 1000
                if full_budget > 0 and desired_budget > 0 and treasury_balance > 0:
                    civic_investment_ryo = min(treasury_balance, desired_budget)
                    treasury["currency.ryo"] = treasury_balance - civic_investment_ryo
                    source["currency.ryo"] = private_balance + civic_investment_ryo
                    full_effect = ints["civic_investment_effect_milli_per_month"] * compacted_months
                    effect = min(1000, (full_effect * civic_investment_ryo) // desired_budget)
                    civic_investment_priority = development_priority
                    if development_priority == "food_security":
                        food_support = min(1000, food_support + effect)
                    elif development_priority == "housing_infrastructure":
                        infrastructure_capacity = min(1000, infrastructure_capacity + effect)
                    elif development_priority == "market_services":
                        service_investment = min(1000, service_investment + effect)
            row["civil_economy"] = {
                "last_settled_at": str(at),
                "private_economy_holder_ref": private_holder_ref,
                "migration_source_pool_ref": migration_source_ref,
                "migration_agreement_ref": migration_agreement_ref,
                "workforce_count": workforce,
                "resident_count": resident_count_end,
                "net_migration_count": net_migration,
                "gross_activity_ryo": gross,
                "consumption_ryo": consumption,
                "surplus_ryo": surplus,
                "shortage_ryo": shortage,
                "food_security_milli": food_security,
                "service_capacity_milli": service_capacity,
                "local_market_milli": local_market,
                "attractiveness_milli": attractiveness,
                "infrastructure_pressure_milli": infrastructure_pressure,
                "integration_delta_milli": integration_delta,
                "resistance_delta_milli": resistance_delta,
                "development_priority": development_priority,
                "development_need_milli": development_need,
                "food_support_milli": food_support,
                "infrastructure_capacity_milli": infrastructure_capacity,
                "service_investment_milli": service_investment,
                "civic_investment_ryo": civic_investment_ryo,
                "civic_investment_priority": civic_investment_priority,
                "tax_due_ryo": tax_due,
                "tax_paid_ryo": paid,
                "tax_arrears_ryo": tax_arrears,
            }
            row["updated_at"] = str(at)
            reviews.append({
                "jurisdiction_ref": jurisdiction_ref,
                "resident_count": resident_count_end,
                "net_migration_count": net_migration,
                "workforce_count": workforce,
                "gross_activity_ryo": gross,
                "surplus_ryo": surplus,
                "shortage_ryo": shortage,
                "tax_due_ryo": tax_due,
                "tax_paid_ryo": paid,
                "tax_arrears_ryo": tax_arrears,
                "attractiveness_milli": attractiveness,
                "infrastructure_pressure_milli": infrastructure_pressure,
                "development_priority": development_priority,
                "civic_investment_ryo": civic_investment_ryo,
                "civic_investment_priority": civic_investment_priority,
            })
        return reviews

    def _advance_time(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(command.payload, ("target_time",), command.command_type)
        try:
            requested_target = CampaignTime.parse(command.payload["target_time"])
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("target_time_invalid") from exc
        if requested_target <= current_time:
            raise CommandRejectedError(
                "command_no_op"
                if requested_target == current_time
                else "target_time_before_current"
            )
        elapsed = int(
            (
                _campaign_datetime(requested_target)
                - _campaign_datetime(current_time)
            ).total_seconds()
        )
        if elapsed > self.MAX_ADVANCE_SECONDS:
            raise CommandRejectedError("target_time_exceeds_command_horizon")

        scene = copy.deepcopy(self._scene_base(current_time))
        try:
            zoom_registry = self.repository.read_json(_COMBAT_ZOOM_REGISTRY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("combat_zoom_registry_invalid") from exc
        pending_zoom = zoom_registry.get("pending_by_actor") if isinstance(zoom_registry, Mapping) else None
        if not isinstance(pending_zoom, Mapping):
            raise CommandRejectedError("combat_zoom_registry_invalid")
        if pending_zoom:
            # Aggregate combat has reserved exact identities for a linked scene.
            # Time may not advance until those exact consequences are reconciled
            # back into the parent battle exactly once.
            raise CommandRejectedError("time_boundary_requires_combat_zoom")
        if scene.get("active_combat") is True:
            raise CommandRejectedError("scene_time_passage_blocked")
        if scene.get("time_passage_allowed") is not True:
            boundaries = scene.get("known_clock_boundaries", [])
            if isinstance(boundaries, list):
                for boundary in boundaries:
                    if not isinstance(boundary, Mapping):
                        continue
                    try:
                        due = CampaignTime.parse(boundary.get("due_at"))
                    except (TypeError, ValueError):
                        continue
                    if due <= current_time:
                        raise CommandRejectedError("scene_boundary_requires_player_decision")
            raise CommandRejectedError("scene_time_passage_blocked")

        battlefield_boundary_time, battlefield_boundary = self._battlefield_next_boundary_time(
            actor_ref=command.actor_id,
            current_time=current_time,
            requested_target=requested_target,
        )
        scheduler_target = (
            battlefield_boundary_time
            if battlefield_boundary_time is not None and battlefield_boundary_time < requested_target
            else requested_target
        )
        scheduler = self._load_scheduler(
            current_time=current_time, scene=scene, target_time=scheduler_target
        )
        try:
            catchup = settle_scheduler(scheduler, target=scheduler_target)
        except (TypeError, ValueError, RuntimeError) as exc:
            raise CommandRejectedError("causal_scheduler_settlement_failed") from exc
        if catchup.unsafe_host_ids and catchup.interrupt is None and not catchup.budget_exhausted:
            raise CommandRejectedError("time_boundary_requires_domain_settlement")
        target = catchup.reached_time
        if catchup.budget_exhausted and target <= current_time and not catchup.processed_event_ids:
            # This should be unreachable with positive work budgets and bounded
            # per-event fanout. Fail rather than commit a no-progress continuation.
            raise CommandRejectedError("time_boundary_no_progress")

        pressures: Optional[Dict[str, Any]] = None
        faction_writes: Dict[str, Dict[str, Any]] = {}
        faction_reviews: list[str] = []
        pressure_reviews: list[str] = []
        world_registry_writes: Dict[str, Dict[str, Any]] = {}
        world_registry_reviews: list[str] = []
        economy_inventory_write: Optional[Dict[str, Any]] = None
        economy_reviews: list[Mapping[str, Any]] = []
        settlement_economy_reviews: list[Mapping[str, Any]] = []
        governance_write: Optional[Dict[str, Any]] = None
        house_writes: Dict[str, Dict[str, Any]] = {}
        house_reviews: list[str] = []
        population_write: Optional[Dict[str, Any]] = None
        population_reviews: list[Mapping[str, Any]] = []
        continuity_write: Optional[Dict[str, Any]] = None
        continuity_reviews: list[Mapping[str, Any]] = []
        recovery_reviews: list[Mapping[str, Any]] = []
        autonomy_policy = self._autonomy_policy_book()
        autonomy_record_writes: Dict[str, Dict[str, Any]] = {}
        autonomy_results: list[Mapping[str, Any]] = []
        team_reviews: list[Mapping[str, Any]] = []
        commitment_write: Optional[Dict[str, Any]] = None
        commitment_reviews: list[Mapping[str, Any]] = []
        diplomacy_write: Optional[Dict[str, Any]] = None
        conflict_write: Optional[Dict[str, Any]] = None
        world_events_for_time: Optional[Dict[str, Any]] = None

        for fact in catchup.public_facts:
            if not isinstance(fact, Mapping):
                raise CommandRejectedError("causal_scheduler_settlement_failed")
            kind = fact.get("scheduler_event_kind")
            payload = fact.get("payload")
            if not isinstance(payload, Mapping):
                raise CommandRejectedError("causal_scheduler_settlement_failed")
            try:
                latest_due = CampaignTime.parse(fact.get("latest_due"))
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("causal_scheduler_settlement_failed") from exc
            compacted = fact.get("compacted_boundaries")
            if isinstance(compacted, bool) or not isinstance(compacted, int) or compacted <= 0:
                raise CommandRejectedError("causal_scheduler_settlement_failed")

            if kind == "person.recovery.periodic_review":
                actor_ref = payload.get("actor_ref")
                owner_ref = payload.get("owner_ref")
                if not isinstance(actor_ref, str) or not isinstance(owner_ref, str):
                    raise CommandRejectedError("person_recovery_boundary_invalid")
                record = autonomy_record_writes.get(owner_ref)
                if record is None:
                    try:
                        loaded = self.repository.read_json(owner_ref)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("person_recovery_owner_invalid") from exc
                    if not isinstance(loaded, dict):
                        raise CommandRejectedError("person_recovery_owner_invalid")
                    record = copy.deepcopy(loaded)
                    autonomy_record_writes[owner_ref] = record
                if record.get("owner_id") != actor_ref:
                    raise CommandRejectedError("person_recovery_owner_invalid")
                try:
                    policy = self.repository.read_json(_RECOVERY_POLICY_PATH)
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("recovery_policy_invalid") from exc
                elapsed_seconds = compacted * 24 * 60 * 60
                before_condition = copy.deepcopy(record.get("condition"))
                before_resources = copy.deepcopy(record.get("resources"))
                try:
                    outcome = settle_recovery(record, elapsed_seconds=elapsed_seconds, policy=policy)
                except (TypeError, ValueError) as exc:
                    raise CommandRejectedError("person_recovery_resolution_invalid") from exc
                ready = record.get("life_status") in ("active", "alive") and isinstance(record.get("condition"), Mapping) and record["condition"].get("readiness") == "ready"
                host_id = str(fact.get("target_host"))
                host = scheduler.hosts.get(host_id)
                force_return: Optional[Mapping[str, Any]] = None
                if ready and host is not None and isinstance(host.metadata, Mapping):
                    force_path = host.metadata.get("force_path")
                    force_ref = host.metadata.get("force_ref")
                    return_class = host.metadata.get("return_availability_class")
                    if all(isinstance(value, str) and value for value in (force_path, force_ref, return_class)):
                        force_record = autonomy_record_writes.get(force_path)
                        if force_record is None:
                            try:
                                loaded_force = self.repository.read_json(force_path)
                            except (FileNotFoundError, ValueError) as exc:
                                raise CommandRejectedError("person_recovery_force_invalid") from exc
                            if not isinstance(loaded_force, dict) or loaded_force.get("id") != force_ref:
                                raise CommandRejectedError("person_recovery_force_invalid")
                            force_record = copy.deepcopy(loaded_force)
                            autonomy_record_writes[force_path] = force_record
                        availability = force_record.get("availability")
                        if not isinstance(availability, dict):
                            raise CommandRejectedError("person_recovery_force_invalid")
                        effective_return_class = return_class
                        returned_formation_ref: Optional[str] = None
                        returned_formation_path: Optional[str] = None
                        returned_team_paths: list[str] = []
                        if return_class == "deployed":
                            formation_ref = host.metadata.get("return_formation_ref")
                            formation_path = host.metadata.get("return_formation_path")
                            team_refs = host.metadata.get("return_team_refs")
                            can_reembed = (
                                isinstance(formation_ref, str) and formation_ref
                                and isinstance(formation_path, str) and formation_path
                                and isinstance(team_refs, list) and bool(team_refs)
                                and all(isinstance(ref, str) and ref for ref in team_refs)
                            )
                            formation_record = None
                            qualifying_teams: list[Tuple[str, Dict[str, Any]]] = []
                            if can_reembed:
                                formation_record = autonomy_record_writes.get(formation_path)
                                if formation_record is None:
                                    try:
                                        loaded_formation = self.repository.read_json(formation_path)
                                    except (FileNotFoundError, ValueError):
                                        loaded_formation = None
                                    if isinstance(loaded_formation, dict):
                                        formation_record = copy.deepcopy(loaded_formation)
                                formations = formation_record.get("formations") if isinstance(formation_record, Mapping) else None
                                formation = next(
                                    (
                                        row for row in formations or []
                                        if isinstance(row, dict)
                                        and row.get("id") == formation_ref
                                        and row.get("force_ref") == force_ref
                                    ),
                                    None,
                                )
                                current_location = record.get("current_location_id")
                                if (
                                    not isinstance(formation, dict)
                                    or formation.get("location_ref") != current_location
                                ):
                                    can_reembed = False
                                if can_reembed:
                                    total = formation.get("personnel_total")
                                    authorized = formation.get("authorized_personnel", total)
                                    if (
                                        isinstance(total, bool) or not isinstance(total, int)
                                        or isinstance(authorized, bool) or not isinstance(authorized, int)
                                        or total < 0 or total >= authorized
                                    ):
                                        can_reembed = False
                                if can_reembed:
                                    for team_ref in team_refs:
                                        try:
                                            team_path, team_view = self._exact_team(team_ref)
                                        except CommandRejectedError:
                                            continue
                                        team_record = autonomy_record_writes.get(team_path)
                                        if team_record is None:
                                            team_record = copy.deepcopy(dict(team_view))
                                        members = team_record.get("member_refs")
                                        if (
                                            team_record.get("status") == "active"
                                            and team_record.get("current_assignment_ref") == formation_ref
                                            and isinstance(members, list)
                                            and actor_ref in members
                                        ):
                                            qualifying_teams.append((team_path, team_record))
                                    if not qualifying_teams:
                                        can_reembed = False
                            if can_reembed and isinstance(formation_record, dict) and isinstance(formation, dict):
                                self._resize_formation_strength(formation, int(formation["personnel_total"]) + 1)
                                autonomy_record_writes[formation_path] = formation_record
                                returned_formation_ref = formation_ref
                                returned_formation_path = formation_path
                                for team_path, team_record in qualifying_teams:
                                    embedded = team_record.get("embedded_member_refs")
                                    if not isinstance(embedded, list):
                                        raise CommandRejectedError("team_embedded_assignment_invalid")
                                    if actor_ref not in embedded:
                                        embedded.append(actor_ref)
                                        embedded.sort()
                                    autonomy_record_writes[team_path] = team_record
                                    returned_team_paths.append(team_path)
                            else:
                                # The original formation may have moved, filled,
                                # dissolved, or lost this team's assignment while
                                # the person recovered.  Return the body to the
                                # force's nearest ready partition instead of
                                # inventing a new deployment.
                                effective_return_class = next(
                                    (
                                        candidate for candidate in (
                                            "ready_24h", "mobilizable_7d", "mobilizable_30d",
                                            "training_or_instruction", "essential_fixed_duty",
                                        )
                                        if isinstance(availability.get(candidate), int)
                                        and not isinstance(availability.get(candidate), bool)
                                    ),
                                    None,
                                )
                                if effective_return_class is None:
                                    raise CommandRejectedError("person_recovery_force_invalid")
                        medical = availability.get("medical_or_recovery")
                        destination = availability.get(effective_return_class)
                        if (
                            isinstance(medical, bool) or not isinstance(medical, int) or medical <= 0
                            or isinstance(destination, bool) or not isinstance(destination, int) or destination < 0
                        ):
                            raise CommandRejectedError("person_recovery_force_invalid")
                        returning_capability = self._reserve_draw(force_record, "medical_or_recovery", 1)
                        availability["medical_or_recovery"] = medical - 1
                        availability[effective_return_class] = destination + 1
                        if effective_return_class != "deployed":
                            self._reserve_add(force_record, effective_return_class, returning_capability, 1)
                        self._validate_reserve_counts(force_record)
                        if sum(value for value in availability.values() if isinstance(value, int) and not isinstance(value, bool)) != force_record.get("total"):
                            raise CommandRejectedError("person_recovery_force_conservation_failed")
                        force_return = {
                            "force_ref": force_ref,
                            "force_path": force_path,
                            "return_availability_class": effective_return_class,
                            "formation_ref": returned_formation_ref,
                            "formation_path": returned_formation_path,
                            "team_paths": returned_team_paths,
                        }
                if ready and host_id in scheduler.hosts:
                    scheduler.queue.replace(
                        event for event in scheduler.queue.snapshot()
                        if not (event.target_host == host_id and event.kind == "person.recovery.periodic_review")
                    )
                    scheduler.hosts[host_id].state.next_due = None
                    scheduler.hosts[host_id].state.safe_through = target
                changed = before_condition != record.get("condition") or before_resources != record.get("resources")
                event_id = None
                if changed:
                    if world_events_for_time is None:
                        world_events_for_time = self._world_events()
                    event_id = self._append_internal_event(
                        world_events_for_time, command=command, identity=f"{actor_ref}:{latest_due}:recovery",
                        kind="person_recovery_progressed", at=latest_due, host_refs=(actor_ref,), actor_refs=(actor_ref,),
                        affected_owner_refs=tuple(
                            dict.fromkeys(
                                [owner_ref]
                                + ([] if force_return is None else [
                                    ref for ref in (
                                        force_return.get("force_path"),
                                        force_return.get("formation_path"),
                                    ) if isinstance(ref, str)
                                ])
                                + ([] if force_return is None else [
                                    ref for ref in force_return.get("team_paths", []) if isinstance(ref, str)
                                ])
                            )
                        ),
                        material_consequence_refs=(f"recovery_hours:{elapsed_seconds // 3600}",),
                        classification="restricted", audience_refs=(), source_refs=(actor_ref,),
                    )
                recovery_reviews.append({
                    "person_ref": actor_ref, "at": str(latest_due), "compacted_days": compacted,
                    "ready": ready, "changed": changed, "event_id": event_id, "outcome": outcome,
                    "force_return": None if force_return is None else dict(force_return),
                })
                continue

            if kind == "faction.periodic_review":
                owner_ref = payload.get("owner_ref")
                faction_id = payload.get("faction_id")
                if not isinstance(owner_ref, str) or not isinstance(faction_id, str):
                    raise CommandRejectedError("faction_owner_invalid")
                if owner_ref not in faction_writes:
                    try:
                        loaded = self.repository.read_json(owner_ref)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("faction_owner_invalid") from exc
                    if not isinstance(loaded, dict):
                        raise CommandRejectedError("faction_owner_invalid")
                    faction_writes[owner_ref] = copy.deepcopy(loaded)
                faction_record = faction_writes[owner_ref]
                faction = faction_record.get("faction")
                plan = faction.get("plan_state") if isinstance(faction, dict) else None
                if (
                    not isinstance(plan, dict)
                    or faction.get("status") != "active"
                    or plan.get("status") != "active"
                ):
                    raise CommandRejectedError("faction_owner_invalid")
                plan["last_review_at"] = str(latest_due)
                faction_reviews.append(f"{faction_id}@{latest_due}x{compacted}")
                if world_events_for_time is None:
                    world_events_for_time = self._world_events()
                operation_progress = self._settle_one_world_operation(
                    faction_id=faction_id,
                    at=latest_due,
                    command=command,
                    world_events=world_events_for_time,
                    record_writes=autonomy_record_writes,
                )
                if operation_progress is not None:
                    autonomy_results.append(operation_progress)
                decisions = review_faction(
                    faction_record=faction_record,
                    at=latest_due,
                    compacted_reviews=compacted,
                    policy_book=autonomy_policy,
                )
                if decisions:
                    if world_events_for_time is None:
                        world_events_for_time = self._world_events()
                    for decision in decisions:
                        autonomy_results.append(
                            self._apply_autonomous_decision(
                                decision=decision,
                                at=latest_due,
                                command=command,
                                scheduler=scheduler,
                                world_events=world_events_for_time,
                                record_writes=autonomy_record_writes,
                                faction_record=faction_record,
                            )
                        )
                continue

            if kind == "team.periodic_review":
                owner_ref = payload.get("owner_ref")
                team_id = payload.get("team_id") or payload.get("identity")
                if not isinstance(owner_ref, str) or not isinstance(team_id, str):
                    raise CommandRejectedError("team_boundary_invalid")
                if world_events_for_time is None:
                    world_events_for_time = self._world_events()
                review = self._apply_team_autonomy_review(
                    owner_ref=owner_ref,
                    at=latest_due,
                    compacted=compacted,
                    command=command,
                    scheduler=scheduler,
                    policy_book=autonomy_policy,
                    world_events=world_events_for_time,
                    record_writes=autonomy_record_writes,
                )
                team_reviews.append(review)
                continue

            if kind == "canon_pressure.periodic_review":
                pressure_id = payload.get("pressure_id")
                if not isinstance(pressure_id, str) or not pressure_id.startswith("pressure_"):
                    raise CommandRejectedError("canon_pressure_boundary_invalid")
                if pressures is None:
                    try:
                        loaded = self.repository.read_json(self.pressures_path)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("canon_pressure_registry_invalid") from exc
                    if not isinstance(loaded, dict):
                        raise CommandRejectedError("canon_pressure_registry_invalid")
                    pressures = copy.deepcopy(loaded)
                pressure_map = pressures.get("pressures")
                pressure = pressure_map.get(pressure_id) if isinstance(pressure_map, dict) else None
                if not isinstance(pressure, dict) or pressure.get("id") != pressure_id:
                    raise CommandRejectedError("canon_pressure_registry_invalid")
                boundary = pressure.get("next_boundary")
                constraints = pressure.get("constraints")
                host_id = "host.canon_pressure." + pressure_id
                host = scheduler.hosts.get(host_id)
                if (
                    pressure.get("status") not in _ACTIVE_PRESSURE_STATUSES
                    or not isinstance(boundary, dict)
                    or boundary.get("host_ref") != host_id
                    or not isinstance(constraints, Mapping)
                    or host is None
                ):
                    raise CommandRejectedError("canon_pressure_boundary_invalid")
                # A canon pressure is a conditional pressure, never a forced
                # future.  Periodic review may keep the pressure current but
                # cannot manufacture the published outcome or block years of
                # world time merely because the pressure has actors/resources.
                # Material consequences must emerge through ordinary missions,
                # information, faction actions, or explicit player boundaries.
                boundary["settled_through"] = str(latest_due)
                boundary["due_at"] = (
                    None if host.state.next_due is None else str(host.state.next_due)
                )
                if world_events_for_time is None:
                    world_events_for_time = self._world_events()
                self._append_internal_event(
                    world_events_for_time,
                    command=command,
                    identity=f"{pressure_id}:{latest_due}:pressure",
                    kind="canon_pressure_reviewed",
                    at=latest_due,
                    host_refs=(pressure_id,),
                    actor_refs=tuple(x for x in pressure.get("actors", []) if isinstance(x, str)),
                    material_consequence_refs=(f"conditional_pressure:{pressure_id}",),
                    classification="restricted",
                    audience_refs=(),
                    source_refs=tuple(x for x in pressure.get("source_refs", []) if isinstance(x, str)),
                )
                pressure_reviews.append(f"{pressure_id}@{latest_due}x{compacted}")
                continue

            if kind == "economy.periodic_review":
                owner_ref = payload.get("owner_ref")
                target_host = fact.get("target_host")
                if owner_ref != _ECONOMY_WORLD_PATH or not isinstance(target_host, str):
                    raise CommandRejectedError("economy_boundary_invalid")
                if owner_ref not in world_registry_writes:
                    try:
                        loaded_economy = self.repository.read_json(owner_ref)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("economy_state_invalid") from exc
                    if not isinstance(loaded_economy, dict) or loaded_economy.get("schema") != "shinobi-world-registry":
                        raise CommandRejectedError("economy_state_invalid")
                    world_registry_writes[owner_ref] = copy.deepcopy(loaded_economy)
                economy_record = world_registry_writes[owner_ref]
                economy_payload = economy_record.get("payload")
                economy_root = economy_payload.get("economies_and_mission_markets") if isinstance(economy_payload, dict) else None
                finance = economy_root.get("finance") if isinstance(economy_root, dict) else None
                flows = finance.get("recurring_flows") if isinstance(finance, dict) else None
                arrears = finance.get("arrears_ryo") if isinstance(finance, dict) else None
                if not isinstance(finance, dict) or not isinstance(flows, list) or not isinstance(arrears, dict):
                    raise CommandRejectedError("economy_state_invalid")
                if economy_inventory_write is None:
                    try:
                        loaded_inventory = self.repository.read_json(_INVENTORY_REGISTRY_PATH)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("economy_inventory_invalid") from exc
                    if not isinstance(loaded_inventory, dict):
                        raise CommandRejectedError("economy_inventory_invalid")
                    economy_inventory_write = copy.deepcopy(loaded_inventory)
                    autonomy_record_writes[_INVENTORY_REGISTRY_PATH] = economy_inventory_write
                holders = economy_inventory_write.get("holders")
                if not isinstance(holders, dict):
                    raise CommandRejectedError("economy_inventory_invalid")
                mechanics = self._economy_mechanics()
                macro_rules = mechanics.get("macro_rules")
                catchup_cap_milli = macro_rules.get("arrears_catchup_cap_milli") if isinstance(macro_rules, Mapping) else None
                if isinstance(catchup_cap_milli, bool) or not isinstance(catchup_cap_milli, int) or catchup_cap_milli < 0 or catchup_cap_milli > 5000:
                    raise CommandRejectedError("economy_mechanics_invalid")
                ordered_flows = sorted(
                    flows,
                    key=lambda row: (
                        row.get("priority", 0) if isinstance(row, Mapping) else 0,
                        row.get("id", "") if isinstance(row, Mapping) else "",
                    ),
                )
                period_expected = period_paid = period_unpaid = 0
                account_expected_in: Dict[str, int] = {}
                account_expected_out: Dict[str, int] = {}
                account_paid_in: Dict[str, int] = {}
                account_paid_out: Dict[str, int] = {}
                arrears_before = sum(
                    value for value in arrears.values()
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0
                )
                partial_flow_count = 0
                for _period in range(compacted):
                    for flow in ordered_flows:
                        if not isinstance(flow, Mapping):
                            raise CommandRejectedError("economy_state_invalid")
                        flow_id = flow.get("id")
                        source_ref = flow.get("source_ref")
                        destination_ref = flow.get("destination_ref")
                        amount = flow.get("amount_ryo")
                        if (
                            not isinstance(flow_id, str) or not flow_id
                            or not isinstance(source_ref, str) or not source_ref
                            or not isinstance(destination_ref, str) or not destination_ref
                            or source_ref == destination_ref
                            or isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0
                        ):
                            raise CommandRejectedError("economy_state_invalid")
                        old_arrears = arrears.get(flow_id, 0)
                        if isinstance(old_arrears, bool) or not isinstance(old_arrears, int) or old_arrears < 0:
                            raise CommandRejectedError("economy_state_invalid")
                        arrears_catchup_due = min(old_arrears, (amount * catchup_cap_milli) // 1000)
                        due = amount + arrears_catchup_due
                        source = holders.setdefault(source_ref, {})
                        destination = holders.setdefault(destination_ref, {})
                        if not isinstance(source, dict) or not isinstance(destination, dict):
                            raise CommandRejectedError("economy_inventory_invalid")
                        available = source.get("currency.ryo", 0)
                        existing = destination.get("currency.ryo", 0)
                        if (
                            isinstance(available, bool) or not isinstance(available, int) or available < 0
                            or isinstance(existing, bool) or not isinstance(existing, int) or existing < 0
                        ):
                            raise CommandRejectedError("economy_inventory_invalid")
                        paid = min(available, due)
                        current_paid = min(paid, amount)
                        arrears_paid = max(0, paid - amount)
                        new_arrears = max(0, old_arrears - arrears_paid) + (amount - current_paid)
                        source["currency.ryo"] = available - paid
                        destination["currency.ryo"] = existing + paid
                        if new_arrears:
                            arrears[flow_id] = new_arrears
                        else:
                            arrears.pop(flow_id, None)
                        period_expected += amount
                        period_paid += paid
                        period_unpaid += amount - current_paid
                        account_expected_out[source_ref] = account_expected_out.get(source_ref, 0) + amount
                        account_expected_in[destination_ref] = account_expected_in.get(destination_ref, 0) + amount
                        account_paid_out[source_ref] = account_paid_out.get(source_ref, 0) + current_paid
                        account_paid_in[destination_ref] = account_paid_in.get(destination_ref, 0) + current_paid
                        if paid < due:
                            partial_flow_count += 1
                if governance_write is None:
                    try:
                        loaded_governance = self.repository.read_json(_GOVERNANCE_PATH)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("governance_registry_invalid") from exc
                    if not isinstance(loaded_governance, dict):
                        raise CommandRejectedError("governance_registry_invalid")
                    governance_write = copy.deepcopy(loaded_governance)
                if population_write is None:
                    existing_population = autonomy_record_writes.get(_POPULATION_REGISTRY_PATH)
                    if existing_population is not None:
                        population_write = existing_population
                    else:
                        try:
                            loaded_population = self.repository.read_json(_POPULATION_REGISTRY_PATH)
                        except (FileNotFoundError, ValueError) as exc:
                            raise CommandRejectedError("population_registry_invalid") from exc
                        if not isinstance(loaded_population, dict):
                            raise CommandRejectedError("population_registry_invalid")
                        population_write = copy.deepcopy(loaded_population)
                civil_reviews = self._settle_governed_civil_economies(
                    governance_write, population_write, holders, finance,
                    at=latest_due, compacted_months=compacted,
                )
                settlement_economy_reviews.extend(civil_reviews)
                host = scheduler.hosts.get(target_host)
                successor = host.state.next_due if host is not None else None
                finance["last_settled_at"] = str(latest_due)
                finance["next_due_at"] = None if successor is None else str(successor)
                economy_root["last_settled_at"] = str(latest_due)
                arrears_after = sum(
                    value for value in arrears.values()
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0
                )
                finance["last_period"] = {
                    "settled_at": str(latest_due),
                    "expected_ryo": period_expected,
                    "paid_ryo": period_paid,
                    "unpaid_current_ryo": period_unpaid,
                    "arrears_before_ryo": arrears_before,
                    "arrears_after_ryo": arrears_after,
                    "flow_count": len(ordered_flows) * compacted,
                    "partial_flow_count": partial_flow_count,
                }
                review = {
                    "at": str(latest_due),
                    "compacted_months": compacted,
                    "expected_ryo": period_expected,
                    "paid_ryo": period_paid,
                    "arrears_after_ryo": arrears_after,
                }
                economy_reviews.append(review)
                if world_events_for_time is None:
                    world_events_for_time = self._world_events()
                account_refs = sorted(set(account_expected_in) | set(account_expected_out))
                account_consequences = [f"account:{ref}" for ref in account_refs]
                for ref in account_refs:
                    expected_in = account_expected_in.get(ref, 0)
                    expected_out = account_expected_out.get(ref, 0)
                    paid_in = account_paid_in.get(ref, 0)
                    paid_out = account_paid_out.get(ref, 0)
                    account_consequences.append(
                        f"finance:{ref}:expected_in:{expected_in}:expected_out:{expected_out}:paid_in:{paid_in}:paid_out:{paid_out}"
                    )
                    structural_gap = max(0, expected_out - expected_in)
                    if structural_gap:
                        account_consequences.append(f"funding_gap:{ref}:{structural_gap}")
                self._append_internal_event(
                    world_events_for_time, command=command, identity=f"economy:{latest_due}",
                    kind="economy_period_settled", at=latest_due, host_refs=(target_host,),
                    affected_owner_refs=(
                        _ECONOMY_WORLD_PATH, _INVENTORY_REGISTRY_PATH,
                        *([_GOVERNANCE_PATH] if civil_reviews else []),
                    ),
                    material_consequence_refs=(
                        f"expected_ryo:{period_expected}", f"paid_ryo:{period_paid}", f"arrears_ryo:{arrears_after}",
                        *(f"settlement_tax:{row['jurisdiction_ref']}:{row['tax_paid_ryo']}" for row in civil_reviews),
                        *account_consequences,
                    ), classification="restricted", audience_refs=(), source_refs=(_ECONOMY_WORLD_PATH,),
                )
                continue

            if kind == "world_registry.periodic_review":
                owner_ref = payload.get("owner_ref")
                target_host = fact.get("target_host")
                if not isinstance(owner_ref, str) or not isinstance(target_host, str):
                    raise CommandRejectedError("world_registry_boundary_invalid")
                if owner_ref not in world_registry_writes:
                    try:
                        loaded = self.repository.read_json(owner_ref)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("world_registry_invalid") from exc
                    if not isinstance(loaded, dict) or loaded.get("schema") != "shinobi-world-registry":
                        raise CommandRejectedError("world_registry_invalid")
                    world_registry_writes[owner_ref] = copy.deepcopy(loaded)
                registry_record = world_registry_writes[owner_ref]
                payload_record = registry_record.get("payload")
                if not isinstance(payload_record, Mapping):
                    raise CommandRejectedError("world_registry_invalid")
                institutions = payload_record.get("institutions", [])
                clans = payload_record.get("clans", [])
                if not isinstance(institutions, list) or not isinstance(clans, list):
                    raise CommandRejectedError("world_registry_invalid")
                host = scheduler.hosts.get(target_host)
                successor = host.state.next_due if host is not None else None
                autonomy_candidates: list[Dict[str, Any]] = []
                # Clan institutions are first-class institutional actors.  They
                # share the same settlement contract and must never be omitted
                # merely because the registry stores them in a separate list.
                for institution in list(institutions) + list(clans):
                    if not isinstance(institution, dict):
                        raise CommandRejectedError("world_registry_invalid")
                    settlement = institution.get("settlement")
                    if not isinstance(settlement, dict):
                        continue
                    settlement["last_settled_at"] = str(latest_due)
                    settlement["next_due_at"] = None if successor is None else str(successor)
                    autonomy_candidates.append(institution)
                # The fiction is not capped to a fixed first-N subset.  This
                # bundled owner is itself the current bounded shard; every due
                # institution in the shard receives its material review.  When
                # registry growth requires further scaling, shard the owner or
                # persist a resumable work cursor rather than dropping actors.
                selected_institutions = sorted(
                    autonomy_candidates,
                    key=lambda row: str(row.get("id") or ""),
                )
                if selected_institutions:
                    if world_events_for_time is None:
                        world_events_for_time = self._world_events()
                    for institution in selected_institutions:
                        autonomy_results.append(
                            self._apply_institution_autonomy_review(
                                institution=institution,
                                at=latest_due,
                                compacted=compacted,
                                command=command,
                                policy_book=autonomy_policy,
                                institution_owner_ref=owner_ref,
                                world_events=world_events_for_time,
                                record_writes=autonomy_record_writes,
                            )
                        )
                if clans:
                    if world_events_for_time is None:
                        world_events_for_time = self._world_events()
                    family_result = self._settle_one_autonomous_npc_family_process(
                        at=latest_due, command=command, world_events=world_events_for_time,
                        record_writes=autonomy_record_writes,
                    )
                    if family_result is not None:
                        autonomy_results.append(dict(family_result))
                world_registry_reviews.append(f"{owner_ref}@{latest_due}x{compacted}")
                continue

            if kind == "house.periodic_review":
                owner_ref = payload.get("owner_ref")
                target_host = fact.get("target_host")
                if not isinstance(owner_ref, str) or not isinstance(target_host, str):
                    raise CommandRejectedError("house_boundary_invalid")
                if owner_ref not in house_writes:
                    try:
                        loaded = self.repository.read_json(owner_ref)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("house_owner_invalid") from exc
                    if not isinstance(loaded, dict) or loaded.get("schema") != "house":
                        raise CommandRejectedError("house_owner_invalid")
                    house_writes[owner_ref] = copy.deepcopy(loaded)
                house = house_writes[owner_ref]
                process = house.get("operating_process")
                if not isinstance(process, dict) or process.get("status") != "active":
                    raise CommandRejectedError("house_owner_invalid")
                host = scheduler.hosts.get(target_host)
                successor = host.state.next_due if host is not None else None
                process["last_review"] = str(latest_due)
                process["next_review"] = None if successor is None else str(successor)
                process["quiet_run_count"] = int(process.get("quiet_run_count", 0)) + compacted
                house_reviews.append(f"{owner_ref}@{latest_due}x{compacted}")
                continue

            if kind == "person_continuity.periodic_review":
                owner_ref = payload.get("owner_ref")
                if owner_ref != _PERSON_CONTINUITY_PATH:
                    raise CommandRejectedError("person_continuity_boundary_invalid")
                if continuity_write is None:
                    try:
                        loaded = self.repository.read_json(_PERSON_CONTINUITY_PATH)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("person_continuity_registry_invalid") from exc
                    if not isinstance(loaded, dict) or loaded.get("schema") != "person-continuity-registry":
                        raise CommandRejectedError("person_continuity_registry_invalid")
                    continuity_write = copy.deepcopy(loaded)
                entries = continuity_write.get("people")
                if not isinstance(entries, dict):
                    raise CommandRejectedError("person_continuity_registry_invalid")
                progressed = 0
                elapsed_days_total = 0
                for person_ref, row in entries.items():
                    if not isinstance(person_ref, str) or not isinstance(row, dict) or row.get("person_ref") != person_ref:
                        raise CommandRejectedError("person_continuity_registry_invalid")
                    try:
                        prior = CampaignTime.parse(row.get("resolved_through"))
                    except (TypeError, ValueError) as exc:
                        raise CommandRejectedError("person_continuity_registry_invalid") from exc
                    if prior >= latest_due:
                        continue
                    elapsed_days = max(0, int((_campaign_datetime(latest_due) - _campaign_datetime(prior)).total_seconds() // 86400))
                    row["resolved_through"] = str(latest_due)
                    row["life_experience_days"] = int(row.get("life_experience_days", 0)) + elapsed_days
                    row["review_count"] = int(row.get("review_count", 0)) + 1
                    row["career_review_cycles"] = int(row.get("career_review_cycles", 0)) + compacted
                    progressed += 1
                    elapsed_days_total += elapsed_days
                continuity_write["resolved_through"] = str(latest_due)
                continuity_reviews.append({
                    "at": str(latest_due),
                    "compacted_years": compacted,
                    "persistent_people_progressed": progressed,
                    "elapsed_person_days": elapsed_days_total,
                })
                continue

            if kind == "commitment.due":
                commitment_id = payload.get("commitment_id")
                if not isinstance(commitment_id, str) or not commitment_id.startswith("commitment."):
                    raise CommandRejectedError("commitment_due_boundary_invalid")
                if commitment_write is None:
                    try:
                        loaded_commitments = self.repository.read_json(_COMMITMENT_REGISTRY_PATH)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("commitment_registry_invalid") from exc
                    if not isinstance(loaded_commitments, dict):
                        raise CommandRejectedError("commitment_registry_invalid")
                    commitment_write = copy.deepcopy(loaded_commitments)
                records = commitment_write.get("records")
                if not isinstance(records, list):
                    raise CommandRejectedError("commitment_registry_invalid")
                matches = [row for row in records if isinstance(row, dict) and row.get("id") == commitment_id]
                if len(matches) != 1:
                    raise CommandRejectedError("commitment_due_boundary_invalid")
                record = matches[0]
                host_id = str(fact.get("target_host"))

                treaty_obligation = (
                    record.get("status") == "active"
                    and record.get("kind") == "obligation"
                    and isinstance(record.get("authority_basis"), str)
                    and record.get("authority_basis", "").startswith("agreement:")
                    and isinstance(record.get("causal_ref"), str)
                )
                treaty_decision = None
                if treaty_obligation:
                    authority_parts = str(record["authority_basis"]).split(":", 2)
                    agreement_ref = authority_parts[1] if len(authority_parts) >= 2 else None
                    agreement_type = authority_parts[2] if len(authority_parts) >= 3 else "treaty"
                    conflict_ref = record.get("causal_ref")
                    obligor_ref = record.get("subject_ref")
                    beneficiary_ref = record.get("target_ref")
                    if not all(isinstance(value, str) and value for value in (agreement_ref, conflict_ref, obligor_ref, beneficiary_ref)):
                        raise CommandRejectedError("treaty_obligation_invalid")
                    if diplomacy_write is None:
                        try:
                            loaded = self.repository.read_json(_DIPLOMACY_PATH)
                        except (FileNotFoundError, ValueError) as exc:
                            raise CommandRejectedError("diplomacy_registry_invalid") from exc
                        if not isinstance(loaded, dict):
                            raise CommandRejectedError("diplomacy_registry_invalid")
                        diplomacy_write = copy.deepcopy(loaded)
                    if conflict_write is None:
                        try:
                            loaded = self.repository.read_json(_CONFLICT_REGISTRY_PATH)
                        except (FileNotFoundError, ValueError) as exc:
                            raise CommandRejectedError("conflict_registry_invalid") from exc
                        if not isinstance(loaded, dict):
                            raise CommandRejectedError("conflict_registry_invalid")
                        conflict_write = copy.deepcopy(loaded)
                    owner_view: Mapping[str, Any] = {}
                    try:
                        owner_index = self.repository.read_json("state/index/owners/faction.json")
                        owner_path = owner_index.get("owners", {}).get(obligor_ref) if isinstance(owner_index, Mapping) else None
                        loaded_owner = self.repository.read_json(owner_path) if isinstance(owner_path, str) else None
                        if isinstance(loaded_owner, Mapping):
                            owner_view = loaded_owner
                    except (FileNotFoundError, ValueError):
                        owner_view = {}
                    sovereign_policy: Mapping[str, Any] = {}
                    force_view: Mapping[str, Any] = {}
                    try:
                        policy_book = self.repository.read_json(_LIVING_WORLD_POLICY_PATH)
                    except (FileNotFoundError, ValueError):
                        policy_book = {}
                    loaded_policy = policy_book.get("sovereign_diplomacy") if isinstance(policy_book, Mapping) else None
                    profiles = loaded_policy.get("profiles") if isinstance(loaded_policy, Mapping) else None
                    profile = profiles.get(obligor_ref) if isinstance(profiles, Mapping) else None
                    if isinstance(loaded_policy, Mapping) and isinstance(profile, Mapping):
                        merged_policy = {
                            key: loaded_policy.get(key)
                            for key in (
                                "honor_threshold_milli", "negotiate_threshold_milli",
                                "active_conflict_penalty_milli", "readiness_weight_milli",
                            )
                        }
                        merged_policy.update(dict(profile))
                        sovereign_policy = merged_policy
                        force_path = profile.get("force_path")
                        if isinstance(force_path, str):
                            try:
                                loaded_force = self.repository.read_json(force_path)
                            except (FileNotFoundError, ValueError):
                                loaded_force = {}
                            if isinstance(loaded_force, Mapping):
                                force_view = loaded_force
                    review_count = record.get("autonomous_review_count", 0)
                    if isinstance(review_count, bool) or not isinstance(review_count, int) or review_count < 0:
                        raise CommandRejectedError("treaty_obligation_invalid")
                    policy = treaty_obligation_policy(
                        diplomacy_write, conflict_write,
                        agreement_ref=agreement_ref, conflict_ref=conflict_ref,
                        obligor_ref=obligor_ref, beneficiary_ref=beneficiary_ref,
                        owner_view=owner_view, policy_view=sovereign_policy, force_view=force_view,
                        review_count=review_count,
                    )
                    treaty_decision = str(policy.get("decision") or "refuse")
                    conflict_records = conflict_write.get("records")
                    conflict = conflict_records.get(conflict_ref) if isinstance(conflict_records, Mapping) else None
                    agreements = diplomacy_write.get("agreements")
                    agreement = agreements.get(agreement_ref) if isinstance(agreements, Mapping) else None
                    if treaty_decision == "comply" and isinstance(conflict, dict):
                        sides = conflict.get("side_refs")
                        if not isinstance(sides, list):
                            raise CommandRejectedError("conflict_registry_invalid")
                        if obligor_ref not in sides and len(sides) >= 8:
                            treaty_decision = "negotiate" if review_count <= 0 else "refuse"
                        else:
                            if obligor_ref not in sides:
                                sides.append(obligor_ref); sides.sort()
                            objectives = conflict.get("objectives")
                            if not isinstance(objectives, dict):
                                raise CommandRejectedError("conflict_registry_invalid")
                            objectives.setdefault(obligor_ref, [f"Honor {agreement_type} defense obligation to {beneficiary_ref} under {agreement_ref}."])
                            alignments = conflict.setdefault("support_alignments", {})
                            if not isinstance(alignments, dict):
                                raise CommandRejectedError("conflict_registry_invalid")
                            alignments[obligor_ref] = beneficiary_ref
                            record["status"] = "completed"
                            record["resolved_at"] = str(latest_due)
                            record["resolution_summary"] = f"Autonomous sovereign review honored {agreement_type}; {obligor_ref} entered {conflict_ref} in support of {beneficiary_ref}."
                    if treaty_decision == "negotiate":
                        record["autonomous_review_count"] = review_count + 1
                        new_due = target.add_seconds(7 * 24 * 60 * 60)
                        record["due_at"] = str(new_due)
                        record["resolution_summary"] = "Autonomous sovereign review sought one bounded treaty consultation because current treaty conflicts or war burden made immediate compliance unsafe."
                        wrapper = scheduler.hosts.get(host_id)
                        if wrapper is None:
                            raise CommandRejectedError("treaty_obligation_scheduler_missing")
                        scheduler.upsert_event(one_shot_event(
                            kind="commitment.due", identity=commitment_id, source_host=host_id,
                            target_host=host_id, due_at=new_due, payload={"commitment_id": commitment_id},
                            priority=30, visibility="world_only", requires_player=False,
                        ))
                    elif treaty_decision == "refuse":
                        record["status"] = "failed"
                        record["resolved_at"] = str(latest_due)
                        record["resolution_summary"] = f"Autonomous sovereign review refused the {agreement_type} defense obligation after weighing treaty conflicts, war burden, and represented relations."
                        if isinstance(agreement, dict) and agreement.get("status") == "active":
                            agreement["status"] = "ended"
                            agreement["ended_at"] = str(latest_due)
                    elif treaty_decision == "cancel":
                        record["status"] = "cancelled"
                        record["resolved_at"] = str(latest_due)
                        record["resolution_summary"] = "Treaty obligation became moot because its agreement or protected conflict was no longer active."

                    if treaty_decision != "negotiate":
                        scheduler.hosts.pop(host_id, None)
                    scheduler.metrics.update({
                        "pending_event_count": len(scheduler.queue),
                        "host_count": len(scheduler.hosts),
                    })
                    if world_events_for_time is None:
                        world_events_for_time = self._world_events()
                    event_kind = {
                        "comply": "treaty_obligation_honored",
                        "negotiate": "treaty_obligation_negotiated",
                        "refuse": "treaty_obligation_breached",
                        "cancel": "treaty_obligation_moot",
                    }.get(treaty_decision, "treaty_obligation_reviewed")
                    treaty_event_id = self._append_semantic_event(
                        world_events_for_time, command=command, kind=event_kind, at=latest_due,
                        host_refs=(agreement_ref, conflict_ref), actor_refs=(obligor_ref, beneficiary_ref),
                        affected_owner_refs=(_COMMITMENT_REGISTRY_PATH, _DIPLOMACY_PATH, _CONFLICT_REGISTRY_PATH),
                        material_consequence_refs=(commitment_id, f"decision:{treaty_decision}", agreement_ref, conflict_ref),
                        classification=str(record.get("visibility") or "restricted"),
                        audience_refs=(obligor_ref, beneficiary_ref),
                        reducer_ref="shinobi_runtime.commands.treaty_obligation_autonomy",
                    )
                    if isinstance(agreement, dict):
                        evidence = agreement.setdefault("evidence_refs", [])
                        if treaty_event_id not in evidence:
                            evidence.append(treaty_event_id); evidence.sort()
                    incidents = diplomacy_write.get("incidents")
                    if not isinstance(incidents, list):
                        raise CommandRejectedError("diplomacy_registry_invalid")
                    incident_id = f"incident.treaty.{hashlib.sha256((commitment_id + ':' + treaty_decision).encode()).hexdigest()[:20]}"
                    if not any(isinstance(item, Mapping) and item.get("id") == incident_id for item in incidents):
                        incidents.append({
                            "id": incident_id, "at": str(latest_due),
                            "party_refs": sorted({obligor_ref, beneficiary_ref}),
                            "kind": event_kind, "evidence_ref": treaty_event_id,
                            "summary": str(record.get("resolution_summary") or f"Treaty obligation {treaty_decision}."),
                            "visibility": str(record.get("visibility") or "restricted"),
                        })
                    commitment_reviews.append({
                        "commitment_id": commitment_id, "due_at": str(latest_due),
                        "status": record.get("status"), "treaty_decision": treaty_decision,
                        "policy_score_milli": policy.get("score_milli"),
                    })
                    continue

                if record.get("status") == "active":
                    record["status"] = "overdue"
                    record["resolution_summary"] = "Due time passed without persisted completion, failure, or cancellation evidence."
                scheduler.hosts.pop(host_id, None)
                scheduler.metrics.update({
                    "pending_event_count": len(scheduler.queue),
                    "host_count": len(scheduler.hosts),
                })
                commitment_reviews.append({
                    "commitment_id": commitment_id,
                    "due_at": str(latest_due),
                    "status": record.get("status"),
                })
                if world_events_for_time is None:
                    world_events_for_time = self._world_events()
                self._append_semantic_event(
                    world_events_for_time, command=command, kind="commitment_overdue", at=latest_due,
                    host_refs=tuple(x for x in (record.get("host_ref"),) if isinstance(x, str)),
                    actor_refs=tuple(x for x in (record.get("subject_ref"), record.get("target_ref")) if isinstance(x, str)),
                    affected_owner_refs=(_COMMITMENT_REGISTRY_PATH,),
                    material_consequence_refs=(commitment_id, "status:overdue"),
                    classification=str(record.get("visibility") or "restricted"),
                    audience_refs=tuple(x for x in (record.get("subject_ref"), record.get("target_ref")) if isinstance(x, str)),
                    reducer_ref="shinobi_runtime.commands.commitment_due_settlement",
                )
                continue

            if kind == "population.periodic_review":
                owner_ref = payload.get("owner_ref")
                policy_ref = payload.get("policy_ref")
                if owner_ref != _POPULATION_REGISTRY_PATH or not isinstance(policy_ref, str):
                    raise CommandRejectedError("population_demography_boundary_invalid")
                if population_write is None:
                    existing_population = autonomy_record_writes.get(_POPULATION_REGISTRY_PATH)
                    if existing_population is not None:
                        population_write = existing_population
                    else:
                        try:
                            loaded = self.repository.read_json(_POPULATION_REGISTRY_PATH)
                        except (FileNotFoundError, ValueError) as exc:
                            raise CommandRejectedError("population_registry_invalid") from exc
                        if not isinstance(loaded, dict):
                            raise CommandRejectedError("population_registry_invalid")
                        population_write = copy.deepcopy(loaded)
                        autonomy_record_writes[_POPULATION_REGISTRY_PATH] = population_write
                population_reviews.append(
                    self._settle_population_demography(
                        population_write,
                        at=latest_due,
                        compacted_years=compacted,
                        policy_ref=policy_ref,
                    )
                )
                continue

            raise CommandRejectedError("causal_scheduler_event_unhandled")

        conflict_write_preexisting = conflict_write is not None
        if conflict_write is None:
            try:
                loaded_conflict = self.repository.read_json(_CONFLICT_REGISTRY_PATH)
            except FileNotFoundError:
                loaded_conflict = None
            except ValueError as exc:
                raise CommandRejectedError("conflict_registry_invalid") from exc
            if loaded_conflict is not None:
                if not isinstance(loaded_conflict, dict):
                    raise CommandRejectedError("conflict_registry_invalid")
                conflict_write = copy.deepcopy(loaded_conflict)

        battlefield_result = (
            self._settle_battlefields(
                conflict_write,
                actor_ref=command.actor_id,
                start_time=current_time,
                end_time=target,
            )
            if conflict_write is not None
            else {"reviews": [], "delivered_reports": [], "player_decisions": [], "changed": False}
        )
        battlefield_reviews = battlefield_result.get("reviews", [])
        battlefield_reports = battlefield_result.get("delivered_reports", [])
        battlefield_decisions = battlefield_result.get("player_decisions", [])
        battlefield_changed = battlefield_result.get("changed") is True
        if battlefield_changed:
            if world_events_for_time is None:
                world_events_for_time = self._world_events()
            self._append_semantic_event(
                world_events_for_time,
                command=command,
                kind="battlefield_operations_settled",
                at=target,
                host_refs=tuple(
                    sorted(
                        {
                            str(row.get("battlefield_ref"))
                            for row in battlefield_reviews
                            if isinstance(row, Mapping) and isinstance(row.get("battlefield_ref"), str)
                        }
                    )
                ),
                affected_owner_refs=(_CONFLICT_REGISTRY_PATH,),
                material_consequence_refs=(
                    f"battlefield_reviews:{len(battlefield_reviews)}",
                    f"delivered_reports:{len(battlefield_reports)}",
                ),
                classification="restricted",
                audience_refs=(command.actor_id,),
                reducer_ref="shinobi_runtime.commands.battlefield_time_settlement",
            )
        elif not conflict_write_preexisting:
            # A read-only copy should not become a spurious write just because
            # time was advanced while no operational battlefield changed.
            conflict_write = None

        battlefield_player_interrupt = bool(battlefield_reports)
        battlefield_protected_decision = bool(battlefield_decisions)

        scene["world_time"] = str(target)
        location = scene.get("location_id")
        if not isinstance(location, str) or not location:
            raise CommandRejectedError("campaign_scene_invalid")

        interrupt_event_id = None
        if catchup.interrupt is None and not battlefield_player_interrupt:
            scene["scene_summary"] = (
                f"Time advances from {current_time} to {target} at {location}; "
                "no player decision boundary was reached."
            )
            scene["decision_required"] = (
                "The prior unresolved decision surface remains; this wait creates no "
                "additional consequential player choice."
            )
        elif catchup.interrupt is None:
            report = battlefield_reports[0]
            report_id = report.get("id") if isinstance(report, Mapping) else None
            battlefield_ref = next(
                (
                    row.get("battlefield_ref")
                    for row in battlefield_reviews
                    if isinstance(row, Mapping)
                    and any(
                        isinstance(change, Mapping)
                        and change.get("report_id") == report_id
                        for change in row.get("changes", [])
                    )
                ),
                None,
            )
            scene["scene_summary"] = (
                f"Time reaches {target} at {location}. Operational battlefield information "
                f"has lawfully reached Wei through report {report_id}."
            )
            if battlefield_protected_decision:
                decision_row = battlefield_decisions[0]
                decision_battlefield = decision_row.get("battlefield_ref") or battlefield_ref or "battlefield.unknown"
                decision_report = decision_row.get("report_id") or report_id or "report.unknown"
                scene["time_passage_allowed"] = False
                scene["decision_required"] = f"battlefield:{decision_battlefield}:{decision_report}"
            else:
                # Critical but non-collapse reports interrupt the current wait so
                # the GM can surface them, while still allowing the player to
                # continue the same standing wait without a forced command.
                scene["decision_required"] = None
        else:
            visible = catchup.interrupt.visible_context
            causal_interrupt_id = (
                visible.get("event_id") if isinstance(visible, Mapping) else None
            )
            if not isinstance(causal_interrupt_id, str):
                raise CommandRejectedError("causal_scheduler_interrupt_invalid")
            pending_interrupt = next(
                (
                    event
                    for event in scheduler.queue.snapshot()
                    if event.event_id == causal_interrupt_id
                ),
                None,
            )
            if pending_interrupt is None:
                raise CommandRejectedError("causal_scheduler_interrupt_invalid")
            semantic_interrupt = (
                pending_interrupt.payload.get("scene_event_id")
                or pending_interrupt.payload.get("mission_id")
                or pending_interrupt.payload.get("commitment_id")
                or pending_interrupt.payload.get("identity")
                or causal_interrupt_id
            )
            interrupt_event_id = (
                semantic_interrupt
                if isinstance(semantic_interrupt, str) and semantic_interrupt
                else causal_interrupt_id
            )
            scene["time_passage_allowed"] = False
            scene["scene_summary"] = (
                f"Time reaches {target} at {location}. The causal boundary "
                f"{interrupt_event_id} is due; no player response has been chosen."
            )
            scene["decision_required"] = (
                f"The boundary {interrupt_event_id} requires an explicit player response."
            )

        demographic_event_id = None
        if population_reviews:
            if world_events_for_time is None:
                world_events_for_time = self._world_events()
            total_births = sum(
                row.get("births", 0)
                for review in population_reviews
                for row in review.get("pool_results", [])
                if isinstance(row, Mapping)
            )
            total_deaths = sum(
                row.get("deaths", 0)
                for review in population_reviews
                for row in review.get("pool_results", [])
                if isinstance(row, Mapping)
            )
            demographic_event_id = self._append_semantic_event(
                world_events_for_time, command=command, kind="population_demography_settled", at=target,
                host_refs=("host.population.great_villages",),
                affected_owner_refs=(_POPULATION_REGISTRY_PATH,),
                material_consequence_refs=(f"births:{total_births}", f"deaths:{total_deaths}"),
                classification="restricted",
                audience_refs=(command.actor_id,),
                reducer_ref="shinobi_runtime.commands.population_demography",
            )

        writes: Dict[str, bytes] = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=target)),
            self.scene_path: _json_bytes(scene),
            **self._scheduler_write_images(scheduler),
        }
        if pressures is not None:
            writes[self.pressures_path] = _json_bytes(pressures)
        for owner_ref, faction_record in faction_writes.items():
            writes[owner_ref] = _json_bytes(faction_record)
        for owner_ref, world_record in world_registry_writes.items():
            writes[owner_ref] = _json_bytes(world_record)
        for owner_ref, house_record in house_writes.items():
            writes[owner_ref] = _json_bytes(house_record)
        for owner_ref, autonomous_record in autonomy_record_writes.items():
            writes[owner_ref] = _json_bytes(autonomous_record)
        if governance_write is not None:
            writes[_GOVERNANCE_PATH] = _json_bytes(governance_write)
        if population_write is not None:
            writes[_POPULATION_REGISTRY_PATH] = _json_bytes(population_write)
        if continuity_write is not None:
            writes[_PERSON_CONTINUITY_PATH] = _json_bytes(continuity_write)
        if commitment_write is not None:
            writes[_COMMITMENT_REGISTRY_PATH] = _json_bytes(commitment_write)
        if diplomacy_write is not None:
            writes[_DIPLOMACY_PATH] = _json_bytes(diplomacy_write)
        if conflict_write is not None:
            writes[_CONFLICT_REGISTRY_PATH] = _json_bytes(conflict_write)
        persisted_world_events_for_time = None
        if world_events_for_time is not None:
            world_event_writes = self._world_event_writes(world_events_for_time)
            writes.update(world_event_writes)
            persisted_world_events_for_time = json.loads(
                world_event_writes[_WORLD_EVENT_REGISTRY_PATH].decode("utf-8")
            )
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("time command write set changed after planning")
            self._assert_meta(
                overlay, manifest, meta_path=self.meta_path, command=command, world_time=target
            )
            staged_scene = overlay.read_json(self.scene_path)
            staged_scheduler = self._scheduler_from_reader(overlay)
            if staged_scene.get("world_time") != str(target) or staged_scheduler.world_time != target:
                raise ValueError("time command core clocks diverge")
            if catchup.interrupt is not None and staged_scene.get("time_passage_allowed") is not False:
                raise ValueError("causal interrupt did not close time passage")
            if battlefield_protected_decision and catchup.interrupt is None and staged_scene.get("time_passage_allowed") is not False:
                raise ValueError("battlefield decision did not close time passage")
            if pressures is not None and overlay.read_json(self.pressures_path) != pressures:
                raise ValueError("canon pressure after-image differs from causal review")
            for owner_ref, expected_record in faction_writes.items():
                if owner_ref in expected_paths and overlay.read_json(owner_ref) != expected_record:
                    raise ValueError("faction review after-image differs from plan")
            for owner_ref, expected_record in world_registry_writes.items():
                if owner_ref in expected_paths and overlay.read_json(owner_ref) != expected_record:
                    raise ValueError("world registry review after-image differs from plan")
            for owner_ref, expected_record in house_writes.items():
                if owner_ref in expected_paths and overlay.read_json(owner_ref) != expected_record:
                    raise ValueError("house review after-image differs from plan")
            for owner_ref, expected_record in autonomy_record_writes.items():
                if owner_ref in expected_paths and overlay.read_json(owner_ref) != expected_record:
                    raise ValueError("autonomous owner after-image differs from plan")
            if governance_write is not None and _GOVERNANCE_PATH in expected_paths and overlay.read_json(_GOVERNANCE_PATH) != governance_write:
                raise ValueError("governance civil-economy after-image differs from plan")
            if population_write is not None and _POPULATION_REGISTRY_PATH in expected_paths and overlay.read_json(_POPULATION_REGISTRY_PATH) != population_write:
                raise ValueError("population demographic after-image differs from plan")
            if continuity_write is not None and overlay.read_json(_PERSON_CONTINUITY_PATH) != continuity_write:
                raise ValueError("person continuity after-image differs from plan")
            if commitment_write is not None and overlay.read_json(_COMMITMENT_REGISTRY_PATH) != commitment_write:
                raise ValueError("commitment due after-image differs from plan")
            if (
                persisted_world_events_for_time is not None
                and overlay.read_json(_WORLD_EVENT_REGISTRY_PATH) != persisted_world_events_for_time
            ):
                raise ValueError("time semantic history after-image differs from plan")

        return _BuiltPlan(
            code=(
                "advance_time_ready"
                if catchup.interrupt is None and not battlefield_player_interrupt
                else "advance_time_interrupt_ready"
            ),
            affected_refs=tuple(sorted(writes)),
            writes=writes,
            result={
                "command_type": command.command_type,
                "world_time": str(target),
                "requested_time": str(requested_target),
                "interrupted": catchup.interrupt is not None or battlefield_player_interrupt,
                "interrupt_event_id": interrupt_event_id,
                "battlefield_boundary": dict(battlefield_boundary) if isinstance(battlefield_boundary, Mapping) else None,
                "battlefield_reviews": [dict(item) for item in battlefield_reviews],
                "battlefield_reports": [dict(item) for item in battlefield_reports],
                "battlefield_player_decisions": [dict(item) for item in battlefield_decisions],
                "processed_causal_events": list(catchup.processed_event_ids),
                "faction_reviews": sorted(faction_reviews),
                "canon_pressure_reviews": sorted(pressure_reviews),
                "world_registry_reviews": sorted(world_registry_reviews),
                "economy_reviews": [dict(item) for item in economy_reviews],
                "settlement_economy_reviews": [dict(item) for item in settlement_economy_reviews],
                "house_reviews": sorted(house_reviews),
                "team_reviews": [dict(item) for item in team_reviews],
                "commitment_reviews": [dict(item) for item in commitment_reviews],
                "autonomous_actions": [dict(item) for item in autonomy_results],
                "population_demographic_reviews": [dict(item) for item in population_reviews],
                "person_continuity_reviews": [dict(item) for item in continuity_reviews],
                "person_recovery_reviews": [dict(item) for item in recovery_reviews],
                "population_demography_event_id": demographic_event_id,
                "scheduler_metrics": dict(scheduler.metrics),
                "continuation_required": bool(
                    target < requested_target
                    and catchup.interrupt is None
                    and not battlefield_player_interrupt
                    and (catchup.budget_exhausted or scheduler_target < requested_target)
                ),
                "continuation_target": (
                    str(requested_target)
                    if target < requested_target
                    and catchup.interrupt is None
                    and not battlefield_player_interrupt
                    and (catchup.budget_exhausted or scheduler_target < requested_target)
                    else None
                ),
                "named_person_owner_scans": 0,
                "faction_directory_scans": 0,
            },
            validator=validate,
        )

