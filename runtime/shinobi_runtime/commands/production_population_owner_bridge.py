"""Production population-owner bridge and bounded plan-integrity diagnostics.

The generic TimeCommandsMixin hook is intentionally kept in the Academy
compatibility module, but the production planner composes several mixins and may
resolve later overrides. This adapter records the exact population object chosen
by production civil-economy settlement for later Academy work.

For one known base-time validator mismatch, this module also emits only a bounded
owner-class diagnostic. It never exposes owner contents or repository paths and
never changes planned bytes or validation. The final preview wrapper classifies
the failing owner from the existing exception traceback after the validator has
already rejected the after-image.
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


def _traceback_owner_class(exc: BaseException) -> str | None:
    """Read only the sanitized owner class from the known validator traceback."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        tb = current.__traceback__
        while tb is not None:
            frame = tb.tb_frame
            owner_ref = frame.f_locals.get("owner_ref")
            expected = frame.f_locals.get("expected_record")
            if isinstance(owner_ref, str) and isinstance(expected, Mapping):
                return _owner_class(owner_ref, expected)
            tb = tb.tb_next
        current = current.__cause__ or current.__context__
    return None


def _install_preview_trace_classifier() -> None:
    from shinobi_runtime.commands.campaign_environment import CampaignCommandPlanner

    preview = CampaignCommandPlanner.preview
    if getattr(preview, "_autonomous_owner_trace_classifier", False):
        return

    @wraps(preview)
    def classified_preview(self: Any, *args: Any, **kwargs: Any):
        try:
            return preview(self, *args, **kwargs)
        except CommandRejectedError as exc:
            if exc.code != _BASE_DRIFT_CODE:
                raise
            owner_class = _traceback_owner_class(exc)
            if owner_class is None:
                raise
            raise CommandRejectedError(
                "autonomous_owner_after_image_drift__" + owner_class
            ) from exc

    classified_preview._autonomous_owner_trace_classifier = True  # type: ignore[attr-defined]
    CampaignCommandPlanner.preview = classified_preview


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

    _install_preview_trace_classifier()
    _INSTALLED = True


__all__ = [
    "install_production_population_owner_bridge",
    "_owner_class",
    "_traceback_owner_class",
]
