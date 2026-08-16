"""Source-aware House Tang intake origins for voluntary external applicants."""
from __future__ import annotations

import copy
import json
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_POPULATION = "state/population/registry.json"
_RULES = "game/rules/recruitment/policies.json"
_PRIVATE_STATUS = "house_tang_private_personnel_not_konoha_shinobi"
_ORIGIN_BY_OWNER = {
    "faction_konoha": "Konoha / Land of Fire",
    "faction_iwa": "Iwagakure / Land of Earth",
    "faction_kiri": "Kirigakure / Land of Water",
    "faction_kumo": "Kumogakure / Land of Lightning",
    "faction_suna": "Sunagakure / Land of Wind",
}
_INSTALLED = False


class _OverlayAdapter:
    def __init__(self, overlay: StagedOverlay, overrides: Mapping[str, Any]):
        self._overlay = overlay
        self._overrides = overrides

    @property
    def changed_paths(self):
        return self._overlay.changed_paths

    def read_json(self, path: str):
        if path in self._overrides:
            return copy.deepcopy(self._overrides[path])
        return self._overlay.read_json(path)

    def __getattr__(self, name: str):
        return getattr(self._overlay, name)


def install_external_house_intake_origin() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_environment as module

    planner = module.CampaignCommandPlanner
    original = planner._institution_intake_resolution
    if getattr(original, "_external_house_intake_origin", False):
        _INSTALLED = True
        return

    @wraps(original)
    def wrapped(self: Any, command: Any, meta: Mapping[str, Any], current_time: Any) -> _BuiltPlan:
        plan = original(self, command, meta, current_time)
        source_pool_id = command.payload.get("source_pool_id")
        policy_ref = command.payload.get("policy_ref")
        institution_ref = command.payload.get("institution_ref")
        if not all(isinstance(value, str) and value for value in (source_pool_id, policy_ref, institution_ref)):
            return plan
        try:
            population = self.repository.read_json(_POPULATION)
            pools = population.get("pools") if isinstance(population, Mapping) else None
            source = pools.get(source_pool_id) if isinstance(pools, Mapping) else None
            owner_ref = source.get("owner_ref") if isinstance(source, Mapping) else None
            rules = self.repository.read_json(_RULES)
            policies = rules.get("policies") if isinstance(rules, Mapping) else None
            policy = policies.get(policy_ref) if isinstance(policies, Mapping) else None
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("institution_intake_source_origin_invalid") from exc
        origin = _ORIGIN_BY_OWNER.get(owner_ref)
        core_path = policy.get("person_core_registry_path") if isinstance(policy, Mapping) else None
        if not isinstance(origin, str) or not isinstance(core_path, str):
            raise CommandRejectedError("institution_intake_source_origin_invalid")
        house_path, _house = self._growth_house(institution_ref)
        if core_path not in plan.writes or house_path not in plan.writes:
            raise CommandRejectedError("institution_intake_source_origin_invalid")

        old_core = json.loads(plan.writes[core_path].decode("utf-8"))
        old_house = json.loads(plan.writes[house_path].decode("utf-8"))
        core = copy.deepcopy(old_core)
        house = copy.deepcopy(old_house)
        people = core.get("people") if isinstance(core, dict) else None
        new_refs = plan.result.get("new_member_refs") if isinstance(plan.result, Mapping) else None
        cohort_ref = plan.result.get("cohort_ref") if isinstance(plan.result, Mapping) else None
        if not isinstance(people, dict) or not isinstance(new_refs, list) or not isinstance(cohort_ref, str):
            raise CommandRejectedError("institution_intake_source_origin_invalid")
        for person_ref in new_refs:
            row = people.get(person_ref)
            if not isinstance(row, dict):
                raise CommandRejectedError("institution_intake_source_origin_invalid")
            row["origin"] = origin

        cohorts = house.get("cohorts") if isinstance(house, dict) else None
        cohort = next(
            (row for row in cohorts or [] if isinstance(row, dict) and row.get("id") == cohort_ref),
            None,
        )
        profile = cohort.get("cohort_profile") if isinstance(cohort, Mapping) else None
        counts = profile.get("category_counts") if isinstance(profile, Mapping) else None
        accepted = plan.result.get("accepted_count") if isinstance(plan.result, Mapping) else None
        if not isinstance(counts, dict) or isinstance(accepted, bool) or not isinstance(accepted, int):
            raise CommandRejectedError("institution_intake_source_origin_invalid")
        for key in tuple(counts):
            if isinstance(key, str) and (key.startswith("origin:") or key.startswith("source_owner:")):
                counts.pop(key, None)
        counts[f"origin:{origin}"] = accepted
        counts[f"source_owner:{owner_ref}"] = accepted
        counts[f"service_status:{_PRIVATE_STATUS}"] = accepted

        writes = dict(plan.writes)
        writes[core_path] = _json_bytes(core)
        writes[house_path] = _json_bytes(house)
        result = dict(plan.result)
        result["source_owner_ref"] = owner_ref
        result["source_origin"] = origin
        result["destination_service_status"] = _PRIVATE_STATUS
        base_validator = plan.validator

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            base_validator(
                _OverlayAdapter(overlay, {core_path: old_core, house_path: old_house}),
                manifest,
            )
            if overlay.read_json(core_path) != core:
                raise ValueError("institution intake source-aware person origin mismatch")
            if overlay.read_json(house_path) != house:
                raise ValueError("institution intake source-aware cohort mismatch")

        return _BuiltPlan(
            code=plan.code,
            affected_refs=plan.affected_refs,
            writes=writes,
            result=result,
            validator=validate,
        )

    wrapped._external_house_intake_origin = True  # type: ignore[attr-defined]
    planner._institution_intake_resolution = wrapped
    _INSTALLED = True


__all__ = ["install_external_house_intake_origin"]
