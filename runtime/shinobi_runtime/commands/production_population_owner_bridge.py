"""Production population-owner bridge and bounded plan-integrity diagnostics.

The generic TimeCommandsMixin hook is intentionally kept in the Academy
compatibility module, but the production planner composes several mixins and may
resolve a later override. Install this adapter after the campaign extension stack
so the exact concrete civil-economy method records the already-loaded population
object in the same context-local transaction slot consumed by later Academy work.

The module also performs a read-only consistency probe on the completed
production time plan. The base time validator captures its expected autonomous
owner after-images in a closure. When preview later reports the deliberately
bounded `autonomous_owner_after_image` token, the exact owner class is otherwise
lost. The probe compares only those captured expected records with the already
planned writes and raises a bounded owner-class code. It never exposes owner
contents or arbitrary paths and never changes plan bytes.
"""
from __future__ import annotations

import json
import re
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.academy_pipeline_transfer_ids import _SHARED_POPULATION
from shinobi_runtime.commands.paths import POPULATION_REGISTRY_PATH

_INSTALLED = False


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


def _diagnose_autonomous_owner_drift(plan: Any) -> None:
    expected = _closure_value(getattr(plan, "validator", None), "autonomy_record_writes")
    writes = getattr(plan, "writes", None)
    if not isinstance(expected, Mapping) or not isinstance(writes, Mapping):
        return
    mismatches: list[str] = []
    for path, record in expected.items():
        if not isinstance(path, str) or not isinstance(record, Mapping):
            continue
        raw = writes.get(path)
        if not isinstance(raw, (bytes, bytearray)):
            continue
        try:
            actual = json.loads(bytes(raw).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if actual != record:
            mismatches.append(_owner_class(path, record))
    if mismatches:
        raise CommandRejectedError(
            "autonomous_owner_after_image_drift__" + "__".join(sorted(set(mismatches)))
        )


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

    advance = CampaignCommandPlanner._advance_time
    if not getattr(advance, "_autonomous_owner_drift_probe", False):
        @wraps(advance)
        def advance_time(self: Any, *args: Any, **kwargs: Any):
            plan = advance(self, *args, **kwargs)
            _diagnose_autonomous_owner_drift(plan)
            return plan

        advance_time._autonomous_owner_drift_probe = True  # type: ignore[attr-defined]
        CampaignCommandPlanner._advance_time = advance_time

    _INSTALLED = True


__all__ = [
    "install_production_population_owner_bridge",
    "_diagnose_autonomous_owner_drift",
]
