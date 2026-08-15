"""Production population-owner bridge and bounded plan-integrity diagnostics.

The generic TimeCommandsMixin hook is intentionally kept in the Academy
compatibility module, but the production planner composes several mixins and may
resolve a later override. Install this adapter after the campaign extension stack
so the exact concrete civil-economy method records the already-loaded population
object in the same context-local transaction slot consumed by later Academy work.

The module also classifies one known base-time validator mismatch without
exposing hidden owner content or arbitrary paths. The classifier runs only when
the existing validator has already rejected `autonomous owner after-image differs
from plan`; it compares the validator's captured expected autonomous owners with
the final staged overlay and substitutes a bounded owner-class diagnostic. It
never changes planned bytes or weakens validation.
"""
from __future__ import annotations

import re
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.academy_pipeline_transfer_ids import _SHARED_POPULATION
from shinobi_runtime.commands.core import _BuiltPlan
from shinobi_runtime.commands.paths import POPULATION_REGISTRY_PATH

_INSTALLED = False
_BASE_DRIFT_CODE = "advance_time_base_validation_invalid__autonomous_owner_after_image"


def _closure_value(callable_obj: Any, name: str, seen: set[int] | None = None) -> Any:
    """Find one named value through nested validator closures without execution."""
    if seen is None:
        seen = set()
    if not callable(callable_obj) or id(callable_obj) in seen:
        return None
    seen.add(id(callable_obj))
    code = getattr(callable_obj, "__code__", None)
    closure = getattr(callable_obj, "__closure__", None)
    if code is None or closure is None:
        return None
    bindings = {}
    for key, cell in zip(code.co_freevars, closure):
        try:
            bindings[key] = cell.cell_contents
        except ValueError:
            continue
    if name in bindings:
        return bindings[name]
    for value in bindings.values():
        found = _closure_value(value, name, seen)
        if found is not None:
            return found
    return None


def _owner_class(path: str, record: Mapping[str, Any]) -> str:
    if path == POPULATION_REGISTRY_PATH:
        return "population"
    schema = record.get("schema")
    if isinstance(schema, str) and schema:
        token = re.sub(r"[^a-z0-9]+", "_", schema.lower()).strip("_")[:48]
        if token:
            return token
    if path.startswith("state/force/"):
        return "force"
    if path.startswith("state/team/"):
        return "team"
    if path.startswith("state/person"):
        return "person"
    if path.startswith("state/world/"):
        return "world"
    return "unknown"


def _drift_classes(raw_validator: Any, overlay: Any) -> tuple[str, ...]:
    expected = _closure_value(raw_validator, "autonomy_record_writes")
    if not isinstance(expected, Mapping):
        return ()
    mismatches: set[str] = set()
    for path, record in expected.items():
        if not isinstance(path, str) or not isinstance(record, Mapping):
            continue
        try:
            actual = overlay.read_json(path)
        except (FileNotFoundError, TypeError, ValueError):
            continue
        if actual != record:
            mismatches.add(_owner_class(path, record))
    return tuple(sorted(mismatches))


def _install_final_overlay_drift_classifier() -> None:
    from shinobi_runtime.commands import campaign_runtime_planner as runtime_module

    guard = runtime_module._guard_plan_validator
    if getattr(guard, "_autonomous_owner_final_overlay_classifier", False):
        return

    @wraps(guard)
    def guarded(plan: _BuiltPlan, code: str, *, overlay_adapter=None) -> _BuiltPlan:
        raw_validator = plan.validator
        wrapped_plan = guard(plan, code, overlay_adapter=overlay_adapter)
        if code != "advance_time_base_validation_invalid":
            return wrapped_plan
        existing = wrapped_plan.validator

        def validate(overlay: Any, manifest: Any) -> None:
            try:
                existing(overlay, manifest)
            except CommandRejectedError as exc:
                if exc.code != _BASE_DRIFT_CODE:
                    raise
                candidate = overlay_adapter(overlay) if overlay_adapter is not None else overlay
                classes = _drift_classes(raw_validator, candidate)
                if not classes:
                    raise
                raise CommandRejectedError(
                    "autonomous_owner_after_image_drift__" + "__".join(classes)
                ) from exc

        return _BuiltPlan(
            code=wrapped_plan.code,
            affected_refs=wrapped_plan.affected_refs,
            writes=wrapped_plan.writes,
            result=wrapped_plan.result,
            validator=validate,
        )

    guarded._autonomous_owner_final_overlay_classifier = True  # type: ignore[attr-defined]
    runtime_module._guard_plan_validator = guarded


def install_production_population_owner_bridge() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from shinobi_runtime.commands.campaign_environment import CampaignCommandPlanner

    civil = CampaignCommandPlanner._settle_governed_civil_economies
    if not getattr(civil, "_production_population_owner_bridge", False):
        @wraps(civil)
        def settle_civil(
            self: Any,
            governance: Any,
            population: Any,
            holders: Any,
            finance: Any,
            *args: Any,
            **kwargs: Any,
        ):
            if not isinstance(population, dict):
                raise CommandRejectedError("population_registry_invalid")
            _SHARED_POPULATION.set(population)
            return civil(
                self,
                governance,
                population,
                holders,
                finance,
                *args,
                **kwargs,
            )

        settle_civil._production_population_owner_bridge = True  # type: ignore[attr-defined]
        CampaignCommandPlanner._settle_governed_civil_economies = settle_civil

    _install_final_overlay_drift_classifier()
    _INSTALLED = True


__all__ = [
    "install_production_population_owner_bridge",
    "_drift_classes",
]
