"""Medical treatment and ocular-procedure command domain."""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from typing import (
    Any,
    Dict,
    Mapping,
    Optional,
)

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.core import _BuiltPlan, _campaign_datetime, _exact_payload, _json_bytes, _stable_id
from shinobi_runtime.domain import LocationGraph
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


from shinobi_runtime.commands.paths import (
    ROUTES_PATH as _ROUTES_PATH,
    MEDICAL_MECHANICS_PATH as _MEDICAL_MECHANICS_PATH,
    MEDICAL_IMPLANT_PROFILES_PATH as _MEDICAL_IMPLANT_PROFILES_PATH,
    BIOLOGICAL_IMPLANTS_PATH as _BIOLOGICAL_IMPLANTS_PATH,
    OCULAR_REGISTRY_PATH as _OCULAR_REGISTRY_PATH,
)


class MedicalCommandsMixin:
    def _medical_facility(self, location_ref: str, *, required: bool, specialty: Optional[str] = None) -> Decimal:
        try:
            graph = LocationGraph(self.repository.read_json(_ROUTES_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("medical_facility_registry_invalid") from exc
        place = graph.place(location_ref)
        modules = place.get("mechanical_modules") if isinstance(place, Mapping) else None
        medical = modules.get("medical") if isinstance(modules, Mapping) else None
        if not isinstance(medical, Mapping):
            if required:
                raise CommandRejectedError("medical_facility_required")
            return Decimal("1")
        quality = medical.get("quality_milli")
        if (
            isinstance(quality, bool) or not isinstance(quality, int) or quality < 100 or quality > 1500
        ):
            raise CommandRejectedError("medical_facility_registry_invalid")
        specialties = medical.get("specialties", [])
        if not isinstance(specialties, list) or any(not isinstance(value, str) for value in specialties):
            raise CommandRejectedError("medical_facility_registry_invalid")
        if specialty is not None and specialty not in specialties:
            raise CommandRejectedError("medical_facility_specialty_required")
        return Decimal(quality) / Decimal(1000)


    @staticmethod
    def _medical_operator_quality(practitioner: Mapping[str, Any]) -> int:
        ops = practitioner.get("operational_skills")
        chakra = practitioner.get("chakra_dimensions")
        spec = practitioner.get("medical_specialization")
        medicine = ops.get("medicine", 0) if isinstance(ops, Mapping) else 0
        control = chakra.get("control", 0) if isinstance(chakra, Mapping) else 0
        surgery = 0
        if isinstance(spec, Mapping):
            surgery = max(int(spec.get("advanced_surgery", 0) or 0), int(spec.get("ocular_surgery", 0) or 0))
        for value in (medicine, control, surgery):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CommandRejectedError("medical_operator_invalid")
        return int((Decimal(medicine) * Decimal("0.50") + Decimal(control) * Decimal("0.30") + Decimal(surgery) * Decimal("0.20")).to_integral_value())


    def _medical_treatment_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("action", "patient_ref", "practitioner_ref", "facility_ref", "injury_ref", "implant_ref", "body_site", "target_time", "active_hours", "summary", "visibility"),
            command.command_type,
        )
        action = command.payload["action"]
        if action not in ("stabilize", "treat", "surgery", "implant", "remove_implant"):
            raise CommandRejectedError("medical_action_invalid")
        patient_ref = _stable_id(command.payload["patient_ref"], "medical_patient_invalid")
        practitioner_ref = _stable_id(command.payload["practitioner_ref"], "medical_practitioner_invalid")
        if command.actor_id not in (patient_ref, practitioner_ref):
            raise CommandRejectedError("medical_actor_not_party")
        facility_raw = command.payload["facility_ref"]
        facility_ref = None if facility_raw is None else _stable_id(facility_raw, "medical_facility_invalid", prefix="place.")
        injury_ref = command.payload["injury_ref"]
        if injury_ref is not None and (not isinstance(injury_ref, str) or not injury_ref or len(injury_ref) > 500):
            raise CommandRejectedError("medical_injury_invalid")
        implant_raw = command.payload["implant_ref"]
        implant_ref = None if implant_raw is None else _stable_id(implant_raw, "medical_implant_invalid")
        body_site = command.payload["body_site"]
        if body_site is not None and (not isinstance(body_site, str) or not body_site or len(body_site) > 80):
            raise CommandRejectedError("medical_body_site_invalid")
        summary = command.payload["summary"]
        visibility = command.payload["visibility"]
        if not isinstance(summary, str) or not summary or len(summary) > 1000:
            raise CommandRejectedError("medical_summary_invalid")
        if visibility not in ("public", "restricted", "secret"):
            raise CommandRejectedError("medical_visibility_invalid")
        try:
            target_time = CampaignTime.parse(command.payload["target_time"])
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("medical_target_time_invalid") from exc
        if target_time <= current_time:
            raise CommandRejectedError("medical_target_time_invalid")
        try:
            active_hours = Decimal(str(command.payload["active_hours"]))
        except Exception as exc:
            raise CommandRejectedError("medical_active_hours_invalid") from exc
        elapsed_hours = Decimal(int((_campaign_datetime(target_time) - _campaign_datetime(current_time)).total_seconds())) / Decimal(3600)
        if not active_hours.is_finite() or active_hours <= 0 or active_hours > elapsed_hours:
            raise CommandRejectedError("medical_active_hours_invalid")

        patient_path, patient = self._resolve_actor_for_write(patient_ref)
        practitioner_path, practitioner = self._resolve_actor_for_write(practitioner_ref)
        if patient.get("life_status") not in ("active", "alive") or practitioner.get("life_status") not in ("active", "alive"):
            raise CommandRejectedError("medical_party_unavailable")
        patient_location = patient.get("current_location_id")
        if not isinstance(patient_location, str) or practitioner.get("current_location_id") != patient_location:
            raise CommandRejectedError("medical_party_not_colocated")
        if facility_ref is not None and facility_ref != patient_location:
            raise CommandRejectedError("medical_facility_not_colocated")
        try:
            mechanics = self.repository.read_json(_MEDICAL_MECHANICS_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("medical_mechanics_invalid") from exc
        thresholds = mechanics.get("procedure_thresholds") if isinstance(mechanics, Mapping) else None
        rule = thresholds.get(action) if isinstance(thresholds, Mapping) else None
        if not isinstance(rule, Mapping):
            raise CommandRejectedError("medical_mechanics_invalid")
        minimum = rule.get("minimum_quality")
        restore_base = rule.get("base_health_restore")
        requires_facility = rule.get("requires_medical_facility")
        if isinstance(minimum, bool) or not isinstance(minimum, int) or isinstance(restore_base, bool) or not isinstance(restore_base, int) or not isinstance(requires_facility, bool):
            raise CommandRejectedError("medical_mechanics_invalid")
        if requires_facility and facility_ref is None:
            raise CommandRejectedError("medical_facility_required")
        required_specialty = "surgery" if action in ("surgery", "implant", "remove_implant") else ("stabilization" if action == "stabilize" and requires_facility else None)
        facility_factor = (
            self._medical_facility(facility_ref, required=True, specialty=required_specialty)
            if facility_ref is not None
            else Decimal("1")
        )
        raw_quality = self._medical_operator_quality(practitioner)
        effective_quality = int((Decimal(raw_quality) * facility_factor).to_integral_value())
        if effective_quality < minimum:
            raise CommandRejectedError("medical_operator_unqualified")

        if action in ("implant", "remove_implant"):
            if implant_ref is None or body_site is None or injury_ref is not None:
                raise CommandRejectedError("medical_action_fields_invalid")
        elif implant_ref is not None or body_site is not None:
            raise CommandRejectedError("medical_action_fields_invalid")

        implant_state: Optional[Dict[str, Any]] = None
        consequence = action
        if action in ("implant", "remove_implant"):
            try:
                implant_state = copy.deepcopy(self.repository.read_json(_BIOLOGICAL_IMPLANTS_PATH))
                profiles = self.repository.read_json(_MEDICAL_IMPLANT_PROFILES_PATH)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("medical_implant_registry_invalid") from exc
            instances = implant_state.get("instances") if isinstance(implant_state, dict) else None
            matches = [x for x in instances if isinstance(x, dict) and x.get("id") == implant_ref] if isinstance(instances, list) else []
            if len(matches) != 1:
                raise CommandRejectedError("medical_implant_unresolved")
            instance = matches[0]
            profile_map = profiles.get("profiles") if isinstance(profiles, Mapping) else None
            profile = profile_map.get(instance.get("profile_id")) if isinstance(profile_map, Mapping) else None
            if not isinstance(profile, Mapping):
                raise CommandRejectedError("medical_implant_profile_invalid")
            requirements = profile.get("surgical_requirements")
            if isinstance(requirements, Mapping) and action == "implant":
                ops = practitioner.get("operational_skills") if isinstance(practitioner.get("operational_skills"), Mapping) else {}
                chakra = practitioner.get("chakra_dimensions") if isinstance(practitioner.get("chakra_dimensions"), Mapping) else {}
                spec = practitioner.get("medical_specialization") if isinstance(practitioner.get("medical_specialization"), Mapping) else {}
                actuals = {
                    "medicine": int(ops.get("medicine", 0) or 0),
                    "chakra_control": int(chakra.get("control", 0) or 0),
                    "advanced_surgery_mastery": int(max(spec.get("advanced_surgery", 0) or 0, spec.get("ocular_surgery", 0) or 0)),
                }
                if any(actuals.get(k, 0) < int(v) for k, v in requirements.items() if isinstance(v, int) and not isinstance(v, bool)):
                    raise CommandRejectedError("medical_implant_requirements_unmet")
            implants = patient.get("biological_implants")
            if implants is None:
                implants = []
                patient["biological_implants"] = implants
            if not isinstance(implants, list) or any(not isinstance(x, str) for x in implants):
                raise CommandRejectedError("medical_patient_implants_invalid")
            if action == "implant":
                if instance.get("availability") in ("installed_not_loot", "installed") or instance.get("current_owner_id") == patient_ref:
                    raise CommandRejectedError("medical_implant_unavailable")
                instance["current_owner_id"] = patient_ref
                instance["current_location"] = body_site
                instance["availability"] = "installed"
                if implant_ref not in implants:
                    implants.append(implant_ref)
                    implants.sort()
                consequence = f"implant:{implant_ref}:{patient_ref}:{body_site}"
            else:
                if instance.get("current_owner_id") != patient_ref or implant_ref not in implants:
                    raise CommandRejectedError("medical_implant_not_installed")
                instance["current_owner_id"] = "storage.medical." + patient_location
                instance["current_location"] = patient_location
                instance["availability"] = "preserved_available"
                implants.remove(implant_ref)
                consequence = f"remove_implant:{implant_ref}:{patient_ref}"
        else:
            condition = patient.get("condition")
            resources = patient.get("resources")
            health = resources.get("health") if isinstance(resources, Mapping) else None
            injuries = condition.get("injuries") if isinstance(condition, dict) else None
            if not isinstance(condition, dict) or not isinstance(health, dict) or not isinstance(injuries, list):
                raise CommandRejectedError("medical_patient_state_invalid")
            if injury_ref is not None and injury_ref not in injuries:
                raise CommandRejectedError("medical_injury_unresolved")
            if action in ("treat", "surgery") and injuries and injury_ref is None:
                raise CommandRejectedError("medical_injury_required")
            if action in ("treat", "surgery") and injury_ref is not None:
                injuries.remove(injury_ref)
            current_health = health.get("current")
            capacity = health.get("capacity")
            if isinstance(current_health, bool) or not isinstance(current_health, int) or isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
                raise CommandRejectedError("medical_patient_state_invalid")
            restore = restore_base + effective_quality // 10
            health["current"] = min(capacity, current_health + max(0, restore))
            if injuries:
                condition["readiness"] = "limited"
            elif health["current"] * 100 >= capacity * 80:
                condition["readiness"] = "ready"
            else:
                condition["readiness"] = "limited"
            consequence = f"{action}:{patient_ref}:health:{current_health}->{health['current']}"

        base = self._time_spanning_base(command, meta, current_time, target_time=target_time)
        if base.result.get("interrupted") or CampaignTime.parse(base.result["world_time"]) != target_time:
            raise CommandRejectedError("time_boundary_requires_domain_settlement")

        facility_routes: Optional[Dict[str, Any]] = None
        pharmacy_stock: Optional[Dict[str, Any]] = None
        pharmacy_stock_path: Optional[str] = None
        capacity_result: Optional[Dict[str, Any]] = None
        if facility_ref is not None:
            raw_routes = base.writes.get(_ROUTES_PATH)
            if raw_routes is not None:
                try:
                    facility_routes = json.loads(raw_routes.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    raise CommandRejectedError("medical_facility_registry_invalid") from exc
            else:
                try:
                    facility_routes = copy.deepcopy(self.repository.read_json(_ROUTES_PATH))
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("medical_facility_registry_invalid") from exc
            payload = facility_routes.get("payload") if isinstance(facility_routes, dict) else None
            places = payload.get("places") if isinstance(payload, dict) else None
            matches = [row for row in places or [] if isinstance(row, dict) and row.get("id") == facility_ref]
            if len(matches) != 1:
                raise CommandRejectedError("medical_facility_registry_invalid")
            modules = matches[0].get("mechanical_modules")
            medical_module = modules.get("medical") if isinstance(modules, dict) else None
            if not isinstance(medical_module, dict):
                raise CommandRejectedError("medical_facility_required")
            policy = mechanics.get("facility_capacity_policy") if isinstance(mechanics, Mapping) else None
            actions = policy.get("actions") if isinstance(policy, Mapping) else None
            capacity_rule = actions.get(action) if isinstance(actions, Mapping) else None
            if not isinstance(capacity_rule, Mapping):
                raise CommandRejectedError("medical_mechanics_invalid")

            # Recover bounded facility workload since the last capacity update.
            last_raw = medical_module.get("last_capacity_update_at")
            try:
                last_capacity_time = current_time if last_raw is None else CampaignTime.parse(last_raw)
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("medical_facility_capacity_invalid") from exc
            if last_capacity_time > current_time:
                raise CommandRejectedError("medical_facility_capacity_invalid")
            recovery_hours = Decimal(int((_campaign_datetime(current_time) - _campaign_datetime(last_capacity_time)).total_seconds())) / Decimal(3600)
            load_pairs = (
                ("bed", "bed_load_units", "bed_capacity_units", "bed_recovery_units_per_hour"),
                ("surgical", "surgical_load_units", "surgical_capacity_units", "surgical_recovery_units_per_hour"),
                ("staff", "staff_load_units", "staff_capacity_units", "staff_recovery_units_per_hour"),
            )
            loads_after: Dict[str, int] = {}
            for label, load_field, capacity_field, recovery_field in load_pairs:
                load = medical_module.get(load_field)
                capacity = medical_module.get(capacity_field)
                recovery = medical_module.get(recovery_field)
                cost = capacity_rule.get(load_field, 0)
                if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (load, capacity, recovery, cost)):
                    raise CommandRejectedError("medical_facility_capacity_invalid")
                recovered = min(load, int((recovery_hours * Decimal(recovery)).to_integral_value()))
                settled_load = load - recovered
                if settled_load + cost > capacity:
                    raise CommandRejectedError(f"medical_facility_{label}_capacity_exhausted")
                loads_after[load_field] = settled_load + cost
            medical_module.update(loads_after)
            medical_module["last_capacity_update_at"] = str(target_time)

            kit_units = capacity_rule.get("medical_kit_units", 0)
            item_ref = policy.get("pharmacy_item_ref") if isinstance(policy, Mapping) else None
            if isinstance(kit_units, bool) or not isinstance(kit_units, int) or kit_units < 0 or not isinstance(item_ref, str) or not item_ref:
                raise CommandRejectedError("medical_mechanics_invalid")
            stock_ref = medical_module.get("pharmacy_stock_ref")
            if kit_units > 0:
                if not isinstance(stock_ref, str) or not stock_ref:
                    raise CommandRejectedError("medical_facility_pharmacy_unconfigured")
                pharmacy_stock_path, current_stock, _stock_owner = self._stock_record(stock_ref)
                raw_stock = base.writes.get(pharmacy_stock_path)
                if raw_stock is not None:
                    try:
                        pharmacy_stock = json.loads(raw_stock.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                        raise CommandRejectedError("medical_facility_pharmacy_invalid") from exc
                else:
                    pharmacy_stock = current_stock
                item_map, item_key = self._stock_item_key(pharmacy_stock, item_ref)
                available = item_map.get(item_key)
                if isinstance(available, bool) or not isinstance(available, int) or available < kit_units:
                    raise CommandRejectedError("medical_facility_pharmacy_stock_insufficient")
                item_map[item_key] = available - kit_units
            capacity_result = {
                "bed_load_units": medical_module["bed_load_units"],
                "surgical_load_units": medical_module["surgical_load_units"],
                "staff_load_units": medical_module["staff_load_units"],
                "medical_kit_units_consumed": kit_units,
            }

        world_events = self._world_events_after(base)
        affected = [patient_path, practitioner_path]
        if implant_state is not None:
            affected.append(_BIOLOGICAL_IMPLANTS_PATH)
        if facility_routes is not None:
            affected.append(_ROUTES_PATH)
        if pharmacy_stock_path is not None:
            affected.append(pharmacy_stock_path)
        event_id = self._append_semantic_event(
            world_events, command=command, kind="medical_treatment_resolved", at=target_time,
            host_refs=(), actor_refs=(command.actor_id, patient_ref, practitioner_ref), place_refs=(patient_location,),
            affected_owner_refs=tuple(affected), material_consequence_refs=(consequence,), classification=visibility,
            audience_refs=(patient_ref, practitioner_ref), reducer_ref="shinobi_runtime.commands.medical_treatment_resolution",
        )
        writes = dict(base.writes)
        writes[patient_path] = _json_bytes(patient)
        # Practitioner may be the same owner as patient.
        if practitioner_path != patient_path:
            writes[practitioner_path] = _json_bytes(practitioner)
        if implant_state is not None:
            writes[_BIOLOGICAL_IMPLANTS_PATH] = _json_bytes(implant_state)
        if facility_routes is not None:
            writes[_ROUTES_PATH] = _json_bytes(facility_routes)
        if pharmacy_stock_path is not None and pharmacy_stock is not None:
            writes[pharmacy_stock_path] = _json_bytes(pharmacy_stock)
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("medical write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=target_time)
            if overlay.read_json(patient_path) != patient:
                raise ValueError("medical patient after-image mismatch")
            if implant_state is not None and overlay.read_json(_BIOLOGICAL_IMPLANTS_PATH) != implant_state:
                raise ValueError("medical implant after-image mismatch")
            if facility_routes is not None and overlay.read_json(_ROUTES_PATH) != facility_routes:
                raise ValueError("medical facility after-image mismatch")
            if pharmacy_stock_path is not None and pharmacy_stock is not None and overlay.read_json(pharmacy_stock_path) != pharmacy_stock:
                raise ValueError("medical pharmacy stock after-image mismatch")

        return _BuiltPlan(
            code="medical_treatment_resolution_ready", affected_refs=expected_paths, writes=writes,
            result={"command_type": command.command_type, "action": action, "patient_ref": patient_ref,
                    "practitioner_ref": practitioner_ref, "effective_quality": effective_quality,
                    "consequence": consequence, "facility_capacity": capacity_result,
                    "world_time": str(target_time), "semantic_event_id": event_id}, validator=validate,
        )


    @staticmethod
    def _remove_available_ordinal(ranges: list[list[int]], ordinal: int) -> list[list[int]]:
        result: list[list[int]] = []
        removed = False
        for row in ranges:
            if not isinstance(row, list) or len(row) != 2:
                raise CommandRejectedError("ocular_stockpile_ranges_invalid")
            start, end = row
            if any(isinstance(v, bool) or not isinstance(v, int) for v in (start, end)) or start < 1 or end < start:
                raise CommandRejectedError("ocular_stockpile_ranges_invalid")
            if start <= ordinal <= end:
                if removed:
                    raise CommandRejectedError("ocular_stockpile_ranges_invalid")
                removed = True
                if start <= ordinal - 1:
                    result.append([start, ordinal - 1])
                if ordinal + 1 <= end:
                    result.append([ordinal + 1, end])
            else:
                result.append([start, end])
        if not removed:
            raise CommandRejectedError("ocular_stockpile_eye_unavailable")
        return result

    def _ocular_stockpile_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        """Materialize one unique virtual ordinal without creating inventory."""
        if command.payload.get("action") != "materialize":
            raise CommandRejectedError("ocular_stockpile_action_invalid")
        stockpile_ref = command.payload.get("stockpile_ref")
        if not isinstance(stockpile_ref, str) or not stockpile_ref or len(stockpile_ref) > 120:
            raise CommandRejectedError("ocular_stockpile_ref_invalid")
        requested_eye_ref = command.payload.get("eye_ref")
        if requested_eye_ref is not None and (not isinstance(requested_eye_ref, str) or not requested_eye_ref):
            raise CommandRejectedError("ocular_eye_invalid")

        try:
            registry = copy.deepcopy(self.repository.read_json(_OCULAR_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("ocular_registry_invalid") from exc
        stockpiles = registry.get("stockpiles") if isinstance(registry, dict) else None
        route = stockpiles.get(stockpile_ref) if isinstance(stockpiles, Mapping) else None
        batch_path = route.get("inventory_ref") if isinstance(route, Mapping) else None
        if not isinstance(batch_path, str) or not batch_path.startswith("state/medical/ocular-storage/"):
            raise CommandRejectedError("ocular_stockpile_unresolved")
        try:
            batch = copy.deepcopy(self.repository.read_json(batch_path))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("ocular_stockpile_unresolved") from exc
        if not isinstance(batch, dict) or batch.get("schema") != "ocular-stockpile-batch" or batch.get("regeneration_forbidden") is not True:
            raise CommandRejectedError("ocular_stockpile_invalid")
        prototype = batch.get("prototype")
        ranges = batch.get("available_ordinal_ranges")
        count_before = batch.get("unique_asset_count")
        id_template = batch.get("id_template")
        original_template = batch.get("original_owner_template")
        if (
            not isinstance(prototype, Mapping)
            or not isinstance(ranges, list)
            or isinstance(count_before, bool) or not isinstance(count_before, int) or count_before <= 0
            or not isinstance(id_template, str) or "{ordinal:03d}" not in id_template
            or not isinstance(original_template, str) or "{ordinal:03d}" not in original_template
        ):
            raise CommandRejectedError("ocular_stockpile_invalid")
        owner_ref = prototype.get("current_owner_id")
        if not isinstance(owner_ref, str) or not owner_ref:
            raise CommandRejectedError("ocular_stockpile_invalid")
        if command.actor_id != owner_ref:
            raise CommandRejectedError("ocular_stockpile_authority_denied")

        available_count = 0
        lowest = None
        for row in ranges:
            if not isinstance(row, list) or len(row) != 2:
                raise CommandRejectedError("ocular_stockpile_ranges_invalid")
            start, end = row
            if any(isinstance(v, bool) or not isinstance(v, int) for v in (start, end)) or start < 1 or end < start:
                raise CommandRejectedError("ocular_stockpile_ranges_invalid")
            available_count += end - start + 1
            lowest = start if lowest is None else min(lowest, start)
        if available_count != count_before or lowest is None:
            raise CommandRejectedError("ocular_stockpile_conservation_invalid")

        if requested_eye_ref is None:
            ordinal = lowest
            eye_ref = id_template.format(ordinal=ordinal)
        else:
            prefix, suffix = id_template.split("{ordinal:03d}", 1)
            if not requested_eye_ref.startswith(prefix) or (suffix and not requested_eye_ref.endswith(suffix)):
                raise CommandRejectedError("ocular_stockpile_eye_invalid")
            middle = requested_eye_ref[len(prefix): len(requested_eye_ref) - len(suffix) if suffix else None]
            if len(middle) != 3 or not middle.isdigit():
                raise CommandRejectedError("ocular_stockpile_eye_invalid")
            ordinal = int(middle)
            eye_ref = requested_eye_ref
            if eye_ref != id_template.format(ordinal=ordinal):
                raise CommandRejectedError("ocular_stockpile_eye_invalid")
        if eye_ref in registry.get("eye_index", {}):
            raise CommandRejectedError("ocular_stockpile_eye_already_materialized")

        batch["available_ordinal_ranges"] = self._remove_available_ordinal(ranges, ordinal)
        batch["unique_asset_count"] = count_before - 1
        owner_index = registry.get("owner_index")
        eye_index = registry.get("eye_index")
        if not isinstance(owner_index, dict) or not isinstance(eye_index, dict):
            raise CommandRejectedError("ocular_registry_invalid")
        destination_path = owner_index.get(owner_ref)
        if destination_path is None:
            destination_path = f"state/medical/ocular/owners/{self._reputation_file_slug(owner_ref)}.json"
            owner_index[owner_ref] = destination_path
            shard = {"schema": "ocular-owner-shard", "eyes": [], "ocular_owner_id": owner_ref}
        elif isinstance(destination_path, str):
            try:
                shard = copy.deepcopy(self.repository.read_json(destination_path))
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("ocular_owner_shard_invalid") from exc
        else:
            raise CommandRejectedError("ocular_owner_shard_invalid")
        eyes = shard.get("eyes") if isinstance(shard, dict) else None
        if not isinstance(eyes, list) or any(isinstance(row, Mapping) and row.get("eye_id") == eye_ref for row in eyes):
            raise CommandRejectedError("ocular_owner_shard_invalid")
        eye = copy.deepcopy(dict(prototype))
        eye["eye_id"] = eye_ref
        eye["original_owner_id"] = original_template.format(ordinal=ordinal)
        eyes.append(eye)
        eyes.sort(key=lambda row: str(row.get("eye_id")) if isinstance(row, Mapping) else "")
        eye_index[eye_ref] = destination_path

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="ocular_stockpile_eye_materialized",
            at=current_time,
            host_refs=(owner_ref, stockpile_ref),
            actor_refs=(command.actor_id,),
            place_refs=((str(batch.get("location_id")),) if isinstance(batch.get("location_id"), str) else ()),
            affected_owner_refs=(batch_path, _OCULAR_REGISTRY_PATH, destination_path),
            material_consequence_refs=(f"eye:{eye_ref}", f"stockpile_remaining:{count_before - 1}"),
            classification="secret",
            audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.commands.domains.medical.ocular_stockpile_resolution",
        )
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            batch_path: _json_bytes(batch),
            _OCULAR_REGISTRY_PATH: _json_bytes(registry),
            destination_path: _json_bytes(shard),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("ocular stockpile write set changed")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged_batch = overlay.read_json(batch_path)
            if staged_batch.get("unique_asset_count") != count_before - 1:
                raise ValueError("ocular stockpile count conservation failed")
            staged_ranges = staged_batch.get("available_ordinal_ranges", [])
            if any(isinstance(row, list) and len(row) == 2 and row[0] <= ordinal <= row[1] for row in staged_ranges):
                raise ValueError("materialized ordinal remained available")
            staged_registry = overlay.read_json(_OCULAR_REGISTRY_PATH)
            if staged_registry.get("eye_index", {}).get(eye_ref) != destination_path:
                raise ValueError("materialized eye routing mismatch")
            matches = [row for row in overlay.read_json(destination_path).get("eyes", []) if isinstance(row, Mapping) and row.get("eye_id") == eye_ref]
            if len(matches) != 1:
                raise ValueError("materialized eye conservation failed")

        return _BuiltPlan(
            code="ocular_stockpile_resolution_ready",
            affected_refs=expected,
            writes=writes,
            result={
                "action": "materialize",
                "stockpile_ref": stockpile_ref,
                "eye_ref": eye_ref,
                "ordinal": ordinal,
                "owner_ref": owner_ref,
                "destination_eye_path": destination_path,
                "remaining_count": count_before - 1,
                "semantic_event_id": event_id,
            },
            validator=validate,
        )

    def _ocular_procedure_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        """Conserve one exact eye through extraction or implantation surgery."""

        _exact_payload(
            command.payload,
            (
                "action", "eye_ref", "patient_ref", "recipient_ref", "practitioner_ref",
                "facility_ref", "side", "target_time", "active_hours", "summary", "visibility",
            ),
            command.command_type,
        )
        action = command.payload["action"]
        if action not in ("extract", "implant"):
            raise CommandRejectedError("ocular_action_invalid")
        eye_ref = _stable_id(command.payload["eye_ref"], "ocular_eye_invalid")
        practitioner_ref = _stable_id(command.payload["practitioner_ref"], "ocular_practitioner_invalid")
        facility_ref = _stable_id(command.payload["facility_ref"], "ocular_facility_invalid", prefix="place.")
        patient_raw = command.payload["patient_ref"]
        recipient_raw = command.payload["recipient_ref"]
        patient_ref = None if patient_raw is None else _stable_id(patient_raw, "ocular_patient_invalid")
        recipient_ref = None if recipient_raw is None else _stable_id(recipient_raw, "ocular_recipient_invalid")
        side = command.payload["side"]
        summary = command.payload["summary"]
        visibility = command.payload["visibility"]
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
            raise CommandRejectedError("ocular_summary_invalid")
        if visibility not in ("public", "restricted", "secret"):
            raise CommandRejectedError("ocular_visibility_invalid")
        if action == "extract":
            if patient_ref is None or recipient_ref is not None or side is not None:
                raise CommandRejectedError("ocular_fields_invalid")
            subject_ref = patient_ref
        else:
            if patient_ref is not None or recipient_ref is None or side not in ("left", "right"):
                raise CommandRejectedError("ocular_fields_invalid")
            subject_ref = recipient_ref
        if command.actor_id not in (subject_ref, practitioner_ref):
            raise CommandRejectedError("ocular_actor_not_party")

        try:
            target_time = CampaignTime.parse(command.payload["target_time"])
            active_hours = Decimal(str(command.payload["active_hours"]))
        except Exception as exc:
            raise CommandRejectedError("ocular_time_invalid") from exc
        if target_time <= current_time:
            raise CommandRejectedError("ocular_time_invalid")
        elapsed_hours = Decimal(
            int((_campaign_datetime(target_time) - _campaign_datetime(current_time)).total_seconds())
        ) / Decimal(3600)
        if not active_hours.is_finite() or active_hours <= 0 or active_hours > elapsed_hours:
            raise CommandRejectedError("ocular_time_invalid")

        subject_path, subject = self._resolve_actor_for_write(subject_ref)
        practitioner_path, practitioner = self._resolve_actor_for_write(practitioner_ref)
        if subject.get("life_status") not in ("active", "alive") or practitioner.get("life_status") not in ("active", "alive"):
            raise CommandRejectedError("ocular_party_unavailable")
        subject_location = subject.get("current_location_id")
        practitioner_location = practitioner.get("current_location_id")
        if subject_location != facility_ref or practitioner_location != facility_ref:
            raise CommandRejectedError("ocular_facility_not_colocated")
        facility_factor = self._medical_facility(facility_ref, required=True, specialty="ocular_surgery")
        try:
            mechanics = self.repository.read_json(_MEDICAL_MECHANICS_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("medical_mechanics_invalid") from exc
        thresholds = mechanics.get("procedure_thresholds") if isinstance(mechanics, Mapping) else None
        threshold_rule = thresholds.get("surgery" if action == "extract" else "implant") if isinstance(thresholds, Mapping) else None
        minimum_quality = threshold_rule.get("minimum_quality") if isinstance(threshold_rule, Mapping) else None
        if isinstance(minimum_quality, bool) or not isinstance(minimum_quality, int) or minimum_quality < 0:
            raise CommandRejectedError("medical_mechanics_invalid")
        operator_quality = self._medical_operator_quality(practitioner)
        effective_quality = int((Decimal(operator_quality) * facility_factor).to_integral_value())
        if effective_quality < minimum_quality:
            raise CommandRejectedError("ocular_operator_unqualified")

        try:
            ocular_registry = copy.deepcopy(self.repository.read_json(_OCULAR_REGISTRY_PATH))
            biological = self.repository.read_json(_BIOLOGICAL_IMPLANTS_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("ocular_registry_invalid") from exc
        eye_index = ocular_registry.get("eye_index") if isinstance(ocular_registry, dict) else None
        owner_index = ocular_registry.get("owner_index") if isinstance(ocular_registry, dict) else None
        source_eye_path = eye_index.get(eye_ref) if isinstance(eye_index, dict) else None
        if not isinstance(source_eye_path, str) or not isinstance(owner_index, dict):
            raise CommandRejectedError("ocular_eye_unresolved")
        try:
            source_shard = copy.deepcopy(self.repository.read_json(source_eye_path))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("ocular_eye_unresolved") from exc
        source_eyes = source_shard.get("eyes") if isinstance(source_shard, dict) else None
        matches = [row for row in source_eyes or [] if isinstance(row, dict) and row.get("eye_id") == eye_ref]
        if len(matches) != 1:
            raise CommandRejectedError("ocular_eye_unresolved")
        eye = matches[0]

        # Eyes physically embedded in another installed implant cannot be
        # extracted independently through this reducer.
        instances = biological.get("instances") if isinstance(biological, Mapping) else None
        if action == "extract" and isinstance(instances, list):
            for instance in instances:
                if (
                    isinstance(instance, Mapping)
                    and eye_ref in (instance.get("contains") or [])
                    and instance.get("availability") in ("installed_not_loot", "installed")
                ):
                    raise CommandRejectedError("ocular_eye_embedded_in_implant")

        writes: Dict[str, bytes] = {}
        destination_eye_path: str
        destination_shard: Dict[str, Any]
        injury_ref: Optional[str] = None
        if action == "extract":
            assert patient_ref is not None
            if eye.get("current_owner_id") != patient_ref:
                raise CommandRejectedError("ocular_owner_mismatch")
            eye_side = eye.get("side")
            if eye_side not in ("left", "right"):
                # Non-socket special ocular implants require their owning
                # implant procedure rather than normal eye extraction.
                raise CommandRejectedError("ocular_eye_not_socket_eye")
            source_eyes.remove(eye)
            storage_owner = "storage.medical." + self._reputation_file_slug(facility_ref)
            destination_eye_path = owner_index.get(storage_owner)
            if destination_eye_path is None:
                destination_eye_path = f"state/medical/ocular/owners/{self._reputation_file_slug(storage_owner)}.json"
                destination_shard = {"schema": "ocular-owner-shard", "eyes": [], "ocular_owner_id": storage_owner}
                owner_index[storage_owner] = destination_eye_path
            elif isinstance(destination_eye_path, str):
                try:
                    destination_shard = copy.deepcopy(self.repository.read_json(destination_eye_path))
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("ocular_storage_invalid") from exc
            else:
                raise CommandRejectedError("ocular_storage_invalid")
            destination_eyes = destination_shard.get("eyes")
            if not isinstance(destination_eyes, list) or any(isinstance(row, Mapping) and row.get("eye_id") == eye_ref for row in destination_eyes):
                raise CommandRejectedError("ocular_storage_invalid")
            eye["current_owner_id"] = storage_owner
            eye["current_location"] = facility_ref + ":ocular_preservation"
            eye["native_to_current_owner"] = False
            eye["integration"] = 0
            eye["activation_state"] = "unimplanted"
            eye["preservation_state"] = "registered_medical_preservation"
            if eye.get("condition") == "functional":
                eye["condition"] = "preserved_functional"
            destination_eyes.append(eye)
            destination_eyes.sort(key=lambda row: str(row.get("eye_id")) if isinstance(row, Mapping) else "")
            eye_index[eye_ref] = destination_eye_path
            condition = subject.get("condition")
            injuries = condition.get("injuries") if isinstance(condition, dict) else None
            if not isinstance(injuries, list):
                raise CommandRejectedError("ocular_patient_state_invalid")
            injury_ref = f"injury.ocular_extraction.{eye_side}.{eye_ref}"
            if injury_ref not in injuries:
                injuries.append(injury_ref)
            condition["readiness"] = "limited"
        else:
            assert recipient_ref is not None
            source_owner = eye.get("current_owner_id")
            if not isinstance(source_owner, str) or not source_owner.startswith("storage.medical."):
                raise CommandRejectedError("ocular_eye_not_preserved_for_implant")
            preservation = eye.get("preservation_state")
            if preservation not in ("registered_medical_preservation", "fluid_preservation"):
                raise CommandRejectedError("ocular_eye_not_preserved_for_implant")
            destination_eye_path = owner_index.get(recipient_ref)
            if destination_eye_path is None:
                destination_eye_path = f"state/medical/ocular/owners/{self._reputation_file_slug(recipient_ref)}.json"
                destination_shard = {"schema": "ocular-owner-shard", "eyes": [], "ocular_owner_id": recipient_ref}
                owner_index[recipient_ref] = destination_eye_path
            elif isinstance(destination_eye_path, str):
                try:
                    destination_shard = copy.deepcopy(self.repository.read_json(destination_eye_path))
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("ocular_recipient_shard_invalid") from exc
            else:
                raise CommandRejectedError("ocular_recipient_shard_invalid")
            destination_eyes = destination_shard.get("eyes")
            if not isinstance(destination_eyes, list):
                raise CommandRejectedError("ocular_recipient_shard_invalid")
            socket = f"{recipient_ref}:{side}_eye_socket"
            if any(
                isinstance(row, Mapping)
                and row.get("current_owner_id") == recipient_ref
                and row.get("current_location") == socket
                for row in destination_eyes
            ):
                raise CommandRejectedError("ocular_recipient_socket_occupied")
            source_eyes.remove(eye)
            eye["side"] = side
            eye["current_owner_id"] = recipient_ref
            eye["current_location"] = socket
            eye["native_to_current_owner"] = eye.get("original_owner_id") == recipient_ref
            eye["integration"] = max(40, min(180, effective_quality))
            eye["activation_state"] = "available"
            eye["preservation_state"] = "living"
            if eye.get("condition") == "preserved_functional":
                eye["condition"] = "functional"
            destination_eyes.append(eye)
            destination_eyes.sort(key=lambda row: str(row.get("eye_id")) if isinstance(row, Mapping) else "")
            eye_index[eye_ref] = destination_eye_path
            condition = subject.get("condition")
            injuries = condition.get("injuries") if isinstance(condition, dict) else None
            if not isinstance(injuries, list):
                raise CommandRejectedError("ocular_recipient_state_invalid")
            prefix = f"injury.ocular_extraction.{side}."
            condition["injuries"] = [row for row in injuries if not (isinstance(row, str) and row.startswith(prefix))]
            if not condition["injuries"]:
                condition["readiness"] = "ready"

        # Write both shards even when they happen to be the same path.  The
        # same-path case is possible only for invalid storage/recipient use and
        # is rejected to avoid source/destination aliasing.
        if source_eye_path == destination_eye_path:
            raise CommandRejectedError("ocular_source_destination_conflict")
        writes[source_eye_path] = _json_bytes(source_shard)
        writes[destination_eye_path] = _json_bytes(destination_shard)
        writes[_OCULAR_REGISTRY_PATH] = _json_bytes(ocular_registry)
        writes[subject_path] = _json_bytes(subject)
        if practitioner_path != subject_path:
            writes[practitioner_path] = _json_bytes(practitioner)

        base = self._time_spanning_base(command, meta, current_time, target_time=target_time)
        if base.result.get("interrupted") or CampaignTime.parse(base.result["world_time"]) != target_time:
            raise CommandRejectedError("time_boundary_requires_domain_settlement")
        world_events = self._world_events_after(base)
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="ocular_procedure_resolved",
            at=target_time,
            actor_refs=tuple(dict.fromkeys((command.actor_id, subject_ref, practitioner_ref))),
            place_refs=(facility_ref,),
            affected_owner_refs=tuple(sorted(set((source_eye_path, destination_eye_path, _OCULAR_REGISTRY_PATH, subject_path)))),
            material_consequence_refs=(f"eye:{eye_ref}:{action}", f"eye_path:{source_eye_path}->{destination_eye_path}"),
            classification=visibility,
            audience_refs=tuple(dict.fromkeys((subject_ref, practitioner_ref))),
            reducer_ref="shinobi_runtime.commands.ocular_procedure_resolution",
        )
        merged = dict(base.writes)
        merged.update(writes)
        merged.update(self._world_event_writes(world_events))
        merged = self._prune_noop_writes(merged)
        expected = tuple(sorted(merged))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("ocular procedure write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=target_time)
            staged_registry = overlay.read_json(_OCULAR_REGISTRY_PATH)
            if staged_registry.get("eye_index", {}).get(eye_ref) != destination_eye_path:
                raise ValueError("ocular eye index mismatch")
            source_matches = [row for row in overlay.read_json(source_eye_path).get("eyes", []) if isinstance(row, Mapping) and row.get("eye_id") == eye_ref]
            destination_matches = [row for row in overlay.read_json(destination_eye_path).get("eyes", []) if isinstance(row, Mapping) and row.get("eye_id") == eye_ref]
            if source_matches or len(destination_matches) != 1:
                raise ValueError("ocular conservation failed")

        return _BuiltPlan(
            code="ocular_procedure_resolution_ready",
            affected_refs=expected,
            writes=merged,
            result={
                "action": action,
                "eye_ref": eye_ref,
                "subject_ref": subject_ref,
                "practitioner_ref": practitioner_ref,
                "effective_quality": effective_quality,
                "destination_eye_path": destination_eye_path,
                "world_time": str(target_time),
                "semantic_event_id": event_id,
            },
            validator=validate,
        )
