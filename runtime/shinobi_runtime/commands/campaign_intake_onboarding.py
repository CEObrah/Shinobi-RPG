"""Conserved Sword Manor capacity and equipment onboarding for House intake.

Rostered recruits remain lightweight people, but admission still consumes real
institution capacity and real equipment.  This layer rejects intake that would
exceed the registered training capacity, provisions the saved cohort loadout
from conserved House stock, and creates exact per-person inventory custody
without materializing heavyweight exact character sheets.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.campaign_manufacturing import CampaignCommandPlanner as _Base
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import INVENTORY_REGISTRY_PATH, ROUTES_PATH
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_RECRUITMENT_POLICY_PATH = "game/rules/recruitment/policies.json"


def _scaled_loadout_quantities(per_person: Mapping[str, Any], count: int) -> Dict[str, int]:
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("intake onboarding count invalid")
    totals: Dict[str, int] = {}
    for item_ref, quantity in sorted(per_person.items()):
        if (
            not isinstance(item_ref, str)
            or not item_ref
            or isinstance(quantity, bool)
            or not isinstance(quantity, int)
            or quantity < 0
        ):
            raise ValueError("intake onboarding loadout invalid")
        if quantity:
            totals[item_ref] = quantity * count
    return totals


def _plan_json(plan: _BuiltPlan, repository: Any, path: str) -> Any:
    raw = plan.writes.get(path)
    if raw is None:
        return copy.deepcopy(repository.read_json(path))
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CommandRejectedError("institution_intake_onboarding_invalid") from exc


class _PriorPlanOverlay:
    """Present a composed final overlay as the already-validated prior plan."""

    def __init__(self, overlay: Any, prior: _BuiltPlan):
        self._overlay = overlay
        self._prior = prior
        self.changed_paths = tuple(sorted(prior.writes))

    def read_json(self, path: str) -> Any:
        raw = self._prior.writes.get(path)
        if raw is None:
            return self._overlay.read_json(path)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError("prior intake overlay decode failed") from exc

    def __getattr__(self, name: str) -> Any:
        return getattr(self._overlay, name)


class CampaignCommandPlanner(_Base):
    """Production planner with conserved Sword Manor admission/onboarding."""

    def _institution_intake_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        plan = super()._institution_intake_resolution(command, meta, current_time)
        institution_ref = plan.result.get("institution_ref")
        policy_ref = plan.result.get("policy_ref")
        cohort_ref = plan.result.get("cohort_ref")
        new_member_refs = plan.result.get("new_member_refs")
        if (
            not all(isinstance(value, str) and value for value in (institution_ref, policy_ref, cohort_ref))
            or not isinstance(new_member_refs, list)
            or not new_member_refs
            or any(not isinstance(value, str) or not value for value in new_member_refs)
        ):
            raise CommandRejectedError("institution_intake_onboarding_invalid")

        try:
            registry = self.repository.read_json(_RECRUITMENT_POLICY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("institution_intake_onboarding_invalid") from exc
        policies = registry.get("policies") if isinstance(registry, Mapping) else None
        policy = policies.get(policy_ref) if isinstance(policies, Mapping) else None
        if not isinstance(policy, Mapping):
            raise CommandRejectedError("institution_intake_onboarding_invalid")

        house_path, _house = self._growth_house(institution_ref)
        house = _plan_json(plan, self.repository, house_path)
        cohorts = house.get("cohorts") if isinstance(house, dict) else None
        rostered_count = house.get("rostered_member_count") if isinstance(house, dict) else None
        if (
            not isinstance(cohorts, list)
            or isinstance(rostered_count, bool)
            or not isinstance(rostered_count, int)
            or rostered_count < len(new_member_refs)
        ):
            raise CommandRejectedError("institution_intake_onboarding_invalid")

        capacity_gate = policy.get("capacity_gate")
        if not isinstance(capacity_gate, Mapping):
            raise CommandRejectedError("institution_intake_capacity_policy_invalid")
        module_kind = capacity_gate.get("module_kind")
        capacity_field = capacity_gate.get("capacity_field")
        home_place_ref = policy.get("home_place_ref")
        if (
            not isinstance(module_kind, str)
            or not module_kind
            or not isinstance(capacity_field, str)
            or not capacity_field
            or not isinstance(home_place_ref, str)
            or not home_place_ref
        ):
            raise CommandRejectedError("institution_intake_capacity_policy_invalid")
        try:
            routes = _plan_json(plan, self.repository, ROUTES_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("institution_intake_capacity_invalid") from exc
        payload = routes.get("payload") if isinstance(routes, Mapping) else None
        places = payload.get("places") if isinstance(payload, Mapping) else None
        matches = [
            row for row in places or []
            if isinstance(row, Mapping) and row.get("id") == home_place_ref
        ]
        if len(matches) != 1:
            raise CommandRejectedError("institution_intake_capacity_invalid")
        modules = matches[0].get("mechanical_modules")
        module = modules.get(module_kind) if isinstance(modules, Mapping) else None
        capacity = module.get(capacity_field) if isinstance(module, Mapping) else None
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise CommandRejectedError("institution_intake_capacity_invalid")
        if rostered_count > capacity:
            raise CommandRejectedError("institution_intake_training_capacity_exceeded")

        loadout_ref = policy.get("onboarding_loadout_ref")
        stock_ref = policy.get("onboarding_stock_ref")
        require_full = policy.get("onboarding_require_full_loadout")
        if (
            not isinstance(loadout_ref, str)
            or not loadout_ref
            or not isinstance(stock_ref, str)
            or not stock_ref
            or require_full is not True
        ):
            raise CommandRejectedError("institution_intake_onboarding_policy_invalid")
        try:
            per_person = self._loadout_quantities(loadout_ref)
            required_totals = _scaled_loadout_quantities(per_person, len(new_member_refs))
        except (CommandRejectedError, TypeError, ValueError) as exc:
            raise CommandRejectedError("institution_intake_onboarding_loadout_invalid") from exc

        stock_path, stock, stock_owner = self._stock_record(stock_ref)
        if stock_owner != institution_ref:
            raise CommandRejectedError("institution_intake_onboarding_stock_invalid")
        for item_ref, total_quantity in required_totals.items():
            container, key = self._stock_item_key(stock, item_ref)
            available = container.get(key)
            if (
                isinstance(available, bool)
                or not isinstance(available, int)
                or available < total_quantity
            ):
                raise CommandRejectedError("institution_intake_onboarding_stock_insufficient")
        for item_ref, total_quantity in required_totals.items():
            container, key = self._stock_item_key(stock, item_ref)
            container[key] -= total_quantity

        try:
            inventory = _plan_json(plan, self.repository, INVENTORY_REGISTRY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("institution_intake_onboarding_inventory_invalid") from exc
        holders = inventory.get("holders") if isinstance(inventory, dict) else None
        if not isinstance(holders, dict):
            raise CommandRejectedError("institution_intake_onboarding_inventory_invalid")
        for person_ref in new_member_refs:
            if person_ref in holders:
                raise CommandRejectedError("institution_intake_onboarding_inventory_conflict")
            holders[person_ref] = dict(per_person)

        cohort_matches = [
            row for row in cohorts if isinstance(row, dict) and row.get("id") == cohort_ref
        ]
        if len(cohort_matches) != 1:
            raise CommandRejectedError("institution_intake_onboarding_invalid")
        cohort_matches[0]["loadout_standard"] = loadout_ref

        writes = dict(plan.writes)
        writes[house_path] = _json_bytes(house)
        writes[stock_path] = _json_bytes(stock)
        writes[INVENTORY_REGISTRY_PATH] = _json_bytes(inventory)
        writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes))
        prior_validator = plan.validator

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("institution intake onboarding write set changed after planning")
            prior_validator(_PriorPlanOverlay(overlay, plan), manifest)
            if overlay.read_json(house_path) != house:
                raise ValueError("institution intake onboarding House after-image mismatch")
            if overlay.read_json(stock_path) != stock:
                raise ValueError("institution intake onboarding stock after-image mismatch")
            if overlay.read_json(INVENTORY_REGISTRY_PATH) != inventory:
                raise ValueError("institution intake onboarding inventory after-image mismatch")

        result = dict(plan.result)
        result.update({
            "capacity_gate": {
                "place_ref": home_place_ref,
                "module_kind": module_kind,
                "capacity_field": capacity_field,
                "capacity_slots": capacity,
                "remaining_after_intake": capacity - rostered_count,
            },
            "onboarding_loadout_ref": loadout_ref,
            "onboarding_stock_ref": stock_ref,
            "onboarding_items_per_member": dict(per_person),
            "onboarding_equipment_conserved": True,
        })
        return _BuiltPlan(
            code=plan.code,
            affected_refs=expected,
            writes=writes,
            result=result,
            validator=validate,
        )


__all__ = ["CampaignCommandPlanner", "_scaled_loadout_quantities"]
